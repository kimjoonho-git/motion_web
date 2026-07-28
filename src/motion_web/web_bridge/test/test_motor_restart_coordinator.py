import subprocess

import pytest

from motion_web_bridge.motor_restart_coordinator import MotorRestartCoordinator
from motion_web_bridge.project_repository import ProjectRepository


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
