import json
import math
import threading
import time
from pathlib import Path
from typing import Any, Dict, List

import rclpy
import yaml
from midi_msgs.msg import Midi
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy
from std_msgs.msg import String

from midi_control.bank_manager import (
    FILTER_LEVEL_MAX,
    MIDI_CHANNEL_COUNT,
    MIDI_VALUE_MAX,
    MIDI_VALUE_MIN,
    MidiBankManager,
)
from midi_control.config_store import load_midi_banks
from midi_control.motion_axis_registry import MotionAxisRegistry


FILTER_ORDER = 2
FILTER_MAX_TIME_CONSTANT_SEC = 0.5
FILTER_MAX_STEP_SEC = 0.05
MIDI_COMMAND_PERIOD_SEC = 0.02
MIDI_COMMAND_DEADBAND_DEG = 0.01
FADER_SYNC_MIN_DURATION_SEC = 0.10
SELECT_TOGGLE_DEBOUNCE_SEC = 0.08
SELECT_RANGE_TOLERANCE_PERCENT = 0.25


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


def motion_value_from_output(output_14bit: float, row: Dict[str, Any]) -> float:
    """Convert the final 14-bit MIDI output into motion-space degrees."""
    lower = _finite_float(row.get('motion_lower_deg'))
    upper = _finite_float(row.get('motion_upper_deg'))
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
) -> int:
    """Invert motion range and bank Min/Max/reverse into a physical fader value."""
    lower = _finite_float(row.get('motion_lower_deg'))
    upper = _finite_float(row.get('motion_upper_deg'))
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
        motion_data_dir = Path(
            str(
                self.declare_parameter(
                    'motion_data_dir', '/home/joonho_test/ros2_ws/motion_data'
                ).value
            )
        ).expanduser()
        self._mappings_dir = motion_data_dir / 'mappings'
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
        self._final_output_values = [0.0] * MIDI_CHANNEL_COUNT
        # Runtime always starts with SELECT OFF, so park all physical faders.
        self._pending_fader_positions: List[int | None] = [0] * MIDI_CHANNEL_COUNT
        self._fader_sync_targets: List[int | None] = [None] * MIDI_CHANNEL_COUNT
        self._awaiting_fader_sync = [False] * MIDI_CHANNEL_COUNT
        self._fader_sync_not_before = [0.0] * MIDI_CHANNEL_COUNT
        self._last_select_toggle_at = [0.0] * MIDI_CHANNEL_COUNT
        self._last_motor_command_at = [0.0] * MIDI_CHANNEL_COUNT
        self._last_motor_target: List[float | None] = [None] * MIDI_CHANNEL_COUNT
        self._pending_motor_requests: Dict[int, Dict[str, Any]] = {}
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
        self._connection_state_subscription = self.create_subscription(
            String, self.connection_state_topic, self._connection_state_callback, 10
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
                bridge_syncing = self._bridge_fader_syncing[channel]
                if (
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
                if select_rising and select_allowed:
                    self._last_select_toggle_at[channel] = now
                    motion_id = mappings[channel]['motion_id']
                    row = self._axis_registry.mapping(motion_id)
                    motor_axis = self._axis_registry.motor_axis(motion_id)
                    motor_position = (
                        self._position_for_axis(self._latest_motion_state, motor_axis)
                        if motor_axis is not None else None
                    )
                    currently_enabled = self._control_enabled[channel]
                    if currently_enabled:
                        self._deactivate_control_channel_locked(channel)
                    elif row is None or motor_axis is None:
                        self._motor_command_state[channel] = 'activation_rejected'
                        self._motor_command_message[channel] = (
                            '활성화 불가: 모션축 설정에 일치하는 모션 ID가 없습니다'
                        )
                    elif motor_position is None:
                        self._motor_command_state[channel] = 'activation_rejected'
                        self._motor_command_message[channel] = (
                            '활성화 불가: 현재 모터 위치를 확인할 수 없습니다'
                        )
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
                            motion_value = motion_value_from_motor(motor_position, row)
                            fader_target = raw_fader_for_motion(
                                motion_value, row, mappings[channel]
                            )
                        except ValueError as exc:
                            self._control_enabled[channel] = False
                            self._motor_command_state[channel] = 'activation_rejected'
                            self._motor_command_message[channel] = str(exc)
                            self._pending_motor_requests.pop(channel, None)
                            self._motor_follow_active[channel] = False
                        else:
                            # Multiple MIDI lines may map to the same motion axis,
                            # but only one line may own that motor at runtime.
                            for other_channel, other_mapping in enumerate(mappings):
                                if other_channel == channel:
                                    continue
                                if self._axis_registry.motor_axis(
                                    other_mapping['motion_id']
                                ) == motor_axis:
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
                            self._motor_command_message[channel] = '현재 모터 위치로 페이더 동기화 중'
                            self._last_motor_target[channel] = motor_position
                            self._motor_follow_active[channel] = False
                self._previous_btn3[channel] = select_pressed

                rec_pressed = self._btn0[channel]
                if rec_pressed and not self._previous_btn0[channel]:
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
                    row = self._axis_registry.mapping(mappings[channel]['motion_id'])
                    motor_axis = self._axis_registry.motor_axis(mappings[channel]['motion_id'])
                    if row is not None and motor_axis is not None:
                        final_output = self._filtered_output_14bit(
                            self._channels[channel], mappings[channel]
                        )
                        try:
                            motion_value = motion_value_from_output(final_output, row)
                            desired_motor_target = motor_target_from_motion(motion_value, row)
                        except ValueError as exc:
                            self._motor_command_state[channel] = 'rejected'
                            self._motor_command_message[channel] = str(exc)
                        else:
                            last_target = self._last_motor_target[channel]
                            motor_target = desired_motor_target

                            if (
                                last_target is not None
                                and abs(motor_target - last_target)
                                < MIDI_COMMAND_DEADBAND_DEG
                            ):
                                if (
                                    not input_valid
                                    and abs(desired_motor_target - motor_target)
                                    < MIDI_COMMAND_DEADBAND_DEG
                                    and abs(
                                        self._channels[channel]
                                        - self._raw_channels[channel]
                                    ) < 0.5
                                ):
                                    self._motor_follow_active[channel] = False
                                    self._pending_motor_requests.pop(channel, None)
                                continue
                            self._request_sequence += 1
                            self._pending_motor_requests[channel] = {
                                'request_id': f'midi-{channel}-{self._request_sequence}',
                                'channel': channel,
                                'motion_id': mappings[channel]['motion_id'],
                                'mapping_file_id': self._axis_registry.file_id,
                                'axis': motor_axis,
                                'motion_deg': motion_value,
                                'target_deg': motor_target,
                            }
                            self._last_motor_target[channel] = motor_target
                            self._motor_command_state[channel] = 'commanding'
                            self._motor_command_message[channel] = '모터 위치 명령 전달 대기'
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
        self._publish_json(self._motor_request_publisher, {
            'request_id': request_id,
            'targets': targets,
        })

    def _deactivate_control_channel_locked(self, channel: int) -> None:
        """Release one MIDI line and park its motorized fader at zero."""
        self._control_enabled[channel] = False
        self._pending_fader_positions[channel] = 0
        self._fader_sync_targets[channel] = None
        self._awaiting_fader_sync[channel] = False
        self._fader_sync_not_before[channel] = 0.0
        self._last_motor_target[channel] = None
        self._pending_motor_requests.pop(channel, None)
        self._motor_follow_active[channel] = False
        self._motor_command_state[channel] = 'inactive'
        self._motor_command_message[channel] = ''

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
            for result in results:
                if isinstance(result, dict):
                    self._apply_motor_result(result)
            return
        if isinstance(payload, dict):
            self._apply_motor_result(payload)

    def _apply_motor_result(self, payload: Dict[str, Any]) -> None:
        try:
            channel = int(payload.get('channel'))
        except (TypeError, ValueError):
            return
        if channel < 0 or channel >= MIDI_CHANNEL_COUNT:
            return
        with self._lock:
            success = payload.get('success') is True
            self._motor_command_state[channel] = 'commanding' if success else 'rejected'
            self._motor_command_message[channel] = str(payload.get('message') or '')

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
        self._axis_registry.refresh(self._preferred_mapping_file_id)
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
            self._refresh_axis_registry_locked(now_monotonic)
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
            matched_axes = [
                self._axis_registry.motor_axis(mapping['motion_id']) for mapping in mappings
            ]
            matched_rows = [
                self._axis_registry.mapping(mapping['motion_id']) for mapping in mappings
            ]
            # An activation error is a live condition. If the motor later moves
            # into this line's representable range (or the mapping is repaired),
            # clear the stale red/error state without requiring another SELECT.
            for channel, mapping in enumerate(mappings):
                if self._motor_command_state[channel] != 'activation_rejected':
                    continue
                row = matched_rows[channel]
                motor_axis = matched_axes[channel]
                motor_position = (
                    self._position_for_axis(motion_state, motor_axis)
                    if motor_axis is not None else None
                )
                if (
                    mapping.get('enabled') is False
                    or row is None
                    or motor_axis is None
                    or motor_position is None
                ):
                    continue
                try:
                    motion_value = motion_value_from_motor(motor_position, row)
                    raw_fader_for_motion(motion_value, row, mapping)
                except ValueError:
                    continue
                self._motor_command_state[channel] = 'inactive'
                self._motor_command_message[channel] = ''
            for channel, motor_axis in enumerate(matched_axes):
                if motor_axis is None:
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
            try:
                motion_value = (
                    motion_value_from_output(final_output_value, row)
                    if row is not None else None
                )
                motor_target = (
                    motor_target_from_motion(motion_value, row)
                    if row is not None and motion_value is not None else None
                )
            except ValueError:
                motion_value = None
                motor_target = None
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
                'motion_axis_matched': motor_axis is not None,
                'matched_motor_axis': motor_axis,
                'display_motor_angle': motor_angle_mode[channel],
                'motor_angle_deg': None if motor_angle is None else round(motor_angle, 6),
                'motion_value_deg': None if motion_value is None else round(motion_value, 6),
                'motor_target_deg': None if motor_target is None else round(motor_target, 6),
                'fader_syncing': (
                    awaiting_fader_sync[channel] or bridge_fader_syncing[channel]
                ),
                'motor_command_state': motor_command_states[channel],
                'motor_command_message': motor_command_messages[channel],
            })
        return {
            'success': True,
            'node_state': 'ok',
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
            'motor_output_enabled': True,
            'motor_output_path': 'motion_supervisor',
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
            hardware_feedback = (*feedback, -1 if fader_position is None else fader_position)
            if self._last_feedback[index] == hardware_feedback:
                continue
            self._last_feedback[index] = hardware_feedback
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
        self._last_select_toggle_at = [0.0] * MIDI_CHANNEL_COUNT
        self._last_motor_command_at = [0.0] * MIDI_CHANNEL_COUNT
        self._last_motor_target = [None] * MIDI_CHANNEL_COUNT
        self._pending_motor_requests = {}
        self._motor_follow_active = [False] * MIDI_CHANNEL_COUNT
        self._motor_command_state = ['inactive'] * MIDI_CHANNEL_COUNT
        self._motor_command_message = [''] * MIDI_CHANNEL_COUNT
        self._motor_angle_mode = [False] * MIDI_CHANNEL_COUNT
        self._last_feedback = [None] * MIDI_CHANNEL_COUNT

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
            if command in {'save_mapping', 'update_bank'}:
                bank_id = payload.get('bank_id') or self._banks.snapshot()['active_bank_id']
                with self._lock:
                    self._banks.update_bank(
                        bank_id,
                        name=payload.get('name'),
                        mappings=payload.get('mappings'),
                    )
                    self._reset_filter_state_locked()
                    self._reset_runtime_controls_locked()
                    self._bank_file_dirty = True
                response = self._snapshot()
                response['message'] = 'MIDI 뱅크 설정을 노드에 임시 적용했습니다'
            elif command == 'create_bank':
                with self._lock:
                    bank = self._banks.create_bank(payload.get('name'), copy_from_active=True)
                    self._banks.select_bank(bank['bank_id'])
                    self._reset_filter_state_locked()
                    self._reset_runtime_controls_locked()
                    self._bank_file_dirty = True
                response = self._snapshot()
                response['message'] = 'MIDI 뱅크 추가 완료 (메모리 전용)'
            elif command == 'select_bank':
                with self._lock:
                    self._banks.select_bank(payload.get('bank_id'))
                    self._reset_filter_state_locked()
                    self._reset_runtime_controls_locked()
                    self._bank_file_dirty = True
                response = self._snapshot()
                response['message'] = 'MIDI 뱅크 전환 완료'
            elif command == 'delete_bank':
                with self._lock:
                    self._banks.delete_bank(payload.get('bank_id'))
                    self._reset_filter_state_locked()
                    self._reset_runtime_controls_locked()
                    self._bank_file_dirty = True
                response = self._snapshot()
                response['message'] = 'MIDI 뱅크 삭제 완료'
            elif command == 'save_banks_to_file':
                response = {
                    'success': False,
                    'message': '파일 저장은 motion_mapping_manager만 수행할 수 있습니다',
                }
            elif command in {'apply_banks', 'load_banks_from_file'}:
                mapping_file = self._requested_mapping_file(payload)
                stored_banks = payload.get('midi_banks')
                if stored_banks is None:
                    stored_banks = load_midi_banks(mapping_file)
                if stored_banks is None:
                    raise ValueError('모션축 설정 파일에 저장된 midi_banks가 없습니다')
                with self._lock:
                    self._banks.replace_state(stored_banks)
                    self._bank_config_file = mapping_file
                    self._reset_filter_state_locked()
                    self._reset_runtime_controls_locked()
                    self._bank_file_loaded = True
                    self._bank_file_dirty = False
                response = self._snapshot()
                response['message'] = '파일에서 검증된 MIDI 뱅크를 노드에 적용했습니다'
            elif command == 'reset_runtime_values':
                with self._lock:
                    self._reset_live_values_locked()
                response = self._snapshot()
                response['message'] = 'MIDI 실시간 값 초기화 완료 · 저장 파일은 변경하지 않았습니다'
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
