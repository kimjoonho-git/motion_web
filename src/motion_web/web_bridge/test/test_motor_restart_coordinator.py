from pathlib import Path
import subprocess

import pytest

from motion_web_bridge.project_service import ProjectService
from motion_web_bridge.motor_restart_coordinator import MotorRestartCoordinator
from motion_web_bridge.motor_runtime_service import MotorRuntimeService
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


def _begin_restart(repository):
    return repository.begin_motor_operation(
        'motor_restart',
        'restart_requested',
        timeout_sec=45.0,
        details={
            'runtime_file': '/runtime/applied.yaml',
            'expected_axes': [0],
        },
    )


def test_worker_does_not_overwrite_a_terminal_operation(tmp_path):
    repository = ProjectRepository(tmp_path / 'projects')
    operation = _begin_restart(repository)

    def terminate_then_fail(_service):
        repository.finish_motor_operation(
            operation['operation_id'],
            'timeout',
            phase='timeout',
            error='작업 제한시간 초과',
        )
        raise RuntimeError('늦게 도착한 서비스 오류')

    coordinator = MotorRestartCoordinator(
        repository,
        lambda *_args: {'ready': False, 'failed': False},
        restart_service=terminate_then_fail,
        service_identity=lambda _service: {},
        sleep=lambda _seconds: None,
    )

    coordinator._restart_worker(operation['operation_id'], {})

    status = repository.motor_operation_status()
    assert status['status'] == 'timeout'
    assert status['error'] == '작업 제한시간 초과'


def test_systemd_timeout_is_reported_as_runtime_error(monkeypatch):
    def timeout(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(['/usr/bin/systemctl'], 3.0)

    monkeypatch.setattr(
        'motion_web_bridge.motor_restart_coordinator.subprocess.run',
        timeout,
    )

    with pytest.raises(RuntimeError, match='시간 초과'):
        MotorRestartCoordinator._read_service_identity(
            MotorRestartCoordinator.SERVICE
        )
