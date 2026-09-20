import asyncio
from unittest import mock
import threading
import json
import subprocess
import tempfile
from pathlib import Path
import time
from types import SimpleNamespace

import pytest
from std_msgs.msg import String

from motion_web_bridge.motor_config_service import MotorConfigService
from motion_web_bridge.execution_context_service import ExecutionContextService
from motion_web_bridge.manual_motor_commands import ManualMotorCommandService
from motion_web_bridge.motor_runtime_service import MotorRuntimeService
from motion_web_bridge.project_service import ProjectService
from motion_web_bridge.bridge_node import MotionWebBridge, create_app
from motion_web_bridge.motion_studio_session import MotionStudioSession
from motion_web_bridge.motor_event_log import MotorEventLog
from motion_web_bridge.scan_orchestrator import ScanOrchestrator
from motion_web_bridge.motion_studio_sync import MotionStudioSync
from motion_common import rpc
from motion_web_bridge import ethercat_project_compat, motor_config_rules


def _project_of(bridge):
    """노드 스텁에 프로젝트 서비스를 붙인다 · §6-23으로 노드에서 떨어져 나왔다."""
    service = getattr(bridge, '_project', None)
    if service is None:
        service = ProjectService(
            bridge,
            repository=getattr(bridge, 'project_repository', None),
            motion_projects_dir=getattr(bridge, 'motion_projects_dir', Path('.')),
        )
        bridge._project = service
    repository = getattr(bridge, 'project_repository', None)
    if repository is not None:
        service.repository = repository
    projects_dir = getattr(bridge, 'motion_projects_dir', None)
    if projects_dir is not None:
        service.motion_projects_dir = projects_dir
    return service


def _runtime_of(bridge):
    """노드 스텁에 모터 런타임 서비스를 붙인다 · §6-22로 노드에서 떨어져 나왔다."""
    service = getattr(bridge, '_motor_runtime', None)
    if service is None:
        service = MotorRuntimeService(
            bridge,
            project=_project_of(bridge),
            repository=getattr(bridge, 'project_repository', None),
            workspace_root=getattr(bridge, 'workspace_root', Path('.')),
        )
        bridge._motor_runtime = service
    repository = getattr(bridge, 'project_repository', None)
    if repository is not None:
        service.repository = repository
    return service


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


def _execution_context_of(bridge, **overrides):
    """노드 스텁에 실행 컨텍스트 서비스를 붙인다 · §6-20으로 노드에서 떨어져 나왔다."""
    service = getattr(bridge, '_execution_context', None)
    if service is None:
        service = ExecutionContextService(
            bridge,
            project=_project_of(bridge),
            repository=getattr(bridge, 'project_repository', None),
            workspace_root=getattr(bridge, 'workspace_root', Path('.')),
        )
        bridge._execution_context = service
    repository = getattr(bridge, 'project_repository', None)
    if repository is not None:
        service.repository = repository
    for name, value in overrides.items():
        setattr(service, name, value)
    return service


def _motor_config_of(bridge, **overrides):
    """노드 스텁에 모터 설정 서비스를 붙인다 · §6-19로 노드에서 떨어져 나왔다."""
    service = getattr(bridge, '_motor_config', None)
    if service is None:
        service = MotorConfigService(
            bridge,
            project=_project_of(bridge),
            runtime=_runtime_of(bridge),
            lifecycle_lock=getattr(
                bridge, '_motor_lifecycle_lock', None
            ) or threading.Lock(),
            repository=getattr(bridge, 'project_repository', None),
            workspace_root=getattr(bridge, 'workspace_root', Path('.')),
            selected=Path(),
            applied=Path(),
            restart_script=Path('restart_motion_monitor.sh'),
        )
        bridge._motor_config = service
    repository = getattr(bridge, 'project_repository', None)
    if repository is not None:
        service.repository = repository
    workspace_root = getattr(bridge, 'workspace_root', None)
    if workspace_root is not None:
        service.workspace_root = workspace_root
    for name, value in overrides.items():
        setattr(service, name, value)
    return service


@pytest.fixture(autouse=True)
def _restore_patched_module_functions():
    """`make_bridge`가 모듈 함수를 갈아끼우므로 테스트마다 되돌린다.

    `_runtime_service_status`는 노드 메서드였을 때 인스턴스에 직접 꽂아 쓰던
    이음매다(§6-13). 순수 모듈로 옮기면서 이음매도 모듈 함수로 옮겼다.
    """
    yield
    mock.patch.stopall()


def _memory_event_log():
    """파일에 쓰지 않는 로그 서비스 · 프로젝트 전환 기억만 검사한다(§6-17)."""
    return MotorEventLog(
        log_dir=Path(tempfile.mkdtemp(prefix='motor-events-')),
        retention_days=30,
        max_bytes=10 * 1024 * 1024,
        max_records=5000,
        max_files=14,
        repository=None,
        workspace_root=Path('.'),
        runtime_project_id=lambda: '',
        logger=lambda: None,
    )


def _scan_of(bridge, **overrides):
    """노드 스텁에 스캔 조율을 붙인다 · §6-18로 노드에서 떨어져 나왔다."""
    scan = getattr(bridge, '_scan', None)
    if scan is None:
        scan = ScanOrchestrator(
            bridge,
            project=_project_of(bridge),
            runtime=_runtime_of(bridge),
            lifecycle_lock=getattr(
                bridge, '_motor_lifecycle_lock', None
            ) or threading.Lock(),
            repository=getattr(bridge, 'project_repository', None),
            scan_client=None,
            scan_ac_servo_client=None,
            scan_dynamixel_client=None,
            scan_service='/motor/scan_all',
            scan_ac_servo_service='/motor/scan_ac_servo',
            scan_dynamixel_service='/motor/scan_dynamixel',
            load_motor_config=lambda: _motor_config_of(bridge).load(),
        )
        bridge._scan = scan
    #: 스텁은 저장소를 나중에 꽂기도 한다 · 매번 최신 값을 따라간다
    repository = getattr(bridge, 'project_repository', None)
    if repository is not None:
        scan.repository = repository
    for name, value in overrides.items():
        setattr(scan, name, value)
    return scan


class _StubTransport:
    """전송 계층 대역 · 노드 껍데기가 사라져 이 자리를 대신한다(§6-15)."""

    def __init__(self, request):
        self.request = request


def _install_studio_sync(bridge, *, prepare, request):
    """준비·전송 이음매를 동기화 서비스에 꽂는다.

    노드 껍데기를 되부르던 순환을 끊으면서(§6-15) 이음매도 서비스로 옮겼다.
    """

    sync = MotionStudioSync(
        bridge, bridge._motion_studio_session, _StubTransport(request)
    )
    sync.prepare = prepare
    bridge._motion_studio_sync_service = sync
    return sync


def _patch_runtime_service_status(value):
    mock.patch.object(
        motor_config_rules, 'runtime_service_status', lambda *_a, **_k: value
    ).start()



def operation_repository(selected_project_id):
    operation = {}

    class Runtime:
        """모터 실행 상태는 별도 객체가 갖는다 (§6-47)."""

        def begin_motor_operation(self, operation_type, phase, **_kwargs):
            operation.clear()
            operation.update({
                'operation_id': 'operation-1',
                'type': operation_type,
                'phase': phase,
                'status': 'running',
            })
            return dict(operation)

        def update_motor_operation(self, operation_id, phase, **_kwargs):
            assert operation_id == operation['operation_id']
            operation['phase'] = phase
            return dict(operation)

        def motor_operation_status(self):
            return dict(operation)

        def finish_motor_operation(self, operation_id, status, *, phase, **_kwargs):
            assert operation_id == operation['operation_id']
            operation.update({'status': status, 'phase': phase})
            return dict(operation)

    class Repository:
        runtime = Runtime()

        def selected_project_id(self):
            return str(selected_project_id())

    return Repository()


class ContextRepository:
    def selected_project_id(self):
        return 'project-1'

    def execution_context(self, _project_id):
        return {
            'version': 1,
            'project_id': 'project-1',
            'context_id': 'context-sha',
            'missing': [],
            'configuration_complete': True,
            'motor_applied': True,
            'files': {
                'motor_axes': {'name': 'motor.yaml', 'sha256': 'motor-sha', 'exists': True},
                'motion_axis_matching': {
                    'name': 'mapping.yaml', 'sha256': 'mapping-sha', 'exists': True,
                },
                'motions': {'name': '', 'sha256': '', 'exists': False},
                'layers': {'name': '', 'sha256': '', 'exists': False},
            },
        }

    def load_servo_alarm_policy(self, _project_id):
        return {'version': 1, 'overrides': {}}


def make_bridge():
    bridge = MotionWebBridge.__new__(MotionWebBridge)
    bridge._motion_studio_session = MotionStudioSession()
    bridge.project_repository = ContextRepository()
    _execution_context_of(bridge)._lock = threading.RLock()
    _execution_context_of(bridge)._apply_lock = threading.Lock()
    bridge._project_generation_lock = threading.Lock()
    bridge._project_generation = 1
    _execution_context_of(bridge)._status = {
        'state': 'starting', 'ready': False, 'context_id': '', 'nodes': {},
    }
    bridge._lock = threading.Lock()
    bridge._motor_event_log = _memory_event_log()
    bridge._jog_result_lock = threading.Lock()
    bridge._action_result_lock = threading.Lock()
    bridge._motion_mapping_lock = threading.Lock()
    bridge._motion_run_lock = threading.Lock()
    bridge._midi_monitor_lock = threading.Lock()
    bridge._motion_studio_session.lock = threading.Lock()
    bridge._motion_studio_editor_lock = threading.Lock()
    bridge._motion_state = {'generated_at': 1.0, 'last_motor_status_at': 1.0, 'motors': []}
    bridge._motion_state_received_at = 1.0
    _manual_of(bridge)._jog_store = rpc.ResultStore()
    _manual_of(bridge)._action_store = rpc.ResultStore()
    bridge._motion_mapping_store = rpc.ResultStore()
    bridge._motion_run_store = rpc.ResultStore()
    bridge._motion_run_status = {}
    bridge._midi_monitor_store = rpc.ResultStore()
    bridge._midi_monitor_status = {}
    bridge._motion_studio_session.store = rpc.ResultStore()
    bridge._motion_studio_session.status = {}
    bridge._motion_studio_session.editor_store = rpc.ResultStore()
    bridge._safety_request_publisher = type('Publisher', (), {
        'publish': lambda _self, _message: None,
    })()
    _manual_of(bridge).wait_for_jog_result = lambda request_id, **_kwargs: {
        'success': True,
        'request_id': request_id,
    }
    _project_of(bridge).runtime_project_id = lambda: 'project-1'
    _patch_runtime_service_status(
        {'phase': 'ready', 'message': 'motor runtime ready'}
    )

    def response(_command, payload, **_kwargs):
        return {
            'success': True,
            'project_id': 'project-1',
            'context_id': payload.get('context_id'),
            'project_generation': 1,
        }

    bridge._request_motion_mapping = response
    bridge._request_midi_monitor = response
    bridge._request_motion_run = response
    bridge._motion_studio_ros_bridge = _StubTransport(response)
    return bridge


def test_coordinator_allows_control_only_after_all_nodes_confirm_context():
    bridge = make_bridge()

    result = _execution_context_of(bridge).reconcile()

    assert result['state'] == 'ready'
    assert result['ready'] is True
    assert result['stored_equals_runtime'] is True
    assert result['context_id'] == 'context-sha'
    assert set(result['nodes']) == {
        'motion_mapping', 'midi_control', 'motion_run', 'motion_studio',
        'motor_runtime',
        'midi_control_confirm', 'motion_run_confirm', 'motion_studio_confirm',
    }


def test_motion_automation_commands_use_current_execution_context():
    bridge = make_bridge()
    _execution_context_of(bridge)._status = {
        'state': 'ready',
        'ready': True,
        'context_id': 'context-sha',
        'nodes': {},
    }
    calls = []

    def request(command, payload, **_kwargs):
        calls.append((command, dict(payload)))
        return {'success': True}

    bridge._request_motion_run = request
    bridge.motor_runtime_control_blocker = lambda: ''

    # 부팅 자동 재생을 빼면서 `start` · `reserve` · `disable` 통로도 함께
    # 없앴다 · §6-134 · 남은 것은 반복 방식 저장뿐이다
    assert bridge.motion_automation_configure({
        'repeat_mode': 'dwell',
        'dwell_sec': 3,
    })['success']
    assert [call[0] for call in calls] == ['automation_configure']
    assert not hasattr(bridge, 'motion_automation_start')
    assert not hasattr(bridge, 'motion_automation_reserve')
    assert not hasattr(bridge, 'motion_automation_disable')


def test_group_motion_commands_include_execution_context_id():
    bridge = make_bridge()
    _execution_context_of(bridge)._status = {
        'state': 'ready',
        'ready': True,
        'context_id': 'context-sha',
        'nodes': {},
    }
    bridge.motor_runtime_control_blocker = lambda: ''
    bridge._request_motion_run = MotionWebBridge._request_motion_run.__get__(
        bridge, MotionWebBridge,
    )
    published = []
    bridge._motion_run_request_publisher = type('Publisher', (), {
        'publish': lambda _self, message: published.append(json.loads(message.data)),
    })()
    bridge._wait_for_motion_run_result = lambda request_id, **_kwargs: {
        'success': True,
        'request_id': request_id,
        'status': {},
    }
    bridge.new_project_request_id = lambda _prefix: 'req-1'
    bridge.current_project_generation = lambda: 1

    assert bridge.motion_group_prepare({
        'execution_id': 'exec-a',
        'motion_file_id': 'motion.json',
        'mapping_file_id': 'mapping.yaml',
    })['success']
    assert bridge.motion_group_start_at({
        'execution_id': 'exec-a',
        'cycle_number': 1,
        'start_monotonic': 100.0,
    })['success']
    assert bridge.motion_group_initialize_at({
        'execution_id': 'exec-a',
        'cycle_number': 1,
        'initialize_monotonic': 100.0,
    })['success']
    payloads = [message['payload'] for message in published]
    assert [message['command'] for message in published] == [
        'group_prepare',
        'group_start_at',
        'group_initialize_at',
    ]
    assert all(payload.get('context_id') == 'context-sha' for payload in payloads)


def test_coordinator_establishes_persisted_generation_after_program_restart():
    bridge = make_bridge()
    bridge._supervisor_project_generation = 0
    published = []
    bridge._action_request_publisher = type('Publisher', (), {
        'publish': lambda _self, message: published.append(json.loads(message.data)),
    })()
    _manual_of(bridge).wait_for_action_result = lambda request_id, **_kwargs: {
        'success': True,
        'request_id': request_id,
        'project_generation': 1,
    }

    result = _execution_context_of(bridge).reconcile()

    assert result['state'] == 'ready'
    assert published == [{
        'request_id': published[0]['request_id'],
        'project_generation': 1,
        'command': 'project_generation_boundary',
    }]
    assert bridge._supervisor_project_generation == 1


def test_coordinator_does_not_enable_context_without_supervisor_generation_ack():
    bridge = make_bridge()
    bridge._supervisor_project_generation = 0
    bridge._action_request_publisher = type('Publisher', (), {
        'publish': lambda _self, _message: None,
    })()
    _manual_of(bridge).wait_for_action_result = lambda *_args, **_kwargs: None

    result = _execution_context_of(bridge).reconcile()

    assert result['state'] == 'waiting_motor_runtime'
    assert result['ready'] is False
    assert 'motor_runtime' in result['failures']


def test_range_recovery_flag_is_forwarded_to_motion_supervisor():
    bridge = make_bridge()
    published = []
    _manual_of(bridge)._motion_state_motor = lambda _axis: {
        'controller_index': 0,
        'motor_type': 'ac_servo',
        'state': 'detected',
        'servo_on': True,
        'fault': False,
    }
    bridge.new_project_request_id = lambda _prefix: 'recovery-1'
    bridge.current_project_generation = lambda: 1
    bridge._action_request_publisher = type('Publisher', (), {
        'publish': lambda _self, message: published.append(json.loads(message.data)),
    })()
    _manual_of(bridge).wait_for_action_result = lambda _request_id: {
        'success': True,
        'message': 'started',
    }
    bridge.snapshot = lambda: {}

    result = _manual_of(bridge).ac_servo_action(
        0,
        -1000.0,
        range_recovery=True,
    )

    assert result['success'] is True
    assert published == [{
        'request_id': 'recovery-1',
        'project_generation': 1,
        'command': 'ac_servo_absolute_move',
        'axis': 0,
        'target_deg': -1000.0,
        'range_recovery': True,
    }]


def test_frequent_status_read_does_not_rehash_project_files():
    bridge = make_bridge()
    calls = []
    original = bridge.project_repository.execution_context

    def counted(project_id):
        calls.append(project_id)
        return original(project_id)

    bridge.project_repository.execution_context = counted
    _execution_context_of(bridge)._status = {
        'state': 'ready',
        'ready': True,
        'project_id': 'project-1',
        'context_id': 'context-sha',
        'context': original('project-1'),
        'nodes': {},
    }
    calls.clear()

    status = _execution_context_of(bridge).status(validate_files=False)

    assert status['ready'] is True
    assert calls == []


def test_snapshot_reads_motor_operation_without_reconciling_it(tmp_path):
    bridge = MotionWebBridge.__new__(MotionWebBridge)
    bridge._motion_studio_session = MotionStudioSession()
    bridge._lock = threading.Lock()
    bridge._motion_state = None
    bridge._motion_state_received_at = None
    bridge._motion_value_lock = threading.Lock()
    bridge._motion_value_state = {}
    bridge._motion_run_lock = threading.Lock()
    bridge._motion_run_status = {}
    bridge._midi_monitor_lock = threading.Lock()
    bridge._midi_monitor_status = {}
    bridge._motion_studio_session.lock = threading.Lock()
    bridge._motion_studio_session.status = {}
    bridge._safety_status_lock = threading.Lock()
    bridge._safety_status = {}
    bridge._bridge_instance_id = 'bridge-1'
    bridge._bridge_started_at = 1.0
    bridge.workspace_root = tmp_path / 'workspace'
    bridge.motion_projects_dir = tmp_path / 'workspace' / 'motion_projects'
    bridge.motion_state_topic = '/motion_state'
    bridge.max_jog_delta_deg = 360.0
    bridge._web_access = {}
    _patch_runtime_service_status({'phase': 'ready'})
    _execution_context_of(bridge).status = lambda **_kwargs: {'ready': True}
    bridge._safety_adjusted_midi_status = lambda status, **_kwargs: status
    bridge.current_project_generation = lambda: 1
    _project_of(bridge).runtime_project_id_from_path = lambda _selected='': 'project-a'
    _runtime_of(bridge).reconcile_operation_status = lambda *_args: (
        pytest.fail('snapshot must be read-only')
    )
    bridge.project_repository = type('Repository', (), {
        # 모터 실행 상태는 별도 객체가 갖는다 (§6-47)
        'runtime': type('Runtime', (), {
            'motor_operation_status': lambda _self: {
            'operation_id': 'operation-1',
            'status': 'running',
            'phase': 'verifying',
        },
        })(),
        'selected_project_id': lambda _self: 'project-a',
    })()

    result = bridge.snapshot()

    assert result['motor_operation']['operation_id'] == 'operation-1'
    assert result['motor_operation']['phase'] == 'verifying'
    assert result['system_info']['hostname']
    assert result['system_info']['workspace_root'] == str(bridge.workspace_root.resolve())
    assert result['system_info']['motion_projects_dir'] == str(
        bridge.motion_projects_dir.resolve()
    )


def test_motor_operation_coordinator_is_the_reconcile_writer():
    bridge = MotionWebBridge.__new__(MotionWebBridge)
    bridge._motion_studio_session = MotionStudioSession()
    bridge._motor_operation_reconcile_lock = threading.Lock()
    bridge._lock = threading.Lock()
    bridge._motion_state = {'motors': []}
    bridge._motion_state_received_at = time.time()
    _patch_runtime_service_status({'phase': 'ready'})
    _execution_context_of(bridge).status = lambda **_kwargs: {'ready': True}
    calls = []
    _runtime_of(bridge).reconcile_operation_status = (
        lambda runtime, motion, context: calls.append(
            (runtime, motion, context)
        )
    )

    _runtime_of(bridge).reconcile_callback()

    assert len(calls) == 1
    assert calls[0][0]['phase'] == 'ready'
    assert calls[0][2]['ready'] is True


def test_high_frequency_runtime_owner_check_is_independent_from_selection(tmp_path):
    bridge = make_bridge()
    bridge.motion_projects_dir = tmp_path
    _motor_config_of(bridge).applied = (
        tmp_path / 'project-1' / 'runtime' / 'applied_motor_config.yaml'
    )
    bridge.project_repository.get_project = lambda _project_id: (_ for _ in ()).throw(
        AssertionError('high-frequency status path must not parse project files')
    )

    # 인자를 받지 않는다 · 답이 고른 프로젝트와 무관하다는 것이 구조로 드러난다
    assert _project_of(bridge).runtime_project_id_from_path() == 'project-1'

    # 고른 프로젝트를 바꿔도 답은 그대로다 · 적용된 파일 경로에서 읽기 때문
    bridge.project_repository.selected_project_id = lambda: 'project-2'
    assert _project_of(bridge).runtime_project_id_from_path() == 'project-1'


def test_status_websocket_reads_disconnect_and_finishes():
    class FakeBridge:
        web_publish_hz = 10.0

        @staticmethod
        def snapshot():
            return {'bridge_state': 'ok'}

    class FakeWebSocket:
        accepted = False
        sent = []

        async def accept(self):
            self.accepted = True

        async def send_text(self, payload):
            # 스냅샷을 만드는 일도, 글자로 바꾸는 일도 스레드에서 한다 · §6-152
            # 루프에서 하면 탭 하나가 초당 10번 서버를 막는다
            self.sent.append(json.loads(payload))

        @staticmethod
        async def receive():
            return {'type': 'websocket.disconnect'}

    app = create_app(FakeBridge())
    endpoint = next(
        route.endpoint for route in app.routes
        if getattr(route, 'path', '') == '/ws/status'
    )
    websocket = FakeWebSocket()

    asyncio.run(endpoint(websocket))

    assert websocket.accepted is True
    assert websocket.sent == [{'bridge_state': 'ok'}]


def test_web_ui_files_are_not_served_from_stale_browser_cache():
    app = create_app(make_bridge())
    index_endpoint = next(
        route.endpoint for route in app.routes
        if getattr(route, 'path', '') == '/'
    )
    static_endpoint = next(
        route.endpoint for route in app.routes
        if getattr(route, 'path', '') == '/static/{asset_path:path}'
    )

    index_response = asyncio.run(index_endpoint())
    script_response = asyncio.run(static_endpoint('app.js'))

    # `no-store`는 캐시 자체를 막아 함께 나가는 ETag를 무의미하게 만든다.
    # `no-cache`는 **매번 물어보게** 하므로 낡은 화면 위험은 같다 · §6-42
    assert index_response.headers['cache-control'] == 'no-cache'
    assert script_response.headers['cache-control'] == 'no-cache'
    for response in (index_response, script_response):
        assert response.headers['etag']
        assert 'max-age' not in response.headers['cache-control']
        assert 'immutable' not in response.headers['cache-control']


def test_unchanged_web_ui_file_answers_304_without_body():
    """바뀌지 않았으면 본문을 다시 보내지 않는다 · §6-42."""
    app = create_app(make_bridge())
    static_endpoint = next(
        route.endpoint for route in app.routes
        if getattr(route, 'path', '') == '/static/{asset_path:path}'
    )

    first = asyncio.run(static_endpoint('app.js'))
    etag = first.headers['etag']

    repeat = asyncio.run(static_endpoint(
        'app.js',
        SimpleNamespace(headers={'if-none-match': etag}),
    ))

    assert repeat.status_code == 304
    assert repeat.body == b''
    assert repeat.headers['etag'] == etag

    stale = asyncio.run(static_endpoint(
        'app.js',
        SimpleNamespace(headers={'if-none-match': 'other-etag'}),
    ))

    assert stale.status_code == 200


def test_coordinator_keeps_control_blocked_when_one_node_does_not_confirm():
    bridge = make_bridge()
    bridge._request_motion_run = lambda command, payload, **_kwargs: (
        {'success': True, 'project_id': 'project-1', 'context_id': payload.get('context_id')}
        if command == 'invalidate_context'
        else {'success': False, 'message': 'node unavailable'}
    )

    result = _execution_context_of(bridge).reconcile()

    assert result['state'] == 'waiting_nodes'
    assert result['ready'] is False
    assert 'motion_run' in result['failures']


def test_coordinator_accepts_midi_snapshot_with_nested_context_acknowledgement():
    bridge = make_bridge()
    default_response = bridge._request_midi_monitor

    def midi_response(command, payload, **kwargs):
        response = default_response(command, payload, **kwargs)
        if command in {'select_project', 'confirm_context'}:
            response.pop('context_id', None)
            response.pop('project_id', None)
            response['execution_context'] = {
                'context_id': payload['context_id'],
                'project_id': 'project-1',
                'project_generation': 1,
            }
        return response

    bridge._request_midi_monitor = midi_response

    result = _execution_context_of(bridge).reconcile()

    assert result['state'] == 'ready'
    assert result['ready'] is True


def test_coordinator_accepts_studio_status_with_nested_context_acknowledgement():
    bridge = make_bridge()
    default_response = bridge._motion_studio_ros_bridge.request

    def studio_response(command, payload, **kwargs):
        response = default_response(command, payload, **kwargs)
        if command == 'confirm_context':
            response.pop('context_id', None)
            response.pop('project_id', None)
            response['status'] = {
                'execution_context': {
                    'context_id': payload['context_id'],
                    'project_id': 'project-1',
                    'project_generation': 1,
                    'ready': True,
                },
            }
        return response

    bridge._motion_studio_ros_bridge = _StubTransport(studio_response)

    result = _execution_context_of(bridge).reconcile()

    assert result['state'] == 'ready'
    assert result['ready'] is True


def test_coordinator_blocks_and_invalidates_when_required_file_is_missing():
    bridge = make_bridge()
    context = bridge.project_repository.execution_context('project-1')
    context['missing'] = ['motion_axis_matching']
    context['configuration_complete'] = False
    bridge.project_repository.execution_context = lambda _project_id: context
    invalidations = []
    _execution_context_of(bridge).invalidate_nodes = lambda context_id='': invalidations.append(context_id)

    result = _execution_context_of(bridge).reconcile()

    assert result['state'] == 'configuration_required'
    assert result['ready'] is False
    assert result['missing'] == ['motion_axis_matching']
    assert invalidations == ['context-sha']


def test_coordinator_blocks_after_node_apply_until_motor_config_is_applied():
    bridge = make_bridge()
    context = bridge.project_repository.execution_context('project-1')
    context['motor_applied'] = False
    bridge.project_repository.execution_context = lambda _project_id: context
    invalidations = []
    midi_commands = []
    default_midi_response = bridge._request_midi_monitor
    _execution_context_of(bridge).invalidate_nodes = lambda context_id='': invalidations.append(context_id)

    def midi_response(command, payload, **kwargs):
        midi_commands.append(command)
        return default_midi_response(command, payload, **kwargs)

    bridge._request_midi_monitor = midi_response

    result = _execution_context_of(bridge).reconcile()

    assert result['state'] == 'motor_apply_required'
    assert result['ready'] is False
    assert result['failures'] == {}
    assert midi_commands == ['select_project']
    assert invalidations == []
    assert result['nodes']['midi_control']['project_id'] == 'project-1'


def test_coordinator_waits_for_current_project_motor_runtime():
    bridge = make_bridge()
    _patch_runtime_service_status(
        {'phase': 'waiting_motor_state', 'message': 'motor state waiting'}
    )

    result = _execution_context_of(bridge).reconcile()

    assert result['state'] == 'waiting_motor_runtime'
    assert result['ready'] is False
    assert result['failures'] == {'motor_runtime': 'motor state waiting'}


def test_project_change_deletes_previous_project_values_from_bridge_memory():
    bridge = MotionWebBridge.__new__(MotionWebBridge)
    bridge._motion_studio_session = MotionStudioSession()
    bridge._lock = threading.Lock()
    bridge._motor_event_log = _memory_event_log()
    bridge._jog_result_lock = threading.Lock()
    bridge._action_result_lock = threading.Lock()
    bridge._motion_mapping_lock = threading.Lock()
    bridge._motion_run_lock = threading.Lock()
    bridge._midi_monitor_lock = threading.Lock()
    bridge._motion_studio_session.lock = threading.Lock()
    bridge._motion_studio_editor_lock = threading.Lock()
    bridge._motion_state = {'motors': [{'alias': 403}]}
    bridge._motion_state_received_at = 1.0
    bridge._motor_event_log._active_motor_errors = {'0': 'old-error'}
    bridge._motor_event_log._last_motion_run_state = 'running'
    _manual_of(bridge)._jog_store = rpc.ResultStore()
    _manual_of(bridge)._jog_store.store('old', {'success': True})
    _manual_of(bridge)._action_store = rpc.ResultStore()
    _manual_of(bridge)._action_store.store('old', {'success': True})
    bridge._motion_mapping_store = rpc.ResultStore()
    bridge._motion_mapping_store.store('old', {'project_id': 'old-project'})
    bridge._motion_run_store = rpc.ResultStore()
    bridge._motion_run_store.store('old', {'project_id': 'old-project'})
    bridge._motion_run_status = {'project_id': 'old-project', 'axes': [1]}
    bridge._midi_monitor_store = rpc.ResultStore()
    bridge._midi_monitor_store.store('old', {'project_id': 'old-project'})
    bridge._midi_monitor_status = {'project_id': 'old-project', 'banks': [1]}
    bridge._motion_studio_session.store = rpc.ResultStore()
    bridge._motion_studio_session.store.store('old', {'project_id': 'old-project'})
    bridge._motion_studio_session.status = {'project_id': 'old-project', 'project': {}}
    bridge._motion_studio_session.editor_store = rpc.ResultStore()
    bridge._motion_studio_session.editor_store.store('old', {'project_id': 'old-project'})

    _project_of(bridge).clear_scoped_memory()

    assert bridge._motion_state is None
    assert bridge._motion_state_received_at is None
    assert bridge._motor_event_log._active_motor_errors == {}
    assert bridge._motor_event_log._last_motion_run_state is None
    assert _manual_of(bridge)._jog_store.pending_count() == 0
    assert _manual_of(bridge)._action_store.pending_count() == 0
    assert bridge._motion_mapping_store.pending_count() == 0
    assert bridge._motion_run_store.pending_count() == 0
    assert bridge._motion_run_status == {}
    assert bridge._midi_monitor_store.pending_count() == 0
    assert bridge._midi_monitor_status == {}
    assert bridge._motion_studio_session.store.pending_count() == 0
    assert bridge._motion_studio_session.status == {}
    assert bridge._motion_studio_session.editor_store.pending_count() == 0


def test_previous_runtime_motor_state_is_not_cached_after_project_change():
    bridge = MotionWebBridge.__new__(MotionWebBridge)
    bridge._motion_studio_session = MotionStudioSession()
    bridge._lock = threading.Lock()
    bridge._motion_state = None
    bridge._motion_state_received_at = None
    bridge.project_repository = type('Repository', (), {
        'selected_project_id': lambda _self: 'new-project',
    })()
    _project_of(bridge).selected_owns_runtime = lambda: True
    bridge._record_motor_error_transitions = lambda _payload: None

    bridge._motion_state_callback(String(data=json.dumps({
        'project_id': 'old-project',
        'motors': [{'controller_index': 0, 'alias': 403}],
    })))

    assert bridge._motion_state is None
    assert bridge._motion_state_received_at is None


def test_scan_result_is_discarded_if_project_changes_while_scanning():
    bridge = MotionWebBridge.__new__(MotionWebBridge)
    bridge._motion_studio_session = MotionStudioSession()
    selected = {'project_id': 'project-a'}
    bridge.project_repository = operation_repository(lambda: selected['project_id'])
    bridge.snapshot = lambda: {}
    bridge.get_logger = lambda: type('Logger', (), {'warn': lambda *_args: None})()

    class Future:
        def done(self):
            return True

        def result(self):
            selected['project_id'] = 'project-b'
            return type('Response', (), {
                'success': True,
                'message': '{"slaves": [{"position": 0}]}',
            })()

    client = type('Client', (), {
        'wait_for_service': lambda _self, **_kwargs: True,
        'call_async': lambda _self, _request: Future(),
    })()

    result = _scan_of(bridge)._call_service(client, '/scan', 1.0)

    assert result['success'] is False
    assert result['scan'] is None
    assert result['project_id'] == 'project-b'


def test_scan_request_is_rejected_while_another_motor_type_scan_is_running():
    bridge = MotionWebBridge.__new__(MotionWebBridge)
    bridge._motion_studio_session = MotionStudioSession()
    _scan_of(bridge)._scan_request_lock.acquire()
    bridge._project_generation_lock = threading.Lock()
    bridge._project_generation = 3
    bridge.project_repository = operation_repository(lambda: 'project-a')
    bridge.snapshot = lambda: {}

    class Client:
        def wait_for_service(self, **_kwargs):
            raise AssertionError('busy scan must not call another ROS scan service')

    result = _scan_of(bridge)._call_service(Client(), '/motor/scan_dynamixel', 1.0)

    assert result['success'] is False
    assert result['scan'] is None
    assert result['project_id'] == 'project-a'
    assert result['project_generation'] == 3
    assert '다른 모터 검색이 진행 중' in result['message']


def test_physical_scan_is_allowed_without_a_selected_project():
    bridge = MotionWebBridge.__new__(MotionWebBridge)
    bridge._motion_studio_session = MotionStudioSession()
    bridge._motor_scan_request_lock = threading.Lock()
    bridge._project_generation_lock = threading.Lock()
    bridge._project_generation = 0
    bridge.project_repository = operation_repository(lambda: '')
    bridge.snapshot = lambda: {}
    bridge.get_logger = lambda: type('Logger', (), {'warn': lambda *_args: None})()

    class Future:
        def done(self):
            return True

        def result(self):
            return type('Response', (), {
                'success': True,
                'message': '{"scan_id":"physical-1","scan_complete":true}',
            })()

    class Client:
        def wait_for_service(self, **_kwargs):
            return True

        def call_async(self, _request):
            return Future()

    result = _scan_of(bridge)._call_service(Client(), '/motor/scan_ac_servo', 1.0)

    assert result['success'] is True
    assert result['project_id'] == ''
    assert result['project_generation'] == 0
    assert result['scan']['scan_id'] == 'physical-1'
    assert result['message'] == '모터 검색 완료 · scan_id physical-1'


def test_scan_result_message_keeps_evidence_out_of_operation_text():
    message = motor_config_rules.scan_result_message(
        True,
        {
            'scan_id': 'physical-5',
            'ethercat_scan': {
                'complete': True,
                'slaves_count': 5,
                'slaves': [{'serial_number': index} for index in range(5)],
            },
            'dynamixel_scan': {'skipped': True},
        },
        '{"large":"raw response"}',
    )

    assert message == '모터 검색 완료 · AC Servo 5축 · scan_id physical-5'
    assert 'serial_number' not in message


def test_scan_result_message_preserves_partial_outcome():
    message = motor_config_rules.scan_result_message(
        False,
        {
            'scan_id': 'mixed-1',
            'ethercat_scan': {
                'complete': True,
                'slaves_count': 5,
            },
            'dynamixel_scan': {
                'complete': False,
                'devices_count': 0,
                'error': 'serial port unavailable',
            },
            'scan_errors': [{
                'transport': 'dynamixel',
                'message': 'serial port unavailable',
            }],
        },
        'raw failure',
    )

    assert message.startswith('모터 검색 부분 완료')
    assert 'AC Servo 5축' in message
    assert 'Dynamixel 0축' in message


def test_an_unused_disconnected_master_does_not_make_the_scan_partial():
    bridge = MotionWebBridge.__new__(MotionWebBridge)
    bridge._motion_studio_session = MotionStudioSession()
    _motor_config_of(bridge).load = lambda: {
        'success': True,
        'registry': {
            'motors': [
                {
                    'axis': axis,
                    'enabled': True,
                    'transport': 'ethercat',
                    'identity': {
                        'ethercat_master_index': 0,
                        'vendor_id': 0x66F,
                        'product_code': 0x60380004,
                        'serial_number': 100 + axis,
                    },
                    'config': {
                        'controller_index': axis,
                        'ethercat_master_index': 0,
                        'position': axis,
                        'alias': 0,
                    },
                }
                for axis in range(5)
            ],
        },
    }
    scan = {
        'scan_id': 'single-project-dual-host',
        'ethercat_scan': {
            'complete': False,
            'slaves_count': 5,
            'masters': [
                {'master_index': 0, 'complete': True, 'slaves_count': 5},
                {
                    'master_index': 1,
                    'complete': False,
                    'slaves_count': 0,
                    'error': '재스캔 후 응답한 Slave가 없습니다',
                },
            ],
            'slaves': [
                {
                    'master_index': 0,
                    'slave_position': axis,
                    'vendor_id': 0x66F,
                    'product_code': 0x60380004,
                    'serial_number': 100 + axis,
                    'ethercat_alias': 0,
                    'direct_read_complete': True,
                }
                for axis in range(5)
            ],
            'error': 'Master 1: 재스캔 후 응답한 Slave가 없습니다',
        },
        'dynamixel_scan': {'skipped': True},
        'scan_errors': [{
            'transport': 'ethercat',
            'message': 'Master 1: 재스캔 후 응답한 Slave가 없습니다',
        }],
    }

    ethercat_project_compat.annotate_ethercat_project_compatibility(
        scan, _motor_config_of(bridge).load
    )

    comparison = scan['project_comparison']['ethercat_project']
    assert comparison['compatible'] is True
    assert comparison['required_master_indices'] == [0]
    assert comparison['unused_registered_master_indices'] == [1]
    # **프로젝트가 쓰는 Master 0 은 다 응답했다** · 그러면 완료다 · §6-198
    #
    # 전에는 「부분 완료」였다 · 안 쓰는 Master 1 하나가 비어서 `complete` 이
    # False 였기 때문이다 · 같은 응답이 스스로 「미사용 Master 1 미연결 허용」
    # 이라고 말하면서 버튼엔 「부분 완료」를 띄웠다 · 사용자는 뭔가 덜 된 줄
    # 안다.
    assert motor_config_rules.scan_operation_outcome(
        scan,
        operation_type='ac_servo_scan',
        fallback_success=False,
    ) == 'success'
    message = motor_config_rules.scan_result_message(True, scan, 'raw failure')
    assert '프로젝트 EtherCAT 구성 확인 완료' in message
    # 안 쓰는 Master 는 **문구로 알린다** · 결과를 깎지는 않는다
    assert '미사용 Master 1 미연결 허용' in message


def test_scan_result_marks_detected_ac_servo_as_partial_without_project_config():
    scan = {
        'scan_id': 'first-connect-dual-master',
        'ethercat_scan': {
            'available': True,
            'complete': False,
            'slaves_count': 1,
            'masters': [
                {'master_index': 0, 'complete': True, 'slaves_count': 1},
                {
                    'master_index': 1,
                    'complete': False,
                    'slaves_count': 0,
                    'error': '재스캔 후 응답한 Slave가 없습니다',
                },
            ],
            'slaves': [{
                'master_index': 0,
                'slave_position': 0,
                'vendor_id': 0x66F,
                'product_code': 0x60380004,
                'serial_number': 402982152,
                'order_number': 'MADLN05BE',
                'direct_read_complete': True,
            }],
            'error': 'Master 1: 재스캔 후 응답한 Slave가 없습니다',
        },
        'dynamixel_scan': {'skipped': True},
        'scan_errors': [{
            'transport': 'ethercat',
            'message': 'Master 1: 재스캔 후 응답한 Slave가 없습니다',
        }],
    }

    assert motor_config_rules.scan_operation_outcome(
        scan,
        operation_type='ac_servo_scan',
        fallback_success=False,
    ) == 'partial'
    message = motor_config_rules.scan_result_message(False, scan, 'raw failure')
    assert message.startswith('모터 검색 부분 완료')
    assert 'AC Servo 1축' in message
    assert 'Master 0 1축 / Master 1 0축' in message


def test_scan_result_handles_multiple_ac_servo_masters_as_success():
    scan = {
        'scan_id': 'multi-master-ac',
        'ethercat_scan': {
            'available': True,
            'complete': True,
            'slaves_count': 5,
            'masters': [
                {'master_index': 0, 'complete': True, 'slaves_count': 2},
                {'master_index': 1, 'complete': True, 'slaves_count': 3},
            ],
            'slaves': [
                {
                    'master_index': 0 if axis < 2 else 1,
                    'slave_position': axis if axis < 2 else axis - 2,
                    'direct_read_complete': True,
                }
                for axis in range(5)
            ],
        },
        'dynamixel_scan': {'skipped': True},
    }

    assert motor_config_rules.scan_operation_outcome(
        scan,
        operation_type='ac_servo_scan',
        fallback_success=False,
    ) == 'success'
    message = motor_config_rules.scan_result_message(True, scan, 'raw success')
    assert message.startswith('모터 검색 완료')
    assert 'AC Servo 5축' in message
    assert 'Master 0 2축 / Master 1 3축' in message


def test_full_scan_marks_mixed_ac_success_and_dynamixel_missing_as_partial():
    scan = {
        'scan_id': 'mixed-ac-dynamixel',
        'ethercat_scan': {
            'available': True,
            'complete': True,
            'slaves_count': 4,
            'slaves': [{'slave_position': axis} for axis in range(4)],
        },
        'dynamixel_scan': {
            'available': False,
            'complete': False,
            'skipped': False,
            'devices_count': 0,
            'devices': [],
            'error': 'Dynamixel 직렬 포트를 찾지 못했습니다',
        },
        'scan_errors': [{
            'transport': 'dynamixel',
            'message': 'Dynamixel 직렬 포트를 찾지 못했습니다',
        }],
    }

    assert motor_config_rules.scan_operation_outcome(
        scan,
        operation_type='full_scan',
        fallback_success=False,
    ) == 'partial'
    message = motor_config_rules.scan_result_message(False, scan, 'raw failure')
    assert message.startswith('모터 검색 부분 완료')
    assert 'AC Servo 4축' in message
    assert 'Dynamixel 0축' in message


def test_dynamixel_scan_with_detected_devices_is_not_reported_as_failure():
    scan = {
        'scan_id': 'dynamixel-partial',
        'dynamixel_scan': {
            'available': True,
            'complete': False,
            'devices_count': 2,
            'devices': [{'id': 1}, {'id': 2}],
            'error': '일부 ID 응답 없음',
        },
        'ethercat_scan': {'skipped': True},
    }

    assert motor_config_rules.scan_operation_outcome(
        scan,
        operation_type='dynamixel_scan',
        fallback_success=False,
    ) == 'partial'
    message = motor_config_rules.scan_result_message(False, scan, 'raw failure')
    assert message.startswith('모터 검색 부분 완료')
    assert 'Dynamixel 2축' in message


def test_scan_result_keeps_failure_when_no_requested_device_is_detected():
    scan = {
        'scan_id': 'empty-ac',
        'ethercat_scan': {
            'available': True,
            'complete': False,
            'slaves_count': 0,
            'slaves': [],
            'error': '재스캔 후 응답한 Slave가 없습니다',
        },
        'dynamixel_scan': {'skipped': True},
    }

    assert motor_config_rules.scan_operation_outcome(
        scan,
        operation_type='ac_servo_scan',
        fallback_success=False,
    ) == 'failure'
    message = motor_config_rules.scan_result_message(False, scan, 'raw failure')
    assert message.startswith('모터 검색 실패')


def test_scan_result_keeps_failure_when_required_project_master_is_missing():
    bridge = MotionWebBridge.__new__(MotionWebBridge)
    bridge._motion_studio_session = MotionStudioSession()
    _motor_config_of(bridge).load = lambda: {
        'success': True,
        'registry': {
            'motors': [{
                'axis': 5,
                'enabled': True,
                'transport': 'ethercat',
                'identity': {'ethercat_master_index': 1},
                'config': {
                    'controller_index': 5,
                    'ethercat_master_index': 1,
                    'position': 0,
                    'alias': 0,
                },
            }],
        },
    }
    scan = {
        'ethercat_scan': {
            'complete': False,
            'masters': [
                {'master_index': 0, 'complete': True, 'slaves_count': 5},
                {'master_index': 1, 'complete': False, 'slaves_count': 0},
            ],
            'slaves': [
                {
                    'master_index': 0,
                    'slave_position': axis,
                    'direct_read_complete': True,
                }
                for axis in range(5)
            ],
        },
        'dynamixel_scan': {'skipped': True},
    }

    ethercat_project_compat.annotate_ethercat_project_compatibility(
        scan, _motor_config_of(bridge).load
    )

    assert scan['project_comparison']['ethercat_project']['compatible'] is False
    assert motor_config_rules.scan_operation_outcome(
        scan,
        operation_type='ac_servo_scan',
        fallback_success=False,
    ) == 'failure'


def test_scan_entrypoints_use_distinct_operation_types():
    bridge = MotionWebBridge.__new__(MotionWebBridge)
    bridge._motion_studio_session = MotionStudioSession()
    bridge._scan_client = object()
    bridge._scan_ac_servo_client = object()
    bridge._scan_dynamixel_client = object()
    bridge.scan_service = '/motor/scan_all'
    bridge.scan_ac_servo_service = '/motor/scan_ac_servo'
    bridge.scan_dynamixel_service = '/motor/scan_dynamixel'
    captured = []

    def call(_client, service_name, _timeout_sec, **kwargs):
        captured.append((service_name, kwargs))
        # 성공을 돌려준다 · 실패하면 §6-234 재시도가 돌아 세 번씩 찍힌다
        return {'success': True}

    _scan_of(bridge)._call_service = call

    _scan_of(bridge).scan_all()
    _scan_of(bridge).scan_ac_servo()
    _scan_of(bridge).scan_dynamixel()

    assert captured == [
        ('/motor/scan_all', {
            'release_ethercat': True,
            'operation_type': 'full_scan',
        }),
        ('/motor/scan_ac_servo', {
            'release_ethercat': True,
            'operation_type': 'ac_servo_scan',
        }),
        # 다이나믹셀도 선을 혼자 쓰고 검색한다 · §6-200
        #
        # 매니저가 같은 시리얼 선을 10Hz 로 쓰는 동안 검색하면 답장이 섞여
        # 모델 번호가 0 으로 읽혔다 · 프로토콜상 답장에 「무슨 질문의 답인지」가
        # 없어 내용으로는 구분할 수 없다.
        ('/motor/scan_dynamixel', {
            'release_ethercat': True,
            'operation_type': 'dynamixel_scan',
        }),
    ]


def test_full_scan_returns_terminal_partial_operation():
    bridge = MotionWebBridge.__new__(MotionWebBridge)
    bridge._motion_studio_session = MotionStudioSession()
    bridge._motor_lifecycle_lock = threading.Lock()
    bridge._project_generation_lock = threading.Lock()
    bridge._project_generation = 4
    repository = operation_repository(lambda: 'project-a')
    bridge.project_repository = repository
    bridge.snapshot = lambda: {
        'motor_operation': repository.runtime.motor_operation_status(),
    }
    _scan_of(bridge)._call_ethercat_service_locked = lambda *_args, **_kwargs: {
        'success': False,
        'message': '모터 검색 부분 완료',
        'scan': {
            'scan_id': 'mixed-1',
            'ethercat_scan': {'complete': True, 'slaves_count': 5},
            'dynamixel_scan': {'complete': False, 'devices_count': 0},
        },
    }

    result = _scan_of(bridge)._call_service(
        object(),
        '/motor/scan_all',
        20.0,
        release_ethercat=True,
        operation_type='full_scan',
    )

    assert result['success'] is False
    assert result['partial'] is True
    assert result['motor_operation']['type'] == 'full_scan'
    assert result['motor_operation']['status'] == 'partial'
    assert result['motor_operation']['phase'] == 'partial'


def test_ac_servo_scan_temporarily_releases_and_restores_motor_service(monkeypatch):
    bridge = MotionWebBridge.__new__(MotionWebBridge)
    bridge._motion_studio_session = MotionStudioSession()
    bridge._lock = threading.Lock()
    bridge._motion_state = {
        'motors': [{
            'controller_index': 0,
            'transport': 'ethercat',
            'connection_state': 'online',
            'connection_connected': True,
            'fault': False,
            'velocity_deg_s': 0.0,
            'target_reached': True,
        }],
    }
    bridge._motion_state_received_at = time.time()
    bridge._motion_run_lock = threading.Lock()
    bridge._motion_run_status = {}
    bridge._motion_studio_session.lock = threading.Lock()
    bridge._motion_studio_session.status = {}
    bridge.project_repository = operation_repository(lambda: 'project-a')
    bridge.snapshot = lambda: {}
    bridge.current_project_generation = lambda: 3
    _runtime_of(bridge).managed_service_active = lambda _service: True
    bridge._expected_runtime_ethercat_axes = lambda: [0]
    calls = []
    _runtime_of(bridge).run_managed_service = (
        lambda action, service: calls.append((action, service))
    )
    monkeypatch.setattr(
        motor_config_rules, 'wait_for_ethercat_release',
        lambda timeout_sec: calls.append(('released', timeout_sec)),
    )
    _scan_of(bridge)._call_service_locked = lambda *_args: {
        'success': True,
        'message': 'scan complete',
        'scan': {'scan_id': 'scan-1'},
    }
    _runtime_of(bridge).wait_for_runtime_recovery = lambda *_args, **_kwargs: {
        'required': True,
        'expected_axes': [0],
        'online_axes': [0],
        'recovered': True,
        'service_active': True,
    }
    monkeypatch.setenv('MOTION_MOTOR_SERVICE_UNIT', 'motion-motor.service')

    result = _scan_of(bridge)._call_ethercat_service_locked(
        object(), '/motor/scan_ac_servo', 10.0
    )

    assert result['success'] is True
    assert result['motor_service_was_active'] is True
    assert result['motor_service_restored'] is True
    assert calls == [
        ('stop', 'motion-motor.service'),
        ('released', 5.0),
        ('start', 'motion-motor.service'),
    ]


def _scan_bridge_with_runtime(monkeypatch):
    """AC 서보 검색 시험용 브리지 · 0·1번 축이 멀쩡히 돌고 있는 상태."""
    bridge = MotionWebBridge.__new__(MotionWebBridge)
    bridge._motion_studio_session = MotionStudioSession()
    bridge._lock = threading.Lock()
    bridge._motion_state = {
        'motors': [
            {
                'controller_index': axis,
                'transport': 'ethercat',
                'connection_state': 'online',
                'connection_connected': True,
                'fault': False,
                'velocity_deg_s': 0.0,
                'target_reached': True,
            }
            for axis in (0, 1)
        ],
    }
    bridge._motion_state_received_at = time.time()
    bridge._motion_run_lock = threading.Lock()
    bridge._motion_run_status = {}
    bridge._motion_studio_session.lock = threading.Lock()
    bridge._motion_studio_session.status = {}
    bridge.project_repository = operation_repository(lambda: 'project-a')
    operation = bridge.project_repository.runtime.begin_motor_operation(
        'ac_servo_scan',
        'preparing',
    )
    bridge.snapshot = lambda: {}
    bridge.current_project_generation = lambda: 3
    _runtime_of(bridge).managed_service_active = lambda _service: True
    _runtime_of(bridge).run_managed_service = lambda _action, _service: None
    monkeypatch.setattr(
        motor_config_rules, 'wait_for_ethercat_release', lambda timeout_sec: None
    )
    bridge._expected_runtime_ethercat_axes = lambda: [0, 1]
    _scan_of(bridge)._call_service_locked = lambda *_args: {
        'success': True,
        'message': 'scan complete',
        'scan': {'scan_id': 'scan-1'},
    }
    monkeypatch.setenv('MOTION_MOTOR_SERVICE_UNIT', 'motion-motor.service')
    return bridge


def test_ac_servo_scan_fails_when_motor_runtime_does_not_recover(monkeypatch):
    bridge = MotionWebBridge.__new__(MotionWebBridge)
    bridge._motion_studio_session = MotionStudioSession()
    bridge._lock = threading.Lock()
    bridge._motion_state = {
        'motors': [
            {
                'controller_index': axis,
                'transport': 'ethercat',
                'connection_state': 'online',
                'connection_connected': True,
                'fault': False,
                'velocity_deg_s': 0.0,
                'target_reached': True,
            }
            for axis in (0, 1)
        ],
    }
    bridge._motion_state_received_at = time.time()
    bridge._motion_run_lock = threading.Lock()
    bridge._motion_run_status = {}
    bridge._motion_studio_session.lock = threading.Lock()
    bridge._motion_studio_session.status = {}
    bridge.project_repository = operation_repository(lambda: 'project-a')
    operation = bridge.project_repository.runtime.begin_motor_operation(
        'ac_servo_scan',
        'preparing',
    )
    bridge.snapshot = lambda: {}
    bridge.current_project_generation = lambda: 3
    _runtime_of(bridge).managed_service_active = lambda _service: True
    _runtime_of(bridge).run_managed_service = lambda _action, _service: None
    monkeypatch.setattr(
        motor_config_rules, 'wait_for_ethercat_release', lambda timeout_sec: None
    )
    bridge._expected_runtime_ethercat_axes = lambda: [0, 1]
    _scan_of(bridge)._call_service_locked = lambda *_args: {
        'success': True,
        'message': 'scan complete',
        'scan': {'scan_id': 'scan-1'},
    }
    # **Motor Manager 자체가 안 돌아온 경우** · 이것만 실패다 · §6-196
    _runtime_of(bridge).wait_for_runtime_recovery = lambda *_args, **_kwargs: {
        'required': True,
        'expected_axes': [0, 1],
        'online_axes': [],
        'recovered': False,
        'missing_axes': [0, 1],
        'service_active': False,
    }
    monkeypatch.setenv('MOTION_MOTOR_SERVICE_UNIT', 'motion-motor.service')

    result = _scan_of(bridge)._call_ethercat_service_locked(
        object(),
        '/motor/scan_ac_servo',
        10.0,
        operation_id=operation['operation_id'],
    )

    assert result['success'] is False
    assert result['motor_service_restored'] is False
    assert '복구 실패' in result['message']
    assert 'Motor Manager 가 다시 실행되지 않았습니다' in result['message']


def test_a_motor_that_did_not_come_back_is_not_a_scan_failure(monkeypatch):
    """빠진 모터 때문에 재검색했는데 「검색 실패」가 뜨던 것 · §6-196

    AC 서보 검색은 EtherCAT 소유권 때문에 Motor Manager 를 껐다 켠다 · 그
    뒤 「검색 전 설정의 축이 **전부** 돌아왔나」를 봤다.

    모터가 빠져서 재검색하는 경우 그 축은 당연히 안 돌아온다 · 검색은
    제대로 됐고 축 목록도 갱신됐는데 버튼엔 「직접 검색 실패」가 떴다 ·
    12초를 기다린 뒤에.

    **사람이 직접 검색을 눌렀다는 것은 모터 상태를 보고 눌렀다는 뜻이다** ·
    없는 모터가 없다고 나오는 것은 실패가 아니라 그 검색의 답이다.
    """
    bridge = _scan_bridge_with_runtime(monkeypatch)
    _runtime_of(bridge).wait_for_runtime_recovery = lambda *_args, **_kwargs: {
        'required': True,
        'expected_axes': [0, 1],
        'online_axes': [0],
        'recovered': False,
        'missing_axes': [1],
        'service_active': True,          # Motor Manager 는 잘 돌아왔다
    }

    result = _scan_of(bridge)._call_ethercat_service_locked(
        object(), '/motor/scan_ac_servo', 10.0,
    )

    assert result['success'] is True, '검색은 성공했다'
    assert result['missing_axes'] == [1]
    assert '1번 축이 돌아오지 않았습니다' in result['message'], '무엇이 없는지 알려야 한다'
    assert '실패' not in result['message'], '실패라고 말하면 안 된다'


def test_ac_servo_scan_restores_service_even_when_stop_command_times_out(monkeypatch):
    bridge = MotionWebBridge.__new__(MotionWebBridge)
    bridge._motion_studio_session = MotionStudioSession()
    bridge._lock = threading.Lock()
    bridge._motion_state = {
        'motors': [{
            'controller_index': 0,
            'transport': 'ethercat',
            'connection_state': 'online',
            'connection_connected': True,
            'fault': False,
            'velocity_deg_s': 0.0,
            'target_reached': True,
        }],
    }
    bridge._motion_state_received_at = time.time()
    bridge._motion_run_lock = threading.Lock()
    bridge._motion_run_status = {}
    bridge._motion_studio_session.lock = threading.Lock()
    bridge._motion_studio_session.status = {}
    bridge.project_repository = operation_repository(lambda: 'project-a')
    bridge.snapshot = lambda: {}
    bridge.current_project_generation = lambda: 3
    _runtime_of(bridge).managed_service_active = lambda _service: True
    bridge._expected_runtime_ethercat_axes = lambda: [0]
    calls = []

    def service_action(action, service):
        calls.append((action, service))
        if action == 'stop':
            raise subprocess.TimeoutExpired(['systemctl', 'stop'], 10.0)

    _runtime_of(bridge).run_managed_service = service_action
    _runtime_of(bridge).wait_for_runtime_recovery = lambda *_args, **_kwargs: {
        'required': True,
        'expected_axes': [0],
        'online_axes': [0],
        'recovered': True,
        'service_active': True,
    }
    monkeypatch.setenv('MOTION_MOTOR_SERVICE_UNIT', 'motion-motor.service')

    result = _scan_of(bridge)._call_ethercat_service_locked(
        object(), '/motor/scan_ac_servo', 10.0
    )

    assert result['success'] is False
    assert calls == [
        ('stop', 'motion-motor.service'),
        ('start', 'motion-motor.service'),
    ]
    assert result['motor_service_restored'] is True


def test_ac_servo_scan_restores_service_even_when_status_update_fails(monkeypatch):
    bridge = MotionWebBridge.__new__(MotionWebBridge)
    bridge._motion_studio_session = MotionStudioSession()
    bridge._lock = threading.Lock()
    bridge._motion_state = {
        'motors': [{
            'controller_index': 0,
            'transport': 'ethercat',
            'connection_state': 'online',
            'connection_connected': True,
            'fault': False,
            'velocity_deg_s': 0.0,
            'target_reached': True,
        }],
    }
    bridge._motion_state_received_at = time.time()
    bridge._motion_run_lock = threading.Lock()
    bridge._motion_run_status = {}
    bridge._motion_studio_session.lock = threading.Lock()
    bridge._motion_studio_session.status = {}
    repository = operation_repository(lambda: 'project-a')
    operation = repository.runtime.begin_motor_operation('ac_servo_scan', 'preparing')
    original_update = repository.runtime.update_motor_operation

    def update(operation_id, phase, **kwargs):
        if phase == 'restoring':
            raise ValueError('operation was concurrently finalized')
        return original_update(operation_id, phase, **kwargs)

    repository.runtime.update_motor_operation = update
    bridge.project_repository = repository
    bridge.snapshot = lambda: {}
    bridge.current_project_generation = lambda: 3
    _runtime_of(bridge).managed_service_active = lambda _service: True
    bridge._expected_runtime_ethercat_axes = lambda: [0]
    calls = []
    _runtime_of(bridge).run_managed_service = (
        lambda action, service: calls.append((action, service))
    )
    monkeypatch.setattr(
        motor_config_rules, 'wait_for_ethercat_release', lambda timeout_sec: None
    )
    _scan_of(bridge)._call_service_locked = lambda *_args: {
        'success': True,
        'message': 'scan complete',
        'scan': {'scan_id': 'scan-1'},
    }
    _runtime_of(bridge).wait_for_runtime_recovery = lambda *_args, **_kwargs: {
        'required': True,
        'expected_axes': [0],
        'online_axes': [0],
        'recovered': True,
        'service_active': True,
    }
    monkeypatch.setenv('MOTION_MOTOR_SERVICE_UNIT', 'motion-motor.service')

    result = _scan_of(bridge)._call_ethercat_service_locked(
        object(),
        '/motor/scan_ac_servo',
        10.0,
        operation_id=operation['operation_id'],
    )

    assert calls == [
        ('stop', 'motion-motor.service'),
        ('start', 'motion-motor.service'),
    ]
    assert result['motor_service_restored'] is True


def test_motor_runtime_recovery_requires_all_configured_transports():
    bridge = MotionWebBridge.__new__(MotionWebBridge)
    bridge._motion_studio_session = MotionStudioSession()
    bridge._lock = threading.Lock()
    bridge._motion_state = {
        'motors': [
            {
                'controller_index': 0,
                'transport': 'ethercat',
                'connection_connected': True,
                'connection_state': 'online',
                'fault': False,
            },
            {
                'controller_index': 1,
                'transport': 'serial',
                'connection_connected': True,
                'connection_state': 'online',
                'fault': False,
            },
        ],
    }
    bridge._motion_state_received_at = time.time() + 1.0
    _runtime_of(bridge).managed_service_active = lambda _service: True

    result = _runtime_of(bridge).wait_for_runtime_recovery(
        [0, 1],
        timeout_sec=0.1,
        motor_service='motion-motor.service',
    )

    assert result['recovered'] is True
    assert result['expected_axes'] == [0, 1]
    assert result['online_axes'] == [0, 1]


def test_ethercat_release_waits_until_slaves_leave_operational_state(monkeypatch):
    calls = []
    slave_outputs = iter([
        '0  0:0  OP  +  Drive\\n',
        '0  0:0  PREOP  +  Drive\\n',
    ])

    def run(command, **_kwargs):
        calls.append(command)
        if command == ['ethercat', 'master']:
            return type('Result', (), {
                'returncode': 0,
                'stdout': 'Phase: Idle\\nActive: no\\n',
                'stderr': '',
            })()
        return type('Result', (), {
            'returncode': 0,
            'stdout': next(slave_outputs),
            'stderr': '',
        })()

    monkeypatch.setattr('motion_web_bridge.motor_config_service.subprocess.run', run)
    monkeypatch.setattr('motion_web_bridge.bridge_node.time.sleep', lambda _sec: None)

    motor_config_rules.wait_for_ethercat_release(1.0)

    assert calls.count(['ethercat', 'slaves']) == 2


def test_motor_runtime_recovery_requires_fresh_online_feedback():
    bridge = MotionWebBridge.__new__(MotionWebBridge)
    bridge._motion_studio_session = MotionStudioSession()
    bridge._lock = threading.Lock()
    bridge._motion_state_received_at = time.time() + 1.0
    bridge._motion_state = {
        'motors': [{
            'controller_index': 0,
            'transport': 'ethercat',
            'connection_connected': True,
            'fault': False,
        }],
    }

    result = _runtime_of(bridge).wait_for_runtime_recovery([0], timeout_sec=0.1)

    assert result['recovered'] is True
    assert result['online_axes'] == [0]


def test_motor_runtime_recovery_rejects_an_empty_expected_axis_set():
    bridge = MotionWebBridge.__new__(MotionWebBridge)
    bridge._motion_studio_session = MotionStudioSession()
    bridge._lock = threading.Lock()
    bridge._motion_state_received_at = time.time() + 1.0
    bridge._motion_state = {'motors': []}

    result = _runtime_of(bridge).wait_for_runtime_recovery([], timeout_sec=0.01)

    assert result['recovered'] is False
    assert result['expected_axes'] == []


def test_execution_context_blocks_control_when_one_axis_is_offline():
    bridge = MotionWebBridge.__new__(MotionWebBridge)
    bridge._motion_studio_session = MotionStudioSession()
    _execution_context_of(bridge)._lock = threading.Lock()
    _execution_context_of(bridge)._status = {'ready': True, 'context_id': 'ctx'}
    bridge._lock = threading.Lock()
    bridge._motion_state_received_at = time.time()
    bridge._motion_state = {
        'motors': [
            {
                'controller_index': 0,
                'connection_state': 'online',
                'connection_connected': True,
                'fault': False,
            },
            {
                'controller_index': 1,
                'connection_state': 'offline',
                'connection_connected': False,
                'fault': False,
            },
        ],
    }
    bridge.project_repository = type('Repository', (), {
        'selected_project_id': lambda _self: '',
    })()

    status = _execution_context_of(bridge).status(validate_files=False)

    assert status['ready'] is True
    assert status['control_allowed'] is False
    assert '1' in status['control_block_reason']


def test_ac_servo_scan_is_blocked_while_runtime_velocity_is_nonzero(monkeypatch):
    bridge = MotionWebBridge.__new__(MotionWebBridge)
    bridge._motion_studio_session = MotionStudioSession()
    bridge._lock = threading.Lock()
    bridge._motion_state = {
        'motors': [{
            'controller_index': 2,
            'transport': 'ethercat',
            'connection_state': 'online',
            'connection_connected': True,
            'fault': False,
            # 문턱 10 deg/s 를 넘는 분명한 움직임 · §6-225
            'velocity_deg_s': 30.0,
            'target_reached': False,
        }],
    }
    bridge._motion_state_received_at = time.time()
    bridge._motion_run_lock = threading.Lock()
    bridge._motion_run_status = {}
    bridge._motion_studio_session.lock = threading.Lock()
    bridge._motion_studio_session.status = {}
    bridge.project_repository = operation_repository(lambda: 'project-a')
    bridge.snapshot = lambda: {}
    bridge.current_project_generation = lambda: 3
    _runtime_of(bridge).managed_service_active = lambda _service: (
        pytest.fail('moving motor must be rejected before checking systemd')
    )
    monkeypatch.setenv('MOTION_MOTOR_SERVICE_UNIT', 'motion-motor.service')

    result = _scan_of(bridge)._call_ethercat_service_locked(
        object(), '/motor/scan_ac_servo', 10.0
    )

    assert result['success'] is False
    assert result['scan_blocked'] is True
    assert '축 2' in result['message']
    assert '움직이는 중' in result['message']


@pytest.mark.parametrize('received_at', [None, time.time() - 2.0])
def test_ac_servo_scan_is_blocked_when_running_motor_state_is_not_fresh(
    monkeypatch, received_at
):
    bridge = MotionWebBridge.__new__(MotionWebBridge)
    bridge._motion_studio_session = MotionStudioSession()
    bridge._lock = threading.Lock()
    bridge._motion_state = None if received_at is None else {'motors': []}
    bridge._motion_state_received_at = received_at
    bridge._motion_run_lock = threading.Lock()
    bridge._motion_run_status = {}
    bridge._motion_studio_session.lock = threading.Lock()
    bridge._motion_studio_session.status = {}
    bridge.project_repository = operation_repository(lambda: 'project-a')
    bridge.snapshot = lambda: {}
    bridge.current_project_generation = lambda: 3
    _runtime_of(bridge).managed_service_active = lambda _service: True
    _runtime_of(bridge).run_managed_service = lambda *_args: pytest.fail(
        'stale motor state must be rejected before stopping Motor Manager'
    )
    monkeypatch.setenv('MOTION_MOTOR_SERVICE_UNIT', 'motion-motor.service')

    result = _scan_of(bridge)._call_ethercat_service_locked(
        object(), '/motor/scan_ac_servo', 10.0
    )

    assert result['success'] is False
    assert result['scan_blocked'] is True
    assert '최신 모터 상태' in result['message']


def test_ac_servo_scan_retires_previous_project_runtime_without_feedback(
    monkeypatch,
):
    bridge = MotionWebBridge.__new__(MotionWebBridge)
    bridge._motion_studio_session = MotionStudioSession()
    bridge._lock = threading.Lock()
    bridge._motion_state = None
    bridge._motion_state_received_at = None
    bridge._motion_run_lock = threading.Lock()
    bridge._motion_run_status = {}
    bridge._motion_studio_session.lock = threading.Lock()
    bridge._motion_studio_session.status = {}
    repository = operation_repository(lambda: 'project-b')
    repository.runtime.motor_runtime_state = lambda: {
        'valid': True,
        'target_project_id': 'project-a',
    }
    bridge.project_repository = repository
    bridge.snapshot = lambda: {}
    bridge.current_project_generation = lambda: 4
    _runtime_of(bridge).managed_service_active = lambda _service: True
    calls = []
    _runtime_of(bridge).run_managed_service = (
        lambda action, service: calls.append((action, service))
    )
    monkeypatch.setattr(
        motor_config_rules, 'wait_for_ethercat_release',
        lambda timeout_sec: calls.append(('released', timeout_sec)),
    )
    _scan_of(bridge)._call_service_locked = lambda *_args: {
        'success': True,
        'message': 'scan complete',
        'scan': {'scan_id': 'scan-project-b'},
    }
    _runtime_of(bridge).wait_for_runtime_recovery = lambda *_args, **_kwargs: (
        pytest.fail('the previous project runtime must not be restarted')
    )
    monkeypatch.setenv('MOTION_MOTOR_SERVICE_UNIT', 'motion-motor.service')

    result = _scan_of(bridge)._call_ethercat_service_locked(
        object(), '/motor/scan_ac_servo', 10.0
    )

    assert result['success'] is True
    assert result['runtime_handoff'] == {
        'required': True,
        'selected_project_id': 'project-b',
        'runtime_project_id': 'project-a',
    }
    assert result['motor_service_was_active'] is True
    assert result['motor_service_restore_required'] is False
    assert result['motor_service_restored'] is False
    assert calls == [
        ('stop', 'motion-motor.service'),
        ('released', 5.0),
    ]
    assert '현재 프로젝트 설정을 저장' in result['message']


def test_ac_servo_scan_still_blocks_observed_motion_during_project_handoff(
    monkeypatch,
):
    bridge = MotionWebBridge.__new__(MotionWebBridge)
    bridge._motion_studio_session = MotionStudioSession()
    bridge._lock = threading.Lock()
    bridge._motion_state = {
        'motors': [{
            'controller_index': 1,
            'transport': 'ethercat',
            'connection_state': 'online',
            'connection_connected': True,
            'fault': False,
            'velocity_deg_s': 30.0,
            'target_reached': False,
        }],
    }
    bridge._motion_state_received_at = time.time()
    bridge._motion_run_lock = threading.Lock()
    bridge._motion_run_status = {}
    bridge._motion_studio_session.lock = threading.Lock()
    bridge._motion_studio_session.status = {}
    repository = operation_repository(lambda: 'project-b')
    repository.runtime.motor_runtime_state = lambda: {
        'valid': True,
        'target_project_id': 'project-a',
    }
    bridge.project_repository = repository
    bridge.snapshot = lambda: {}
    bridge.current_project_generation = lambda: 4
    _runtime_of(bridge).managed_service_active = lambda _service: (
        pytest.fail('moving motor must be rejected before checking systemd')
    )
    monkeypatch.setenv('MOTION_MOTOR_SERVICE_UNIT', 'motion-motor.service')

    result = _scan_of(bridge)._call_ethercat_service_locked(
        object(), '/motor/scan_ac_servo', 10.0
    )

    assert result['success'] is False
    assert result['scan_blocked'] is True
    assert '축 1' in result['message']
    assert '움직이는 중' in result['message']


def test_ac_servo_scan_ignores_stopped_servo_velocity_quantization_noise():
    bridge = MotionWebBridge.__new__(MotionWebBridge)
    bridge._motion_studio_session = MotionStudioSession()
    bridge._lock = threading.Lock()
    bridge._motion_state = {
        'motors': [{
            'controller_index': 2,
            'transport': 'ethercat',
            'connection_state': 'online',
            'connection_connected': True,
            'fault': False,
            # 실측 노이즈 최대 · 문턱 10 아래라 막지 않는다 · §6-225
            'velocity_deg_s': 2.06,
            'target_reached': True,
        }],
    }
    bridge._motion_state_received_at = time.time()
    bridge._motion_run_lock = threading.Lock()
    bridge._motion_run_status = {}
    bridge._motion_studio_session.lock = threading.Lock()
    bridge._motion_studio_session.status = {}
    bridge.project_repository = type('Repository', (), {
        'selected_project_id': lambda _self: 'project-a',
    })()
    bridge.snapshot = lambda: {}
    bridge.current_project_generation = lambda: 3

    assert _runtime_of(bridge).ethercat_scan_safety_blocker() == ''


def test_ac_servo_scan_blocks_clear_motion_even_when_target_is_reached():
    bridge = MotionWebBridge.__new__(MotionWebBridge)
    bridge._motion_studio_session = MotionStudioSession()
    bridge._lock = threading.Lock()
    bridge._motion_state = {
        'motors': [{
            'controller_index': 4,
            'transport': 'ethercat',
            'connection_state': 'online',
            'connection_connected': True,
            'fault': False,
            # `target_reached` 가 True 여도 문턱을 넘으면 막는다 · §6-225
            'velocity_deg_s': 30.0,
            'target_reached': True,
        }],
    }
    bridge._motion_state_received_at = time.time()
    bridge._motion_run_lock = threading.Lock()
    bridge._motion_run_status = {}
    bridge._motion_studio_session.lock = threading.Lock()
    bridge._motion_studio_session.status = {}
    bridge.project_repository = type('Repository', (), {
        'selected_project_id': lambda _self: 'project-a',
    })()
    bridge.snapshot = lambda: {}
    bridge.current_project_generation = lambda: 3

    blocker = _runtime_of(bridge).ethercat_scan_safety_blocker()

    assert '축 4' in blocker
    assert '움직이는 중' in blocker


def test_ac_servo_scan_ignores_stale_velocity_when_axis_is_bus_down():
    bridge = MotionWebBridge.__new__(MotionWebBridge)
    bridge._motion_studio_session = MotionStudioSession()
    bridge._lock = threading.Lock()
    bridge._motion_state = {
        'motors': [{
            'controller_index': 0,
            'transport': 'ethercat',
            'connection_state': 'bus_down',
            'connection_connected': False,
            'fault': True,
            'velocity_deg_s': 6.0,
            'target_reached': False,
        }],
    }
    bridge._motion_state_received_at = time.time()
    bridge._motion_run_lock = threading.Lock()
    bridge._motion_run_status = {}
    bridge._motion_studio_session.lock = threading.Lock()
    bridge._motion_studio_session.status = {}
    bridge.project_repository = type('Repository', (), {
        'selected_project_id': lambda _self: 'project-a',
    })()

    assert _runtime_of(bridge).ethercat_scan_safety_blocker() == ''


def test_scan_result_is_discarded_after_a_to_b_to_a_project_switch():
    bridge = MotionWebBridge.__new__(MotionWebBridge)
    bridge._motion_studio_session = MotionStudioSession()
    bridge._project_generation_lock = threading.Lock()
    bridge._project_generation = 7
    bridge.project_repository = operation_repository(lambda: 'project-a')
    bridge.snapshot = lambda: {}
    bridge.get_logger = lambda: type('Logger', (), {'warn': lambda *_args: None})()

    class Future:
        def done(self):
            return True

        def result(self):
            # The visible project ID returned to A, but two boundaries passed.
            bridge._project_generation = 9
            return type('Response', (), {
                'success': True,
                'message': '{"slaves": [{"position": 0}]}',
            })()

    client = type('Client', (), {
        'wait_for_service': lambda _self, **_kwargs: True,
        'call_async': lambda _self, _request: Future(),
    })()

    result = _scan_of(bridge)._call_service(client, '/scan', 1.0)

    assert result['success'] is False
    assert result['scan'] is None
    assert result['project_generation'] == 9


def test_late_ros_response_from_previous_generation_is_never_cached():
    bridge = make_bridge()

    bridge._motion_mapping_response_callback(String(data=json.dumps({
        'request_id': 'mapping-g1-100',
        'project_generation': 1,
        'success': True,
    })))
    assert 'mapping-g1-100' in bridge._motion_mapping_store

    bridge._project_generation = 2
    bridge._motion_mapping_response_callback(String(data=json.dumps({
        'request_id': 'mapping-g1-200',
        'project_generation': 1,
        'success': True,
    })))

    assert 'mapping-g1-200' not in bridge._motion_mapping_store


def test_coordinator_rejects_successful_confirmation_for_wrong_context():
    bridge = make_bridge()
    default_response = bridge._motion_studio_ros_bridge.request

    def studio_response(command, payload, **kwargs):
        response = default_response(command, payload, **kwargs)
        if command == 'confirm_context':
            response['context_id'] = 'different-context'
        return response

    bridge._motion_studio_ros_bridge = _StubTransport(studio_response)

    result = _execution_context_of(bridge).reconcile()

    assert result['state'] == 'waiting_nodes'
    assert result['ready'] is False
    assert 'motion_studio' in result['failures']


def test_coordinator_recovers_on_retry_after_temporary_node_failure():
    bridge = make_bridge()
    default_response = bridge._request_motion_run
    apply_attempts = 0

    def run_response(command, payload, **kwargs):
        nonlocal apply_attempts
        if command == 'apply_context':
            apply_attempts += 1
            if apply_attempts == 1:
                return {'success': False, 'message': 'temporary unavailable'}
        return default_response(command, payload, **kwargs)

    bridge._request_motion_run = run_response

    first = _execution_context_of(bridge).reconcile()
    second = _execution_context_of(bridge).reconcile()

    assert first['state'] == 'waiting_nodes'
    assert first['ready'] is False
    assert second['state'] == 'ready'
    assert second['ready'] is True


def test_ready_context_becomes_stale_immediately_when_project_files_change():
    bridge = make_bridge()
    ready = _execution_context_of(bridge).reconcile()
    assert ready['ready'] is True
    changed = bridge.project_repository.execution_context('project-1')
    changed['context_id'] = 'new-context-sha'
    bridge.project_repository.execution_context = lambda _project_id: changed

    status = _execution_context_of(bridge).status()

    assert status['state'] == 'stale'
    assert status['ready'] is False
    assert status['control_allowed'] is False
    assert _execution_context_of(bridge).context_id() == ''


def test_ready_context_is_not_reapplied_during_an_active_operation():
    bridge = make_bridge()
    calls = []
    default_run_response = bridge._request_motion_run

    def run_response(command, payload, **kwargs):
        calls.append(command)
        return default_run_response(command, payload, **kwargs)

    bridge._request_motion_run = run_response
    first = _execution_context_of(bridge).reconcile()
    assert first['ready'] is True
    assert calls == ['apply_context', 'confirm_context']

    # The periodic coordinator may run long after the original verification.
    # It must not send apply_context again while recording/playback can be live.
    _execution_context_of(bridge)._status['verified_at'] = 0.0
    second = _execution_context_of(bridge).reconcile()

    assert second['ready'] is True
    assert calls == ['apply_context', 'confirm_context']


def test_record_prepares_unified_project_before_requesting_operation():
    bridge = MotionWebBridge.__new__(MotionWebBridge)
    bridge._motion_studio_session = MotionStudioSession()
    bridge._lock = threading.Lock()
    bridge._motion_state_received_at = time.time()
    bridge._motion_state = {
        'motors': [{
            'controller_index': 0,
            'connection_state': 'online',
            'connection_connected': True,
            'fault': False,
        }],
    }
    calls = []
    _install_studio_sync(
        bridge,
        prepare=lambda: calls.append('prepare') or {'success': True},
        request=lambda command, payload, **_kwargs: (
            calls.append((command, payload)) or {'success': True}
        ),
    )

    result = bridge._motion_studio_sync().request_prepared('record', {'mode': 'record'})

    assert result['success'] is True
    assert calls == ['prepare', ('record', {'mode': 'record'})]


def test_record_does_not_start_when_unified_project_prepare_fails():
    bridge = MotionWebBridge.__new__(MotionWebBridge)
    bridge._motion_studio_session = MotionStudioSession()
    _install_studio_sync(
        bridge,
        prepare=lambda: {'success': False, 'message': 'project prepare failed'},
        request=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError('record must not be requested')
        ),
    )

    result = bridge._motion_studio_sync().request_prepared('record', {'mode': 'record'})

    assert result == {'success': False, 'message': 'project prepare failed'}


def test_motion_studio_is_blocked_while_dds_group_owns_local_execution():
    bridge = MotionWebBridge.__new__(MotionWebBridge)
    bridge._motion_studio_session = MotionStudioSession()
    bridge.coordination_execution_blocker = (
        lambda: 'DDS 그룹 실행이 로컬 모션 실행을 사용 중입니다'
    )
    _install_studio_sync(
        bridge,
        prepare=lambda: {'success': True},
        request=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError('DDS 그룹 실행 중에는 모션 스튜디오를 요청하면 안 됩니다')
        ),
    )

    result = bridge._motion_studio_sync().request_prepared('play', {})

    assert result == {
        'success': False,
        'message': (
            '모션 스튜디오 동작 불가: '
            'DDS 그룹 실행이 로컬 모션 실행을 사용 중입니다'
        ),
    }
