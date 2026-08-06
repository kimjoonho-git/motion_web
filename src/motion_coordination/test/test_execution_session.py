from motion_coordination.execution_session import ExecutionSession


def test_clear_active_resets_every_execution_transport_field():
    session = ExecutionSession(
        execution_id='exec-a', coordinator_id='pc-a',
        participants=('pc-a', 'pc-b'), stopped_members={'pc-a'},
        pending_command='start_at', pending_command_id='cmd-a',
        pending_acks={'pc-a'}, pending_ack_deadline=10.0,
        pending_scheduled_at=9.0, motion_start_report_deadline=11.0,
        motion_start_report_cycle=2, retry_attempt=1,
        retry_root_execution_id='root-a', retry_pending={'stage': 'start'},
        stop_confirmation_deadline=12.0,
    )

    session.clear_active()

    assert session.execution_id == ''
    assert session.coordinator_id == ''
    assert session.participants == ()
    assert session.stopped_members == set()
    assert session.pending_command_id == ''
    assert session.pending_acks == set()
    assert session.pending_ack_deadline == 0.0
    assert session.pending_scheduled_at == 0.0
    assert session.motion_start_report_deadline == 0.0
    assert session.motion_start_report_cycle == 0
    assert session.retry_attempt == 1
    assert session.retry_pending == {'stage': 'start'}
    assert session.stop_confirmation_deadline == 12.0


def test_reset_also_clears_retry_and_stop_confirmation_state():
    session = ExecutionSession(
        execution_id='exec-a', retry_attempt=1,
        retry_root_execution_id='root-a', retry_pending={'retry': True},
        stop_confirmation_deadline=3.0,
    )

    session.reset()

    assert session.execution_id == ''
    assert session.retry_attempt == 0
    assert session.retry_root_execution_id == ''
    assert session.retry_pending == {}
    assert session.stop_confirmation_deadline == 0.0
