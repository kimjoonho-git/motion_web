from motion_coordination.safety_stop import SafetyStopController


def test_safety_stop_always_runs_local_before_dds_publish():
    events = []
    outcome = SafetyStopController().stop_now(
        lambda: events.append('local') or {'success': True},
        lambda: events.append('dds'),
    )
    assert events == ['local', 'dds']
    assert outcome.local_success is True
    assert outcome.dds_stop_published is True


def test_safety_stop_still_publishes_dds_when_local_stop_fails():
    events = []
    outcome = SafetyStopController().stop_now(
        lambda: events.append('local') or {
            'success': False, 'message': 'local timeout',
        },
        lambda: events.append('dds'),
    )
    assert events == ['local', 'dds']
    assert outcome.local_success is False
    assert outcome.dds_stop_published is True


def test_received_stop_can_apply_locally_without_rebroadcast():
    events = []
    outcome = SafetyStopController().stop_now(
        lambda: events.append('local') or {'success': True},
    )
    assert events == ['local']
    assert outcome.local_success is True
    assert outcome.dds_stop_published is False
