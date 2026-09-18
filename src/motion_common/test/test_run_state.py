"""「돌고 있다」의 뜻은 하나다 · §6-136."""

import pytest

from motion_common import run_state


@pytest.mark.parametrize('state', [
    'running', 'preparing', 'initializing', 'initialized', 'countdown',
    'waiting', 'stopping', 'armed', 'start_scheduled', 'cycle_ready',
    'waiting_cycle_ready', 'cycle_initialize_scheduled', 'releasing',
])
def test_moving_states_are_running(state):
    assert run_state.is_running(state) is True


@pytest.mark.parametrize('state', [
    'idle', 'off', 'ready', 'stopped', 'completed', 'motion_completed',
    'error', 'blocked',
])
def test_resting_states_are_idle(state):
    assert run_state.is_idle(state) is True


@pytest.mark.parametrize('state', ['', None, '   '])
def test_an_unread_state_is_not_treated_as_stopped(state):
    """못 읽은 것과 멈춘 것은 다르다.

    브리지가 아직 안 떴거나 응답이 늦으면 상태가 비어 온다 · 그것을 「멈췄다」로
    읽으면 이미 도는 모션에 시작 명령을 보낸다 · 실제로 멈춰 있으면 `idle` 이
    온다.
    """
    assert run_state.is_running(state) is True


def test_unknown_states_count_as_running():
    """모르면 가만둔다 · 도는 모션을 또 시작시키는 것보다 낫다.

    새 실행 단계가 생겼는데 여기에 안 적으면, 「멈춰 있다」로 읽어 이미 도는
    모션을 또 시작시킨다 · 그래서 목록은 **멈춰 있는 쪽**만 적는다.
    """
    assert run_state.is_running('새로_생긴_단계') is True
    assert run_state.is_running('RUNNING') is True


def test_case_and_space_do_not_matter():
    assert run_state.is_idle('  STOPPED  ') is True


@pytest.mark.parametrize('state', [
    'preparing', 'initializing', 'armed', 'start_scheduled', 'waiting',
    'running', 'waiting_cycle_ready', 'cycle_ready', 'stop_after_cycle',
    'releasing',
])
def test_a_live_group_execution_counts_as_running(state):
    """준비 단계도 「돌고 있다」다 · §6-145

    그룹 실행은 준비가 길다 · 그동안 이 PC 의 로컬 모션은 아직 `stopped` 라,
    로컬만 보면 「멈춤」으로 읽고 이미 시작된 그룹 실행을 또 시작시킨다.
    """
    assert run_state.group_is_active({
        'execution_id': 'exec-1', 'state': state,
    }) is True


def test_a_finished_group_execution_is_not_active():
    """끝나면 `execution_id` 가 빈다 · 상태만 보고 판단하지 않는다."""
    assert run_state.group_is_active({'execution_id': '', 'state': 'running'}) is False
    assert run_state.group_is_active({'execution_id': 'exec-1', 'state': 'stopped'}) is False
    assert run_state.group_is_active({}) is False
    assert run_state.group_is_active(None) is False
