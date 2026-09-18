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


def test_slave_pc_cannot_own_schedules():
    """연동 슬레이브는 스케줄을 만들 수 없다 · §6-69

    슬레이브는 스케줄을 저장해도 발화하지 않는다 · `_on_timer_tick` 이 마스터가
    아니면 바로 돌아간다. 그런데 API 와 화면은 저장을 받아 줬다 · 돌지 않는
    스케줄이 조용히 쌓이고 마스터의 목록과도 따로 놀았다.
    """
    routes = (
        Path(__file__).resolve().parents[2]
        / 'web_bridge' / 'motion_web_bridge' / 'routes' / 'schedule_routes.py'
    ).read_text(encoding='utf-8')

    assert 'def _require_schedule_owner()' in routes

    def inside(name: str) -> str:
        """정의 줄을 뺀 본문 · 그 줄은 자기 이름을 품고 있어 검사를 무력하게 한다."""
        start = routes.index(f'def {name}(')
        nxt = routes.find('\n    def ', start)
        nxt2 = routes.find('\n    @app.', start)
        ends = [x for x in (nxt, nxt2) if x > 0]
        body = routes[start:min(ends) if ends else len(routes)]
        return body.split('\n', 1)[1] if '\n' in body else ''

    def reaches(name: str, needle: str, depth: int = 2) -> bool:
        """한 다리 건너 불러도 관문을 지난 것으로 본다 · §6-146

        조회를 스레드로 옮기면서 본문이 `_..._blocking` 으로 갈라졌다 · 글자만
        보면 깨지고, 검사를 지우면 "슬레이브가 스케줄을 만든다" 를 못 잡는다 ·
        `to_thread(_save_schedule_blocking, ...)` 처럼 이름만 넘기는 것도 센다.
        """
        body = inside(name)
        if needle in body:
            return True
        if depth <= 0:
            return False
        for callee in sorted(set(re.findall(r'\b(_[a-z][a-z0-9_]*)\b', body))):
            if callee == name:
                continue
            try:
                if reaches(callee, needle, depth - 1):
                    return True
            except ValueError:
                continue
        return False

    # 쓰기 네 곳이 모두 관문을 지난다 · 읽기는 열어 둔다
    for handler in ('save_schedule', 'delete_schedule', 'enable_schedule', 'disable_schedule'):
        assert reaches(handler, '_require_schedule_owner()'), f'{handler} 에 관문이 없다'
    for reader in ('get_schedule_list', 'get_schedule_status'):
        assert not reaches(reader, '_require_schedule_owner()'), (
            f'{reader} 는 열려 있어야 한다 · 슬레이브도 무엇이 걸려 있는지 볼 수 있어야 한다'
        )


def test_schedule_button_is_disabled_on_a_slave():
    manager = (
        Path(__file__).resolve().parents[2]
        / 'web_ui' / 'static' / 'js' / 'schedule_manager.js'
    ).read_text(encoding='utf-8')
    start = manager.index('updateStatusBadge()')
    body = manager[start:start + 1600]
    # 무엇을 잠글지는 `schedule_scope.js` 가 정한다 · 네 상태를 가른다 · §6-133
    # (연동 안 씀 · 마스터 · 마스터인데 빠짐 · 슬레이브)
    assert 'motionScheduleBadgeState(this.status)' in body, '상태 판단을 쓰지 않는다'
    assert 'button.disabled = !state.canEdit' in body, '슬레이브에서 버튼이 잠기지 않는다'
    assert 'state.blockedReason' in body, '왜 못 쓰는지 알려주지 않는다'

    scope = (
        Path(__file__).resolve().parents[2]
        / 'web_ui' / 'static' / 'js' / 'schedule_scope.js'
    ).read_text(encoding='utf-8')
    assert '마스터 PC 에서 설정' in scope, '슬레이브에게 어디서 설정하는지 알려주지 않는다'
    assert '시각이 되어도 실행되지 않습니다' in scope, '빠져 있을 때 조용히 실패한다'


def test_a_saved_schedule_remembers_which_timezone_it_was_born_in():
    """들고 나갔는데 시간대만 안 바뀐 경우를 잡는 지문 · §6-150

    NTP 는 절대 시각(UTC)만 맞춘다 · 시간대는 사람이 정하는 값이라 다른 나라에
    가서 네트워크에 붙여도 안 바뀐다 · 한국에서 만든 09:17 스케줄이 파리에서
    현지 02:17 에 돈다 · 시계는 맞아서 화면 어디에도 이상이 없다.

    **찍는 것은 이 PC 다** · 브라우저가 정하게 두면 한국에서 원격으로 파리 PC 를
    설정할 때 한국 시간대가 박힌다 · 어긋남을 잡으려던 값이 되레 어긋남을 만든다.
    """
    routes = (
        Path(__file__).resolve().parents[2]
        / 'web_bridge' / 'motion_web_bridge' / 'routes' / 'schedule_routes.py'
    ).read_text(encoding='utf-8')

    start = routes.index('def _save_schedule_blocking(')
    body = routes[start:routes.index('\n    def ', start)]
    assert 'saved_timezone' in body, '저장할 때 지문을 안 찍는다'
    assert 'local_clock.timezone_name()' in body, '지문을 이 PC 에서 안 가져온다'


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
