import asyncio
import threading
import json
import subprocess
import time

import pytest
from std_msgs.msg import String

from motion_web_bridge.bridge_node import MotionWebBridge, create_app


def operation_repository(selected_project_id):
    operation = {}

    class Repository:
        def selected_project_id(self):
            return str(selected_project_id())

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
    bridge._safety_request_publisher = type('Publisher', (), {
        'publish': lambda _self, _message: None,
    })()
    bridge._wait_for_jog_result = lambda request_id, **_kwargs: {
        'success': True,
        'request_id': request_id,
    }
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


def test_motion_automation_commands_use_current_execution_context():
    bridge = make_bridge()
    bridge._execution_context_status = {
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
    bridge._motor_runtime_control_blocker = lambda: ''

    assert bridge.motion_automation_configure({
        'enabled': True,
        'repeat_mode': 'dwell',
        'dwell_sec': 3,
    })['success']
    assert bridge.motion_automation_start({
        'motion_file_id': 'motion.json',
        'mapping_file_id': 'mapping.yaml',
    })['success']
    assert bridge.motion_automation_disable()['success']
    assert [call[0] for call in calls] == [
        'automation_configure',
        'automation_start',
        'automation_disable',
    ]


def test_motion_automation_start_obeys_motor_runtime_blocker():
    bridge = make_bridge()
    calls = []
    bridge._request_motion_run = lambda *args, **kwargs: calls.append((args, kwargs))
    bridge._motor_runtime_control_blocker = lambda: '서보 에러 2등급'

    result = bridge.motion_automation_start({
        'motion_file_id': 'motion.json',
        'mapping_file_id': 'mapping.yaml',
    })

    assert result == {
        'success': False,
        'message': '자동 반복 시작 불가: 서보 에러 2등급',
    }
    assert calls == []


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


def test_snapshot_reads_motor_operation_without_reconciling_it():
    bridge = MotionWebBridge.__new__(MotionWebBridge)
    bridge._lock = threading.Lock()
    bridge._motion_state = None
    bridge._motion_state_received_at = None
    bridge._motion_value_lock = threading.Lock()
    bridge._motion_value_state = {}
    bridge._motion_run_lock = threading.Lock()
    bridge._motion_run_status = {}
    bridge._midi_monitor_lock = threading.Lock()
    bridge._midi_monitor_status = {}
    bridge._motion_studio_lock = threading.Lock()
    bridge._motion_studio_status = {}
    bridge._safety_status_lock = threading.Lock()
    bridge._safety_status = {}
    bridge._bridge_instance_id = 'bridge-1'
    bridge._bridge_started_at = 1.0
    bridge.motion_state_topic = '/motion_state'
    bridge.max_jog_delta_deg = 360.0
    bridge._web_access = {}
    bridge._runtime_service_status = lambda _state: {'phase': 'ready'}
    bridge.execution_context_status = lambda **_kwargs: {'ready': True}
    bridge._safety_adjusted_midi_status = lambda status, **_kwargs: status
    bridge._current_project_generation = lambda: 1
    bridge._runtime_project_id_from_path = lambda _selected='': 'project-a'
    bridge._reconcile_motor_operation_status = lambda *_args: (
        pytest.fail('snapshot must be read-only')
    )
    bridge.project_repository = type('Repository', (), {
        'selected_project_id': lambda _self: 'project-a',
        'motor_operation_status': lambda _self: {
            'operation_id': 'operation-1',
            'status': 'running',
            'phase': 'verifying',
        },
    })()

    result = bridge.snapshot()

    assert result['motor_operation']['operation_id'] == 'operation-1'
    assert result['motor_operation']['phase'] == 'verifying'


def test_motor_operation_coordinator_is_the_reconcile_writer():
    bridge = MotionWebBridge.__new__(MotionWebBridge)
    bridge._motor_operation_reconcile_lock = threading.Lock()
    bridge._lock = threading.Lock()
    bridge._motion_state = {'motors': []}
    bridge._motion_state_received_at = time.time()
    bridge._runtime_service_status = lambda _state: {'phase': 'ready'}
    bridge.execution_context_status = lambda **_kwargs: {'ready': True}
    calls = []
    bridge._reconcile_motor_operation_status = (
        lambda runtime, motion, context: calls.append(
            (runtime, motion, context)
        )
    )

    bridge._motor_operation_reconcile_callback()

    assert len(calls) == 1
    assert calls[0][0]['phase'] == 'ready'
    assert calls[0][2]['ready'] is True


def test_high_frequency_runtime_owner_check_is_independent_from_selection(tmp_path):
    bridge = make_bridge()
    bridge.motion_projects_dir = tmp_path
    bridge.applied_motor_config_file = (
        tmp_path / 'project-1' / 'runtime' / 'applied_motor_config.yaml'
    )
    bridge.project_repository.get_project = lambda _project_id: (_ for _ in ()).throw(
        AssertionError('high-frequency status path must not parse project files')
    )

    assert bridge._runtime_project_id_from_path('project-1') == 'project-1'
    assert bridge._runtime_project_id_from_path('project-2') == 'project-1'


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
    bridge.project_repository = operation_repository(lambda: 'project-a')
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

    result = bridge._call_scan_service(Client(), '/scan_ac_servo_motors', 1.0)

    assert result['success'] is True
    assert result['project_id'] == ''
    assert result['project_generation'] == 0
    assert result['scan']['scan_id'] == 'physical-1'
    assert result['message'] == '모터 검색 완료 · scan_id physical-1'


def test_scan_result_message_keeps_evidence_out_of_operation_text():
    message = MotionWebBridge._scan_result_message(
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
    message = MotionWebBridge._scan_result_message(
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


def test_scan_entrypoints_use_distinct_operation_types():
    bridge = MotionWebBridge.__new__(MotionWebBridge)
    bridge._scan_client = object()
    bridge._scan_ac_servo_client = object()
    bridge._scan_dynamixel_client = object()
    bridge.scan_service = '/scan_motors'
    bridge.scan_ac_servo_service = '/scan_ac_servo_motors'
    bridge.scan_dynamixel_service = '/scan_dynamixel_motors'
    captured = []

    def call(_client, service_name, _timeout_sec, **kwargs):
        captured.append((service_name, kwargs))
        return {}

    bridge._call_scan_service = call

    bridge.scan_motors()
    bridge.scan_ac_servo_motors()
    bridge.scan_dynamixel_motors()

    assert captured == [
        ('/scan_motors', {
            'release_ethercat': True,
            'operation_type': 'full_scan',
        }),
        ('/scan_ac_servo_motors', {
            'release_ethercat': True,
            'operation_type': 'ac_servo_scan',
        }),
        ('/scan_dynamixel_motors', {
            'operation_type': 'dynamixel_scan',
        }),
    ]


def test_full_scan_returns_terminal_partial_operation():
    bridge = MotionWebBridge.__new__(MotionWebBridge)
    bridge._motor_lifecycle_lock = threading.Lock()
    bridge._motor_scan_request_lock = threading.Lock()
    bridge._project_generation_lock = threading.Lock()
    bridge._project_generation = 4
    repository = operation_repository(lambda: 'project-a')
    bridge.project_repository = repository
    bridge.snapshot = lambda: {
        'motor_operation': repository.motor_operation_status(),
    }
    bridge._call_ethercat_scan_service_locked = lambda *_args, **_kwargs: {
        'success': False,
        'message': '모터 검색 부분 완료',
        'scan': {
            'scan_id': 'mixed-1',
            'ethercat_scan': {'complete': True, 'slaves_count': 5},
            'dynamixel_scan': {'complete': False, 'devices_count': 0},
        },
    }

    result = bridge._call_scan_service(
        object(),
        '/scan_motors',
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
    bridge._motion_studio_lock = threading.Lock()
    bridge._motion_studio_status = {}
    bridge.project_repository = operation_repository(lambda: 'project-a')
    bridge.snapshot = lambda: {}
    bridge._current_project_generation = lambda: 3
    bridge._managed_user_service_active = lambda _service: True
    bridge._expected_runtime_ethercat_axes = lambda: [0]
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
    bridge._wait_for_motor_runtime_recovery = lambda *_args, **_kwargs: {
        'required': True,
        'expected_axes': [0],
        'online_axes': [0],
        'recovered': True,
        'service_active': True,
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


def test_ac_servo_scan_fails_when_motor_runtime_does_not_recover(monkeypatch):
    bridge = MotionWebBridge.__new__(MotionWebBridge)
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
    bridge._motion_studio_lock = threading.Lock()
    bridge._motion_studio_status = {}
    bridge.project_repository = operation_repository(lambda: 'project-a')
    operation = bridge.project_repository.begin_motor_operation(
        'ac_servo_scan',
        'preparing',
    )
    bridge.snapshot = lambda: {}
    bridge._current_project_generation = lambda: 3
    bridge._managed_user_service_active = lambda _service: True
    bridge._run_managed_user_service = lambda _action, _service: None
    bridge._wait_for_ethercat_release = lambda timeout_sec: None
    bridge._expected_runtime_ethercat_axes = lambda: [0, 1]
    bridge._call_scan_service_locked = lambda *_args: {
        'success': True,
        'message': 'scan complete',
        'scan': {'scan_id': 'scan-1'},
    }
    bridge._wait_for_motor_runtime_recovery = lambda *_args, **_kwargs: {
        'required': True,
        'expected_axes': [0, 1],
        'online_axes': [0],
        'recovered': False,
        'service_active': True,
    }
    monkeypatch.setenv('MOTION_MOTOR_SERVICE_UNIT', 'motion-motor.service')

    result = bridge._call_ethercat_scan_service_locked(
        object(),
        '/scan_ac_servo_motors',
        10.0,
        operation_id=operation['operation_id'],
    )

    assert result['success'] is False
    assert result['motor_service_restored'] is False
    assert '복구 실패' in result['message']


def test_ac_servo_scan_restores_service_even_when_stop_command_times_out(monkeypatch):
    bridge = MotionWebBridge.__new__(MotionWebBridge)
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
    bridge._motion_studio_lock = threading.Lock()
    bridge._motion_studio_status = {}
    bridge.project_repository = operation_repository(lambda: 'project-a')
    bridge.snapshot = lambda: {}
    bridge._current_project_generation = lambda: 3
    bridge._managed_user_service_active = lambda _service: True
    bridge._expected_runtime_ethercat_axes = lambda: [0]
    calls = []

    def service_action(action, service):
        calls.append((action, service))
        if action == 'stop':
            raise subprocess.TimeoutExpired(['systemctl', 'stop'], 10.0)

    bridge._run_managed_user_service = service_action
    bridge._wait_for_motor_runtime_recovery = lambda *_args, **_kwargs: {
        'required': True,
        'expected_axes': [0],
        'online_axes': [0],
        'recovered': True,
        'service_active': True,
    }
    monkeypatch.setenv('MOTION_MOTOR_SERVICE_UNIT', 'motion-motor.service')

    result = bridge._call_ethercat_scan_service_locked(
        object(), '/scan_ac_servo_motors', 10.0
    )

    assert result['success'] is False
    assert calls == [
        ('stop', 'motion-motor.service'),
        ('start', 'motion-motor.service'),
    ]
    assert result['motor_service_restored'] is True


def test_ac_servo_scan_restores_service_even_when_status_update_fails(monkeypatch):
    bridge = MotionWebBridge.__new__(MotionWebBridge)
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
    bridge._motion_studio_lock = threading.Lock()
    bridge._motion_studio_status = {}
    repository = operation_repository(lambda: 'project-a')
    operation = repository.begin_motor_operation('ac_servo_scan', 'preparing')
    original_update = repository.update_motor_operation

    def update(operation_id, phase, **kwargs):
        if phase == 'restoring':
            raise ValueError('operation was concurrently finalized')
        return original_update(operation_id, phase, **kwargs)

    repository.update_motor_operation = update
    bridge.project_repository = repository
    bridge.snapshot = lambda: {}
    bridge._current_project_generation = lambda: 3
    bridge._managed_user_service_active = lambda _service: True
    bridge._expected_runtime_ethercat_axes = lambda: [0]
    calls = []
    bridge._run_managed_user_service = (
        lambda action, service: calls.append((action, service))
    )
    bridge._wait_for_ethercat_release = lambda timeout_sec: None
    bridge._call_scan_service_locked = lambda *_args: {
        'success': True,
        'message': 'scan complete',
        'scan': {'scan_id': 'scan-1'},
    }
    bridge._wait_for_motor_runtime_recovery = lambda *_args, **_kwargs: {
        'required': True,
        'expected_axes': [0],
        'online_axes': [0],
        'recovered': True,
        'service_active': True,
    }
    monkeypatch.setenv('MOTION_MOTOR_SERVICE_UNIT', 'motion-motor.service')

    result = bridge._call_ethercat_scan_service_locked(
        object(),
        '/scan_ac_servo_motors',
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
    bridge._managed_user_service_active = lambda _service: True

    result = bridge._wait_for_motor_runtime_recovery(
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

    monkeypatch.setattr('motion_web_bridge.bridge_node.subprocess.run', run)
    monkeypatch.setattr('motion_web_bridge.bridge_node.time.sleep', lambda _sec: None)

    MotionWebBridge._wait_for_ethercat_release(1.0)

    assert calls.count(['ethercat', 'slaves']) == 2


def test_motor_runtime_recovery_requires_fresh_online_feedback():
    bridge = MotionWebBridge.__new__(MotionWebBridge)
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

    result = bridge._wait_for_motor_runtime_recovery([0], timeout_sec=0.1)

    assert result['recovered'] is True
    assert result['online_axes'] == [0]


def test_motor_runtime_recovery_rejects_an_empty_expected_axis_set():
    bridge = MotionWebBridge.__new__(MotionWebBridge)
    bridge._lock = threading.Lock()
    bridge._motion_state_received_at = time.time() + 1.0
    bridge._motion_state = {'motors': []}

    result = bridge._wait_for_motor_runtime_recovery([], timeout_sec=0.01)

    assert result['recovered'] is False
    assert result['expected_axes'] == []


def test_execution_context_blocks_control_when_one_axis_is_offline():
    bridge = MotionWebBridge.__new__(MotionWebBridge)
    bridge._execution_context_lock = threading.Lock()
    bridge._execution_context_status = {'ready': True, 'context_id': 'ctx'}
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

    status = bridge.execution_context_status(validate_files=False)

    assert status['ready'] is True
    assert status['control_allowed'] is False
    assert '1' in status['control_block_reason']


def test_ac_servo_scan_is_blocked_while_runtime_velocity_is_nonzero(monkeypatch):
    bridge = MotionWebBridge.__new__(MotionWebBridge)
    bridge._lock = threading.Lock()
    bridge._motion_state = {
        'motors': [{
            'controller_index': 2,
            'transport': 'ethercat',
            'connection_state': 'online',
            'connection_connected': True,
            'fault': False,
            'velocity_deg_s': 1.5,
            'target_reached': False,
        }],
    }
    bridge._motion_state_received_at = time.time()
    bridge._motion_run_lock = threading.Lock()
    bridge._motion_run_status = {}
    bridge._motion_studio_lock = threading.Lock()
    bridge._motion_studio_status = {}
    bridge.project_repository = operation_repository(lambda: 'project-a')
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


@pytest.mark.parametrize('received_at', [None, time.time() - 2.0])
def test_ac_servo_scan_is_blocked_when_running_motor_state_is_not_fresh(
    monkeypatch, received_at
):
    bridge = MotionWebBridge.__new__(MotionWebBridge)
    bridge._lock = threading.Lock()
    bridge._motion_state = None if received_at is None else {'motors': []}
    bridge._motion_state_received_at = received_at
    bridge._motion_run_lock = threading.Lock()
    bridge._motion_run_status = {}
    bridge._motion_studio_lock = threading.Lock()
    bridge._motion_studio_status = {}
    bridge.project_repository = operation_repository(lambda: 'project-a')
    bridge.snapshot = lambda: {}
    bridge._current_project_generation = lambda: 3
    bridge._managed_user_service_active = lambda _service: True
    bridge._run_managed_user_service = lambda *_args: pytest.fail(
        'stale motor state must be rejected before stopping Motor Manager'
    )
    monkeypatch.setenv('MOTION_MOTOR_SERVICE_UNIT', 'motion-motor.service')

    result = bridge._call_ethercat_scan_service_locked(
        object(), '/scan_ac_servo_motors', 10.0
    )

    assert result['success'] is False
    assert result['scan_blocked'] is True
    assert '최신 모터 상태' in result['message']


def test_ac_servo_scan_ignores_stopped_servo_velocity_quantization_noise():
    bridge = MotionWebBridge.__new__(MotionWebBridge)
    bridge._lock = threading.Lock()
    bridge._motion_state = {
        'motors': [{
            'controller_index': 2,
            'transport': 'ethercat',
            'connection_state': 'online',
            'connection_connected': True,
            'fault': False,
            'velocity_deg_s': 2.1,
            'target_reached': True,
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

    assert bridge._ethercat_scan_safety_blocker() == ''


def test_ac_servo_scan_blocks_clear_motion_even_when_target_is_reached():
    bridge = MotionWebBridge.__new__(MotionWebBridge)
    bridge._lock = threading.Lock()
    bridge._motion_state = {
        'motors': [{
            'controller_index': 4,
            'transport': 'ethercat',
            'connection_state': 'online',
            'connection_connected': True,
            'fault': False,
            'velocity_deg_s': 5.1,
            'target_reached': True,
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

    blocker = bridge._ethercat_scan_safety_blocker()

    assert '축 4' in blocker
    assert '움직이는 중' in blocker


def test_ac_servo_scan_ignores_stale_velocity_when_axis_is_bus_down():
    bridge = MotionWebBridge.__new__(MotionWebBridge)
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
    bridge._motion_studio_lock = threading.Lock()
    bridge._motion_studio_status = {}
    bridge.project_repository = type('Repository', (), {
        'selected_project_id': lambda _self: 'project-a',
    })()

    assert bridge._ethercat_scan_safety_blocker() == ''


def test_scan_result_is_discarded_after_a_to_b_to_a_project_switch():
    bridge = MotionWebBridge.__new__(MotionWebBridge)
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
