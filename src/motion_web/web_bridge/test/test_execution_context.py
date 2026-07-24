import asyncio
import threading
import json
import time

import pytest
from std_msgs.msg import String

from motion_web_bridge.bridge_node import MotionWebBridge, create_app


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


def make_bridge():
    bridge = MotionWebBridge.__new__(MotionWebBridge)
    bridge.project_repository = ContextRepository()
    bridge._execution_context_lock = threading.RLock()
    bridge._execution_context_apply_lock = threading.Lock()
    bridge._project_generation_lock = threading.Lock()
    bridge._project_generation = 1
    bridge._execution_context_status = {
        'state': 'starting', 'ready': False, 'context_id': '', 'nodes': {},
    }
    bridge._lock = threading.Lock()
    bridge._event_log_lock = threading.RLock()
    bridge._jog_result_lock = threading.Lock()
    bridge._action_result_lock = threading.Lock()
    bridge._motion_mapping_lock = threading.Lock()
    bridge._motion_run_lock = threading.Lock()
    bridge._midi_monitor_lock = threading.Lock()
    bridge._motion_studio_lock = threading.Lock()
    bridge._motion_studio_editor_lock = threading.Lock()
    bridge._motion_state = {'generated_at': 1.0, 'last_motor_status_at': 1.0, 'motors': []}
    bridge._motion_state_received_at = 1.0
    bridge._active_motor_errors = {}
    bridge._last_motion_run_state = None
    bridge._jog_results = {}
    bridge._action_results = {}
    bridge._motion_mapping_results = {}
    bridge._motion_run_results = {}
    bridge._motion_run_status = {}
    bridge._midi_monitor_results = {}
    bridge._midi_monitor_status = {}
    bridge._motion_studio_results = {}
    bridge._motion_studio_status = {}
    bridge._motion_studio_editor_results = {}
    bridge._runtime_project_id = lambda: 'project-1'
    bridge._runtime_service_status = lambda _state: {
        'phase': 'ready', 'message': 'motor runtime ready',
    }

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
    bridge.request_motion_studio = response
    return bridge


def test_coordinator_allows_control_only_after_all_nodes_confirm_context():
    bridge = make_bridge()

    result = bridge._reconcile_execution_context()

    assert result['state'] == 'ready'
    assert result['ready'] is True
    assert result['stored_equals_runtime'] is True
    assert result['context_id'] == 'context-sha'
    assert set(result['nodes']) == {
        'motion_mapping', 'midi_control', 'motion_run', 'motion_studio',
        'motor_runtime',
        'midi_control_confirm', 'motion_run_confirm', 'motion_studio_confirm',
    }


def test_coordinator_establishes_persisted_generation_after_program_restart():
    bridge = make_bridge()
    bridge._supervisor_project_generation = 0
    published = []
    bridge._action_request_publisher = type('Publisher', (), {
        'publish': lambda _self, message: published.append(json.loads(message.data)),
    })()
    bridge._wait_for_action_result = lambda request_id, **_kwargs: {
        'success': True,
        'request_id': request_id,
        'project_generation': 1,
    }

    result = bridge._reconcile_execution_context()

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
    bridge._wait_for_action_result = lambda *_args, **_kwargs: None

    result = bridge._reconcile_execution_context()

    assert result['state'] == 'waiting_motor_runtime'
    assert result['ready'] is False
    assert 'motor_runtime' in result['failures']


def test_range_recovery_flag_is_forwarded_to_motion_supervisor():
    bridge = make_bridge()
    published = []
    bridge._motion_state_motor = lambda _axis: {
        'controller_index': 0,
        'motor_type': 'ac_servo',
        'state': 'detected',
        'servo_on': True,
        'fault': False,
    }
    bridge._new_project_request_id = lambda _prefix: 'recovery-1'
    bridge._current_project_generation = lambda: 1
    bridge._action_request_publisher = type('Publisher', (), {
        'publish': lambda _self, message: published.append(json.loads(message.data)),
    })()
    bridge._wait_for_action_result = lambda _request_id: {
        'success': True,
        'message': 'started',
    }
    bridge.snapshot = lambda: {}

    result = bridge.request_ac_servo_action(
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
    bridge._execution_context_status = {
        'state': 'ready',
        'ready': True,
        'project_id': 'project-1',
        'context_id': 'context-sha',
        'context': original('project-1'),
        'nodes': {},
    }
    calls.clear()

    status = bridge.execution_context_status(validate_files=False)

    assert status['ready'] is True
    assert calls == []


def test_high_frequency_runtime_owner_check_does_not_load_project(tmp_path):
    bridge = make_bridge()
    bridge.motion_projects_dir = tmp_path
    bridge.applied_motor_config_file = (
        tmp_path / 'project-1' / 'runtime' / 'applied_motor_config.yaml'
    )
    bridge.project_repository.get_project = lambda _project_id: (_ for _ in ()).throw(
        AssertionError('high-frequency status path must not parse project files')
    )

    assert bridge._runtime_project_id_from_path('project-1') == 'project-1'
    assert bridge._runtime_project_id_from_path('project-2') == ''


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

        async def send_json(self, payload):
            self.sent.append(payload)

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

    assert index_response.headers['cache-control'] == 'no-store'
    assert script_response.headers['cache-control'] == 'no-store'


def test_coordinator_keeps_control_blocked_when_one_node_does_not_confirm():
    bridge = make_bridge()
    bridge._request_motion_run = lambda command, payload, **_kwargs: (
        {'success': True, 'project_id': 'project-1', 'context_id': payload.get('context_id')}
        if command == 'invalidate_context'
        else {'success': False, 'message': 'node unavailable'}
    )

    result = bridge._reconcile_execution_context()

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

    result = bridge._reconcile_execution_context()

    assert result['state'] == 'ready'
    assert result['ready'] is True


def test_coordinator_accepts_studio_status_with_nested_context_acknowledgement():
    bridge = make_bridge()
    default_response = bridge.request_motion_studio

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

    bridge.request_motion_studio = studio_response

    result = bridge._reconcile_execution_context()

    assert result['state'] == 'ready'
    assert result['ready'] is True


def test_coordinator_blocks_and_invalidates_when_required_file_is_missing():
    bridge = make_bridge()
    context = bridge.project_repository.execution_context('project-1')
    context['missing'] = ['motion_axis_matching']
    context['configuration_complete'] = False
    bridge.project_repository.execution_context = lambda _project_id: context
    invalidations = []
    bridge._invalidate_execution_nodes = lambda context_id='': invalidations.append(context_id)

    result = bridge._reconcile_execution_context()

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
    bridge._invalidate_execution_nodes = lambda context_id='': invalidations.append(context_id)

    def midi_response(command, payload, **kwargs):
        midi_commands.append(command)
        return default_midi_response(command, payload, **kwargs)

    bridge._request_midi_monitor = midi_response

    result = bridge._reconcile_execution_context()

    assert result['state'] == 'motor_apply_required'
    assert result['ready'] is False
    assert result['failures'] == {}
    assert midi_commands == ['select_project']
    assert invalidations == []
    assert result['nodes']['midi_control']['project_id'] == 'project-1'


def test_coordinator_waits_for_current_project_motor_runtime():
    bridge = make_bridge()
    bridge._runtime_service_status = lambda _state: {
        'phase': 'waiting_motor_state',
        'message': 'motor state waiting',
    }

    result = bridge._reconcile_execution_context()

    assert result['state'] == 'waiting_motor_runtime'
    assert result['ready'] is False
    assert result['failures'] == {'motor_runtime': 'motor state waiting'}


def test_project_change_deletes_previous_project_values_from_bridge_memory():
    bridge = MotionWebBridge.__new__(MotionWebBridge)
    bridge._lock = threading.Lock()
    bridge._event_log_lock = threading.RLock()
    bridge._jog_result_lock = threading.Lock()
    bridge._action_result_lock = threading.Lock()
    bridge._motion_mapping_lock = threading.Lock()
    bridge._motion_run_lock = threading.Lock()
    bridge._midi_monitor_lock = threading.Lock()
    bridge._motion_studio_lock = threading.Lock()
    bridge._motion_studio_editor_lock = threading.Lock()
    bridge._motion_state = {'motors': [{'alias': 403}]}
    bridge._motion_state_received_at = 1.0
    bridge._active_motor_errors = {'0': 'old-error'}
    bridge._last_motion_run_state = 'running'
    bridge._jog_results = {'old': {'success': True}}
    bridge._action_results = {'old': {'success': True}}
    bridge._motion_mapping_results = {'old': {'project_id': 'old-project'}}
    bridge._motion_run_results = {'old': {'project_id': 'old-project'}}
    bridge._motion_run_status = {'project_id': 'old-project', 'axes': [1]}
    bridge._midi_monitor_results = {'old': {'project_id': 'old-project'}}
    bridge._midi_monitor_status = {'project_id': 'old-project', 'banks': [1]}
    bridge._motion_studio_results = {'old': {'project_id': 'old-project'}}
    bridge._motion_studio_status = {'project_id': 'old-project', 'project': {}}
    bridge._motion_studio_editor_results = {'old': {'project_id': 'old-project'}}

    bridge._clear_project_scoped_memory()

    assert bridge._motion_state is None
    assert bridge._motion_state_received_at is None
    assert bridge._active_motor_errors == {}
    assert bridge._last_motion_run_state is None
    assert bridge._jog_results == {}
    assert bridge._action_results == {}
    assert bridge._motion_mapping_results == {}
    assert bridge._motion_run_results == {}
    assert bridge._motion_run_status == {}
    assert bridge._midi_monitor_results == {}
    assert bridge._midi_monitor_status == {}
    assert bridge._motion_studio_results == {}
    assert bridge._motion_studio_status == {}
    assert bridge._motion_studio_editor_results == {}


def test_previous_runtime_motor_state_is_not_cached_after_project_change():
    bridge = MotionWebBridge.__new__(MotionWebBridge)
    bridge._lock = threading.Lock()
    bridge._motion_state = None
    bridge._motion_state_received_at = None
    bridge.project_repository = type('Repository', (), {
        'selected_project_id': lambda _self: 'new-project',
    })()
    bridge._selected_project_owns_runtime = lambda: True
    bridge._record_motor_error_transitions = lambda _payload: None

    bridge._motion_state_callback(String(data=json.dumps({
        'project_id': 'old-project',
        'motors': [{'controller_index': 0, 'alias': 403}],
    })))

    assert bridge._motion_state is None
    assert bridge._motion_state_received_at is None


def test_scan_result_is_discarded_if_project_changes_while_scanning():
    bridge = MotionWebBridge.__new__(MotionWebBridge)
    selected = {'project_id': 'project-a'}
    bridge.project_repository = type('Repository', (), {
        'selected_project_id': lambda _self: selected['project_id'],
    })()
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

    result = bridge._call_scan_service(client, '/scan', 1.0)

    assert result['success'] is False
    assert result['scan'] is None
    assert result['project_id'] == 'project-b'


def test_scan_request_is_rejected_while_another_motor_type_scan_is_running():
    bridge = MotionWebBridge.__new__(MotionWebBridge)
    bridge._motor_scan_request_lock = threading.Lock()
    bridge._motor_scan_request_lock.acquire()
    bridge._project_generation_lock = threading.Lock()
    bridge._project_generation = 3
    bridge.project_repository = type('Repository', (), {
        'selected_project_id': lambda _self: 'project-a',
    })()
    bridge.snapshot = lambda: {}

    class Client:
        def wait_for_service(self, **_kwargs):
            raise AssertionError('busy scan must not call another ROS scan service')

    result = bridge._call_scan_service(Client(), '/scan_dynamixel_motors', 1.0)

    assert result['success'] is False
    assert result['scan'] is None
    assert result['project_id'] == 'project-a'
    assert result['project_generation'] == 3
    assert '다른 모터 검색이 진행 중' in result['message']


def test_physical_scan_is_allowed_without_a_selected_project():
    bridge = MotionWebBridge.__new__(MotionWebBridge)
    bridge._motor_scan_request_lock = threading.Lock()
    bridge._project_generation_lock = threading.Lock()
    bridge._project_generation = 0
    bridge.project_repository = type('Repository', (), {
        'selected_project_id': lambda _self: '',
    })()
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

    result = bridge._call_scan_service(Client(), '/scan_ac_servo_motors', 1.0)

    assert result['success'] is True
    assert result['project_id'] == ''
    assert result['project_generation'] == 0
    assert result['scan']['scan_id'] == 'physical-1'


def test_ac_servo_scan_temporarily_releases_and_restores_motor_service(monkeypatch):
    bridge = MotionWebBridge.__new__(MotionWebBridge)
    bridge._lock = threading.Lock()
    bridge._motion_state = {'motors': []}
    bridge._motion_state_received_at = time.time()
    bridge._motion_run_lock = threading.Lock()
    bridge._motion_run_status = {}
    bridge._motion_studio_lock = threading.Lock()
    bridge._motion_studio_status = {}
    bridge.project_repository = type('Repository', (), {
        'selected_project_id': lambda _self: 'project-a',
    })()
    bridge.snapshot = lambda: {}
    bridge._current_project_generation = lambda: 3
    bridge._managed_user_service_active = lambda _service: True
    calls = []
    bridge._run_managed_user_service = (
        lambda action, service: calls.append((action, service))
    )
    bridge._wait_for_ethercat_release = lambda timeout_sec: calls.append(
        ('released', timeout_sec)
    )
    bridge._call_scan_service_locked = lambda *_args: {
        'success': True,
        'message': 'scan complete',
        'scan': {'scan_id': 'scan-1'},
    }
    monkeypatch.setenv('MOTION_MOTOR_SERVICE_UNIT', 'motion-motor.service')

    result = bridge._call_ethercat_scan_service_locked(
        object(), '/scan_ac_servo_motors', 10.0
    )

    assert result['success'] is True
    assert result['motor_service_was_active'] is True
    assert result['motor_service_restored'] is True
    assert calls == [
        ('stop', 'motion-motor.service'),
        ('released', 5.0),
        ('start', 'motion-motor.service'),
    ]


def test_ac_servo_scan_is_blocked_while_runtime_velocity_is_nonzero(monkeypatch):
    bridge = MotionWebBridge.__new__(MotionWebBridge)
    bridge._lock = threading.Lock()
    bridge._motion_state = {
        'motors': [{
            'controller_index': 2,
            'transport': 'ethercat',
            'velocity_deg_s': 1.5,
        }],
    }
    bridge._motion_state_received_at = time.time()
    bridge._motion_run_lock = threading.Lock()
    bridge._motion_run_status = {}
    bridge._motion_studio_lock = threading.Lock()
    bridge._motion_studio_status = {}
    bridge.project_repository = type('Repository', (), {
        'selected_project_id': lambda _self: 'project-a',
    })()
    bridge.snapshot = lambda: {}
    bridge._current_project_generation = lambda: 3
    bridge._managed_user_service_active = lambda _service: (
        pytest.fail('moving motor must be rejected before checking systemd')
    )
    monkeypatch.setenv('MOTION_MOTOR_SERVICE_UNIT', 'motion-motor.service')

    result = bridge._call_ethercat_scan_service_locked(
        object(), '/scan_ac_servo_motors', 10.0
    )

    assert result['success'] is False
    assert result['scan_blocked'] is True
    assert '축 2' in result['message']
    assert '움직이는 중' in result['message']


def test_scan_result_is_discarded_after_a_to_b_to_a_project_switch():
    bridge = MotionWebBridge.__new__(MotionWebBridge)
    bridge._project_generation_lock = threading.Lock()
    bridge._project_generation = 7
    bridge.project_repository = type('Repository', (), {
        'selected_project_id': lambda _self: 'project-a',
    })()
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

    result = bridge._call_scan_service(client, '/scan', 1.0)

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
    assert 'mapping-g1-100' in bridge._motion_mapping_results

    bridge._project_generation = 2
    bridge._motion_mapping_response_callback(String(data=json.dumps({
        'request_id': 'mapping-g1-200',
        'project_generation': 1,
        'success': True,
    })))

    assert 'mapping-g1-200' not in bridge._motion_mapping_results


def test_coordinator_rejects_successful_confirmation_for_wrong_context():
    bridge = make_bridge()
    default_response = bridge.request_motion_studio

    def studio_response(command, payload, **kwargs):
        response = default_response(command, payload, **kwargs)
        if command == 'confirm_context':
            response['context_id'] = 'different-context'
        return response

    bridge.request_motion_studio = studio_response

    result = bridge._reconcile_execution_context()

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

    first = bridge._reconcile_execution_context()
    second = bridge._reconcile_execution_context()

    assert first['state'] == 'waiting_nodes'
    assert first['ready'] is False
    assert second['state'] == 'ready'
    assert second['ready'] is True


def test_ready_context_becomes_stale_immediately_when_project_files_change():
    bridge = make_bridge()
    ready = bridge._reconcile_execution_context()
    assert ready['ready'] is True
    changed = bridge.project_repository.execution_context('project-1')
    changed['context_id'] = 'new-context-sha'
    bridge.project_repository.execution_context = lambda _project_id: changed

    status = bridge.execution_context_status()

    assert status['state'] == 'stale'
    assert status['ready'] is False
    assert status['control_allowed'] is False
    assert bridge._execution_context_id() == ''


def test_ready_context_is_not_reapplied_during_an_active_operation():
    bridge = make_bridge()
    calls = []
    default_run_response = bridge._request_motion_run

    def run_response(command, payload, **kwargs):
        calls.append(command)
        return default_run_response(command, payload, **kwargs)

    bridge._request_motion_run = run_response
    first = bridge._reconcile_execution_context()
    assert first['ready'] is True
    assert calls == ['apply_context', 'confirm_context']

    # The periodic coordinator may run long after the original verification.
    # It must not send apply_context again while recording/playback can be live.
    bridge._execution_context_status['verified_at'] = 0.0
    second = bridge._reconcile_execution_context()

    assert second['ready'] is True
    assert calls == ['apply_context', 'confirm_context']


def test_record_prepares_unified_project_before_requesting_operation():
    bridge = MotionWebBridge.__new__(MotionWebBridge)
    calls = []
    bridge.prepare_unified_motion_studio = lambda: calls.append('prepare') or {
        'success': True,
    }
    bridge.request_motion_studio = (
        lambda command, payload: calls.append((command, payload)) or {
            'success': True,
        }
    )

    result = bridge.request_prepared_motion_studio('record', {'mode': 'record'})

    assert result['success'] is True
    assert calls == ['prepare', ('record', {'mode': 'record'})]


def test_record_does_not_start_when_unified_project_prepare_fails():
    bridge = MotionWebBridge.__new__(MotionWebBridge)
    bridge.prepare_unified_motion_studio = lambda: {
        'success': False,
        'message': 'project prepare failed',
    }
    bridge.request_motion_studio = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        AssertionError('record must not be requested')
    )

    result = bridge.request_prepared_motion_studio('record', {'mode': 'record'})

    assert result == {'success': False, 'message': 'project prepare failed'}
