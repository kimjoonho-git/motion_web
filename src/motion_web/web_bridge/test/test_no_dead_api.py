"""서버가 내놓은 길은 누군가 다닌다 · §6-180

**아무도 안 부르는 API 가 다섯 개 있었다.**

    PUT  /api/midi-monitor/mapping          화면은 update_bank 를 쓴다
    POST /api/midi-monitor/banks/file/save  저장은 뱅크 수정이 알아서 한다
    GET  /api/motors/ethercat-aliases       쓰는 곳이 없었다
    POST /api/execution-context/apply       다른 길로 이미 돈다
    POST /api/motors/scan/cancel            **화면에 버튼이 없다**

앞의 넷은 지웠다 · 라우트도 그 뒤의 브리지 메서드도 서로만 부르고 있었다.

마지막 하나는 **남겼다** · 죽은 길이 아니라 **아직 안 붙인 길**이다 ·
모터 검색은 몇 분씩 걸리는데 시작하면 끝날 때까지 기다리는 수밖에 없다 ·
버튼만 붙이면 바로 쓸 수 있다.

죽은 길이 나쁜 이유는 둘이다 · 읽는 사람이 「이건 언제 쓰나」를 매번 묻게
되고, 고칠 때 거기까지 같이 고치게 된다.
"""

import re
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parents[4]
BRIDGE = WORKSPACE / 'src/motion_web/web_bridge/motion_web_bridge'
JS_DIR = WORKSPACE / 'src/motion_web/web_ui/static/js'

ROUTE = re.compile(r"@app\.(get|post|put|patch|delete|websocket)\('([^']+)'")

#: 화면이 안 불러도 되는 길 · 부르는 쪽이 따로 있다
ALLOWED = {
    # 연동 노드가 되돌아 부르는 길 · 브라우저가 아니다
    '/api/coordination/local-status',
    '/api/coordination/local-control',
    '/api/coordination/local-readiness',
    # 화면 자체를 내주는 길
    '/',
    '/static/{asset_path:path}',
    # 브라우저가 알아서 부르는 길 · 없으면 콘솔에 404 가 남아 진짜 오류가 묻힌다
    '/favicon.ico',
    # 아직 버튼을 안 붙인 길 · 지우지 말 것
    '/api/motors/scan/cancel',
}


def _declared():
    routes = {}
    for path in BRIDGE.rglob('*.py'):
        text = path.read_text(encoding='utf-8')
        for match in ROUTE.finditer(text):
            routes[match.group(2)] = path.name
    return routes


def _frontend():
    return '\n'.join(
        path.read_text(encoding='utf-8') for path in JS_DIR.glob('*.js')
    )


def _loose(text: str) -> str:
    """`{id}` 는 아무 글자나 · 주소 조각을 정규식으로."""
    escaped = re.escape(text).replace(r'\{', '{').replace(r'\}', '}')
    return re.sub(r'\{[^}]+\}', '[^\'"`]*', escaped)


def _is_called(route: str, js: str) -> bool:
    """화면은 주소를 세 가지 방법으로 만든다 · 셋 다 본다.

        통째로   request('GET', '/api/projects')
        앞자락   motionStudioRequest('/import', ...)      ← `/api/motion-studio` 생략
        뒷자락   `${projectFileUrl(...)}/download`        ← 앞이 함수로 조립됨
    """
    if re.search(_loose(route), js):
        return True

    # 앞자락을 떼고 부르는 것
    for prefix in ('/api/motion-studio', '/api/docs'):
        if route.startswith(prefix):
            tail = route[len(prefix):] or '/'
            if re.search(f"['`]{_loose(tail)}", js):
                return True

    # 앞이 함수로 조립되고 뒷자락만 글자로 남는 것
    tail = '/' + route.rsplit('/', 1)[-1]
    if '{' not in tail and len(tail) > 4 and re.search(f'}}{_loose(tail)}`', js):
        return True
    return False


def test_no_route_is_left_without_a_caller():
    js = _frontend()
    dead = sorted(
        f'{route}   ({where})'
        for route, where in _declared().items()
        if route not in ALLOWED and not _is_called(route, js)
    )
    assert dead == [], (
        '아무도 안 부르는 API 가 있습니다 · 지우거나, 왜 남기는지 ALLOWED 에 '
        f'적으세요:\n  ' + '\n  '.join(dead)
    )


def test_the_removed_routes_stay_removed():
    """지운 넷이 슬그머니 돌아오면 다시 죽은 길이 된다."""
    declared = _declared()
    for route in (
        '/api/midi-monitor/mapping',
        '/api/midi-monitor/banks/file/save',
        '/api/motors/ethercat-aliases',
        '/api/execution-context/apply',
        '/api/motion-studio/projects',
        '/api/motion-studio/projects/load',
    ):
        assert route not in declared, (
            f'{route} 가 돌아왔습니다 · 정말 쓰는 곳이 있다면 화면에서 부르세요'
        )


def test_the_methods_behind_them_are_gone_too():
    """라우트만 지우고 메서드를 두면 그게 또 죽은 코드다."""
    source = (BRIDGE / 'bridge_node.py').read_text(encoding='utf-8')
    for name in (
        'def save_midi_monitor_mapping(',
        'def save_midi_banks_to_file(',
        'def read_ethercat_aliases(',
    ):
        assert name not in source, f'뒤에 남은 메서드가 있습니다: {name}'


def test_scan_cancel_is_kept_on_purpose():
    """지우지 말 것 · 죽은 길이 아니라 아직 안 붙인 길이다."""
    source = (BRIDGE / 'routes/motor_routes.py').read_text(encoding='utf-8')

    assert "@app.post('/api/motors/scan/cancel')" in source
    assert '화면에 취소 버튼이 없다' in source, (
        '왜 남기는지가 적혀 있어야 합니다 · 안 그러면 다음 사람이 지웁니다'
    )
