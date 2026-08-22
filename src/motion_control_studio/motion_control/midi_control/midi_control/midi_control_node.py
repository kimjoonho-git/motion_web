import json
import hashlib
import math
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict, List

import rclpy
import yaml
from midi_msgs.msg import Midi
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
from std_msgs.msg import String

from midi_control.bank_manager import (
    FILTER_LEVEL_MAX,
    MIDI_CHANNEL_COUNT,
    MIDI_VALUE_MAX,
    MIDI_VALUE_MIN,
    MidiBankManager,
    mapping_motion_ids,
)
from midi_control.config_store import load_midi_banks
from midi_control.motion_axis_registry import MotionAxisRegistry
from motion_common import command_router, generation as generation_mod, topics, values
from motion_common.timing import CONTROL_PERIOD_SEC


FILTER_ORDER = 2
FILTER_MAX_TIME_CONSTANT_SEC = 0.5
FILTER_MAX_STEP_SEC = 0.05
MIDI_COMMAND_PERIOD_SEC = CONTROL_PERIOD_SEC
MIDI_COMMAND_DEADBAND_DEG = 0.01
FADER_SYNC_MIN_DURATION_SEC = 0.10
FADER_PARK_RETRY_SEC = 0.15
FADER_PARK_TOLERANCE_RAW = 16
FADER_PARK_TIMEOUT_SEC = 2.0
PLAYBACK_FADER_RESUME_DELAY_SEC = 0.35
SELECT_TOGGLE_DEBOUNCE_SEC = 0.08
SELECT_RANGE_TOLERANCE_PERCENT = 0.25
LINKED_RANGE_TOLERANCE_DEG = 1e-6
LINKED_MOTION_VALUE_TOLERANCE_DEG = 1e-6
PICKUP_TOLERANCE_DEG = 0.5
PICKUP_FEEDBACK_CONSISTENCY_DEG = 1.0


class LinkedMotionRangeMismatch(ValueError):
    """Raised when one MIDI fader links Motion IDs with different ranges."""


def motion_value_display(
    motion_ids: List[str],
    source_values: Dict[str, float],
    *,
    control_enabled: bool = False,
    estimated_value: float | None = None,
) -> tuple[float | None, str, str]:
    """Prefer confirmed source values, then an explicitly marked SELECT preview."""
    if not motion_ids:
        return None, 'NO DATA', 'missing'
    values = []
    for motion_id in motion_ids:
        value = _finite_float(source_values.get(str(motion_id)))
        if value is None:
            values = []
            break
        values.append(value)
    if values:
        if max(values) - min(values) > LINKED_MOTION_VALUE_TOLERANCE_DEG:
            return None, 'DIFF', 'different'
        value = sum(values) / len(values)
        return value, _motion_lcd_number(value), 'confirmed'
    preview = _finite_float(estimated_value)
    if control_enabled and preview is not None:
        return preview, _motion_lcd_number(preview, prefix='~'), 'estimated'
    return None, 'NO DATA', 'missing'


def _motion_lcd_number(value: float, prefix: str = '') -> str:
    for decimals in (3, 2, 1, 0):
        text = f'{value:.{decimals}f}'
        if len(prefix) + len(text) <= 7:
            return prefix + text
    return (prefix + f'{value:.1e}')[:7]


def second_order_low_pass(
    input_value: float,
    filter_level: float,
    dt_sec: float,
    stage1_previous: float,
    stage2_previous: float,
) -> tuple[float, float, float]:
    """Apply two cascaded first-order sections as a stable second-order LPF."""
    level = max(0, min(FILTER_LEVEL_MAX, int(filter_level)))
    if level <= 0:
        value = float(input_value)
        return value, value, value
    normalized_level = level / FILTER_LEVEL_MAX
    tau_sec = normalized_level * FILTER_MAX_TIME_CONSTANT_SEC
    dt_sec = max(1e-6, min(float(dt_sec), FILTER_MAX_STEP_SEC))
    alpha = 1.0 - math.exp(-dt_sec / tau_sec)
    stage1 = stage1_previous + alpha * (input_value - stage1_previous)
    stage2 = stage2_previous + alpha * (stage1 - stage2_previous)
    return stage2, stage1, stage2


def _finite_float(value: Any) -> float | None:
    return values.finite_float(value)


def motion_value_from_output(
    output_14bit: float,
    row: Dict[str, Any],
    motion_range: tuple[float, float] | None = None,
) -> float:
    """Convert the final 14-bit MIDI output into motion-space degrees."""
    lower = _finite_float(row.get('motion_lower_deg'))
    upper = _finite_float(row.get('motion_upper_deg'))
    if motion_range is not None:
        lower, upper = motion_range
    if lower is None or upper is None or upper <= lower:
        raise ValueError('motion-axis Min/Max angle is invalid')
    normalized = max(0.0, min(1.0, float(output_14bit) / MIDI_VALUE_MAX))
    return lower + ((upper - lower) * normalized)


def motor_target_from_motion(motion_value: float, row: Dict[str, Any]) -> float:
    """Use the same mapping equation as motion_runtime."""
    sign = -1.0 if bool(row.get('invert')) else 1.0
    reference = _finite_float(row.get('reference_position_deg')) or 0.0
    if row.get('reference_enabled') is False:
        reference = 0.0
    offset = _finite_float(row.get('offset_deg')) or 0.0
    scale = _finite_float(row.get('scale')) or 1.0
    gear_ratio = _finite_float(row.get('gear_ratio')) or 1.0
    return reference + ((float(motion_value) + offset) * scale * sign * gear_ratio)


def motion_value_from_motor(motor_position: float, row: Dict[str, Any]) -> float:
    """Invert actual motor feedback into the configured logical motion value."""
    sign = -1.0 if bool(row.get('invert')) else 1.0
    reference = _finite_float(row.get('reference_position_deg')) or 0.0
    if row.get('reference_enabled') is False:
        reference = 0.0
    offset = _finite_float(row.get('offset_deg')) or 0.0
    scale = _finite_float(row.get('scale')) or 1.0
    gear_ratio = _finite_float(row.get('gear_ratio')) or 1.0
    factor = scale * sign * gear_ratio
    if math.isclose(factor, 0.0, abs_tol=1e-12):
        raise ValueError('motion-axis scale/gear ratio is zero')
    return ((float(motor_position) - reference) / factor) - offset


def raw_fader_for_motion(
    motion_value: float,
    row: Dict[str, Any],
    bank_mapping: Dict[str, Any],
    motion_range: tuple[float, float] | None = None,
) -> int:
    """Invert motion range and bank Min/Max/reverse into a physical fader value."""
    lower = _finite_float(row.get('motion_lower_deg'))
    upper = _finite_float(row.get('motion_upper_deg'))
    if motion_range is not None:
        lower, upper = motion_range
    if lower is None or upper is None or upper <= lower:
        raise ValueError('motion-axis Min/Max angle is invalid')
    output_percent = 100.0 * ((motion_value - lower) / (upper - lower))
    minimum = float(bank_mapping['min_percent'])
    maximum = float(bank_mapping['max_percent'])
    span = maximum - minimum
    if span <= 0.0:
        raise ValueError('MIDI Min/Max percent is invalid')
    representable_min = max(0.0, min(100.0, minimum))
    representable_max = max(0.0, min(100.0, maximum))
    if (
        output_percent < representable_min - SELECT_RANGE_TOLERANCE_PERCENT
        or output_percent > representable_max + SELECT_RANGE_TOLERANCE_PERCENT
    ):
        allowed_lower = lower + ((upper - lower) * representable_min / 100.0)
        allowed_upper = lower + ((upper - lower) * representable_max / 100.0)
        raise ValueError(
            f'활성화 불가: 현재 위치 {motion_value:.2f}°가 '
            f'이 라인의 제어 범위 {allowed_lower:.2f}°~{allowed_upper:.2f}° 밖입니다'
        )
    output_percent = max(representable_min, min(representable_max, output_percent))
    normalized = max(0.0, min(1.0, (output_percent - minimum) / span))
    if bank_mapping['reversed']:
        normalized = 1.0 - normalized
    return int(round(MIDI_VALUE_MAX * normalized))


def require_same_motion_ranges(rows: List[Dict[str, Any]]) -> tuple[float, float]:
    """Return the shared range, rejecting linked axes with different ranges."""
    if not rows:
        raise ValueError('모션축 설정을 확인할 수 없습니다')
    ranges = []
    for row in rows:
        lower = _finite_float(row.get('motion_lower_deg'))
        upper = _finite_float(row.get('motion_upper_deg'))
        if lower is None or upper is None or upper <= lower:
            raise ValueError('모션축 Min/Max 각도를 확인하세요')
        ranges.append((lower, upper))
    first_lower, first_upper = ranges[0]
    if any(
        abs(lower - first_lower) > LINKED_RANGE_TOLERANCE_DEG
        or abs(upper - first_upper) > LINKED_RANGE_TOLERANCE_DEG
        for lower, upper in ranges[1:]
    ):
        raise LinkedMotionRangeMismatch(
            '연동 Motion ID의 모션 범위가 서로 다릅니다'
        )
    return first_lower, first_upper


def safe_motion_range_for_motor(
    row: Dict[str, Any],
    motor: Dict[str, Any],
) -> tuple[float, float]:
    """Return the configured motion range intersected with motor limits."""
    motion_lower = _finite_float(row.get('motion_lower_deg'))
    motion_upper = _finite_float(row.get('motion_upper_deg'))
    if (
        motion_lower is None
        or motion_upper is None
        or motion_upper <= motion_lower
    ):
        raise ValueError('모션축 Min/Max 각도를 확인하세요')

    motor_lower = _finite_float(motor.get('lower'))
    motor_upper = _finite_float(motor.get('upper'))
    if motor_lower is None or motor_upper is None:
        raise ValueError('모터축 Lower/Upper 제한을 확인할 수 없습니다')
    if motor_upper < motor_lower:
        raise ValueError('모터축 Lower/Upper 설정을 확인하세요')

    safe_from_motor = sorted((
        motion_value_from_motor(motor_lower, row),
        motion_value_from_motor(motor_upper, row),
    ))
    safe_lower = max(motion_lower, safe_from_motor[0])
    safe_upper = min(motion_upper, safe_from_motor[1])
    if safe_upper < safe_lower:
        raise ValueError(
            f'모션범위 {motion_lower:.3f}°~{motion_upper:.3f}°와 '
            f'모터범위 {motor_lower:.3f}°~{motor_upper:.3f}°가 겹치지 않습니다'
        )
    return safe_lower, safe_upper


def require_motion_value_within_limits(
    motion_id: Any,
    motion_value: float,
    row: Dict[str, Any],
    motor: Dict[str, Any],
) -> float:
    """Return a motor target only when one motion command passes both limits."""
    motion_lower = _finite_float(row.get('motion_lower_deg'))
    motion_upper = _finite_float(row.get('motion_upper_deg'))
    if motion_lower is None or motion_upper is None:
        raise ValueError('모션축 Min/Max 각도를 확인하세요')
    tolerance = 1e-6
    value = float(motion_value)
    if value < motion_lower - tolerance or value > motion_upper + tolerance:
        raise ValueError(
            f'{motion_id}: 모션 명령 {value:.3f}°가 모션범위 '
            f'{motion_lower:.3f}°~{motion_upper:.3f}°를 벗어납니다'
        )
    target = motor_target_from_motion(value, row)
    motor_lower = _finite_float(motor.get('lower'))
    motor_upper = _finite_float(motor.get('upper'))
    if motor_lower is not None and target < motor_lower - tolerance:
        raise ValueError(
            f'{motion_id}: 모터 목표 {target:.3f}°가 Lower '
            f'{motor_lower:.3f}°보다 작습니다'
        )
    if motor_upper is not None and target > motor_upper + tolerance:
        axis = motor.get('controller_index')
        raise ValueError(
            f'{motion_id}: 모터축 {axis} 목표 {target:.3f}°가 Upper '
            f'{motor_upper:.3f}°보다 큽니다'
        )
    return target


def safe_motion_range_for_group(
    group: List[Dict[str, Any]],
) -> tuple[float, float]:
    """Return one shared MIDI range safe for every linked motor axis."""
    safe_ranges = []
    for item in group:
        row = item['row']
        motor = item.get('motor')
        if isinstance(motor, dict):
            safe_ranges.append(safe_motion_range_for_motor(row, motor))
        else:
            lower = _finite_float(row.get('motion_lower_deg'))
            upper = _finite_float(row.get('motion_upper_deg'))
            if lower is None or upper is None:
                raise ValueError('모션축 Min/Max 각도를 확인하세요')
            safe_ranges.append((lower, upper))
    if not safe_ranges:
        raise ValueError('안전범위를 계산할 모션축이 없습니다')
    safe_lower = max(item[0] for item in safe_ranges)
    safe_upper = min(item[1] for item in safe_ranges)
    if safe_upper <= safe_lower:
        raise ValueError('연동 축이 함께 사용할 수 있는 안전 모션범위가 없습니다')
    return safe_lower, safe_upper


class MidiControlNode(Node):
    """MIDI monitor and safe motion-supervisor request producer."""

    def __init__(self) -> None:
        super().__init__('midi_control_node')
        self.input_topic = str(self.declare_parameter('input_topic', topics.XTOUCH_MIDI).value)
        self.state_topic = str(
            self.declare_parameter('state_topic', topics.MIDI_MONITOR_STATE).value
        )
        self.request_topic = str(
            self.declare_parameter('request_topic', topics.MIDI_MONITOR_REQUEST).value
        )
        self.response_topic = str(
            self.declare_parameter('response_topic', topics.MIDI_MONITOR_RESPONSE).value
        )
        self.feedback_topic = str(
            self.declare_parameter('feedback_topic', topics.XTOUCH_FEEDBACK).value
        )
        self.input_state_topic = str(
            self.declare_parameter('input_state_topic', topics.XTOUCH_INPUT_STATE).value
        )
        self.connection_command_topic = str(
            self.declare_parameter(
                'connection_command_topic', topics.XTOUCH_CONNECTION_COMMAND
            ).value
        )
        self.connection_state_topic = str(
            self.declare_parameter(
                'connection_state_topic', topics.XTOUCH_CONNECTION_STATE
            ).value
        )
        self.motion_state_topic = str(
            self.declare_parameter('motion_state_topic', topics.MOTION_STATE).value
        )
        self.motion_run_status_topic = str(
            self.declare_parameter(
                'motion_run_status_topic', topics.MOTION_RUN_STATUS
            ).value
        )
        self.motion_studio_status_topic = str(
            self.declare_parameter(
                'motion_studio_status_topic', topics.STUDIO_STATUS
            ).value
        )
        self.motion_mapping_response_topic = str(
            self.declare_parameter(
                'motion_mapping_response_topic',
                topics.MOTION_MAPPING_RESPONSE,
            ).value
        )
        self.motor_request_topic = str(
            self.declare_parameter(
                'motor_request_topic', topics.MIDI_POSITION_REQUEST
            ).value
        )
        self.motor_result_topic = str(
            self.declare_parameter(
                'motor_result_topic', topics.MIDI_POSITION_RESULT
            ).value
        )
        self.motion_value_topic = str(
            self.declare_parameter(
                'motion_value_topic', topics.MOTION_VALUE_STATE
            ).value
        )
        self._motion_projects_dir = Path(
            str(self.declare_parameter(
                'motion_projects_dir',
                str(
                    Path(os.environ.get('MOTION_WORKSPACE', Path.cwd())).expanduser()
                    / 'motion_projects'
                ),
            ).value)
        ).expanduser().resolve()
        self._project_id = ''
        self._execution_context: Dict[str, Any] = {}
        self._execution_context_ready = False
        self._project_generation = 0
        self._mappings_dir = self._motion_projects_dir
        self.publish_hz = max(1.0, float(self.declare_parameter('publish_hz', 10.0).value))
        self.stale_timeout_sec = max(
            0.1,
            float(self.declare_parameter('stale_timeout_sec', 0.5).value),
        )
        self.pickup_tolerance_deg = max(
            0.0,
            float(
                self.declare_parameter(
                    'pickup_tolerance_deg', PICKUP_TOLERANCE_DEG
                ).value
            ),
        )
        self.pickup_feedback_consistency_deg = max(
            0.0,
            float(
                self.declare_parameter(
                    'pickup_feedback_consistency_deg',
                    PICKUP_FEEDBACK_CONSISTENCY_DEG,
                ).value
            ),
        )

        self._lock = threading.Lock()
        self._last_received_monotonic: float | None = None
        self._last_received_wall: float | None = None
        self._last_physical_input_monotonic: float | None = None
        self._last_physical_input_wall: float | None = None
        self._device_connected = False
        self._device_connection_message = 'MIDI 장치 연결 상태 확인 중'
        self._device_last_connected_at: float | None = None
        self._device_last_disconnected_at: float | None = None
        self._device_last_power_reconnected_at: float | None = None
        self._device_connection_count = 0
        self._device_power_reconnect_count = 0
        self._raw_channels = [0] * MIDI_CHANNEL_COUNT
        # Latest device-reported fader values for the LCD only. Command
        # targets remain in _raw_channels and keep their touch/movement gate.
        self._observed_raw_channels = [0] * MIDI_CHANNEL_COUNT
        self._channels = [0.0] * MIDI_CHANNEL_COUNT
        self._filter_stage1 = [0.0] * MIDI_CHANNEL_COUNT
        self._filter_stage2 = [0.0] * MIDI_CHANNEL_COUNT
        self._filter_last_at: List[float | None] = [None] * MIDI_CHANNEL_COUNT
        self._touch = [False] * MIDI_CHANNEL_COUNT
        self._physical_touch = [False] * MIDI_CHANNEL_COUNT
        self._fader_moving = [False] * MIDI_CHANNEL_COUNT
        self._bridge_fader_syncing = [False] * MIDI_CHANNEL_COUNT
        self._fader_input_generation = [0] * MIDI_CHANNEL_COUNT
        self._dial = [0] * MIDI_CHANNEL_COUNT
        self._btn0 = [False] * MIDI_CHANNEL_COUNT
        self._btn1 = [False] * MIDI_CHANNEL_COUNT
        self._btn2 = [False] * MIDI_CHANNEL_COUNT
        self._btn3 = [False] * MIDI_CHANNEL_COUNT
        self._previous_btn0 = [False] * MIDI_CHANNEL_COUNT
        self._previous_btn3 = [False] * MIDI_CHANNEL_COUNT
        self._previous_dial = [0] * MIDI_CHANNEL_COUNT
        self._confirmed = [False] * MIDI_CHANNEL_COUNT
        self._control_enabled = [False] * MIDI_CHANNEL_COUNT
        self._studio_select_locked = False
        self._motion_run_state = 'idle'
        self._motion_run_request_source = ''
        self._motion_studio_state = 'idle'
        self._playback_phase = 'idle'
        self._playback_follow_enabled = [False] * MIDI_CHANNEL_COUNT
        self._playback_follow_targets: List[int | None] = [
            None
        ] * MIDI_CHANNEL_COUNT
        self._playback_follow_resume_not_before = [
            0.0
        ] * MIDI_CHANNEL_COUNT
        self._studio_zero_fader_targets = [0] * MIDI_CHANNEL_COUNT
        self._final_output_values = [0.0] * MIDI_CHANNEL_COUNT
        # Runtime always starts with SELECT OFF, so park all physical faders.
        self._pending_fader_positions: List[int | None] = [0] * MIDI_CHANNEL_COUNT
        self._pending_fader_input_generations = [0] * MIDI_CHANNEL_COUNT
        self._fader_sync_targets: List[int | None] = [None] * MIDI_CHANNEL_COUNT
        self._awaiting_fader_sync = [False] * MIDI_CHANNEL_COUNT
        self._fader_sync_not_before = [0.0] * MIDI_CHANNEL_COUNT
        self._fader_parking = [False] * MIDI_CHANNEL_COUNT
        self._fader_park_last_command_at = [0.0] * MIDI_CHANNEL_COUNT
        self._fader_zero_required = [True] * MIDI_CHANNEL_COUNT
        self._last_select_toggle_at = [0.0] * MIDI_CHANNEL_COUNT
        self._last_motor_command_at = [0.0] * MIDI_CHANNEL_COUNT
        self._last_motor_target: List[float | None] = [None] * MIDI_CHANNEL_COUNT
        self._last_group_motor_targets: List[Dict[int, float]] = [
            {} for _ in range(MIDI_CHANNEL_COUNT)
        ]
        self._approved_motion_values: List[Dict[str, float]] = [
            {} for _ in range(MIDI_CHANNEL_COUNT)
        ]
        self._approved_motor_targets: List[Dict[int, float]] = [
            {} for _ in range(MIDI_CHANNEL_COUNT)
        ]
        self._approved_command_stamp = [0.0] * MIDI_CHANNEL_COUNT
        # Logical values accepted from MIDI commands. SELECT may instead use
        # validated source-topic state or reconstruct from live motor feedback.
        self._current_motion_values: Dict[str, float] = {}
        # Exact values received from the same source topic used by monitoring.
        # The MIDI LCD consumes this cache directly; the web bridge is not in
        # the data path.
        self._source_motion_values: Dict[str, float] = {}
        self._source_motion_value_stamps: Dict[str, float] = {}
        self._source_motion_value_context: tuple[str, int] = ('', 0)
        self._pending_motor_requests: Dict[Any, Dict[str, Any]] = {}
        self._motor_follow_active = [False] * MIDI_CHANNEL_COUNT
        self._pickup_pending = [False] * MIDI_CHANNEL_COUNT
        self._pickup_reference_motion = [None] * MIDI_CHANNEL_COUNT
        self._pickup_previous_motion = [None] * MIDI_CHANNEL_COUNT
        self._pickup_reference_source = [''] * MIDI_CHANNEL_COUNT
        self._motor_command_state = ['inactive'] * MIDI_CHANNEL_COUNT
        self._motor_command_message = [''] * MIDI_CHANNEL_COUNT
        self._request_sequence = 0
        self._motor_angle_mode = [False] * MIDI_CHANNEL_COUNT
        self._latest_motion_state: Dict[str, Any] = {}
        self._selected_mapping_file_id = ''
        self._run_mapping_file_id = ''
        self._preferred_mapping_file_id = ''
        self._axis_registry = MotionAxisRegistry(self._mappings_dir)
        self._axis_registry.refresh()
        self._selected_mapping_file_id = self._axis_registry.file_id
        self._preferred_mapping_file_id = self._selected_mapping_file_id
        self._bank_config_file: Path | None = self._mapping_file_path_or_none(
            self._selected_mapping_file_id
        )
        self._last_axis_registry_refresh = 0.0
        self._last_feedback = [None] * MIDI_CHANNEL_COUNT
        self._banks = MidiBankManager()
        self._bank_file_loaded = False
        self._bank_file_dirty = False
        try:
            stored_banks = (
                load_midi_banks(self._bank_config_file)
                if self._bank_config_file is not None else None
            )
            if stored_banks is not None:
                self._banks.replace_state(stored_banks)
                self._bank_file_loaded = True
        except (OSError, ValueError, yaml.YAMLError) as exc:
            self.get_logger().error(f'failed to load MIDI banks from motion mapping: {exc}')

        self._state_publisher = self.create_publisher(String, self.state_topic, 10)
        self._response_publisher = self.create_publisher(String, self.response_topic, 10)
        self._feedback_publisher = self.create_publisher(String, self.feedback_topic, 10)
        self._connection_command_publisher = self.create_publisher(
            String, self.connection_command_topic, 10
        )
        self._motor_request_publisher = self.create_publisher(
            String, self.motor_request_topic, 10
        )
        motion_value_qos = QoSProfile(
            depth=10,
            reliability=ReliabilityPolicy.RELIABLE,
            durability=DurabilityPolicy.TRANSIENT_LOCAL,
        )
        self._motion_value_publisher = self.create_publisher(
            String, self.motion_value_topic, motion_value_qos
        )
        self._motion_value_subscription = self.create_subscription(
            String,
            self.motion_value_topic,
            self._motion_value_callback,
            motion_value_qos,
        )
        midi_qos = QoSProfile(depth=1, reliability=ReliabilityPolicy.BEST_EFFORT)
        self._midi_subscription = self.create_subscription(
            Midi,
            self.input_topic,
            self._midi_callback,
            midi_qos,
        )
        self._input_state_subscription = self.create_subscription(
            String, self.input_state_topic, self._input_state_callback, 10
        )
        connection_state_qos = QoSProfile(
            depth=1,
            reliability=ReliabilityPolicy.RELIABLE,
            durability=DurabilityPolicy.TRANSIENT_LOCAL,
        )
        self._connection_state_subscription = self.create_subscription(
            String,
            self.connection_state_topic,
            self._connection_state_callback,
            connection_state_qos,
        )
        self._request_subscription = self.create_subscription(
            String,
            self.request_topic,
            self._request_callback,
            10,
        )
        self._motion_state_subscription = self.create_subscription(
            String, self.motion_state_topic, self._motion_state_callback, 10
        )
        self._motion_run_status_subscription = self.create_subscription(
            String, self.motion_run_status_topic, self._motion_run_status_callback, 10
        )
        self._motion_studio_status_subscription = self.create_subscription(
            String,
            self.motion_studio_status_topic,
            self._motion_studio_status_callback,
            10,
        )
        self._motion_mapping_response_subscription = self.create_subscription(
            String,
            self.motion_mapping_response_topic,
            self._motion_mapping_response_callback,
            10,
        )
        self._motor_result_subscription = self.create_subscription(
            String, self.motor_result_topic, self._motor_result_callback, 10
        )
        self._timer = self.create_timer(1.0 / self.publish_hz, self._publish_state)
        self._motor_command_timer = self.create_timer(
            MIDI_COMMAND_PERIOD_SEC, self._publish_motor_request_batch
        )
        self.get_logger().info(
            f'MIDI monitor ready: input={self.input_topic}, state={self.state_topic}, '
            f'feedback={self.feedback_topic}, '
            f'motor_output=via_motion_supervisor, bank_file={self._bank_config_file}, '
            f'banks_loaded={self._bank_file_loaded}'
        )

    def _validated_mapping(self, mappings: Any) -> List[Dict[str, Any]]:
        return MidiBankManager.validate_mappings(mappings)

    @staticmethod
    def _array_value(values: Any, index: int, fallback: Any) -> Any:
        return values[index] if index < len(values) else fallback

    def _mapping_group_locked(
        self,
        mapping: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        self._ensure_linked_runtime_state_locked()
        group = []
        for motion_id in mapping_motion_ids(mapping):
            row = self._axis_registry.mapping(motion_id)
            motor_axis = self._axis_registry.motor_axis(motion_id)
            if row is None or motor_axis is None:
                raise ValueError(f'{motion_id}: 모션축 설정에 매칭되지 않았습니다')
            motion_state = getattr(self, '_latest_motion_state', {})
            motor = self._motor_for_axis(motion_state, motor_axis)
            if motor is None:
                raise ValueError(f'{motion_id}: 현재 모터 정보를 확인할 수 없습니다')
            group.append({
                'motion_id': motion_id,
                'row': row,
                'axis': motor_axis,
                'motor': motor,
            })
        if not group:
            raise ValueError('연결할 Motion ID가 없습니다')
        axes = [int(item['axis']) for item in group]
        if len(set(axes)) != len(axes):
            raise ValueError('연동 Motion ID가 같은 모터축을 중복으로 가리킵니다')
        require_same_motion_ranges([item['row'] for item in group])
        safe_motion_range_for_group(group)
        return group

    def _ensure_linked_runtime_state_locked(self) -> None:
        if not hasattr(self, '_last_group_motor_targets'):
            self._last_group_motor_targets = [
                {} for _ in range(MIDI_CHANNEL_COUNT)
            ]

    def _ensure_pickup_state_locked(self) -> None:
        if not hasattr(self, '_pickup_pending'):
            self._pickup_pending = [False] * MIDI_CHANNEL_COUNT
        if not hasattr(self, '_pickup_reference_motion'):
            self._pickup_reference_motion = [None] * MIDI_CHANNEL_COUNT
        if not hasattr(self, '_pickup_previous_motion'):
            self._pickup_previous_motion = [None] * MIDI_CHANNEL_COUNT
        if not hasattr(self, '_pickup_reference_source'):
            self._pickup_reference_source = [''] * MIDI_CHANNEL_COUNT

    def _clear_pickup_state_locked(self, channel: int) -> None:
        self._ensure_pickup_state_locked()
        self._pickup_pending[channel] = False
        self._pickup_reference_motion[channel] = None
        self._pickup_previous_motion[channel] = None
        self._pickup_reference_source[channel] = ''

    def _ensure_fader_parking_state_locked(self) -> None:
        if not hasattr(self, '_fader_parking'):
            self._fader_parking = [False] * MIDI_CHANNEL_COUNT
        if not hasattr(self, '_fader_park_last_command_at'):
            self._fader_park_last_command_at = [0.0] * MIDI_CHANNEL_COUNT
        if not hasattr(self, '_fader_park_started_at'):
            self._fader_park_started_at = [0.0] * MIDI_CHANNEL_COUNT
        if not hasattr(self, '_fader_zero_required'):
            self._fader_zero_required = [False] * MIDI_CHANNEL_COUNT

    def _ensure_fader_input_generation_locked(self) -> None:
        if not hasattr(self, '_fader_input_generation'):
            self._fader_input_generation = [0] * MIDI_CHANNEL_COUNT
        if not hasattr(self, '_pending_fader_input_generations'):
            self._pending_fader_input_generations = list(
                self._fader_input_generation
            )

    def _queue_fader_position_locked(
        self, channel: int, position: int | None
    ) -> None:
        self._ensure_fader_input_generation_locked()
        self._pending_fader_positions[channel] = position
        self._pending_fader_input_generations[channel] = int(
            self._fader_input_generation[channel]
        )

    def _start_fader_parking_locked(self, channel: int, now: float) -> None:
        self._ensure_fader_parking_state_locked()
        if not hasattr(self, '_last_feedback'):
            self._last_feedback = [None] * MIDI_CHANNEL_COUNT
        self._fader_zero_required[channel] = True
        self._fader_parking[channel] = True
        self._fader_park_started_at[channel] = now
        self._fader_park_last_command_at[channel] = now
        self._queue_fader_position_locked(channel, 0)
        self._fader_sync_targets[channel] = 0
        self._awaiting_fader_sync[channel] = True
        self._fader_sync_not_before[channel] = now + FADER_SYNC_MIN_DURATION_SEC
        self._last_feedback[channel] = None
        self._motor_command_state[channel] = 'parking_fader'
        self._motor_command_message[channel] = 'SELECT 해제 · 페이더 0 복귀 중'

    def _queue_normal_fader_zero_locked(self, channel: int, now: float) -> None:
        """Send a best-effort zero command without blocking the next SELECT."""
        self._ensure_fader_parking_state_locked()
        if not hasattr(self, '_last_feedback'):
            self._last_feedback = [None] * MIDI_CHANNEL_COUNT
        self._fader_zero_required[channel] = True
        self._fader_parking[channel] = False
        self._fader_park_started_at[channel] = 0.0
        self._fader_park_last_command_at[channel] = 0.0
        self._queue_fader_position_locked(channel, 0)
        self._fader_sync_targets[channel] = 0
        self._awaiting_fader_sync[channel] = True
        self._fader_sync_not_before[channel] = now + FADER_SYNC_MIN_DURATION_SEC
        self._last_feedback[channel] = None
        self._motor_command_state[channel] = 'inactive'
        self._motor_command_message[channel] = (
            'SELECT 사용 가능 · 페이더 0 이동 명령 전송(도착 피드백 없음)'
        )

    def _update_fader_parking_locked(
        self, channel: int, raw: int, now: float
    ) -> bool:
        """Advance one mandatory SELECT-off park and return its prior state."""
        self._ensure_fader_parking_state_locked()
        was_parking = self._fader_parking[channel]
        if not was_parking:
            return False
        self._raw_channels[channel] = raw
        self._channels[channel] = float(raw)
        self._filter_stage1[channel] = float(raw)
        self._filter_stage2[channel] = float(raw)
        physically_busy = (
            self._physical_touch[channel]
            or self._fader_moving[channel]
            or self._bridge_fader_syncing[channel]
        )
        if raw <= FADER_PARK_TOLERANCE_RAW and not physically_busy:
            self._fader_parking[channel] = False
            self._fader_park_started_at[channel] = 0.0
            self._fader_park_last_command_at[channel] = 0.0
            self._fader_sync_targets[channel] = None
            self._awaiting_fader_sync[channel] = False
            self._fader_sync_not_before[channel] = 0.0
            self._raw_channels[channel] = 0
            self._channels[channel] = 0.0
            self._filter_stage1[channel] = 0.0
            self._filter_stage2[channel] = 0.0
            self._motor_command_state[channel] = 'inactive'
            self._motor_command_message[channel] = (
                'SELECT 사용 가능 · 페이더 0 이동 명령 전송'
                '(물리 도착 피드백 없음)'
            )
            return True
        if (
            not bool(getattr(self, '_studio_select_locked', False))
            and self._fader_park_started_at[channel] > 0.0
            and now - self._fader_park_started_at[channel]
            >= FADER_PARK_TIMEOUT_SEC
        ):
            # A failed motorized-fader return must not permanently lock the
            # physical SELECT button. Motor ownership is already released;
            # stop retrying and let the next SELECT perform a fresh pickup
            # from the logical Motion ID value before motor commands resume.
            self._fader_parking[channel] = False
            self._fader_park_started_at[channel] = 0.0
            self._fader_park_last_command_at[channel] = 0.0
            self._queue_fader_position_locked(channel, None)
            self._fader_sync_targets[channel] = None
            self._awaiting_fader_sync[channel] = False
            self._fader_sync_not_before[channel] = 0.0
            self._last_feedback[channel] = None
            self._motor_command_state[channel] = 'fader_park_failed'
            self._motor_command_message[channel] = (
                f'페이더 0 복귀 실패(현재 {raw}) · SELECT 재시도 가능'
            )
            return False
        if (
            not self._physical_touch[channel]
            and not self._fader_moving[channel]
            and now - self._fader_park_last_command_at[channel]
            >= FADER_PARK_RETRY_SEC
        ):
            self._queue_fader_position_locked(channel, 0)
            self._fader_sync_targets[channel] = 0
            self._awaiting_fader_sync[channel] = True
            self._fader_sync_not_before[channel] = now + FADER_SYNC_MIN_DURATION_SEC
            self._fader_park_last_command_at[channel] = now
            self._last_feedback[channel] = None
        return True

    def _request_motor_hold_locked(self, channel: int, axes: List[int]) -> None:
        publisher = getattr(self, '_motor_request_publisher', None)
        if publisher is None or not axes:
            return
        self._request_sequence = int(getattr(self, '_request_sequence', 0)) + 1
        generation = int(
            getattr(self, '_execution_context', {}).get('project_generation') or 0
        )
        self._publish_json(publisher, {
            'request_id': generation_mod.new_request_id(
                'midi-hold', generation, f'{channel}-{self._request_sequence}'
            ),
            'project_generation': generation,
            'channel': channel,
            'hold_axes': sorted(set(int(axis) for axis in axes)),
        })

    def _clear_pending_channel_locked(self, channel: int) -> None:
        self._pending_motor_requests = {
            key: target
            for key, target in self._pending_motor_requests.items()
            if int(target.get('channel', -1)) != channel
        }

    def _midi_callback(self, msg: Midi) -> None:
        now = time.monotonic()
        motor_request_payload = None
        with self._lock:
            self._ensure_pickup_state_locked()
            self._ensure_fader_parking_state_locked()
            select_lock_reason = self._select_lock_reason_locked()
            if not hasattr(self, '_observed_raw_channels'):
                self._observed_raw_channels = list(self._raw_channels)
            mappings = self._banks.active_bank()['mappings']
            for channel in range(MIDI_CHANNEL_COUNT):
                raw = max(
                    MIDI_VALUE_MIN,
                    min(MIDI_VALUE_MAX, int(self._array_value(msg.channel, channel, 0))),
                )
                self._observed_raw_channels[channel] = raw
                input_valid = bool(self._array_value(msg.touch, channel, False))
                self._touch[channel] = input_valid
                if self._studio_select_locked:
                    input_valid = False
                    self._touch[channel] = False
                    # The initial prepare call owns the zero command and the
                    # parking state owns retries. Re-queueing zero on every
                    # MIDI position echo makes an already-arrived fader look
                    # as if it started moving again, so initialization can
                    # wait forever.
                    if (
                        not self._fader_parking[channel]
                        and raw > FADER_PARK_TOLERANCE_RAW
                    ):
                        self._start_fader_parking_locked(channel, now)
                elif (
                    not self._control_enabled[channel]
                    and self._fader_zero_required[channel]
                    and not self._fader_parking[channel]
                    and raw > FADER_PARK_TOLERANCE_RAW
                    and input_valid
                    and self._motor_command_state[channel] != 'fader_park_failed'
                ):
                    # The bridge cannot confirm host-driven arrival. A late
                    # non-zero physical report proves the earlier zero did not
                    # hold. Send zero once more, expose the failure, and stop
                    # automatic retries instead of cycling on assumed success.
                    self._queue_normal_fader_zero_locked(channel, now)
                    self._awaiting_fader_sync[channel] = False
                    self._fader_sync_targets[channel] = None
                    self._fader_sync_not_before[channel] = 0.0
                    self._motor_command_state[channel] = 'fader_park_failed'
                    self._motor_command_message[channel] = (
                        f'페이더 0 복귀 실패(현재 {raw}) · 0 재명령 후 정지'
                    )
                    input_valid = False
                    self._touch[channel] = False
                was_parking = self._update_fader_parking_locked(channel, raw, now)
                if self._studio_select_locked and not was_parking:
                    # Even a channel that was already parked must reflect its
                    # latest physical position. Otherwise a stale pre-lock raw
                    # value can keep the all-channel zero check blocked.
                    self._raw_channels[channel] = raw
                    self._channels[channel] = float(raw)
                    self._filter_stage1[channel] = float(raw)
                    self._filter_stage2[channel] = float(raw)
                if was_parking:
                    input_valid = False
                    self._touch[channel] = False
                bridge_syncing = self._bridge_fader_syncing[channel]
                if input_valid and self._awaiting_fader_sync[channel]:
                    # A real hand input takes ownership immediately.  Waiting
                    # for the previously commanded pickup/park position here
                    # drops the first movement made just after SELECT and can
                    # leave the motor at its old position.
                    self._queue_fader_position_locked(channel, None)
                    self._fader_sync_targets[channel] = None
                    self._awaiting_fader_sync[channel] = False
                    self._fader_sync_not_before[channel] = 0.0
                    if self._pickup_pending[channel]:
                        self._motor_command_state[channel] = 'waiting_pickup'
                        self._motor_command_message[channel] = (
                            '사용자 페이더 조작 감지 · Pickup 기준 위치 대기'
                        )
                    else:
                        self._motor_command_state[channel] = 'ready'
                        self._motor_command_message[channel] = '사용자 페이더 조작 감지'
                if (
                    not self._fader_parking[channel]
                    and
                    self._awaiting_fader_sync[channel]
                    and now >= self._fader_sync_not_before[channel]
                    and not bridge_syncing
                ):
                    self._awaiting_fader_sync[channel] = False
                    self._fader_sync_targets[channel] = None
                    self._fader_sync_not_before[channel] = 0.0
                    if self._pickup_pending[channel]:
                        self._motor_command_state[channel] = 'waiting_pickup'
                        self._motor_command_message[channel] = (
                            'Pickup 기준 위치로 페이더를 이동하세요'
                        )
                    elif self._control_enabled[channel]:
                        self._motor_command_state[channel] = 'ready'
                        self._motor_command_message[channel] = '페이더 조작 대기'
                    else:
                        self._motor_command_state[channel] = 'inactive'
                        self._motor_command_message[channel] = 'SELECT 사용 가능'
                # input_valid is physical touch OR user fader movement. The
                # bridge excludes only target-matched motor synchronization.
                if input_valid and not self._awaiting_fader_sync[channel]:
                    self._raw_channels[channel] = raw
                # Keep advancing toward the last hand-touched target after
                # release. Untouched device/motor position changes never
                # replace that target.
                target = self._raw_channels[channel]
                last_at = self._filter_last_at[channel]
                if last_at is None:
                    filtered = float(target)
                    stage1 = filtered
                    stage2 = filtered
                else:
                    filtered, stage1, stage2 = second_order_low_pass(
                        target,
                        mappings[channel]['filter_level'],
                        now - last_at,
                        self._filter_stage1[channel],
                        self._filter_stage2[channel],
                    )
                self._channels[channel] = filtered
                self._filter_stage1[channel] = stage1
                self._filter_stage2[channel] = stage2
                self._filter_last_at[channel] = now
                self._dial[channel] = int(self._array_value(msg.dial, channel, 0))
                self._btn0[channel] = bool(self._array_value(msg.btn0, channel, False))
                self._btn1[channel] = bool(self._array_value(msg.btn1, channel, False))
                self._btn2[channel] = bool(self._array_value(msg.btn2, channel, False))
                self._btn3[channel] = bool(self._array_value(msg.btn3, channel, False))
                self._confirmed[channel] = self._confirmed[channel] or input_valid

                select_pressed = self._btn3[channel]
                select_rising = select_pressed and not self._previous_btn3[channel]
                select_allowed = (
                    now - self._last_select_toggle_at[channel]
                    >= SELECT_TOGGLE_DEBOUNCE_SEC
                )
                if (
                    select_rising
                    and select_allowed
                    and not select_lock_reason
                    and self._fader_parking[channel]
                ):
                    # SELECT-OFF has already released/held the robot motor.
                    # A new SELECT press is therefore a request to take the
                    # line back. Cancel the physical-zero park and perform a
                    # fresh pickup from the current logical Motion ID value instead of
                    # consuming this press as "still parking".
                    self._fader_parking[channel] = False
                    self._fader_park_started_at[channel] = 0.0
                    self._fader_park_last_command_at[channel] = 0.0
                    self._queue_fader_position_locked(channel, None)
                    self._fader_sync_targets[channel] = None
                    self._awaiting_fader_sync[channel] = False
                    self._fader_sync_not_before[channel] = 0.0
                    was_parking = False
                self._ensure_playback_follow_state_locked()
                if (
                    select_rising
                    and select_allowed
                    and not select_lock_reason
                    and self._execution_context_ready
                    and self._playback_phase == 'playing'
                    and not was_parking
                ):
                    self._last_select_toggle_at[channel] = now
                    try:
                        self._set_playback_follow_enabled_locked(
                            channel,
                            not self._playback_follow_enabled[channel],
                            mappings[channel],
                        )
                    except ValueError as exc:
                        self._playback_follow_enabled[channel] = False
                        self._playback_follow_targets[channel] = None
                        self._motor_command_state[channel] = (
                            'playback_follow_rejected'
                        )
                        self._motor_command_message[channel] = (
                            f'재생 위치 추종 불가: {exc}'
                        )
                elif (
                    select_rising
                    and select_allowed
                    and not select_lock_reason
                    and self._execution_context_ready
                    and not was_parking
                ):
                    self._last_select_toggle_at[channel] = now
                    currently_enabled = self._control_enabled[channel]
                    if currently_enabled:
                        self._deactivate_control_channel_locked(channel)
                    elif mappings[channel].get('enabled') is False:
                        self._motor_command_state[channel] = 'activation_rejected'
                        self._motor_command_message[channel] = (
                            '활성화 불가: 현재 뱅크에서 이 라인의 사용이 꺼져 있습니다'
                        )
                    else:
                        # Validate and calculate the pickup point before releasing
                        # another line which currently owns the same motor axis.
                        # A failed handover must leave the old owner untouched.
                        try:
                            group = self._mapping_group_locked(mappings[channel])
                            motion_value, pickup_source = (
                                self._pickup_reference_for_group_locked(group)
                            )
                            safe_range = safe_motion_range_for_group(group)
                            fader_target = raw_fader_for_motion(
                                motion_value,
                                group[0]['row'],
                                mappings[channel],
                                safe_range,
                            )
                        except ValueError as exc:
                            self._control_enabled[channel] = False
                            self._clear_pickup_state_locked(channel)
                            self._motor_command_state[channel] = 'activation_rejected'
                            self._motor_command_message[channel] = f'활성화 불가: {exc}'
                            self._clear_pending_channel_locked(channel)
                            self._motor_follow_active[channel] = False
                        else:
                            self._set_group_motion_value_locked(group, motion_value)
                            selected_axes = {int(item['axis']) for item in group}
                            # Multiple MIDI lines may include the same motor axis,
                            # but only one line may own that motor at runtime.
                            for other_channel, other_mapping in enumerate(mappings):
                                if other_channel == channel:
                                    continue
                                other_axes = {
                                    self._axis_registry.motor_axis(motion_id)
                                    for motion_id in mapping_motion_ids(other_mapping)
                                }
                                if (
                                    self._control_enabled[other_channel]
                                    and selected_axes.intersection(other_axes)
                                ):
                                    self._deactivate_control_channel_locked(other_channel)
                            self._control_enabled[channel] = True
                            self._fader_zero_required[channel] = False
                            self._pickup_pending[channel] = True
                            self._pickup_reference_motion[channel] = motion_value
                            self._pickup_previous_motion[channel] = None
                            self._pickup_reference_source[channel] = pickup_source
                            self._queue_fader_position_locked(
                                channel, fader_target
                            )
                            self._fader_sync_targets[channel] = fader_target
                            self._awaiting_fader_sync[channel] = True
                            self._fader_sync_not_before[channel] = (
                                now + FADER_SYNC_MIN_DURATION_SEC
                            )
                            self._motor_command_state[channel] = 'waiting_pickup'
                            self._motor_command_message[channel] = (
                                f'{len(group)}개 연동 축 Pickup 대기 · '
                                f'기준 {motion_value:.3f}°'
                            )
                            logical_targets = {
                                int(item['axis']): require_motion_value_within_limits(
                                    item['motion_id'],
                                    motion_value,
                                    item['row'],
                                    item['motor'],
                                )
                                for item in group
                            }
                            self._last_group_motor_targets[channel] = logical_targets
                            self._last_motor_target[channel] = logical_targets[
                                int(group[0]['axis'])
                            ]
                            self._motor_follow_active[channel] = False
                elif select_rising and select_lock_reason:
                    self._motor_command_state[channel] = (
                        'studio_initializing'
                        if self._studio_select_locked else 'select_locked'
                    )
                    self._motor_command_message[channel] = (
                        f'{select_lock_reason} · SELECT 입력 무시됨'
                    )
                elif select_rising and not self._execution_context_ready:
                    self._motor_command_state[channel] = 'context_waiting'
                    self._motor_command_message[channel] = (
                        '현재 프로젝트 실행 컨텍스트 적용 대기 중'
                    )
                elif select_rising and was_parking:
                    self._motor_command_state[channel] = 'parking_fader'
                    self._motor_command_message[channel] = (
                        '페이더 0 복귀 완료 후 SELECT를 다시 누르세요'
                    )
                self._previous_btn3[channel] = select_pressed

                rec_pressed = self._btn0[channel]
                if (
                    rec_pressed
                    and not self._previous_btn0[channel]
                    and not self._studio_select_locked
                ):
                    motion_id = mappings[channel]['motion_id']
                    if self._axis_registry.motor_axis(motion_id) is not None:
                        self._motor_angle_mode[channel] = not self._motor_angle_mode[channel]
                    else:
                        self._motor_angle_mode[channel] = False
                self._previous_btn0[channel] = rec_pressed

                dial_delta = self._dial[channel] - self._previous_dial[channel]
                self._previous_dial[channel] = self._dial[channel]
                if dial_delta and self._control_enabled[channel]:
                    mappings[channel]['filter_level'] = max(
                        0,
                        min(
                            FILTER_LEVEL_MAX,
                            int(mappings[channel]['filter_level']) + dial_delta,
                        ),
                    )
                    self._banks.update_bank(
                        self._banks.snapshot()['active_bank_id'], mappings=mappings
                    )
                    self._bank_file_dirty = True

                pickup_completed_now = False
                if (
                    input_valid
                    and self._control_enabled[channel]
                    and self._pickup_pending[channel]
                    and not self._awaiting_fader_sync[channel]
                ):
                    try:
                        group = self._mapping_group_locked(mappings[channel])
                        safe_range = safe_motion_range_for_group(group)
                        pickup_output = self._filtered_output_14bit(
                            float(raw), mappings[channel]
                        )
                        pickup_motion = motion_value_from_output(
                            pickup_output, group[0]['row'], safe_range
                        )
                        reference = float(
                            self._pickup_reference_motion[channel]
                        )
                    except (TypeError, ValueError) as exc:
                        self._motor_follow_active[channel] = False
                        self._clear_pending_channel_locked(channel)
                        self._motor_command_state[channel] = 'pickup_rejected'
                        self._motor_command_message[channel] = str(exc)
                    else:
                        previous = self._pickup_previous_motion[channel]
                        if self._pickup_reached(
                            previous,
                            pickup_motion,
                            reference,
                            self._pickup_tolerance(),
                        ):
                            self._pickup_pending[channel] = False
                            self._pickup_previous_motion[channel] = pickup_motion
                            self._raw_channels[channel] = raw
                            self._channels[channel] = float(raw)
                            self._filter_stage1[channel] = float(raw)
                            self._filter_stage2[channel] = float(raw)
                            self._filter_last_at[channel] = now
                            self._motor_follow_active[channel] = False
                            self._motor_command_state[channel] = 'pickup_complete'
                            self._motor_command_message[channel] = (
                                f'Pickup 완료 {pickup_motion:.3f}° · '
                                '다음 페이더 움직임부터 모터 제어'
                            )
                            pickup_completed_now = True
                        else:
                            self._pickup_previous_motion[channel] = pickup_motion
                            self._motor_follow_active[channel] = False
                            self._clear_pending_channel_locked(channel)
                            self._motor_command_state[channel] = 'waiting_pickup'
                            self._motor_command_message[channel] = (
                                f'Pickup 대기 · 현재 {pickup_motion:.3f}° / '
                                f'기준 {reference:.3f}°'
                            )

                if (
                    input_valid
                    and self._control_enabled[channel]
                    and not self._pickup_pending[channel]
                    and not pickup_completed_now
                ):
                    self._motor_follow_active[channel] = True

                if (
                    self._motor_follow_active[channel]
                    and self._control_enabled[channel]
                    and not self._awaiting_fader_sync[channel]
                ):
                    final_output = self._filtered_output_14bit(
                        self._channels[channel], mappings[channel]
                    )
                    try:
                        group = self._mapping_group_locked(mappings[channel])
                        safe_range = safe_motion_range_for_group(group)
                        motion_value = motion_value_from_output(
                            final_output, group[0]['row'], safe_range
                        )
                        desired_targets = {
                            int(item['axis']): (
                                require_motion_value_within_limits(
                                    item['motion_id'],
                                    motion_value,
                                    item['row'],
                                    item['motor'],
                                )
                                if isinstance(item.get('motor'), dict)
                                else motor_target_from_motion(motion_value, item['row'])
                            )
                            for item in group
                        }
                    except ValueError as exc:
                        self._clear_pending_channel_locked(channel)
                        self._motor_command_state[channel] = 'rejected'
                        self._motor_command_message[channel] = str(exc)
                    else:
                        previous_targets = self._last_group_motor_targets[channel]
                        unchanged = previous_targets and all(
                            axis in previous_targets
                            and abs(target - previous_targets[axis])
                            < MIDI_COMMAND_DEADBAND_DEG
                            for axis, target in desired_targets.items()
                        )
                        if unchanged:
                            if (
                                not input_valid
                                and abs(
                                    self._channels[channel]
                                    - self._raw_channels[channel]
                                ) < 0.5
                            ):
                                self._motor_follow_active[channel] = False
                                self._clear_pending_channel_locked(channel)
                            continue
                        self._clear_pending_channel_locked(channel)
                        for item in group:
                            axis = int(item['axis'])
                            self._request_sequence += 1
                            generation = int(
                                getattr(self, '_execution_context', {}).get(
                                    'project_generation'
                                ) or 0
                            )
                            self._pending_motor_requests[(channel, axis)] = {
                                'request_id': (
                                    generation_mod.new_request_id(
                                        'midi', generation,
                                        f'{channel}-{self._request_sequence}',
                                    )
                                ),
                                'project_generation': generation,
                                'channel': channel,
                                'motion_id': item['motion_id'],
                                'mapping_file_id': self._axis_registry.file_id,
                                'axis': axis,
                                'motion_deg': motion_value,
                                'target_deg': desired_targets[axis],
                            }
                        self._last_group_motor_targets[channel] = desired_targets
                        self._last_motor_target[channel] = desired_targets[
                            int(group[0]['axis'])
                        ]
                        self._motor_command_state[channel] = 'commanding'
                        self._motor_command_message[channel] = (
                            f'{len(group)}개 연동 축 위치 명령 전달 대기'
                        )
            self._last_received_monotonic = now
            self._last_received_wall = time.time()
            # Publish the command produced by this MIDI input immediately.
            # A continuously-ready MIDI subscription can delay an independent
            # timer callback, leaving a valid command stuck in "pending".
            motor_request_payload = self._take_motor_request_batch_locked()

        if motor_request_payload is not None:
            self._publish_json(self._motor_request_publisher, motor_request_payload)

    def _take_motor_request_batch_locked(self) -> Dict[str, Any] | None:
        if not self._pending_motor_requests:
            return None
        targets = list(self._pending_motor_requests.values())
        self._pending_motor_requests.clear()
        now = time.monotonic()
        for target in targets:
            channel = int(target['channel'])
            self._last_motor_command_at[channel] = now
            self._motor_command_message[channel] = '다축 모터 위치 명령 전달 중'
        self._request_sequence += 1
        generation = int(
            getattr(self, '_execution_context', {}).get('project_generation') or 0
        )
        request_id = generation_mod.new_request_id(
            'midi-batch', generation, self._request_sequence
        )
        counts: Dict[int, int] = {}
        for target in targets:
            channel = int(target['channel'])
            counts[channel] = counts.get(channel, 0) + 1
        return {
            'request_id': request_id,
            'project_generation': generation,
            'targets': targets,
            'atomic_channels': [
                channel for channel, count in counts.items() if count > 1
            ],
        }

    def _publish_motor_request_batch(self) -> None:
        with self._lock:
            payload = self._take_motor_request_batch_locked()
        if payload is not None:
            self._publish_json(self._motor_request_publisher, payload)

    def _deactivate_control_channel_locked(
        self, channel: int, *, request_motor_hold: bool = True
    ) -> None:
        """Release one MIDI line and park its motorized fader at zero."""
        self._ensure_linked_runtime_state_locked()
        self._clear_pickup_state_locked(channel)
        axes = list(self._last_group_motor_targets[channel])
        if request_motor_hold:
            self._request_motor_hold_locked(channel, axes)
        self._control_enabled[channel] = False
        self._last_motor_target[channel] = None
        self._last_group_motor_targets[channel] = {}
        self._ensure_approved_command_state_locked()
        self._approved_motion_values[channel] = {}
        self._approved_motor_targets[channel] = {}
        self._approved_command_stamp[channel] = 0.0
        self._clear_pending_channel_locked(channel)
        self._motor_follow_active[channel] = False
        # A SELECT press can arrive while the bridge still considers the last
        # hand movement active. The bridge deliberately drops fader commands
        # during that short protection window, so SELECT OFF must use the
        # retrying park path instead of a one-shot zero command.
        self._start_fader_parking_locked(channel, time.monotonic())

    def _select_lock_reason_locked(self) -> str:
        if bool(getattr(self, '_studio_select_locked', False)):
            return '모션 녹화 초기화 중'
        self._ensure_playback_follow_state_locked()
        if self._playback_phase == 'initializing':
            return '초기 위치 이동 중'
        if self._playback_phase == 'stopping':
            return '모션 정지 중'
        return ''

    def _ensure_playback_follow_state_locked(self) -> None:
        if not hasattr(self, '_motion_run_state'):
            self._motion_run_state = 'idle'
        if not hasattr(self, '_motion_run_request_source'):
            self._motion_run_request_source = ''
        if not hasattr(self, '_motion_studio_state'):
            self._motion_studio_state = 'idle'
        if not hasattr(self, '_playback_phase'):
            self._playback_phase = 'idle'
        if not hasattr(self, '_playback_follow_enabled'):
            self._playback_follow_enabled = [False] * MIDI_CHANNEL_COUNT
        if not hasattr(self, '_playback_follow_targets'):
            self._playback_follow_targets = [None] * MIDI_CHANNEL_COUNT
        if not hasattr(self, '_playback_follow_resume_not_before'):
            self._playback_follow_resume_not_before = [
                0.0
            ] * MIDI_CHANNEL_COUNT

    def _force_all_select_off_for_playback_locked(self, message: str) -> None:
        """Release MIDI ownership/follow state without commanding robot motors."""
        self._ensure_playback_follow_state_locked()
        now = time.monotonic()
        for channel in range(MIDI_CHANNEL_COUNT):
            was_selected = bool(
                self._control_enabled[channel]
                or self._playback_follow_enabled[channel]
            )
            if self._control_enabled[channel]:
                self._deactivate_control_channel_locked(
                    channel, request_motor_hold=False
                )
            self._playback_follow_enabled[channel] = False
            self._playback_follow_targets[channel] = None
            self._playback_follow_resume_not_before[channel] = 0.0
            self._clear_pending_channel_locked(channel)
            self._motor_follow_active[channel] = False
            if was_selected and not self._fader_parking[channel]:
                self._start_fader_parking_locked(channel, now)
            if was_selected:
                self._motor_command_state[channel] = 'playback_select_off'
                self._motor_command_message[channel] = message
                self._last_feedback[channel] = None
        self._previous_btn3 = list(getattr(
            self, '_btn3', [False] * MIDI_CHANNEL_COUNT
        ))

    def _combined_playback_phase_locked(self) -> str:
        """Combine general-motion and Motion Studio into one MIDI lifecycle."""
        self._ensure_playback_follow_state_locked()
        studio_state = self._motion_studio_state
        run_state = self._motion_run_state
        run_terminal = run_state in {
            'idle', 'ready', 'initialized', 'completed', 'stopped', 'error'
        }
        if studio_state == 'initializing':
            return 'initializing'
        if studio_state == 'playing':
            return 'idle' if run_terminal else 'playing'
        if studio_state == 'stopping':
            return 'stopping'
        if run_state == 'initializing':
            return 'initializing'
        if run_state in {'running', 'verifying'}:
            return 'playing'
        if run_state == 'stopping':
            return 'stopping'
        return 'idle'

    def _update_playback_phase_locked(self) -> None:
        self._ensure_playback_follow_state_locked()
        previous = self._playback_phase
        current = self._combined_playback_phase_locked()
        if current == previous:
            return
        self._playback_phase = current
        if current == 'initializing':
            message = '초기 위치 이동 시작 · SELECT OFF 및 잠금'
        elif current == 'playing':
            message = '모션 동작 시작 · SELECT OFF'
        elif current == 'stopping':
            message = '모션 정지 시작 · SELECT OFF'
        elif previous == 'initializing':
            message = '초기 위치 이동 종료 · SELECT OFF'
        else:
            message = '모션 동작 종료 · SELECT OFF'
        self._force_all_select_off_for_playback_locked(message)

    def _playback_follow_target_locked(
        self, channel: int, mapping: Dict[str, Any]
    ) -> int:
        group = self._mapping_group_locked(mapping)
        motion_ids = [str(item['motion_id']) for item in group]
        values = [
            _finite_float(self._source_motion_values.get(motion_id))
            for motion_id in motion_ids
        ]
        if any(value is None for value in values):
            raise ValueError('현재 모션 동작값을 아직 받지 못했습니다')
        logical_values = [float(value) for value in values if value is not None]
        if (
            max(logical_values) - min(logical_values)
            > LINKED_MOTION_VALUE_TOLERANCE_DEG
        ):
            raise ValueError('연동 Motion ID의 현재 동작값이 서로 다릅니다')
        motion_value = sum(logical_values) / len(logical_values)
        return raw_fader_for_motion(
            motion_value,
            group[0]['row'],
            mapping,
            safe_motion_range_for_group(group),
        )

    def _set_playback_follow_enabled_locked(
        self, channel: int, enabled: bool, mapping: Dict[str, Any]
    ) -> None:
        self._ensure_playback_follow_state_locked()
        now = time.monotonic()
        if not enabled:
            self._playback_follow_enabled[channel] = False
            self._playback_follow_targets[channel] = None
            self._playback_follow_resume_not_before[channel] = 0.0
            self._start_fader_parking_locked(channel, now)
            self._motor_command_state[channel] = 'playback_follow_off'
            self._motor_command_message[channel] = (
                '재생 위치 추종 OFF · 페이더 0 복귀 중'
            )
            return
        if mapping.get('enabled') is False:
            raise ValueError('현재 뱅크에서 이 라인의 사용이 꺼져 있습니다')
        target = self._playback_follow_target_locked(channel, mapping)
        self._fader_parking[channel] = False
        self._fader_zero_required[channel] = False
        self._fader_park_started_at[channel] = 0.0
        self._fader_park_last_command_at[channel] = 0.0
        self._control_enabled[channel] = False
        self._clear_pickup_state_locked(channel)
        self._clear_pending_channel_locked(channel)
        self._motor_follow_active[channel] = False
        self._playback_follow_enabled[channel] = True
        self._playback_follow_targets[channel] = target
        self._playback_follow_resume_not_before[channel] = 0.0
        self._queue_fader_position_locked(channel, target)
        self._motor_command_state[channel] = 'playback_follow'
        self._motor_command_message[channel] = '재생 모션값 읽기 전용 추종 중'
        self._last_feedback[channel] = None

    def _service_playback_follow_locked(self, now: float) -> None:
        self._ensure_playback_follow_state_locked()
        if self._playback_phase != 'playing':
            return
        mappings = self._banks.active_bank()['mappings']
        for channel, enabled in enumerate(self._playback_follow_enabled):
            if not enabled:
                continue
            try:
                target = self._playback_follow_target_locked(
                    channel, mappings[channel]
                )
            except ValueError as exc:
                self._motor_command_state[channel] = 'playback_follow_waiting'
                self._motor_command_message[channel] = f'재생 위치 추종 대기: {exc}'
                continue
            self._playback_follow_targets[channel] = target
            busy = bool(
                self._physical_touch[channel]
                or self._fader_moving[channel]
            )
            if busy:
                self._queue_fader_position_locked(channel, None)
                self._playback_follow_resume_not_before[channel] = max(
                    self._playback_follow_resume_not_before[channel],
                    now + PLAYBACK_FADER_RESUME_DELAY_SEC,
                )
                self._motor_command_message[channel] = (
                    '사용자 터치 중 · 재생 위치 추종 일시 정지'
                )
                continue
            if now < self._playback_follow_resume_not_before[channel]:
                continue
            pending = self._pending_fader_positions[channel]
            observed = self._observed_raw_channels[channel]
            if pending != target and abs(int(observed) - target) > 1:
                self._queue_fader_position_locked(channel, target)
            self._motor_command_state[channel] = 'playback_follow'
            self._motor_command_message[channel] = '재생 모션값 읽기 전용 추종 중'

    def _input_state_callback(self, msg: String) -> None:
        try:
            payload = json.loads(msg.data)
        except json.JSONDecodeError:
            return
        if not isinstance(payload, dict):
            return
        with self._lock:
            self._physical_touch = [
                bool(self._array_value(payload.get('physical_touch', []), channel, False))
                for channel in range(MIDI_CHANNEL_COUNT)
            ]
            self._fader_moving = [
                bool(self._array_value(payload.get('fader_moving', []), channel, False))
                for channel in range(MIDI_CHANNEL_COUNT)
            ]
            self._bridge_fader_syncing = [
                bool(self._array_value(payload.get('fader_syncing', []), channel, False))
                for channel in range(MIDI_CHANNEL_COUNT)
            ]
            self._ensure_fader_input_generation_locked()
            self._fader_input_generation = [
                max(
                    int(self._fader_input_generation[channel]),
                    int(self._array_value(
                        payload.get('fader_input_generation', []),
                        channel,
                        self._fader_input_generation[channel],
                    )),
                )
                for channel in range(MIDI_CHANNEL_COUNT)
            ]
            self._service_playback_follow_locked(time.monotonic())
            if payload.get('input_event_seen') is True:
                try:
                    age_sec = max(
                        0.0, float(payload.get('last_input_event_age_ms')) / 1000.0
                    )
                except (TypeError, ValueError):
                    age_sec = None
                if age_sec is not None:
                    self._last_physical_input_monotonic = time.monotonic() - age_sec
                    self._last_physical_input_wall = time.time() - age_sec

    def _connection_state_callback(self, msg: String) -> None:
        try:
            payload = json.loads(msg.data)
        except json.JSONDecodeError:
            return
        if not isinstance(payload, dict):
            return
        connected = bool(payload.get('connected'))
        message = str(payload.get('message') or '')
        def positive_float(key: str) -> float | None:
            try:
                value = float(payload.get(key))
            except (TypeError, ValueError):
                return None
            return value if value > 0.0 else None

        with self._lock:
            changed = connected != self._device_connected
            self._device_connected = connected
            self._device_connection_message = message
            self._device_last_connected_at = positive_float(
                'last_connected_at'
            )
            self._device_last_disconnected_at = positive_float(
                'last_disconnected_at'
            )
            self._device_last_power_reconnected_at = positive_float(
                'last_power_reconnected_at'
            )
            try:
                self._device_connection_count = max(
                    0, int(payload.get('connection_count') or 0)
                )
                self._device_power_reconnect_count = max(
                    0, int(payload.get('power_reconnect_count') or 0)
                )
            except (TypeError, ValueError):
                pass
            if changed:
                # A USB reconnect creates a new hardware session. Never retain
                # SELECT/motor ownership across it. Once the bridge has opened
                # the new port it has also cleared its touch/movement state, so
                # explicitly park every SELECT-OFF fader at zero.
                self._reset_runtime_controls_locked()
                if connected:
                    now = time.monotonic()
                    for channel in range(MIDI_CHANNEL_COUNT):
                        self._start_fader_parking_locked(channel, now)
                else:
                    self._pending_fader_positions = [None] * MIDI_CHANNEL_COUNT
                    self._pending_fader_input_generations = list(
                        self._fader_input_generation
                    )
                    self._touch = [False] * MIDI_CHANNEL_COUNT
                    self._physical_touch = [False] * MIDI_CHANNEL_COUNT
                    self._fader_moving = [False] * MIDI_CHANNEL_COUNT
                    self._bridge_fader_syncing = [False] * MIDI_CHANNEL_COUNT

    def _motor_result_callback(self, msg: String) -> None:
        try:
            payload = json.loads(msg.data)
        except json.JSONDecodeError:
            return
        if not isinstance(payload, dict):
            return
        try:
            response_generation = int(payload.get('project_generation'))
            current_generation = int(
                self._execution_context.get('project_generation') or 0
            )
        except (AttributeError, TypeError, ValueError):
            return
        if response_generation != current_generation:
            return
        results = payload.get('results') if isinstance(payload, dict) else None
        if isinstance(results, list):
            self._apply_motor_results(
                [result for result in results if isinstance(result, dict)],
                _finite_float(payload.get('stamp')) or time.time(),
            )
            return
        if isinstance(payload, dict):
            self._apply_motor_results(
                [payload], _finite_float(payload.get('stamp')) or time.time()
            )

    def _motion_value_callback(self, msg: String) -> None:
        try:
            payload = json.loads(msg.data)
        except json.JSONDecodeError:
            return
        if not isinstance(payload, dict):
            return
        project_id = str(payload.get('project_id') or '')
        try:
            generation = int(payload.get('project_generation'))
            current_generation = int(
                self._execution_context.get('project_generation') or 0
            )
        except (AttributeError, TypeError, ValueError):
            return
        if project_id != self._project_id or generation != current_generation:
            return
        raw_values = payload.get('values')
        if not isinstance(raw_values, dict):
            return
        stamp = _finite_float(payload.get('stamp')) or time.time()
        updates = {}
        for motion_id, value in raw_values.items():
            key = str(motion_id or '').strip()
            number = _finite_float(value)
            if key and number is not None:
                updates[key] = number
        if not updates:
            return
        with self._lock:
            context = (project_id, generation)
            if getattr(self, '_source_motion_value_context', ('', 0)) != context:
                self._source_motion_values = {}
                self._source_motion_value_stamps = {}
                self._source_motion_value_context = context
            for motion_id, value in updates.items():
                if stamp < self._source_motion_value_stamps.get(motion_id, 0.0):
                    continue
                self._source_motion_values[motion_id] = value
                self._source_motion_value_stamps[motion_id] = stamp
            self._service_playback_follow_locked(time.monotonic())

    def _ensure_approved_command_state_locked(self) -> None:
        if not hasattr(self, '_approved_motion_values'):
            self._approved_motion_values = [
                {} for _ in range(MIDI_CHANNEL_COUNT)
            ]
        if not hasattr(self, '_approved_motor_targets'):
            self._approved_motor_targets = [
                {} for _ in range(MIDI_CHANNEL_COUNT)
            ]
        if not hasattr(self, '_approved_command_stamp'):
            self._approved_command_stamp = [0.0] * MIDI_CHANNEL_COUNT

    def _ensure_current_motion_state_locked(self) -> None:
        if not hasattr(self, '_current_motion_values'):
            self._current_motion_values = {}

    def _logical_motion_value_for_group_locked(
        self, group: List[Dict[str, Any]]
    ) -> float:
        value, _source = self._pickup_reference_for_group_locked(group)
        return value

    def _pickup_reference_for_group_locked(
        self, group: List[Dict[str, Any]]
    ) -> tuple[float, str]:
        """Prefer the latest accepted logical value, then invert live feedback."""
        if not group:
            raise ValueError('연결할 Motion ID가 없습니다')
        self._ensure_current_motion_state_locked()
        motion_ids = [str(item['motion_id']) for item in group]
        context = (
            str(getattr(self, '_project_id', '') or ''),
            int(getattr(self, '_execution_context', {}).get('project_generation') or 0),
        )
        source_values = (
            getattr(self, '_source_motion_values', {})
            if getattr(self, '_source_motion_value_context', ('', 0)) == context
            else {}
        )
        candidates = (
            ('source_topic', source_values),
            ('midi_approved', self._current_motion_values),
        )
        for source, values_by_id in candidates:
            values = [
                _finite_float(values_by_id.get(motion_id))
                for motion_id in motion_ids
            ]
            if any(value is None for value in values):
                continue
            logical_values = [float(value) for value in values if value is not None]
            if (
                max(logical_values) - min(logical_values)
                > LINKED_MOTION_VALUE_TOLERANCE_DEG
            ):
                continue
            candidate = sum(logical_values) / len(logical_values)
            if self._logical_value_matches_feedback(group, candidate):
                return candidate, source

        feedback_values = []
        for item in group:
            if not self._motor_feedback_ready_for_pickup(item.get('motor')):
                raise ValueError(
                    f"{item['motion_id']}: Pickup에 사용할 최신 모터 피드백이 없습니다"
                )
            position = self._position_from_motor(item.get('motor'))
            if position is None:
                raise ValueError(
                    f"{item['motion_id']}: Pickup 기준을 계산할 실제 모터 위치가 없습니다"
                )
            feedback_values.append(
                motion_value_from_motor(position, item['row'])
            )
        tolerance = self._pickup_feedback_consistency_tolerance()
        if max(feedback_values) - min(feedback_values) > tolerance:
            raise ValueError(
                '연동 축의 실제 위치를 같은 모션값으로 환산할 수 없습니다. '
                '초기 위치 정렬 후 다시 SELECT 하세요'
            )
        return sum(feedback_values) / len(feedback_values), 'motor_feedback'

    def _logical_value_matches_feedback(
        self, group: List[Dict[str, Any]], motion_value: float
    ) -> bool:
        tolerance = self._pickup_feedback_consistency_tolerance()
        for item in group:
            if not self._motor_feedback_ready_for_pickup(item.get('motor')):
                return False
            position = self._position_from_motor(item.get('motor'))
            if position is None:
                return False
            try:
                target = require_motion_value_within_limits(
                    item['motion_id'], motion_value, item['row'], item['motor']
                )
            except ValueError:
                return False
            if abs(position - target) > tolerance:
                return False
        return True

    def _motor_feedback_ready_for_pickup(self, motor: Any) -> bool:
        if not isinstance(motor, dict):
            return False
        connection_state = str(motor.get('connection_state') or '').strip().lower()
        if connection_state and connection_state != 'online':
            return False
        runtime_state = str(motor.get('state') or '').strip().lower()
        if runtime_state and runtime_state != 'detected':
            return False
        age = _finite_float(motor.get('age_sec'))
        if age is not None and age > max(
            float(getattr(self, 'stale_timeout_sec', 0.5)), 0.1
        ):
            return False
        if bool(motor.get('fault')):
            return False
        return self._position_from_motor(motor) is not None

    def _pickup_tolerance(self) -> float:
        return max(
            0.0,
            float(getattr(self, 'pickup_tolerance_deg', PICKUP_TOLERANCE_DEG)),
        )

    def _pickup_feedback_consistency_tolerance(self) -> float:
        return max(
            0.0,
            float(
                getattr(
                    self,
                    'pickup_feedback_consistency_deg',
                    PICKUP_FEEDBACK_CONSISTENCY_DEG,
                )
            ),
        )

    @staticmethod
    def _pickup_reached(
        previous: float | None,
        current: float,
        reference: float,
        tolerance: float,
    ) -> bool:
        if abs(current - reference) <= tolerance:
            return True
        if previous is None:
            return False
        return (previous <= reference <= current) or (current <= reference <= previous)

    def _set_group_motion_value_locked(
        self, group: List[Dict[str, Any]], motion_value: float
    ) -> None:
        self._ensure_current_motion_state_locked()
        for item in group:
            self._current_motion_values[str(item['motion_id'])] = float(motion_value)

    def _apply_motor_results(
        self,
        results: List[Dict[str, Any]],
        stamp: float,
    ) -> None:
        by_channel: Dict[int, List[Dict[str, Any]]] = {}
        for result in results:
            try:
                channel = int(result.get('channel'))
            except (TypeError, ValueError):
                continue
            if 0 <= channel < MIDI_CHANNEL_COUNT:
                by_channel.setdefault(channel, []).append(result)
        with self._lock:
            self._ensure_approved_command_state_locked()
            mappings = self._banks.snapshot()['active_bank']['mappings']
            for channel, channel_results in by_channel.items():
                if stamp < self._approved_command_stamp[channel]:
                    continue
                self._approved_command_stamp[channel] = stamp
                if all(item.get('operation') == 'hold' for item in channel_results):
                    self._approved_motion_values[channel] = {}
                    self._approved_motor_targets[channel] = {}
                    if self._fader_parking[channel]:
                        self._motor_command_state[channel] = 'parking_fader'
                        hold_message = str(channel_results[-1].get('message') or '')
                        self._motor_command_message[channel] = (
                            f'모터 현재 위치 유지 · 페이더 0 복귀 중 · {hold_message}'
                            if hold_message else '모터 현재 위치 유지 · 페이더 0 복귀 중'
                        )
                    continue

                success = all(item.get('success') is True for item in channel_results)
                approved_motion: Dict[str, float] = {}
                approved_targets: Dict[int, float] = {}
                if success:
                    for item in channel_results:
                        motion_id = str(item.get('motion_id') or '').strip()
                        motion_value = _finite_float(item.get('motion_deg'))
                        target_value = _finite_float(item.get('target_deg'))
                        try:
                            axis = int(item.get('axis'))
                        except (TypeError, ValueError):
                            axis = -1
                        if not motion_id or motion_value is None or target_value is None or axis < 0:
                            success = False
                            break
                        approved_motion[motion_id] = motion_value
                        approved_targets[axis] = target_value
                expected_ids = set(mapping_motion_ids(mappings[channel]))
                if success and set(approved_motion) != expected_ids:
                    success = False
                if success and any(
                    str(item.get('mapping_file_id') or '')
                    != str(self._axis_registry.file_id or '')
                    for item in channel_results
                ):
                    success = False
                if success:
                    self._approved_motion_values[channel] = approved_motion
                    self._approved_motor_targets[channel] = approved_targets
                    self._ensure_current_motion_state_locked()
                    self._current_motion_values.update(approved_motion)
                    self._publish_current_motion_values_locked()
                else:
                    self._approved_motion_values[channel] = {}
                    self._approved_motor_targets[channel] = {}
                self._motor_command_state[channel] = 'commanding' if success else 'rejected'
                self._motor_command_message[channel] = str(
                    channel_results[-1].get('message') or ''
                )

    def _publish_current_motion_values_locked(self) -> None:
        publisher = getattr(self, '_motion_value_publisher', None)
        if publisher is None:
            return
        self._ensure_current_motion_state_locked()
        values = {
            str(motion_id): float(value)
            for motion_id, value in self._current_motion_values.items()
            if str(motion_id or '').strip() and _finite_float(value) is not None
        }
        if not values:
            return
        payload = {
            'source': 'midi',
            'project_id': str(self._project_id or ''),
            'project_generation': int(
                self._execution_context.get('project_generation') or 0
            ),
            'stamp': time.time(),
            'values': values,
        }
        publisher.publish(String(data=json.dumps(payload, ensure_ascii=False)))

    def _motion_state_callback(self, msg: String) -> None:
        try:
            payload = json.loads(msg.data)
        except json.JSONDecodeError:
            return
        if not isinstance(payload, dict):
            return
        if str(payload.get('project_id') or '') != self._project_id:
            return
        try:
            state_generation = int(payload.get('project_generation'))
            current_generation = int(
                self._execution_context.get('project_generation') or 0
            )
        except (AttributeError, TypeError, ValueError):
            return
        if state_generation != current_generation:
            return
        with self._lock:
            if self._project_id and self._execution_context_ready:
                self._latest_motion_state = payload

    def _motion_run_status_callback(self, msg: String) -> None:
        try:
            payload = json.loads(msg.data)
        except json.JSONDecodeError:
            return
        if not isinstance(payload, dict):
            return
        if str(payload.get('project_id') or '') != self._project_id:
            return
        context = payload.get('execution_context')
        if not isinstance(context, dict):
            return
        try:
            if int(context.get('project_generation')) != int(
                self._execution_context.get('project_generation')
            ):
                return
        except (AttributeError, TypeError, ValueError):
            return
        mapping_file_id = str(payload.get('mapping_file_id') or '').strip()
        with self._lock:
            self._ensure_playback_follow_state_locked()
            self._motion_run_state = str(payload.get('state') or 'idle')
            self._motion_run_request_source = str(
                payload.get('request_source') or ''
            )
            self._update_playback_phase_locked()
            self._run_mapping_file_id = mapping_file_id
            self._preferred_mapping_file_id = (
                self._run_mapping_file_id or self._selected_mapping_file_id
            )

    def _motion_studio_status_callback(self, msg: String) -> None:
        try:
            payload = json.loads(msg.data)
        except json.JSONDecodeError:
            return
        if not isinstance(payload, dict):
            return
        context = payload.get('execution_context')
        if not isinstance(context, dict):
            return
        try:
            if (
                str(context.get('project_id') or '') != self._project_id
                or int(context.get('project_generation')) != int(
                    self._execution_context.get('project_generation')
                )
            ):
                return
        except (AttributeError, TypeError, ValueError):
            return
        with self._lock:
            self._ensure_playback_follow_state_locked()
            self._motion_studio_state = str(payload.get('state') or 'idle')
            self._update_playback_phase_locked()

    def _motion_mapping_response_callback(self, msg: String) -> None:
        try:
            payload = json.loads(msg.data)
        except json.JSONDecodeError:
            return
        if not isinstance(payload, dict) or payload.get('success') is False:
            return
        try:
            response_generation = int(payload.get('project_generation'))
            current_generation = int(
                self._execution_context.get('project_generation') or 0
            )
        except (AttributeError, TypeError, ValueError):
            return
        if response_generation != current_generation:
            return
        file_info = payload.get('file')
        if not isinstance(file_info, dict):
            return
        mapping_file_id = str(
            file_info.get('id') or file_info.get('filename') or ''
        ).strip()
        if not mapping_file_id:
            return
        with self._lock:
            self._selected_mapping_file_id = mapping_file_id
            self._preferred_mapping_file_id = (
                self._run_mapping_file_id or self._selected_mapping_file_id
            )
            self._bank_config_file = self._mapping_file_path_or_none(
                self._selected_mapping_file_id
            )
            self._bank_file_loaded = False
            self._bank_file_dirty = True

    def _mapping_file_path_or_none(self, file_id: Any) -> Path | None:
        name = str(file_id or '').strip()
        if (
            not name
            or name != Path(name).name
            or '/' in name
            or '\\' in name
            or not name.lower().endswith(('.yaml', '.yml'))
        ):
            return None
        path = self._mappings_dir / name
        return path if path.is_file() else None

    def _requested_mapping_file(self, payload: Dict[str, Any]) -> Path:
        requested_id = str(payload.get('mapping_file_id') or '').strip()
        if requested_id:
            path = self._mapping_file_path_or_none(requested_id)
        else:
            path = self._bank_config_file
        if path is None:
            raise ValueError('현재 선택된 모션축 매칭 파일이 없습니다')
        return path

    def _select_project_mapping_dir(self, payload: Dict[str, Any]) -> None:
        project_id = str(payload.get('project_id') or '').strip()
        if (
            not project_id
            or project_id != Path(project_id).name
            or project_id.startswith('.')
            or '/' in project_id
            or '\\' in project_id
        ):
            raise ValueError('유효한 통합 프로젝트 ID가 필요합니다')
        root = self._motion_projects_dir.resolve()
        project_dir = (root / project_id).resolve()
        if project_dir.parent != root or not (project_dir / 'project.json').is_file():
            raise ValueError(f'통합 프로젝트를 찾을 수 없습니다: {project_id}')
        self._project_id = project_id
        self._mappings_dir = project_dir / 'motion_axis_matching'

    @staticmethod
    def _motor_for_axis(
        state: Dict[str, Any], motor_axis: int
    ) -> Dict[str, Any] | None:
        motors = state.get('motors') if isinstance(state, dict) else None
        if not isinstance(motors, list):
            return None
        for motor in motors:
            if not isinstance(motor, dict):
                continue
            try:
                axis = int(motor.get('controller_index'))
            except (TypeError, ValueError):
                continue
            if axis != motor_axis:
                continue
            return motor
        return None

    @staticmethod
    def _position_from_motor(motor: Dict[str, Any] | None) -> float | None:
        if not isinstance(motor, dict):
            return None
        for key in ('position_deg', 'actual_position_deg', 'output_position_deg',
                    'present_position_deg', 'position_actual', 'position'):
            try:
                value = float(motor.get(key))
            except (TypeError, ValueError):
                continue
            if math.isfinite(value):
                return value
        return None

    @classmethod
    def _position_for_axis(cls, state: Dict[str, Any], motor_axis: int) -> float | None:
        return cls._position_from_motor(cls._motor_for_axis(state, motor_axis))

    def _refresh_axis_registry_locked(self, now: float) -> None:
        if now - self._last_axis_registry_refresh < 1.0:
            return
        self._axis_registry.refresh(
            self._preferred_mapping_file_id,
            getattr(self, '_latest_motion_state', {}),
        )
        self._last_axis_registry_refresh = now

    @staticmethod
    def _filtered_output_14bit(filtered_value: float, mapping: Dict[str, Any]) -> float:
        normalized = max(0.0, min(1.0, float(filtered_value) / MIDI_VALUE_MAX))
        if mapping['reversed']:
            normalized = 1.0 - normalized
        output_percent = mapping['min_percent'] + (
            (mapping['max_percent'] - mapping['min_percent']) * normalized
        )
        limited_percent = max(0.0, min(100.0, output_percent))
        return round(MIDI_VALUE_MAX * limited_percent / 100.0, 6)

    def _snapshot(self) -> Dict[str, Any]:
        now_monotonic = time.monotonic()
        with self._lock:
            self._ensure_fader_parking_state_locked()
            self._service_playback_follow_locked(now_monotonic)
            self._refresh_axis_registry_locked(now_monotonic)
            if self._execution_context_ready and self._bank_config_file is not None:
                expected_sha = str(self._execution_context.get('mapping_sha256') or '')
                try:
                    actual_sha = hashlib.sha256(
                        self._bank_config_file.read_bytes()
                    ).hexdigest()
                except OSError:
                    actual_sha = ''
                if not expected_sha or actual_sha != expected_sha:
                    self._execution_context_ready = False
                    self._reset_bank_change_state_locked()
            last_monotonic = self._last_received_monotonic
            last_wall = self._last_received_wall
            physical_input_monotonic = self._last_physical_input_monotonic
            physical_input_wall = self._last_physical_input_wall
            device_connected = self._device_connected
            device_connection_message = self._device_connection_message
            device_last_connected_at = self._device_last_connected_at
            device_last_disconnected_at = self._device_last_disconnected_at
            device_last_power_reconnected_at = (
                self._device_last_power_reconnected_at
            )
            device_connection_count = self._device_connection_count
            device_power_reconnect_count = self._device_power_reconnect_count
            raw_values = list(self._raw_channels)
            observed_raw_values = list(getattr(
                self, '_observed_raw_channels', self._raw_channels
            ))
            filtered_values = list(self._channels)
            touch = list(self._touch)
            physical_touch = list(self._physical_touch)
            fader_moving = list(self._fader_moving)
            bridge_fader_syncing = list(self._bridge_fader_syncing)
            self._ensure_fader_input_generation_locked()
            fader_input_generation = list(self._fader_input_generation)
            dial = list(self._dial)
            buttons = [
                [self._btn0[index], self._btn1[index], self._btn2[index], self._btn3[index]]
                for index in range(MIDI_CHANNEL_COUNT)
            ]
            confirmed = list(self._confirmed)
            control_enabled = list(self._control_enabled)
            self._ensure_playback_follow_state_locked()
            playback_follow_enabled = list(self._playback_follow_enabled)
            select_enabled = [
                bool(control_enabled[channel] or playback_follow_enabled[channel])
                for channel in range(MIDI_CHANNEL_COUNT)
            ]
            playback_phase = self._playback_phase
            motion_value_mode = list(self._motor_angle_mode)
            motion_state = dict(self._latest_motion_state)
            source_context = (
                str(self._project_id or ''),
                int(self._execution_context.get('project_generation') or 0),
            )
            source_motion_values = (
                dict(getattr(self, '_source_motion_values', {}))
                if getattr(self, '_source_motion_value_context', ('', 0))
                == source_context
                else {}
            )
            bank_state = self._banks.snapshot()
            bank_export = self._banks.export_state()
            mappings = bank_state['active_bank']['mappings']
            motion_id_groups = [mapping_motion_ids(mapping) for mapping in mappings]
            matched_axis_groups = [
                [self._axis_registry.motor_axis(motion_id) for motion_id in motion_ids]
                for motion_ids in motion_id_groups
            ]
            matched_row_groups = [
                [self._axis_registry.mapping(motion_id) for motion_id in motion_ids]
                for motion_ids in motion_id_groups
            ]
            axis_groups_matched = []
            group_valid = []
            group_messages = []
            group_safe_ranges: List[tuple[float, float] | None] = []
            for mapping, motion_ids, axes, rows in zip(
                mappings, motion_id_groups, matched_axis_groups, matched_row_groups
            ):
                matched = bool(axes) and all(axis is not None for axis in axes)
                matched = matched and all(row is not None for row in rows)
                valid = matched
                message = ''
                if not matched:
                    missing = [
                        motion_id for motion_id, axis, row in zip(motion_ids, axes, rows)
                        if axis is None or row is None
                    ]
                    message = '모션축 매칭 없음: ' + ', '.join(missing)
                if matched:
                    try:
                        group = self._mapping_group_locked(mapping)
                        safe_range = safe_motion_range_for_group(group)
                    except ValueError as exc:
                        valid = False
                        message = str(exc)
                        safe_range = None
                else:
                    safe_range = None
                axis_groups_matched.append(matched)
                group_valid.append(valid)
                group_messages.append(message)
                group_safe_ranges.append(safe_range)
            matched_axes = [axes[0] if axes else None for axes in matched_axis_groups]
            matched_rows = [rows[0] if rows else None for rows in matched_row_groups]
            # An activation error is a live condition. If the motor later moves
            # into this line's representable range (or the mapping is repaired),
            # clear the stale red/error state without requiring another SELECT.
            for channel, mapping in enumerate(mappings):
                if self._motor_command_state[channel] != 'activation_rejected':
                    continue
                if (
                    mapping.get('enabled') is False
                    or not group_valid[channel]
                ):
                    # A missing/disabled mapping is the current condition.
                    # Do not keep a historical SELECT rejection that makes
                    # duplicate Motion IDs display different states.
                    self._motor_command_state[channel] = 'inactive'
                    self._motor_command_message[channel] = ''
                    continue
                try:
                    group = self._mapping_group_locked(mapping)
                    motion_value = self._logical_motion_value_for_group_locked(group)
                    raw_fader_for_motion(
                        motion_value,
                        group[0]['row'],
                        mapping,
                        safe_motion_range_for_group(group),
                    )
                except ValueError:
                    continue
                self._motor_command_state[channel] = 'inactive'
                self._motor_command_message[channel] = ''
            for channel, valid in enumerate(group_valid):
                if not valid:
                    self._control_enabled[channel] = False
                    self._motor_angle_mode[channel] = False
                    control_enabled[channel] = False
                    motion_value_mode[channel] = False
            for channel, mapping in enumerate(mappings):
                if control_enabled[channel]:
                    self._final_output_values[channel] = self._filtered_output_14bit(
                        filtered_values[channel], mapping
                    )
            final_output_values = list(self._final_output_values)
            motor_command_states = list(self._motor_command_state)
            motor_command_messages = list(self._motor_command_message)
            awaiting_fader_sync = list(self._awaiting_fader_sync)
            fader_sync_targets = list(self._fader_sync_targets)
            fader_parking = list(self._fader_parking)
            select_lock_reason = self._select_lock_reason_locked()
            self._ensure_approved_command_state_locked()
            self._ensure_current_motion_state_locked()
            self._ensure_pickup_state_locked()
            current_motion_values = dict(self._current_motion_values)
            pickup_pending = list(self._pickup_pending)
            pickup_reference_motion = list(self._pickup_reference_motion)
            pickup_reference_source = list(self._pickup_reference_source)
            approved_motion_values = [
                dict(values) for values in self._approved_motion_values
            ]
            approved_motor_targets = [
                dict(values) for values in self._approved_motor_targets
            ]
            mapping_file_id = self._axis_registry.file_id
        bridge_age_sec = (
            None if last_monotonic is None else max(0.0, now_monotonic - last_monotonic)
        )
        age_sec = (
            None
            if physical_input_monotonic is None
            else max(0.0, now_monotonic - physical_input_monotonic)
        )
        connected = (
            device_connected
            and age_sec is not None
            and age_sec <= self.stale_timeout_sec
        )
        channels = []
        for channel, mapping in enumerate(mappings):
            raw_value = raw_values[channel]
            observed_raw_value = observed_raw_values[channel]
            display_raw_value = (
                int(fader_sync_targets[channel])
                if (
                    awaiting_fader_sync[channel]
                    and fader_sync_targets[channel] is not None
                )
                else observed_raw_value
            )
            filtered_value = filtered_values[channel]
            final_output_value = final_output_values[channel]
            motor_axis = matched_axes[channel]
            motor_angle = (
                self._position_for_axis(motion_state, motor_axis)
                if motor_axis is not None else None
            )
            row = matched_rows[channel]
            safe_range = group_safe_ranges[channel]
            motion_ids = motion_id_groups[channel]
            group_axes = matched_axis_groups[channel]
            try:
                requested_motion_value = (
                    motion_value_from_output(final_output_value, row, safe_range)
                    if row is not None and safe_range is not None else None
                )
            except ValueError:
                requested_motion_value = None
            # SELECT may be enabled before any logical source topic has been
            # published. During Pickup the motor-feedback-derived reference is
            # the authoritative current value; showing the old raw/final output
            # here produced values such as -12° while the actual axis was 0°.
            pickup_display_value = (
                _finite_float(pickup_reference_motion[channel])
                if control_enabled[channel] and pickup_pending[channel]
                else None
            )
            displayed_motion_value, motion_display_text, motion_display_status = (
                motion_value_display(
                    motion_ids,
                    source_motion_values,
                    control_enabled=control_enabled[channel],
                    estimated_value=(
                        pickup_display_value
                        if pickup_display_value is not None
                        else requested_motion_value
                    ),
                )
            )
            approved_values = approved_motion_values[channel]
            approved_targets = approved_motor_targets[channel]
            approved_complete = bool(motion_ids) and all(
                motion_id in approved_values for motion_id in motion_ids
            )
            logical_values = {
                motion_id: value
                for motion_id in motion_ids
                if (value := _finite_float(current_motion_values.get(motion_id)))
                is not None
            }
            motion_value = (
                sum(logical_values.values()) / len(logical_values)
                if motion_ids and len(logical_values) == len(motion_ids)
                else None
            )
            motor_target = (
                approved_targets.get(int(motor_axis))
                if motor_axis is not None else None
            )
            channels.append({
                **mapping,
                'channel_number': channel + 1,
                'raw_value': raw_value,
                'observed_raw_value': observed_raw_value,
                # The LCD should show the SELECT pickup/park target as soon as
                # it is requested. Keep observed_raw_value unchanged so
                # physical-arrival and retry decisions still use device state.
                'display_raw_value': display_raw_value,
                'filtered_value': round(filtered_value, 6),
                'final_output_value': round(final_output_value, 6),
                'raw_normalized': round(raw_value / MIDI_VALUE_MAX, 6),
                'filtered_normalized': round(filtered_value / MIDI_VALUE_MAX, 6),
                'normalized': round(final_output_value / MIDI_VALUE_MAX, 6),
                'value_confirmed': confirmed[channel],
                'touch': touch[channel],
                'physical_touch': physical_touch[channel],
                'fader_moving': fader_moving[channel],
                'fader_input_generation': fader_input_generation[channel],
                'input_valid': touch[channel],
                'dial': dial[channel],
                'buttons': buttons[channel],
                'control_enabled': control_enabled[channel],
                'select_enabled': select_enabled[channel],
                'playback_follow_enabled': playback_follow_enabled[channel],
                'pickup_pending': pickup_pending[channel],
                'pickup_complete': bool(
                    control_enabled[channel] and not pickup_pending[channel]
                ),
                'pickup_reference_motion_deg': pickup_reference_motion[channel],
                'pickup_reference_source': pickup_reference_source[channel],
                'motion_ids': motion_ids,
                'motion_axis_matched': axis_groups_matched[channel],
                'motion_group_valid': group_valid[channel],
                'motion_group_message': group_messages[channel],
                'matched_motor_axis': motor_axis,
                'matched_motor_axes': group_axes,
                'display_motion_value': motion_value_mode[channel],
                'motor_angle_deg': None if motor_angle is None else round(motor_angle, 6),
                'source_motion_value_deg': (
                    None
                    if motion_display_status != 'confirmed'
                    else round(float(displayed_motion_value), 6)
                ),
                'displayed_motion_value_deg': (
                    None
                    if displayed_motion_value is None
                    else round(displayed_motion_value, 6)
                ),
                'motion_value_display_text': motion_display_text,
                'motion_value_display_status': motion_display_status,
                'motion_value_deg': None if motion_value is None else round(motion_value, 6),
                'requested_motion_value_deg': (
                    None
                    if requested_motion_value is None
                    else round(requested_motion_value, 6)
                ),
                'safe_motion_lower_deg': (
                    None if safe_range is None else round(safe_range[0], 6)
                ),
                'safe_motion_upper_deg': (
                    None if safe_range is None else round(safe_range[1], 6)
                ),
                'motion_command_valid': bool(
                    control_enabled[channel]
                    and group_valid[channel]
                    and approved_complete
                    and motor_command_states[channel] not in {
                        'rejected', 'activation_rejected'
                    }
                ),
                'motion_values_deg': {
                    motion_id: round(logical_values[motion_id], 6)
                    for motion_id in motion_ids
                    if motion_id in logical_values
                },
                'motor_target_deg': None if motor_target is None else round(motor_target, 6),
                'fader_syncing': (
                    awaiting_fader_sync[channel]
                    or bridge_fader_syncing[channel]
                    or fader_parking[channel]
                ),
                'fader_parking': fader_parking[channel],
                'motor_command_state': motor_command_states[channel],
                'motor_command_message': motor_command_messages[channel],
            })
        return {
            'success': True,
            'node_state': 'ok',
            'project_id': self._project_id,
            'execution_context': {
                **self._execution_context,
                'ready': self._execution_context_ready,
            },
            'connected': connected,
            'device_connected': device_connected,
            'device_connection_message': device_connection_message,
            'device_last_connected_at': device_last_connected_at,
            'device_last_disconnected_at': device_last_disconnected_at,
            'device_last_power_reconnected_at': (
                device_last_power_reconnected_at
            ),
            'device_connection_count': device_connection_count,
            'device_power_reconnect_count': device_power_reconnect_count,
            'message': (
                'MIDI 데이터 수신 정상'
                if connected else (
                    device_connection_message or 'MIDI 데이터 수신 대기'
                )
            ),
            'input_topic': self.input_topic,
            'last_received_at': physical_input_wall,
            'age_sec': None if age_sec is None else round(age_sec, 3),
            'bridge_publish_age_sec': (
                None if bridge_age_sec is None else round(bridge_age_sec, 3)
            ),
            'value_bits': 14,
            'value_min': MIDI_VALUE_MIN,
            'value_max': MIDI_VALUE_MAX,
            'unit': '14bit',
            'motor_output_enabled': self._execution_context_ready,
            'motor_output_path': 'motion_supervisor',
            'select_locked': bool(select_lock_reason),
            'select_lock_reason': select_lock_reason,
            'playback_phase': playback_phase,
            'motion_mapping_file_id': mapping_file_id,
            'touch_gated_input': True,
            'filter_order': FILTER_ORDER,
            'filter_level_min': 0,
            'filter_level_max': FILTER_LEVEL_MAX,
            'filter_max_time_constant_sec': FILTER_MAX_TIME_CONSTANT_SEC,
            'bank_storage': 'motion_mapping_yaml',
            'bank_persistent': self._bank_file_loaded and not self._bank_file_dirty,
            'bank_config_file': (
                str(self._bank_config_file) if self._bank_config_file is not None else ''
            ),
            'max_banks': bank_state['max_banks'],
            'active_bank_id': bank_state['active_bank_id'],
            'active_bank': bank_state['active_bank'],
            'banks': bank_state['banks'],
            'bank_state': bank_export,
            'channels': channels,
        }

    def _publish_json(self, publisher: Any, payload: Dict[str, Any]) -> None:
        msg = String()
        msg.data = json.dumps(payload, ensure_ascii=False)
        publisher.publish(msg)

    def _publish_state(self) -> None:
        snapshot = self._snapshot()
        self._publish_json(self._state_publisher, snapshot)
        for channel in snapshot['channels']:
            display_motion_value = bool(channel['display_motion_value'])
            bottom = (
                str(channel['motion_value_display_text'])
                if display_motion_value
                else str(int(channel.get(
                    'display_raw_value',
                    channel.get('observed_raw_value', channel['raw_value']),
                )))
            )
            feedback = (
                int(bool(channel.get(
                    'select_enabled', channel['control_enabled']
                ))),
                int(display_motion_value),
                int(channel['filter_level']),
                str(channel['motion_id']),
                bottom,
            )
            index = int(channel['channel'])
            with self._lock:
                self._ensure_fader_input_generation_locked()
                fader_position = self._pending_fader_positions[index]
                fader_input_generation = int(
                    self._pending_fader_input_generations[index]
                )
                self._queue_fader_position_locked(index, None)
            # A consumed one-shot fader target must not make the following
            # cycle look like a UI-state change. Otherwise every retry emits
            # a second LED/LCD-only packet immediately after the fader packet.
            if self._last_feedback[index] == feedback and fader_position is None:
                continue
            self._last_feedback[index] = feedback
            hardware_feedback = (
                *feedback,
                -1 if fader_position is None else fader_position,
                fader_input_generation,
            )
            msg = String()
            msg.data = '\t'.join((str(index), *(str(value) for value in hardware_feedback)))
            self._feedback_publisher.publish(msg)

    def _reset_runtime_controls_locked(self) -> None:
        self._control_enabled = [False] * MIDI_CHANNEL_COUNT
        self._ensure_playback_follow_state_locked()
        self._playback_follow_enabled = [False] * MIDI_CHANNEL_COUNT
        self._playback_follow_targets = [None] * MIDI_CHANNEL_COUNT
        self._playback_follow_resume_not_before = [
            0.0
        ] * MIDI_CHANNEL_COUNT
        self._final_output_values = [0.0] * MIDI_CHANNEL_COUNT
        self._observed_raw_channels = [0] * MIDI_CHANNEL_COUNT
        self._pending_fader_positions = [0] * MIDI_CHANNEL_COUNT
        self._ensure_fader_input_generation_locked()
        self._pending_fader_input_generations = list(
            self._fader_input_generation
        )
        self._fader_sync_targets = [None] * MIDI_CHANNEL_COUNT
        self._awaiting_fader_sync = [False] * MIDI_CHANNEL_COUNT
        self._fader_sync_not_before = [0.0] * MIDI_CHANNEL_COUNT
        self._fader_parking = [False] * MIDI_CHANNEL_COUNT
        self._fader_park_last_command_at = [0.0] * MIDI_CHANNEL_COUNT
        self._fader_park_started_at = [0.0] * MIDI_CHANNEL_COUNT
        self._fader_zero_required = [True] * MIDI_CHANNEL_COUNT
        self._last_select_toggle_at = [0.0] * MIDI_CHANNEL_COUNT
        self._last_motor_command_at = [0.0] * MIDI_CHANNEL_COUNT
        self._last_motor_target = [None] * MIDI_CHANNEL_COUNT
        self._last_group_motor_targets = [
            {} for _ in range(MIDI_CHANNEL_COUNT)
        ]
        self._approved_motion_values = [
            {} for _ in range(MIDI_CHANNEL_COUNT)
        ]
        self._approved_motor_targets = [
            {} for _ in range(MIDI_CHANNEL_COUNT)
        ]
        self._approved_command_stamp = [0.0] * MIDI_CHANNEL_COUNT
        self._pending_motor_requests = {}
        self._motor_follow_active = [False] * MIDI_CHANNEL_COUNT
        self._pickup_pending = [False] * MIDI_CHANNEL_COUNT
        self._pickup_reference_motion = [None] * MIDI_CHANNEL_COUNT
        self._pickup_previous_motion = [None] * MIDI_CHANNEL_COUNT
        self._pickup_reference_source = [''] * MIDI_CHANNEL_COUNT
        self._motor_command_state = ['inactive'] * MIDI_CHANNEL_COUNT
        self._motor_command_message = [''] * MIDI_CHANNEL_COUNT
        self._motor_angle_mode = [False] * MIDI_CHANNEL_COUNT
        self._last_feedback = [None] * MIDI_CHANNEL_COUNT

    def _reset_bank_change_state_locked(self) -> None:
        """Release SELECT and request zero without blocking later selection."""
        self._reset_live_values_locked()
        now = time.monotonic()
        for channel in range(MIDI_CHANNEL_COUNT):
            self._queue_normal_fader_zero_locked(channel, now)
        self._previous_btn3 = list(self._btn3)

    @staticmethod
    def _active_bank_control_signature(state: Dict[str, Any]) -> tuple:
        active = state.get('active_bank') if isinstance(state, dict) else None
        if not isinstance(active, dict):
            return ()
        mappings = active.get('mappings')
        if not isinstance(mappings, list):
            mappings = []
        return (
            str(state.get('active_bank_id') or ''),
            tuple(
                (
                    int(item.get('channel', index)),
                    bool(item.get('enabled', True)),
                    str(item.get('motion_id') or ''),
                    tuple(str(value or '') for value in item.get('linked_motion_ids') or []),
                    float(item.get('min_percent', 0.0)),
                    float(item.get('max_percent', 100.0)),
                    bool(item.get('reversed', False)),
                )
                for index, item in enumerate(mappings)
                if isinstance(item, dict)
            ),
        )

    def _finish_bank_settings_change_locked(self, previous_state: Dict[str, Any]) -> bool:
        """Reset ownership unless the active bank changed only filter levels."""
        reset_select = (
            self._active_bank_control_signature(previous_state)
            != self._active_bank_control_signature(self._banks.snapshot())
        )
        if reset_select:
            self._reset_bank_change_state_locked()
        else:
            self._reset_filter_state_locked()
        return reset_select

    def _reset_filter_state_locked(self) -> None:
        self._filter_stage1 = [float(value) for value in self._raw_channels]
        self._filter_stage2 = [float(value) for value in self._raw_channels]
        self._channels = [float(value) for value in self._raw_channels]
        self._filter_last_at = [None] * MIDI_CHANNEL_COUNT

    def _reset_live_values_locked(self) -> None:
        self._raw_channels = [0] * MIDI_CHANNEL_COUNT
        self._channels = [0.0] * MIDI_CHANNEL_COUNT
        self._filter_stage1 = [0.0] * MIDI_CHANNEL_COUNT
        self._filter_stage2 = [0.0] * MIDI_CHANNEL_COUNT
        self._filter_last_at = [None] * MIDI_CHANNEL_COUNT
        self._confirmed = [False] * MIDI_CHANNEL_COUNT
        self._touch = [False] * MIDI_CHANNEL_COUNT
        self._physical_touch = [False] * MIDI_CHANNEL_COUNT
        self._fader_moving = [False] * MIDI_CHANNEL_COUNT
        self._bridge_fader_syncing = [False] * MIDI_CHANNEL_COUNT
        self._last_physical_input_monotonic = None
        self._last_physical_input_wall = None
        self._previous_dial = list(self._dial)
        self._reset_runtime_controls_locked()

    def _resync_controlled_faders_locked(self) -> Dict[str, Any]:
        """Re-pickup every SELECT-owned fader from logical Motion ID values."""
        now = time.monotonic()
        self._last_axis_registry_refresh = 0.0
        self._refresh_axis_registry_locked(now)
        mappings = self._banks.snapshot()['active_bank']['mappings']
        synced = []
        errors = []
        for channel, mapping in enumerate(mappings):
            if not self._control_enabled[channel]:
                continue
            motion_ids = mapping_motion_ids(mapping)
            try:
                group = self._mapping_group_locked(mapping)
                motion_value = self._logical_motion_value_for_group_locked(group)
                fader_target = raw_fader_for_motion(
                    motion_value,
                    group[0]['row'],
                    mapping,
                    safe_motion_range_for_group(group),
                )
            except ValueError as exc:
                self._deactivate_control_channel_locked(channel)
                self._motor_command_state[channel] = 'activation_rejected'
                self._motor_command_message[channel] = f'재동기화 실패: {exc}'
                errors.append({
                    'channel': channel + 1,
                    'motion_ids': motion_ids,
                    'message': str(exc),
                })
                continue
            self._set_group_motion_value_locked(group, motion_value)
            self._clear_pending_channel_locked(channel)
            self._raw_channels[channel] = fader_target
            self._channels[channel] = float(fader_target)
            self._filter_stage1[channel] = float(fader_target)
            self._filter_stage2[channel] = float(fader_target)
            self._queue_fader_position_locked(channel, fader_target)
            self._fader_sync_targets[channel] = fader_target
            self._awaiting_fader_sync[channel] = True
            self._fader_sync_not_before[channel] = now + FADER_SYNC_MIN_DURATION_SEC
            self._motor_command_state[channel] = 'syncing_fader'
            self._motor_command_message[channel] = '현재 모션값으로 페이더 재동기화 중'
            logical_targets = {
                int(item['axis']): require_motion_value_within_limits(
                    item['motion_id'],
                    motion_value,
                    item['row'],
                    item['motor'],
                )
                for item in group
            }
            self._last_group_motor_targets[channel] = logical_targets
            self._last_motor_target[channel] = logical_targets[int(group[0]['axis'])]
            self._motor_follow_active[channel] = False
            synced.append({
                'channel': channel + 1,
                'motion_ids': motion_ids,
                'raw_target': fader_target,
            })
        return {'synced': synced, 'errors': errors}

    def _prepare_studio_recording_locked(self) -> Dict[str, Any]:
        """Disable SELECT and park every motorized fader at physical zero."""
        now = time.monotonic()
        self._last_axis_registry_refresh = 0.0
        self._refresh_axis_registry_locked(now)
        mappings = self._banks.snapshot()['active_bank']['mappings']
        targets = []
        errors = []
        unavailable_channels = []
        validation_results: Dict[tuple[str, ...], str | None] = {}
        for channel, mapping in enumerate(mappings):
            if mapping.get('enabled') is False:
                continue
            motion_ids = mapping_motion_ids(mapping)
            mapped = all(
                self._axis_registry.mapping(motion_id) is not None
                and self._axis_registry.motor_axis(motion_id) is not None
                for motion_id in motion_ids
            )
            if not mapped:
                continue
            signature = tuple(motion_ids)
            if signature not in validation_results:
                try:
                    self._mapping_group_locked(mapping)
                except LinkedMotionRangeMismatch as exc:
                    validation_results[signature] = str(exc)
                except ValueError as exc:
                    errors.append(str(exc))
                    validation_results[signature] = None
                else:
                    validation_results[signature] = None
            unavailable_message = validation_results[signature]
            if unavailable_message:
                unavailable_channels.append({
                    'channel': channel + 1,
                    'motion_ids': motion_ids,
                    'message': unavailable_message,
                })
        if errors:
            return {
                'targets': targets,
                'errors': errors,
                'unavailable_channels': unavailable_channels,
            }

        self._studio_select_locked = True
        self._pending_motor_requests.clear()
        for channel, mapping in enumerate(mappings):
            # Recording initialization immediately hands motor ownership to the
            # motion run manager.  Do not insert a separate MIDI hold command
            # between SELECT release and that ownership transfer.
            self._deactivate_control_channel_locked(
                channel, request_motor_hold=False
            )
            self._start_fader_parking_locked(channel, now)
            motion_ids = mapping_motion_ids(mapping)
            mapped = all(
                self._axis_registry.mapping(motion_id) is not None
                and self._axis_registry.motor_axis(motion_id) is not None
                for motion_id in motion_ids
            )
            fader_target = 0
            self._studio_zero_fader_targets[channel] = fader_target
            self._raw_channels[channel] = fader_target
            self._channels[channel] = float(fader_target)
            self._filter_stage1[channel] = float(fader_target)
            self._filter_stage2[channel] = float(fader_target)
            self._filter_last_at[channel] = now
            self._queue_fader_position_locked(channel, fader_target)
            self._fader_sync_targets[channel] = fader_target
            self._awaiting_fader_sync[channel] = True
            self._fader_sync_not_before[channel] = now + FADER_SYNC_MIN_DURATION_SEC
            self._motor_command_state[channel] = 'studio_initializing'
            self._motor_command_message[channel] = (
                '녹화 초기화 중 · SELECT 잠금 · 페이더 물리 0 이동'
            )
            self._last_feedback[channel] = None
            targets.append({
                'channel': channel + 1,
                'motion_ids': motion_ids,
                'raw_target': fader_target,
                'mapped': mapped,
            })
        self._previous_btn3 = list(self._btn3)
        return {
            'targets': targets,
            'errors': errors,
            'unavailable_channels': unavailable_channels,
        }

    def _studio_recording_zero_status_locked(self) -> Dict[str, Any]:
        """Report whether every physical MIDI fader has finished parking at zero."""
        self._ensure_fader_parking_state_locked()
        pending = []
        for channel in range(MIDI_CHANNEL_COUNT):
            raw = int(self._raw_channels[channel])
            busy = bool(
                self._physical_touch[channel]
                or self._fader_moving[channel]
                or self._bridge_fader_syncing[channel]
            )
            if self._fader_parking[channel] or raw > FADER_PARK_TOLERANCE_RAW or busy:
                pending.append({
                    'channel': channel + 1,
                    'raw': raw,
                    'parking': bool(self._fader_parking[channel]),
                    'busy': busy,
                    'physical_touch': bool(self._physical_touch[channel]),
                    'fader_moving': bool(self._fader_moving[channel]),
                    'fader_syncing': bool(self._bridge_fader_syncing[channel]),
                })
        connected = bool(getattr(self, '_device_connected', True))
        ready = connected and not pending
        return {
            'ready': ready,
            'pending_channels': pending,
            'device_connected': connected,
        }

    def _finish_studio_recording_initialization_locked(self) -> None:
        self._studio_select_locked = False
        self._previous_btn3 = list(self._btn3)
        for channel in range(MIDI_CHANNEL_COUNT):
            self._control_enabled[channel] = False
            self._clear_pending_channel_locked(channel)
            self._motor_follow_active[channel] = False
            if self._fader_parking[channel]:
                self._motor_command_state[channel] = 'parking_fader'
                self._motor_command_message[channel] = '페이더 0 복귀 확인 중'
            else:
                self._motor_command_state[channel] = 'inactive'
                self._motor_command_message[channel] = 'SELECT를 눌러 녹화할 축을 선택하세요'

    def _command_router(self) -> command_router.CommandRouter:
        """처리기 표 · 처음 쓸 때 만든다.

        노드를 띄우지 않고 (`__new__`) 콜백만 검증하는 테스트에서도 동작하도록
        `__init__`에 의존하지 않는다.
        """
        router = getattr(self, '_router', None)
        if router is None:
            router = self._build_router()
            self._router = router
        return router

    def _build_router(self) -> command_router.CommandRouter:
        """명령 → 처리기 표 · 처리기는 payload를 받아 응답 dict를 돌려준다."""
        router = command_router.CommandRouter(context_commands=self.CONTEXT_COMMANDS)
        router.register('select_project', self._cmd_select_project)
        router.register('confirm_context', self._cmd_confirm_context)
        router.register('invalidate_context', self._cmd_invalidate_context)
        router.register('save_mapping', self._cmd_save_mapping)
        router.register('update_bank', self._cmd_save_mapping)
        router.register('create_bank', self._cmd_create_bank)
        router.register('select_bank', self._cmd_select_bank)
        router.register('delete_bank', self._cmd_delete_bank)
        router.register('save_banks_to_file', self._cmd_save_banks_to_file)
        router.register('apply_banks', self._cmd_apply_banks)
        router.register('load_banks_from_file', self._cmd_apply_banks)
        router.register('reset_runtime_values', self._cmd_reset_runtime_values)
        router.register('resync_selected_faders', self._cmd_resync_selected_faders)
        router.register('studio_recording_prepare', self._cmd_studio_recording_prepare)
        router.register('studio_recording_zero_status', self._cmd_studio_recording_zero_status)
        router.register('studio_recording_ready', self._cmd_studio_recording_ready)
        router.register('connect_device', lambda payload, _c='connect_device': self._cmd_connect_device(payload, _c))
        router.register('disconnect_device', lambda payload, _c='disconnect_device': self._cmd_connect_device(payload, _c))
        router.register('status', self._cmd_status)
        return router

    def _cmd_select_project(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """선택 프로젝트의 매핑·뱅크 컨텍스트로 전환한다."""
        previous_project_id = self._project_id
        self._select_project_mapping_dir(payload)
        preferred = str(payload.get('mapping_file_id') or '').strip()
        registry = MotionAxisRegistry(self._mappings_dir)
        # A newly selected project must never resolve its axes against
        # feedback cached from the previously running project.
        registry.refresh(preferred, {})
        mapping_file = self._mapping_file_path_or_none(registry.file_id)
        stored_banks = load_midi_banks(mapping_file) if mapping_file else None
        context_id = str(payload.get('context_id') or '').strip()
        expected_mapping_sha = str(payload.get('mapping_sha256') or '').strip()
        actual_mapping_sha = (
            hashlib.sha256(mapping_file.read_bytes()).hexdigest()
            if mapping_file is not None else ''
        )
        if expected_mapping_sha and actual_mapping_sha != expected_mapping_sha:
            raise ValueError('모션축 설정 파일 버전이 실행 컨텍스트와 다릅니다')
        incoming_banks = MidiBankManager()
        if stored_banks is not None:
            incoming_banks.replace_state(stored_banks)
        with self._lock:
            same_context = (
                previous_project_id == self._project_id
                and self._selected_mapping_file_id == registry.file_id
                and self._banks.export_state() == incoming_banks.export_state()
                and self._execution_context.get('context_id') == context_id
            )
            self._axis_registry = registry
            self._selected_mapping_file_id = registry.file_id
            self._preferred_mapping_file_id = registry.file_id
            self._bank_config_file = mapping_file
            if not same_context:
                self._banks = incoming_banks
                self._current_motion_values = {}
                self._reset_bank_change_state_locked()
                self._motion_run_state = 'idle'
                self._motion_run_request_source = ''
                self._motion_studio_state = 'idle'
                self._playback_phase = 'idle'
                self._execution_context_ready = False
            self._execution_context = {
                'context_id': context_id,
                'project_id': self._project_id,
                'project_generation': int(payload.get('project_generation') or 0),
                'mapping_file_id': registry.file_id,
                'mapping_sha256': actual_mapping_sha,
            }
            self._bank_file_loaded = stored_banks is not None
            self._bank_file_dirty = False
        response = self._snapshot()
        # The project coordinator validates every node with the same
        # top-level acknowledgement contract.  `_snapshot()` keeps
        # these values nested for the UI, so expose them here as well.
        response.update({
            'context_id': context_id,
            'project_id': self._project_id,
            'mapping_file_id': registry.file_id,
            'mapping_sha256': actual_mapping_sha,
        })
        response['context_changed'] = not same_context
        response['message'] = (
            '현재 프로젝트 MIDI·모션축 컨텍스트로 전환했습니다'
            if not same_context else
            '현재 프로젝트 MIDI·모션축 컨텍스트가 이미 적용되어 있습니다'
        )
        return response

    def _cmd_confirm_context(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """적용된 실행 컨텍스트를 확인하고 MIDI 제어를 허용한다."""
        context_id = str(payload.get('context_id') or '').strip()
        with self._lock:
            if (
                not context_id
                or context_id != self._execution_context.get('context_id')
            ):
                raise ValueError('확인하려는 실행 컨텍스트가 적용된 설정과 다릅니다')
            self._execution_context_ready = True
        response = self._snapshot()
        response.update({
            'context_id': context_id,
            'project_id': self._project_id,
            'mapping_file_id': self._execution_context.get('mapping_file_id', ''),
            'mapping_sha256': self._execution_context.get('mapping_sha256', ''),
        })
        response['message'] = '현재 프로젝트 MIDI 제어 허용'
        return response

    def _cmd_invalidate_context(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """프로젝트 매핑·뱅크 메모리를 버린다."""
        with self._lock:
            self._project_id = ''
            self._mappings_dir = self._motion_projects_dir
            self._axis_registry = MotionAxisRegistry(self._motion_projects_dir)
            self._selected_mapping_file_id = ''
            self._preferred_mapping_file_id = ''
            self._run_mapping_file_id = ''
            self._latest_motion_state = {}
            self._bank_config_file = None
            self._banks = MidiBankManager()
            self._execution_context = {}
            self._execution_context_ready = False
            self._bank_file_loaded = False
            self._bank_file_dirty = False
            self._current_motion_values = {}
            self._reset_bank_change_state_locked()
        response = self._snapshot()
        response.update({
            'project_id': '',
            'context_id': '',
            'message': 'MIDI 프로젝트 매핑·뱅크 메모리 폐기',
        })
        return response

    def _cmd_save_mapping(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """뱅크 설정을 메모리에 임시 적용한다 · 파일은 건드리지 않는다."""
        bank_id = payload.get('bank_id') or self._banks.snapshot()['active_bank_id']
        with self._lock:
            previous_bank_state = self._banks.snapshot()
            self._banks.update_bank(
                bank_id,
                name=payload.get('name'),
                mappings=payload.get('mappings'),
            )
            reset_select = self._finish_bank_settings_change_locked(previous_bank_state)
            self._bank_file_dirty = True
        response = self._snapshot()
        response['select_reset'] = reset_select
        response['message'] = (
            'MIDI 뱅크 설정 임시 적용 · SELECT 전체 해제 · 페이더 0 이동'
            if reset_select else
            'MIDI 필터 설정 임시 적용 · SELECT 상태 유지'
        )
        return response

    def _cmd_create_bank(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """활성 뱅크를 복제해 새 뱅크를 만든다."""
        with self._lock:
            bank = self._banks.create_bank(payload.get('name'), copy_from_active=True)
            self._banks.select_bank(bank['bank_id'])
            self._reset_bank_change_state_locked()
            self._bank_file_dirty = True
        response = self._snapshot()
        response['message'] = (
            'MIDI 뱅크 추가 완료 (메모리 전용) · SELECT 전체 해제 · 페이더 0 이동'
        )
        return response

    def _cmd_select_bank(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """활성 뱅크를 바꾼다."""
        with self._lock:
            self._banks.select_bank(payload.get('bank_id'))
            self._reset_bank_change_state_locked()
            self._bank_file_dirty = True
        response = self._snapshot()
        response['message'] = 'MIDI 뱅크 전환 완료 · SELECT 전체 해제 · 페이더 0 이동'
        return response

    def _cmd_delete_bank(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """뱅크를 지운다."""
        with self._lock:
            self._banks.delete_bank(payload.get('bank_id'))
            self._reset_bank_change_state_locked()
            self._bank_file_dirty = True
        response = self._snapshot()
        response['message'] = 'MIDI 뱅크 삭제 완료 · SELECT 전체 해제 · 페이더 0 이동'
        return response

    def _cmd_save_banks_to_file(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """파일 저장은 이 노드의 권한이 아니다 · 거부를 응답한다."""
        response = {
            'success': False,
            'message': '파일 저장은 motion_mapping_manager만 수행할 수 있습니다',
        }
        return response

    def _cmd_apply_banks(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """파일 또는 페이로드의 뱅크 상태를 적용한다."""
        self._select_project_mapping_dir(payload)
        mapping_file = self._requested_mapping_file(payload)
        stored_banks = payload.get('midi_banks')
        if stored_banks is None:
            stored_banks = load_midi_banks(mapping_file)
        if stored_banks is None:
            raise ValueError('모션축 설정 파일에 저장된 midi_banks가 없습니다')
        with self._lock:
            previous_bank_state = self._banks.snapshot()
            self._axis_registry = MotionAxisRegistry(self._mappings_dir)
            self._axis_registry.refresh(
                mapping_file.name, self._latest_motion_state
            )
            self._selected_mapping_file_id = mapping_file.name
            self._preferred_mapping_file_id = mapping_file.name
            self._banks.replace_state(stored_banks)
            self._bank_config_file = mapping_file
            reset_select = self._finish_bank_settings_change_locked(previous_bank_state)
            self._bank_file_loaded = True
            self._bank_file_dirty = False
        response = self._snapshot()
        response['select_reset'] = reset_select
        response['message'] = (
            '파일의 MIDI 뱅크 적용 완료 · SELECT 전체 해제 · 페이더 0 이동'
            if reset_select else
            '파일의 MIDI 필터 설정 적용 완료 · SELECT 상태 유지'
        )
        return response

    def _cmd_reset_runtime_values(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """실시간 값만 초기화한다 · 저장 파일은 그대로다."""
        with self._lock:
            self._reset_live_values_locked()
        response = self._snapshot()
        response['message'] = 'MIDI 실시간 값 초기화 완료 · 저장 파일은 변경하지 않았습니다'
        return response

    def _cmd_resync_selected_faders(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """SELECT된 페이더를 물리 위치와 다시 맞춘다."""
        with self._lock:
            sync_result = self._resync_controlled_faders_locked()
        response = self._snapshot()
        response.update(sync_result)
        response['success'] = not sync_result['errors']
        response['message'] = (
            f'SELECT 페이더 {len(sync_result["synced"])}개 재동기화 시작'
            if not sync_result['errors']
            else '일부 SELECT 페이더 재동기화 실패'
        )
        return response

    def _cmd_studio_recording_prepare(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """녹화 전 모든 페이더를 물리 0으로 보낸다."""
        with self._lock:
            prepare_result = self._prepare_studio_recording_locked()
        response = self._snapshot()
        response.update(prepare_result)
        response['success'] = not prepare_result['errors']
        response['message'] = (
            (
                '모든 SELECT 해제 · SELECT 잠금 · 모든 페이더 물리 0 이동 시작'
                + (
                    f' · 범위 불일치 연동 채널 '
                    f'{len(prepare_result["unavailable_channels"])}개 선택 불가'
                    if prepare_result['unavailable_channels'] else ''
                )
            )
            if not prepare_result['errors']
            else prepare_result['errors'][0]
        )
        return response

    def _cmd_studio_recording_zero_status(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """페이더 0 복귀 상태를 조회한다."""
        with self._lock:
            zero_status = self._studio_recording_zero_status_locked()
        response = self._snapshot()
        response.update(zero_status)
        response['success'] = True
        if not zero_status['device_connected']:
            response['message'] = 'MIDI 장치가 연결되지 않았습니다'
        elif zero_status['ready']:
            response['message'] = '모든 MIDI 페이더의 물리 0 복귀 확인 완료'
        else:
            channels = ', '.join(
                f'{item["channel"]}(raw {item["raw"]})'
                for item in zero_status['pending_channels']
            )
            response['message'] = f'MIDI 페이더 0 복귀 대기: 채널 {channels}'
        return response

    def _cmd_studio_recording_ready(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """SELECT 잠금을 풀어 녹화 축 선택을 허용한다."""
        with self._lock:
            self._finish_studio_recording_initialization_locked()
        response = self._snapshot()
        response['message'] = 'MIDI SELECT 잠금 해제 · 녹화할 축을 선택하세요'
        return response

    def _cmd_connect_device(self, payload: Dict[str, Any], command: str) -> Dict[str, Any]:
        """MIDI 장치 연결·해제를 요청한다."""
        with self._lock:
            self._reset_runtime_controls_locked()
            self._pending_fader_positions = [None] * MIDI_CHANNEL_COUNT
            self._pending_fader_input_generations = list(
                self._fader_input_generation
            )
        connection_command = String()
        connection_command.data = (
            'connect' if command == 'connect_device' else 'disconnect'
        )
        self._connection_command_publisher.publish(connection_command)
        response = self._snapshot()
        response['message'] = (
            'MIDI 연결 요청을 전송했습니다'
            if command == 'connect_device'
            else 'MIDI 연결 해제 요청을 전송했습니다'
        )
        return response

    def _cmd_status(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """현재 상태를 돌려준다."""
        response = self._snapshot()
        return response
    def _request_callback(self, msg: String) -> None:
        request = command_router.parse_request(msg.data, default_command='status')
        if request is None:
            return
        command = request.command
        response: Dict[str, Any]
        try:
            self._validate_request_generation(command, request.generation, request.payload)
            handler = self._command_router().resolve(command)
            if handler is None:
                response = command_router.error_response(
                    f'unsupported command: {command}'
                )
            else:
                response = handler(request.payload)
        except ValueError as exc:
            response = command_router.error_response(exc)
        self._publish_json(
            self._response_publisher, command_router.finalize(response, request)
        )

    #: 실행 컨텍스트를 새로 세우는 명령 · 이때만 세대가 오를 수 있다
    CONTEXT_COMMANDS = frozenset({'select_project', 'invalidate_context'})

    def _validate_request_generation(
        self, command: str, request_generation: Any, payload: Dict[str, Any]
    ) -> int:
        advancing = command in self.CONTEXT_COMMANDS
        value = generation_mod.validate_request_generation(
            request_generation,
            payload,
            current_generation=getattr(self, '_project_generation', 0),
            advances_context=advancing,
        )
        if advancing:
            self._project_generation = value
        return value


def main(args: List[str] | None = None) -> None:
    rclpy.init(args=args)
    node = MidiControlNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()
