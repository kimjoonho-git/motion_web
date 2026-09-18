"""거부를 성공으로 세지 않는다 · §6-147

실제로 겪은 고장이다 · 연동 PC 가 한 대뿐이라 스케줄이 점검마다 시도하고
매번 거부당했는데, 로그에는 이렇게 찍혔다.

    HTTP Request [...] success: {'success': False, 'message': '...2대 이상 필요합니다'}

`success` 라고 적고 바로 옆에 `'success': False` 가 붙은 자기모순이다 ·
HTTP 200 만 보고 본문을 안 봤기 때문이다 · 거부가 실패로 세어지지 않으니
알람도 화면 표시도 생기지 않았고, 상단 배지는 한 시간 내내 초록불이었다.
"""

import json
from types import SimpleNamespace

import motion_schedule.motion_schedule_node as schedule_node
from motion_schedule.motion_schedule_node import MotionScheduleNode


class _Logger:
    def __init__(self):
        self.lines = []

    def _say(self, message):
        self.lines.append(str(message))

    info = warning = error = debug = _say


class _Response:
    def __init__(self, payload):
        self._payload = payload

    def read(self):
        return json.dumps(self._payload).encode('utf-8')

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


def _node(monkeypatch, payload=None, *, boom=None):
    node = MotionScheduleNode.__new__(MotionScheduleNode)
    node._last_failure = {}
    node.logger = _Logger()
    node.get_logger = lambda: node.logger

    def fake_urlopen(_request, timeout=None):
        if boom is not None:
            raise boom
        return _Response(payload)

    monkeypatch.setattr(schedule_node.urllib.request, 'urlopen', fake_urlopen)
    return node


def test_a_rejection_is_a_failure(monkeypatch):
    """본문이 안 된다고 하면 안 된 것이다 · 200 이 왔다고 받아들여진 게 아니다."""
    node = _node(monkeypatch, {
        'success': False,
        'message': '그룹 연동에는 정상 연결된 PC가 2대 이상 필요합니다',
    })

    assert node._send_http_request('/api/coordination/control', {}) is False
    assert node._last_failure['count'] == 1
    assert '2대 이상' in node._last_failure['message']
    assert any('거부' in line for line in node.logger.lines), '조용히 넘어갔다'


def test_repeated_rejections_are_counted(monkeypatch):
    """한 번은 경합일 수 있다 · 계속이면 고장이다 · 화면이 둘을 갈라야 한다."""
    node = _node(monkeypatch, {'success': False, 'message': '안 됩니다'})

    for _ in range(3):
        node._send_http_request('/api/coordination/control', {})

    assert node._last_failure['count'] == 3


def test_being_accepted_clears_the_alarm(monkeypatch):
    """받아들여지면 경고를 거둔다 · 안 거두면 아무도 경고를 안 읽게 된다."""
    node = _node(monkeypatch, {'success': False, 'message': '안 됩니다'})
    node._send_http_request('/api/coordination/control', {})
    assert node._last_failure

    node = _node(monkeypatch, {'success': True, 'message': '시작'})
    node._last_failure = {'count': 4, 'message': '안 됩니다'}
    assert node._send_http_request('/api/coordination/control', {}) is True
    assert node._last_failure == {}


def test_a_transport_failure_is_remembered_too(monkeypatch):
    """브리지가 아예 대답을 안 해도 마찬가지다 · 시각은 지나가고 있다."""
    node = _node(monkeypatch, boom=OSError('연결 거부'))

    assert node._send_http_request('/api/motion-run/start', {}) is False
    assert node._last_failure['count'] == 1
    assert '연결 거부' in node._last_failure['message']


def test_a_reply_without_a_verdict_is_not_a_failure(monkeypatch):
    """`success` 를 안 적어 보내는 API 도 있다 · 그것까지 실패로 세면 안 된다."""
    node = _node(monkeypatch, {'status': 'ok'})

    assert node._send_http_request('/api/motion-run/start', {}) is True
    assert node._last_failure == {}


def test_the_screen_can_read_the_failure(monkeypatch):
    """로그에만 있으면 아무도 안 본다 · 상태에 실려야 화면까지 간다."""
    node = _node(monkeypatch, {'success': False, 'message': '안 됩니다'})
    node._send_http_request('/api/coordination/control', {})

    published = []
    node.store = SimpleNamespace(
        current_project_id='proj-a', list_schedules=lambda: [],
    )
    node.engine = SimpleNamespace(active=lambda _now, _items: None)
    node._is_master_pc = lambda: True
    node._run_mode = 'schedule'
    node.status_pub = SimpleNamespace(publish=lambda msg: published.append(msg.data))

    from datetime import datetime
    node._publish_status(datetime.now().astimezone())

    payload = json.loads(published[0])
    assert payload['last_failure']['count'] == 1
    assert '안 됩니다' in payload['last_failure']['message']


# 멈추면 얼마 만에 되살아나는가 · §6-149
#
# 1분이었다 · "전시·무대에 충분" 하다고 적어 뒀는데, 정작 1분이 아쉬운 순간이
# 공연 중에 멈췄을 때다 · 관객 앞에서 최대 1분을 죽어 있는다.

def test_the_gap_after_a_stop_is_short():
    """줄인 값이 다시 늘어나면 여기서 걸린다."""
    from motion_schedule.motion_schedule_node import RECONCILE_INTERVAL_SEC
    assert RECONCILE_INTERVAL_SEC <= 10.0


def test_the_screen_is_told_how_long_the_gap_is(monkeypatch):
    """화면이 「최대 N초 뒤」라고 말하려면 그 N 을 알아야 한다 · 지어내면 안 된다."""
    from datetime import datetime
    from motion_schedule.motion_schedule_node import RECONCILE_INTERVAL_SEC

    node = _node(monkeypatch, {'success': True})
    published = []
    node.store = SimpleNamespace(
        current_project_id='proj-a', list_schedules=lambda: [],
    )
    node.engine = SimpleNamespace(active=lambda _now, _items: None)
    node._is_master_pc = lambda: True
    node._run_mode = 'schedule'
    node.status_pub = SimpleNamespace(publish=lambda msg: published.append(msg.data))

    node._publish_status(datetime.now().astimezone())

    assert json.loads(published[0])['reconcile_interval_sec'] == RECONCILE_INTERVAL_SEC
