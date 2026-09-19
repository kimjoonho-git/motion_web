"""모터 런타임 수명주기 · 서비스 기동 · 복구 대기 · 조작 상태 조정.

`MotionWebBridge`에서 떼어냈다 · §6-22

`ScanOrchestrator`(§6-18)와 `MotorConfigService`(§6-19)가 노드를 거쳐 쓰고 있던
도우미들을 한곳에 모았다. 둘 다 이 객체를 **직접** 받는다 · 노드를 거치지 않는다.

여기 있는 것

- systemd 사용자 서비스 기동·정지 판정
- 스캔 뒤 모터 런타임이 돌아왔는지 기다리는 판정
- EtherCAT 재열거 안전 차단 판정 (움직이는 축이 있으면 막는다)
- 중단된 스캔 복구와 모터 조작 상태 조정
"""

from __future__ import annotations

import copy
import json
import subprocess
import threading
import time
from functools import partial
from pathlib import Path
from typing import Any, Dict, List

from motion_common.values import optional_float

from motion_web_bridge import motor_config_rules
from motion_web_bridge.bridge_helpers import _monitoring_finite_float
from motion_web_bridge.motor_restart_coordinator import MotorRestartCoordinator
from motion_web_bridge.motor_restart_diagnostics import diagnose_motor_restart_failure


class MotorRuntimeService:
    def __init__(
        self,
        bridge: Any,
        *,
        project: Any,
        repository: Any,
        workspace_root: Path,
        restart_coordinator: Any = None,
    ) -> None:
        self.bridge = bridge
        #: 프로젝트 서비스 협력자 (§6-23)
        self.project = project
        self.repository = repository
        self.workspace_root = workspace_root
        self._restart_coordinator = restart_coordinator
        self._recovery_lock = threading.Lock()
        self._reconcile_lock = threading.Lock()

    @staticmethod
    def managed_service_active(service: str) -> bool:
        completed = subprocess.run(
            ['/usr/bin/systemctl', '--user', 'is-active', '--quiet', service],
            check=False,
            capture_output=True,
            text=True,
            timeout=3.0,
        )
        return completed.returncode == 0

    @staticmethod
    def run_managed_service(action: str, service: str) -> None:
        if action not in {'start', 'stop'} or service != 'motion-motor.service':
            raise ValueError('허용되지 않은 Motor Manager 서비스 작업입니다')
        completed = subprocess.run(
            ['/usr/bin/systemctl', '--user', action, service],
            check=False,
            capture_output=True,
            text=True,
            timeout=10.0,
        )
        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip()
            raise RuntimeError(detail or f'{service} {action} 실패')

    def restart_lifecycle(self) -> MotorRestartCoordinator:
        coordinator = getattr(self, '_restart_coordinator', None)
        if coordinator is None:
            coordinator = MotorRestartCoordinator(
                self.repository,
                partial(
                motor_config_rules.motor_operation_runtime_readiness,
                self.repository,
            ),
            )
            self._restart_coordinator = coordinator
        return coordinator

    def ethercat_scan_safety_blocker(
        self,
        *,
        require_fresh_motor_state: bool = True,
        allow_run_stopping: bool = False,
        allow_studio_stopping: bool = False,
    ) -> str:
        blocker = self.project.change_blocker(
            ignore_motor_lifecycle=True,
            allow_run_stopping=allow_run_stopping,
            allow_studio_stopping=allow_studio_stopping,
        )
        if blocker:
            return blocker

        motion_state, received_at = self.bridge.motion_state_with_time()
        if not isinstance(motion_state, dict) or received_at is None:
            return (
                '최신 모터 상태를 확인할 수 없습니다'
                if require_fresh_motor_state else ''
            )
        if time.time() - float(received_at) > 1.0:
            return (
                '최신 모터 상태가 중단되어 정지 여부를 확인할 수 없습니다'
                if require_fresh_motor_state else ''
            )

        moving_axes = []
        observed_axes = []
        for motor in motion_state.get('motors') or []:
            if not isinstance(motor, dict):
                continue
            if str(motor.get('transport') or '').lower() != 'ethercat':
                continue
            observed_axes.append(str(motor.get('controller_index', '?')))
            if (
                motor.get('connection_connected') is not True
                or str(motor.get('connection_state') or '') != 'online'
                or motor.get('fault') is True
            ):
                continue
            velocity = _monitoring_finite_float(
                motor.get('velocity_deg_s', motor.get('velocity'))
            )
            target_reached = motor.get('target_reached') is True
            # A stopped servo can report roughly 1~2 deg/s of quantization
            # noise.  Ignore that noise only when the drive also reports that
            # its target has been reached.  Missing/false target state keeps
            # the stricter threshold, while clear motion is always blocked.
            moving = (
                velocity is not None
                and (
                    abs(velocity) > 5.0
                    or (not target_reached and abs(velocity) > 1.0)
                )
            )
            if moving:
                moving_axes.append(str(motor.get('controller_index', '?')))
        if require_fresh_motor_state and not observed_axes:
            return '최신 모터 상태에서 AC Servo 축을 확인할 수 없습니다'
        if moving_axes:
            return (
                f'AC Servo 축 {", ".join(moving_axes)}이 움직이는 중입니다. '
                '완전히 정지한 뒤 다시 검색하세요'
            )
        return ''

    @staticmethod
    def _missing_axis_message(operation: Dict[str, Any], state_payload: Any) -> str:
        """어느 축이 안 올라왔는지 말해 준다 · §6-199

        전에는 「45.1초 동안 완료 조건을 확인하지 못했습니다」 뿐이었다 ·
        서버는 어느 축인지 이미 알고 있는데 말하지 않았다 · 사용자는 네 축을
        하나씩 뒤져야 했다.
        """
        details = operation.get('details')
        expected = (details or {}).get('expected_axes') if isinstance(details, dict) else None
        expected = sorted(set(int(axis) for axis in (expected or [])))
        online = set()
        motors = (state_payload or {}).get('motors') if isinstance(state_payload, dict) else None
        for motor in motors or []:
            if isinstance(motor, dict) and motor.get('connection_connected') is True:
                try:
                    online.add(int(motor.get('controller_index')))
                except (TypeError, ValueError):
                    continue
        missing = [axis for axis in expected if axis not in online]
        if not missing:
            return '모터 설정 적용 제한시간을 초과했습니다'
        return (
            f'{", ".join(str(axis) for axis in missing)}번 축이 올라오지 않았습니다 · '
            f'전원·통신선·드라이버 상태를 확인하세요 '
            f'(붙은 축 {len(expected) - len(missing)}/{len(expected)})'
        )

    def wait_for_runtime_recovery(
        self,
        expected_axes: List[int],
        timeout_sec: float,
        motor_service: str = '',
    ) -> Dict[str, Any]:
        expected = sorted(set(int(axis) for axis in expected_axes))
        started_at = time.time()
        online_axes: List[int] = []
        if not expected:
            return {
                'required': True,
                'expected_axes': [],
                'online_axes': [],
                'recovered': False,
                'missing_axes': [],
                'service_active': (
                    not motor_service
                    or self.managed_service_active(motor_service)
                ),
                'duration_sec': 0.0,
                'error': '복구 대상 EtherCAT 축을 확인할 수 없습니다',
            }
        while time.time() - started_at < timeout_sec:
            service_active = (
                not motor_service
                or self.managed_service_active(motor_service)
            )
            motion_state, received_at = self.bridge.motion_state_with_time()
            online_axes = []
            if (
                isinstance(motion_state, dict)
                and received_at is not None
                and float(received_at) >= started_at
            ):
                for motor in motion_state.get('motors') or []:
                    if not isinstance(motor, dict):
                        continue
                    if motor.get('connection_connected') is not True:
                        continue
                    if motor.get('fault') is True:
                        continue
                    try:
                        online_axes.append(int(motor.get('controller_index')))
                    except (TypeError, ValueError):
                        continue
                online_axes = sorted(set(online_axes))
                if service_active and all(axis in online_axes for axis in expected):
                    return {
                        'required': True,
                        'expected_axes': expected,
                        'online_axes': online_axes,
                        'recovered': True,
                        'missing_axes': [],
                        'service_active': True,
                        'duration_sec': round(time.time() - started_at, 3),
                    }
            time.sleep(0.05)
        return {
            'required': True,
            'expected_axes': expected,
            'online_axes': online_axes,
            'recovered': False,
            # 돌아오지 않은 축 · **실패인지 아닌지는 부르는 쪽이 판단한다** · §6-196
            #
            # 사람이 직접 검색을 눌렀다면 모터가 빠졌거나 알람인 것을 이미
            # 알고 있다 · 그때 이 값은 「실패」가 아니라 「이것들이 없더라」다 ·
            # Motor Manager 자체가 안 돌아온 것과는 다른 일이라 갈라 놓는다.
            'missing_axes': [axis for axis in expected if axis not in online_axes],
            'service_active': (
                not motor_service
                or self.managed_service_active(motor_service)
            ),
            'duration_sec': round(time.time() - started_at, 3),
        }

    def recover_interrupted_scan(self, operation: Dict[str, Any]) -> None:
        lock = getattr(self, '_recovery_lock', None)
        operation_id = str(operation.get('operation_id') or '')
        details = operation.get('details')
        details = dict(details) if isinstance(details, dict) else {}
        was_active = details.get('motor_service_was_active') is True
        expected_axes = details.get('expected_axes')
        expected_axes = list(expected_axes) if isinstance(expected_axes, list) else []
        try:
            if not was_active:
                self.repository.runtime.finish_motor_operation(
                    operation_id,
                    'failure',
                    phase='interrupted',
                    error='브리지 종료로 AC Servo 검색 결과를 확인할 수 없습니다',
                )
                return
            self.run_managed_service('start', 'motion-motor.service')
            recovery = self.wait_for_runtime_recovery(
                expected_axes,
                timeout_sec=12.0,
                motor_service='motion-motor.service',
            )
            if recovery.get('recovered') is True:
                self.repository.runtime.finish_motor_operation(
                    operation_id,
                    'failure',
                    phase='interrupted_recovered',
                    error=(
                        '브리지 종료로 AC Servo 검색 결과를 확인할 수 없습니다. '
                        'Motor Manager는 검색 전 실행 상태로 복구했습니다'
                    ),
                    details={'recovery': recovery},
                )
            else:
                self.repository.runtime.finish_motor_operation(
                    operation_id,
                    'failure',
                    phase='restore_failed',
                    error='중단된 AC Servo 검색 후 Motor Manager 복구에 실패했습니다',
                    details={'recovery': recovery},
                )
        except (OSError, RuntimeError, subprocess.TimeoutExpired, ValueError) as exc:
            try:
                self.repository.runtime.finish_motor_operation(
                    operation_id,
                    'failure',
                    phase='restore_failed',
                    error=f'중단된 AC Servo 검색 복구 실패: {exc}',
                )
            except ValueError:
                pass
        finally:
            if lock is not None and lock.locked():
                lock.release()

    def schedule_interrupted_scan_recovery(
        self,
        operation: Dict[str, Any],
    ) -> Dict[str, Any]:
        lock = getattr(self, '_recovery_lock', None)
        if lock is None:
            lock = threading.Lock()
            self._recovery_lock = lock
        if not lock.acquire(blocking=False):
            return self.repository.runtime.motor_operation_status()
        operation_id = str(operation.get('operation_id') or '')
        try:
            updated = self.repository.runtime.update_motor_operation(
                operation_id,
                'restoring_after_bridge_restart',
                message='중단된 AC Servo 검색의 Motor Manager 복구 중',
                timeout_sec=20.0,
            )
        except ValueError:
            lock.release()
            return self.repository.runtime.motor_operation_status()
        threading.Thread(
            target=self._recover_interrupted_scan,
            args=(updated,),
            name='interrupted-ac-servo-scan-recovery',
            daemon=True,
        ).start()
        return updated

    def reconcile_operation_status(
        self,
        runtime_status: Dict[str, Any],
        motion_state: Any,
        execution_context: Dict[str, Any],
    ) -> Dict[str, Any]:
        repository = getattr(self, 'repository', None)
        if repository is None or not hasattr(getattr(repository, 'runtime', None), 'motor_operation_status'):
            return {}
        operation = repository.runtime.motor_operation_status()
        if not operation:
            return {}
        operation_id = str(operation.get('operation_id') or '')
        status = str(operation.get('status') or '')
        operation_type = str(operation.get('type') or '')
        started_at = float(operation.get('started_at') or 0.0)
        state_payload = motion_state if isinstance(motion_state, dict) else {}
        bridge_restarted = float(
            getattr(self.bridge, '_bridge_started_at', 0.0) or 0.0
        ) > started_at
        if (
            operation_type in {'ac_servo_scan', 'full_scan'}
            and status in {'running', 'timeout'}
            and bridge_restarted
        ):
            return self.schedule_interrupted_scan_recovery(operation)
        if status == 'timeout':
            # ``motor_operation_status`` synthesizes phase=timeout only while
            # the stored operation is still running.  A terminal timeout with
            # another phase has already been handled and must never schedule
            # rollback/restart again after the next snapshot or bridge start.
            if str(operation.get('phase') or '') != 'timeout':
                return operation
            if operation_type == 'motor_apply':
                # **되돌리지 않는다** · §6-199
                #
                # 전에는 여기서 옛 설정으로 롤백하고 두 서비스를 또
                # 재시작했다 · 그런데 **새 설정은 이미 잘 돌고 있다** ·
                # 축 넷 중 셋이 붙었는데 하나가 안 붙었을 뿐이다.
                #
                # 되돌리면 잘 붙은 셋까지 잃고, 화면엔 엉뚱하게 옛 프로젝트가
                # 뜬다 · 사용자는 「내가 만든 프로젝트가 왜 사라졌지」가 된다 ·
                # 게다가 재시작이 한 번 더 돌아 30초를 더 쓴다.
                #
                # 안 붙은 축은 **말로 알린다** · 고치는 것은 사람의 몫이다.
                return repository.runtime.finish_motor_operation(
                    operation_id,
                    'timeout',
                    phase='incomplete',
                    error=self._missing_axis_message(operation, state_payload),
                )
            if operation_type == 'motor_restart':
                diagnosis = diagnose_motor_restart_failure(
                    operation,
                    state_payload,
                    runtime_status,
                )
                try:
                    return repository.runtime.finish_motor_operation(
                        operation_id,
                        'timeout',
                        phase='timed_out',
                        error=str(diagnosis['message']),
                        details={
                            'failure_code': diagnosis['failure_code'],
                            'pending_axes': diagnosis['pending_axes'],
                            'pending_connections': diagnosis['pending_connections'],
                        },
                    )
                except ValueError:
                    return operation
            try:
                return repository.runtime.finish_motor_operation(
                    operation_id,
                    'timeout',
                    phase='timed_out',
                    error=str(operation.get('error') or '모터 작업 제한시간을 초과했습니다'),
                )
            except ValueError:
                return operation
        if status != 'running':
            return operation
        # Scan operations own their preparation/scanning/restoring phases.
        # Applying restart/apply readiness rules here can terminate a scan
        # while it temporarily stops Motor Manager for EtherCAT ownership.
        if operation_type in {
            'ac_servo_scan',
            'dynamixel_scan',
            'full_scan',
            'motor_scan',
        }:
            return operation

        if operation_type == 'motor_restart':
            return self.restart_lifecycle().reconcile(
                operation,
                runtime_status,
                state_payload,
            )
        last_motor_status_at = optional_float(
            state_payload.get('last_motor_status_at'), None
        )
        fresh_feedback = bool(
            last_motor_status_at is not None
            and last_motor_status_at > started_at
        )
        runtime_ready = runtime_status.get('phase') == 'ready'
        readiness = (
            motor_config_rules.motor_operation_runtime_readiness(self.repository, 
                operation,
                state_payload,
                runtime_status,
            )
            if runtime_ready and fresh_feedback
            else {'ready': False, 'failed': False, 'error': ''}
        )
        if readiness.get('failed') is True:
            error = str(readiness.get('error') or 'Motor Manager 실행 검증 실패')
            # 프로젝트를 되돌리지 않는다 · §6-199 · 고른 사람은 사용자다
            return repository.runtime.finish_motor_operation(
                operation_id,
                'failure',
                phase='failed',
                error=error,
            )
        if operation_type == 'motor_apply':
            if (
                bridge_restarted
                and runtime_ready
                and fresh_feedback
                and readiness.get('ready') is True
            ):
                return repository.runtime.finish_motor_operation(
                    operation_id,
                    'success',
                    phase='completed',
                    message='모터 설정 적용·재시작 완료',
                )
            if (
                bridge_restarted
                and runtime_status.get('phase') in {
                    'motor_manager_start_blocked',
                    'motor_manager_disabled',
                    'runtime_config_mismatch',
                }
            ):
                # 여기서도 되돌리지 않는다 · §6-199
                #
                # Motor Manager 가 아예 못 뜬 경우다 · 전에는 옛 프로젝트로
                # 되돌려 「최소한 뭔가는 돌게」 했다 · 그런데 그러면 사용자가
                # 고르지도 않은 프로젝트가 장비에 올라간다 · 어느 설정으로
                # 움직이는지 사람이 모르는 것이 더 위험하다.
                return repository.runtime.finish_motor_operation(
                    operation_id,
                    'failure',
                    phase='failed',
                    error=str(
                        runtime_status.get('message')
                        or 'Motor Manager 시작 실패'
                    ),
                )
        return operation

    def reconcile_callback(self) -> None:
        lock = getattr(self, '_reconcile_lock', None)
        if lock is None:
            lock = threading.Lock()
            self._reconcile_lock = lock
        if not lock.acquire(blocking=False):
            return
        try:
            motion_state = self.bridge.motion_state()
            runtime_status = motor_config_rules.runtime_service_status(
                motion_state,
                applied_motor_config_file=getattr(getattr(self.bridge, '_motor_config', None), 'applied', None),
                repository=getattr(self, 'repository', None),
                workspace_root=getattr(self, 'workspace_root', Path()),
            )
            execution_context = self.bridge.execution_context_status(validate_files=False)
            self.reconcile_operation_status(
                runtime_status,
                motion_state,
                execution_context,
            )
        except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
            self.bridge.get_logger().error(
                f'Motor operation reconcile failed: {exc}'
            )
        finally:
            lock.release()
