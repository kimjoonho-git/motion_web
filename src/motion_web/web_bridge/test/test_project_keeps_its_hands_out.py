"""프로젝트 쪽은 브리지 속살을 만지지 않는다 · §6-183

**프로젝트를 건드리면 MIDI·모터·스튜디오가 딸려 왔다.**

`project_service` 가 브리지의 밑줄 붙은 칸 다섯을 직접 열었다 · 43회.

    _execution_context              12회  ← 남의 자물쇠를 잡았다 놓았다
    _motor_config                   11회  ← 남의 칸에 직접 값을 적었다
    _current_project_generation      9회
    _ensure_project_mutation_allowed 7회  ← 브리지를 돌아 제자리로 왔다
    _advance_project_generation      3회

가장 나빴던 것은 자물쇠다.

    bridge._execution_context._apply_lock.acquire()
    try:
        ...
    finally:
        bridge._execution_context._apply_lock.release()

두 겹 건너 남의 자물쇠를 손으로 잡는다 · 세 자리에 똑같이 적혀 있었고,
한 곳에서 `finally` 를 빠뜨리면 실행 컨텍스트가 **영영 잠긴다** ·
`reconcile()` 이 오류 없이 그냥 건너뛰기 때문에 아무도 모른다.
"""

import contextlib
import threading
from pathlib import Path

import pytest

from motion_web_bridge.bridge_node import MotionWebBridge
from motion_web_bridge.execution_context_service import ExecutionContextService
from motion_web_bridge.motor_config_service import MotorConfigService

SERVICE_SOURCE = (
    Path(__file__).resolve().parents[1]
    / 'motion_web_bridge' / 'project_service.py'
).read_text(encoding='utf-8')


# --------------------------------------------------------------------------- #
# 서랍이 닫혀 있는가
# --------------------------------------------------------------------------- #

def test_the_project_side_opens_no_private_drawer():
    """`self.bridge._...` 가 한 곳도 없어야 한다."""
    offenders = [
        f'{number}: {line.strip()}'
        for number, line in enumerate(SERVICE_SOURCE.splitlines(), 1)
        if 'self.bridge._' in line
    ]

    assert offenders == [], (
        '프로젝트 쪽이 브리지 속살을 만집니다 · 이름이 바뀌면 여기가 깨집니다:\n  '
        + '\n  '.join(offenders)
    )


def test_it_does_not_hold_someone_elses_lock():
    """자물쇠는 주인이 연다 · 여기서 잡으면 풀어줄 책임이 생긴다."""
    assert '_apply_lock' not in SERVICE_SOURCE


def test_the_guard_does_not_go_around_the_bridge_and_come_back():
    """`bridge._ensure_project_mutation_allowed` 는 이 객체를 되부른다.

    무엇을 보는지 · **부르는 모양**만 본다 · 파일 첫머리 설명에 옛 이름이
    역사로 적혀 있어서, 이름만 찾으면 그 설명에 걸린다.
    """
    assert 'self.bridge._ensure_project_mutation_allowed(' not in SERVICE_SOURCE
    assert 'self.ensure_mutation_allowed(' in SERVICE_SOURCE


# --------------------------------------------------------------------------- #
# 「프로젝트가 바뀐다」 · 순서가 전부다
# --------------------------------------------------------------------------- #

class _Context:
    """실행 컨텍스트 대역 · 무슨 일이 어떤 순서로 있었는지 적는다."""

    def __init__(self, log):
        self.log = log
        self._apply_lock = threading.Lock()

    @contextlib.contextmanager
    def paused_for_project_change(self):
        self.log.append('자물쇠 잠금')
        yield
        self.log.append('자물쇠 풀림')

    def invalidate_nodes(self, context_id=''):
        self.log.append('노드 무효화')


class _Bridge:
    """브리지 대역 · 진짜 메서드를 빌려 쓴다 (rclpy 없이)."""

    changing_project = MotionWebBridge.changing_project

    def __init__(self):
        self.log = []
        self._execution_context = _Context(self.log)

    def _advance_project_generation(self):
        self.log.append('세대 올림')
        return 2


def test_the_order_is_lock_then_generation_then_invalidate():
    """순서가 어긋나면 노드들이 옛 세대를 붙든 채 남는다.

    자물쇠를 먼저 잡아 재조정을 멈추고, 그 다음 세대를 올리고, 그 세대로
    노드를 무효화한다 · 반대로 하면 재조정이 두 세대 사이에 끼어든다.
    """
    bridge = _Bridge()

    with bridge.changing_project():
        bridge.log.append('할 일')

    assert bridge.log == [
        '자물쇠 잠금', '세대 올림', '노드 무효화', '할 일', '자물쇠 풀림',
    ]


def test_the_lock_is_released_even_when_the_work_blows_up():
    """풀어주지 않으면 실행 컨텍스트가 영영 잠긴다 · 조용히."""
    context = ExecutionContextService.__new__(ExecutionContextService)
    context._apply_lock = threading.Lock()

    with pytest.raises(ValueError):
        with context.paused_for_project_change():
            raise ValueError('프로젝트를 만들지 못했다')

    assert not context._apply_lock.locked(), '자물쇠가 잠긴 채 남았습니다'


# --------------------------------------------------------------------------- #
# 모터 축 파일 · 「비운다」를 일곱 곳이 알 필요 없다
# --------------------------------------------------------------------------- #

def _motor_config():
    service = MotorConfigService.__new__(MotorConfigService)
    service.selected = Path('/어딘가/옛파일.yaml')
    service.applied = Path('/어딘가/적용된것.yaml')
    return service


@pytest.mark.parametrize('nothing', [None, '', Path()])
def test_selecting_nothing_means_nothing_is_selected(nothing):
    """전에는 일곱 곳이 `Path()` 라는 표현을 똑같이 알아야 했다."""
    service = _motor_config()

    service.select_file(nothing)

    assert service.selected == Path()


def test_selecting_a_file_keeps_it():
    service = _motor_config()

    service.select_file(Path('/어딘가/새파일.yaml'))

    assert service.selected == Path('/어딘가/새파일.yaml')


def test_chosen_and_applied_are_not_the_same_thing():
    """`selected` 는 화면에서 고른 것 · `applied` 는 모터에 들어간 것."""
    service = _motor_config()

    service.select_file(Path('/어딘가/새파일.yaml'))

    assert service.applied_file() == Path('/어딘가/적용된것.yaml')
    assert service.selected_file() != service.applied_file()


# --------------------------------------------------------------------------- #
# 같은 프로젝트를 다시 고르는 것은 「바뀜」이 아니다
# --------------------------------------------------------------------------- #

def test_reselecting_the_same_project_does_not_bump_the_generation():
    """세대를 올리면 화면들이 통째로 새로고침된다 · 괜히 올리면 안 된다.

    조건부로 자물쇠를 잡던 코드를 `ExitStack` 으로 바꿨다 · 그때 조건이
    사라지면 같은 프로젝트를 다시 눌러도 화면이 튄다.
    """
    source = SERVICE_SOURCE
    start = source.index('def select_project(')
    body = source[start:source.index('\n    def ', start)]

    assert 'changing_project = (' in body, '바뀌는지 판단하지 않습니다'
    assert 'if changing_project:' in body, '조건 없이 세대를 올립니다'
    assert 'stack.enter_context(self.bridge.changing_project())' in body
