from motion_runtime.motion_group_display import (
    apply_group_display,
    resolve_display_cycle,
    resolve_display_step,
)


def test_display_cycle_uses_group_cycle_number():
    assert resolve_display_cycle({
        'group_execution': True,
        'execution_id': 'exec-a',
        'group_cycle_number': 3,
        'current_cycle': 1,
    }) == 3


def test_display_cycle_hidden_before_first_motion():
    assert resolve_display_cycle({
        'group_execution': True,
        'execution_id': 'exec-a',
        'phase': 'group_preparing',
    }) == 0


def test_display_cycle_stays_on_completed_cycle_during_reinitialize():
    status = apply_group_display({
        'group_execution': True,
        'execution_id': 'exec-a',
        'phase': 'group_cycle_initialize_scheduled',
        'state': 'waiting',
        'group_cycle_number': 3,
        'current_cycle': 3,
    })
    assert status['display_cycle'] == 3
    assert status['display_step'] == '회차 초기화 예약'


def test_display_step_running_is_korean():
    status = apply_group_display({
        'group_execution': True,
        'execution_id': 'exec-a',
        'phase': 'running',
        'state': 'running',
        'group_cycle_number': 2,
    })
    assert status['display_step'] == '모션 실행 중'
    assert status['display_cycle'] == 2


def test_display_step_motion_completed_state():
    assert resolve_display_step({
        'group_execution': True,
        'execution_id': 'exec-a',
        'phase': '',
        'state': 'motion_completed',
    }) == '회차 완료'
