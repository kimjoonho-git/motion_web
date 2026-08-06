import time

from motion_coordination.local_runtime_monitor import LocalRuntimeMonitor


def test_monitor_keeps_last_good_status_when_next_poll_fails():
    responses = [
        {'bridge_state': 'ok', 'motion_run_status': {'phase': 'running'}},
        OSError('bridge timeout'),
    ]

    def fetch():
        value = responses.pop(0)
        if isinstance(value, Exception):
            raise value
        return value

    monitor = LocalRuntimeMonitor(fetch)
    monitor.poll_once()
    first = monitor.snapshot()
    monitor.poll_once()
    second = monitor.snapshot()

    assert first['status']['motion_run_status']['phase'] == 'running'
    assert second['status'] == first['status']
    assert second['error'] == 'bridge timeout'


def test_monitor_polls_more_often_while_execution_is_active():
    calls = []
    monitor = LocalRuntimeMonitor(
        lambda: calls.append(time.monotonic()) or {'bridge_state': 'ok'},
        active_interval_sec=0.01,
        idle_interval_sec=0.2,
    )
    monitor.start()
    try:
        deadline = time.monotonic() + 0.2
        while len(calls) < 1 and time.monotonic() < deadline:
            time.sleep(0.005)
        monitor.set_active(True)
        deadline = time.monotonic() + 0.2
        while len(calls) < 3 and time.monotonic() < deadline:
            time.sleep(0.005)
        assert len(calls) >= 3
    finally:
        monitor.close()


def test_monitor_records_activation_boundary_for_first_fresh_sample():
    monitor = LocalRuntimeMonitor(lambda: {'bridge_state': 'ok'})
    monitor.poll_once()
    idle = monitor.snapshot()

    monitor.set_active(True)
    active = monitor.snapshot()

    assert active['active'] is True
    assert active['active_since_monotonic'] >= idle['received_monotonic']
