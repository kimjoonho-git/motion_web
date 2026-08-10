from motion_coordination.group_peer_display import (
    build_peer_motion_view,
    motion_cycle_number,
    motion_step_label,
)


def test_motion_step_label_covers_initializing_phase():
    assert motion_step_label(
        motion_phase='initializing',
        motion_state='initializing',
        execution_active=True,
    ) == '초기위치 이동'


def test_motion_step_label_maps_motion_completed_state():
    assert motion_step_label(
        motion_phase='',
        motion_state='motion_completed',
        execution_active=True,
    ) == '회차 완료'


def test_motion_cycle_shows_next_cycle_during_reinitialize():
    assert motion_cycle_number(
        motion_phase='group_cycle_initialize_scheduled',
        motion_state='waiting',
        current_cycle=1,
        execution_cycle=1,
        execution_active=True,
    ) == 2


def test_motion_cycle_keeps_running_cycle():
    assert motion_cycle_number(
        motion_phase='running',
        motion_state='running',
        current_cycle=3,
        execution_cycle=3,
        execution_active=True,
    ) == 3


def test_motion_cycle_hidden_before_first_motion():
    view = build_peer_motion_view(
        motion_phase='group_preparing',
        motion_state='preparing',
        execution_active=True,
    )
    assert view['motion_cycle_text'] == '-'
    assert view['motion_step'] == '그룹 준비'


def test_motion_cycle_keeps_next_cycle_during_reinitialize_motion():
    assert motion_cycle_number(
        motion_phase='initializing',
        motion_state='initializing',
        current_cycle=2,
        execution_cycle=1,
        execution_active=True,
    ) == 2


def test_build_peer_motion_view_formats_running_row():
    view = build_peer_motion_view(
        motion_phase='running',
        motion_state='running',
        current_cycle=2,
        execution_cycle=2,
        execution_active=True,
        elapsed_sec=1.5,
        duration_sec=6.0,
        progress_ratio=0.25,
    )
    assert view['motion_cycle_text'] == '2회차'
    assert view['motion_step'] == '모션 실행 중'
    assert view['motion_progress'] == '1.50 / 6.00초 · 25%'
