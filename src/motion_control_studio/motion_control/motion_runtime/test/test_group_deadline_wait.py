"""그룹 마감 대기가 음수로 잠들지 않는다 · §6-156

**11회차에서 세 대가 통째로 섰다.**

    ValueError: sleep length must be non-negative

`_wait_group_deadline` 은 시계를 **두 번** 본다 · `while` 에서 한 번,
`time.sleep(...)` 의 인자를 만들며 또 한 번 · 그 사이에 마감이 지나가면
남은 시간이 음수가 되고 파이썬이 터진다.

기다림의 **마지막 한 바퀴**는 늘 남은 시간이 0 에 가깝다 · 그래서 매 회차가
이 외줄을 한 번씩 탄다 · 대개 무사히 건너므로 재현이 안 됐다 ·
2026-09-18 에는 11회차, 그전에는 6회차에서 걸렸다.

여기서는 그 찰나를 **손으로 만들어** 건넌다.
"""

import threading

import pytest

from motion_runtime import motion_run_manager
from motion_runtime.motion_run_manager import MotionRunManager


def _manager():
    manager = MotionRunManager.__new__(MotionRunManager)
    manager._stop_event = threading.Event()
    manager.period_sec = 0.02
    manager._status = {}
    manager._update_status = lambda value: manager._status.update(dict(value))
    return manager


@pytest.fixture
def clock(monkeypatch):
    """시계와 잠을 손에 쥔다 · 실제로 기다리지는 않는다."""
    state = {'reads': [], 'slept': []}

    def fake_monotonic():
        return state['reads'].pop(0)

    monkeypatch.setattr(motion_run_manager.time, 'monotonic', fake_monotonic)
    monkeypatch.setattr(
        motion_run_manager.time, 'sleep', lambda value: state['slept'].append(value)
    )
    return state


def _wait(manager):
    manager._wait_group_deadline(
        100.0,
        phase='group_cycle_initialize_scheduled',
        message='그룹 모션 11회차 후 초기화 대기',
        execution_id='exec-a',
        cycle_number=11,
    )


def test_the_deadline_passing_mid_loop_does_not_sleep_a_negative(clock):
    """실제로 터진 그 찰나 · `while` 통과 뒤에 마감이 지나간다."""
    clock['reads'] = [
        99.99,      # remaining 계산 · 0.01초 남았다
        99.9999,    # while · 아직 0.1ms 남았다 → 들어간다
        100.0002,   # sleep 인자 · 이미 0.2ms 지났다 ← 여기서 음수가 나왔다
        100.0002,   # while · 지났으니 빠져나간다
    ]

    _wait(_manager())

    assert clock['slept'] == [0.0], (
        '마감이 지난 뒤에는 0 으로 잠들어야 합니다 · '
        f'실제로 넘긴 값: {clock["slept"]}'
    )


def test_a_normal_wait_still_sleeps(clock):
    """음수만 막을 것 · 멀쩡한 기다림까지 건너뛰면 편차가 깨진다."""
    clock['reads'] = [
        99.0,       # remaining 계산 · 1초 남았다
        99.0,       # while
        99.0,       # sleep 인자 · 1초 남았으므로 상한 0.02 로 잘린다
        100.0,      # while · 마감 도달
    ]

    _wait(_manager())

    assert clock['slept'] == [0.02]


def test_a_deadline_long_past_is_refused_before_waiting(clock):
    """한 주기보다 더 지났으면 기다리지 말고 알린다 · 원래 있던 문."""
    clock['reads'] = [100.5]  # 0.5초 지났다 · period_sec(0.02) 를 넘는다

    with pytest.raises(RuntimeError, match='놓쳤습니다'):
        _wait(_manager())

    assert clock['slept'] == []


def test_a_stop_while_waiting_still_interrupts(clock):
    """음수 막기가 정지 신호를 삼키지 않는지."""
    clock['reads'] = [99.0, 99.0]
    manager = _manager()
    manager._stop_event.set()

    with pytest.raises(InterruptedError):
        _wait(manager)

    assert clock['slept'] == []
