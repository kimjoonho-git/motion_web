import threading

from motion_studio.ros_gateway import StudioRosGateway


class GatewayHost:
    def __init__(self):
        self._lock = threading.RLock()
        self._run_results = {}
        self._midi_results = {}
        self._workspace_project_id = 'workspace-a'
        self._request_pub = object()
        self._midi_request_pub = object()
        self.published = []

    @staticmethod
    def _context_generation():
        return 7

    @staticmethod
    def _response_generation_matches(payload):
        return payload.get('project_generation') == 7

    def _publish_json(self, publisher, payload):
        self.published.append((publisher, payload))


def test_gateway_caches_only_current_generation_responses():
    host = GatewayHost()
    gateway = StudioRosGateway(host)

    gateway.accept_run_response({
        'request_id': 'current',
        'project_generation': 7,
        'success': True,
    })
    gateway.accept_run_response({
        'request_id': 'stale',
        'project_generation': 6,
        'success': True,
    })

    assert set(host._run_results) == {'current'}


def test_gateway_preserves_run_request_payload_contract(monkeypatch):
    host = GatewayHost()
    gateway = StudioRosGateway(host)
    monkeypatch.setattr('motion_studio.ros_gateway.time.time_ns', lambda: 123)
    host._run_results['studio-run-g7-123'] = {'success': True}

    result = gateway.request_run('stop', {'reason': 'user'}, 0.01)

    assert result == {'success': True}
    assert host.published == [(
        host._request_pub,
        {
            'request_id': 'studio-run-g7-123',
            'project_generation': 7,
            'command': 'stop',
            'payload': {'reason': 'user', 'project_generation': 7},
        },
    )]


def test_gateway_adds_project_identity_to_midi_requests(monkeypatch):
    host = GatewayHost()
    gateway = StudioRosGateway(host)
    monkeypatch.setattr('motion_studio.ros_gateway.time.time_ns', lambda: 456)
    host._midi_results['studio-midi-g7-456'] = {'success': True}

    result = gateway.request_midi('studio_recording_ready', {}, 0.01)

    assert result == {'success': True}
    assert host.published[0][1]['payload'] == {
        'project_id': 'workspace-a',
        'project_generation': 7,
    }
