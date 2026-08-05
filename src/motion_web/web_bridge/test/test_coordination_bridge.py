import json
from pathlib import Path
import motion_web_bridge.coordination_bridge as coordination_bridge_module

from motion_web_bridge.coordination_bridge import (
    CoordinationWebBridge,
    local_motion_control,
    local_motion_readiness,
)


class _Publisher:
    def __init__(self, node):
        self.node = node

    def publish(self, message):
        request = json.loads(message.data)
        if self.node.change_generation_on_publish:
            self.node.generation[0] += 1
        callback = self.node.callbacks['/motion_coordination/response']
        response = type(message)(data=json.dumps({
            'request_id': request['request_id'],
            'success': True,
            'message': '전체 실행 준비 완료',
            'results': [],
        }))
        callback(response)


class _Node:
    def __init__(self):
        self.callbacks = {}
        self.generation = [1]
        self.change_generation_on_publish = False

    def create_publisher(self, _type, _topic, _depth):
        return _Publisher(self)

    def create_subscription(self, _type, topic, callback, _depth):
        self.callbacks[topic] = callback
        return object()


class _Repository:
    def __init__(self, root: Path, *, selected='project-a'):
        self.root = root
        self.selected = selected

    def selected_project_id(self):
        return self.selected

    def get_project(self, _project_id):
        return {'project': {'active_files': {
            'motion_axis_matching': 'mapping.yaml',
        }}}

    def export_path(self, _project_id, category, name):
        path = self.root / category / name
        if not path.is_file():
            raise ValueError(f'missing {category}')
        return path


class _Bridge:
    def __init__(self, repository):
        self.project_repository = repository
        self.payload = None

    def motion_run_check(self, payload):
        self.payload = payload
        return {'success': True, 'status': {'state': 'ready'}}

    def motion_run_start(self, payload):
        self.payload = payload
        return {'success': True, 'status': {'state': 'preparing'}}

    def motion_run_initialize(self, payload):
        self.payload = payload
        return {'success': True, 'status': {'state': 'preparing'}}

    def motion_run_stop(self):
        return {'success': True, 'status': {'state': 'stopping'}}


class _MultiRepository:
    def __init__(self, root: Path, selected: str):
        self.root = root
        self.selected = selected

    def selected_project_id(self):
        return self.selected

    def get_project(self, project_id):
        return {'project': {'active_files': {
            'motion_axis_matching': f'{project_id}-mapping.yaml',
        }}}

    def export_path(self, project_id, category, name):
        path = self.root / project_id / category / name
        if not path.is_file():
            raise ValueError('missing local asset')
        return path


def test_local_readiness_uses_only_local_active_files(tmp_path):
    mapping_dir = tmp_path / 'motion_axis_matching'
    motion_dir = tmp_path / 'motions'
    mapping_dir.mkdir()
    motion_dir.mkdir()
    (mapping_dir / 'mapping.yaml').write_text(
        'motion_file_id: local-motion.jsonl\n', encoding='utf-8'
    )
    (motion_dir / 'local-motion.jsonl').write_text('{}\n', encoding='utf-8')
    bridge = _Bridge(_Repository(tmp_path))

    result = local_motion_readiness(bridge)

    assert result['success'] is True
    assert bridge.payload['motion_file_id'] == 'local-motion.jsonl'
    assert bridge.payload['mapping_file_id'] == 'mapping.yaml'
    assert bridge.payload['request_source'] == 'network_readiness'


def test_local_readiness_requires_a_local_project(tmp_path):
    bridge = _Bridge(_Repository(tmp_path, selected=''))

    result = local_motion_readiness(bridge)

    assert result['success'] is False
    assert '프로젝트' in result['message']


def test_synchronized_local_control_uses_active_local_files(tmp_path):
    mapping_dir = tmp_path / 'motion_axis_matching'
    motion_dir = tmp_path / 'motions'
    mapping_dir.mkdir()
    motion_dir.mkdir()
    (mapping_dir / 'mapping.yaml').write_text(
        'motion_file_id: local-motion.jsonl\n', encoding='utf-8'
    )
    (motion_dir / 'local-motion.jsonl').write_text('{}\n', encoding='utf-8')
    bridge = _Bridge(_Repository(tmp_path))
    result = local_motion_control(bridge, {
        'command': 'start_at', 'network_operation_id': 'op-1',
        'lease_id': 'lease-1', 'start_at': 100.0,
        'cycle_sec': 2.0, 'repeat_count': 3,
    })
    assert result['success'] is True
    assert bridge.payload['run_mode'] == 'continuous'
    assert bridge.payload['motion_file_id'] == 'local-motion.jsonl'
    assert bridge.payload['scheduled_start_at'] == 100.0
    assert bridge.payload['request_source'] == 'network_control'


def test_two_projects_keep_their_local_motion_selection_isolated(tmp_path):
    for project_id in ('project-a', 'project-b'):
        mapping_dir = tmp_path / project_id / 'motion_axis_matching'
        motion_dir = tmp_path / project_id / 'motions'
        mapping_dir.mkdir(parents=True)
        motion_dir.mkdir(parents=True)
        (mapping_dir / f'{project_id}-mapping.yaml').write_text(
            f'motion_file_id: {project_id}-motion.jsonl\n', encoding='utf-8'
        )
        (motion_dir / f'{project_id}-motion.jsonl').write_text(
            '{}\n', encoding='utf-8'
        )
    repository = _MultiRepository(tmp_path, 'project-a')
    bridge = _Bridge(repository)
    local_motion_control(bridge, {
        'command': 'run_once', 'network_operation_id': 'op-a',
    })
    first = dict(bridge.payload)
    repository.selected = 'project-b'
    local_motion_control(bridge, {
        'command': 'run_once', 'network_operation_id': 'op-b',
    })
    second = dict(bridge.payload)
    assert first['motion_file_id'] == 'project-a-motion.jsonl'
    assert second['motion_file_id'] == 'project-b-motion.jsonl'
    assert first['mapping_file_id'] != second['mapping_file_id']


def test_readiness_response_is_returned_within_same_project_generation(tmp_path):
    node = _Node()
    service = CoordinationWebBridge(node, tmp_path, lambda: node.generation[0])

    result = service.request_readiness()

    assert result['success'] is True


def test_readiness_response_is_discarded_after_project_transition(tmp_path):
    node = _Node()
    node.change_generation_on_publish = True
    service = CoordinationWebBridge(node, tmp_path, lambda: node.generation[0])

    result = service.request_readiness()

    assert result['success'] is False
    assert result['stale_project_generation'] is True
    assert result['results'] == []


def test_network_execution_ownership_blocks_only_local_start(tmp_path):
    node = _Node()
    service = CoordinationWebBridge(node, tmp_path, lambda: node.generation[0])
    message = type('Message', (), {'data': json.dumps({
        'execution_control': {'state': 'network', 'owner': 'pc-a'},
    })})()
    node.callbacks['/motion_coordination/status'](message)
    assert '네트워크 동기 실행' in service.local_execution_blocker()


def test_control_response_is_discarded_after_project_transition(tmp_path):
    node = _Node()
    node.change_generation_on_publish = True
    service = CoordinationWebBridge(node, tmp_path, lambda: node.generation[0])
    result = service.request_control({'command': 'run_once'})
    assert result['success'] is False
    assert result['stale_project_generation'] is True


def test_pairing_offer_is_global_and_contains_no_project_data(tmp_path, monkeypatch):
    monkeypatch.setenv('HOME', str(tmp_path / 'home'))
    node = _Node()
    service = CoordinationWebBridge(node, tmp_path, lambda: node.generation[0])

    offer = service.start_pairing({
        'machine_id': 'pc-a',
        'display_name': 'PC A',
    })
    public_info = service.pairing_info()

    assert offer['pairing_code']
    assert public_info['coordinator_machine_id'] == 'pc-a'
    assert 'pairing_code' not in public_info
    assert 'project' not in str(public_info).lower()
    assert offer['service']['installed'] is False


def test_pairing_rejects_machine_id_change_when_existing_peer_is_registered(
    tmp_path,
):
    config = tmp_path / 'config/motion_coordination.yaml'
    config.parent.mkdir(parents=True)
    config.write_text(
        'version: 1\n'
        'machine_id: pc-a\n'
        'display_name: PC A\n'
        'mode: off\n'
        'role: peer\n'
        'peers:\n'
        '  - machine_id: pc-b\n'
        '    url: http://192.168.10.20:8010\n'
        'access: {}\n',
        encoding='utf-8',
    )
    node = _Node()
    service = CoordinationWebBridge(node, tmp_path, lambda: node.generation[0])

    try:
        service.start_pairing({
            'machine_id': 'renamed-pc-a', 'display_name': 'Renamed',
        })
    except ValueError as exc:
        assert 'PC ID를 변경할 수 없습니다' in str(exc)
    else:
        raise AssertionError('existing pairing must lock the local machine ID')


def test_pairing_reports_partial_when_either_pc_service_is_not_ready(
    tmp_path, monkeypatch,
):
    node = _Node()
    service = CoordinationWebBridge(node, tmp_path, lambda: node.generation[0])
    monkeypatch.setattr(coordination_bridge_module, 'join_pairing', lambda *args, **kwargs: {
        'success': True,
        'paired': True,
        'message': 'saved',
        'central_restart': {
            'service_installed': False,
            'restart_pending': False,
            'message': '중앙 서비스 미설치',
        },
    })
    monkeypatch.setattr(service, '_restart_coordination_service', lambda: {
        'service_installed': True,
        'restart_pending': True,
        'message': '참여 서비스 재시작 요청 완료',
    })

    result = service.join_pairing({
        'coordinator_host': '192.168.10.10',
        'pairing_code': 'ABCD-EFGH',
        'machine_id': 'pc-b',
        'display_name': 'PC B',
    })

    assert result['success'] is False
    assert result['configuration_saved'] is True
    assert result['operation_state'] == 'partial'
    assert '중앙 PC' in result['message']
