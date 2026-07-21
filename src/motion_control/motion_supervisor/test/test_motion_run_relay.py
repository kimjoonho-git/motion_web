import json
import threading

from motion_control_msgs.msg import MotorStatus
from std_msgs.msg import Int8MultiArray, String

from motion_supervisor.supervisor_node import MotionSupervisor, motion_run_rejection_reason


class CapturePublisher:
    def __init__(self):
        self.messages = []

    def publish(self, message):
        self.messages.append(message)


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
    success, _ = supervisor._handle_safety_stop(False)
    assert success is True
    assert supervisor._emergency_latched is False
    assert supervisor._active_jogs == {}
    command = supervisor._command_pub.messages[-1]
    assert list(command.number_of_target_interfaces) == [2, 2]
    assert list(command.position) == [15.0, -8.0]


def test_emergency_stop_disables_ac_and_holds_dynamixel_then_latches():
    supervisor = safety_supervisor()
    success, _ = supervisor._handle_safety_stop(True)
    assert success is True
    assert supervisor._emergency_latched is True
    command = supervisor._command_pub.messages[-1]
    assert list(command.number_of_target_interfaces) == [1, 2]
    assert command.controlword[0] == 0x0007
    assert command.position[1] == -8.0


def test_emergency_stop_uses_last_known_ac_axis_but_not_stale_position():
    supervisor = safety_supervisor()
    last_known = supervisor._current_motors()
    supervisor._current_motors = lambda: []
    supervisor._last_known_motors = lambda: last_known
    success, _ = supervisor._handle_safety_stop(True)
    assert success is True
    command = supervisor._command_pub.messages[-1]
    assert list(command.number_of_target_interfaces) == [1, 0]
    assert command.controlword[0] == 0x0007


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
