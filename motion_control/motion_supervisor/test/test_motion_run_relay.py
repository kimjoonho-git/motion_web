import json
import threading

import pytest
from motion_control_msgs.msg import MotorStatus
from std_msgs.msg import Int8MultiArray, String

from motion_supervisor.command_arbiter import CommandArbiter, CommandOwner
from motion_supervisor.servo_alarm_guard import ServoAlarmGuard, policy_revision
from motion_supervisor.supervisor_node import MotionSupervisor, motion_run_rejection_reason


class CapturePublisher:
    def __init__(self):
        self.messages = []

    def publish(self, message):
        self.messages.append(message)


class QuietLogger:
    def info(self, *_args, **_kwargs):
        pass

    def warning(self, *_args, **_kwargs):
        pass

    def error(self, *_args, **_kwargs):
        pass


def test_runtime_command_is_allowed_when_state_is_current_and_manual_is_idle():
    assert motion_run_rejection_reason(True, False) is None


def test_runtime_command_is_rejected_without_current_motor_state():
    assert motion_run_rejection_reason(False, False) == (
        'motor state is unavailable or stale'
    )


def test_runtime_command_is_rejected_while_manual_command_is_active():
    assert motion_run_rejection_reason(True, True) == 'a manual command is active'


def test_runtime_command_is_rejected_while_midi_fader_owns_output():
    assert motion_run_rejection_reason(True, False, True) == (
        'MIDI fader control is active'
    )


def test_runtime_command_is_rejected_while_emergency_stop_is_latched():
    assert motion_run_rejection_reason(True, False, False, True) == (
        'emergency stop is latched; restart the full program'
    )


def test_ac_servo_jog_duration_uses_40ms_units_with_half_second_cap():
    supervisor = MotionSupervisor.__new__(MotionSupervisor)
    supervisor.action_period_sec = 0.02
    supervisor._velocity_limit_deg_sec = lambda _motor: 18000.0
    supervisor._acceleration_limit_deg_sec2 = lambda _motor: 180000.0

    corrected = supervisor._correct_jog_duration_sec({}, 0.0, 360.0)
    steps = supervisor._jog_step_count(corrected['applied_sec'])

    assert corrected['applied_sec'] == pytest.approx(0.48)
    assert steps == 12
    assert steps * 0.04 == pytest.approx(0.48)


def test_ac_servo_jog_exceeds_half_second_for_lower_motor_limits():
    supervisor = MotionSupervisor.__new__(MotionSupervisor)
    supervisor.action_period_sec = 0.02
    supervisor._velocity_limit_deg_sec = lambda _motor: 300.0
    supervisor._acceleration_limit_deg_sec2 = lambda _motor: 3000.0

    corrected = supervisor._correct_jog_duration_sec({}, 0.0, 360.0)
    steps = supervisor._jog_step_count(corrected['applied_sec'])

    assert corrected['applied_sec'] == pytest.approx(1.8)
    assert steps == 45
    assert steps * 0.04 == pytest.approx(1.8)


def test_range_recovery_accepts_only_the_violated_boundary():
    supervisor = MotionSupervisor.__new__(MotionSupervisor)
    motor = {
        'controller_index': 0,
        'lower': -1000.0,
        'upper': 1000.0,
    }

    assert supervisor._range_recovery_target_error(motor, -1200.0, -1000.0) == ''
    assert supervisor._range_recovery_target_error(motor, 1200.0, 1000.0) == ''
    assert 'must target the lower limit' in supervisor._range_recovery_target_error(
        motor,
        -1200.0,
        -900.0,
    )
    assert 'already within position limits' in supervisor._range_recovery_target_error(
        motor,
        0.0,
        -1000.0,
    )


def test_range_recovery_sends_one_in_range_target_without_intermediate_targets():
    supervisor = MotionSupervisor.__new__(MotionSupervisor)
    supervisor._active_actions = {}
    published = []
    supervisor._publish_ac_servo_action_setpoint = (
        lambda _motors, _motor, axis, target: (
            published.append((axis, target)) is None,
            '',
        )
    )
    motor = {
        'controller_index': 0,
        'lower': -1000.0,
        'upper': 1000.0,
    }

    success, message = supervisor._start_range_recovery(
        [motor],
        motor,
        0,
        -1200.0,
        -1000.0,
        'recovery-1',
        is_ac_servo=True,
    )

    assert success is True
    assert 'range recovery started' in message
    assert published == [(0, -1000.0)]
    assert supervisor._active_actions[0]['steps'] == 1.0
    assert supervisor._active_actions[0]['last_step'] == 1.0


def test_ac_servo_jog_trajectory_sends_cubic_intermediate_targets():
    supervisor = MotionSupervisor.__new__(MotionSupervisor)
    supervisor.action_period_sec = 0.001
    supervisor._jog_threads = {}
    motor = {'controller_index': 0}
    supervisor._active_jogs = {
        0: {
            'start_position': 0.0,
            'target_position': 100.0,
            'steps': 5.0,
            'command_period_sec': 0.001,
            'motors': [motor],
            'motor': motor,
            'last_step': 0.0,
        },
    }
    positions = []
    supervisor._publish_ac_servo_action_setpoint = (
        lambda _motors, _motor, _axis, target: (
            positions.append(target) is None,
            '',
        )
    )
    supervisor.get_logger = lambda: QuietLogger()

    supervisor._run_ac_servo_jog_trajectory(0)

    assert positions == pytest.approx([10.4, 35.2, 64.8, 89.6, 100.0])
    assert supervisor._active_jogs[0]['last_step'] == 5.0


def test_midi_result_returns_the_exact_supervisor_approved_command_values():
    result = MotionSupervisor._midi_target_result({
        'request_id': 'midi-0-1',
        'channel': 0,
        'axis': 2,
        'motion_id': '3-1',
        'mapping_file_id': 'mapping.yaml',
        'motion_deg': 170.0,
        'target_deg': 180.0,
    }, True, 'accepted')

    assert result['success'] is True
    assert result['motion_id'] == '3-1'
    assert result['mapping_file_id'] == 'mapping.yaml'
    assert result['motion_deg'] == 170.0
    assert result['target_deg'] == 180.0


def test_midi_batch_publishes_multiple_axes_in_one_motor_status():
    supervisor = MotionSupervisor.__new__(MotionSupervisor)
    supervisor._last_motion_run_command_at = 0.0
    supervisor._active_jogs = {}
    supervisor._active_actions = {}
    supervisor._midi_active_until = 0.0
    supervisor._emergency_latched = False
    supervisor._motion_stop_block_until = 0.0
    supervisor._command_lock = threading.RLock()
    supervisor._command_pub = CapturePublisher()
    motors = [
        {
            'controller_index': 1,
            'state': 'detected',
            'fault': False,
            'motor_type': 'dynamixel',
            'lower': -180.0,
            'upper': 180.0,
        },
        {
            'controller_index': 3,
            'state': 'detected',
            'fault': False,
            'motor_type': 'dynamixel',
            'lower': -180.0,
            'upper': 180.0,
        },
    ]
    supervisor._current_motors = lambda: motors

    success, _, results = supervisor._handle_midi_position_batch([
        {'request_id': 'one', 'channel': 1, 'axis': 1, 'target_deg': 10.0},
        {'request_id': 'two', 'channel': 3, 'axis': 3, 'target_deg': -20.0},
    ])

    assert success is True
    assert all(result['success'] for result in results)
    assert len(supervisor._command_pub.messages) == 1
    command = supervisor._command_pub.messages[0]
    assert command.number_of_target_interfaces[1] == 2
    assert command.number_of_target_interfaces[3] == 2
    assert command.position[1] == 10.0
    assert command.position[3] == -20.0


def test_linked_midi_group_blocks_every_axis_when_one_target_is_invalid():
    supervisor = MotionSupervisor.__new__(MotionSupervisor)
    supervisor._last_motion_run_command_at = 0.0
    supervisor._active_jogs = {}
    supervisor._active_actions = {}
    supervisor._midi_active_until = 0.0
    supervisor._emergency_latched = False
    supervisor._motion_stop_block_until = 0.0
    supervisor._command_lock = threading.RLock()
    supervisor._command_pub = CapturePublisher()
    supervisor._current_motors = lambda: [{
        'controller_index': 1,
        'state': 'detected',
        'fault': False,
        'motor_type': 'dynamixel',
        'lower': -180.0,
        'upper': 180.0,
    }]

    success, _, results = supervisor._handle_midi_position_batch(
        [
            {'request_id': 'one', 'channel': 0, 'axis': 1, 'target_deg': 10.0},
            {'request_id': 'two', 'channel': 0, 'axis': 9, 'target_deg': 10.0},
        ],
        atomic_channels={0},
    )

    assert success is False
    assert not any(result['success'] for result in results)
    assert all('연동 축 전체 차단' in result['message'] for result in results)
    assert supervisor._command_pub.messages == []


def test_midi_select_off_holds_linked_axes_at_their_current_positions():
    supervisor = MotionSupervisor.__new__(MotionSupervisor)
    supervisor._last_motion_run_command_at = 0.0
    supervisor._active_jogs = {}
    supervisor._active_actions = {}
    supervisor._midi_active_until = 0.0
    supervisor._emergency_latched = False
    supervisor._motion_stop_block_until = 0.0
    supervisor._command_lock = threading.RLock()
    supervisor._command_pub = CapturePublisher()
    motors = [
        {
            'controller_index': 1,
            'state': 'detected',
            'fault': False,
            'motor_type': 'dynamixel',
            'position_deg': 12.5,
            'lower': -180.0,
            'upper': 180.0,
        },
        {
            'controller_index': 3,
            'state': 'detected',
            'fault': False,
            'motor_type': 'dynamixel',
            'position_deg': -7.0,
            'lower': -180.0,
            'upper': 180.0,
        },
    ]
    supervisor._current_motors = lambda: motors

    success, _, results = supervisor._handle_midi_hold_axes([1, 3], 0)

    assert success is True
    assert all(result['operation'] == 'hold' for result in results)
    assert len(supervisor._command_pub.messages) == 1
    command = supervisor._command_pub.messages[0]
    assert command.position[1] == 12.5
    assert command.position[3] == -7.0
    assert supervisor._midi_active_until == 0.0


def test_normal_midi_position_keeps_short_command_ownership():
    supervisor = MotionSupervisor.__new__(MotionSupervisor)
    supervisor._last_motion_run_command_at = 0.0
    supervisor._active_jogs = {}
    supervisor._active_actions = {}
    supervisor._midi_active_until = 0.0
    supervisor._emergency_latched = False
    supervisor._motion_stop_block_until = 0.0
    supervisor._command_lock = threading.RLock()
    supervisor._command_pub = CapturePublisher()
    supervisor._current_motors = lambda: [{
        'controller_index': 0,
        'state': 'detected',
        'fault': False,
        'motor_type': 'dynamixel',
        'lower': -180.0,
        'upper': 180.0,
    }]

    success, _, _ = supervisor._handle_midi_position_batch([
        {'request_id': 'move', 'channel': 0, 'axis': 0, 'target_deg': 10.0},
    ])

    assert success is True
    assert supervisor._midi_active_until > 0.0


def test_midi_blocks_every_ac_axis_with_live_internal_limit_status():
    supervisor = MotionSupervisor.__new__(MotionSupervisor)
    supervisor._last_motion_run_command_at = 0.0
    supervisor._active_jogs = {}
    supervisor._active_actions = {}
    supervisor._midi_active_until = 0.0
    supervisor._emergency_latched = False
    supervisor._motion_stop_block_until = 0.0
    supervisor._command_lock = threading.RLock()
    supervisor._command_pub = CapturePublisher()
    supervisor._current_motors = lambda: [{
        'controller_index': 1,
        'state': 'detected',
        'fault': False,
        'motor_type': 'minas',
        'servo_on': True,
        'statusword': 0x0E37,
        'lower': -36000.0,
        'upper': 36000.0,
    }]

    success, _, results = supervisor._handle_midi_position_batch([{
        'request_id': 'limited', 'channel': 5, 'axis': 1, 'target_deg': 0.5,
    }])

    assert success is False
    assert results[0]['success'] is False
    assert 'internal limit is active' in results[0]['message']
    assert supervisor._command_pub.messages == []


def test_playback_owner_blocks_midi_even_without_legacy_grace_flag():
    supervisor = MotionSupervisor.__new__(MotionSupervisor)
    supervisor._last_motion_run_command_at = 0.0
    supervisor._active_jogs = {}
    supervisor._active_actions = {}
    supervisor._midi_active_until = 0.0
    supervisor._emergency_latched = False
    supervisor._motion_stop_block_until = 0.0
    supervisor._command_lock = threading.RLock()
    supervisor._command_arbiter = CommandArbiter()
    supervisor._project_generation = 1
    supervisor._command_arbiter.acquire(CommandOwner.PLAYBACK, lease_sec=1.0)
    supervisor._command_pub = CapturePublisher()
    supervisor._current_motors = lambda: [{
        'controller_index': 0,
        'state': 'detected',
        'fault': False,
        'motor_type': 'dynamixel',
        'lower': -180.0,
        'upper': 180.0,
    }]

    success, message, results = supervisor._handle_midi_position_batch([
        {'request_id': 'move', 'channel': 0, 'axis': 0, 'target_deg': 10.0},
    ])

    assert success is False
    assert message == 'motion playback is active'
    assert results[0]['success'] is False
    assert supervisor._command_pub.messages == []


def test_busy_playback_owner_rejects_manual_jog_before_handler_runs():
    supervisor = MotionSupervisor.__new__(MotionSupervisor)
    supervisor._emergency_latched = False
    supervisor._motion_stop_block_until = 0.0
    supervisor._active_jogs = {}
    supervisor._active_actions = {}
    supervisor._command_arbiter = CommandArbiter()
    supervisor._project_generation = 1
    supervisor._command_arbiter.acquire(CommandOwner.PLAYBACK, lease_sec=1.0)
    results = []
    supervisor._publish_result = lambda request_id, success, message: results.append(
        (request_id, success, message)
    )
    supervisor._handle_ac_servo_jog = lambda _request: (_ for _ in ()).throw(
        AssertionError('busy manual handler must not run')
    )

    supervisor._jog_request_callback(String(data=json.dumps({
        'request_id': 'jog-1',
        'project_generation': 1,
        'command': 'ac_servo_jog',
        'axis': 0,
        'relative_deg': 1.0,
    })))

    assert results == [('jog-1', False, 'motion playback is active')]


def test_runtime_callback_acquires_playback_owner_before_final_publish():
    supervisor = MotionSupervisor.__new__(MotionSupervisor)
    supervisor._active_jogs = {}
    supervisor._active_actions = {}
    supervisor._midi_active_until = 0.0
    supervisor._last_motion_run_command_at = 0.0
    supervisor._emergency_latched = False
    supervisor._motion_stop_block_until = 0.0
    supervisor._command_lock = threading.RLock()
    supervisor._command_arbiter = CommandArbiter()
    supervisor._project_generation = 1
    supervisor._command_pub = CapturePublisher()
    supervisor.get_logger = lambda: QuietLogger()
    motors = [{'controller_index': 0}]
    supervisor._current_motors = lambda: motors
    command = supervisor._empty_motor_command(motors)
    command.number_of_target_interfaces[0] = 2
    command.target_interface_id[0] = Int8MultiArray(data=[0, 1])
    command.controlword[0] = 1
    command.position[0] = 5.0

    supervisor._motion_run_command_callback(command)

    assert supervisor._command_pub.messages == [command]
    assert supervisor._command_arbiter.snapshot().owner is CommandOwner.PLAYBACK


@pytest.mark.parametrize(
    ('blocked_axes', 'controller_indexes', 'expected_interfaces'),
    [
        ({0}, [1], [2]),
        ({1}, [1], [0]),
        ({2}, [0, 2, 5], [2, 0, 2]),
        ({0, 5}, [2, 5], [2, 0]),
    ],
)
def test_grade1_playback_filter_uses_controller_index_not_array_slot(
    blocked_axes,
    controller_indexes,
    expected_interfaces,
):
    supervisor = MotionSupervisor.__new__(MotionSupervisor)
    supervisor._active_jogs = {}
    supervisor._active_actions = {}
    supervisor._midi_active_until = 0.0
    supervisor._last_motion_run_command_at = 0.0
    supervisor._emergency_latched = False
    supervisor._servo_alarm_guard = ServoAlarmGuard()
    grades = {'16': 1}
    success, _ = supervisor._servo_alarm_guard.apply_policy(
        grades,
        project_id='test',
        catalog_version=1,
        revision=policy_revision(grades, 1),
    )
    assert success is True
    supervisor._servo_alarm_guard.evaluate(
        [
            {
                'controller_index': axis,
                'motor_type': 'ac_servo',
                'errorcode': 16,
                'errorcode_raw': 16,
                'fault': True,
            }
            for axis in blocked_axes
        ],
        is_ac_servo=MotionSupervisor._is_ac_servo,
        axis_value=MotionSupervisor._optional_int,
        playback_active=False,
    )
    supervisor._motion_stop_block_until = 0.0
    supervisor._command_lock = threading.RLock()
    supervisor._command_arbiter = CommandArbiter()
    supervisor._project_generation = 1
    supervisor._command_pub = CapturePublisher()
    supervisor.get_logger = lambda: QuietLogger()
    supervisor._current_motors = lambda: [
        {'controller_index': axis} for axis in controller_indexes
    ]
    command = MotorStatus()
    size = len(controller_indexes)
    command.controller_index = list(controller_indexes)
    command.number_of_target_interfaces = [2] * size
    command.target_interface_id = [
        Int8MultiArray(data=[0, 1]) for _ in range(size)
    ]
    command.controlword = [1] * size
    command.statusword = [0] * size
    command.errorcode = [0] * size
    command.position = [5.0] * size
    command.velocity = [0.0] * size
    command.effort = [0.0] * size

    supervisor._motion_run_command_callback(command)

    published = supervisor._command_pub.messages[-1]
    assert list(published.number_of_target_interfaces) == expected_interfaces
    for slot, interface_count in enumerate(expected_interfaces):
        assert list(published.target_interface_id[slot].data) == (
            [0, 1] if interface_count else []
        )


def test_failed_manual_request_releases_ownership():
    supervisor = MotionSupervisor.__new__(MotionSupervisor)
    supervisor._emergency_latched = False
    supervisor._motion_stop_block_until = 0.0
    supervisor._active_jogs = {}
    supervisor._active_actions = {}
    supervisor._command_arbiter = CommandArbiter()
    supervisor._publish_result = lambda *_args: None
    supervisor._handle_ac_servo_jog = lambda _request: (False, 'invalid jog')

    supervisor._jog_request_callback(String(data=json.dumps({
        'request_id': 'jog-invalid',
        'command': 'ac_servo_jog',
    })))

    assert supervisor._command_arbiter.snapshot().owner is CommandOwner.NONE


def test_servo_control_does_not_release_an_active_manual_trajectory_owner():
    supervisor = MotionSupervisor.__new__(MotionSupervisor)
    supervisor._emergency_latched = False
    supervisor._motion_stop_block_until = 0.0
    supervisor._active_jogs = {0: {'target_position': 10.0}}
    supervisor._active_actions = {}
    supervisor._command_arbiter = CommandArbiter()
    supervisor._project_generation = 1
    supervisor._command_arbiter.acquire(CommandOwner.MANUAL)
    results = []
    supervisor._publish_result = lambda request_id, success, message: results.append(
        (request_id, success, message)
    )
    supervisor._handle_ac_servo_control = lambda _request: (_ for _ in ()).throw(
        AssertionError('servo control must not run over an active trajectory')
    )

    supervisor._jog_request_callback(String(data=json.dumps({
        'request_id': 'servo-off-during-jog',
        'project_generation': 1,
        'command': 'ac_servo_control',
        'action': 'servo_off',
        'axis': 0,
    })))

    assert results == [(
        'servo-off-during-jog', False, 'a manual command is active'
    )]
    assert supervisor._command_arbiter.snapshot().owner is CommandOwner.MANUAL


def test_project_boundary_rejects_old_commands_and_cancels_current_generation():
    supervisor = MotionSupervisor.__new__(MotionSupervisor)
    supervisor._project_generation = 4
    supervisor._emergency_latched = False
    supervisor._motion_stop_block_until = 0.0
    supervisor._active_jogs = {0: {'target_position': 1.0}}
    supervisor._active_actions = {}
    supervisor._midi_active_until = 1.0
    supervisor._last_motion_run_command_at = 1.0
    supervisor._command_lock = threading.RLock()
    supervisor._command_arbiter = CommandArbiter()
    supervisor._command_arbiter.acquire(CommandOwner.MANUAL)
    supervisor._latest_state = None
    supervisor._latest_state_at = None
    supervisor._command_pub = CapturePublisher()
    supervisor._publish_safety_status = lambda: None

    success, _ = supervisor._apply_project_generation_boundary({
        'project_generation': 5,
    })

    assert success is True
    assert supervisor._project_generation == 5
    assert supervisor._active_jogs == {}
    assert supervisor._command_arbiter.snapshot().owner is CommandOwner.NONE
    assert supervisor._command_pub.messages == []
    assert supervisor._request_generation_is_current({'project_generation': 4}) is False
    assert supervisor._request_generation_is_current({'project_generation': 5}) is True


def test_ac_servo_on_off_commands_never_include_target_position():
    supervisor = MotionSupervisor.__new__(MotionSupervisor)
    supervisor._command_lock = threading.RLock()
    supervisor._command_pub = CapturePublisher()
    supervisor._emergency_latched = False
    supervisor._motion_stop_block_until = 0.0
    motors = [{
        'controller_index': 0,
        'state': 'detected',
        'motor_type': 'ac_servo',
        'fault': False,
    }]
    supervisor._current_motors = lambda: motors

    success_on, _ = supervisor._handle_ac_servo_control({
        'action': 'servo_on', 'scope': 'all',
    })
    success_off, _ = supervisor._handle_ac_servo_control({
        'action': 'servo_off', 'scope': 'all',
    })

    assert success_on is True
    assert success_off is True
    assert len(supervisor._command_pub.messages) == 4
    for command in supervisor._command_pub.messages:
        assert list(command.number_of_target_interfaces) == [1]
        assert list(command.target_interface_id[0].data) == [0]


def test_motor_command_shape_rejects_short_interface_data():
    command = MotorStatus()
    command.controller_index = [0]
    command.number_of_target_interfaces = [2]
    command.target_interface_id = [Int8MultiArray(data=[0])]
    command.controlword = [0]
    command.statusword = [0]
    command.errorcode = [0]
    command.position = [0.0]
    command.velocity = [0.0]
    command.effort = [0.0]

    assert 'interface data length 1 is smaller than 2' in (
        MotionSupervisor._motor_command_shape_error(command) or ''
    )


def test_empty_motor_command_uses_empty_interfaces_until_axis_is_activated():
    supervisor = MotionSupervisor.__new__(MotionSupervisor)
    command = supervisor._empty_motor_command([{'controller_index': 0}])

    assert list(command.target_interface_id[0].data) == []
    assert MotionSupervisor._motor_command_shape_error(command) is None


def safety_supervisor():
    supervisor = MotionSupervisor.__new__(MotionSupervisor)
    supervisor._active_jogs = {1: {'target_position': 12.0}}
    supervisor._active_actions = {}
    supervisor._action_threads = {}
    supervisor._midi_active_until = 1.0
    supervisor._last_motion_run_command_at = 1.0
    supervisor._emergency_latched = False
    supervisor._motion_stop_block_until = 0.0
    supervisor._command_lock = threading.RLock()
    supervisor._command_pub = CapturePublisher()
    supervisor._safety_status_pub = CapturePublisher()
    supervisor._current_motors = lambda: [
        {
            'controller_index': 0,
            'state': 'detected',
            'motor_type': 'ac_servo',
            'position_deg': 15.0,
        },
        {
            'controller_index': 1,
            'state': 'detected',
            'motor_type': 'dynamixel',
            'position_deg': -8.0,
        },
    ]
    return supervisor


def test_motion_stop_holds_all_axes_without_emergency_latch():
    supervisor = safety_supervisor()
    supervisor._command_arbiter = CommandArbiter()
    supervisor._command_arbiter.acquire(CommandOwner.PLAYBACK, lease_sec=1.0)
    success, _ = supervisor._handle_safety_stop(False)
    assert success is True
    assert supervisor._emergency_latched is False
    assert supervisor._active_jogs == {}
    assert supervisor._command_arbiter.snapshot().owner is CommandOwner.NONE
    command = supervisor._command_pub.messages[-1]
    assert list(command.number_of_target_interfaces) == [2, 2]
    assert list(command.position) == [15.0, -8.0]


def test_emergency_stop_disables_ac_and_dynamixel_without_position_then_latches():
    supervisor = safety_supervisor()
    success, _ = supervisor._handle_safety_stop(True)
    assert success is True
    assert supervisor._emergency_latched is True
    command = supervisor._command_pub.messages[-1]
    assert list(command.number_of_target_interfaces) == [1, 1]
    assert command.controlword[0] == 0x0007
    assert command.controlword[1] == 0
    assert list(command.target_interface_id[0].data) == [0]
    assert list(command.target_interface_id[1].data) == [0]


def test_emergency_stop_disables_last_known_axes_without_stale_position():
    supervisor = safety_supervisor()
    last_known = supervisor._current_motors()
    supervisor._current_motors = lambda: []
    supervisor._last_known_motors = lambda: last_known
    success, _ = supervisor._handle_safety_stop(True)
    assert success is True
    command = supervisor._command_pub.messages[-1]
    assert list(command.number_of_target_interfaces) == [1, 1]
    assert command.controlword[0] == 0x0007
    assert command.controlword[1] == 0
    assert list(command.target_interface_id[1].data) == [0]


def test_motion_stop_cannot_bypass_emergency_latch():
    supervisor = safety_supervisor()
    supervisor._handle_safety_stop(True)
    published_count = len(supervisor._command_pub.messages)
    success, message = supervisor._handle_safety_stop(False)
    assert success is False
    assert 'emergency stop is latched' in message
    assert len(supervisor._command_pub.messages) == published_count


def test_dedicated_safety_callback_latches_emergency_and_reports_result():
    supervisor = safety_supervisor()
    results = []
    supervisor._publish_result = lambda request_id, success, message: results.append(
        (request_id, success, message)
    )

    supervisor._safety_request_callback(String(data=json.dumps({
        'request_id': 'emergency-1',
        'command': 'safety_emergency_stop',
    })))

    assert supervisor._emergency_latched is True
    assert results[0][0] == 'emergency-1'
    assert results[0][1] is True


def servo_alarm_supervisor():
    supervisor = MotionSupervisor.__new__(MotionSupervisor)
    supervisor._latest_state = {'motors': []}
    supervisor._latest_state_at = 1.0
    supervisor._servo_alarm_guard = ServoAlarmGuard()
    grades = {'16': 1, '24': 2, '98': 3}
    success, _ = supervisor._servo_alarm_guard.apply_policy(
        grades,
        project_id='test',
        catalog_version=1,
        revision=policy_revision(grades, 1),
    )
    assert success is True
    supervisor._last_motion_run_command_at = 0.0
    supervisor._command_arbiter = CommandArbiter()
    supervisor._publish_safety_status = lambda: None
    supervisor._handle_safety_stop = lambda emergency: (True, 'stopped')
    return supervisor


def ac_servo_alarm(axis, code, *, raw=None):
    return {
        'controller_index': axis,
        'motor_type': 'ac_servo',
        'errorcode': code,
        'errorcode_raw': code if raw is None else raw,
        'fault': True,
    }


def test_grade1_blocks_only_the_faulted_axis():
    supervisor = servo_alarm_supervisor()
    supervisor._latest_state = {
        'motors': [
            ac_servo_alarm(0, 16),
            {
                'controller_index': 1,
                'motor_type': 'ac_servo',
                'errorcode': 0,
                'fault': False,
            },
        ],
    }

    supervisor._evaluate_servo_alarms()

    assert supervisor._servo_alarm_guard.snapshot()['grade'] == 1
    assert supervisor._servo_alarm_block_reason(0)
    assert supervisor._servo_alarm_block_reason(1) == ''
    assert supervisor._servo_alarm_block_reason() == ''


def test_grade1_axis_does_not_rejoin_an_active_playback_mid_motion():
    supervisor = servo_alarm_supervisor()
    supervisor._latest_state = {'motors': [ac_servo_alarm(0, 16)]}
    supervisor._evaluate_servo_alarms()
    supervisor._command_arbiter.acquire(CommandOwner.PLAYBACK, lease_sec=1.0)

    supervisor._latest_state = {'motors': []}
    supervisor._evaluate_servo_alarms()

    alarm_state = supervisor._servo_alarm_guard.snapshot()
    assert alarm_state['active'] == []
    assert alarm_state['blocked_axes'] == [0]
    assert alarm_state['recovery_hold_axes'] == [0]

    supervisor._command_arbiter.release(CommandOwner.PLAYBACK)
    supervisor._evaluate_servo_alarms()

    alarm_state = supervisor._servo_alarm_guard.snapshot()
    assert alarm_state['blocked_axes'] == []
    assert alarm_state['grade'] == 0


def test_grade2_clears_after_live_alarm_is_healthy_without_auto_resume():
    supervisor = servo_alarm_supervisor()
    stops = []
    supervisor._handle_safety_stop = lambda emergency: (
        stops.append(emergency) is None,
        'stopped',
    )
    supervisor._latest_state = {'motors': [ac_servo_alarm(0, 24)]}
    supervisor._evaluate_servo_alarms()

    assert supervisor._servo_alarm_guard.snapshot()['grade'] == 2
    assert stops == [False]

    supervisor._latest_state = {'motors': []}
    supervisor._evaluate_servo_alarms()
    supervisor._servo_alarm_guard._clear_since -= 1.0
    supervisor._evaluate_servo_alarms()

    assert supervisor._servo_alarm_guard.snapshot()['grade'] == 0
    assert supervisor._servo_alarm_block_reason() == ''
    assert stops == [False]


def test_grade3_remains_latched_after_live_alarm_disappears():
    supervisor = servo_alarm_supervisor()
    supervisor._latest_state = {'motors': [ac_servo_alarm(0, 98)]}
    supervisor._evaluate_servo_alarms()
    supervisor._latest_state = {'motors': []}
    supervisor._evaluate_servo_alarms()

    alarm_state = supervisor._servo_alarm_guard.snapshot()
    assert alarm_state['grade3_latched'] is True
    assert alarm_state['grade'] == 3
    assert '프로그램 재시작' in supervisor._servo_alarm_block_reason()


def test_unavailable_ffff_sdo_value_is_not_panasonic_alarm():
    supervisor = servo_alarm_supervisor()
    supervisor._latest_state = {
        'motors': [ac_servo_alarm(0, 0, raw=0xFFFF)],
    }

    supervisor._evaluate_servo_alarms()

    alarm_state = supervisor._servo_alarm_guard.snapshot()
    assert alarm_state['active'] == []
    assert alarm_state['grade'] == 0


def test_manual_activity_snapshot_distinguishes_jog_and_action_axes():
    supervisor = MotionSupervisor.__new__(MotionSupervisor)
    supervisor._active_jogs = {2: {'request_id': 'jog'}}
    supervisor._active_actions = {0: {'request_id': 'action'}}

    assert supervisor._manual_activity_snapshot() == {
        'manual_activity_modes': ['jog', 'action'],
        'manual_activity_axes': [0, 2],
    }
