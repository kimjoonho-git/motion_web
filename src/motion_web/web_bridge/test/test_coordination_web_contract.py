from pathlib import Path


WORKSPACE = Path(__file__).resolve().parents[4]
UI = WORKSPACE / 'src/motion_web/web_ui/static'
BRIDGE = WORKSPACE / 'src/motion_web/web_bridge/motion_web_bridge/bridge_node.py'


def test_coordination_screen_has_mode_role_peers_and_readiness_controls():
    html = (UI / 'index.html').read_text(encoding='utf-8')

    for marker in (
        'data-workspace-tab="coordination"',
        'data-workspace-panel="coordination"',
        'id="coordinationModeSelect"',
        'id="coordinationRoleSelect"',
        'id="coordinationPeerRows"',
        'id="coordinationReadinessButton"',
        'id="coordinationRunOnceButton"',
        'id="coordinationMotionStopButton"',
        'id="coordinationInitializeButton"',
        'id="coordinationSynchronizedRunButton"',
    ):
        assert marker in html


def test_user_web_exposes_only_high_level_coordination_control():
    source = BRIDGE.read_text(encoding='utf-8')

    assert "@app.get('/api/coordination')" in source
    assert "@app.put('/api/coordination/settings')" in source
    assert "@app.post('/api/coordination/readiness')" in source
    assert "@app.post('/api/coordination/control')" in source
    assert "@app.post('/api/coordination/local-control')" in source
    assert "/api/coordination/motion-start" not in source
    assert "/api/coordination/motor-command" not in source


def test_coordination_frontend_modules_parse_as_project_assets():
    main = (UI / 'js/main.js').read_text(encoding='utf-8')
    controller = (UI / 'js/coordination.js').read_text(encoding='utf-8')

    assert "from './coordination.js?v=" in main
    assert 'createCoordinationController' in controller
    assert '/api/coordination' in (UI / 'js/api.js').read_text(encoding='utf-8')
