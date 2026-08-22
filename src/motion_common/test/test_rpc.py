"""요청·응답 채널 단일 구현 검증.

네 곳에 흩어져 있던 폴링 대기·만료 정리를 흡수한 결과다. 흡수 전에 어긋나 있던
부분(만료 주기, 만료 기준 시각, 시계 종류)이 하나로 모였는지가 핵심.
"""

import threading
import time

from motion_common import rpc


class FakeClock:
    """시간을 손으로 밀어 대기·만료를 결정적으로 검증한다."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.slept = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept += seconds
        self.now += seconds


def make_store(**kwargs) -> tuple:
    clock = FakeClock()
    store = rpc.ResultStore(clock=clock, sleep=clock.sleep, **kwargs)
    return store, clock


# --------------------------------------------------------------------------- #
# 저장 · 회수
# --------------------------------------------------------------------------- #

def test_store_then_take_returns_payload():
    store, _ = make_store()
    store.store('req-1', {'success': True})
    assert store.take('req-1') == {'success': True}


def test_taking_twice_returns_none_the_second_time():
    """요청·응답은 1:1 · 한 번 꺼낸 응답은 사라진다."""
    store, _ = make_store()
    store.store('req-1', {'ok': 1})
    assert store.take('req-1') == {'ok': 1}
    assert store.take('req-1') is None


def test_blank_request_id_is_rejected():
    store, _ = make_store()
    assert store.store('', {'x': 1}) is False
    assert store.store(None, {'x': 1}) is False
    assert store.take('') is None
    assert store.pending_count() == 0


def test_take_of_unknown_id_returns_none():
    store, _ = make_store()
    store.store('other', {'x': 1})
    assert store.take('missing') is None


# --------------------------------------------------------------------------- #
# 대기
# --------------------------------------------------------------------------- #

def test_wait_returns_immediately_when_result_is_present():
    store, clock = make_store()
    store.store('req-1', {'ok': True})
    assert store.wait('req-1', 5.0) == {'ok': True}
    assert clock.slept == 0.0


def test_wait_times_out_and_returns_none():
    store, clock = make_store()
    assert store.wait('never', 0.05) is None
    assert clock.slept >= 0.05


def test_wait_checks_once_more_after_the_deadline():
    """마감 직전 도착한 응답을 놓치면 안 된다."""
    store, clock = make_store()

    original_sleep = clock.sleep

    def sleep_then_deliver(seconds):
        original_sleep(seconds)
        if clock.now >= 1000.05:
            store.store('late', {'late': True})

    store._sleep = sleep_then_deliver
    assert store.wait('late', 0.05) == {'late': True}


def test_wait_with_blank_id_returns_none_without_sleeping():
    store, clock = make_store()
    assert store.wait('', 1.0) is None
    assert clock.slept == 0.0


def test_wait_with_zero_timeout_still_checks_once():
    store, _ = make_store()
    store.store('req-1', {'ok': True})
    assert store.wait('req-1', 0.0) == {'ok': True}


def test_negative_timeout_is_treated_as_zero():
    store, clock = make_store()
    assert store.wait('missing', -5.0) is None
    assert clock.slept == 0.0


# --------------------------------------------------------------------------- #
# 만료
# --------------------------------------------------------------------------- #

def test_entries_expire_after_ttl():
    store, clock = make_store(ttl_sec=10.0)
    store.store('old', {'x': 1})
    clock.now += 11.0
    store.purge()
    assert store.take('old') is None


def test_fresh_entries_survive_purge():
    store, clock = make_store(ttl_sec=10.0)
    store.store('fresh', {'x': 1})
    clock.now += 5.0
    assert store.purge() == 0
    assert store.take('fresh') == {'x': 1}


def test_storing_purges_stale_entries():
    """별도 정리 타이머 없이 저장 시점에 정리된다."""
    store, clock = make_store(ttl_sec=10.0)
    store.store('old', {'x': 1})
    clock.now += 11.0
    store.store('new', {'y': 2})
    assert store.pending_count() == 1
    assert store.take('old') is None
    assert store.take('new') == {'y': 2}


def test_expiry_uses_receipt_time_not_sender_stamp():
    """발신자 시계에 의존하지 않는다 · PC 간 시계 차이로 결과를 잃지 않는다."""
    store, clock = make_store(ttl_sec=10.0)
    # 발신자가 아주 오래된 stamp를 실어 보내도 수신 시각 기준으로 살아 있어야 한다
    store.store('req-1', {'stamp': 0.0})
    clock.now += 1.0
    assert store.take('req-1') == {'stamp': 0.0}


# --------------------------------------------------------------------------- #
# 정리 · 진단
# --------------------------------------------------------------------------- #

def test_clear_drops_everything():
    store, _ = make_store()
    store.store('a', 1)
    store.store('b', 2)
    store.clear()
    assert store.pending_count() == 0
    assert store.take('a') is None


def test_contains_reports_membership():
    store, _ = make_store()
    store.store('a', 1)
    assert 'a' in store
    assert 'b' not in store


# --------------------------------------------------------------------------- #
# 요청 식별자
# --------------------------------------------------------------------------- #

def test_request_ids_are_unique():
    ids = {rpc.new_request_id() for _ in range(500)}
    assert len(ids) == 500


def test_request_id_prefix_is_applied():
    value = rpc.new_request_id('studio-run')
    assert value.startswith('studio-run-')
    assert len(value) > len('studio-run-')


# --------------------------------------------------------------------------- #
# 스레드 안전성
# --------------------------------------------------------------------------- #

def test_callback_thread_and_waiter_thread_hand_off_correctly():
    """실제 사용 형태 · 콜백 스레드가 넣고 요청 스레드가 기다린다."""
    store = rpc.ResultStore(poll_interval_sec=0.001)
    received = []

    def waiter():
        received.append(store.wait('req-1', 5.0))

    thread = threading.Thread(target=waiter)
    thread.start()
    time.sleep(0.05)
    store.store('req-1', {'ok': True})
    thread.join(timeout=5.0)

    assert received == [{'ok': True}]


def test_concurrent_requests_do_not_cross_wires():
    """서로 다른 request_id가 뒤섞이지 않는다."""
    store = rpc.ResultStore(poll_interval_sec=0.001)
    results = {}

    def waiter(key):
        results[key] = store.wait(key, 5.0)

    threads = [threading.Thread(target=waiter, args=(f'req-{i}',)) for i in range(8)]
    for thread in threads:
        thread.start()
    time.sleep(0.05)
    for i in range(8):
        store.store(f'req-{i}', {'index': i})
    for thread in threads:
        thread.join(timeout=5.0)

    assert results == {f'req-{i}': {'index': i} for i in range(8)}
