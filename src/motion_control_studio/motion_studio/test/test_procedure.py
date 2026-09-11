"""절차 실행기 · §6-82

녹화·미리보기·초기 위치 이동은 넷 다 "여러 단계를 차례로 밟되, 사용자가 언제든
멈출 수 있고, 실패하면 되감아야 하는" 절차다. 전에는 넷이 각자 손으로 짜여 있어
단계를 하나 끼울 때마다 그 전부를 건드려야 했다 · 빠뜨려도 아무도 안 알려줬다.

여기서는 그 공통 규칙이 진짜로 지켜지는지 본다.
"""

import threading
import time

import pytest

from motion_studio.procedure import ProcedureStopped, StudioProcedure


class _Studio:
    def __init__(self, generation=1):
        self._lock = threading.RLock()
        self._operation_generation = generation
        self._motion_run_status = {}
        self.failed = None

    def _require_active_operation(self, token, expected_state):
        if token != self._operation_generation:
            raise RuntimeError('사용자가 모션 동작을 정지했습니다')

    def _takes(self):
        studio = self

        class _Board:
            def fail(self, message):
                studio.failed = message

        return _Board()


def _proc(studio=None, token=1):
    return StudioProcedure(studio or _Studio(), token)


# --------------------------------------------------------------------- #
# 단계를 차례로 밟는다
# --------------------------------------------------------------------- #

def test_steps_run_in_order_and_report_success():
    done = []
    assert _proc().run([
        ('하나', lambda: done.append(1)),
        ('둘', lambda: done.append(2)),
    ]) is True
    assert done == [1, 2]


def test_a_failed_step_stops_the_rest_and_names_itself():
    """어느 단계에서 실패했는지 메시지에 남아야 한다 · 화면에는 요약만 뜬다."""
    studio = _Studio()
    done = []

    def boom():
        raise ValueError('모션 실행 준비 실패')

    assert _proc(studio).run([
        ('초기 위치 이동', boom),
        ('녹화 시작', lambda: done.append('안 와야 한다')),
    ]) is False
    assert done == []
    assert '초기 위치 이동' in studio.failed
    assert '모션 실행 준비 실패' in studio.failed


def test_stopping_is_not_an_error():
    """사용자가 멈춘 것은 실패가 아니다 · 빨간 오류로 보이면 안 된다."""
    studio = _Studio()
    assert _proc(studio).run([
        ('카운트다운', lambda: (_ for _ in ()).throw(ProcedureStopped())),
    ]) is False
    assert studio.failed is None


def test_a_replaced_operation_stops_quietly_before_the_next_step():
    """사용자가 멈추고 다른 걸 시작했다 · 남은 단계를 밟으면 안 된다."""
    studio = _Studio()
    done = []

    def user_stops():
        studio._operation_generation = 99

    assert _proc(studio).run([
        ('정지 누름', user_stops),
        ('녹화 시작', lambda: done.append('안 와야 한다')),
    ]) is False
    assert done == []
    assert studio.failed is None


# --------------------------------------------------------------------- #
# 되감기
# --------------------------------------------------------------------- #

def test_unwind_runs_in_reverse_even_when_a_step_fails():
    """MIDI 잠금 같은 것을 손으로 풀다 보면 빠뜨린다 · 실행기가 맡는다."""
    undone = []
    steps = _proc()
    steps.unwind(lambda: undone.append('첫째'))
    steps.unwind(lambda: undone.append('둘째'))

    steps.run([('터짐', lambda: (_ for _ in ()).throw(ValueError('실패')))])
    assert undone == ['둘째', '첫째']


def test_unwind_also_runs_on_success():
    undone = []
    steps = _proc()
    steps.unwind(lambda: undone.append('풀기'))
    steps.run([('하나', lambda: None)])
    assert undone == ['풀기']


def test_a_cancelled_unwind_is_not_run():
    """정상적으로 풀었으면 다시 풀 필요가 없다."""
    undone = []
    steps = _proc()
    release = lambda: undone.append('풀기')
    steps.unwind(release)
    steps.run([('정상 해제', lambda: steps.cancel_unwind(release))])
    assert undone == []


def test_an_unwind_that_itself_fails_does_not_hide_the_original_failure():
    studio = _Studio()
    steps = _proc(studio)
    steps.unwind(lambda: (_ for _ in ()).throw(RuntimeError('되감기도 실패')))
    steps.run([('터짐', lambda: (_ for _ in ()).throw(ValueError('원래 실패')))])
    assert '원래 실패' in studio.failed


# --------------------------------------------------------------------- #
# 기다림 · 한 가지 방식만
# --------------------------------------------------------------------- #

def test_waiting_returns_as_soon_as_the_condition_holds():
    studio = _Studio()
    steps = _proc(studio)
    ticks = {'n': 0}

    def ready():
        ticks['n'] += 1
        return ticks['n'] >= 3

    steps.wait_until(ready, timeout=2.0, timeout_message='확인', interval=0.001)
    assert ticks['n'] == 3


def test_waiting_gives_up_with_the_step_name_in_the_message():
    with pytest.raises(ValueError, match='초기 위치 도착 확인 · 제한 시간 초과'):
        _proc().wait_until(
            lambda: False, timeout=0.06,
            timeout_message='초기 위치 도착 확인', interval=0.001,
        )


def test_a_run_node_error_fails_at_once_instead_of_waiting_out_the_clock():
    """실행 노드가 오류를 보고했는데 제한 시간까지 기다리면 사용자는 영문을
    모른 채 30초를 본다."""
    studio = _Studio()
    studio._motion_run_status = {'state': 'error', 'message': '계획 생성 실패'}
    begin = time.monotonic()

    with pytest.raises(ValueError, match='계획 생성 실패'):
        _proc(studio).wait_for_run_state(
            {'initialized'}, timeout=5.0, timeout_message='초기 위치 도착 확인',
        )

    assert time.monotonic() - begin < 1.0, '오류를 보고도 계속 기다렸다'


def test_waiting_stops_when_the_operation_is_replaced():
    studio = _Studio()
    studio._operation_generation = 99
    with pytest.raises(ProcedureStopped):
        _proc(studio).wait_until(
            lambda: False, timeout=1.0, timeout_message='확인', interval=0.001,
        )


def test_a_run_state_we_want_ends_the_wait():
    studio = _Studio()
    studio._motion_run_status = {'state': 'running'}
    _proc(studio).wait_for_run_state(
        {'running', 'verifying'}, timeout=1.0, timeout_message='재생 시작 확인',
    )
