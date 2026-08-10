from motion_coordination.group_peer_display import enrich_peer_row


def test_enrich_peer_row_idle_when_execution_inactive():
    row = enrich_peer_row({
        'pc_id': 'pc-a',
        'display_cycle': 3,
        'display_step': '모션 실행 중',
        'motion_phase': 'running',
        'motion_state': 'running',
        'motion_duration_sec': 6.0,
        'motion_elapsed_sec': 1.5,
        'motion_progress_ratio': 0.25,
    }, execution_active=False)
    assert row['motion_cycle'] == 0
    assert row['motion_cycle_text'] == '-'
    assert row['motion_step'] == '그룹 대기'
    assert row['motion_progress'] == '-'


def test_enrich_peer_row_relays_display_fields():
    row = enrich_peer_row({
        'pc_id': 'pc-a',
        'display_cycle': 3,
        'display_step': '모션 실행 중',
        'motion_phase': 'running',
        'motion_state': 'running',
        'motion_duration_sec': 6.0,
        'motion_elapsed_sec': 1.5,
        'motion_progress_ratio': 0.25,
    }, execution_active=True)
    assert row['motion_cycle'] == 3
    assert row['motion_cycle_text'] == '3회차'
    assert row['motion_step'] == '모션 실행 중'
    assert row['motion_progress'] == '1.50 / 6.00초 · 25%'


def test_enrich_peer_row_falls_back_to_current_cycle():
    row = enrich_peer_row({
        'current_cycle': 2,
        'display_step': '회차 완료',
    }, execution_active=True)
    assert row['motion_cycle'] == 2
    assert row['motion_cycle_text'] == '2회차'
    assert row['motion_step'] == '회차 완료'


def test_enrich_peer_row_step_fallback_when_missing():
    row = enrich_peer_row({
        'display_cycle': 1,
    }, execution_active=True)
    assert row['motion_step'] == '확인 중'


def test_enrich_peer_row_hides_progress_without_duration():
    row = enrich_peer_row({
        'display_cycle': 1,
        'display_step': '그룹 준비',
        'motion_phase': 'group_preparing',
        'motion_state': 'preparing',
    }, execution_active=True)
    assert row['motion_progress'] == '-'
