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


def test_midi_batch_publishes_multiple_axes_in_one_motor_status():
    supervisor = MotionSupervisor.__new__(MotionSupervisor)
    supervisor._last_motion_run_command_at = 0.0
    supervisor._active_jogs = {}
    supervisor._active_actions = {}
    supervisor._midi_active_until = 0.0
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
