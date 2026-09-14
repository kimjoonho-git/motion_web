from pathlib import Path


from motion_web_bridge.routes.system_routes import IndexComposer

WORKSPACE = Path(__file__).resolve().parents[4]
UI = WORKSPACE / 'src/motion_web/web_ui/static'
BRIDGE = WORKSPACE / 'src/motion_web/web_bridge/motion_web_bridge/bridge_node.py'


def test_coordination_lives_in_one_screen():
    """연동은 한 화면에 모인다 · 실행 제어는 모션 실행 화면이 갖는다.

    **결정이 한 번 뒤집혔다.**

    2026-09-10(§6-66) 에는 연동 탭을 없애고 실행 화면 안으로 넣었다 · 실행하려면
    한쪽, 연동 상태를 보려면 다른 쪽을 봐야 했기 때문이다.

    2026-09-14 에 다시 탭으로 나눴다 · 그 사이에 연동 살림살이(설정·세션)가
    시스템 정보로 갔고, 그래서 연동이 **두 화면에 반씩** 나뉘어 [그룹 참가]
    버튼이 양쪽에 하나씩 생겼다 · 어느 쪽을 열어야 할지 매번 생각해야 했다.

    지금 규칙은 하나다 · **연동에 관한 것은 연동 화면 하나에만 있다** ·
    실행 화면에는 "지금 시작해도 되는가" 에 답하는 것만 남는다(대상 · 참가 PC
    한 줄 · 막힘 사유) · 실행 버튼은 여전히 실행 화면에만 있다.

    화면은 셸에 조각을 끼운 결과다 · 셸만 읽으면 패널이 보이지 않는다 · §6-44
    """
    html, _etag = IndexComposer(UI / 'index.html').compose()

    # 연동 화면이 있고, 연동에 관한 것이 거기 있다
    assert 'data-workspace-tab="coordination"' in html
    assert 'data-workspace-panel="coordination"' in html
    for marker in (
        'id="coordinationGroupId"', 'id="coordinationDomainId"',
        'id="coordinationJoinButton"', 'id="coordinationLeaveButton"',
        'id="coordinationPeerRows"', 'id="coordinationRunAvailability"',
        'id="coordinationAcknowledgeErrorButton"',
        'id="coordinationErrorSummary"', '실행 참가',
    ):
        assert marker in html

    # 실행 제어는 모션 실행 화면 한 곳에만 있고, 대상으로 갈린다
    for moved in (
        'coordinationStartButton', 'coordinationContinuousStartButton',
        'coordinationInitializeButton', 'coordinationStopNowButton',
        'coordinationStopAfterButton', 'coordinationRepeatMode',
        'coordinationDwellSec', 'coordinationTargetStopCycle',
    ):
        assert moved not in html, f'{moved} 가 연동 화면에 남아 있다'
    for scope in (
        'id="motionRunScopeLocal"', 'id="motionRunScopeGroup"',
        'id="motionRunPeerSummary"', 'id="motionRunRoleBadge"',
    ):
        assert scope in html

    # 같은 조작이 두 곳에 있으면 안 된다 · 참가는 연동 화면에서만
    assert 'id="motionRunJoinGroupButton"' not in html
    assert 'id="motionRunOpenCoordinationButton"' in html

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


def test_the_web_can_hand_the_midi_over():
    """MIDI 를 쓸 PC 를 화면에서 정한다 · §6-94

    중계는 조정 노드가 이미 하고 있었는데 화면에 길이 없어 `curl` 로만 됐다 ·
    허용 목록에 없으면 400 으로 막힌다.

    **판정은 여기서 하지 않는다** · 장치를 든 PC 만 정할 수 있다는 규칙은 조정
    노드에 있고, 웹은 통로만 연다 · `pc_id` 를 그대로 넘겨야 하고 빈 값은
    되돌리기다 · 값을 지어내면 안 된다.
    """
    source = (BRIDGE.parent / 'coordination_bridge.py').read_text(encoding='utf-8')

    assert "'set_midi_target'," in source, '허용 목록에 없다'
    assert "request['pc_id'] = str(payload.get('pc_id') or '')" in source, (
        'pc_id 를 그대로 넘기지 않는다'
    )
