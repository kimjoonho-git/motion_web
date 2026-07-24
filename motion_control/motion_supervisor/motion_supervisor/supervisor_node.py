import json
import math
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

import rclpy
import yaml
from motion_control_msgs.msg import MotorStatus
from rclpy.callback_groups import MutuallyExclusiveCallbackGroup
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
from std_msgs.msg import Int8MultiArray, String

from motion_supervisor.command_arbiter import CommandArbiter, CommandOwner


ID_CONTROLWORD = 0
ID_TARGET_POSITION = 1
CW_SHUTDOWN_MINAS = 0x0006
CW_SWITCH_ON_MINAS = 0x0007
CW_ENABLE_OPERATION_MINAS = 0x000F
CW_DISABLE_OPERATION_MINAS = 0x0007
CW_FAULT_RESET_MINAS = 0x0080
CW_NEW_SET_POINT_MINAS = 0x003F
DYNAMIXEL_TORQUE_ENABLE = 1
DYNAMIXEL_TORQUE_DISABLE = 0
CONTROLWORD_SEQUENCE_DELAY_SEC = 0.05
JOG_TARGET_TOLERANCE_DEG = 0.05
JOG_DONE_VELOCITY_DEG_SEC = 0.05
JOG_ACTIVE_TIMEOUT_SEC = 120.0
DYNAMIXEL_TARGET_TOLERANCE_RAW_COUNTS = 2.0
DYNAMIXEL_DONE_VELOCITY_DEG_SEC = 2.0
DYNAMIXEL_JOG_ACTIVE_TIMEOUT_SEC = 8.0
DEFAULT_CONFIG_FILE = str(
    Path(os.environ.get('MOTION_WORKSPACE', Path.cwd())).expanduser()
    / 'config'
    / 'bootstrap_motor_config.yaml'
)
DEFAULT_ACTION_PERIOD_SEC = 0.02
DEFAULT_ACTION_DURATION_SEC = 1.0
MIN_ACTION_DURATION_SEC = 0.02
CUBIC_SMOOTHSTEP_MAX_VELOCITY = 1.5
CUBIC_SMOOTHSTEP_MAX_ACCELERATION = 6.0
DEFAULT_JOG_MIN_DURATION_SEC = 0.15
DEFAULT_JOG_MAX_DURATION_SEC = 0.5
JOG_COMMAND_PERIOD_SEC = 0.04
DEFAULT_JOG_VELOCITY_DEG_SEC = 1125.0
DEFAULT_JOG_ACCELERATION_DEG_SEC2 = 9375.0
ACTION_RESULT_SETTLE_SEC = 2.0
DYNAMIXEL_ACTION_MIN_DEG = -180.0
DYNAMIXEL_ACTION_MAX_DEG = 180.0
MIDI_COMMAND_OWNERSHIP_SEC = 0.15
MOTION_RUN_ACTIVE_GRACE_SEC = 0.15
MANUAL_CONTROL_OWNERSHIP_SEC = 1.0

COMMAND_OWNER_LABELS = {
    CommandOwner.MANUAL: 'manual jog/action control',
    CommandOwner.MIDI: 'MIDI fader control',
    CommandOwner.PLAYBACK: 'motion playback',
}


def motion_run_rejection_reason(
    motor_state_available: bool,
    manual_command_active: bool,
    midi_command_active: bool = False,
    emergency_latched: bool = False,
) -> Optional[str]:
    """Return why a runtime command cannot own the final command output."""
    if emergency_latched:
        return 'emergency stop is latched; restart the full program'
    if not motor_state_available:
        return 'motor state is unavailable or stale'
    if manual_command_active:
        return 'a manual command is active'
    if midi_command_active:
        return 'MIDI fader control is active'
    return None


class MotionSupervisor(Node):
    """Final publisher for upper-level motion commands."""

    def __init__(self) -> None:
        super().__init__('motion_supervisor')

        self.motion_state_topic = self.declare_parameter(
            'motion_state_topic',
            '/motion_control/motion_state',
        ).value
        self.jog_request_topic = self.declare_parameter(
            'jog_request_topic',
            '/motion_control/manual_jog_request',
        ).value
        self.jog_result_topic = self.declare_parameter(
            'jog_result_topic',
            '/motion_control/manual_jog_result',
        ).value
        self.safety_request_topic = self.declare_parameter(
            'safety_request_topic',
            '/motion_control/safety_request',
        ).value
        self.action_request_topic = self.declare_parameter(
            'action_request_topic',
            '/motion_control/manual_action_request',
        ).value
        self.action_result_topic = self.declare_parameter(
            'action_result_topic',
            '/motion_control/manual_action_result',
        ).value
        self.motor_command_topic = self.declare_parameter(
            'motor_command_topic',
            '/motion_control/motor_command',
        ).value
        self.motion_run_command_topic = self.declare_parameter(
            'motion_run_command_topic',
            '/motion_control/motion_run_command',
        ).value
        self.midi_position_request_topic = self.declare_parameter(
            'midi_position_request_topic',
            '/motion_control/midi_position_request',
        ).value
        self.midi_position_result_topic = self.declare_parameter(
            'midi_position_result_topic',
            '/motion_control/midi_position_result',
        ).value
        self.safety_status_topic = self.declare_parameter(
            'safety_status_topic',
            '/motion_control/safety_status',
        ).value
        self.state_timeout_sec = float(
            self.declare_parameter('state_timeout_sec', 0.5).value
        )
        self.max_jog_delta_deg = float(
            self.declare_parameter('max_jog_delta_deg', 360.0).value
        )
        self.config_file = Path(
            str(self.declare_parameter('config_file', DEFAULT_CONFIG_FILE).value)
        ).expanduser()
        self.action_period_sec = self._load_action_period_sec()

        qos = QoSProfile(depth=1, reliability=ReliabilityPolicy.BEST_EFFORT)
        self._latest_state: Optional[Dict[str, Any]] = None
        self._latest_state_at: Optional[float] = None
        self._active_jogs: Dict[int, Dict[str, Any]] = {}
        self._active_actions: Dict[int, Dict[str, Any]] = {}
        self._jog_threads: Dict[int, threading.Thread] = {}
        self._action_threads: Dict[int, threading.Thread] = {}
        self._motor_config_cache: Optional[Dict[str, Any]] = None
        self._midi_active_until = 0.0
        self._last_motion_run_command_at = 0.0
        self._emergency_latched = False
        self._motion_stop_block_until = 0.0
        self._command_lock = threading.RLock()
        self._command_arbiter = CommandArbiter()
        self._project_generation = 0
        self._safety_callback_group = MutuallyExclusiveCallbackGroup()

        self._state_sub = self.create_subscription(
            String,
            self.motion_state_topic,
            self._motion_state_callback,
            10,
        )
        self._jog_sub = self.create_subscription(
            String,
            self.jog_request_topic,
            self._jog_request_callback,
            10,
        )
        self._safety_sub = self.create_subscription(
            String,
            self.safety_request_topic,
            self._safety_request_callback,
            10,
            callback_group=self._safety_callback_group,
        )
        self._action_sub = self.create_subscription(
            String,
            self.action_request_topic,
            self._action_request_callback,
            10,
        )
        self._command_pub = self.create_publisher(
            MotorStatus,
            self.motor_command_topic,
            qos,
        )
        self._motion_run_command_sub = self.create_subscription(
            MotorStatus,
            self.motion_run_command_topic,
            self._motion_run_command_callback,
            qos,
        )
        self._midi_position_sub = self.create_subscription(
            String,
            self.midi_position_request_topic,
            self._midi_position_request_callback,
            10,
        )
        self._result_pub = self.create_publisher(String, self.jog_result_topic, 10)
        self._action_result_pub = self.create_publisher(String, self.action_result_topic, 10)
        self._midi_position_result_pub = self.create_publisher(
            String, self.midi_position_result_topic, 10
        )
        safety_qos = QoSProfile(
            depth=1,
            reliability=ReliabilityPolicy.RELIABLE,
            durability=DurabilityPolicy.TRANSIENT_LOCAL,
        )
        self._safety_status_pub = self.create_publisher(
            String, self.safety_status_topic, safety_qos
        )
        self._safety_status_timer = self.create_timer(0.5, self._publish_safety_status)
        self._publish_safety_status()

        self.get_logger().info(
            f'motion_supervisor started: state={self.motion_state_topic}, '
            f'jog_request={self.jog_request_topic}, '
            f'action_request={self.action_request_topic}, '
            f'motion_run_command={self.motion_run_command_topic}, '
            f'midi_position_request={self.midi_position_request_topic}, '
            f'safety_status={self.safety_status_topic}, '
            f'command={self.motor_command_topic}, '
            f'config_file={self.config_file}, '
            f'action_period={self.action_period_sec * 1000.0:.3f} ms'
        )

    def _command_arbiter_instance(self) -> CommandArbiter:
        """Return the arbiter, including for lightweight unit-test instances."""
        arbiter = getattr(self, '_command_arbiter', None)
        if arbiter is None:
            arbiter = CommandArbiter()
            self._command_arbiter = arbiter
        return arbiter

    @staticmethod
    def _command_owner_label(owner: CommandOwner) -> str:
        return COMMAND_OWNER_LABELS.get(owner, 'another command source')

    def _acquire_command_owner(
        self,
        owner: CommandOwner,
        *,
        lease_sec: Optional[float] = None,
    ) -> tuple[bool, str]:
        acquired, current_owner = self._command_arbiter_instance().acquire(
            owner,
            lease_sec=lease_sec,
        )
        if acquired:
            return True, ''
        return False, f'{self._command_owner_label(current_owner)} is active'

    def _release_manual_owner_if_idle(self) -> None:
        if not self._active_jogs and not self._active_actions:
            self._command_arbiter_instance().release(CommandOwner.MANUAL)

    def _motion_state_callback(self, msg: String) -> None:
        try:
            state = json.loads(msg.data)
        except json.JSONDecodeError:
            self.get_logger().warn('Invalid motion_state JSON received.')
            return
        self._latest_state = state if isinstance(state, dict) else None
        self._latest_state_at = time.time()
        self._clear_completed_jogs()
        self._clear_completed_actions()
        self._release_manual_owner_if_idle()

    def _motion_run_command_callback(self, msg: MotorStatus) -> None:
        """Relay runtime commands through the sole final command publisher."""
        shape_error = self._motor_command_shape_error(msg)
        if shape_error:
            self.get_logger().error(
                f'Rejected malformed motion runtime command: {shape_error}.',
                throttle_duration_sec=1.0,
            )
            return
        reason = motion_run_rejection_reason(
            motor_state_available=bool(self._current_motors()),
            manual_command_active=bool(self._active_jogs or self._active_actions),
            midi_command_active=time.monotonic() < self._midi_active_until,
            emergency_latched=self._emergency_latched,
        )
        if reason is None and time.monotonic() < self._motion_stop_block_until:
            reason = 'motion stop is settling'
        if reason:
            self.get_logger().warning(
                f'Rejected motion runtime command because {reason}.',
                throttle_duration_sec=1.0,
            )
            return
        acquired, current_owner = self._command_arbiter_instance().acquire(
            CommandOwner.PLAYBACK,
            lease_sec=MOTION_RUN_ACTIVE_GRACE_SEC,
        )
        if not acquired:
            self.get_logger().warning(
                'Rejected motion runtime command because '
                f'{self._command_owner_label(current_owner)} is active.',
                throttle_duration_sec=1.0,
            )
            return
        with self._command_lock:
            if self._emergency_latched or time.monotonic() < self._motion_stop_block_until:
                return
            self._last_motion_run_command_at = time.monotonic()
            self._command_pub.publish(msg)

    def _midi_position_request_callback(self, msg: String) -> None:
        try:
            request = json.loads(msg.data)
        except json.JSONDecodeError:
            self._publish_midi_position_result({}, False, 'invalid MIDI position JSON')
            return
        if not isinstance(request, dict):
            self._publish_midi_position_result({}, False, 'invalid MIDI position request')
            return
        if not self._request_generation_is_current(request):
            self._publish_midi_position_result(
                request, False, '이전 프로젝트 세대의 MIDI 명령을 폐기했습니다'
            )
            return
        if self._emergency_latched:
            self._publish_midi_position_result(
                request, False, 'emergency stop is latched; restart the full program'
            )
            return
        if time.monotonic() < self._motion_stop_block_until:
            self._publish_midi_position_result(request, False, 'motion stop is settling')
            return
        hold_axes = request.get('hold_axes')
        if isinstance(hold_axes, list):
            success, message, results = self._handle_midi_hold_axes(
                hold_axes, self._optional_int(request.get('channel'))
            )
            self._publish_midi_position_result(
                request, success, message, results=results
            )
            return
        targets = request.get('targets')
        if isinstance(targets, list):
            atomic_channels = {
                channel
                for value in request.get('atomic_channels') or []
                if (channel := self._optional_int(value)) is not None
            }
            success, message, results = self._handle_midi_position_batch(
                targets, atomic_channels=atomic_channels
            )
            self._publish_midi_position_result(
                request, success, message, results=results
            )
        else:
            success, message = self._handle_midi_position_request(request)
            self._publish_midi_position_result(request, success, message)

    def _handle_midi_hold_axes(
        self, axes: list[Any], channel: Optional[int]
    ) -> tuple[bool, str, list[Dict[str, Any]]]:
        unique_axes = []
        for value in axes:
            axis = self._optional_int(value)
            if axis is not None and axis not in unique_axes:
                unique_axes.append(axis)
        if not unique_axes:
            return False, 'MIDI hold axis list is empty', []
        motors = self._current_motors()
        targets = []
        for axis in unique_axes:
            motor = self._motor_for_axis(axis, motors)
            position = self._optional_float(
                motor.get('position_deg', motor.get('position'))
            ) if motor is not None else None
            if position is None:
                results = [
                    self._midi_target_result({
                        'request_id': f'midi-hold-{candidate}',
                        'channel': channel,
                        'axis': candidate,
                        'operation': 'hold',
                    }, False, f'연동 축 전체 정지 실패: Axis {axis} 현재 위치 확인 불가')
                    for candidate in unique_axes
                ]
                return False, results[0]['message'], results
            targets.append({
                'request_id': f'midi-hold-{axis}',
                'channel': channel,
                'axis': axis,
                'target_deg': position,
                'operation': 'hold',
            })
        atomic_channels = {channel} if channel is not None else set()
        return self._handle_midi_position_batch(
            targets, atomic_channels=atomic_channels
        )

    def _handle_midi_position_batch(
        self,
        targets: list[Any],
        *,
        atomic_channels: set[int] | None = None,
    ) -> tuple[bool, str, list[Dict[str, Any]]]:
        requests = [target for target in targets if isinstance(target, dict)]
        if not requests:
            return False, 'MIDI target batch is empty', []

        now = time.monotonic()
        global_error = ''
        if now - self._last_motion_run_command_at < MOTION_RUN_ACTIVE_GRACE_SEC:
            global_error = 'motion playback is active'
        elif self._active_jogs or self._active_actions:
            global_error = 'a manual command is active'

        motors = self._current_motors()
        if not global_error and not motors:
            global_error = 'current motion_state is unavailable'
        if global_error:
            results = [self._midi_target_result(target, False, global_error) for target in requests]
            return False, global_error, results

        command = self._empty_motor_command(motors)
        results: list[Dict[str, Any]] = []
        commanded_axes: set[int] = set()
        success_count = 0
        atomic_channels = atomic_channels or set()

        for target in requests:
            axis = self._optional_int(target.get('axis'))
            target_position = self._optional_float(target.get('target_deg'))
            error = ''
            controlword = 0
            motor = self._motor_for_axis(axis, motors) if axis is not None else None
            if axis is None:
                error = 'axis is required'
            elif target_position is None:
                error = 'target_deg is required'
            elif axis in commanded_axes:
                error = f'Axis {axis} is duplicated in MIDI batch'
            elif motor is None:
                error = f'Axis {axis} not found in current motion_state'
            elif str(motor.get('state') or '') != 'detected':
                error = f'Axis {axis} is not detected'
            elif bool(motor.get('fault', False)):
                error = f'Axis {axis} has error'
            elif self._is_ac_servo(motor):
                if motor.get('servo_on') is not True:
                    error = f'Axis {axis} servo is OFF'
                elif (
                    target.get('operation') != 'hold'
                    and self._ac_servo_internal_limit_active(motor)
                ):
                    error = (
                        f'Axis {axis} internal limit is active; '
                        'check POT/NOT, emergency stop, torque limit, and software limit'
                    )
                else:
                    controlword = CW_NEW_SET_POINT_MINAS
            elif self._is_dynamixel(motor):
                controlword = DYNAMIXEL_TORQUE_ENABLE
            else:
                error = f'Axis {axis} motor type is unsupported for MIDI control'

            if (
                not error
                and target_position is not None
                and motor is not None
                and target.get('operation') != 'hold'
            ):
                error = self._target_position_limit_error(motor, target_position) or ''

            if error:
                results.append(self._midi_target_result(target, False, error))
                continue

            commanded_axes.add(axis)
            command.number_of_target_interfaces[axis] = 2
            command.target_interface_id[axis] = Int8MultiArray(
                data=[ID_CONTROLWORD, ID_TARGET_POSITION]
            )
            command.controlword[axis] = int(controlword)
            command.position[axis] = float(target_position)
            motion_deg = self._optional_float(target.get('motion_deg'))
            motion_text = '' if motion_deg is None else f', motion {motion_deg:.3f} deg'
            results.append(self._midi_target_result(
                target,
                True,
                f'MIDI target published: Axis {axis}{motion_text}, '
                f'motor {target_position:.3f} deg (arrival not verified)',
            ))
            success_count += 1

        for channel in atomic_channels:
            indexes = [
                index for index, target in enumerate(requests)
                if self._optional_int(target.get('channel')) == channel
            ]
            if not indexes or all(results[index]['success'] for index in indexes):
                continue
            first_error = next(
                results[index]['message']
                for index in indexes if not results[index]['success']
            )
            group_message = f'연동 축 전체 차단: {first_error}'
            for index in indexes:
                if not results[index]['success']:
                    results[index]['message'] = group_message
                    continue
                axis = self._optional_int(requests[index].get('axis'))
                if axis is not None:
                    command.number_of_target_interfaces[axis] = 0
                    command.target_interface_id[axis] = Int8MultiArray(data=[])
                    command.controlword[axis] = 0
                    command.position[axis] = 0.0
                    commanded_axes.discard(axis)
                results[index] = self._midi_target_result(
                    requests[index], False, group_message
                )
                success_count -= 1

        if success_count:
            shape_error = self._motor_command_shape_error(command)
            if shape_error:
                message = f'invalid motor command blocked: {shape_error}'
                return False, message, [
                    self._midi_target_result(target, False, message)
                    for target in requests
                ]
            acquired, owner_error = self._acquire_command_owner(
                CommandOwner.MIDI,
                lease_sec=MIDI_COMMAND_OWNERSHIP_SEC,
            )
            if not acquired:
                return False, owner_error, [
                    self._midi_target_result(target, False, owner_error)
                    for target in requests
                ]
            # All accepted axes are published atomically in one MotorStatus,
            # preventing depth-1 QoS from dropping an earlier per-axis command.
            with self._command_lock:
                if self._emergency_latched or time.monotonic() < self._motion_stop_block_until:
                    self._command_arbiter_instance().release(CommandOwner.MIDI)
                    message = 'motor command blocked by safety stop'
                    return False, message, [
                        self._midi_target_result(target, False, message)
                        for target in requests
                    ]
                self._command_pub.publish(command)
            # A SELECT-off hold releases MIDI ownership. The initialization
            # command which follows must be allowed through immediately.
            if any(target.get('operation') != 'hold' for target in requests):
                self._midi_active_until = now + MIDI_COMMAND_OWNERSHIP_SEC
            else:
                self._midi_active_until = 0.0
                self._command_arbiter_instance().release(CommandOwner.MIDI)

        all_success = success_count == len(requests)
        message = (
            f'MIDI batch sent: {success_count}/{len(requests)} axes'
            if success_count
            else 'MIDI batch rejected'
        )
        return all_success, message, results

    @staticmethod
    def _midi_target_result(
        request: Dict[str, Any], success: bool, message: str
    ) -> Dict[str, Any]:
        return {
            'request_id': str(request.get('request_id') or ''),
            'channel': request.get('channel'),
            'axis': request.get('axis'),
            'operation': request.get('operation'),
            'motion_id': request.get('motion_id'),
            'mapping_file_id': request.get('mapping_file_id'),
            'motion_deg': request.get('motion_deg'),
            'target_deg': request.get('target_deg'),
            'success': success,
            'message': message,
        }

    def _handle_midi_position_request(self, request: Dict[str, Any]) -> tuple[bool, str]:
        axis = self._optional_int(request.get('axis'))
        target_position = self._optional_float(request.get('target_deg'))
        if axis is None:
            return False, 'axis is required'
        if target_position is None:
            return False, 'target_deg is required'
        now = time.monotonic()
        if now - self._last_motion_run_command_at < MOTION_RUN_ACTIVE_GRACE_SEC:
            return False, 'motion playback is active'
        if self._active_jogs or self._active_actions:
            return False, 'a manual command is active'

        motors = self._current_motors()
        motor = self._motor_for_axis(axis, motors)
        if motor is None:
            return False, f'Axis {axis} not found in current motion_state'
        if str(motor.get('state') or '') != 'detected':
            return False, f'Axis {axis} is not detected'
        if bool(motor.get('fault', False)):
            return False, f'Axis {axis} has error'

        if self._is_ac_servo(motor):
            if motor.get('servo_on') is not True:
                return False, f'Axis {axis} servo is OFF'
            if self._ac_servo_internal_limit_active(motor):
                return False, (
                    f'Axis {axis} internal limit is active; '
                    'check POT/NOT, emergency stop, torque limit, and software limit'
                )
            controlword = CW_NEW_SET_POINT_MINAS
        elif self._is_dynamixel(motor):
            controlword = DYNAMIXEL_TORQUE_ENABLE
        else:
            return False, f'Axis {axis} motor type is unsupported for MIDI control'

        acquired, owner_error = self._acquire_command_owner(
            CommandOwner.MIDI,
            lease_sec=MIDI_COMMAND_OWNERSHIP_SEC,
        )
        if not acquired:
            return False, owner_error
        success, message = self._publish_position_target(
            motors, motor, axis, target_position, controlword
        )
        if success:
            self._midi_active_until = now + MIDI_COMMAND_OWNERSHIP_SEC
            motion_deg = self._optional_float(request.get('motion_deg'))
            motion_text = '' if motion_deg is None else f', motion {motion_deg:.3f} deg'
            return True, (
                f'MIDI target published: Axis {axis}{motion_text}, '
                f'motor {target_position:.3f} deg (arrival not verified)'
            )
        self._command_arbiter_instance().release(CommandOwner.MIDI)
        return False, message

    def _publish_midi_position_result(
        self,
        request: Dict[str, Any],
        success: bool,
        message: str,
        results: Optional[list[Dict[str, Any]]] = None,
    ) -> None:
        payload = {
            'request_id': str(request.get('request_id') or ''),
            'project_generation': request.get('project_generation'),
            'channel': request.get('channel'),
            'axis': request.get('axis'),
            'motion_id': request.get('motion_id'),
            'mapping_file_id': request.get('mapping_file_id'),
            'motion_deg': request.get('motion_deg'),
            'target_deg': request.get('target_deg'),
            'success': success,
            'message': message,
            'stamp': time.time(),
        }
        if results is not None:
            payload['results'] = results
        self._midi_position_result_pub.publish(
            String(data=json.dumps(payload, ensure_ascii=False))
        )
        if not success:
            self.get_logger().warning(message, throttle_duration_sec=1.0)

    def _jog_request_callback(self, msg: String) -> None:
        try:
            request = json.loads(msg.data)
        except json.JSONDecodeError:
            self._publish_result('', False, 'invalid jog request JSON')
            return

        request_id = str(request.get('request_id') or '')
        command = str(request.get('command') or 'ac_servo_jog')
        if command == 'safety_emergency_stop':
            success, message = self._handle_safety_stop(emergency=True)
        elif command == 'safety_motion_stop' and not self._emergency_latched:
            success, message = self._handle_safety_stop(emergency=False)
        elif not self._request_generation_is_current(request):
            success, message = False, '이전 프로젝트 세대의 수동 명령을 폐기했습니다'
        elif self._emergency_latched:
            success, message = False, 'emergency stop is latched; restart the full program'
        elif time.monotonic() < self._motion_stop_block_until:
            success, message = False, 'motion stop is settling'
        elif command == 'ac_servo_control' and (
            self._active_jogs or self._active_actions
        ):
            success, message = False, 'a manual command is active'
        elif command in ('ac_servo_control', 'ac_servo_jog', 'dynamixel_jog'):
            lease_sec = (
                MANUAL_CONTROL_OWNERSHIP_SEC
                if command == 'ac_servo_control'
                else None
            )
            success, message = self._acquire_command_owner(
                CommandOwner.MANUAL,
                lease_sec=lease_sec,
            )
            if success:
                if command == 'ac_servo_control':
                    try:
                        success, message = self._handle_ac_servo_control(request)
                    finally:
                        self._command_arbiter_instance().release(CommandOwner.MANUAL)
                elif command == 'ac_servo_jog':
                    success, message = self._handle_ac_servo_jog(request)
                else:
                    success, message = self._handle_dynamixel_jog(request)
                if not success:
                    self._release_manual_owner_if_idle()
        else:
            success, message = False, f'unknown manual command: {command}'
        self._publish_result(request_id, success, message)

    def _safety_request_callback(self, msg: String) -> None:
        """Handle safety commands on a callback group independent of normal control."""
        try:
            request = json.loads(msg.data)
        except json.JSONDecodeError:
            self._publish_result('', False, 'invalid safety request JSON')
            return
        if not isinstance(request, dict):
            self._publish_result('', False, 'invalid safety request')
            return
        request_id = str(request.get('request_id') or '')
        command = str(request.get('command') or '')
        if command == 'safety_emergency_stop':
            success, message = self._handle_safety_stop(emergency=True)
        elif command == 'safety_motion_stop' and not self._emergency_latched:
            success, message = self._handle_safety_stop(emergency=False)
        elif self._emergency_latched:
            success, message = False, 'emergency stop is latched; restart the full program'
        else:
            success, message = False, f'unknown safety command: {command}'
        self._publish_result(request_id, success, message)

    def _action_request_callback(self, msg: String) -> None:
        try:
            request = json.loads(msg.data)
        except json.JSONDecodeError:
            self._publish_action_result('', False, 'invalid action request JSON')
            return

        request_id = str(request.get('request_id') or '')
        command = str(request.get('command') or 'ac_servo_absolute_move')
        if command == 'project_generation_boundary':
            success, message = self._apply_project_generation_boundary(request)
        elif not self._request_generation_is_current(request):
            success, message = False, '이전 프로젝트 세대의 동작 명령을 폐기했습니다'
        elif self._emergency_latched:
            success, message = False, 'emergency stop is latched; restart the full program'
        elif time.monotonic() < self._motion_stop_block_until:
            success, message = False, 'motion stop is settling'
        elif command in ('ac_servo_absolute_move', 'dynamixel_absolute_move'):
            success, message = self._acquire_command_owner(CommandOwner.MANUAL)
            if success:
                if command == 'ac_servo_absolute_move':
                    success, message = self._handle_ac_servo_absolute_move(request)
                else:
                    success, message = self._handle_dynamixel_absolute_move(request)
                if not success:
                    self._release_manual_owner_if_idle()
        else:
            success, message = False, f'unknown action command: {command}'
        self._publish_action_result(request_id, success, message)

    def _apply_project_generation_boundary(
        self, request: Dict[str, Any]
    ) -> tuple[bool, str]:
        try:
            generation = int(request.get('project_generation'))
        except (TypeError, ValueError):
            return False, '프로젝트 세대 번호가 필요합니다'
        current = int(getattr(self, '_project_generation', 0) or 0)
        if generation < current:
            return False, '이전 프로젝트 세대 경계를 폐기했습니다'
        self._project_generation = generation
        if bool(getattr(self, '_emergency_latched', False)):
            return True, f'프로젝트 세대 {generation} 명령 경계 적용 · 긴급정지 유지'

        # A project/configuration restart is only a software command boundary.
        # Never turn it into a motor set-point: servo/torque enable already holds
        # the drive's current position and a sampled position may be stale or may
        # have changed after an encoder/multi-turn reset.
        cancelled_actions = []
        with self._command_lock:
            self._motion_stop_block_until = time.monotonic() + 0.5
            cancelled_actions = [
                str(item.get('request_id') or '')
                for item in self._active_actions.values()
                if isinstance(item, dict) and item.get('request_id')
            ]
            self._active_jogs.clear()
            self._active_actions.clear()
            self._midi_active_until = 0.0
            self._last_motion_run_command_at = 0.0
            self._command_arbiter_instance().revoke_all()

        for request_id in cancelled_actions:
            self._publish_action_result(
                request_id,
                False,
                'trajectory cancelled by project/configuration restart',
            )
        self._publish_safety_status()
        return True, f'프로젝트 세대 {generation} 명령 경계 적용'

    def _request_generation_is_current(self, request: Dict[str, Any]) -> bool:
        try:
            generation = int(request.get('project_generation'))
        except (TypeError, ValueError):
            generation = self._request_project_generation(request.get('request_id'))
        return generation >= 1 and generation == int(
            getattr(self, '_project_generation', 0) or 0
        )

    def _handle_ac_servo_jog(self, request: Dict[str, Any]) -> tuple[bool, str]:
        axis = self._optional_int(request.get('axis'))
        relative_deg = self._optional_float(request.get('relative_deg'))
        if axis is None:
            return False, 'axis is required'
        if relative_deg is None or math.isclose(relative_deg, 0.0, abs_tol=1e-9):
            return False, 'relative_deg is required'
        if abs(relative_deg) > self.max_jog_delta_deg:
            return False, f'jog delta exceeds limit: {self.max_jog_delta_deg:g} deg'

        motors = self._current_motors()
        motor = self._motor_for_axis(axis, motors)
        if motor is None:
            return False, f'Axis {axis} not found in motion_state'
        if not self._is_ac_servo(motor):
            return False, f'Axis {axis} is not AC Servo'
        if str(motor.get('state') or '') != 'detected':
            return False, f'Axis {axis} is not detected'
        if motor.get('servo_on') is not True:
            return False, f'Axis {axis} servo is OFF'
        if bool(motor.get('fault', False)):
            return False, f'Axis {axis} has error'

        current_position = self._optional_float(
            motor.get('position_deg', motor.get('position'))
        )
        if current_position is None:
            return False, f'Axis {axis} position is unavailable'

        self._clear_completed_jogs()
        if axis in self._active_jogs:
            active = self._active_jogs[axis]
            return (
                False,
                f'Axis {axis} previous jog is still running: '
                f'target {active["target_position"]:.3f} deg',
            )
        if axis in self._active_actions:
            active = self._active_actions[axis]
            return (
                False,
                f'Axis {axis} previous action is still running: '
                f'target {active["target_position"]:.3f} deg',
            )

        target_position = current_position + relative_deg
        limit_error = self._target_position_limit_error(motor, target_position)
        if limit_error:
            return False, limit_error

        jog_duration = self._correct_jog_duration_sec(
            motor,
            current_position,
            target_position,
        )
        command_period_sec = JOG_COMMAND_PERIOD_SEC
        steps = self._jog_step_count(jog_duration['applied_sec'])
        self._active_jogs[axis] = {
            'request_id': str(request.get('request_id') or ''),
            'target_position': float(target_position),
            'started_at': time.time(),
            'started_monotonic': time.monotonic(),
            'start_position': float(current_position),
            'applied_duration_sec': float(steps * command_period_sec),
            'command_period_sec': float(command_period_sec),
            'steps': float(steps),
            'last_step': 0.0,
            'motors': motors,
            'motor': motor,
        }
        thread = threading.Thread(
            target=self._run_ac_servo_jog_trajectory,
            args=(axis,),
            daemon=True,
        )
        self._jog_threads[axis] = thread
        thread.start()

        return (
            True,
            f'AC Servo smooth jog started: Axis {axis}, '
            f'{relative_deg:+.3f} deg, target {target_position:.3f} deg, '
            f'command interval {command_period_sec * 1000.0:.1f}ms, '
            f'steps {steps}, duration {steps * command_period_sec:.3f}s',
        )

    def _handle_dynamixel_jog(self, request: Dict[str, Any]) -> tuple[bool, str]:
        axis = self._optional_int(request.get('axis'))
        relative_deg = self._optional_float(request.get('relative_deg'))
        if axis is None:
            return False, 'axis is required'
        if relative_deg is None or math.isclose(relative_deg, 0.0, abs_tol=1e-9):
            return False, 'relative_deg is required'
        if abs(relative_deg) > self.max_jog_delta_deg:
            return False, f'jog delta exceeds limit: {self.max_jog_delta_deg:g} deg'

        motors = self._current_motors()
        motor = self._motor_for_axis(axis, motors)
        if motor is None:
            return False, f'Axis {axis} not found in motion_state'
        if not self._is_dynamixel(motor):
            return False, f'Axis {axis} is not Dynamixel'
        if str(motor.get('state') or '') != 'detected':
            return False, f'Axis {axis} is not detected'
        if bool(motor.get('fault', False)):
            return False, f'Axis {axis} has error'

        current_position = self._optional_float(
            motor.get('position_deg', motor.get('position'))
        )
        if current_position is None:
            return False, f'Axis {axis} position is unavailable'

        self._clear_completed_jogs()
        self._clear_completed_actions()
        if axis in self._active_jogs:
            active = self._active_jogs[axis]
            return (
                False,
                f'Axis {axis} previous jog is still running: '
                f'target {active["target_position"]:.3f} deg',
            )
        if axis in self._active_actions:
            active = self._active_actions[axis]
            return (
                False,
                f'Axis {axis} previous action is still running: '
                f'target {active["target_position"]:.3f} deg',
            )

        target_position = current_position + relative_deg
        success, message = self._publish_position_target(
            motors,
            motor,
            axis,
            target_position,
            DYNAMIXEL_TORQUE_ENABLE,
        )
        if not success:
            return False, message
        self._active_jogs[axis] = {
            'target_position': float(target_position),
            'started_at': time.time(),
            'active_timeout_sec': DYNAMIXEL_JOG_ACTIVE_TIMEOUT_SEC,
        }
        return (
            True,
            f'Dynamixel jog command sent: Axis {axis}, '
            f'{relative_deg:+.3f} deg, target {target_position:.3f} deg',
        )

    def _handle_ac_servo_absolute_move(self, request: Dict[str, Any]) -> tuple[bool, str]:
        request_id = str(request.get('request_id') or '')
        axis = self._optional_int(request.get('axis'))
        target_position = self._optional_float(request.get('target_deg'))
        requested_duration = self._optional_float(request.get('duration_sec'))
        if axis is None:
            return False, 'axis is required'
        if target_position is None:
            return False, 'target_deg is required'
        if requested_duration is not None and requested_duration <= 0:
            return False, 'duration_sec must be greater than 0'

        motors = self._current_motors()
        motor = self._motor_for_axis(axis, motors)
        if motor is None:
            return False, f'Axis {axis} not found in motion_state'
        if not self._is_ac_servo(motor):
            return False, f'Axis {axis} is not AC Servo'
        if str(motor.get('state') or '') != 'detected':
            return False, f'Axis {axis} is not detected'
        if motor.get('servo_on') is not True:
            return False, f'Axis {axis} servo is OFF'
        if bool(motor.get('fault', False)):
            return False, f'Axis {axis} has error'
        current_position = self._optional_float(
            motor.get('position_deg', motor.get('position'))
        )
        if current_position is None:
            return False, f'Axis {axis} position is unavailable'

        self._clear_completed_jogs()
        self._clear_completed_actions()
        if axis in self._active_jogs:
            active = self._active_jogs[axis]
            return (
                False,
                f'Axis {axis} previous jog is still running: '
                f'target {active["target_position"]:.3f} deg',
            )
        if axis in self._active_actions:
            active = self._active_actions[axis]
            return (
                False,
                f'Axis {axis} previous action is still running: '
                f'target {active["target_position"]:.3f} deg',
            )

        if request.get('range_recovery') is True:
            return self._start_range_recovery(
                motors,
                motor,
                axis,
                current_position,
                target_position,
                request_id,
                is_ac_servo=True,
            )

        corrected_duration = self._correct_action_duration_sec(
            motor,
            current_position,
            target_position,
            requested_duration,
        )
        command_period_sec = self._action_command_period_sec()
        steps = max(1, int(math.ceil(corrected_duration['applied_sec'] / command_period_sec)))
        self._active_actions[axis] = {
            'request_id': request_id,
            'target_position': float(target_position),
            'started_at': time.time(),
            'started_monotonic': time.monotonic(),
            'start_position': float(current_position),
            'applied_duration_sec': float(corrected_duration['applied_sec']),
            'requested_duration_sec': float(corrected_duration['requested_sec']),
            'command_period_sec': float(command_period_sec),
            'steps': float(steps),
            'last_step': 0.0,
            'motors': motors,
            'motor': motor,
            'label': 'AC Servo',
        }
        thread = threading.Thread(
            target=self._run_action_trajectory,
            args=(axis,),
            daemon=True,
        )
        self._action_threads[axis] = thread
        thread.start()

        limit_text = ''
        if corrected_duration['limited']:
            limit_text = (
                f', requested {corrected_duration["requested_sec"]:.3f}s '
                f'-> applied {corrected_duration["applied_sec"]:.3f}s by speed/acceleration limits'
            )
        else:
            limit_text = f', duration {corrected_duration["applied_sec"]:.3f}s'
        return (
            True,
            f'AC Servo absolute trajectory started: Axis {axis}, '
            f'current {current_position:.3f} deg, target {target_position:.3f} deg, '
            f'command interval {command_period_sec * 1000.0:.1f}ms, steps {steps}{limit_text}',
        )

    def _handle_dynamixel_absolute_move(self, request: Dict[str, Any]) -> tuple[bool, str]:
        request_id = str(request.get('request_id') or '')
        axis = self._optional_int(request.get('axis'))
        target_position = self._optional_float(request.get('target_deg'))
        requested_duration = self._optional_float(request.get('duration_sec'))
        range_recovery = request.get('range_recovery') is True
        if axis is None:
            return False, 'axis is required'
        if target_position is None:
            return False, 'target_deg is required'
        if requested_duration is not None and requested_duration <= 0:
            return False, 'duration_sec must be greater than 0'
        if (
            not range_recovery
            and (
                target_position < DYNAMIXEL_ACTION_MIN_DEG
                or target_position > DYNAMIXEL_ACTION_MAX_DEG
            )
        ):
            return (
                False,
                f'Dynamixel action target must be between '
                f'{DYNAMIXEL_ACTION_MIN_DEG:.3f} and {DYNAMIXEL_ACTION_MAX_DEG:.3f} deg',
            )

        motors = self._current_motors()
        motor = self._motor_for_axis(axis, motors)
        if motor is None:
            return False, f'Axis {axis} not found in motion_state'
        if not self._is_dynamixel(motor):
            return False, f'Axis {axis} is not Dynamixel'
        if str(motor.get('state') or '') != 'detected':
            return False, f'Axis {axis} is not detected'
        if bool(motor.get('fault', False)):
            return False, f'Axis {axis} has error'
        current_position = self._optional_float(
            motor.get('position_deg', motor.get('position'))
        )
        if current_position is None:
            return False, f'Axis {axis} position is unavailable'

        self._clear_completed_jogs()
        self._clear_completed_actions()
        if axis in self._active_jogs:
            active = self._active_jogs[axis]
            return (
                False,
                f'Axis {axis} previous jog is still running: '
                f'target {active["target_position"]:.3f} deg',
            )
        if axis in self._active_actions:
            active = self._active_actions[axis]
            return (
                False,
                f'Axis {axis} previous action is still running: '
                f'target {active["target_position"]:.3f} deg',
            )

        if range_recovery:
            return self._start_range_recovery(
                motors,
                motor,
                axis,
                current_position,
                target_position,
                request_id,
                is_ac_servo=False,
            )

        corrected_duration = self._correct_action_duration_sec(
            motor,
            current_position,
            target_position,
            requested_duration,
        )
        command_period_sec = self.action_period_sec
        steps = max(1, int(math.ceil(corrected_duration['applied_sec'] / command_period_sec)))
        self._active_actions[axis] = {
            'request_id': request_id,
            'target_position': float(target_position),
            'started_at': time.time(),
            'started_monotonic': time.monotonic(),
            'start_position': float(current_position),
            'applied_duration_sec': float(corrected_duration['applied_sec']),
            'requested_duration_sec': float(corrected_duration['requested_sec']),
            'command_period_sec': float(command_period_sec),
            'steps': float(steps),
            'last_step': 0.0,
            'motors': motors,
            'motor': motor,
            'label': 'Dynamixel',
        }
        thread = threading.Thread(
            target=self._run_dynamixel_action_trajectory,
            args=(axis,),
            daemon=True,
        )
        self._action_threads[axis] = thread
        thread.start()

        limit_text = ''
        if corrected_duration['limited']:
            limit_text = (
                f', requested {corrected_duration["requested_sec"]:.3f}s '
                f'-> applied {corrected_duration["applied_sec"]:.3f}s by speed/acceleration limits'
            )
        else:
            limit_text = f', duration {corrected_duration["applied_sec"]:.3f}s'
        return (
            True,
            f'Dynamixel absolute trajectory started: Axis {axis}, '
            f'current {current_position:.3f} deg, target {target_position:.3f} deg, '
            f'command interval {command_period_sec * 1000.0:.1f}ms, steps {steps}{limit_text}',
        )

    def _clear_completed_jogs(self) -> None:
        self._clear_completed_position_targets(self._active_jogs)

    def _clear_completed_actions(self) -> None:
        self._clear_completed_action_targets()

    def _run_ac_servo_jog_trajectory(self, axis: int) -> None:
        active = self._active_jogs.get(axis)
        if active is None:
            self._jog_threads.pop(axis, None)
            return

        steps = max(1, int(active.get('steps') or 1))
        active_start_position = self._optional_float(active.get('start_position'))
        active_target_position = self._optional_float(active.get('target_position'))
        start_position = 0.0 if active_start_position is None else active_start_position
        target_position = (
            start_position
            if active_target_position is None
            else active_target_position
        )
        command_period_sec = max(
            self._optional_float(active.get('command_period_sec'))
            or self._action_command_period_sec(),
            self.action_period_sec,
        )
        start_time = time.monotonic()

        for step in range(1, steps + 1):
            active = self._active_jogs.get(axis)
            if active is None:
                break

            u = min(max(step / steps, 0.0), 1.0)
            command_position = start_position + (
                (target_position - start_position) * self._cubic_smoothstep(u)
            )
            motors = active.get('motors')
            motor = active.get('motor')
            if not isinstance(motors, list) or not isinstance(motor, dict):
                self._active_jogs.pop(axis, None)
                self.get_logger().warning(
                    f'AC Servo smooth jog stopped: Axis {axis}, '
                    'motor state snapshot is unavailable'
                )
                break

            success, message = self._publish_ac_servo_action_setpoint(
                motors,
                motor,
                axis,
                command_position,
            )
            if not success:
                self._active_jogs.pop(axis, None)
                self.get_logger().warning(
                    f'AC Servo smooth jog stopped: Axis {axis}, {message}'
                )
                break

            active['last_step'] = float(step)
            next_time = start_time + (step * command_period_sec)
            sleep_sec = next_time - time.monotonic()
            if sleep_sec > 0.0:
                time.sleep(sleep_sec)

        active = self._active_jogs.get(axis)
        if active is not None and int(active.get('last_step') or 0) >= steps:
            active['commands_sent_at'] = time.time()
            self.get_logger().info(
                f'AC Servo smooth jog commands sent: Axis {axis}, '
                f'sent {steps}/{steps} steps, target {target_position:.3f} deg'
            )
        self._jog_threads.pop(axis, None)

    def _run_action_trajectory(self, axis: int) -> None:
        active = self._active_actions.get(axis)
        if active is None:
            return

        steps = max(1, int(active.get('steps') or 1))
        active_start_position = self._optional_float(active.get('start_position'))
        active_target_position = self._optional_float(active.get('target_position'))
        start_position = 0.0 if active_start_position is None else active_start_position
        target_position = (
            start_position
            if active_target_position is None
            else active_target_position
        )
        command_period_sec = max(
            self._optional_float(active.get('command_period_sec')) or self._action_command_period_sec(),
            self.action_period_sec,
        )
        start_time = time.monotonic()

        for step in range(1, steps + 1):
            active = self._active_actions.get(axis)
            if active is None:
                break

            u = min(max(step / steps, 0.0), 1.0)
            command_position = start_position + (
                (target_position - start_position) * self._cubic_smoothstep(u)
            )

            motors = active.get('motors')
            motor = active.get('motor')
            if not isinstance(motors, list) or not isinstance(motor, dict):
                current_motors = self._current_motors()
                current_motor = self._motor_for_axis(axis, current_motors)
                if current_motor is None:
                    self.get_logger().warn(
                        f'AC Servo trajectory waiting for Axis {axis} motion_state'
                    )
                    time.sleep(self.action_period_sec)
                    continue
                motors = current_motors
                motor = current_motor

            success, message = self._publish_ac_servo_action_setpoint(
                motors,
                motor,
                axis,
                command_position,
            )
            if not success:
                request_id = str(active.get('request_id') or '')
                self._active_actions.pop(axis, None)
                self._action_threads.pop(axis, None)
                self._publish_action_result(
                    request_id,
                    False,
                    f'AC Servo trajectory stopped: Axis {axis}, {message}',
                )
                return

            active['last_step'] = float(step)
            next_time = start_time + (step * command_period_sec)
            sleep_sec = next_time - time.monotonic()
            if sleep_sec > 0.0:
                time.sleep(sleep_sec)

        active = self._active_actions.get(axis)
        if active is not None:
            active['last_step'] = float(steps)
            active['commands_sent_at'] = time.time()
            self.get_logger().info(
                f'AC Servo trajectory commands sent: Axis {axis}, '
                f'sent {steps}/{steps} steps, target {target_position:.3f} deg'
            )
        self._action_threads.pop(axis, None)

    def _run_dynamixel_action_trajectory(self, axis: int) -> None:
        active = self._active_actions.get(axis)
        if active is None:
            return

        steps = max(1, int(active.get('steps') or 1))
        active_start_position = self._optional_float(active.get('start_position'))
        active_target_position = self._optional_float(active.get('target_position'))
        start_position = 0.0 if active_start_position is None else active_start_position
        target_position = (
            start_position
            if active_target_position is None
            else active_target_position
        )
        command_period_sec = max(
            self._optional_float(active.get('command_period_sec')) or self.action_period_sec,
            self.action_period_sec,
        )
        start_time = time.monotonic()

        for step in range(1, steps + 1):
            active = self._active_actions.get(axis)
            if active is None:
                break

            u = min(max(step / steps, 0.0), 1.0)
            command_position = start_position + (
                (target_position - start_position) * self._cubic_smoothstep(u)
            )

            motors = active.get('motors')
            motor = active.get('motor')
            if not isinstance(motors, list) or not isinstance(motor, dict):
                current_motors = self._current_motors()
                current_motor = self._motor_for_axis(axis, current_motors)
                if current_motor is None:
                    self.get_logger().warn(
                        f'Dynamixel trajectory waiting for Axis {axis} motion_state'
                    )
                    time.sleep(self.action_period_sec)
                    continue
                motors = current_motors
                motor = current_motor

            success, message = self._publish_position_target(
                motors,
                motor,
                axis,
                command_position,
                DYNAMIXEL_TORQUE_ENABLE,
            )
            if not success:
                request_id = str(active.get('request_id') or '')
                self._active_actions.pop(axis, None)
                self._action_threads.pop(axis, None)
                self._publish_action_result(
                    request_id,
                    False,
                    f'Dynamixel trajectory stopped: Axis {axis}, {message}',
                )
                return

            active['last_step'] = float(step)
            next_time = start_time + (step * command_period_sec)
            sleep_sec = next_time - time.monotonic()
            if sleep_sec > 0.0:
                time.sleep(sleep_sec)

        active = self._active_actions.get(axis)
        if active is not None:
            active['last_step'] = float(steps)
            active['commands_sent_at'] = time.time()
            self.get_logger().info(
                f'Dynamixel trajectory commands sent: Axis {axis}, '
                f'sent {steps}/{steps} steps, target {target_position:.3f} deg'
            )
        self._action_threads.pop(axis, None)

    def _clear_completed_position_targets(self, active_targets: Dict[int, Dict[str, Any]]) -> None:
        if not active_targets:
            return

        now = time.time()
        motors = self._current_motors()
        for axis, active in list(active_targets.items()):
            motor = self._motor_for_axis(axis, motors)
            if motor is None:
                if now - active['started_at'] > self.state_timeout_sec:
                    active_targets.pop(axis, None)
                continue

            position = self._optional_float(
                motor.get('position_deg', motor.get('position'))
            )
            velocity = self._optional_float(
                motor.get('velocity_deg_s', motor.get('velocity'))
            )
            target_position = active['target_position']
            target_tolerance = self._target_tolerance_deg(motor)
            done_velocity = self._done_velocity_deg_sec(motor)
            target_close = (
                position is not None
                and abs(position - target_position) <= target_tolerance
            )
            velocity_quiet = (
                velocity is None
                or abs(velocity) <= done_velocity
            )
            timeout_limit = max(
                float(active.get('active_timeout_sec') or JOG_ACTIVE_TIMEOUT_SEC),
                float(active.get('applied_duration_sec') or 0.0) + 5.0,
            )
            timed_out = now - active['started_at'] > timeout_limit

            if (target_close and velocity_quiet) or timed_out:
                active_targets.pop(axis, None)

    def _clear_completed_action_targets(self) -> None:
        if not self._active_actions:
            return

        now = time.time()
        motors = self._current_motors()
        for axis, active in list(self._active_actions.items()):
            steps = max(1, int(active.get('steps') or 1))
            last_step = int(active.get('last_step') or 0)
            request_id = str(active.get('request_id') or '')
            label = str(active.get('label') or 'Motion')
            timeout_limit = max(float(active.get('applied_duration_sec') or 0.0) + 10.0, 10.0)
            timed_out = now - active['started_at'] > timeout_limit
            commands_sent = last_step >= steps
            commands_sent_at = self._optional_float(active.get('commands_sent_at'))
            settle_timeout = (
                commands_sent
                and commands_sent_at is not None
                and now - commands_sent_at > max(ACTION_RESULT_SETTLE_SEC, self.action_period_sec * 3.0)
            )

            if last_step < steps and not timed_out:
                continue

            motor = self._motor_for_axis(axis, motors)
            if motor is None:
                if timed_out:
                    self._active_actions.pop(axis, None)
                continue

            position = self._optional_float(
                motor.get('position_deg', motor.get('position'))
            )
            velocity = self._optional_float(
                motor.get('velocity_deg_s', motor.get('velocity'))
            )
            target_position = active['target_position']
            target_tolerance = self._target_tolerance_deg(motor)
            done_velocity = self._done_velocity_deg_sec(motor)
            target_close = (
                position is not None
                and abs(position - target_position) <= target_tolerance
            )
            velocity_quiet = (
                velocity is None
                or abs(velocity) <= done_velocity
            )

            if target_close and velocity_quiet:
                self._active_actions.pop(axis, None)
                self._publish_action_result(
                    request_id,
                    True,
                    (
                        f'{label} trajectory completed: Axis {axis}, '
                        f'sent {last_step}/{steps} steps, '
                        f'target {target_position:.3f} deg'
                    ),
                )
                continue

            if (settle_timeout and velocity_quiet) or timed_out:
                self._active_actions.pop(axis, None)
                position_text = 'unavailable' if position is None else f'{position:.3f} deg'
                message = (
                    f'{label} trajectory did not reach target: Axis {axis}, '
                    f'current {position_text}, target {target_position:.3f} deg, '
                    f'sent {last_step}/{steps} steps'
                )
                self._publish_action_result(request_id, False, message)
                self.get_logger().warn(message)

    @staticmethod
    def _cubic_smoothstep(u: float) -> float:
        return (3.0 * u * u) - (2.0 * u * u * u)

    def _correct_action_duration_sec(
        self,
        motor: Dict[str, Any],
        current_position: float,
        target_position: float,
        requested_duration: Optional[float],
    ) -> Dict[str, Any]:
        requested = requested_duration
        if requested is None:
            requested = DEFAULT_ACTION_DURATION_SEC
        requested = max(float(requested), MIN_ACTION_DURATION_SEC, self.action_period_sec)

        distance = abs(target_position - current_position)
        applied = requested
        velocity_limit = self._velocity_limit_deg_sec(motor)
        acceleration_limit = self._acceleration_limit_deg_sec2(motor)

        if distance > 0.0 and velocity_limit is not None and velocity_limit > 0.0:
            applied = max(
                applied,
                CUBIC_SMOOTHSTEP_MAX_VELOCITY * distance / velocity_limit,
            )
        if distance > 0.0 and acceleration_limit is not None and acceleration_limit > 0.0:
            applied = max(
                applied,
                math.sqrt(CUBIC_SMOOTHSTEP_MAX_ACCELERATION * distance / acceleration_limit),
            )

        return {
            'requested_sec': requested,
            'applied_sec': applied,
            'limited': applied > requested + 1e-9,
            'velocity_limit_deg_sec': velocity_limit,
            'acceleration_limit_deg_sec2': acceleration_limit,
        }

    def _correct_jog_duration_sec(
        self,
        motor: Dict[str, Any],
        current_position: float,
        target_position: float,
    ) -> Dict[str, Any]:
        distance = abs(target_position - current_position)
        configured_velocity = self._velocity_limit_deg_sec(motor)
        configured_acceleration = self._acceleration_limit_deg_sec2(motor)
        applied = max(DEFAULT_JOG_MIN_DURATION_SEC, JOG_COMMAND_PERIOD_SEC)
        if distance > 0.0:
            nominal_duration = max(
                CUBIC_SMOOTHSTEP_MAX_VELOCITY
                * distance
                / DEFAULT_JOG_VELOCITY_DEG_SEC,
                math.sqrt(
                    CUBIC_SMOOTHSTEP_MAX_ACCELERATION
                    * distance
                    / DEFAULT_JOG_ACCELERATION_DEG_SEC2
                ),
            )
            applied = max(
                applied,
                min(nominal_duration, DEFAULT_JOG_MAX_DURATION_SEC),
            )
            if configured_velocity is not None:
                applied = max(
                    applied,
                    CUBIC_SMOOTHSTEP_MAX_VELOCITY
                    * distance
                    / configured_velocity,
                )
            if configured_acceleration is not None:
                applied = max(
                    applied,
                    math.sqrt(
                        CUBIC_SMOOTHSTEP_MAX_ACCELERATION
                        * distance
                        / configured_acceleration
                    ),
                )
        return {
            'applied_sec': applied,
            'velocity_limit_deg_sec': configured_velocity,
            'acceleration_limit_deg_sec2': configured_acceleration,
        }

    @staticmethod
    def _jog_step_count(duration_sec: float) -> int:
        return max(
            4,
            int(math.ceil((duration_sec - 1e-9) / JOG_COMMAND_PERIOD_SEC)),
        )

    def _velocity_limit_deg_sec(self, motor: Dict[str, Any]) -> Optional[float]:
        driver = self._driver_config_for_motor(motor)
        candidates = [
            self._optional_float((driver or {}).get('profile_velocity')),
        ]
        rated_speed_rpm = self._optional_float((driver or {}).get('rated_speed_rpm'))
        if rated_speed_rpm is None:
            rated_speed_rpm = self._optional_float(motor.get('rated_speed_rpm'))
        if rated_speed_rpm is not None:
            candidates.append(rated_speed_rpm * 6.0)

        speed = self._optional_float((driver or {}).get('speed'))
        if speed is not None and speed <= 1_000_000.0:
            candidates.append(speed)
        return self._min_positive(candidates)

    def _acceleration_limit_deg_sec2(self, motor: Dict[str, Any]) -> Optional[float]:
        driver = self._driver_config_for_motor(motor)
        candidates = [
            self._optional_float((driver or {}).get('profile_acceleration')),
            self._optional_float((driver or {}).get('profile_deceleration')),
            self._optional_float((driver or {}).get('acceleration')),
            self._optional_float((driver or {}).get('deceleration')),
        ]
        return self._min_positive(candidates)

    def _driver_config_for_motor(self, motor: Dict[str, Any]) -> Dict[str, Any]:
        axis = self._optional_int(motor.get('controller_index'))
        if axis is None:
            return {}

        config = self._load_motor_config()
        masters = config.get('masters', [])
        drivers = config.get('drivers', [])
        if not isinstance(masters, list) or not isinstance(drivers, list):
            return {}

        driver_by_id: Dict[int, Dict[str, Any]] = {}
        for driver in drivers:
            if not isinstance(driver, dict):
                continue
            driver_id = self._optional_int(driver.get('id'))
            if driver_id is not None:
                driver_by_id[driver_id] = driver

        for master in masters:
            if not isinstance(master, dict):
                continue
            slaves = master.get('slaves', [])
            if not isinstance(slaves, list):
                continue
            for slave in slaves:
                if not isinstance(slave, dict):
                    continue
                if self._optional_int(slave.get('controller_index')) != axis:
                    continue
                driver_id = self._optional_int(slave.get('driver_id'))
                if driver_id is None:
                    return {}
                return driver_by_id.get(driver_id, {})
        return {}

    def _load_motor_config(self) -> Dict[str, Any]:
        if self._motor_config_cache is not None:
            return self._motor_config_cache
        if not self.config_file.is_file():
            self.get_logger().warn(f'motor config file not found: {self.config_file}')
            self._motor_config_cache = {}
            return self._motor_config_cache
        try:
            data = yaml.safe_load(self.config_file.read_text(encoding='utf-8')) or {}
        except (OSError, yaml.YAMLError) as exc:
            self.get_logger().warn(f'Failed to read motor config file: {exc}')
            data = {}
        self._motor_config_cache = data if isinstance(data, dict) else {}
        return self._motor_config_cache

    def _load_action_period_sec(self) -> float:
        period = self._optional_float(
            self.declare_parameter('action_period_sec', DEFAULT_ACTION_PERIOD_SEC).value
        )
        if period is None or period <= 0:
            return DEFAULT_ACTION_PERIOD_SEC
        return max(period, 0.001)

    def _action_setpoint_clear_sec(self) -> float:
        return max(self.action_period_sec + 0.002, 0.002)

    def _action_command_period_sec(self) -> float:
        return self.action_period_sec + self._action_setpoint_clear_sec()

    @staticmethod
    def _min_positive(values: list[Optional[float]]) -> Optional[float]:
        positive = [
            value for value in values
            if value is not None and math.isfinite(value) and value > 0.0
        ]
        return min(positive) if positive else None

    def _target_position_limit_error(
        self,
        motor: Dict[str, Any],
        target_position: float,
    ) -> str:
        """Common limit guard for every upper-level position target."""
        axis = self._optional_int(motor.get('controller_index'))
        lower = self._optional_float(motor.get('lower'))
        upper = self._optional_float(motor.get('upper'))
        if lower is not None and target_position < lower:
            return (
                f'Axis {axis} position target {target_position:.3f} deg '
                f'is below lower limit {lower:.3f} deg'
            )
        if upper is not None and target_position > upper:
            return (
                f'Axis {axis} position target {target_position:.3f} deg '
                f'is above upper limit {upper:.3f} deg'
            )
        return ''

    def _range_recovery_target_error(
        self,
        motor: Dict[str, Any],
        current_position: float,
        target_position: float,
    ) -> str:
        axis = self._optional_int(motor.get('controller_index'))
        lower = self._optional_float(motor.get('lower'))
        upper = self._optional_float(motor.get('upper'))
        if lower is None or upper is None or lower > upper:
            return f'Axis {axis} has invalid position limits'

        expected_target: Optional[float] = None
        boundary_name = ''
        if current_position < lower:
            expected_target = lower
            boundary_name = 'lower'
        elif current_position > upper:
            expected_target = upper
            boundary_name = 'upper'
        else:
            return f'Axis {axis} is already within position limits'

        if not math.isclose(target_position, expected_target, abs_tol=1e-6):
            return (
                f'Axis {axis} range recovery must target the {boundary_name} '
                f'limit {expected_target:.3f} deg'
            )
        return ''

    def _start_range_recovery(
        self,
        motors: list[Dict[str, Any]],
        motor: Dict[str, Any],
        axis: int,
        current_position: float,
        target_position: float,
        request_id: str,
        *,
        is_ac_servo: bool,
    ) -> tuple[bool, str]:
        recovery_error = self._range_recovery_target_error(
            motor,
            current_position,
            target_position,
        )
        if recovery_error:
            return False, recovery_error

        if is_ac_servo:
            success, message = self._publish_ac_servo_action_setpoint(
                motors,
                motor,
                axis,
                target_position,
            )
            label = 'AC Servo range recovery'
        else:
            success, message = self._publish_position_target(
                motors,
                motor,
                axis,
                target_position,
                DYNAMIXEL_TORQUE_ENABLE,
            )
            label = 'Dynamixel range recovery'
        if not success:
            return False, message

        now = time.time()
        self._active_actions[axis] = {
            'request_id': request_id,
            'target_position': float(target_position),
            'started_at': now,
            'started_monotonic': time.monotonic(),
            'start_position': float(current_position),
            'applied_duration_sec': 0.0,
            'requested_duration_sec': 0.0,
            'command_period_sec': 0.0,
            'steps': 1.0,
            'last_step': 1.0,
            'commands_sent_at': now,
            'motors': motors,
            'motor': motor,
            'label': label,
        }
        return (
            True,
            f'{label} started: Axis {axis}, current {current_position:.3f} deg, '
            f'target {target_position:.3f} deg',
        )

    def _target_tolerance_deg(self, motor: Dict[str, Any]) -> float:
        if not self._is_dynamixel(motor):
            return JOG_TARGET_TOLERANCE_DEG

        raw_per_degree = self._optional_float(motor.get('position_raw_per_degree'))
        if raw_per_degree is None or raw_per_degree <= 0.0:
            pulse_per_revolution = self._optional_float(motor.get('pulse_per_revolution'))
            if pulse_per_revolution is not None and pulse_per_revolution > 0.0:
                raw_per_degree = pulse_per_revolution / 360.0

        if raw_per_degree is None or raw_per_degree <= 0.0:
            return max(JOG_TARGET_TOLERANCE_DEG, 0.2)
        return max(
            JOG_TARGET_TOLERANCE_DEG,
            DYNAMIXEL_TARGET_TOLERANCE_RAW_COUNTS / raw_per_degree,
        )

    def _done_velocity_deg_sec(self, motor: Dict[str, Any]) -> float:
        if self._is_dynamixel(motor):
            return DYNAMIXEL_DONE_VELOCITY_DEG_SEC
        return JOG_DONE_VELOCITY_DEG_SEC

    def _publish_position_target(
        self,
        motors: list[Dict[str, Any]],
        motor: Dict[str, Any],
        axis: int,
        target_position: float,
        controlword: int,
    ) -> tuple[bool, str]:
        limit_error = self._target_position_limit_error(motor, target_position)
        if limit_error:
            return False, limit_error

        command = self._empty_motor_command(motors)
        command.number_of_target_interfaces[axis] = 2
        command.target_interface_id[axis] = Int8MultiArray(
            data=[ID_CONTROLWORD, ID_TARGET_POSITION]
        )
        command.controlword[axis] = int(controlword)
        command.position[axis] = float(target_position)
        with self._command_lock:
            if self._emergency_latched:
                return False, 'emergency stop is latched; restart the full program'
            if time.monotonic() < self._motion_stop_block_until:
                return False, 'motion stop is settling'
            self._command_pub.publish(command)
        return True, 'position target command sent'

    def _publish_ac_servo_action_setpoint(
        self,
        motors: list[Dict[str, Any]],
        motor: Dict[str, Any],
        axis: int,
        target_position: float,
    ) -> tuple[bool, str]:
        limit_error = self._target_position_limit_error(motor, target_position)
        if limit_error:
            return False, limit_error

        self._publish_controlword(motors, [axis], CW_ENABLE_OPERATION_MINAS)
        time.sleep(self._action_setpoint_clear_sec())
        return self._publish_position_target(
            motors,
            motor,
            axis,
            target_position,
            CW_NEW_SET_POINT_MINAS,
        )

    def _handle_ac_servo_control(self, request: Dict[str, Any]) -> tuple[bool, str]:
        action = str(request.get('action') or '').strip().lower().replace('-', '_')
        if action not in ('servo_on', 'servo_off', 'fault_reset'):
            return False, 'action must be servo_on, servo_off, or fault_reset'

        motors = self._current_motors()
        if not motors:
            return False, 'current motion_state is unavailable'

        axes, error = self._ac_servo_control_axes(request, motors)
        if error:
            return False, error
        if not axes:
            return False, 'AC Servo axis not found'

        if action == 'servo_on':
            fault_axes = [
                axis for axis in axes
                if bool((self._motor_for_axis(axis, motors) or {}).get('fault', False))
            ]
            if fault_axes:
                return False, f'Axis {fault_axes[0]} has error; run Fault Reset first'
            self._publish_controlword(motors, axes, CW_SHUTDOWN_MINAS)
            time.sleep(CONTROLWORD_SEQUENCE_DELAY_SEC)
            self._publish_controlword(motors, axes, CW_SWITCH_ON_MINAS)
            time.sleep(CONTROLWORD_SEQUENCE_DELAY_SEC)
            self._publish_controlword(motors, axes, CW_ENABLE_OPERATION_MINAS)
            return True, f'AC Servo ON command sent: axes {self._axis_list_text(axes)}'

        if action == 'servo_off':
            self._publish_controlword(motors, axes, CW_DISABLE_OPERATION_MINAS)
            return True, f'AC Servo OFF command sent: axes {self._axis_list_text(axes)}'

        self._publish_controlword(motors, axes, CW_FAULT_RESET_MINAS)
        time.sleep(CONTROLWORD_SEQUENCE_DELAY_SEC)
        self._publish_controlword(motors, axes, CW_SHUTDOWN_MINAS)
        return True, f'AC Servo Fault Reset command sent: axes {self._axis_list_text(axes)}'

    def _ac_servo_control_axes(
        self,
        request: Dict[str, Any],
        motors: list[Dict[str, Any]],
    ) -> tuple[list[int], str]:
        scope = str(request.get('scope') or 'selected').strip().lower()
        if scope == 'all':
            axes = []
            for motor in motors:
                axis = self._optional_int(motor.get('controller_index'))
                if axis is None:
                    continue
                if not self._is_ac_servo(motor):
                    continue
                if str(motor.get('state') or '') != 'detected':
                    continue
                axes.append(axis)
            return sorted(set(axes)), ''

        if isinstance(request.get('axes'), list):
            requested_axes = [
                self._optional_int(axis)
                for axis in request.get('axes')
            ]
            requested_axes = [
                axis for axis in requested_axes
                if axis is not None
            ]
        else:
            axis = self._optional_int(request.get('axis'))
            requested_axes = [] if axis is None else [axis]

        if not requested_axes:
            return [], 'axis is required'

        axes = []
        for axis in requested_axes:
            motor = self._motor_for_axis(axis, motors)
            if motor is None:
                return [], f'Axis {axis} not found in motion_state'
            if not self._is_ac_servo(motor):
                return [], f'Axis {axis} is not AC Servo'
            if str(motor.get('state') or '') != 'detected':
                return [], f'Axis {axis} is not detected'
            axes.append(axis)
        return sorted(set(axes)), ''

    def _publish_controlword(
        self,
        motors: list[Dict[str, Any]],
        axes: list[int],
        controlword: int,
    ) -> None:
        command = self._empty_motor_command(motors)
        for axis in axes:
            if axis < 0 or axis >= len(command.number_of_target_interfaces):
                continue
            command.number_of_target_interfaces[axis] = 1
            command.target_interface_id[axis] = Int8MultiArray(data=[ID_CONTROLWORD])
            command.controlword[axis] = int(controlword)
        with self._command_lock:
            if self._emergency_latched:
                return
            if time.monotonic() < self._motion_stop_block_until:
                return
            self._command_pub.publish(command)

    def _handle_safety_stop(self, emergency: bool) -> tuple[bool, str]:
        """Cancel every upper-level command and publish one final safe command."""
        if self._emergency_latched and not emergency:
            return False, 'emergency stop is latched; restart the full program'
        cancelled_actions = []
        with self._command_lock:
            if emergency:
                self._emergency_latched = True
            self._motion_stop_block_until = (
                float('inf') if emergency else time.monotonic() + 0.5
            )
            cancelled_actions = [
                str(item.get('request_id') or '')
                for item in self._active_actions.values()
                if isinstance(item, dict) and item.get('request_id')
            ]
            self._active_jogs.clear()
            self._active_actions.clear()
            self._midi_active_until = 0.0
            self._last_motion_run_command_at = 0.0
            self._command_arbiter_instance().revoke_all()

            current_motors = self._current_motors()
            motors = current_motors
            if emergency and not motors:
                motors = self._last_known_motors()
            current_axes = {
                axis for axis in (
                    self._optional_int(motor.get('controller_index'))
                    for motor in current_motors
                )
                if axis is not None
            }
            command = self._empty_motor_command(motors)
            affected_axes = []
            for motor in motors:
                if str(motor.get('state') or '') != 'detected':
                    continue
                axis = self._optional_int(motor.get('controller_index'))
                if axis is None or axis < 0 or axis >= len(command.number_of_target_interfaces):
                    continue
                position = self._optional_float(
                    motor.get('position_deg', motor.get('position'))
                )
                if emergency and (
                    self._is_ac_servo(motor) or self._is_dynamixel(motor)
                ):
                    command.number_of_target_interfaces[axis] = 1
                    command.target_interface_id[axis] = Int8MultiArray(data=[ID_CONTROLWORD])
                    command.controlword[axis] = (
                        CW_DISABLE_OPERATION_MINAS
                        if self._is_ac_servo(motor)
                        else DYNAMIXEL_TORQUE_DISABLE
                    )
                    affected_axes.append(axis)
                    continue
                if axis not in current_axes:
                    # Never command a stale position merely to make a software stop.
                    continue
                if position is None:
                    continue
                command.number_of_target_interfaces[axis] = 2
                command.target_interface_id[axis] = Int8MultiArray(
                    data=[ID_CONTROLWORD, ID_TARGET_POSITION]
                )
                command.controlword[axis] = (
                    CW_NEW_SET_POINT_MINAS
                    if self._is_ac_servo(motor)
                    else DYNAMIXEL_TORQUE_ENABLE
                )
                command.position[axis] = float(position)
                affected_axes.append(axis)
            if affected_axes:
                # Safety command is intentionally the final command and may bypass
                # the latch/block that was set immediately above.
                self._command_pub.publish(command)

        for request_id in cancelled_actions:
            self._publish_action_result(
                request_id,
                False,
                'trajectory cancelled by emergency stop' if emergency
                else 'trajectory cancelled by motion stop',
            )
        self._publish_safety_status()
        mode = 'Emergency stop latched' if emergency else 'Motion stopped; servo remains enabled'
        axes_text = self._axis_list_text(sorted(set(affected_axes))) or 'none'
        return True, f'{mode}: axes {axes_text}'

    def _publish_safety_status(self) -> None:
        publisher = getattr(self, '_safety_status_pub', None)
        if publisher is None:
            return
        settling = (
            not self._emergency_latched
            and time.monotonic() < self._motion_stop_block_until
        )
        payload = {
            'emergency_latched': bool(self._emergency_latched),
            'motion_stop_settling': settling,
            'commands_blocked': bool(self._emergency_latched or settling),
            'command_owner': self._command_arbiter_instance().snapshot().owner.value,
            'message': (
                '긴급정지 잠김 · 상위 프로그램 재시작 필요'
                if self._emergency_latched
                else '전체 동작 정지 처리 중'
                if settling
                else '동작 가능'
            ),
            'stamp': time.time(),
        }
        publisher.publish(String(data=json.dumps(payload, ensure_ascii=False)))

    def _current_motors(self) -> list[Dict[str, Any]]:
        if self._latest_state is None or self._latest_state_at is None:
            return []
        if time.time() - self._latest_state_at > self.state_timeout_sec:
            return []
        motors = self._latest_state.get('motors', [])
        if not isinstance(motors, list):
            return []
        return [motor for motor in motors if isinstance(motor, dict)]

    def _last_known_motors(self) -> list[Dict[str, Any]]:
        if self._latest_state is None:
            return []
        motors = self._latest_state.get('motors', [])
        if not isinstance(motors, list):
            return []
        return [motor for motor in motors if isinstance(motor, dict)]

    def _motor_for_axis(
        self,
        axis: int,
        motors: Optional[list[Dict[str, Any]]] = None,
    ) -> Optional[Dict[str, Any]]:
        if motors is None:
            motors = self._current_motors()
        if not motors:
            return None
        for motor in motors:
            if self._optional_int(motor.get('controller_index')) == axis:
                return motor
        return None

    def _empty_motor_command(self, motors: list[Dict[str, Any]]) -> MotorStatus:
        axes = [
            axis for axis in (
                self._optional_int(motor.get('controller_index')) for motor in motors
            )
            if axis is not None and axis >= 0
        ]
        size = max(axes) + 1 if axes else 0

        command = MotorStatus()
        command.number_of_target_interfaces = [0] * size
        command.target_interface_id = [Int8MultiArray(data=[]) for _ in range(size)]
        command.controller_index = list(range(size))
        command.controlword = [0] * size
        command.statusword = [0] * size
        command.errorcode = [0] * size
        command.position = [0.0] * size
        command.velocity = [0.0] * size
        command.effort = [0.0] * size
        return command

    @staticmethod
    def _motor_command_shape_error(command: MotorStatus) -> Optional[str]:
        """Validate arrays before the C++ motor bridge indexes the message."""
        size = len(command.controller_index)
        parallel_fields = (
            'number_of_target_interfaces',
            'target_interface_id',
            'controlword',
            'statusword',
            'errorcode',
            'position',
            'velocity',
            'effort',
        )
        for field in parallel_fields:
            values = getattr(command, field, None)
            if values is None or len(values) < size:
                actual = 0 if values is None else len(values)
                return f'{field} length {actual} is smaller than {size}'
        indexes = [int(value) for value in command.controller_index]
        if len(set(indexes)) != len(indexes):
            return 'controller_index contains duplicates'
        if any(index < 0 for index in indexes):
            return 'controller_index contains a negative value'
        for slot in range(size):
            interface_count = int(command.number_of_target_interfaces[slot])
            interface_ids = command.target_interface_id[slot].data
            if len(interface_ids) < interface_count:
                return (
                    f'axis slot {slot} interface data length {len(interface_ids)} '
                    f'is smaller than {interface_count}'
                )
        return None

    @staticmethod
    def _is_ac_servo(motor: Dict[str, Any]) -> bool:
        values = [
            motor.get('motor_type'),
            motor.get('motor_type_label'),
            motor.get('driver_model'),
            motor.get('driver_name'),
            motor.get('transport'),
        ]
        text = ' '.join(str(value or '').lower() for value in values)
        return 'minas' in text or 'ac servo' in text or 'ac_servo' in text

    @staticmethod
    def _ac_servo_internal_limit_active(motor: Dict[str, Any]) -> bool:
        """Use the live CiA-402 Statusword, never cached UI/mapping state."""
        if motor.get('internal_limit_active') is not None:
            return bool(motor.get('internal_limit_active'))
        try:
            return bool(int(motor.get('statusword', 0)) & 0x0800)
        except (TypeError, ValueError):
            return False

    @staticmethod
    def _is_dynamixel(motor: Dict[str, Any]) -> bool:
        values = [
            motor.get('motor_type'),
            motor.get('motor_type_label'),
            motor.get('driver_model'),
            motor.get('driver_name'),
            motor.get('transport'),
        ]
        text = ' '.join(str(value or '').lower() for value in values)
        return 'dynamixel' in text or 'serial' in text and 'xm' in text

    def _publish_result(self, request_id: str, success: bool, message: str) -> None:
        payload = {
            'request_id': request_id,
            'project_generation': self._request_project_generation(request_id),
            'success': success,
            'message': message,
            'stamp': time.time(),
        }
        self._result_pub.publish(String(data=json.dumps(payload, ensure_ascii=False)))
        if success:
            self.get_logger().info(message)
        else:
            self.get_logger().warn(message)

    def _publish_action_result(self, request_id: str, success: bool, message: str) -> None:
        payload = {
            'request_id': request_id,
            'project_generation': self._request_project_generation(request_id),
            'success': success,
            'message': message,
            'stamp': time.time(),
        }
        self._action_result_pub.publish(String(data=json.dumps(payload, ensure_ascii=False)))
        if success:
            self.get_logger().info(message)
        else:
            self.get_logger().warn(message)

    @staticmethod
    def _request_project_generation(request_id: Any) -> int:
        match = re.search(r'-g(\d+)-', str(request_id or ''))
        return int(match.group(1)) if match else 0

    @staticmethod
    def _axis_list_text(axes: list[int]) -> str:
        return ', '.join(str(axis) for axis in axes)

    @staticmethod
    def _optional_int(value: Any) -> Optional[int]:
        try:
            if value is None or value == '':
                return None
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _optional_float(value: Any) -> Optional[float]:
        try:
            if value is None or value == '':
                return None
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if math.isfinite(number) else None


def main(args=None) -> None:
    rclpy.init(args=args)
    node = MotionSupervisor()
    executor = MultiThreadedExecutor(num_threads=2)
    executor.add_node(node)
    try:
        executor.spin()
    except KeyboardInterrupt:
        pass
    finally:
        executor.shutdown()
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
