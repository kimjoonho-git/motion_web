from motion_supervisor.supervisor_node import motion_run_rejection_reason


def test_runtime_command_is_allowed_when_state_is_current_and_manual_is_idle():
    assert motion_run_rejection_reason(True, False) is None


def test_runtime_command_is_rejected_without_current_motor_state():
    assert motion_run_rejection_reason(False, False) == (
        'motor state is unavailable or stale'
    )


def test_runtime_command_is_rejected_while_manual_command_is_active():
    assert motion_run_rejection_reason(True, True) == 'a manual command is active'
