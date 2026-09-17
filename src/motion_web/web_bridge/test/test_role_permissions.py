"""단독·마스터·슬레이브의 권한 계약 · §6-70.

원칙은 하나다 · **시작은 마스터만, 정지는 누구나.**

전에는 경로마다 달랐다 · 스케줄로 시작하는 길은 마스터만이었는데 손으로
시작하는 길은 참가한 PC 면 누구나였다. 같은 일에 권한이 다르면 여러 사람이
각자 앞의 PC 에서 눌렀을 때 그룹이 어느 명령을 따르는지 알 수 없다.
"""

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
    body = _body(SAFETY, '_stop_group_too')
    assert 'local_execution_blocker()' in body, '그룹 실행 중인지 보지 않는다'
    assert "'command': 'stop_now'" in body, '그룹 정지를 보내지 않는다'
    assert 'is_master' not in body, '정지는 역할과 무관해야 한다'
    for handler in ('safety_motion_stop', 'safety_emergency_stop'):
        assert '_stop_group_too(' in _body(SAFETY, handler), f'{handler} 가 그룹을 세우지 않는다'


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
