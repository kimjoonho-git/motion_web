"""모터 물리 검색 조율 · 스캔 요청 · 진행 상태 · EtherCAT 재열거.

`MotionWebBridge`에서 떼어냈다 · §5 분해 목표안의 `ScanOrchestrator` · §6-18

**모터 스캔 영구 불변조건은 그대로다.** 이 클래스는 스캔의 *조율*만 옮긴 것이고
물리 검색 자체는 `motion_system`의 스캔 서비스가 수행한다. `ethercat rescan` 요구도
`scan_contract`도 건드리지 않았다.

이 서비스가 갖는 것 · 스캔 요청 락 · 진행 상태와 그 락 · 스캔 클라이언트 3종.
`lifecycle_lock`은 노드가 갖고 넘겨준다 · 설정 적용·재시작과 **같은 락**이어야
"다른 모터 작업이 진행 중"이 성립한다.
"""

from __future__ import annotations

import copy
import json
import os
import subprocess
import threading
import time
from typing import Any, Dict, List

from rclpy.action import ActionClient
from std_msgs.msg import String
from std_srvs.srv import Trigger

from motion_common import topics
from motion_coordination_interfaces.action import MotorScan

from motion_web_bridge.motor_runtime_store import MOTOR_BUSY_MESSAGE
from motion_web_bridge import (
    ethercat_project_compat,
    motion_file_analysis,
    motor_config_rules,
)


#: 장치 종류별 물리 검색 시한.
#:
#: **전체 검색은 두 종류를 차례로 돌리므로 합보다 짧으면 안 된다.** §6-16에서
#: Dynamixel 시한만 20 → 40초로 올리고 전체 검색은 20초로 남겨두어, 장치가 늘면
#: 단독 검색은 되는데 전체 검색만 시한 초과로 실패하는 상태였다. 값을 한 곳에서
#: 유도해 다시 갈라지지 않게 한다 · §6-37
AC_SERVO_SCAN_TIMEOUT_SEC = 10.0
DYNAMIXEL_SCAN_TIMEOUT_SEC = 40.0
FULL_SCAN_TIMEOUT_SEC = AC_SERVO_SCAN_TIMEOUT_SEC + DYNAMIXEL_SCAN_TIMEOUT_SEC


class ScanOrchestrator:
    def __init__(
        self,
        bridge: Any,
        *,
        project: Any,
        runtime: Any,
        lifecycle_lock: threading.Lock,
        repository: Any,
        scan_client: Any,
        scan_ac_servo_client: Any,
        scan_dynamixel_client: Any,
        scan_service: str,
        scan_ac_servo_service: str,
        scan_dynamixel_service: str,
        load_motor_config: Any,
    ) -> None:
        self.bridge = bridge
        #: 프로젝트 서비스 협력자 (§6-23)
        self.project = project
        #: 모터 런타임 수명주기 협력자 (§6-22)
        self.runtime = runtime
        #: 설정 적용·재시작과 공유한다 · 노드가 소유
        self.lifecycle_lock = lifecycle_lock
        self.repository = repository
        self._scan_client = scan_client
        self._scan_ac_servo_client = scan_ac_servo_client
        self._scan_dynamixel_client = scan_dynamixel_client
        self.scan_service = scan_service
        self.scan_ac_servo_service = scan_ac_servo_service
        self.scan_dynamixel_service = scan_dynamixel_service
        #: 프로젝트 설정을 읽어오는 콜러블 · `MotorConfigService.load` (§6-19)
        self.load_motor_config = load_motor_config
        #: 장기 작업 Action · 진행 상황과 취소를 같은 통로로 (§6-26)
        #: 실제 노드가 있을 때만 만든다 · 노드 없는 시험 스텁도 이 클래스를 쓴다
        self._action_client = None
        self._active_goal_handle = None
        self._goal_lock = threading.Lock()
        self._scan_request_lock = threading.Lock()
        #: 노드에서 그대로 옮겼다 · 진행 이벤트를 락 안에서 다시 읽는다
        self._progress_lock = threading.RLock()
        self._progress: Dict[str, Any] = self._empty_progress()
        #: 중복 진행 이벤트를 거르는 표식 · 토픽과 Action이 같은 것을 보낸다
        self._seen_events: set = set()

    @staticmethod
    def _empty_progress() -> Dict[str, Any]:
        return {
            'scan_id': '',
            'events': [],
            'running': False,
            'updated_at': None,
        }

    def clear_progress(self) -> None:
        """프로젝트가 바뀌면 이전 스캔 진행 상태를 남기지 않는다."""
        with self._progress_lock:
            self._progress = self._empty_progress()
            self._seen_events = set()

    def progress_callback(self, msg: String) -> None:
        try:
            event = json.loads(msg.data)
        except json.JSONDecodeError:
            self.bridge.get_logger().warn(f'Invalid {self.scan_progress_topic} JSON received.')
            return
        self.record_progress_event(event)

    def record_progress_event(self, event: Any) -> None:
        """진행 이벤트 하나를 기록한다.

        토픽으로도 오고 Action feedback으로도 온다 · 같은 항목이므로 같은 곳에
        쌓는다 · §6-26
        """
        if not isinstance(event, dict) or not str(event.get('scan_id') or ''):
            return
        now = time.time()
        with self._progress_lock:
            scan_id = str(event['scan_id'])
            if scan_id != self._progress.get('scan_id'):
                self._progress = {
                    'scan_id': scan_id,
                    'events': [],
                    'running': True,
                    'started_at': event.get('timestamp') or now,
                    'updated_at': now,
                    'project_id': self.repository.selected_project_id(),
                    'project_generation': self.bridge.current_project_generation(),
                }
                self._seen_events = set()
            # 같은 이벤트가 토픽과 Action feedback 양쪽으로 온다 · 한 번만 센다
            key = (
                scan_id,
                str(event.get('phase') or ''),
                str(event.get('transport') or ''),
                float(event.get('timestamp') or 0.0),
            )
            seen = getattr(self, '_seen_events', None)
            if seen is None:
                seen = set()
                self._seen_events = seen
            if key in seen:
                return
            seen.add(key)
            events = self._progress.setdefault('events', [])
            recorded = dict(event)
            recorded['index'] = len(events)
            events.append(recorded)
            if len(events) > 300:
                del events[:-300]
                for index, item in enumerate(events):
                    item['index'] = index
            self._progress['updated_at'] = now
            if event.get('phase') in {'complete', 'completed', 'partial', 'failed'}:
                self._progress['running'] = False
                self._progress['completed_at'] = now

    def progress(self) -> Dict[str, Any]:
        with self._progress_lock:
            progress = copy.deepcopy(self._progress)
        return {
            'success': True,
            'progress': progress,
            'project_id': self.repository.selected_project_id(),
            'project_generation': self.bridge.current_project_generation(),
        }

    def scan_all(self, timeout_sec: float = FULL_SCAN_TIMEOUT_SEC) -> Dict[str, Any]:
        return self._call_service(
            self._scan_client,
            self.scan_service,
            timeout_sec,
            release_ethercat=True,
            operation_type='full_scan',
        )

    def scan_ac_servo(self, timeout_sec: float = AC_SERVO_SCAN_TIMEOUT_SEC) -> Dict[str, Any]:
        return self._call_service(
            self._scan_ac_servo_client,
            self.scan_ac_servo_service,
            timeout_sec,
            release_ethercat=True,
            operation_type='ac_servo_scan',
        )

    def scan_dynamixel(self, timeout_sec: float = DYNAMIXEL_SCAN_TIMEOUT_SEC) -> Dict[str, Any]:
        return self._call_service(
            self._scan_dynamixel_client,
            self.scan_dynamixel_service,
            timeout_sec,
            operation_type='dynamixel_scan',
        )

    def _call_service(
        self,
        client,
        service_name: str,
        timeout_sec: float,
        *,
        release_ethercat: bool = False,
        operation_type: str = 'motor_scan',
    ) -> Dict[str, Any]:
        lifecycle_lock = getattr(self, 'lifecycle_lock', None)
        if lifecycle_lock is None:
            lifecycle_lock = threading.Lock()
            self.lifecycle_lock = lifecycle_lock
        if not lifecycle_lock.acquire(blocking=False):
            return {
                'success': False,
                'message': MOTOR_BUSY_MESSAGE,
                'scan': None,
                'project_id': self.repository.selected_project_id(),
                'project_generation': self.bridge.current_project_generation(),
                **self.bridge.snapshot(),
            }
        scan_lock = getattr(self, '_scan_request_lock', None)
        if scan_lock is None:
            scan_lock = threading.Lock()
            self._scan_request_lock = scan_lock
        if not scan_lock.acquire(blocking=False):
            lifecycle_lock.release()
            return {
                'success': False,
                # 위 `lifecycle_lock` 과 **다른 상황**이다 · 저쪽은 설정 적용·
                # 재시작까지 포함하고, 여기는 검색이 이미 도는 중이다 ·
                # 문구를 합치면 사용자가 무엇을 기다려야 하는지 모른다
                'message': '다른 모터 검색이 진행 중입니다. 완료 후 다시 시도하세요',
                'scan': None,
                'project_id': self.repository.selected_project_id(),
                'project_generation': self.bridge.current_project_generation(),
                **self.bridge.snapshot(),
            }
        operation: Dict[str, Any] = {}
        result: Dict[str, Any]
        try:
            operation = self.repository.runtime.begin_motor_operation(
                operation_type,
                'preparing',
                timeout_sec=timeout_sec + (20.0 if release_ethercat else 5.0),
                details={
                    'service_name': service_name,
                    'project_id': self.repository.selected_project_id(),
                },
            )
            operation_id = str(operation.get('operation_id') or '')
            if release_ethercat:
                result = self._call_ethercat_service_locked(
                    client,
                    service_name,
                    timeout_sec,
                    operation_id=operation_id,
                )
            else:
                self.repository.runtime.update_motor_operation(
                    operation_id,
                    'scanning',
                    message='모터 물리 검색 진행 중',
                )
                result = self._call_service_locked(client, service_name, timeout_sec)
            current = self.repository.runtime.motor_operation_status()
            outcome = motor_config_rules.scan_operation_outcome(
                result.get('scan'),
                operation_type=operation_type,
                fallback_success=result.get('success') is True,
            )
            result['partial'] = outcome == 'partial'
            result['success'] = outcome == 'success'
            if (
                current.get('operation_id') == operation_id
                and current.get('status') == 'running'
            ):
                current = self.repository.runtime.finish_motor_operation(
                    operation_id,
                    outcome,
                    phase={
                        'success': 'completed',
                        'partial': 'partial',
                        'failure': 'failed',
                    }[outcome],
                    message=str(result.get('message') or ''),
                    error=(
                        ''
                        if outcome in {'success', 'partial'}
                        else str(result.get('message') or '')
                    ),
                )
            result.update(self.bridge.snapshot())
            result['motor_operation'] = current
            return result
        except ValueError as exc:
            return {
                'success': False,
                'message': str(exc),
                'scan': None,
                'project_id': self.repository.selected_project_id(),
                'project_generation': self.bridge.current_project_generation(),
                **self.bridge.snapshot(),
            }
        except Exception as exc:
            operation_id = str(operation.get('operation_id') or '')
            if operation_id:
                try:
                    self.repository.runtime.finish_motor_operation(
                        operation_id,
                        'failure',
                        phase='failed',
                        error=str(exc),
                    )
                except ValueError:
                    pass
            raise
        finally:
            scan_lock.release()
            lifecycle_lock.release()

    # ------------------------------------------------------------------ #
    # 장기 작업 Action · §6-26
    # ------------------------------------------------------------------ #

    #: 서비스 이름 → Action 목표의 검색 종류
    TRANSPORT_BY_SERVICE = {
        'scan_motors': 'all',
        'scan_ac_servo_motors': 'ac_servo',
        'scan_dynamixel_motors': 'dynamixel',
    }

    def _scan_action_client(self) -> Any:
        """Action 클라이언트를 처음 쓸 때 만든다.

        `ActionClient`는 진짜 ROS 노드를 요구한다. 노드 없이 세우는 시험 스텁도
        이 클래스를 쓰므로 생성자에서 만들지 않는다.
        """
        if self._action_client is not None:
            return self._action_client
        try:
            self._action_client = ActionClient(
                self.bridge, MotorScan, topics.MOTOR_SCAN_ACTION
            )
        except (AttributeError, TypeError):
            return None
        return self._action_client

    def _run_scan(self, client: Any, service_name: str, timeout_sec: float) -> Any:
        """Action으로 스캔을 돌리고 결과를 돌려준다.

        Action 서버가 없으면 기존 `Trigger` 서비스로 돌아간다 · 구버전 노드가
        떠 있는 동안에도 검색이 멈추지 않아야 한다.
        """
        # 이름표가 붙은 뒤에는 `/joonhoTest/scan_ac_servo_motors` 로 온다 ·
        # `lstrip('/')` 만 하면 표에서 못 찾아 **조용히 전체 검색으로 바뀐다** ·
        # 끝 조각으로 맞춘다 · §6-103
        transport = self.TRANSPORT_BY_SERVICE.get(
            str(service_name).rsplit('/', 1)[-1], 'all'
        )
        action_client = self._scan_action_client()
        if action_client is None or not action_client.wait_for_server(timeout_sec=0.5):
            self.bridge.get_logger().warn(
                'motor_scan action server unavailable · Trigger 서비스로 진행한다'
            )
            return self._run_scan_via_service(client, timeout_sec)

        goal = MotorScan.Goal()
        goal.transport = transport
        send_future = action_client.send_goal_async(
            goal, feedback_callback=self._on_scan_feedback
        )
        handle = self._await(send_future, 5.0)
        if handle is None or not handle.accepted:
            self.bridge.get_logger().warn('motor_scan goal rejected · Trigger로 진행한다')
            return self._run_scan_via_service(client, timeout_sec)

        with self._goal_lock:
            self._active_goal_handle = handle
        try:
            result_wrapper = self._await(handle.get_result_async(), timeout_sec)
        finally:
            with self._goal_lock:
                self._active_goal_handle = None
        if result_wrapper is None:
            return None
        return result_wrapper.result

    def _run_scan_via_service(self, client: Any, timeout_sec: float) -> Any:
        """예전 경로 · 진행 상황은 토픽으로만 오고 취소는 없다."""
        if client is None:
            return None
        future = client.call_async(Trigger.Request())
        return self._await(future, timeout_sec)

    @staticmethod
    def _await(future: Any, timeout_sec: float) -> Any:
        deadline = time.time() + timeout_sec
        while not future.done() and time.time() < deadline:
            time.sleep(0.02)
        return future.result() if future.done() else None

    def _on_scan_feedback(self, message: Any) -> None:
        feedback = getattr(message, 'feedback', None)
        if feedback is None:
            return
        try:
            details = json.loads(feedback.details or '{}')
        except json.JSONDecodeError:
            details = {}
        self.record_progress_event({
            'scan_id': feedback.scan_id,
            'phase': feedback.phase,
            'transport': feedback.transport,
            'message': feedback.message,
            'details': details,
            'timestamp': feedback.timestamp,
        })

    def cancel(self) -> Dict[str, Any]:
        """진행 중인 검색을 취소한다.

        **이미 시작한 물리 검색은 끝까지 간다.** 취소는 다음 장치 종류로 넘어가기
        전에 확인된다 · 모터 스캔 불변조건이 물리 검색을 중간에 끊는 것을 허락하지
        않기 때문이다.
        """
        with self._goal_lock:
            handle = self._active_goal_handle
        if handle is None:
            return {'success': False, 'message': '진행 중인 모터 검색이 없습니다'}
        handle.cancel_goal_async()
        return {
            'success': True,
            'message': '모터 검색 취소를 요청했습니다 · 진행 중인 장치 검색은 끝난 뒤 중단됩니다',
        }

    def _call_service_locked(
        self,
        client,
        service_name: str,
        timeout_sec: float,
    ) -> Dict[str, Any]:
        scan_project_id = self.repository.selected_project_id()
        scan_generation = self.bridge.current_project_generation()
        if not client.wait_for_service(timeout_sec=0.2):
            return {
                'success': False,
                'message': f'scan service unavailable: {service_name}',
                'scan': None,
                'project_generation': scan_generation,
                **self.bridge.snapshot(),
            }

        response = self._run_scan(client, service_name, timeout_sec)
        if response is None:
            return {
                'success': False,
                'message': 'scan service timeout',
                'scan': None,
                'project_generation': scan_generation,
                **self.bridge.snapshot(),
            }
        if (
            self.repository.selected_project_id() != scan_project_id
            or self.bridge.current_project_generation() != scan_generation
        ):
            return {
                'success': False,
                'message': '프로젝트가 변경되어 이전 프로젝트의 검색 결과를 폐기했습니다',
                'scan': None,
                'project_id': self.repository.selected_project_id(),
                'project_generation': self.bridge.current_project_generation(),
                **self.bridge.snapshot(),
            }
        scan = None
        try:
            scan = json.loads(response.message)
        except json.JSONDecodeError:
            self.bridge.get_logger().warn('Invalid scan JSON received.')
        if isinstance(scan, dict):
            ethercat_project_compat.annotate_ethercat_project_compatibility(
                scan, self.load_motor_config
            )
        message = motor_config_rules.scan_result_message(
            bool(response.success),
            scan,
            str(response.message or ''),
        )

        return {
            'success': bool(response.success),
            'message': message,
            'scan': scan,
            'project_id': scan_project_id,
            'project_generation': scan_generation,
            **self.bridge.snapshot(),
        }

    def _call_ethercat_service_locked(
        self,
        client,
        service_name: str,
        timeout_sec: float,
        *,
        operation_id: str = '',
    ) -> Dict[str, Any]:
        """Release the persistent EtherCAT owner for one physical scan.

        The scan contract requires ``ethercat rescan``.  The persistent Motor
        Manager must therefore be stopped first and restored afterwards.  This
        orchestration belongs to the upper web layer; motion_system remains
        unchanged.
        """
        motor_service = str(
            os.environ.get('MOTION_MOTOR_SERVICE_UNIT') or ''
        ).strip()
        if motor_service != 'motion-motor.service':
            return self._call_service_locked(client, service_name, timeout_sec)

        blocker = self.runtime.ethercat_scan_safety_blocker(
            require_fresh_motor_state=False,
        )
        if blocker:
            return {
                'success': False,
                'message': f'AC Servo 검색 미실행: {blocker}',
                'scan': None,
                'scan_blocked': True,
                'project_id': self.repository.selected_project_id(),
                'project_generation': self.bridge.current_project_generation(),
                **self.bridge.snapshot(),
            }

        was_active = self.runtime.managed_service_active(motor_service)
        runtime_handoff = self._runtime_handoff()
        restore_runtime = bool(was_active and not runtime_handoff['required'])
        if restore_runtime:
            blocker = self.runtime.ethercat_scan_safety_blocker(
                require_fresh_motor_state=True,
            )
            if blocker:
                return {
                    'success': False,
                    'message': f'AC Servo 검색 미실행: {blocker}',
                    'scan': None,
                    'scan_blocked': True,
                    'project_id': self.repository.selected_project_id(),
                    'project_generation': self.bridge.current_project_generation(),
                    **self.bridge.snapshot(),
                }

        expected_ethercat_axes = (
            self._expected_runtime_ethercat_axes() if restore_runtime else []
        )
        expected_recovery_axes = (
            self._expected_runtime_axes() if restore_runtime else []
        )
        if operation_id:
            self.repository.runtime.update_motor_operation(
                operation_id,
                'preparing',
                details={
                    'motor_service_was_active': was_active,
                    'expected_axes': expected_recovery_axes,
                    'expected_ethercat_axes': expected_ethercat_axes,
                    'runtime_handoff': runtime_handoff,
                },
            )
        if restore_runtime and not expected_ethercat_axes:
            return {
                'success': False,
                'message': (
                    'AC Servo 검색 미실행: 실행 설정에서 복구 대상 EtherCAT 축을 '
                    '확인할 수 없습니다'
                ),
                'scan': None,
                'scan_blocked': True,
                'project_id': self.repository.selected_project_id(),
                'project_generation': self.bridge.current_project_generation(),
                **self.bridge.snapshot(),
            }
        if restore_runtime and not expected_recovery_axes:
            return {
                'success': False,
                'message': (
                    'AC Servo 검색 미실행: 실행 설정에서 복구 대상 전체 모터축을 '
                    '확인할 수 없습니다'
                ),
                'scan': None,
                'scan_blocked': True,
                'project_id': self.repository.selected_project_id(),
                'project_generation': self.bridge.current_project_generation(),
                **self.bridge.snapshot(),
            }
        result: Dict[str, Any]
        restore_error = ''
        recovery: Dict[str, Any] = {
            'required': restore_runtime,
            'expected_axes': expected_recovery_axes if restore_runtime else [],
            'online_axes': [],
            'recovered': not restore_runtime,
        }
        try:
            if was_active:
                if operation_id:
                    stop_message = (
                        '이전 프로젝트 Motor Manager 정지 및 EtherCAT 소유권 해제 중'
                        if runtime_handoff['required']
                        else 'Motor Manager 정지 및 EtherCAT 소유권 해제 중'
                    )
                    self.repository.runtime.update_motor_operation(
                        operation_id,
                        'stopping_runtime',
                        message=stop_message,
                    )
                self.runtime.run_managed_service('stop', motor_service)
                motor_config_rules.wait_for_ethercat_release(timeout_sec=5.0)
            if operation_id:
                self.repository.runtime.update_motor_operation(
                    operation_id,
                    'scanning',
                    message='AC Servo 물리 검색 진행 중',
                )
            result = self._call_service_locked(client, service_name, timeout_sec)
        except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
            result = {
                'success': False,
                'message': f'AC Servo 검색 미실행: {exc}',
                'scan': None,
                'scan_blocked': True,
                'project_id': self.repository.selected_project_id(),
                'project_generation': self.bridge.current_project_generation(),
                **self.bridge.snapshot(),
            }
        finally:
            if restore_runtime:
                try:
                    if operation_id:
                        try:
                            self.repository.runtime.update_motor_operation(
                                operation_id,
                                'restoring',
                                message='검색 전 Motor Manager 실행 상태 복구 중',
                            )
                        except ValueError:
                            # Runtime restoration is a safety action and must
                            # not depend on operation bookkeeping still being
                            # writable/running.
                            pass
                    self.runtime.run_managed_service('start', motor_service)
                    recovery = self.runtime.wait_for_runtime_recovery(
                        expected_recovery_axes,
                        timeout_sec=12.0,
                        motor_service=motor_service,
                    )
                    # **둘을 가른다** · §6-196
                    #
                    # Motor Manager 가 안 돌아온 것   →  진짜 실패
                    # 축 몇 개가 안 돌아온 것         →  알림 (실패 아님)
                    #
                    # 사람이 직접 「검색」을 눌렀다는 것은, 모터가 빠졌거나
                    # 알람인 것을 이미 보고 누른 것이다 · 그런데 전에는 검색
                    # 전 설정에 있던 축이 **전부** 돌아와야 성공으로 쳤다 ·
                    # 빠진 모터 때문에 재검색하면 그 모터가 안 돌아와서
                    # 「직접 검색 실패」가 떴다 · 축 목록은 제대로 갱신됐는데도.
                    if not recovery.get('recovered'):
                        if not recovery.get('service_active'):
                            restore_error = 'Motor Manager 가 다시 실행되지 않았습니다'
                        else:
                            missing = recovery.get('missing_axes') or []
                            result['missing_axes'] = missing
                except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
                    restore_error = str(exc)

        result['motor_service_was_active'] = was_active
        result['motor_service_restore_required'] = restore_runtime
        result['motor_service_restored'] = bool(
            restore_runtime and not restore_error
        )
        result['motor_runtime_recovery'] = recovery
        result['runtime_handoff'] = runtime_handoff
        if runtime_handoff['required'] and result.get('success') is True:
            result['message'] = (
                f'{result.get("message") or "AC Servo 검색 완료"} / '
                '이전 프로젝트 모터 실행은 정지되었습니다. '
                '현재 프로젝트 설정을 저장한 뒤 장비에 적용 · 모터 재시작하세요'
            )
        missing_axes = result.get('missing_axes') or []
        if missing_axes and not restore_error:
            # 검색 결과는 그대로 살린다 · 무엇이 없었는지만 덧붙인다
            result['message'] = (
                f'{result.get("message") or "AC Servo 검색 완료"} / '
                f'검색 전 설정의 {", ".join(str(a) for a in missing_axes)}번 축이 '
                '돌아오지 않았습니다 · 빠졌거나 알람 상태인지 확인하세요'
            )
        if restore_error:
            result['success'] = False
            result['restore_error'] = restore_error
            result['message'] = (
                f'{result.get("message") or "AC Servo 검색 종료"} / '
                f'Motor Manager 복구 실패: {restore_error}'
            )
        return result

    def _expected_runtime_axes(self) -> List[int]:
        repository = getattr(self, 'repository', None)
        runtime = (
            repository.runtime.applied_runtime_motor_config()
            if repository is not None
            and hasattr(getattr(repository, 'runtime', None), 'applied_runtime_motor_config')
            else None
        )
        if runtime is not None:
            return motion_file_analysis.configured_axes_from_runtime_file(runtime)

        motion_state = self.bridge.fresh_motion_state()
        if motion_state is None:
            return []
        axes = []
        for motor in motion_state.get('motors') or []:
            if not isinstance(motor, dict):
                continue
            if motor.get('connection_connected') is not True:
                continue
            try:
                axes.append(int(motor.get('controller_index')))
            except (TypeError, ValueError):
                continue
        return sorted(set(axes))

    def _expected_runtime_ethercat_axes(self) -> List[int]:
        repository = getattr(self, 'repository', None)
        runtime = (
            repository.runtime.applied_runtime_motor_config()
            if repository is not None
            and hasattr(getattr(repository, 'runtime', None), 'applied_runtime_motor_config')
            else None
        )
        if runtime is not None:
            return motion_file_analysis.configured_axes_from_runtime_file(
                runtime,
                transport='ethercat',
            )

        # Compatibility fallback for an unmanaged/legacy launch with no
        # durable runtime target.
        motion_state = self.bridge.fresh_motion_state()
        if motion_state is None:
            return []
        axes = []
        for motor in motion_state.get('motors') or []:
            if not isinstance(motor, dict):
                continue
            if str(motor.get('transport') or '').lower() != 'ethercat':
                continue
            if motor.get('connection_connected') is not True:
                continue
            try:
                axes.append(int(motor.get('controller_index')))
            except (TypeError, ValueError):
                continue
        return sorted(set(axes))

    def _runtime_handoff(self) -> Dict[str, Any]:
        """Describe whether an active Motor Manager belongs to another project.

        A project switch intentionally does not change the active runtime.
        Therefore a physical scan for the newly selected project must be able
        to retire the previous project's runtime without depending on feedback
        from that runtime.  The scan safety blocker still rejects every active
        upper-level motion operation and any observed moving EtherCAT axis.
        """
        selected_project_id = str(
            self.repository.selected_project_id() or ''
        ).strip()
        runtime_project_id = ''
        try:
            runtime_state = self.repository.runtime.motor_runtime_state()
        except (AttributeError, OSError, ValueError, json.JSONDecodeError):
            runtime_state = {}
        if isinstance(runtime_state, dict):
            runtime_project_id = str(
                runtime_state.get('target_project_id') or ''
            ).strip()
        if not runtime_project_id:
            runtime_project_id = str(
                self.project.runtime_project_id_from_path() or ''
            ).strip()
        return {
            'required': bool(
                selected_project_id
                and runtime_project_id
                and runtime_project_id != selected_project_id
            ),
            'selected_project_id': selected_project_id,
            'runtime_project_id': runtime_project_id,
        }
