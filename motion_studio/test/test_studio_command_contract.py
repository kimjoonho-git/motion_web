import threading

import pytest

from motion_studio.studio_node import MotionStudioNode


class CharacterizationStore:
    def list_projects(self):
        return [{'project_id': 'studio-project'}]

    def list_mappings(self):
        return [{'file_id': 'mapping.yaml'}]

    def list_motion_files(self):
        return [{'file_id': 'motion.json'}]


def bare_node():
    node = MotionStudioNode.__new__(MotionStudioNode)
    node._lock = threading.RLock()
    node._store = CharacterizationStore()
    node._current_project = {'project_id': 'studio-project'}
    node._select_workspace = lambda payload: setattr(
        node, '_selected_workspace_payload', dict(payload)
    )
    node.snapshot = lambda: {'state': 'idle'}
    return node


def test_status_and_list_command_response_contract_is_stable():
    node = bare_node()

    status = node._handle('status', {'project_id': 'workspace-a'})
    listed = node._handle('list', {'project_id': 'workspace-a'})

    assert status == {'state': 'idle'}
    assert listed == {
        'success': True,
        'projects': [{'project_id': 'studio-project'}],
        'mappings': [{'file_id': 'mapping.yaml'}],
        'motion_files': [{'file_id': 'motion.json'}],
        'project': {'project_id': 'studio-project'},
        'status': {'state': 'idle'},
        'composition': {},
    }
    assert node._selected_workspace_payload == {'project_id': 'workspace-a'}


def test_repeated_list_command_reuses_workspace_catalog():
    node = bare_node()

    first = node._handle('list', {'project_id': 'workspace-a'})
    node._store.list_projects = lambda: pytest.fail('projects catalog reread')
    node._store.list_mappings = lambda: pytest.fail('mapping catalog reread')
    node._store.list_motion_files = lambda: pytest.fail('motion catalog reread')
    second = node._handle('list', {'project_id': 'workspace-a'})

    assert second['projects'] == first['projects']
    assert second['mappings'] == first['mappings']
    assert second['motion_files'] == first['motion_files']


@pytest.mark.parametrize(
    ('command', 'method_name'),
    [
        ('update_layer', '_update_layer'),
        ('create_layer', '_create_layer'),
        ('replace_layer_data', '_replace_layer_data'),
        ('delete_layer', '_delete_layer'),
        ('duplicate_layer', '_duplicate_layer'),
        ('commit_merged_layer', '_commit_merged_layer'),
    ],
)
def test_layer_command_routing_contract_is_stable(command, method_name):
    node = bare_node()
    payload = {'layer_id': 'layer-a'}
    setattr(node, method_name, lambda value: {'command': command, 'payload': value})

    assert node._handle(command, payload) == {
        'command': command,
        'payload': payload,
    }


@pytest.mark.parametrize(
    ('command', 'method_name'),
    [
        ('record', '_start_record'),
        ('initialize', '_start_initial_position'),
        ('play', '_start_playback'),
    ],
)
def test_motor_operation_commands_require_context_before_dispatch(command, method_name):
    node = bare_node()
    calls = []
    node._require_execution_context = lambda: calls.append('context')
    setattr(
        node,
        method_name,
        lambda payload: calls.append((command, payload)) or {'success': True},
    )

    result = node._handle(command, {'value': 1})

    assert result == {'success': True}
    assert calls == ['context', (command, {'value': 1})]


def test_unknown_command_keeps_existing_error_contract():
    node = bare_node()

    with pytest.raises(ValueError, match='지원하지 않는 모션 스튜디오 명령'):
        node._handle('unknown', {})
