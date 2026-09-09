import asyncio
import json
import threading
from motion_web_bridge.manual_motor_commands import ManualMotorCommandService
from motion_web_bridge.bridge_node import MotionWebBridge, _safety_first_stop, create_app


def _manual_of(bridge, **overrides):
    """노드 스텁에 수동 명령 서비스를 붙인다 · §6-21로 노드에서 떨어져 나왔다."""
    service = getattr(bridge, '_manual', None)
    if service is None:
        service = ManualMotorCommandService(
            bridge,
            repository=getattr(bridge, 'project_repository', None),
            jog_publisher=getattr(bridge, '_jog_request_publisher', None),
            action_publisher=getattr(bridge, '_action_request_publisher', None),
            jog_result_topic=getattr(bridge, 'jog_result_topic', '/jog_result'),
            action_result_topic=getattr(bridge, 'action_result_topic', '/action_result'),
        )
        bridge._manual = service
    repository = getattr(bridge, 'project_repository', None)
    if repository is not None:
        service.repository = repository
    #: 스텁은 발행자를 나중에 꽂기도 한다 · 매번 최신 값을 따라간다
    for attr, field in (
        ('_jog_request_publisher', '_jog_request_publisher'),
        ('_action_request_publisher', '_action_request_publisher'),
    ):
        publisher = getattr(bridge, attr, None)
        if publisher is not None:
            setattr(service, field, publisher)
    for name, value in overrides.items():
        setattr(service, name, value)
    return service


class SafetyBridge:
    host = '127.0.0.1'
    port = 8000
    web_publish_hz = 10.0

    def __init__(self):
        self.calls = []

    def request_safety_stop(self, emergency):
        self.calls.append(('safety', emergency))
        return {'success': True, 'message': 'held'}

    def publish_safety_stop(self, emergency):
        self.calls.append(('safety_publish', emergency))
        return 'safety-request-1'

    def motion_run_stop(self):
        self.calls.append(('motion_run', 'stop'))
        return {'success': True, 'message': 'run stopped'}


def route_endpoint(app, path, method):
    return next(
        route.endpoint
        for route in app.routes
        if getattr(route, 'path', None) == path and method in getattr(route, 'methods', set())
    )


def test_motion_run_stop_holds_final_output_before_stopping_source():
    bridge = SafetyBridge()

    result = _safety_first_stop(bridge, bridge.motion_run_stop)

    assert bridge.calls == [('safety', False), ('motion_run', 'stop')]
    assert result['success'] is True
    assert result['safety_stop']['success'] is True


def test_safety_http_publishes_without_waiting_for_acknowledgement():
    bridge = SafetyBridge()
    endpoint = route_endpoint(create_app(bridge), '/api/safety/emergency-stop', 'POST')

    result = asyncio.run(endpoint())

    assert result['success'] is True
    assert result['acknowledgement_pending'] is True
    assert bridge.calls == [('safety_publish', True)]


def test_bridge_publishes_safety_stop_on_dedicated_topic():
    bridge = MotionWebBridge.__new__(MotionWebBridge)
    published = []

    class Publisher:
        def publish(self, message):
            published.append(json.loads(message.data))

    bridge._safety_request_publisher = Publisher()
    _manual_of(bridge).wait_for_jog_result = lambda request_id, timeout_sec: {
        'success': True,
        'message': 'stopped',
        'request_id': request_id,
    }
    bridge.snapshot = lambda: {}

    result = bridge.request_safety_stop(True)

    assert result['success'] is True
    assert len(published) == 1
    assert published[0]['command'] == 'safety_emergency_stop'


def test_midi_status_reports_final_output_blocked_while_emergency_is_latched():
    bridge = MotionWebBridge.__new__(MotionWebBridge)
    bridge._safety_status_lock = threading.Lock()
    bridge._safety_status = {
        'commands_blocked': True,
        'message': '긴급정지 잠김 · 전체 프로그램 재시작 필요',
    }

    result = bridge._safety_adjusted_midi_status({
        'device_connected': True,
        'motor_output_enabled': True,
    })

    assert result['device_connected'] is True
    assert result['motor_output_enabled'] is False
    assert result['motor_output_blocked_by_safety'] is True
    assert result['motor_output_block_reason'] == '긴급정지 잠김 · 전체 프로그램 재시작 필요'
