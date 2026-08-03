import json
import threading

import pytest

from motion_web_bridge.bridge_node import (
    MotionWebBridge,
    _project_tree_category_signature,
)
from motion_web_bridge.project_repository import ProjectRepository


MOTION_TEXT = '\n'.join([
    json.dumps({'type': 'motion_header', 'rotation_unit': 'deg'}),
    json.dumps([1, 0.0, '1-1', 0.0]),
])


def test_motion_studio_refresh_does_not_reopen_workspace_while_recording(tmp_path):
    repository = ProjectRepository(tmp_path / 'projects')
    project = repository.create_project('recording')['project']
    project_id = project['project_id']
    repository.import_text(
        project_id,
        'motion_axis_matching',
        'mapping.yaml',
        'version: 1\nname: recording\nmappings: []\n',
    )

    bridge = MotionWebBridge.__new__(MotionWebBridge)
    bridge.project_repository = repository
    bridge._motion_studio_lock = threading.Lock()
    bridge._motion_studio_status = {'state': 'recording'}
    commands = []

    def request(command, payload=None, timeout_sec=4.0):
        commands.append((command, payload, timeout_sec))
        return {
            'success': True,
            'project': {'project_id': 'studio-project', 'layers': []},
            'mappings': [{'file_id': 'mapping.yaml'}],
            'motion_files': [],
            'status': {'state': 'recording'},
        }

    bridge.request_motion_studio = request

    result = bridge.prepare_unified_motion_studio()

    assert result['success'] is True
    assert commands[0][0] == 'list'
    assert result['status']['state'] == 'recording'


def test_motion_studio_idle_refresh_reuses_matching_workspace(tmp_path):
    repository = ProjectRepository(tmp_path / 'projects')
    project = repository.create_project('reuse')['project']
    project_id = project['project_id']
    repository.import_text(
        project_id,
        'motion_axis_matching',
        'mapping.yaml',
        'version: 1\nname: reuse\nmappings: []\n',
    )
    detail = repository.get_project(project_id)
    mapping_folder = next(
        item for item in detail['tree']
        if item['category'] == 'motion_axis_matching'
    )
    mapping_sha256 = next(
        item['sha256'] for item in mapping_folder['children']
        if item['name'] == 'mapping.yaml'
    )
    bridge = MotionWebBridge.__new__(MotionWebBridge)
    bridge.project_repository = repository
    bridge._motion_studio_lock = threading.Lock()
    bridge._motion_studio_status = {'state': 'idle'}
    bridge._motion_studio_workspace_signatures = {
        project_id: {
            'layers': _project_tree_category_signature(detail['tree'], 'layers'),
            'motions': _project_tree_category_signature(detail['tree'], 'motions'),
        },
    }
    commands = []

    def request(command, payload=None, timeout_sec=4.0):
        commands.append(command)
        return {
            'success': True,
            'project': {
                'project_id': 'studio-project',
                'workspace_project_id': project_id,
                'mapping_file_id': 'mapping.yaml',
                'mapping_sha256': mapping_sha256,
                'layers': [],
            },
            'mappings': [{'file_id': 'mapping.yaml'}],
            'motion_files': [],
            'status': {'state': 'idle'},
            'composition': {'conflicts': [], 'transition_warnings': []},
        }

    bridge.request_motion_studio = request

    result = bridge.prepare_unified_motion_studio()

    assert result['success'] is True
    assert commands == ['list']
    assert result['composition']['conflicts'] == []


def test_motion_studio_mapping_content_change_reopens_workspace(tmp_path):
    repository = ProjectRepository(tmp_path / 'projects')
    project = repository.create_project('mapping change')['project']
    project_id = project['project_id']
    repository.import_text(
        project_id,
        'motion_axis_matching',
        'mapping.yaml',
        'version: 1\nname: current\nmappings: []\n',
    )
    bridge = MotionWebBridge.__new__(MotionWebBridge)
    bridge.project_repository = repository
    bridge._motion_studio_lock = threading.Lock()
    bridge._motion_studio_status = {'state': 'idle'}
    commands = []

    def request(command, payload=None, timeout_sec=4.0):
        commands.append(command)
        if command == 'list':
            return {
                'success': True,
                'project': {
                    'project_id': 'studio-project',
                    'workspace_project_id': project_id,
                    'mapping_file_id': 'mapping.yaml',
                    'mapping_sha256': 'stale-sha256',
                    'layers': [],
                },
                'composition': {'conflicts': []},
                'status': {'state': 'idle'},
            }
        return {
            'success': True,
            'project': {
                'project_id': 'studio-project',
                'workspace_project_id': project_id,
                'mapping_file_id': 'mapping.yaml',
                'layers': [],
            },
            'composition': {'conflicts': []},
            'status': {'state': 'idle'},
        }

    bridge.request_motion_studio = request

    bridge.prepare_unified_motion_studio()

    assert commands == ['list', 'open_workspace']


@pytest.mark.parametrize('changed_category', ['layers', 'motions'])
def test_motion_studio_project_file_change_reopens_workspace(
    tmp_path, changed_category
):
    repository = ProjectRepository(tmp_path / 'projects')
    project = repository.create_project('file change')['project']
    project_id = project['project_id']
    repository.import_text(
        project_id,
        'motion_axis_matching',
        'mapping.yaml',
        'version: 1\nname: current\nmappings: []\n',
    )
    repository.sync_studio_layers({
        'project_id': 'studio-project',
        'layers': [{'layer_id': 'layer', 'name': 'before', 'frames': []}],
    })
    before = repository.get_project(project_id)
    mapping_folder = next(
        item for item in before['tree']
        if item['category'] == 'motion_axis_matching'
    )
    mapping_sha256 = next(
        item['sha256'] for item in mapping_folder['children']
        if item['name'] == 'mapping.yaml'
    )
    bridge = MotionWebBridge.__new__(MotionWebBridge)
    bridge.project_repository = repository
    bridge._motion_studio_lock = threading.Lock()
    bridge._motion_studio_status = {'state': 'idle'}
    bridge._motion_studio_workspace_signatures = {
        project_id: {
            'layers': _project_tree_category_signature(before['tree'], 'layers'),
            'motions': _project_tree_category_signature(before['tree'], 'motions'),
        },
    }
    if changed_category == 'layers':
        repository.save_file(
            project_id,
            'layers',
            'studio-project__layer.json',
            json.dumps({
                'layer_id': 'layer', 'name': 'after', 'frames': [],
            }),
        )
    else:
        repository.import_text(
            project_id, 'motions', 'new-motion.json', MOTION_TEXT
        )
    commands = []

    def request(command, payload=None, timeout_sec=4.0):
        commands.append(command)
        if command == 'list':
            return {
                'success': True,
                'project': {
                    'project_id': 'studio-project',
                    'workspace_project_id': project_id,
                    'mapping_file_id': 'mapping.yaml',
                    'mapping_sha256': mapping_sha256,
                    'layers': [],
                },
                'composition': {'conflicts': []},
                'status': {'state': 'idle'},
            }
        return {
            'success': True,
            'project': {
                'project_id': 'studio-project',
                'workspace_project_id': project_id,
                'mapping_file_id': 'mapping.yaml',
                'layers': payload['layers'],
            },
            'composition': {'conflicts': []},
            'status': {'state': 'idle'},
        }

    bridge.request_motion_studio = request

    bridge.prepare_unified_motion_studio()

    assert commands == ['list', 'open_workspace']


def test_studio_sync_discards_result_after_project_switch(tmp_path):
    repository = ProjectRepository(tmp_path / 'projects')
    first_id = repository.create_project('first')['project']['project_id']
    second_id = repository.create_project('second')['project']['project_id']
    repository.select_project(second_id)
    bridge = MotionWebBridge.__new__(MotionWebBridge)
    bridge.project_repository = repository

    result = bridge.sync_motion_studio_result({
        'success': True,
        'project': {
            'project_id': 'studio-first',
            'workspace_project_id': first_id,
            'layers': [{'layer_id': 'wrong-project', 'frames': []}],
        },
        'layer_sync': {
            'upsert_layer_ids': ['wrong-project'],
            'delete_layer_ids': [],
        },
    })

    assert result['success'] is False
    assert 'project' not in result
    assert '선택 프로젝트가 변경' in result['project_sync_warning']
    assert list(
        (tmp_path / 'projects' / second_id / 'layers').iterdir()
    ) == []


def test_studio_sync_returns_changed_layer_patch_instead_of_full_project(tmp_path):
    repository = ProjectRepository(tmp_path / 'projects')
    project_id = repository.create_project('compact response')['project']['project_id']
    bridge = MotionWebBridge.__new__(MotionWebBridge)
    bridge.project_repository = repository
    unchanged = {'layer_id': 'unchanged', 'frames': []}
    changed = {'layer_id': 'changed', 'name': 'after', 'frames': []}

    result = bridge.sync_motion_studio_result({
        'success': True,
        'project': {
            'project_id': 'studio-project',
            'workspace_project_id': project_id,
            'name': 'compact',
            'layers': [unchanged, changed],
        },
        'layer_sync': {
            'upsert_layer_ids': ['changed'],
            'delete_layer_ids': [],
        },
    })

    assert 'project' not in result
    assert result['project_patch']['upsert_layers'] == [changed]
    assert result['project_patch']['layer_order'] == ['unchanged', 'changed']
    assert result['project_patch']['metadata']['name'] == 'compact'


def test_motion_studio_project_switch_loads_only_selected_project_layers(tmp_path):
    repository = ProjectRepository(tmp_path / 'projects')
    first_id = repository.create_project('first')['project']['project_id']
    repository.import_text(
        first_id,
        'motion_axis_matching',
        'mapping.yaml',
        'version: 1\nname: first\nmappings: []\n',
    )
    repository.sync_studio_layers({
        'project_id': 'studio-first',
        'layers': [{'layer_id': 'layer-first', 'frames': []}],
    })
    second_id = repository.create_project('second')['project']['project_id']
    repository.import_text(
        second_id,
        'motion_axis_matching',
        'mapping.yaml',
        'version: 1\nname: second\nmappings: []\n',
    )
    repository.sync_studio_layers({
        'project_id': 'studio-second',
        'layers': [{'layer_id': 'layer-second', 'frames': []}],
    })
    repository.select_project(second_id)
    bridge = MotionWebBridge.__new__(MotionWebBridge)
    bridge.project_repository = repository
    bridge._motion_studio_lock = threading.Lock()
    bridge._motion_studio_status = {'state': 'idle'}
    requests = []

    def request(command, payload=None, timeout_sec=4.0):
        requests.append((command, payload))
        if command == 'list':
            return {
                'success': True,
                'project': {
                    'workspace_project_id': first_id,
                    'mapping_file_id': 'mapping.yaml',
                    'layers': [{'layer_id': 'layer-first'}],
                },
                'composition': {'conflicts': []},
                'status': {'state': 'idle'},
            }
        return {
            'success': True,
            'project': {
                'workspace_project_id': second_id,
                'mapping_file_id': 'mapping.yaml',
                'layers': payload['layers'],
            },
            'composition': {'conflicts': []},
            'status': {'state': 'idle'},
        }

    bridge.request_motion_studio = request

    result = bridge.prepare_unified_motion_studio()

    assert [item[0] for item in requests] == ['list', 'open_workspace']
    opened_layers = requests[1][1]['layers']
    assert [layer['layer_id'] for layer in opened_layers] == ['layer-second']
    assert result['workspace_project']['project_id'] == second_id


def test_motion_studio_stop_cancels_start_still_in_preparation():
    bridge = MotionWebBridge.__new__(MotionWebBridge)
    bridge._motion_studio_command_order_lock = threading.Lock()
    bridge._motion_studio_start_generation = 0
    bridge._motor_runtime_control_blocker = lambda: ''
    published = []

    class Publisher:
        def publish(self, message):
            published.append(json.loads(message.data))

    bridge._motion_studio_request_publisher = Publisher()
    bridge._new_project_request_id = lambda prefix: f'{prefix}-request'
    bridge._current_project_generation = lambda: 1
    bridge._execution_context_id = lambda: 'context'
    bridge.project_repository = type(
        'Repository',
        (),
        {'selected_project_id': lambda self: 'workspace-a'},
    )()
    bridge._wait_for_motion_studio_result = lambda request_id, timeout_sec: {
        'success': True,
    }

    def prepare_then_stop():
        bridge.cancel_pending_motion_studio_start()
        return {'success': True}

    bridge.prepare_unified_motion_studio = prepare_then_stop

    result = bridge.request_prepared_motion_studio('play', {})

    assert result['success'] is False
    assert result['start_cancelled'] is True
    assert published == []


def test_motion_studio_start_publishes_before_a_later_stop_generation():
    bridge = MotionWebBridge.__new__(MotionWebBridge)
    bridge._motion_studio_command_order_lock = threading.Lock()
    bridge._motion_studio_start_generation = 3
    published = []

    class Publisher:
        def publish(self, message):
            published.append(json.loads(message.data))

    bridge._motion_studio_request_publisher = Publisher()
    bridge._new_project_request_id = lambda prefix: f'{prefix}-request'
    bridge._current_project_generation = lambda: 1
    bridge._execution_context_id = lambda: 'context'
    bridge.project_repository = type(
        'Repository',
        (),
        {'selected_project_id': lambda self: 'workspace-a'},
    )()
    bridge._wait_for_motion_studio_result = lambda request_id, timeout_sec: {
        'success': True,
    }

    result = bridge.request_motion_studio(
        'play',
        {},
        start_generation=3,
    )

    assert result['success'] is True
    assert [item['command'] for item in published] == ['play']
