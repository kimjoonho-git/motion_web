from pathlib import Path


from motion_web_bridge.index_composer import IndexComposer

WORKSPACE = Path(__file__).resolve().parents[4]
UI = WORKSPACE / 'src/motion_web/web_ui/static'
BRIDGE = WORKSPACE / 'src/motion_web/web_bridge/motion_web_bridge/bridge_node.py'


def test_coordination_lives_in_one_screen():
    """그룹에 관한 것은 연동 화면에, 이 PC 실행은 실행 화면에.

    **결정이 세 번 바뀌었다 · 마지막이 이것이다.**

    2026-09-10(§6-66) 연동 탭을 없애고 실행 화면에 넣었다 · 실행하려면 한쪽,
    연동 상태를 보려면 다른 쪽을 봐야 했기 때문이다.

    2026-09-14 다시 탭으로 나눴다 · 연동 살림살이가 시스템 정보로 가면서
    연동이 두 화면에 반씩 나뉘었고 [그룹 참가] 가 양쪽에 생겼다.

    2026-09-15(§6-100) **실행과 연동을 완전히 가른다** · 그 사이에는 실행
    화면이 `실행 대상` 을 골라 같은 버튼이 이 PC 를 돌리기도, 참가한 PC
    전부를 돌리기도 했다 · 모터가 실제로 움직이는 버튼에서 그 애매함은
    위험하다.

    가를 수 있는 근거는 **둘이 실제로 다른 일**이라는 것이다 · 그룹 실행은
    모션 파일을 들고 가지 않는다(`run_mode`·`repeat_mode`·`dwell_sec`·
    `target_cycle_count` 뿐) · 참가한 PC 들에게 시작·정지 신호만 보내고 각
    PC 는 제 모션을 돌린다.

        PC 연동 설정 · 그룹  · 참가 · 명단 · 그룹 실행 · 각 PC 진행 · 예약 · MIDI
        모션 실행    · 이 PC · 모션 고르기 · 실행 · 진행 · 그래프

    화면은 셸에 조각을 끼운 결과다 · 셸만 읽으면 패널이 보이지 않는다 · §6-44
    """
    html, _etag = IndexComposer(UI / 'index.html').compose()

    assert 'data-workspace-tab="coordination"' in html
    assert 'data-workspace-panel="coordination"' in html

    # 그룹에 관한 것은 전부 연동 화면에 있다
    for marker in (
        'id="coordinationGroupId"', 'id="coordinationDomainId"',
        # 들어오거나 나가거나 둘 뿐이다 · 「지금 빠지기」는 없앴다 · §6-164
        'id="coordinationJoinButton"', 'id="coordinationLeaveButton"',
        'id="coordinationPeerRows"', 'id="coordinationRunAvailability"',
        'id="coordinationAcknowledgeErrorButton"',
        'id="coordinationErrorSummary"', '실행 참가',
        'id="coordinationStartOnceButton"',
        'id="coordinationStartContinuousButton"',
        'id="coordinationInitializeButton"', 'id="coordinationStopNowButton"',
        'id="coordinationStopAfterButton"', 'id="motionRunPeerRows"',
    ):
        assert marker in html, f'{marker} 가 연동 화면에 없다'

    # 그룹 반복 옵션은 **그룹 것**을 따로 갖는다 · 실행 화면 입력을 빌려 쓰면
    # 화면을 가른 순간 말없이 기본값으로 돈다
    for marker in (
        'id="coordinationRepeatMode"', 'id="coordinationDwellSec"',
        'id="coordinationTargetCycle"',
    ):
        assert marker in html, f'{marker} 가 없다 · 그룹 옵션이 기본값으로 떨어진다'

    # 실행 화면에는 연동 이야기가 없다 · 대상을 고르지 않는다
    for gone in (
        'id="motionRunScopeLocal"', 'id="motionRunScopeGroup"',
        'id="motionRunScopeGroupOption"', 'id="motionRunPeerSummary"',
        'id="motionRunRoleBadge"', 'id="motionRunOpenCoordinationButton"',
        'id="motionRunJoinGroupButton"',
    ):
        assert gone not in html, f'{gone} 가 실행 화면에 남아 있다'

    # 왜 못 누르는지는 로컬 실행에도 필요하다
    assert 'id="motionRunBlockReason"' in html

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
        # 참가와 탈퇴 둘뿐이다 · §6-164
        'join', 'leave', 'start_group', 'stop_after_cycle',
        'stop_now', 'acknowledge_group_error',
    ):
        assert command in controller
    assert 'temporarily_disable' not in controller, (
        '「지금 빠지기」가 화면에 남아 있습니다 · 참가/탈퇴 둘로 갑니다'
    )
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
