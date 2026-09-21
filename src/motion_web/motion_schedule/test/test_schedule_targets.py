"""스케줄이 단독·연동 어느 쪽으로 나가는지 · §6-68.

스케줄은 그룹 전용이었다 · `start_group` 만 보내서 연동을 쓰지 않는 PC 에서는
발화는 하는데 "먼저 DDS 그룹에 참가하세요" 로 매번 실패했다. `resolve_master_role`
이 연동 미사용을 "단독 동작으로 간주" 하며 마스터 판정을 통과시키기 때문에 조용히
실패했다 · 로그를 보지 않으면 알 수 없었다.

노드를 띄우려면 rclpy 가 필요하므로, 여기서는 **어느 엔드포인트로 나가는지**를
소스에서 확인한다.
"""

import re
from pathlib import Path

NODE = (
    Path(__file__).resolve().parents[1]
    / 'motion_schedule' / 'motion_schedule_node.py'
).read_text(encoding='utf-8')


def _body(name: str) -> str:
    start = NODE.index(f'def {name}(')
    nxt = NODE.find('\n    def ', start)
    return NODE[start:nxt if nxt > 0 else len(NODE)]


def test_schedule_start_targets_both_scopes():
    body = _body('_execute_start')
    assert 'self._coordination_enabled()' in body, '범위를 보지 않는다'
    assert '/api/coordination/control' in body, '연동 경로가 없다'
    assert '/api/motion-run/start' in body, '단독 경로가 없다'
    # 연동 경로가 먼저 나가고 단독으로 떨어진다
    assert body.index('/api/coordination/control') < body.index('/api/motion-run/start')


def test_schedule_stop_targets_both_scopes():
    body = _body('_execute_stop_after_cycle')
    assert 'self._coordination_enabled()' in body
    assert '/api/coordination/control' in body
    assert '/api/motion-run/stop-after-cycle' in body


def test_coordination_enabled_reads_the_settings_file():
    body = _body('_coordination_enabled')
    assert 'load_coordination_settings' in body
    # 설정이 없거나 읽지 못하면 단독으로 본다 · 그룹 명령이 실패하는 것보다 낫다
    assert 'return False' in body
    assert "settings.get('enabled'" in body


def test_local_run_fills_active_project_files():
    """단독 스케줄은 무엇을 재생할지 모른다 · 브리지가 활성 파일로 채운다."""
    bridge = (
        Path(__file__).resolve().parents[2]
        / 'web_bridge' / 'motion_web_bridge' / 'bridge_node.py'
    ).read_text(encoding='utf-8')
    start = bridge.index('def motion_run_start(')
    body = bridge[start:bridge.index('\n    def ', start)]
    assert '_with_active_project_files(payload)' in body


# 「슬레이브는 스케줄을 못 고친다」(§6-69) 는 이제 `schedule_service.py` 가 지킨다.
# 라우트 글자를 대조하던 검사가 여기 있었는데, 로직이 서비스로 내려가면서
# 대조가 깨졌다 · 같은 사실을 `web_bridge/test/test_schedule_service.py` 가
# 실제 호출로 검사한다 (쓰기 셋은 막히고 읽기 둘은 열린다) · §6-182


def test_schedule_button_is_disabled_on_a_slave():
    manager = (
        Path(__file__).resolve().parents[2]
        / 'web_ui' / 'static' / 'js' / 'schedule_manager.js'
    ).read_text(encoding='utf-8')
    start = manager.index('updateStatusBadge()')
    body = manager[start:start + 1600]
    # 무엇을 잠글지는 `schedule_scope.js` 가 정한다 · §6-133 · §6-266
    #
    # 「묶여 있지 않으면 실행되지 않는다」는 상태는 없어졌다 · 이제 혼자 돈다 ·
    # 받는 쪽 PC 에서만 잠근다.
    assert 'motionScheduleBadgeState(this.status)' in body, '상태 판단을 쓰지 않는다'
    assert 'button.disabled = !state.canEdit' in body, '받는 쪽에서 버튼이 잠기지 않는다'
    assert 'state.blockedReason' in body, '왜 못 쓰는지 알려주지 않는다'

    scope = (
        Path(__file__).resolve().parents[2]
        / 'web_ui' / 'static' / 'js' / 'schedule_scope.js'
    ).read_text(encoding='utf-8')
    # 주석에는 옛 문구를 적어 둘 수 있다 · 주석을 빼고 본다
    shown = re.sub(r'/\*[\s\S]*?\*/', '', scope)
    shown = re.sub(r'^\s*//.*$', '', shown, flags=re.M)
    assert '이 PC 에서는 설정하지 않습니다' in shown, '어디서 설정하는지 알려주지 않는다'
    assert '시각이 되어도 실행되지 않습니다' not in shown, (
        '이제 혼자 돈다 · 실행되지 않는다는 말은 사실이 아니다'
    )


# 「지문은 이 PC 가 찍는다」(§6-150) 도 `schedule_service.save_schedule()` 로
# 옮겼다 · `test_schedule_service.py` 가 저장된 항목에 시간대가 실제로
# 박히는지 본다 · 아래 두 시험은 그 값이 오가며 살아남는지를 맡는다.


def test_the_fingerprint_survives_a_round_trip():
    """저장했다 읽으면 남아 있어야 한다 · 안 남으면 경고가 영영 안 뜬다."""
    from motion_common.schedule_models import ScheduleItem

    item = ScheduleItem.from_dict({'saved_timezone': 'Europe/Paris'})
    assert item.saved_timezone == 'Europe/Paris'
    assert ScheduleItem.from_dict(item.to_dict()).saved_timezone == 'Europe/Paris'


def test_an_old_schedule_without_a_fingerprint_still_loads():
    """이 값이 생기기 전에 만든 스케줄이 있다 · 그것 때문에 안 열리면 안 된다."""
    from motion_common.schedule_models import ScheduleItem

    assert ScheduleItem.from_dict({'schedule_name': '옛것'}).saved_timezone is None


# --------------------------------------------------------------------------- #
# 쓰겠다는 **설정**과 지금 묶여 있다는 **상태**는 다르다 · §6-266
#
# 설정만 보고 그쪽으로 보내면, 묶이지 않은 PC 에서는 받을 데가 없어 매번
# 거절당한다 · 실측으로 16~18시 구간 안에서 1분마다 거절이 쌓였고(15분에 7회)
# 그동안 모션은 한 번도 돌지 않았다 · 단독으로 도는 길은 이미 있고 잘 돈다.
# --------------------------------------------------------------------------- #


def test_start_checks_the_state_not_only_the_setting():
    body = _body('_execute_start')

    assert 'self._coordination_joined()' in body, '지금 묶여 있는지를 보지 않는다'
    assert (
        'self._coordination_enabled() and self._coordination_joined()' in body
    ), '설정과 상태를 함께 보아야 한다'


def test_stop_uses_the_same_rule():
    body = _body('_execute_stop_after_cycle')

    assert 'self._coordination_enabled() and self._coordination_joined()' in body


def test_joined_reads_the_live_runtime():
    body = _body('_coordination_joined')

    assert "/api/coordination" in body, '지금 상태를 물어보지 않는다'
    assert "runtime" in body
    assert "joined" in body


def test_joined_is_false_when_the_answer_is_not_usable():
    """못 읽으면 단독으로 본다 · 그룹 명령이 실패하는 것보다 낫다."""
    body = _body('_coordination_joined')

    assert 'return False' in body
    assert "is True" in body, '참인 경우에만 묶인 것으로 본다'


def test_the_status_says_which_way_it_went():
    """단독으로 돌았는지 화면이 알 수 있어야 한다."""
    body = _body('_publish_status')

    assert '"coordination_enabled"' in body
    assert '"coordination_joined"' in body
