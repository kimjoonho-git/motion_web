import json
import math
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

import rclpy
import yaml
from motion_control_msgs.msg import MotorStatus
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy
from std_msgs.msg import Int8MultiArray, String


ID_CONTROLWORD = 0
ID_TARGET_POSITION = 1
CW_SHUTDOWN_MINAS = 0x0006
CW_SWITCH_ON_MINAS = 0x0007
CW_ENABLE_OPERATION_MINAS = 0x000F
CW_DISABLE_OPERATION_MINAS = 0x0007
CW_FAULT_RESET_MINAS = 0x0080
CW_NEW_SET_POINT_MINAS = 0x003F
DYNAMIXEL_TORQUE_ENABLE = 1
CONTROLWORD_SEQUENCE_DELAY_SEC = 0.05
JOG_TARGET_TOLERANCE_DEG = 0.05
JOG_DONE_VELOCITY_DEG_SEC = 0.05
JOG_ACTIVE_TIMEOUT_SEC = 120.0
DYNAMIXEL_TARGET_TOLERANCE_RAW_COUNTS = 2.0
DYNAMIXEL_DONE_VELOCITY_DEG_SEC = 2.0
DYNAMIXEL_JOG_ACTIVE_TIMEOUT_SEC = 8.0
DEFAULT_CONFIG_FILE = '/home/joonho_test/ros2_ws/config/active_motor_config.yaml'
DEFAULT_ACTION_PERIOD_SEC = 0.02
DEFAULT_ACTION_DURATION_SEC = 1.0
MIN_ACTION_DURATION_SEC = 0.02
CUBIC_SMOOTHSTEP_MAX_VELOCITY = 1.5
CUBIC_SMOOTHSTEP_MAX_ACCELERATION = 6.0
ACTION_RESULT_SETTLE_SEC = 2.0
DYNAMIXEL_ACTION_MIN_DEG = -180.0
DYNAMIXEL_ACTION_MAX_DEG = 180.0


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
        self._action_threads: Dict[int, threading.Thread] = {}
        self._motor_config_cache: Optional[Dict[str, Any]] = None

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
        self._result_pub = self.create_publisher(String, self.jog_result_topic, 10)
        self._action_result_pub = self.create_publisher(String, self.action_result_topic, 10)

        self.get_logger().info(
            f'motion_supervisor started: state={self.motion_state_topic}, '
            f'jog_request={self.jog_request_topic}, '
            f'action_request={self.action_request_topic}, '
            f'command={self.motor_command_topic}, '
            f'config_file={self.config_file}, '
            f'action_period={self.action_period_sec * 1000.0:.3f} ms'
        )

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

    def _jog_request_callback(self, msg: String) -> None:
        try:
            request = json.loads(msg.data)
        except json.JSONDecodeError:
            self._publish_result('', False, 'invalid jog request JSON')
            return

        request_id = str(request.get('request_id') or '')
        command = str(request.get('command') or 'ac_servo_jog')
        if command == 'ac_servo_control':
            success, message = self._handle_ac_servo_control(request)
        elif command == 'ac_servo_jog':
            success, message = self._handle_ac_servo_jog(request)
        elif command == 'dynamixel_jog':
            success, message = self._handle_dynamixel_jog(request)
        else:
            success, message = False, f'unknown manual command: {command}'
        self._publish_result(request_id, success, message)

    def _action_request_callback(self, msg: String) -> None:
        try:
            request = json.loads(msg.data)
        except json.JSONDecodeError:
            self._publish_action_result('', False, 'invalid action request JSON')
            return

        request_id = str(request.get('request_id') or '')
        command = str(request.get('command') or 'ac_servo_absolute_move')
        if command == 'ac_servo_absolute_move':
            success, message = self._handle_ac_servo_absolute_move(request)
        elif command == 'dynamixel_absolute_move':
            success, message = self._handle_dynamixel_absolute_move(request)
        else:
            success, message = False, f'unknown action command: {command}'
        self._publish_action_result(request_id, success, message)

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
        success, message = self._publish_position_target(
            motors,
            motor,
            axis,
            target_position,
            CW_NEW_SET_POINT_MINAS,
        )
        if not success:
            return False, message
        self._active_jogs[axis] = {
            'target_position': float(target_position),
            'started_at': time.time(),
        }
        return (
            True,
            f'AC Servo jog command sent: Axis {axis}, '
            f'{relative_deg:+.3f} deg, target {target_position:.3f} deg',
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
        if axis is None:
            return False, 'axis is required'
        if target_position is None:
            return False, 'target_deg is required'
        if requested_duration is not None and requested_duration <= 0:
            return False, 'duration_sec must be greater than 0'
        if target_position < DYNAMIXEL_ACTION_MIN_DEG or target_position > DYNAMIXEL_ACTION_MAX_DEG:
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
        self._command_pub.publish(command)

    def _current_motors(self) -> list[Dict[str, Any]]:
        if self._latest_state is None or self._latest_state_at is None:
            return []
        if time.time() - self._latest_state_at > self.state_timeout_sec:
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
        command.target_interface_id = [Int8MultiArray(data=[0] * size) for _ in range(size)]
        command.controller_index = list(range(size))
        command.controlword = [0] * size
        command.statusword = [0] * size
        command.errorcode = [0] * size
        command.position = [0.0] * size
        command.velocity = [0.0] * size
        command.effort = [0.0] * size
        return command

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
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
