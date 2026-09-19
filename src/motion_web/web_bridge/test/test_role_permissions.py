"""단독·마스터·슬레이브의 권한 계약 · §6-70.

원칙은 하나다 · **시작은 마스터만, 정지는 누구나.**

전에는 경로마다 달랐다 · 스케줄로 시작하는 길은 마스터만이었는데 손으로
시작하는 길은 참가한 PC 면 누구나였다. 같은 일에 권한이 다르면 여러 사람이
각자 앞의 PC 에서 눌렀을 때 그룹이 어느 명령을 따르는지 알 수 없다.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
COORD_NODE = (
    ROOT / 'motion_coordination' / 'motion_coordination' / 'coordination_node.py'
).read_text(encoding='utf-8')
SAFETY = (
    ROOT / 'motion_web' / 'web_bridge' / 'motion_web_bridge' / 'routes' / 'safety_routes.py'
).read_text(encoding='utf-8')
RUN_MANAGER = (
    ROOT / 'motion_control_studio' / 'motion_control' / 'motion_runtime'
    / 'motion_runtime' / 'motion_run_manager.py'
).read_text(encoding='utf-8')


def _body(source: str, name: str) -> str:
    start = source.index(f'def {name}(')
    nxt = source.find('\n    def ', start)
    return source[start:nxt if nxt > 0 else len(source)]


def _inside(source: str, name: str) -> str:
    """정의 줄을 뺀 본문 · **이름이 적힌 그 줄은 빼야 한다**.

    안 빼면 `def _stop_group_too(` 가 `_stop_group_too(` 를 품어서, 그 함수가
    자기 자신에 닿는다고 나온다 · 그러면 실제로 호출을 지워도 검사가 통과한다.
    """
    body = _body(source, name)
    return body.split('\n', 1)[1] if '\n' in body else ''


def _reaches(source: str, name: str, needle: str, depth: int = 2) -> bool:
    """그 함수가 `needle` 에 닿는가 · **한 다리 건너 불러도** 인정한다.

    본문을 글자로 보는 검사라, 로직을 헬퍼로 옮기면 그대로 깨진다 · 실제로
    정지 경로를 스레드로 옮기면서(§6-146) 깨졌다 · 그렇다고 검사를 지우면
    "정지가 그룹을 안 세운다" 를 아무도 못 잡는다 · 부르는 곳을 따라간다.

    `to_thread(_stop_blocking, ...)` 처럼 **이름만 넘기는** 것도 호출로 본다.
    """
    inside = _inside(source, name)
    if needle in inside:
        return True
    if depth <= 0:
        return False
    for callee in sorted(set(re.findall(r'\b(_[a-z][a-z0-9_]*)\b', inside))):
        if callee == name:
            continue
        try:
            if _reaches(source, callee, needle, depth - 1):
                return True
        except ValueError:
            continue          # 이 파일에 없는 이름 · 남의 것이다
    return False


def test_only_the_master_starts_a_group_run():
    body = _body(COORD_NODE, '_start_group_execution')
    assert 'if not self._joined:' in body
    assert 'if not self._config.is_master:' in body, '슬레이브도 시작할 수 있다'
    assert '슬레이브' in body


def test_anyone_can_stop_a_group_run():
    """정지에 마스터 검사가 붙으면 안 된다 · 사고 시 누구나 세울 수 있어야 한다."""
    for command in ("'stop_after_cycle'", "'stop_now'"):
        assert command in COORD_NODE
    stop = COORD_NODE[COORD_NODE.index("elif command == 'stop_after_cycle'"):]
    stop = stop[:stop.index('def ', 1)] if 'def ' in stop else stop
    assert 'is_master' not in stop, '정지에 역할 제한이 붙었다'


def test_safety_stop_also_stops_the_group():
    """정지가 그룹도 세우는지 · **HTTP 없이 진짜로 부른다** · §6-191

    전에는 `safety_routes.py` 의 글자를 대조했다 · 정지 로직이 길목 안에
    있어서 그 방법밖에 없었다 · 이제 서비스가 따로 있으니 실제로 불러 본다.

    정지는 눌러서 확인하기 어려운 기능이다 · 눌러 보려면 장비를 세워야 하고,
    세우고 나면 프로그램을 다시 시작해야 한다 · 그래서 더더욱 여기서 확인한다.
    """
    from motion_web_bridge.safety_service import SafetyService

    sent = []

    class _Group:
        def __init__(self, running):
            self.running = running

        def local_execution_blocker(self):
            return '그룹 실행 중' if self.running else ''

        def request_control(self, payload):
            sent.append(payload)

    class _Bridge:
        def __init__(self, group):
            self.coordination = group

        def publish_safety_stop(self, emergency):
            sent.append(('로컬 정지', emergency))
            return 'req-1'

    # 그룹이 도는 중이면 그룹도 세운다
    result = SafetyService(_Bridge(_Group(running=True))).stop(
        emergency=False, kind='전체 동작 정지', message='보냈습니다',
    )
    assert ('로컬 정지', False) in sent, '이 PC 를 세우지 않는다'
    assert {'command': 'stop_now'} in sent, '그룹을 세우지 않는다'
    assert '그룹 실행도 함께 정지 요청' in result['message']
    # 이 PC 부터 세운다 · 그룹은 HTTP 왕복이라 느리다
    assert sent.index(('로컬 정지', False)) < sent.index({'command': 'stop_now'})

    # 그룹이 안 도는 중이면 로컬 정지로 충분하다
    sent.clear()
    SafetyService(_Bridge(_Group(running=False))).stop(
        emergency=True, kind='긴급 정지', message='보냈습니다',
    )
    assert sent == [('로컬 정지', True)]

    # 연동을 쓰지 않는 PC · 없다고 터지면 안 된다
    sent.clear()
    SafetyService(_Bridge(None)).stop(
        emergency=True, kind='긴급 정지', message='보냈습니다',
    )
    assert sent == [('로컬 정지', True)]


def test_stopping_never_asks_who_is_master():
    """정지는 역할과 무관하게 누구나 할 수 있어야 한다 · §6-70"""
    source = (
        ROOT / 'motion_web' / 'web_bridge' / 'motion_web_bridge' / 'safety_service.py'
    ).read_text(encoding='utf-8')

    assert 'is_master' not in source


def test_a_failed_group_stop_does_not_undo_the_local_stop():
    """그룹을 못 세웠다고 예외를 올리면, **이미 선 이 PC 도 실패로 보인다.**"""
    from motion_web_bridge.safety_service import SafetyService

    class _Broken:
        def local_execution_blocker(self):
            return '그룹 실행 중'

        def request_control(self, payload):
            raise OSError('연동 노드 응답 없음')

    class _Bridge:
        coordination = _Broken()

        def publish_safety_stop(self, emergency):
            return 'req-1'

    result = SafetyService(_Bridge()).stop(
        emergency=True, kind='긴급 정지', message='보냈습니다',
    )

    assert result['success'] is True, '이 PC 는 실제로 섰다'
    assert '그룹 정지 요청 실패' in result['message'], '실패를 숨기면 안 된다' 


def test_no_pc_revives_playback_on_boot():
    """부팅 때 스스로 시작하는 기능은 없앴다 · §6-134

    켜는 곳이 둘이었고(이 PC · 그룹) 서로 배타적이었다 · 연동을 켜면 로컬이
    스스로 꺼지고(`_coordination_enabled`), 그룹은 필수 PC 가 2대 미만이면
    안 떴다(`_drive_auto_play`) · 그래서 혼자 쓰는 PC 가 연동을 켜 두면
    아무것도 안 됐다 · 시작은 사람이 누르거나 스케줄이 시킨다.
    """
    assert '_coordination_enabled' not in RUN_MANAGER, '되살리기 판단이 남아 있다'
    assert '_automation_resume_pending' not in RUN_MANAGER, '복구 예약이 남아 있다'
    assert 'resume_pending' not in RUN_MANAGER
    body = _body(RUN_MANAGER, '_confirm_execution_context')
    assert 'automation' not in body, '실행 컨텍스트 확인이 자동 재생을 건드린다'

    coordination = (
        Path(__file__).resolve().parents[3]
        / 'motion_coordination' / 'motion_coordination' / 'coordination_node.py'
    ).read_text(encoding='utf-8')
    assert 'auto_play' not in coordination, '그룹 부팅 자동 재생이 남아 있다'
