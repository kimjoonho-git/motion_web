import json
import threading
from pathlib import Path

import pytest

from motion_web_bridge.project_service import ProjectService
from motion_web_bridge.execution_context_service import ExecutionContextService
from motion_web_bridge.bridge_node import (
    MotionWebBridge,
    _project_tree_category_signature,
)
from motion_web_bridge.motion_studio_session import MotionStudioSession
from motion_web_bridge.motion_studio_sync import MotionStudioSync
from motion_web_bridge.project_repository import ProjectRepository


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


class _StubTransport:
    """전송 계층 대역 · 동기화 서비스가 노드가 아니라 이것을 부른다(§6-15)."""

    def __init__(self, request):
        self.request = request


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

    bridge._motion_studio_session = MotionStudioSession()
    bridge.project_repository = repository
    bridge._motion_studio_session.lock = threading.Lock()
    bridge._motion_studio_session.status = {'state': 'recording'}
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

    bridge._motion_studio_ros_bridge = _StubTransport(request)

    result = bridge._motion_studio_sync().prepare()

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
    bridge._motion_studio_session = MotionStudioSession()
    bridge.project_repository = repository
    bridge._motion_studio_session.lock = threading.Lock()
    bridge._motion_studio_session.status = {'state': 'idle'}
    bridge._motion_studio_session.workspace_signatures = {
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

    bridge._motion_studio_ros_bridge = _StubTransport(request)

    result = bridge._motion_studio_sync().prepare()

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
    bridge._motion_studio_session = MotionStudioSession()
    bridge.project_repository = repository
    bridge._motion_studio_session.lock = threading.Lock()
    bridge._motion_studio_session.status = {'state': 'idle'}
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

    bridge._motion_studio_ros_bridge = _StubTransport(request)

    bridge._motion_studio_sync().prepare()

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
    bridge._motion_studio_session = MotionStudioSession()
    bridge.project_repository = repository
    bridge._motion_studio_session.lock = threading.Lock()
    bridge._motion_studio_session.status = {'state': 'idle'}
    bridge._motion_studio_session.workspace_signatures = {
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

    bridge._motion_studio_ros_bridge = _StubTransport(request)

    bridge._motion_studio_sync().prepare()

    assert commands == ['list', 'open_workspace']


def test_studio_sync_discards_result_after_project_switch(tmp_path):
    repository = ProjectRepository(tmp_path / 'projects')
    first_id = repository.create_project('first')['project']['project_id']
    second_id = repository.create_project('second')['project']['project_id']
    repository.select_project(second_id)
    bridge = MotionWebBridge.__new__(MotionWebBridge)
    bridge._motion_studio_session = MotionStudioSession()
    bridge.project_repository = repository

    result = bridge._motion_studio_sync().sync_result({
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
    bridge._motion_studio_session = MotionStudioSession()
    bridge.project_repository = repository
    unchanged = {'layer_id': 'unchanged', 'frames': []}
    changed = {'layer_id': 'changed', 'name': 'after', 'frames': []}

    result = bridge._motion_studio_sync().sync_result({
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
    bridge._motion_studio_session = MotionStudioSession()
    bridge.project_repository = repository
    bridge._motion_studio_session.lock = threading.Lock()
    bridge._motion_studio_session.status = {'state': 'idle'}
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

    bridge._motion_studio_ros_bridge = _StubTransport(request)

    result = bridge._motion_studio_sync().prepare()

    assert [item[0] for item in requests] == ['list', 'open_workspace']
    opened_layers = requests[1][1]['layers']
    assert [layer['layer_id'] for layer in opened_layers] == ['layer-second']
    assert result['workspace_project']['project_id'] == second_id


def test_motion_studio_stop_cancels_start_still_in_preparation():
    bridge = MotionWebBridge.__new__(MotionWebBridge)
    bridge._motion_studio_session = MotionStudioSession()
    bridge._motion_studio_session.order_lock = threading.Lock()
    bridge._motion_studio_session.start_generation = 0
    bridge.motor_runtime_control_blocker = lambda: ''
    published = []

    class Publisher:
        def publish(self, message):
            published.append(json.loads(message.data))

    bridge._motion_studio_request_publisher = Publisher()
    bridge.new_project_request_id = lambda prefix: f'{prefix}-request'
    bridge.current_project_generation = lambda: 1
    _execution_context_of(bridge).context_id = lambda: 'context'
    bridge.project_repository = type(
        'Repository',
        (),
        {'selected_project_id': lambda self: 'workspace-a'},
    )()
    # 응답은 세션 저장소로 들어온다 · 노드 껍데기를 거치지 않는다(§6-15)
    bridge._motion_studio_session.store.store('studio-request', {'success': True})

    def prepare_then_stop():
        bridge.cancel_pending_motion_studio_start()
        return {'success': True}

    sync = MotionStudioSync(
        bridge,
        bridge._motion_studio_session,
        bridge._motion_studio_transport(),
    )
    sync.prepare = prepare_then_stop
    bridge._motion_studio_sync_service = sync

    result = bridge._motion_studio_sync().request_prepared('play', {})

    assert result['success'] is False
    assert result['start_cancelled'] is True
    assert published == []


def test_motion_studio_start_publishes_before_a_later_stop_generation():
    bridge = MotionWebBridge.__new__(MotionWebBridge)
    bridge._motion_studio_session = MotionStudioSession()
    bridge._motion_studio_session.order_lock = threading.Lock()
    bridge._motion_studio_session.start_generation = 3
    published = []

    class Publisher:
        def publish(self, message):
            published.append(json.loads(message.data))

    bridge._motion_studio_request_publisher = Publisher()
    bridge.new_project_request_id = lambda prefix: f'{prefix}-request'
    bridge.current_project_generation = lambda: 1
    _execution_context_of(bridge).context_id = lambda: 'context'
    bridge.project_repository = type(
        'Repository',
        (),
        {'selected_project_id': lambda self: 'workspace-a'},
    )()
    # 응답은 세션 저장소로 들어온다 · 노드 껍데기를 거치지 않는다(§6-15)
    bridge._motion_studio_session.store.store('studio-request', {'success': True})

    result = bridge._motion_studio_transport().request(
        'play',
        {},
        start_generation=3,
    )

    assert result['success'] is True
    assert [item['command'] for item in published] == ['play']
