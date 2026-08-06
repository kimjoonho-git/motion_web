from pathlib import Path


WORKSPACE = Path(__file__).resolve().parents[4]
UI = WORKSPACE / 'src/motion_web/web_ui/static'
BRIDGE = WORKSPACE / 'src/motion_web/web_bridge/motion_web_bridge/bridge_node.py'


def test_coordination_screen_has_dds_group_controls_only():
    html = (UI / 'index.html').read_text(encoding='utf-8')
    for marker in (
        'id="coordinationGroupId"', 'id="coordinationDomainId"',
        'id="coordinationJoinButton"', 'id="coordinationLeaveButton"',
        'id="coordinationStartButton"', 'id="coordinationStopAfterButton"',
        'id="coordinationStopNowButton"', 'id="coordinationPeerRows"',
        'id="coordinationAcknowledgeErrorButton"',
        'id="coordinationErrorSummary"', '실행 참가',
        '실물 미검증',
    ):
        assert marker in html
    for obsolete in (
        'coordinationPairingStartButton', 'coordinationRoleSelect',
        'coordinationRepeatCountInput', 'coordinationDwellInput',
        'coordinationAcquireButton',
    ):
        assert obsolete not in html


def test_user_web_exposes_only_local_high_level_group_control():
    source = BRIDGE.read_text(encoding='utf-8')
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
