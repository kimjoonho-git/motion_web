from pathlib import Path


from motion_web_bridge.routes.system_routes import IndexComposer

WORKSPACE = Path(__file__).resolve().parents[4]
UI = WORKSPACE / 'src/motion_web/web_ui/static'
BRIDGE = WORKSPACE / 'src/motion_web/web_bridge/motion_web_bridge/bridge_node.py'


def test_coordination_screen_keeps_settings_and_roster_only():
    """연동 화면은 설정·명단·진단만 갖는다 · 실행은 모션 실행 화면이 갖는다.

    "1회 시작"이 연동 화면과 모션 실행 화면에 두 벌 있었고, 한쪽은 전체 PC,
    다른 쪽은 이 PC였다. 이름으로 구별할 수 없어 위험했다 · §6-65

    화면은 셸에 조각을 끼운 결과다 · 셸만 읽으면 패널이 보이지 않는다 · §6-44
    """
    html, _etag = IndexComposer(UI / 'index.html').compose()
    for marker in (
        'id="coordinationGroupId"', 'id="coordinationDomainId"',
        'id="coordinationJoinButton"', 'id="coordinationLeaveButton"',
        'id="coordinationPeerRows"', 'id="coordinationRunAvailability"',
        'id="coordinationAcknowledgeErrorButton"',
        'id="coordinationErrorSummary"', '실행 참가',
        '실물 미검증',
    ):
        assert marker in html

    # 실행 제어는 모션 실행 화면 한 곳에만 있고, 범위로 갈린다
    for moved in (
        'coordinationStartButton', 'coordinationContinuousStartButton',
        'coordinationInitializeButton', 'coordinationStopNowButton',
        'coordinationStopAfterButton', 'coordinationRepeatMode',
        'coordinationDwellSec', 'coordinationTargetStopCycle',
    ):
        assert moved not in html, f'{moved} 가 연동 화면에 남아 있다'
    for scope in ('id="motionRunScopeLocal"', 'id="motionRunScopeGroup"'):
        assert scope in html

    for obsolete in (
        'coordinationPairingStartButton', 'coordinationRoleSelect',
        'coordinationRepeatCountInput', 'coordinationDwellInput',
        'coordinationAcquireButton',
    ):
        assert obsolete not in html


def test_user_web_exposes_only_local_high_level_group_control():
    # 라우트가 어느 모듈에 있든 계약은 같다 · 웹 경계 전체를 본다
    source = '\n'.join(
        path.read_text(encoding='utf-8')
        for path in sorted(BRIDGE.parent.rglob('*.py'))
    )
    assert "@app.get('/api/coordination')" in source
    assert "@app.put('/api/coordination/settings')" in source
    assert "@app.post('/api/coordination/control')" in source
    assert "@app.post('/api/coordination/local-control')" in source
    assert "@app.get('/api/coordination/local-status')" in source
    assert '/api/coordination/pairing/' not in source
    assert '8010' not in source


def test_frontend_uses_manual_group_commands_without_repeat_count():
    controller = (UI / 'js/coordination.js').read_text(encoding='utf-8')
    for command in (
        'join', 'leave', 'start_group', 'stop_after_cycle', 'stop_now',
        'acknowledge_group_error',
    ):
        assert command in controller
    assert 'groupErrorActive' in controller
    assert "peer.state !== 'online'" in controller
    assert 'repeat_count' not in controller
    assert 'common_dwell' not in controller
