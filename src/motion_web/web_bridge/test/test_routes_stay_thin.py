"""길목은 받아서 넘기기만 한다 · §6-190 · §6-191 · §6-192

**길 하나당 줄 수를 세면 드러난다.**

    docs_routes      124줄 · 길 3개 · 길당 41.3   ← 목록·읽기·그림 꺼내기
    safety_routes     64줄 · 길 2개 · 길당 32.0   ← 정지 로직
    system_routes    304줄 · 길 19개 · 길당 16.0  ← 화면 조립기
    나머지                            길당 5~10

업무가 길목에 있으면 **HTTP 를 거치지 않고는 시험할 수 없다.**

특히 정지가 그랬다 · 이 저장소에서 가장 중요한 코드인데, 눌러서 확인하려면
실제로 장비를 세우고 프로그램을 다시 시작해야 한다 · 그러니 더더욱 HTTP
없이 시험할 수 있어야 했는데 그럴 수가 없었다.

**그리고 업무 함수가 `HTTPException` 을 던졌다** · 「그런 문서가 없다」는
업무의 사실이고, 그것을 404 로 옮기는 것은 길목의 일이다 · 섞여 있으면
화면 아닌 곳에서 그 기능을 쓸 수 없다.
"""

import ast
import re
from pathlib import Path

import pytest

ROUTES = Path(__file__).resolve().parents[1] / 'motion_web_bridge' / 'routes'

#: 길 하나당 **코드** 줄 수의 상한 · 지금 최대가 11.2 다
#:
#: 설명문을 빼고 센다 · 이 저장소는 「왜 이렇게 했나」를 길게 적으므로,
#: 전체 줄 수로 재면 잘 적은 파일이 벌을 받는다 · 문제는 **로직**이 길목에
#: 있는 것이지 설명이 긴 것이 아니다.
#:
#: 2026-09-19  docs 41.3 → 9.7 · safety 32.0 → 11.0 · system 16.0 → 11.2
PER_ROUTE_LIMIT = 12.0


def _files():
    return sorted(path for path in ROUTES.glob('*.py') if path.name != '__init__.py')


def _routes_in(text: str) -> int:
    return len(re.findall(r'@app\.(get|post|put|patch|delete|websocket)\(', text))


def code_lines(text: str) -> int:
    """설명문과 주석을 뺀 줄 수 · 남는 것이 로직이다."""
    prose = set()
    for node in ast.walk(ast.parse(text)):
        if (
            isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            prose.update(range(node.lineno, node.end_lineno + 1))
    return sum(
        1
        for number, line in enumerate(text.splitlines(), 1)
        if line.strip() and not line.strip().startswith('#') and number not in prose
    )


@pytest.mark.parametrize('path', _files(), ids=lambda p: p.name)
def test_no_route_file_carries_the_work(path):
    text = path.read_text(encoding='utf-8')
    count = _routes_in(text)
    if not count:
        pytest.skip('길이 없는 파일')
    lines = code_lines(text)
    per_route = lines / count

    assert per_route <= PER_ROUTE_LIMIT, (
        f'{path.name} 이 길 하나당 코드 {per_route:.1f}줄입니다 (코드 {lines}줄 · 길 {count}개)\n'
        '업무를 서비스로 옮기세요 · 길목은 받아서 넘기고 예외를 HTTP 로 바꿉니다'
    )


# --------------------------------------------------------------------------- #
# 정지 · HTTP 없이 부를 수 있어야 한다
# --------------------------------------------------------------------------- #

def test_stopping_can_be_called_without_http():
    from motion_web_bridge.safety_service import SafetyService

    assert callable(SafetyService.stop)


def test_the_stop_route_only_moves_it_off_the_event_loop():
    """길목이 하는 일은 하나 · 스레드로 옮겨 부르기 · §6-146

    정지는 로컬 노드에 HTTP 로도 묻는다 · 이벤트 루프 안에서 그대로 하면
    그 동안 웹 서버가 통째로 멈추고, 50ms 마다 오는 상태 조회가 굶어
    **정지시키려다 고장을 만든다.**
    """
    text = (ROUTES / 'safety_routes.py').read_text(encoding='utf-8')

    assert text.count('asyncio.to_thread(') == 2
    assert 'local_execution_blocker' not in text, '판단이 길목에 남아 있습니다'
    assert 'stop_now' not in text, '명령이 길목에 남아 있습니다'


# --------------------------------------------------------------------------- #
# 업무는 HTTP 를 모른다
# --------------------------------------------------------------------------- #

SERVICES = ['docs_service.py', 'safety_service.py', 'schedule_service.py']


@pytest.mark.parametrize('name', SERVICES)
def test_a_service_does_not_speak_http(name):
    """서비스가 `HTTPException` 을 던지면 화면 밖에서 쓸 수 없다."""
    source = (ROUTES.parent / name).read_text(encoding='utf-8')
    imports = [
        line.strip() for line in source.splitlines()
        if line.startswith(('import ', 'from '))
    ]

    # 들여오는 것만 본다 · 설명문에는 옛 이야기로 적혀 있다
    assert not [line for line in imports if 'fastapi' in line], (
        f'{name} 이 fastapi 를 들여옵니다: {imports}'
    )
    assert 'raise HTTPException' not in source


@pytest.mark.parametrize('name', ['docs_routes.py', 'schedule_routes.py'])
def test_the_route_turns_refusals_into_status_codes(name):
    """업무의 사실을 HTTP 로 옮기는 것은 길목의 일이다."""
    text = (ROUTES / name).read_text(encoding='utf-8')

    assert 'status_code=404' in text


# --------------------------------------------------------------------------- #
# 화면 조립 · HTTP 와 상관없다
# --------------------------------------------------------------------------- #

def test_the_page_composer_lives_outside_the_routes():
    from motion_web_bridge.index_composer import IndexComposer

    assert callable(IndexComposer.compose)

    text = (ROUTES / 'system_routes.py').read_text(encoding='utf-8')
    assert 'class IndexComposer' not in text
    assert '<!--#include' not in text, '조각 끼우는 규칙이 길목에 남아 있습니다'
