"""웹 요청 처리가 이벤트 루프를 막지 않는다 · §6-152

**화면을 보는 행위가 공연을 멈출 수 있었다.**

연동 노드는 그룹 실행 중 50ms 마다 `/api/coordination/local-status` 를 묻고,
0.5초 동안 못 받으면 참가 PC 이상으로 보고 **전체를 정지시킨다** · 그 조회가
지나는 길이 웹 서버의 이벤트 루프다.

그런데 처리기들이 디스크를 읽고 ROS 를 부르는 일을 루프 위에서 했다 · 사람이
웹 탭 하나를 누르면 조회가 몰려 루프가 막히고, 그 틈에 안전 타이머가 굶어
`GROUP_PARTICIPANT_FAILURE` 로 3대 연동이 통째로 섰다 · 실측으로 평상시
3.8ms 이던 응답이 475ms 로 뛰었다.

한 번 고치고도 또 멈췄다 · 그때 훑은 것이 `@app.get`·`@app.post` 뿐이라
**`@app.websocket` 이 남아 있었다** · 탭 하나가 초당 10번 66KB 스냅샷을 루프
위에서 만들고 있었다.

그래서 종류를 가리지 않고 본다 · 빠뜨린 한 곳이 다시 무대를 멈춘다.
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
ROUTE_FILES = sorted(
    (ROOT / 'motion_web' / 'web_bridge' / 'motion_web_bridge' / 'routes').glob('*.py')
) + [
    ROOT / 'motion_web' / 'web_bridge' / 'motion_web_bridge' / 'motion_studio_routes.py'
]

#: 처리기 안에서 이 이름들을 그냥 부르면 루프 위에서 도는 것이다
BLOCKING_CALL = re.compile(r'\b(?:bridge|service|store)[\w.]*\(')

#: 스레드로 넘겼거나(`to_thread`), 넘기는 문을 지나면(`await project_call`) 괜찮다
SAFE = ('to_thread', 'await project_call')

#: 어느 종류든 다 본다 · `websocket` 을 빠뜨려서 한 번 더 멈췄다
HANDLER = re.compile(r"@app\.(get|post|put|patch|delete|websocket)\(\s*'([^']+)'")


def _handlers(source: str):
    lines = source.splitlines()
    route = None
    for index, line in enumerate(lines):
        found = HANDLER.search(line)
        if found:
            route = f'{found.group(1).upper()} {found.group(2)}'
            continue
        if route and re.match(r'\s*(async )?def ', line):
            body, cursor = [], index + 1
            while cursor < len(lines) and (
                lines[cursor].startswith('        ') or not lines[cursor].strip()
            ):
                body.append(lines[cursor])
                cursor += 1
            yield route, '\n'.join(body)
            route = None


def _offenders(source: str):
    return [
        route for route, body in _handlers(source)
        if BLOCKING_CALL.search(body) and not any(mark in body for mark in SAFE)
    ]


@pytest.mark.parametrize('path', ROUTE_FILES, ids=lambda p: p.name)
def test_no_handler_works_on_the_event_loop(path):
    offenders = _offenders(path.read_text(encoding='utf-8'))
    assert offenders == [], (
        '이벤트 루프에서 일하는 처리기가 있습니다 · 그룹 실행이 통째로 멈출 수 '
        f'있습니다:\n  ' + '\n  '.join(offenders)
    )


def test_the_check_would_notice_the_websocket_again():
    """검사가 무력해지지 않았는지 · 실제로 겪은 그 모양을 넣어 본다."""
    regressed = """
    @app.websocket('/ws/status')
    async def websocket_status(websocket: WebSocket):
        await websocket.accept()
        while True:
            await websocket.send_json(bridge.snapshot())
"""
    assert _offenders(regressed) == ['WEBSOCKET /ws/status']


def test_the_check_accepts_work_moved_to_a_thread():
    """지나치게 빡빡해서 옳은 코드를 막지는 않는지."""
    fine = """
    @app.get('/api/status')
    async def status():
        return await asyncio.to_thread(bridge.snapshot)
"""
    assert _offenders(fine) == []


# 루프가 막힌 시간을 재는가 · §6-153
#
# 막혔다는 사실은 **밖에서는 안 보인다** · 요청이 늦게 처리될 뿐이고, 로그에는
# 아무것도 안 남는다 · 그래서 그룹이 멈춘 뒤에도 브리지 탓인지 아닌지 몰랐다.

def test_the_bridge_measures_how_long_the_loop_was_blocked():
    import asyncio
    import time as clock

    from motion_web_bridge import bridge_node

    said = []

    class FakeBridge:
        @staticmethod
        def get_logger():
            return type('L', (), {'warn': staticmethod(said.append)})()

    async def drive():
        task = asyncio.create_task(bridge_node._watch_event_loop_lag(FakeBridge()))
        await asyncio.sleep(0)
        # 루프를 실제로 막는다 · 재는 쪽이 이걸 알아채야 한다
        clock.sleep(bridge_node.EVENT_LOOP_LAG_PROBE_SEC
                    + bridge_node.EVENT_LOOP_LAG_WARN_SEC + 0.05)
        await asyncio.sleep(0.05)
        task.cancel()

    asyncio.run(drive())

    assert said, '루프가 막혔는데 아무 말도 안 했다'
    assert '루프 지연' in said[0]
    assert 'ms 막힘' in said[0]


def test_a_smooth_loop_stays_quiet():
    """멀쩡할 때 떠들면 진짜 막혔을 때의 기록이 묻힌다."""
    import asyncio

    from motion_web_bridge import bridge_node

    said = []

    class FakeBridge:
        @staticmethod
        def get_logger():
            return type('L', (), {'warn': staticmethod(said.append)})()

    async def drive():
        task = asyncio.create_task(bridge_node._watch_event_loop_lag(FakeBridge()))
        await asyncio.sleep(bridge_node.EVENT_LOOP_LAG_PROBE_SEC * 3)
        task.cancel()

    asyncio.run(drive())
    assert said == []


# `project_call` 을 스레드로 또 감싸지 않는다 · §6-155
#
# `project_call` 은 스스로 스레드에서 도는 **비동기** 함수다 · 그런데 이것을
# `asyncio.to_thread(project_call, ...)` 로 넘기면 코루틴이 그대로 돌아오고,
# 아무도 기다리지 않아 **500 이 난다**.
#
# 실제로 그렇게 깨졌다 · 모션 스튜디오 화면에서 레이어가 통째로 안 보였고,
# 「프로그램 재시작」·「모터 제어 재시작」·「실행 적용 해제」도 같이 죽어 있었다 ·
# 앞의 검사는 `to_thread` 라는 글자만 보고 통과시켰다.

def test_project_call_is_awaited_not_threaded():
    threaded = []
    for path in ROUTE_FILES:
        source = path.read_text(encoding='utf-8')
        for found in re.finditer(r'to_thread\(\s*\n?\s*project_call\b', source):
            line = source[:found.start()].count('\n') + 1
            threaded.append(f'{path.name}:{line}')
    assert threaded == [], (
        'project_call 을 to_thread 로 감쌌습니다 · 코루틴이 그대로 돌아와 '
        f'500 이 납니다:\n  ' + '\n  '.join(threaded)
    )


def test_the_check_would_notice_it_again():
    """검사가 무력해지지 않았는지 · 실제로 깨졌던 그 모양을 넣어 본다."""
    broken = "        return await asyncio.to_thread(project_call, something)"
    assert re.search(r'to_thread\(\s*\n?\s*project_call\b', broken)
