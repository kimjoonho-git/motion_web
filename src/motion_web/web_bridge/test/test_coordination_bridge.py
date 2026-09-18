from pathlib import Path

from motion_common.paths import NO_PROJECT_SELECTED
from motion_web_bridge.coordination_bridge import (
    CoordinationWebBridge,
    local_motion_control,
    local_motion_readiness,
)


class _FakeExecutionContext:
    def reconcile(self):
        return {'ready': True, 'message': 'applied'}

    def reconcile_blocking(self, *, timeout_sec=10.0):
        return self.reconcile()


class _Node:
    def __init__(self):
        self.generation = [1]
        self.change_generation_on_publish = False


def _idle_service(node, root):
    """이 PC 에서 도는 연동 서비스에 붙지 않는다 · §6-168

    `_local_api` 를 막지 않으면 진짜 `localhost:8011` 로 나간다 · 이 PC 에서
    그룹이 돌고 있으면 시험이 「도는 중」을 읽고, 아무도 시작한 적 없는 실행
    때문에 설정 저장이 거부된다 · 실제로 그렇게 두 시험이 깨졌다.

    시험은 제 손으로 세운 상태만 본다.
    """
    service = CoordinationWebBridge(node, root, lambda: 1)
    service._local_api = lambda path, payload=None: {
        'success': True,
        'joined': True,
        'execution': {'state': 'stopped', 'execution_id': ''},
    }
    return service


class _Repository:
    def __init__(self, root: Path, *, selected='project-a'):
        self.root = root
        self.selected = selected

    def selected_project_id(self):
        return self.selected

    def require_selected_project_id(self):
        # 검사와 문구를 함께 하는 주인 · §6-169
        if not self.selected:
            raise ValueError(NO_PROJECT_SELECTED)
        return self.selected

    def get_project(self, _project_id):
        return {'project': {'active_files': {'motion_axis_matching': 'mapping.yaml'}}}

    def export_path(self, _project_id, category, name):
        path = self.root / category / name
        if not path.is_file():
            raise ValueError(f'missing {category}')
        return path


class _Bridge:
    def __init__(self, repository):
        self.project_repository = repository
        self.payload = None
        #: 실행 컨텍스트는 서비스가 갖는다 (§6-20)
        self._execution_context = _FakeExecutionContext()

    def motion_run_check(self, payload):
        self.payload = payload
        return {'success': True, 'status': {'state': 'ready'}}

    def motion_run_status(self):
        return {'status': {'automation': {'repeat_mode': 'dwell', 'dwell_sec': 0.25}}}

    def motion_group_prepare(self, payload):
        self.payload = payload
        return {'success': True}

    def motion_group_start_at(self, payload):
        self.payload = payload
        return {'success': True}

    def motion_group_initialize_at(self, payload):
        self.payload = payload
        return {'success': True}

    def motion_group_cancel(self, payload):
        self.payload = payload
        return {'success': True}

    def motion_run_stop(self):
        return {'success': True}

    def coordination_stop_now(self):
        self.payload = {'command': 'coordination_stop_now'}
        return {'success': True}

    def motion_run_stop_after_cycle(self):
        return {'success': True}


def _assets(tmp_path):
    mapping_dir = tmp_path / 'motion_axis_matching'
    motion_dir = tmp_path / 'motions'
    mapping_dir.mkdir()
    motion_dir.mkdir()
    (mapping_dir / 'mapping.yaml').write_text(
        'motion_file_id: local-motion.jsonl\n', encoding='utf-8'
    )
    (motion_dir / 'local-motion.jsonl').write_text('{}\n', encoding='utf-8')


def test_local_readiness_uses_only_local_active_files(tmp_path):
    _assets(tmp_path)
    bridge = _Bridge(_Repository(tmp_path))
    result = local_motion_readiness(bridge)
    assert result['success'] is True
    assert bridge.payload['motion_file_id'] == 'local-motion.jsonl'
    assert bridge.payload['request_source'] == 'network_readiness'


def test_group_prepare_preserves_local_between_cycle_setting_without_auto_repeat(tmp_path):
    _assets(tmp_path)
    bridge = _Bridge(_Repository(tmp_path))
    result = local_motion_control(bridge, {
        'command': 'group_prepare', 'execution_id': 'exec-a',
        'initialize_monotonic': 100.0, 'network_operation_id': 'command-a',
    })
    assert result['success'] is True
    assert bridge.payload['run_mode'] == 'once'
    assert bridge.payload['group_execution'] is True
    assert bridge.payload['repeat_mode'] == 'dwell'
    assert bridge.payload['dwell_sec'] == 0.25
    assert 'automation_run' not in bridge.payload


def test_each_group_start_at_schedules_exactly_one_cycle(tmp_path):
    _assets(tmp_path)
    bridge = _Bridge(_Repository(tmp_path))
    result = local_motion_control(bridge, {
        'command': 'group_start_at', 'execution_id': 'exec-a',
        'cycle_number': 3, 'start_monotonic': 100.0,
        'network_operation_id': 'command-b',
    })
    assert result['success'] is True
    assert bridge.payload['run_mode'] == 'once'
    assert bridge.payload['cycle_number'] == 3
    assert bridge.payload['start_monotonic'] == 100.0


def test_group_initialize_at_targets_the_existing_group_session(tmp_path):
    _assets(tmp_path)
    bridge = _Bridge(_Repository(tmp_path))
    result = local_motion_control(bridge, {
        'command': 'group_initialize_at', 'execution_id': 'exec-a',
        'cycle_number': 3, 'initialize_monotonic': 100.0,
        'network_operation_id': 'command-c',
    })
    assert result['success'] is True
    assert bridge.payload['execution_id'] == 'exec-a'
    assert bridge.payload['cycle_number'] == 3
    assert bridge.payload['initialize_monotonic'] == 100.0


def test_stop_now_uses_coordination_safety_stop_path(tmp_path):
    bridge = _Bridge(_Repository(tmp_path))

    result = local_motion_control(bridge, {'command': 'stop_now'})

    assert result['success'] is True
    assert bridge.payload == {'command': 'coordination_stop_now'}


def test_control_uses_loopback_local_api(tmp_path):
    node = _Node()
    service = CoordinationWebBridge(node, tmp_path, lambda: node.generation[0])
    calls = []
    service._local_api = lambda path, payload=None: (
        calls.append((path, payload)) or {'success': True, 'message': 'accepted'}
    )
    result = service.request_control({'command': 'join'})
    assert result['success'] is True
    assert calls == [('/control', {'command': 'join'})]


def test_group_error_acknowledgement_uses_loopback_local_api(tmp_path):
    node = _Node()
    service = CoordinationWebBridge(node, tmp_path, lambda: node.generation[0])
    calls = []
    service._local_api = lambda path, payload=None: (
        calls.append((path, payload)) or {'success': True, 'message': 'cleared'}
    )

    result = service.request_control({'command': 'acknowledge_group_error'})

    assert result['success'] is True
    assert calls == [('/control', {'command': 'acknowledge_group_error'})]


def test_active_dds_execution_blocks_local_start(tmp_path):
    node = _Node()
    service = CoordinationWebBridge(node, tmp_path, lambda: node.generation[0])
    service._local_api = lambda *_args, **_kwargs: {
        'execution': {'state': 'running', 'execution_id': 'exec-a'},
    }
    assert 'DDS 그룹 실행' in service.local_execution_blocker()


def test_stale_dds_display_state_without_lease_does_not_block_local_start(tmp_path):
    node = _Node()
    service = CoordinationWebBridge(node, tmp_path, lambda: node.generation[0])
    service._local_api = lambda *_args, **_kwargs: {
        'execution': {'state': 'preparing', 'execution_id': ''},
    }

    assert service.local_execution_blocker() == ''


def test_control_response_is_discarded_after_project_transition(tmp_path):
    node = _Node()
    node.change_generation_on_publish = True
    service = CoordinationWebBridge(node, tmp_path, lambda: node.generation[0])

    def request(*_args, **_kwargs):
        node.generation[0] += 1
        return {'success': True}
    service._local_api = request
    result = service.request_control({'command': 'start_group'})
    assert result['success'] is False
    assert result['stale_project_generation'] is True


def test_global_group_settings_do_not_modify_project_files(tmp_path, monkeypatch):
    project = tmp_path / 'projects/project-a/project.yaml'
    project.parent.mkdir(parents=True)
    project.write_text('name: A\n', encoding='utf-8')
    node = _Node()
    service = _idle_service(node, tmp_path)
    monkeypatch.setattr(service, '_restart_coordination_service', lambda: {
        'service_installed': False, 'restart_pending': False, 'message': 'not installed',
    })
    result = service.update_settings({
        'enabled': True, 'group_id': 'stage-a', 'dds_domain_id': 21,
        'display_name': 'PC A',
    })
    assert result['saved'] is True
    assert project.read_text(encoding='utf-8') == 'name: A\n'
    assert (tmp_path / 'config/motion_coordination.yaml').is_file()


def test_invalid_group_settings_do_not_replace_valid_file(tmp_path, monkeypatch):
    node = _Node()
    service = _idle_service(node, tmp_path)
    monkeypatch.setattr(service, '_restart_coordination_service', lambda: {
        'service_installed': True, 'restart_pending': True, 'message': 'ok',
    })
    service.update_settings({
        'enabled': True, 'group_id': 'stage-a', 'dds_domain_id': 21,
        'display_name': 'PC A',
    })
    original = (tmp_path / 'config/motion_coordination.yaml').read_text(encoding='utf-8')
    try:
        service.update_settings({
            'enabled': True, 'group_id': '', 'dds_domain_id': 500,
            'display_name': 'PC A',
        })
    except ValueError:
        pass
    else:
        raise AssertionError('invalid settings accepted')
    assert (tmp_path / 'config/motion_coordination.yaml').read_text(encoding='utf-8') == original
