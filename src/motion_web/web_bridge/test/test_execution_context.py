import threading

from motion_web_bridge.bridge_node import MotionWebBridge


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
    bridge._execution_context_status = {
        'state': 'starting', 'ready': False, 'context_id': '', 'nodes': {},
    }
    bridge._lock = threading.Lock()
    bridge._motion_state = {'generated_at': 1.0, 'last_motor_status_at': 1.0, 'motors': []}
    bridge._runtime_project_id = lambda: 'project-1'
    bridge._runtime_service_status = lambda _state: {
        'phase': 'ready', 'message': 'motor runtime ready',
    }

    def response(_command, payload, **_kwargs):
        return {
            'success': True,
            'project_id': 'project-1',
            'context_id': payload.get('context_id'),
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

    result = bridge._reconcile_execution_context()

    assert result['state'] == 'motor_apply_required'
    assert result['ready'] is False
    assert result['failures'] == {}


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
