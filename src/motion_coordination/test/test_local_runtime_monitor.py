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


# 못 받았을 때 「왜」가 남는가 · §6-153
#
# 전에는 `timed out` 한 마디가 전부였다 · 브리지가 느렸는지 죽었는지, 한 번
# 튄 건지 계속 그런 건지 가릴 수가 없었다 · 그래서 원인 찾기에 반나절이 갔고,
# 재현이 안 되니 고쳤는지도 확인할 수 없었다.

def test_a_failure_leaves_numbers_behind():
    """사유만 남기면 다음에도 똑같이 헤맨다."""
    def refuse():
        raise OSError('timed out')

    monitor = LocalRuntimeMonitor(refuse)
    for _ in range(3):
        monitor.poll_once()

    said = monitor.diagnosis()
    assert '연속 실패 3회' in said
    assert '최근 응답(ms)' in said
    assert 'timed out' in said
    assert monitor.snapshot()['failures_total'] == 3


def test_a_success_resets_the_streak_but_keeps_the_worst():
    """연속 몇 번인지가 판정의 핵심이다 · 최악값은 여유를 보는 데 쓴다."""
    answers = [OSError('느림'), OSError('느림'), {'bridge_state': 'ok'}]

    def take_turns():
        value = answers.pop(0)
        if isinstance(value, Exception):
            raise value
        return value

    monitor = LocalRuntimeMonitor(take_turns)
    for _ in range(3):
        monitor.poll_once()

    sample = monitor.snapshot()
    assert sample['consecutive_failures'] == 0, '성공했는데 연속 실패가 남았다'
    assert sample['failures_total'] == 2, '지나간 실패까지 지우면 안 된다'
    assert len(sample['recent_ms']) == 3, '성공도 실패도 다 재야 한다'


def test_recent_samples_do_not_grow_without_end():
    """오래 돌아도 메모리를 먹으면 안 된다 · 앞뒤만 보면 된다."""
    monitor = LocalRuntimeMonitor(lambda: {'bridge_state': 'ok'})
    for _ in range(200):
        monitor.poll_once()
    assert len(monitor.snapshot()['recent_ms']) == 20


def test_never_receiving_anything_says_so():
    """한 번도 못 받은 것과 받다가 끊긴 것은 원인이 다르다."""
    monitor = LocalRuntimeMonitor(lambda: (_ for _ in ()).throw(OSError('연결 거부')))
    monitor.poll_once()
    assert '한 번도 못 받음' in monitor.diagnosis()
