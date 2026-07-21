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


FILTER_ORDER = 2
FILTER_MAX_TIME_CONSTANT_SEC = 0.5
FILTER_MAX_STEP_SEC = 0.05
MIDI_COMMAND_PERIOD_SEC = 0.02
MIDI_COMMAND_DEADBAND_DEG = 0.01
FADER_SYNC_MIN_DURATION_SEC = 0.10
FADER_PARK_RETRY_SEC = 0.15
FADER_PARK_TOLERANCE_RAW = 16
SELECT_TOGGLE_DEBOUNCE_SEC = 0.08
SELECT_RANGE_TOLERANCE_PERCENT = 0.25
LINKED_RANGE_TOLERANCE_DEG = 1e-6
LINKED_POSITION_TOLERANCE_DEG = 1.0


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
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


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
    """Inverse of motor_target_from_motion for SELECT fader synchronization."""
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
        raise ValueError('연동 Motion ID의 모션 범위가 서로 다릅니다')
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
        self.input_topic = str(self.declare_parameter('input_topic', '/xtouch/midi').value)
        self.state_topic = str(
            self.declare_parameter('state_topic', '/motion_web/midi_monitor/state').value
        )
        self.request_topic = str(
            self.declare_parameter('request_topic', '/motion_web/midi_monitor/request').value
        )
        self.response_topic = str(
            self.declare_parameter('response_topic', '/motion_web/midi_monitor/response').value
        )
        self.feedback_topic = str(
            self.declare_parameter('feedback_topic', '/xtouch/feedback').value
        )
        self.input_state_topic = str(
            self.declare_parameter('input_state_topic', '/xtouch/input_state').value
        )
        self.connection_command_topic = str(
            self.declare_parameter(
                'connection_command_topic', '/xtouch/connection/command'
            ).value
        )
        self.connection_state_topic = str(
            self.declare_parameter(
                'connection_state_topic', '/xtouch/connection/state'
            ).value
        )
        self.motion_state_topic = str(
            self.declare_parameter('motion_state_topic', '/motion_control/motion_state').value
        )
        self.motion_run_status_topic = str(
            self.declare_parameter(
                'motion_run_status_topic', '/motion_control/motion_run_status'
            ).value
        )
        self.motion_mapping_response_topic = str(
            self.declare_parameter(
                'motion_mapping_response_topic',
                '/motion_control/motion_mapping_response',
            ).value
        )
        self.motor_request_topic = str(
            self.declare_parameter(
                'motor_request_topic', '/motion_control/midi_position_request'
            ).value
        )
        self.motor_result_topic = str(
            self.declare_parameter(
                'motor_result_topic', '/motion_control/midi_position_result'
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
        self._mappings_dir = self._motion_projects_dir
        self.publish_hz = max(1.0, float(self.declare_parameter('publish_hz', 10.0).value))
        self.stale_timeout_sec = max(
            0.1,
            float(self.declare_parameter('stale_timeout_sec', 0.5).value),
        )

        self._lock = threading.Lock()
        self._last_received_monotonic: float | None = None
        self._last_received_wall: float | None = None
        self._device_connected = False
        self._device_connection_message = 'MIDI 장치 연결 상태 확인 중'
        self._raw_channels = [0] * MIDI_CHANNEL_COUNT
        self._channels = [0.0] * MIDI_CHANNEL_COUNT
        self._filter_stage1 = [0.0] * MIDI_CHANNEL_COUNT
        self._filter_stage2 = [0.0] * MIDI_CHANNEL_COUNT
        self._filter_last_at: List[float | None] = [None] * MIDI_CHANNEL_COUNT
        self._touch = [False] * MIDI_CHANNEL_COUNT
        self._physical_touch = [False] * MIDI_CHANNEL_COUNT
        self._fader_moving = [False] * MIDI_CHANNEL_COUNT
        self._bridge_fader_syncing = [False] * MIDI_CHANNEL_COUNT
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
        self._studio_zero_fader_targets = [0] * MIDI_CHANNEL_COUNT
        self._final_output_values = [0.0] * MIDI_CHANNEL_COUNT
        # Runtime always starts with SELECT OFF, so park all physical faders.
        self._pending_fader_positions: List[int | None] = [0] * MIDI_CHANNEL_COUNT
        self._fader_sync_targets: List[int | None] = [None] * MIDI_CHANNEL_COUNT
        self._awaiting_fader_sync = [False] * MIDI_CHANNEL_COUNT
        self._fader_sync_not_before = [0.0] * MIDI_CHANNEL_COUNT
        self._fader_parking = [False] * MIDI_CHANNEL_COUNT
        self._fader_park_last_command_at = [0.0] * MIDI_CHANNEL_COUNT
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
        self._pending_motor_requests: Dict[Any, Dict[str, Any]] = {}
        self._motor_follow_active = [False] * MIDI_CHANNEL_COUNT
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
        *,
        require_positions: bool = False,
    ) -> List[Dict[str, Any]]:
        self._ensure_linked_runtime_state_locked()
        group = []
        for motion_id in mapping_motion_ids(mapping):
            row = self._axis_registry.mapping(motion_id)
            motor_axis = self._axis_registry.motor_axis(motion_id)
            if row is None or motor_axis is None:
                raise ValueError(f'{motion_id}: 모션축 설정에 매칭되지 않았습니다')
            motion_state = getattr(self, '_latest_motion_state', {})
            motor_position = self._position_for_axis(
                motion_state, motor_axis
            )
            motor = self._motor_for_axis(motion_state, motor_axis)
            if motor is None:
                raise ValueError(f'{motion_id}: 현재 모터 정보를 확인할 수 없습니다')
            if require_positions and motor_position is None:
                raise ValueError(f'{motion_id}: 현재 모터 위치를 확인할 수 없습니다')
            group.append({
                'motion_id': motion_id,
                'row': row,
                'axis': motor_axis,
                'motor': motor,
                'motor_position': motor_position,
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

    def _ensure_fader_parking_state_locked(self) -> None:
        if not hasattr(self, '_fader_parking'):
            self._fader_parking = [False] * MIDI_CHANNEL_COUNT
        if not hasattr(self, '_fader_park_last_command_at'):
            self._fader_park_last_command_at = [0.0] * MIDI_CHANNEL_COUNT

    def _start_fader_parking_locked(self, channel: int, now: float) -> None:
        self._ensure_fader_parking_state_locked()
        if not hasattr(self, '_last_feedback'):
            self._last_feedback = [None] * MIDI_CHANNEL_COUNT
        self._fader_parking[channel] = True
        self._fader_park_last_command_at[channel] = now
        self._pending_fader_positions[channel] = 0
        self._fader_sync_targets[channel] = 0
        self._awaiting_fader_sync[channel] = True
        self._fader_sync_not_before[channel] = now + FADER_SYNC_MIN_DURATION_SEC
        self._last_feedback[channel] = None
        self._motor_command_state[channel] = 'parking_fader'
        self._motor_command_message[channel] = 'SELECT 해제 · 페이더 0 복귀 중'

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
            self._fader_park_last_command_at[channel] = 0.0
            self._fader_sync_targets[channel] = None
            self._awaiting_fader_sync[channel] = False
            self._fader_sync_not_before[channel] = 0.0
            self._raw_channels[channel] = 0
            self._channels[channel] = 0.0
            self._filter_stage1[channel] = 0.0
            self._filter_stage2[channel] = 0.0
            self._motor_command_state[channel] = 'inactive'
            self._motor_command_message[channel] = 'SELECT 사용 가능'
            return True
        if (
            not self._physical_touch[channel]
            and not self._fader_moving[channel]
            and now - self._fader_park_last_command_at[channel]
            >= FADER_PARK_RETRY_SEC
        ):
            self._pending_fader_positions[channel] = 0
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
        self._publish_json(publisher, {
            'request_id': f'midi-hold-{channel}-{self._request_sequence}',
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
        with self._lock:
            mappings = self._banks.active_bank()['mappings']
            for channel in range(MIDI_CHANNEL_COUNT):
                raw = max(
                    MIDI_VALUE_MIN,
                    min(MIDI_VALUE_MAX, int(self._array_value(msg.channel, channel, 0))),
                )
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
                    self._motor_command_state[channel] = 'ready'
                    self._motor_command_message[channel] = '페이더 조작 대기'
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
                    and not self._studio_select_locked
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
                            group = self._mapping_group_locked(
                                mappings[channel], require_positions=True
                            )
                            motion_values = [
                                motion_value_from_motor(
                                    float(item['motor_position']), item['row']
                                )
                                for item in group
                            ]
                            if (
                                max(motion_values) - min(motion_values)
                                > LINKED_POSITION_TOLERANCE_DEG
                            ):
                                raise ValueError(
                                    '연동 축의 현재 모션값이 서로 다릅니다. '
                                    '초기 위치 이동 후 다시 SELECT 하세요'
                                )
                            motion_value = sum(motion_values) / len(motion_values)
                            safe_range = safe_motion_range_for_group(group)
                            fader_target = raw_fader_for_motion(
                                motion_value,
                                group[0]['row'],
                                mappings[channel],
                                safe_range,
                            )
                        except ValueError as exc:
                            self._control_enabled[channel] = False
                            self._motor_command_state[channel] = 'activation_rejected'
                            self._motor_command_message[channel] = f'활성화 불가: {exc}'
                            self._clear_pending_channel_locked(channel)
                            self._motor_follow_active[channel] = False
                        else:
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
                            self._raw_channels[channel] = fader_target
                            self._channels[channel] = float(fader_target)
                            self._filter_stage1[channel] = float(fader_target)
                            self._filter_stage2[channel] = float(fader_target)
                            self._pending_fader_positions[channel] = fader_target
                            self._fader_sync_targets[channel] = fader_target
                            self._awaiting_fader_sync[channel] = True
                            self._fader_sync_not_before[channel] = (
                                now + FADER_SYNC_MIN_DURATION_SEC
                            )
                            self._motor_command_state[channel] = 'syncing_fader'
                            self._motor_command_message[channel] = (
                                f'{len(group)}개 연동 축 현재 위치로 페이더 동기화 중'
                            )
                            self._last_group_motor_targets[channel] = {
                                int(item['axis']): float(item['motor_position'])
                                for item in group
                            }
                            self._last_motor_target[channel] = float(
                                group[0]['motor_position']
                            )
                            self._motor_follow_active[channel] = False
                elif select_rising and self._studio_select_locked:
                    self._motor_command_state[channel] = 'studio_initializing'
                    self._motor_command_message[channel] = (
                        '녹화 초기화 중 · SELECT 입력 무시됨'
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

                if input_valid and self._control_enabled[channel]:
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
                            self._pending_motor_requests[(channel, axis)] = {
                                'request_id': f'midi-{channel}-{self._request_sequence}',
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

    def _publish_motor_request_batch(self) -> None:
        with self._lock:
            if not self._pending_motor_requests:
                return
            targets = list(self._pending_motor_requests.values())
            self._pending_motor_requests.clear()
            now = time.monotonic()
            for target in targets:
                channel = int(target['channel'])
                self._last_motor_command_at[channel] = now
                self._motor_command_message[channel] = '다축 모터 위치 명령 전달 중'
            self._request_sequence += 1
            request_id = f'midi-batch-{self._request_sequence}'
            counts: Dict[int, int] = {}
            for target in targets:
                channel = int(target['channel'])
                counts[channel] = counts.get(channel, 0) + 1
        self._publish_json(self._motor_request_publisher, {
            'request_id': request_id,
            'targets': targets,
            'atomic_channels': [
                channel for channel, count in counts.items() if count > 1
            ],
        })

    def _deactivate_control_channel_locked(
        self, channel: int, *, request_motor_hold: bool = True
    ) -> None:
        """Release one MIDI line and park its motorized fader at zero."""
        self._ensure_linked_runtime_state_locked()
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
        self._start_fader_parking_locked(channel, time.monotonic())

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

    def _connection_state_callback(self, msg: String) -> None:
        try:
            payload = json.loads(msg.data)
        except json.JSONDecodeError:
            return
        if not isinstance(payload, dict):
            return
        connected = bool(payload.get('connected'))
        message = str(payload.get('message') or '')
        with self._lock:
            changed = connected != self._device_connected
            self._device_connected = connected
            self._device_connection_message = message
            if changed:
                # A USB reconnect creates a new hardware session. Never retain
                # SELECT/motor ownership across it. Replay bank/LCD/LED only;
                # do not issue a fader position because that can make the
                # motorized fader fight the user's hand after a USB restart.
                self._reset_runtime_controls_locked()
                self._pending_fader_positions = [None] * MIDI_CHANNEL_COUNT
                if not connected:
                    self._touch = [False] * MIDI_CHANNEL_COUNT
                    self._physical_touch = [False] * MIDI_CHANNEL_COUNT
                    self._fader_moving = [False] * MIDI_CHANNEL_COUNT
                    self._bridge_fader_syncing = [False] * MIDI_CHANNEL_COUNT

    def _motor_result_callback(self, msg: String) -> None:
        try:
            payload = json.loads(msg.data)
        except json.JSONDecodeError:
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
                else:
                    self._approved_motion_values[channel] = {}
                    self._approved_motor_targets[channel] = {}
                self._motor_command_state[channel] = 'commanding' if success else 'rejected'
                self._motor_command_message[channel] = str(
                    channel_results[-1].get('message') or ''
                )

    def _motion_state_callback(self, msg: String) -> None:
        try:
            payload = json.loads(msg.data)
        except json.JSONDecodeError:
            return
        if isinstance(payload, dict):
            with self._lock:
                self._latest_motion_state = payload

    def _motion_run_status_callback(self, msg: String) -> None:
        try:
            payload = json.loads(msg.data)
        except json.JSONDecodeError:
            return
        if not isinstance(payload, dict):
            return
        mapping_file_id = str(payload.get('mapping_file_id') or '').strip()
        with self._lock:
            self._run_mapping_file_id = mapping_file_id
            self._preferred_mapping_file_id = (
                self._run_mapping_file_id or self._selected_mapping_file_id
            )

    def _motion_mapping_response_callback(self, msg: String) -> None:
        try:
            payload = json.loads(msg.data)
        except json.JSONDecodeError:
            return
        if not isinstance(payload, dict) or payload.get('success') is False:
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
            device_connected = self._device_connected
            device_connection_message = self._device_connection_message
            raw_values = list(self._raw_channels)
            filtered_values = list(self._channels)
            touch = list(self._touch)
            physical_touch = list(self._physical_touch)
            fader_moving = list(self._fader_moving)
            bridge_fader_syncing = list(self._bridge_fader_syncing)
            dial = list(self._dial)
            buttons = [
                [self._btn0[index], self._btn1[index], self._btn2[index], self._btn3[index]]
                for index in range(MIDI_CHANNEL_COUNT)
            ]
            confirmed = list(self._confirmed)
            control_enabled = list(self._control_enabled)
            motor_angle_mode = list(self._motor_angle_mode)
            motion_state = dict(self._latest_motion_state)
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
                    group = self._mapping_group_locked(mapping, require_positions=True)
                    motion_values = [
                        motion_value_from_motor(
                            float(item['motor_position']), item['row']
                        )
                        for item in group
                    ]
                    if (
                        max(motion_values) - min(motion_values)
                        > LINKED_POSITION_TOLERANCE_DEG
                    ):
                        raise ValueError('linked positions differ')
                    raw_fader_for_motion(
                        sum(motion_values) / len(motion_values),
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
                    motor_angle_mode[channel] = False
            for channel, mapping in enumerate(mappings):
                if control_enabled[channel]:
                    self._final_output_values[channel] = self._filtered_output_14bit(
                        filtered_values[channel], mapping
                    )
            final_output_values = list(self._final_output_values)
            motor_command_states = list(self._motor_command_state)
            motor_command_messages = list(self._motor_command_message)
            awaiting_fader_sync = list(self._awaiting_fader_sync)
            fader_parking = list(self._fader_parking)
            self._ensure_approved_command_state_locked()
            approved_motion_values = [
                dict(values) for values in self._approved_motion_values
            ]
            approved_motor_targets = [
                dict(values) for values in self._approved_motor_targets
            ]
            mapping_file_id = self._axis_registry.file_id
        age_sec = None if last_monotonic is None else max(0.0, now_monotonic - last_monotonic)
        connected = (
            device_connected
            and age_sec is not None
            and age_sec <= self.stale_timeout_sec
        )
        channels = []
        for channel, mapping in enumerate(mappings):
            raw_value = raw_values[channel]
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
            approved_values = approved_motion_values[channel]
            approved_targets = approved_motor_targets[channel]
            approved_complete = bool(motion_ids) and all(
                motion_id in approved_values for motion_id in motion_ids
            )
            motion_value = (
                sum(approved_values[motion_id] for motion_id in motion_ids)
                / len(motion_ids)
                if approved_complete else None
            )
            motor_target = (
                approved_targets.get(int(motor_axis))
                if motor_axis is not None else None
            )
            channels.append({
                **mapping,
                'channel_number': channel + 1,
                'raw_value': raw_value,
                'filtered_value': round(filtered_value, 6),
                'final_output_value': round(final_output_value, 6),
                'raw_normalized': round(raw_value / MIDI_VALUE_MAX, 6),
                'filtered_normalized': round(filtered_value / MIDI_VALUE_MAX, 6),
                'normalized': round(final_output_value / MIDI_VALUE_MAX, 6),
                'value_confirmed': confirmed[channel],
                'touch': touch[channel],
                'physical_touch': physical_touch[channel],
                'fader_moving': fader_moving[channel],
                'input_valid': touch[channel],
                'dial': dial[channel],
                'buttons': buttons[channel],
                'control_enabled': control_enabled[channel],
                'motion_ids': motion_ids,
                'motion_axis_matched': axis_groups_matched[channel],
                'motion_group_valid': group_valid[channel],
                'motion_group_message': group_messages[channel],
                'matched_motor_axis': motor_axis,
                'matched_motor_axes': group_axes,
                'display_motor_angle': motor_angle_mode[channel],
                'motor_angle_deg': None if motor_angle is None else round(motor_angle, 6),
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
                    motion_id: round(approved_values[motion_id], 6)
                    for motion_id in motion_ids
                    if motion_id in approved_values
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
            'message': (
                'MIDI 데이터 수신 정상'
                if connected else (
                    device_connection_message or 'MIDI 데이터 수신 대기'
                )
            ),
            'input_topic': self.input_topic,
            'last_received_at': last_wall,
            'age_sec': None if age_sec is None else round(age_sec, 3),
            'value_bits': 14,
            'value_min': MIDI_VALUE_MIN,
            'value_max': MIDI_VALUE_MAX,
            'unit': '14bit',
            'motor_output_enabled': self._execution_context_ready,
            'motor_output_path': 'motion_supervisor',
            'select_locked': self._studio_select_locked,
            'select_lock_reason': (
                '모션 녹화 초기화 중'
                if self._studio_select_locked else ''
            ),
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
            display_motor_angle = bool(channel['display_motor_angle'])
            motor_angle = channel['motor_angle_deg']
            bottom = (
                'N/A' if motor_angle is None else f'{motor_angle:.1f}'
            ) if display_motor_angle else str(int(round(channel['final_output_value'])))
            feedback = (
                int(bool(channel['control_enabled'])),
                int(display_motor_angle),
                int(channel['filter_level']),
                str(channel['motion_id']),
                bottom,
            )
            index = int(channel['channel'])
            with self._lock:
                fader_position = self._pending_fader_positions[index]
                self._pending_fader_positions[index] = None
            # A consumed one-shot fader target must not make the following
            # cycle look like a UI-state change. Otherwise every retry emits
            # a second LED/LCD-only packet immediately after the fader packet.
            if self._last_feedback[index] == feedback and fader_position is None:
                continue
            self._last_feedback[index] = feedback
            hardware_feedback = (*feedback, -1 if fader_position is None else fader_position)
            msg = String()
            msg.data = '\t'.join((str(index), *(str(value) for value in hardware_feedback)))
            self._feedback_publisher.publish(msg)

    def _reset_runtime_controls_locked(self) -> None:
        self._control_enabled = [False] * MIDI_CHANNEL_COUNT
        self._final_output_values = [0.0] * MIDI_CHANNEL_COUNT
        self._pending_fader_positions = [0] * MIDI_CHANNEL_COUNT
        self._fader_sync_targets = [None] * MIDI_CHANNEL_COUNT
        self._awaiting_fader_sync = [False] * MIDI_CHANNEL_COUNT
        self._fader_sync_not_before = [0.0] * MIDI_CHANNEL_COUNT
        self._fader_parking = [False] * MIDI_CHANNEL_COUNT
        self._fader_park_last_command_at = [0.0] * MIDI_CHANNEL_COUNT
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
        self._motor_command_state = ['inactive'] * MIDI_CHANNEL_COUNT
        self._motor_command_message = [''] * MIDI_CHANNEL_COUNT
        self._motor_angle_mode = [False] * MIDI_CHANNEL_COUNT
        self._last_feedback = [None] * MIDI_CHANNEL_COUNT

    def _reset_bank_change_state_locked(self) -> None:
        """Release every SELECT line and park all motorized faders at zero."""
        self._reset_live_values_locked()
        now = time.monotonic()
        for channel in range(MIDI_CHANNEL_COUNT):
            self._start_fader_parking_locked(channel, now)
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
        self._previous_dial = list(self._dial)
        self._reset_runtime_controls_locked()

    def _resync_controlled_faders_locked(self) -> Dict[str, Any]:
        """Re-pickup every SELECT-owned fader from current motor feedback."""
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
                group = self._mapping_group_locked(mapping, require_positions=True)
                motion_values = [
                    motion_value_from_motor(float(item['motor_position']), item['row'])
                    for item in group
                ]
                if (
                    max(motion_values) - min(motion_values)
                    > LINKED_POSITION_TOLERANCE_DEG
                ):
                    raise ValueError('연동 축의 현재 모션값이 서로 다릅니다')
                motion_value = sum(motion_values) / len(motion_values)
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
            self._clear_pending_channel_locked(channel)
            self._raw_channels[channel] = fader_target
            self._channels[channel] = float(fader_target)
            self._filter_stage1[channel] = float(fader_target)
            self._filter_stage2[channel] = float(fader_target)
            self._pending_fader_positions[channel] = fader_target
            self._fader_sync_targets[channel] = fader_target
            self._awaiting_fader_sync[channel] = True
            self._fader_sync_not_before[channel] = now + FADER_SYNC_MIN_DURATION_SEC
            self._motor_command_state[channel] = 'syncing_fader'
            self._motor_command_message[channel] = '초기 위치 기준으로 페이더 재동기화 중'
            self._last_group_motor_targets[channel] = {
                int(item['axis']): float(item['motor_position']) for item in group
            }
            self._last_motor_target[channel] = float(group[0]['motor_position'])
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
        validated_groups = set()
        for mapping in mappings:
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
            if signature in validated_groups:
                continue
            validated_groups.add(signature)
            try:
                self._mapping_group_locked(mapping)
            except ValueError as exc:
                errors.append(str(exc))
        if errors:
            return {'targets': targets, 'errors': errors}

        self._studio_select_locked = True
        self._pending_motor_requests.clear()
        for channel, mapping in enumerate(mappings):
            # Recording initialization immediately hands motor ownership to the
            # motion run manager.  Do not insert a separate MIDI hold command
            # between SELECT release and that ownership transfer.
            self._deactivate_control_channel_locked(
                channel, request_motor_hold=False
            )
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
            self._pending_fader_positions[channel] = fader_target
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
        return {'targets': targets, 'errors': errors}

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

    def _request_callback(self, msg: String) -> None:
        try:
            request = json.loads(msg.data)
        except json.JSONDecodeError:
            return
        if not isinstance(request, dict):
            return
        request_id = str(request.get('request_id') or '')
        command = str(request.get('command') or 'status')
        payload = request.get('payload') if isinstance(request.get('payload'), dict) else {}
        response: Dict[str, Any]
        try:
            if command == 'select_project':
                previous_project_id = self._project_id
                self._select_project_mapping_dir(payload)
                preferred = str(payload.get('mapping_file_id') or '').strip()
                registry = MotionAxisRegistry(self._mappings_dir)
                registry.refresh(preferred, self._latest_motion_state)
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
                        self._reset_bank_change_state_locked()
                        self._execution_context_ready = False
                    self._execution_context = {
                        'context_id': context_id,
                        'project_id': self._project_id,
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
            elif command == 'confirm_context':
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
            elif command == 'invalidate_context':
                with self._lock:
                    was_ready = self._execution_context_ready
                    self._execution_context_ready = False
                    if was_ready or any(self._control_enabled):
                        self._reset_bank_change_state_locked()
                response = self._snapshot()
                response['message'] = '현재 프로젝트 MIDI 제어 차단'
            elif command in {'save_mapping', 'update_bank'}:
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
            elif command == 'create_bank':
                with self._lock:
                    bank = self._banks.create_bank(payload.get('name'), copy_from_active=True)
                    self._banks.select_bank(bank['bank_id'])
                    self._reset_bank_change_state_locked()
                    self._bank_file_dirty = True
                response = self._snapshot()
                response['message'] = (
                    'MIDI 뱅크 추가 완료 (메모리 전용) · SELECT 전체 해제 · 페이더 0 이동'
                )
            elif command == 'select_bank':
                with self._lock:
                    self._banks.select_bank(payload.get('bank_id'))
                    self._reset_bank_change_state_locked()
                    self._bank_file_dirty = True
                response = self._snapshot()
                response['message'] = 'MIDI 뱅크 전환 완료 · SELECT 전체 해제 · 페이더 0 이동'
            elif command == 'delete_bank':
                with self._lock:
                    self._banks.delete_bank(payload.get('bank_id'))
                    self._reset_bank_change_state_locked()
                    self._bank_file_dirty = True
                response = self._snapshot()
                response['message'] = 'MIDI 뱅크 삭제 완료 · SELECT 전체 해제 · 페이더 0 이동'
            elif command == 'save_banks_to_file':
                response = {
                    'success': False,
                    'message': '파일 저장은 motion_mapping_manager만 수행할 수 있습니다',
                }
            elif command in {'apply_banks', 'load_banks_from_file'}:
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
            elif command == 'reset_runtime_values':
                with self._lock:
                    self._reset_live_values_locked()
                response = self._snapshot()
                response['message'] = 'MIDI 실시간 값 초기화 완료 · 저장 파일은 변경하지 않았습니다'
            elif command == 'resync_selected_faders':
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
            elif command == 'studio_recording_prepare':
                with self._lock:
                    prepare_result = self._prepare_studio_recording_locked()
                response = self._snapshot()
                response.update(prepare_result)
                response['success'] = not prepare_result['errors']
                response['message'] = (
                    '모든 SELECT 해제 · SELECT 잠금 · 모든 페이더 물리 0 이동 시작'
                    if not prepare_result['errors']
                    else prepare_result['errors'][0]
                )
            elif command == 'studio_recording_zero_status':
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
            elif command == 'studio_recording_ready':
                with self._lock:
                    self._finish_studio_recording_initialization_locked()
                response = self._snapshot()
                response['message'] = 'MIDI SELECT 잠금 해제 · 녹화할 축을 선택하세요'
            elif command in {'connect_device', 'disconnect_device'}:
                with self._lock:
                    self._reset_runtime_controls_locked()
                    self._pending_fader_positions = [None] * MIDI_CHANNEL_COUNT
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
            elif command == 'status':
                response = self._snapshot()
            else:
                response = {
                    'success': False,
                    'message': f'unsupported command: {command}',
                }
        except ValueError as exc:
            response = {
                'success': False,
                'message': str(exc),
            }
        response['request_id'] = request_id
        self._publish_json(self._response_publisher, response)


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
