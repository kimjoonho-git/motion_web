"""Motor Manager restart lifecycle.

This module owns the service-generation boundary for an explicit Motor Manager
restart.  The web bridge supplies runtime readiness rules, but it does not
schedule the restart or decide when that restart operation is complete.
"""

import math
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Optional


ReadinessCheck = Callable[
    [Dict[str, Any], Dict[str, Any], Dict[str, Any]],
    Dict[str, Any],
]


class MotorRestartCoordinator:
    SERVICE = 'motion-motor.service'

    def __init__(
        self,
        repository: Any,
        readiness_check: ReadinessCheck,
        *,
        service_identity: Optional[Callable[[str], Dict[str, Any]]] = None,
        restart_service: Optional[Callable[[str], None]] = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.time,
        thread_factory: Callable[..., threading.Thread] = threading.Thread,
    ) -> None:
        self._repository = repository
        self._readiness_check = readiness_check
        self._service_identity = service_identity or self._read_service_identity
        self._restart_service = restart_service or self._restart_managed_service
        self._sleep = sleep
        self._clock = clock
        self._thread_factory = thread_factory

    def begin(
        self,
        *,
        project_id: str,
        runtime_file: Path,
        expected_axes: Iterable[int],
    ) -> Dict[str, Any]:
        identity_before = self._service_identity(self.SERVICE)
        if (
            identity_before.get('active_state') != 'active'
            or int(identity_before.get('main_pid') or 0) <= 0
        ):
            raise RuntimeError('Motor Manager 서비스가 실행 중이 아닙니다')

        operation = self._repository.begin_motor_operation(
            'motor_restart',
            'restart_requested',
            timeout_sec=45.0,
            details={
                'project_id': str(project_id),
                'runtime_file': str(runtime_file),
                'expected_axes': [int(axis) for axis in expected_axes],
                'service_main_pid_before': int(
                    identity_before.get('main_pid') or 0
                ),
                'service_invocation_id_before': str(
                    identity_before.get('invocation_id') or ''
                ),
                'service_started_monotonic_before': int(
                    identity_before.get('started_monotonic') or 0
                ),
            },
        )
        try:
            self._schedule(operation['operation_id'], identity_before)
        except (OSError, RuntimeError, ValueError) as exc:
            self._repository.finish_motor_operation(
                operation['operation_id'],
                'failure',
                phase='failed',
                error=str(exc),
            )
            raise
        return operation

    def reconcile(
        self,
        operation: Dict[str, Any],
        runtime_status: Dict[str, Any],
        motion_state: Dict[str, Any],
    ) -> Dict[str, Any]:
        if (
            str(operation.get('type') or '') != 'motor_restart'
            or str(operation.get('status') or '') != 'running'
        ):
            return operation

        details = operation.get('details')
        details = dict(details) if isinstance(details, dict) else {}
        restart_observed_at = self._finite_float(
            details.get('restart_observed_at')
        )
        if (
            str(operation.get('phase') or '') != 'verifying'
            or restart_observed_at is None
        ):
            return operation

        last_motor_status_at = self._finite_float(
            motion_state.get('last_motor_status_at')
        )
        if (
            runtime_status.get('phase') != 'ready'
            or last_motor_status_at is None
            or last_motor_status_at <= restart_observed_at
        ):
            return operation

        readiness = self._readiness_check(
            operation,
            motion_state,
            runtime_status,
        )
        operation_id = str(operation.get('operation_id') or '')
        if readiness.get('failed') is True:
            return self._repository.finish_motor_operation(
                operation_id,
                'failure',
                phase='failed',
                error=str(
                    readiness.get('error')
                    or 'Motor Manager 실행 검증 실패'
                ),
            )
        if readiness.get('ready') is True:
            return self._repository.finish_motor_operation(
                operation_id,
                'success',
                phase='completed',
                message='모터 제어 재시작 완료',
            )
        return operation

    def _schedule(
        self,
        operation_id: str,
        identity_before: Dict[str, Any],
    ) -> None:
        worker = self._thread_factory(
            target=self._restart_worker,
            args=(str(operation_id), dict(identity_before)),
            name=f'motor-restart-{str(operation_id)[-8:]}',
            daemon=True,
        )
        worker.start()

    def _restart_worker(
        self,
        operation_id: str,
        identity_before: Dict[str, Any],
    ) -> None:
        try:
            # Allow the HTTP response containing operation_id to leave first.
            self._sleep(0.5)
            self._repository.update_motor_operation(
                operation_id,
                'restarting',
                message='Motor Manager 서비스를 재시작하는 중입니다',
            )
            self._restart_service(self.SERVICE)
            identity_after = self._service_identity(self.SERVICE)
            self._validate_new_generation(identity_before, identity_after)
            self._repository.update_motor_operation(
                operation_id,
                'verifying',
                message='새 Motor Manager 실행과 모터 상태를 확인하는 중입니다',
                details={
                    'restart_observed_at': self._clock(),
                    'service_main_pid_after': int(
                        identity_after.get('main_pid') or 0
                    ),
                    'service_invocation_id_after': str(
                        identity_after.get('invocation_id') or ''
                    ),
                    'service_started_monotonic_after': int(
                        identity_after.get('started_monotonic') or 0
                    ),
                },
            )
        except (OSError, RuntimeError, ValueError) as exc:
            self._finish_failure_if_running(operation_id, str(exc))

    @staticmethod
    def _validate_new_generation(
        identity_before: Dict[str, Any],
        identity_after: Dict[str, Any],
    ) -> None:
        if (
            identity_after.get('active_state') != 'active'
            or int(identity_after.get('main_pid') or 0) <= 0
        ):
            raise RuntimeError(
                'Motor Manager 서비스가 active 상태로 복구되지 않았습니다'
            )
        before_invocation = str(identity_before.get('invocation_id') or '')
        after_invocation = str(identity_after.get('invocation_id') or '')
        before_pid = int(identity_before.get('main_pid') or 0)
        after_pid = int(identity_after.get('main_pid') or 0)
        if (
            before_invocation
            and after_invocation
            and before_invocation == after_invocation
        ):
            raise RuntimeError('Motor Manager 서비스 실행 세대가 변경되지 않았습니다')
        if not before_invocation and before_pid == after_pid:
            raise RuntimeError('Motor Manager 서비스 PID가 변경되지 않았습니다')

    def _finish_failure_if_running(
        self,
        operation_id: str,
        error: str,
    ) -> None:
        current = self._repository.motor_operation_status()
        if (
            str(current.get('operation_id') or '') != operation_id
            or str(current.get('status') or '') != 'running'
        ):
            return
        try:
            self._repository.finish_motor_operation(
                operation_id,
                'failure',
                phase='failed',
                error=error,
            )
        except ValueError:
            pass

    @classmethod
    def _read_service_identity(cls, service: str) -> Dict[str, Any]:
        cls._validate_service(service)
        try:
            completed = subprocess.run(
                [
                    '/usr/bin/systemctl',
                    '--user',
                    'show',
                    service,
                    '--property=ActiveState',
                    '--property=SubState',
                    '--property=MainPID',
                    '--property=InvocationID',
                    '--property=ExecMainStartTimestampMonotonic',
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=3.0,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f'{service} 실행 상태 확인 시간 초과') from exc
        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip()
            raise RuntimeError(detail or f'{service} 실행 상태 확인 실패')
        values: Dict[str, str] = {}
        for line in completed.stdout.splitlines():
            key, separator, value = line.partition('=')
            if separator:
                values[key.strip()] = value.strip()
        try:
            return {
                'active_state': values.get('ActiveState', ''),
                'sub_state': values.get('SubState', ''),
                'main_pid': int(values.get('MainPID') or 0),
                'invocation_id': values.get('InvocationID', ''),
                'started_monotonic': int(
                    values.get('ExecMainStartTimestampMonotonic') or 0
                ),
            }
        except ValueError as exc:
            raise RuntimeError(
                f'{service} 실행 식별정보를 해석할 수 없습니다'
            ) from exc

    @classmethod
    def _restart_managed_service(cls, service: str) -> None:
        cls._validate_service(service)
        try:
            completed = subprocess.run(
                ['/usr/bin/systemctl', '--user', 'restart', service],
                check=False,
                capture_output=True,
                text=True,
                timeout=10.0,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f'{service} restart 시간 초과') from exc
        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip()
            raise RuntimeError(detail or f'{service} restart 실패')

    @classmethod
    def _validate_service(cls, service: str) -> None:
        if service != cls.SERVICE:
            raise ValueError('허용되지 않은 Motor Manager 서비스입니다')

    @staticmethod
    def _finite_float(value: Any) -> Optional[float]:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if math.isfinite(number) else None
