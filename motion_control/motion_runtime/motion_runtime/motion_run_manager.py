"""Validate and execute motion plans independently from the web API process."""

import ast
import hashlib
import json
import math
import os
import threading
import time
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional

import rclpy
import yaml
from motion_control_msgs.msg import MotorStatus
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
from std_msgs.msg import Int8MultiArray, String

from .motion_automation_store import (
    MotionAutomationStore,
    REPEAT_MODES,
    default_automation_state,
    normalize_automation_state,
)


ID_CONTROLWORD = 0
ID_TARGET_POSITION = 1
CW_ENABLE_OPERATION_MINAS = 0x000F
CW_NEW_SET_POINT_MINAS = 0x003F
DYNAMIXEL_TORQUE_ENABLE = 1
DEFAULT_MOTION_PROJECTS_DIR = (
    Path(os.environ.get('MOTION_WORKSPACE', Path.cwd())).expanduser()
    / 'motion_projects'
)
DEFAULT_PERIOD_SEC = 0.02
STATE_TIMEOUT_SEC = 1.0
SAFETY_STATUS_TIMEOUT_SEC = 2.0
AC_TARGET_TOLERANCE_DEG = 0.1
DYNAMIXEL_TARGET_TOLERANCE_DEG = 1.0
TARGET_SETTLE_TIMEOUT_SEC = 3.0
CONTINUOUS_LOOP_TOLERANCE_DEG = 5.0
INITIAL_MOVE_TIME_OPTIONS_SEC = (5.0, 7.0, 10.0)


class MotionRunManager(Node):
    """Runs a saved motion file through a saved motion-axis mapping.

    This node owns the motion-file lifecycle but never publishes to the final
    hardware command topic. Combined setpoints are submitted to the supervisor
    through /motion_control/motion_run_command.
    """

    def __init__(self) -> None:
        super().__init__('motion_run_manager')

        self.motion_state_topic = str(
            self.declare_parameter('motion_state_topic', '/motion_control/motion_state').value
        )
        self.motor_command_topic = str(
            self.declare_parameter(
                'motor_command_topic',
                '/motion_control/motion_run_command',
            ).value
        )
        self.request_topic = str(
            self.declare_parameter(
                'request_topic',
                '/motion_control/motion_run_request',
            ).value
        )
        self.response_topic = str(
            self.declare_parameter(
                'response_topic',
                '/motion_control/motion_run_response',
            ).value
        )
        self.status_topic = str(
            self.declare_parameter(
                'status_topic',
                '/motion_control/motion_run_status',
            ).value
        )
        self.motion_value_topic = str(
            self.declare_parameter(
                'motion_value_topic',
                '/motion_control/motion_value_state',
            ).value
        )
        self.safety_status_topic = str(
            self.declare_parameter(
                'safety_status_topic',
                '/motion_control/safety_status',
            ).value
        )
        self.action_request_topic = str(
            self.declare_parameter(
                'action_request_topic',
                '/motion_control/manual_action_request',
            ).value
        )
        self.action_result_topic = str(
            self.declare_parameter(
                'action_result_topic',
                '/motion_control/manual_action_result',
            ).value
        )
        self.motion_projects_dir = Path(
            str(self.declare_parameter(
                'motion_projects_dir', str(DEFAULT_MOTION_PROJECTS_DIR)
            ).value)
        ).expanduser().resolve()
        self.period_sec = self._load_period_sec()
        self.motion_files_dir = self.motion_projects_dir
        self.mappings_dir = self.motion_projects_dir

        qos = QoSProfile(depth=1, reliability=ReliabilityPolicy.BEST_EFFORT)
        self._state_lock = threading.Lock()
        self._latest_state: Optional[Dict[str, Any]] = None
        self._latest_state_at: Optional[float] = None
        self._safety_status_lock = threading.Lock()
        self._latest_safety_status: Optional[Dict[str, Any]] = None
        self._latest_safety_status_at: Optional[float] = None
        self._run_lock = threading.RLock()
        self._run_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._graceful_stop_event = threading.Event()
        self._automation_store = MotionAutomationStore(self.motion_projects_dir)
        self._automation_state = default_automation_state()
        self._automation_runtime: Dict[str, Any] = {
            'state': 'off',
            'message': '',
            'resume_pending': False,
            'stop_after_cycle': False,
        }
        self._automation_project_id = ''
        self._automation_resume_pending = False
        self._automation_resume_started_at: Optional[float] = None
        self._automation_last_attempt_at = 0.0
        self.automation_startup_timeout_sec = max(
            float(
                self.declare_parameter(
                    'automation_startup_timeout_sec',
                    120.0,
                ).value
            ),
            1.0,
        )
        self.ac_target_tolerance_deg = max(
            float(self.declare_parameter('ac_target_tolerance_deg', AC_TARGET_TOLERANCE_DEG).value),
            0.0,
        )
        self.dynamixel_target_tolerance_deg = max(
            float(
                self.declare_parameter(
                    'dynamixel_target_tolerance_deg',
                    DYNAMIXEL_TARGET_TOLERANCE_DEG,
                ).value
            ),
            0.0,
        )
        self.target_settle_timeout_sec = max(
            float(self.declare_parameter('target_settle_timeout_sec', TARGET_SETTLE_TIMEOUT_SEC).value),
            0.0,
        )
        self._status: Dict[str, Any] = self._empty_status()
        self._execution_context: Dict[str, Any] = {}
        self._execution_context_ready = False
        self._project_generation = 0
        self._action_result_lock = threading.Lock()
        self._action_results: Dict[str, List[Dict[str, Any]]] = {}

        self._state_sub = self.create_subscription(
            String,
            self.motion_state_topic,
            self._motion_state_callback,
            10,
        )
        safety_qos = QoSProfile(
            depth=1,
            reliability=ReliabilityPolicy.RELIABLE,
            durability=DurabilityPolicy.TRANSIENT_LOCAL,
        )
        self._safety_status_sub = self.create_subscription(
            String,
            self.safety_status_topic,
            self._safety_status_callback,
            safety_qos,
        )
        self._request_sub = self.create_subscription(
            String,
            self.request_topic,
            self._request_callback,
            10,
        )
        self._action_result_sub = self.create_subscription(
            String,
            self.action_result_topic,
            self._action_result_callback,
            10,
        )
        self._response_pub = self.create_publisher(String, self.response_topic, 10)
        self._status_pub = self.create_publisher(String, self.status_topic, 10)
        self._command_pub = self.create_publisher(MotorStatus, self.motor_command_topic, qos)
        motion_value_qos = QoSProfile(
            depth=10,
            reliability=ReliabilityPolicy.RELIABLE,
            durability=DurabilityPolicy.TRANSIENT_LOCAL,
        )
        self._motion_value_pub = self.create_publisher(
            String, self.motion_value_topic, motion_value_qos
        )
        self._action_request_pub = self.create_publisher(String, self.action_request_topic, 10)
        self._status_timer = self.create_timer(0.5, self._publish_status)
        self._automation_timer = self.create_timer(1.0, self._automation_tick)

        self.get_logger().info(
            f'motion_run_manager started: state={self.motion_state_topic}, '
            f'command={self.motor_command_topic}, request={self.request_topic}, '
            f'action_request={self.action_request_topic}, '
            f'safety_status={self.safety_status_topic}, '
            f'period={self.period_sec * 1000.0:.3f} ms, '
            f'motion_projects_dir={self.motion_projects_dir}'
        )

    def _motion_state_callback(self, msg: String) -> None:
        try:
            payload = json.loads(msg.data)
        except json.JSONDecodeError:
            self.get_logger().warn('Invalid motion_state JSON received.')
            return
        with self._state_lock:
            self._latest_state = payload if isinstance(payload, dict) else None
            self._latest_state_at = time.time()

    def _safety_status_callback(self, msg: String) -> None:
        try:
            payload = json.loads(msg.data)
        except json.JSONDecodeError:
            self.get_logger().warn('Invalid safety_status JSON received.')
            return
        if not isinstance(payload, dict):
            return
        with self._safety_status_lock:
            self._latest_safety_status = payload
            self._latest_safety_status_at = time.monotonic()

    def _action_result_callback(self, msg: String) -> None:
        try:
            payload = json.loads(msg.data)
        except json.JSONDecodeError:
            self.get_logger().warn('Invalid action result JSON received.')
            return
        if not isinstance(payload, dict):
            return
        request_id = str(payload.get('request_id') or '')
        if not request_id:
            return
        generation = int(self._execution_context.get('project_generation') or 0)
        try:
            if int(payload.get('project_generation')) != generation:
                return
        except (TypeError, ValueError):
            return
        now = time.time()
        with self._action_result_lock:
            self._action_results.setdefault(request_id, []).append(payload)
            for key, values in list(self._action_results.items()):
                last_stamp = now
                if values:
                    last_stamp = self._finite_float(values[-1].get('stamp')) or now
                if now - last_stamp > 60.0:
                    self._action_results.pop(key, None)

    def _request_callback(self, msg: String) -> None:
        try:
            request = json.loads(msg.data)
        except json.JSONDecodeError as exc:
            self.get_logger().warn(f'invalid motion run request JSON: {exc}')
            return
        if not isinstance(request, dict):
            return

        request_id = str(request.get('request_id') or '')
        project_generation = request.get('project_generation')
        command = str(request.get('command') or '').strip()
        payload = request.get('payload')
        if not isinstance(payload, dict):
            payload = {}

        try:
            request_generation = self._validate_request_generation(
                command, project_generation, payload
            )
            if command == 'apply_context':
                response = self._apply_execution_context(payload)
            elif command == 'confirm_context':
                response = self._confirm_execution_context(payload)
            elif command == 'invalidate_context':
                with self._run_lock:
                    if self._run_thread is not None and self._run_thread.is_alive():
                        raise ValueError(
                            '모션 동작 중에는 프로젝트 메모리를 폐기할 수 없습니다'
                        )
                    self._execution_context = {}
                    self._execution_context_ready = False
                    self.motion_files_dir = self.motion_projects_dir
                    self.mappings_dir = self.motion_projects_dir
                    self._status = self._empty_status()
                    self._automation_project_id = ''
                    self._automation_state = default_automation_state()
                    self._automation_runtime = {
                        'state': 'off',
                        'message': '',
                        'resume_pending': False,
                        'stop_after_cycle': False,
                    }
                    self._automation_resume_pending = False
                    self._automation_resume_started_at = None
                response = {
                    'success': True,
                    'message': '모션 실행 프로젝트 메모리 폐기',
                    'project_id': '',
                    'context_id': '',
                    'status': self.status(),
                }
            elif command == 'status':
                response = {'success': True, 'message': 'motion run status', 'status': self.status()}
            elif command == 'automation_configure':
                self._require_execution_context(payload)
                response = self._configure_automation(payload)
            elif command == 'automation_start':
                self._require_execution_context(payload)
                response = self._start_automation(payload)
            elif command == 'automation_disable':
                response = self._disable_automation(payload)
            elif command == 'check':
                self._require_execution_context(payload)
                response = self._handle_check(payload)
            elif command == 'initialize':
                self._require_execution_context(payload)
                response = self._start_thread('initialize', payload)
            elif command == 'start':
                self._require_execution_context(payload)
                response = self._start_thread('run', payload)
            elif command == 'stop':
                response = self._handle_stop()
            else:
                response = {'success': False, 'message': f'unknown motion run command: {command}'}
        except Exception as exc:  # Defensive boundary for the web bridge.
            self.get_logger().error(
                f'motion run command failed: {command}\n{traceback.format_exc()}'
            )
            response = {'success': False, 'message': f'motion run command failed: {exc}'}

        response['request_id'] = request_id
        response['project_generation'] = project_generation
        self._publish_response(response)
        self._publish_status()

    def _validate_request_generation(
        self, command: str, request_generation: Any, payload: Dict[str, Any]
    ) -> int:
        try:
            generation = int(request_generation)
            payload_generation = int(payload.get('project_generation'))
        except (TypeError, ValueError) as exc:
            raise ValueError('프로젝트 세대 번호가 필요합니다') from exc
        if generation < 1 or payload_generation != generation:
            raise ValueError('요청의 프로젝트 세대 번호가 일치하지 않습니다')
        current = int(getattr(self, '_project_generation', 0) or 0)
        if command in {'apply_context', 'invalidate_context'}:
            if generation < current:
                raise ValueError('이전 프로젝트 세대의 요청을 폐기했습니다')
            self._project_generation = generation
            return generation
        if generation != current:
            raise ValueError('현재 프로젝트 세대와 다른 요청을 폐기했습니다')
        return generation

    def _apply_execution_context(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        project_id, _, mappings_dir = self._project_asset_dirs(payload)
        context_id = str(payload.get('context_id') or '').strip()
        mapping_file_id = str(payload.get('mapping_file_id') or '').strip()
        mapping_sha256 = str(payload.get('mapping_sha256') or '').strip()
        if not context_id or not mapping_file_id or not mapping_sha256:
            raise ValueError('실행 컨텍스트 ID와 모션축 설정 버전이 필요합니다')
        mapping_path = self._mapping_file_path(mapping_file_id, mappings_dir)
        actual_sha = hashlib.sha256(mapping_path.read_bytes()).hexdigest()
        if actual_sha != mapping_sha256:
            raise ValueError('모션축 설정 파일 버전이 실행 컨텍스트와 다릅니다')
        with self._run_lock:
            if self._run_thread is not None and self._run_thread.is_alive():
                raise ValueError('모션 동작 중에는 실행 컨텍스트를 변경할 수 없습니다')
            next_context = {
                'context_id': context_id,
                'project_id': project_id,
                'project_generation': int(payload.get('project_generation') or 0),
                'mapping_file_id': mapping_path.name,
                'mapping_sha256': actual_sha,
            }
            same_context = self._execution_context == next_context
            self._execution_context = next_context
            if not same_context:
                self._execution_context_ready = False
        if not same_context:
            self._load_automation_project(project_id)
        return {
            'success': True,
            'message': '모션 실행 컨텍스트 적용 확인 완료',
            **self._execution_context,
        }

    def _confirm_execution_context(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        context_id = str(payload.get('context_id') or '').strip()
        with self._run_lock:
            if not context_id or context_id != self._execution_context.get('context_id'):
                raise ValueError('확인하려는 실행 컨텍스트가 적용된 설정과 다릅니다')
            self._execution_context_ready = True
            automation = dict(
                getattr(self, '_automation_state', default_automation_state())
            )
            if automation.get('enabled') and automation.get('armed'):
                self._automation_resume_pending = True
                self._automation_resume_started_at = time.monotonic()
                self._automation_runtime = {
                    **self._automation_runtime,
                    'state': 'waiting',
                    'message': '프로그램 시작 후 자동 반복 준비 중',
                    'resume_pending': True,
                    'stop_after_cycle': False,
                }
        return {
            'success': True,
            'message': '모션 실행 허용',
            **self._execution_context,
        }

    def _require_execution_context(self, payload: Dict[str, Any]) -> None:
        context_id = str(payload.get('context_id') or '').strip()
        project_id = str(payload.get('project_id') or '').strip()
        with self._run_lock:
            applied = dict(self._execution_context)
            ready = self._execution_context_ready
        if (
            not ready
            or not context_id
            or context_id != applied.get('context_id')
            or project_id != applied.get('project_id')
            or int(payload.get('project_generation') or 0)
            != int(applied.get('project_generation') or 0)
        ):
            raise ValueError('현재 프로젝트 실행 컨텍스트 적용 대기 중입니다')
        _, _, mappings_dir = self._project_asset_dirs(payload)
        mapping_path = self._mapping_file_path(applied.get('mapping_file_id'), mappings_dir)
        actual_sha = hashlib.sha256(mapping_path.read_bytes()).hexdigest()
        if actual_sha != applied.get('mapping_sha256'):
            with self._run_lock:
                self._execution_context_ready = False
            raise ValueError('모션축 설정 파일이 변경되어 실행 컨텍스트 재적용이 필요합니다')

    def _load_automation_project(self, project_id: str) -> None:
        try:
            state = self._automation_store.load(project_id)
            error = ''
        except ValueError as exc:
            state = default_automation_state()
            error = str(exc)
        with self._run_lock:
            self._automation_project_id = project_id
            self._automation_state = state
            self._automation_resume_pending = False
            self._automation_resume_started_at = None
            self._automation_runtime = {
                'state': (
                    'blocked'
                    if error
                    else ('ready' if state.get('enabled') else 'off')
                ),
                'message': error,
                'resume_pending': False,
                'stop_after_cycle': False,
            }
        self._graceful_stop_event.clear()

    def _save_automation(
        self,
        values: Dict[str, Any],
        *,
        runtime_state: Optional[str] = None,
        runtime_message: Optional[str] = None,
    ) -> Dict[str, Any]:
        with self._run_lock:
            project_id = self._automation_project_id
            candidate = {**self._automation_state, **values}
        if not project_id:
            raise ValueError('자동 반복을 저장할 현재 프로젝트가 없습니다')
        saved = self._automation_store.save(project_id, candidate)
        with self._run_lock:
            self._automation_state = saved
            if runtime_state is not None:
                self._automation_runtime['state'] = runtime_state
            if runtime_message is not None:
                self._automation_runtime['message'] = runtime_message
        return dict(saved)

    def _configure_automation(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        with self._run_lock:
            current = dict(self._automation_state)
        candidate = normalize_automation_state({
            **current,
            'enabled': bool(payload.get('enabled', current.get('enabled', False))),
            'repeat_mode': payload.get(
                'repeat_mode',
                current.get('repeat_mode', 'direct'),
            ),
            'dwell_sec': payload.get('dwell_sec', current.get('dwell_sec', 0.0)),
        })
        if not candidate['enabled']:
            return self._disable_automation(payload)
        saved = self._save_automation(
            {
                **candidate,
                'enabled': True,
                'last_error': '',
            },
            runtime_state='running' if current.get('armed') else 'ready',
            runtime_message=(
                '현재 실행에는 기존 설정을 유지하고 다음 시작부터 적용합니다'
                if current.get('armed')
                else '자동 반복 사용'
            ),
        )
        return {
            'success': True,
            'message': '자동 반복 설정 저장 완료',
            'automation': self._automation_snapshot(),
            'settings': saved,
            'status': self.status(),
        }

    def _start_automation(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        with self._run_lock:
            configured = dict(self._automation_state)
        if not configured.get('enabled'):
            return {
                'success': False,
                'message': '자동 반복 사용을 먼저 켜세요',
                'automation': self._automation_snapshot(),
                'status': self.status(),
            }
        motion_file_id = str(payload.get('motion_file_id') or '').strip()
        mapping_file_id = str(payload.get('mapping_file_id') or '').strip()
        if not motion_file_id or not mapping_file_id:
            return {
                'success': False,
                'message': '재생 등록된 모션 파일과 모션축 설정이 필요합니다',
                'automation': self._automation_snapshot(),
                'status': self.status(),
            }
        project_id, motions_dir, mappings_dir = self._project_asset_dirs(payload)
        motion_path = self._motion_file_path(motion_file_id, motions_dir)
        mapping_path = self._mapping_file_path(mapping_file_id, mappings_dir)
        saved = self._save_automation(
            {
                'enabled': True,
                'armed': True,
                'repeat_mode': configured.get('repeat_mode', 'direct'),
                'dwell_sec': configured.get('dwell_sec', 0.0),
                'motion_file_id': motion_path.name,
                'mapping_file_id': mapping_path.name,
                'motion_sha256': hashlib.sha256(motion_path.read_bytes()).hexdigest(),
                'mapping_sha256': hashlib.sha256(mapping_path.read_bytes()).hexdigest(),
                'last_error': '',
            },
            runtime_state='checking',
            runtime_message='자동 반복 시작 검사 중',
        )
        request_payload = {
            **payload,
            'project_id': project_id,
            'motion_file_id': saved['motion_file_id'],
            'mapping_file_id': saved['mapping_file_id'],
            'run_mode': 'continuous',
            'automation_run': True,
            'repeat_mode': saved['repeat_mode'],
            'dwell_sec': saved['dwell_sec'],
        }
        try:
            result = self._start_thread('run', request_payload)
        except Exception as exc:
            self._automation_failure(str(exc))
            return {
                'success': False,
                'message': str(exc),
                'automation': self._automation_snapshot(),
                'status': self.status(),
            }
        if not result.get('success'):
            self._automation_failure(str(result.get('message') or '자동 반복 시작 실패'))
            result['automation'] = self._automation_snapshot()
            result['status'] = self.status()
            return result
        with self._run_lock:
            self._automation_resume_pending = False
            self._automation_runtime = {
                **self._automation_runtime,
                'state': 'starting',
                'message': '초기위치 이동 후 자동 반복을 시작합니다',
                'resume_pending': False,
                'stop_after_cycle': False,
            }
        result['automation'] = self._automation_snapshot()
        return result

    def _disable_automation(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        project_id = str(payload.get('project_id') or '').strip()
        with self._run_lock:
            automation_project_id = self._automation_project_id
        if not project_id or project_id != automation_project_id:
            raise ValueError('현재 프로젝트의 자동 반복 설정만 해제할 수 있습니다')
        current_status = self.status()
        active = (
            bool(current_status.get('automation_run'))
            and current_status.get('state')
            in {'initializing', 'initialized', 'running', 'waiting', 'verifying'}
        )
        self._save_automation(
            {
                'enabled': False,
                'armed': False,
                'last_error': '',
            },
            runtime_state='stop_requested' if active else 'off',
            runtime_message=(
                '현재 단계 완료 후 자동 반복을 정지합니다'
                if active
                else '자동 반복 사용 안 함'
            ),
        )
        with self._run_lock:
            self._automation_resume_pending = False
            self._automation_resume_started_at = None
            self._automation_runtime['resume_pending'] = False
            self._automation_runtime['stop_after_cycle'] = active
        if active:
            self._graceful_stop_event.set()
        else:
            self._graceful_stop_event.clear()
        return {
            'success': True,
            'message': (
                '현재 단계 완료 후 자동 반복 정지'
                if active
                else '자동 반복 사용 안 함'
            ),
            'automation': self._automation_snapshot(),
            'status': self.status(),
        }

    def _automation_failure(self, message: str) -> None:
        text = str(message or '자동 반복 실행 실패')
        try:
            self._save_automation(
                {
                    'armed': False,
                    'last_error': text,
                },
                runtime_state='blocked',
                runtime_message=text,
            )
        except ValueError:
            with self._run_lock:
                self._automation_runtime.update({
                    'state': 'blocked',
                    'message': text,
                })
        with self._run_lock:
            self._automation_resume_pending = False
            self._automation_runtime['resume_pending'] = False

    def _automation_snapshot(self) -> Dict[str, Any]:
        with self._run_lock:
            return {
                **self._automation_state,
                **self._automation_runtime,
                'project_id': self._automation_project_id,
            }

    def _automation_tick(self) -> None:
        with self._run_lock:
            pending = self._automation_resume_pending
            ready = self._execution_context_ready
            context = dict(self._execution_context)
            state = dict(self._automation_state)
            thread_running = self._run_thread is not None and self._run_thread.is_alive()
            started_at = self._automation_resume_started_at
            last_attempt = self._automation_last_attempt_at
        if not pending or not ready or thread_running:
            return
        now = time.monotonic()
        if now - last_attempt < 1.0:
            return
        with self._run_lock:
            self._automation_last_attempt_at = now
        if started_at is not None and now - started_at > self.automation_startup_timeout_sec:
            self._automation_failure('자동 반복 시작 대기 시간 초과')
            return
        try:
            self._verify_automation_files(context, state)
            payload = {
                'project_id': context.get('project_id'),
                'project_generation': context.get('project_generation'),
                'context_id': context.get('context_id'),
                'motion_file_id': state.get('motion_file_id'),
                'mapping_file_id': state.get('mapping_file_id'),
                'run_mode': 'continuous',
                'automation_run': True,
                'repeat_mode': state.get('repeat_mode', 'direct'),
                'dwell_sec': state.get('dwell_sec', 0.0),
            }
            result = self._start_thread('run', payload)
        except Exception as exc:
            with self._run_lock:
                still_pending = self._automation_resume_pending
            if not still_pending:
                return
            with self._run_lock:
                self._automation_runtime.update({
                    'state': 'waiting',
                    'message': f'자동 반복 시작 대기: {exc}',
                    'resume_pending': True,
                })
            return
        if result.get('success'):
            with self._run_lock:
                self._automation_resume_pending = False
                self._automation_runtime.update({
                    'state': 'starting',
                    'message': '재시작 후 자동 반복 시작',
                    'resume_pending': False,
                })
        else:
            with self._run_lock:
                self._automation_runtime.update({
                    'state': 'waiting',
                    'message': f"자동 반복 시작 대기: {result.get('message') or '준비되지 않음'}",
                    'resume_pending': True,
                })

    def _verify_automation_files(
        self,
        context: Dict[str, Any],
        state: Dict[str, Any],
    ) -> None:
        project_id, motions_dir, mappings_dir = self._project_asset_dirs(context)
        if project_id != self._automation_project_id:
            raise ValueError('자동 반복 프로젝트가 현재 프로젝트와 다릅니다')
        motion_path = self._motion_file_path(state.get('motion_file_id'), motions_dir)
        mapping_path = self._mapping_file_path(state.get('mapping_file_id'), mappings_dir)
        motion_sha = hashlib.sha256(motion_path.read_bytes()).hexdigest()
        mapping_sha = hashlib.sha256(mapping_path.read_bytes()).hexdigest()
        if motion_sha != state.get('motion_sha256'):
            self._automation_failure('자동 반복 모션 파일이 시작 당시와 다릅니다')
            raise ValueError('자동 반복 모션 파일이 시작 당시와 다릅니다')
        if mapping_sha != state.get('mapping_sha256'):
            self._automation_failure('자동 반복 모션축 설정이 시작 당시와 다릅니다')
            raise ValueError('자동 반복 모션축 설정이 시작 당시와 다릅니다')

    def _handle_check(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        try:
            plan = self._build_plan(payload)
        except Exception as exc:
            reason = str(exc) or '실행 준비 검사 실패'
            status = self._empty_status()
            status.update({
                'state': 'error',
                'phase': 'error',
                'message': f'실행 준비 검사 실패: {reason}',
                'project_id': str(payload.get('project_id') or ''),
                'motion_file_id': str(payload.get('motion_file_id') or ''),
                'mapping_file_id': str(payload.get('mapping_file_id') or ''),
                'capabilities': self._unavailable_capabilities(reason),
                'updated_at': time.time(),
            })
            self._set_status(status)
            return {
                'success': False,
                'message': reason,
                'status': self.status(),
                'summary': {},
            }
        status = self._status_from_plan('ready', '실행 준비 검사 완료', plan)
        status['phase'] = 'ready'
        status['lifecycle'] = {
            **status.get('lifecycle', {}),
            'checked_at': time.time(),
            'initial_started_at': None,
            'initial_finished_at': None,
            'motion_started_at': None,
            'motion_finished_at': None,
        }
        self._set_status(status)
        return {
            'success': True,
            'message': 'motion run check complete',
            'status': self.status(),
            'summary': plan['summary'],
        }

    def _start_thread(self, mode: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        with self._run_lock:
            if self._run_thread is not None and self._run_thread.is_alive():
                return {
                    'success': False,
                    'message': 'previous motion run task is still running',
                    'status': self.status(),
                }
            ownership_error = self._playback_ownership_error()
            if ownership_error:
                return {
                    'success': False,
                    'message': ownership_error,
                    'status': self.status(),
                }
            if mode == 'initialize':
                plan = self._build_plan(payload, initialization_only=True)
                target = self._run_initialization
                target_args = (plan,)
            else:
                plan = self._build_plan(payload)
                initialization_plan = self._build_plan(
                    payload,
                    initialization_only=True,
                )
                guard_error = self._motion_auto_start_guard_error(plan)
                if guard_error:
                    current = self.status()
                    current_state = str(current.get('state') or 'ready')
                    status = self._status_from_plan(current_state, f'모션 시작 불가: {guard_error}', plan)
                    status['phase'] = current.get('phase', 'initialized')
                    status['phase_started_at'] = current.get('phase_started_at')
                    status['phase_finished_at'] = current.get('phase_finished_at')
                    status['lifecycle'] = self._current_lifecycle()
                    self._set_status(status)
                    return {
                        'success': False,
                        'message': guard_error,
                        'status': self.status(),
                        'summary': plan['summary'],
                    }
                target = self._run_initialization_then_motion
                target_args = (initialization_plan, plan)
            self._stop_event.clear()
            self._graceful_stop_event.clear()
            self._run_thread = threading.Thread(
                target=target,
                args=target_args,
                daemon=True,
            )
            self._run_thread.start()

        return {
            'success': True,
            'message': (
                'initial position move started'
                if mode == 'initialize'
                else (
                    'initial position move and continuous motion run started'
                    if plan.get('run_mode') == 'continuous'
                    else 'initial position move and single motion run started'
                )
            ),
            'status': self.status(),
            'summary': plan['summary'],
        }

    def _handle_stop(self) -> Dict[str, Any]:
        current = self.status()
        if current.get('automation_run'):
            try:
                self._save_automation(
                    {
                        'armed': False,
                        'last_error': '사용자가 모션을 즉시 정지했습니다',
                    },
                    runtime_state='stopped',
                    runtime_message='사용자가 모션을 즉시 정지했습니다',
                )
            except ValueError:
                pass
        self._stop_event.set()
        self._graceful_stop_event.clear()
        if current.get('state') in (
            'initializing',
            'countdown',
            'running',
            'verifying',
        ):
            self._update_status({
                'state': 'stopping',
                'phase': 'stopping',
                'message': 'stop requested',
            })
        else:
            self._update_status({
                'state': 'stopped',
                'phase': 'stopped',
                'phase_finished_at': time.time(),
                'message': 'stop requested',
            })
        return {
            'success': True,
            'message': 'motion run stop requested',
            'status': self.status(),
        }

    def _run_initialization(self, plan: Dict[str, Any]) -> None:
        try:
            init_axes = list(plan['axes'])
            if not init_axes:
                now = time.time()
                status = self._status_from_plan('initialized', '초기 위치 이동 대상이 없습니다', plan)
                status['phase'] = 'initialized'
                status['phase_started_at'] = now
                status['phase_finished_at'] = now
                status['lifecycle'] = {
                    **self._current_lifecycle(),
                    'initial_started_at': now,
                    'initial_finished_at': now,
                }
                self._set_status(status)
                return

            motors = self._current_motors()
            starts: Dict[int, float] = {}
            targets: Dict[int, float] = {}
            durations: Dict[int, float] = {}
            for axis in init_axes:
                motor_axis = int(axis['motor_axis'])
                motor = self._motor_for_axis(motor_axis, motors)
                motor_error = self._motor_ready_error(
                    motor or {'controller_index': motor_axis}
                )
                if motor_error:
                    raise RuntimeError(motor_error)
                current = self._motor_position_deg(motor)
                if current is None:
                    raise RuntimeError(f'Axis {motor_axis} current position is unavailable')
                starts[motor_axis] = current
                targets[motor_axis] = float(axis['initial_motor_target_deg'])
                durations[motor_axis] = max(float(axis.get('initial_move_time_sec') or 0.0), self.period_sec)

            max_duration = max(durations.values()) if durations else self.period_sec
            initial_started_at = time.time()
            status = self._status_from_plan('initializing', '초기 위치 이동 중', plan)
            status['phase'] = 'initializing'
            status['phase_started_at'] = initial_started_at
            status['phase_finished_at'] = None
            status['lifecycle'] = {
                **self._current_lifecycle(),
                'initial_started_at': initial_started_at,
                'initial_finished_at': None,
            }
            if plan.get('automation_run'):
                with self._run_lock:
                    self._automation_runtime.update({
                        'state': 'initializing',
                        'message': '자동 반복 초기위치 이동 중',
                    })
            self._set_status(status)
            self._run_initial_position_stream(
                motors,
                init_axes,
                starts,
                targets,
                durations,
                max_duration,
            )
            reached, message = self._wait_for_targets(
                init_axes,
                targets,
                self._target_settle_timeout_sec(),
            )
            if not reached:
                raise RuntimeError(f'초기 위치 도달 확인 실패: {message}')
            self._publish_motion_values({
                str(axis['motion_id']): float(axis['initial_motion_position_deg'])
                for axis in init_axes
            })
            initial_finished_at = time.time()
            status = self._status_from_plan('initialized', '초기 위치 이동 완료', plan)
            status['phase'] = 'initialized'
            status['phase_started_at'] = initial_started_at
            status['phase_finished_at'] = initial_finished_at
            status['lifecycle'] = {
                **self._current_lifecycle(),
                'initial_started_at': initial_started_at,
                'initial_finished_at': initial_finished_at,
            }
            if plan.get('automation_run'):
                with self._run_lock:
                    self._automation_runtime.update({
                        'state': 'initialized',
                        'message': '자동 반복 초기위치 이동 완료',
                    })
            self._set_status(status)
        except InterruptedError:
            status = self._status_from_plan('stopped', '초기 위치 이동 정지', plan)
            status['phase'] = 'stopped'
            status['phase_finished_at'] = time.time()
            status['lifecycle'] = self._current_lifecycle()
            self._set_status(status)
        except Exception as exc:
            self.get_logger().error(f'initial position move failed\n{traceback.format_exc()}')
            status = self._status_from_plan('error', f'초기 위치 이동 실패: {exc}', plan)
            status['phase'] = 'error'
            status['phase_finished_at'] = time.time()
            status['lifecycle'] = self._current_lifecycle()
            self._set_status(status)
            if plan.get('automation_run'):
                self._automation_failure(str(exc))

    def _run_initialization_then_motion(
        self,
        initialization_plan: Dict[str, Any],
        motion_plan: Dict[str, Any],
    ) -> None:
        self._run_initialization(initialization_plan)
        if self._stop_event.is_set():
            return
        if self.status().get('state') != 'initialized':
            return
        if not self._run_countdown(motion_plan):
            return
        if (
            motion_plan.get('automation_run')
            and self._graceful_stop_event.is_set()
        ):
            self._finish_cycle_stop(
                motion_plan,
                time.time(),
                0,
                '초기위치 이동 완료 후 자동 반복 정지',
            )
            return
        if motion_plan.get('repeat_mode') == 'reinitialize':
            self._run_motion(motion_plan, initialization_plan)
        else:
            self._run_motion(motion_plan)

    def _run_countdown(self, plan: Dict[str, Any]) -> bool:
        duration = max(float(plan.get('countdown_sec') or 0.0), 0.0)
        if duration <= 0.0:
            return True
        started_at = time.time()
        deadline = time.monotonic() + duration
        status = self._status_from_plan('countdown', '모션 시작 대기', plan)
        status['phase'] = 'countdown'
        status['phase_started_at'] = started_at
        status['phase_finished_at'] = None
        status['lifecycle'] = self._current_lifecycle()
        self._set_status(status)
        while True:
            if self._stop_event.is_set():
                status = self._status_from_plan(
                    'stopped',
                    '모션 시작 대기 중 정지',
                    plan,
                )
                status['phase'] = 'stopped'
                status['phase_started_at'] = started_at
                status['phase_finished_at'] = time.time()
                status['lifecycle'] = self._current_lifecycle()
                self._set_status(status)
                return False
            remaining = max(deadline - time.monotonic(), 0.0)
            elapsed = min(duration - remaining, duration)
            self._update_status({
                'state': 'countdown',
                'phase': 'countdown',
                'message': f'모션 시작 {max(math.ceil(remaining), 1)}초 전',
                'progress': {
                    'elapsed_sec': elapsed,
                    'duration_sec': duration,
                    'ratio': min(elapsed / duration, 1.0),
                    'sample_index': 0,
                    'active_axis_count': len(plan.get('axes') or []),
                },
            })
            if remaining <= 0.0:
                return True
            time.sleep(min(0.05, remaining))

    def _run_motion(
        self,
        plan: Dict[str, Any],
        initialization_plan: Optional[Dict[str, Any]] = None,
    ) -> None:
        try:
            run_mode = str(plan.get('run_mode') or 'once')
            continuous = run_mode == 'continuous'
            automation_run = bool(plan.get('automation_run'))
            repeat_mode = str(plan.get('repeat_mode') or 'direct')
            dwell_sec = max(float(plan.get('dwell_sec') or 0.0), 0.0)
            self._require_playback_command_allowed()
            motors = self._current_motors()
            self._prepare_motion_stream(motors, plan['axes'])
            motion_started_at = time.time()
            running_message = (
                '자동 반복 모션 실행 중'
                if automation_run
                else ('연속 모션 실행 중' if continuous else '모션 1회 실행 중')
            )
            status = self._status_from_plan('running', running_message, plan)
            status['phase'] = 'running'
            status['phase_started_at'] = motion_started_at
            status['phase_finished_at'] = None
            status['lifecycle'] = {
                **self._current_lifecycle(),
                'motion_started_at': motion_started_at,
                'motion_finished_at': None,
            }
            if automation_run:
                with self._run_lock:
                    self._automation_runtime.update({
                        'state': 'running',
                        'message': running_message,
                    })
            self._set_status(status)
            samples = plan['samples']
            cycle_count = 0
            grade1_seen = False
            while True:
                cycle_started = time.monotonic()
                for index, sample in enumerate(samples):
                    if self._stop_event.is_set():
                        status = self._status_from_plan('stopped', '연속 모션 정지' if continuous else '모션 실행 정지', plan)
                        status['phase'] = 'stopped'
                        status['phase_started_at'] = motion_started_at
                        status['phase_finished_at'] = time.time()
                        status['lifecycle'] = self._current_lifecycle()
                        status['cycle_count'] = cycle_count
                        self._set_status(status)
                        return
                    self._require_playback_command_allowed()
                    if automation_run and self._current_servo_alarm_grade() == 1:
                        grade1_seen = True
                    positions = sample['positions']
                    self._publish_motion_setpoints(
                        motors,
                        plan['axes'],
                        positions,
                        sample.get('motion_values'),
                    )
                    self._update_progress(
                        'running',
                        float(sample['time_sec']),
                        float(plan['summary']['duration_sec']),
                        index,
                        len(positions),
                        run_mode=run_mode,
                        cycle_count=cycle_count,
                        current_cycle=cycle_count + 1,
                    )
                    self._sleep_until(cycle_started + ((index + 1) * self.period_sec))
                cycle_count += 1
                if not continuous:
                    break
                if automation_run and grade1_seen:
                    self._automation_failure(
                        '1등급 서보 에러 · 나머지 축의 현재 회차 완료 후 자동 반복 중단'
                    )
                    self._finish_cycle_stop(
                        plan,
                        motion_started_at,
                        cycle_count,
                        '1등급 서보 에러로 자동 반복 중단',
                        state='error',
                    )
                    return
                if automation_run and self._graceful_stop_event.is_set():
                    self._finish_cycle_stop(
                        plan,
                        motion_started_at,
                        cycle_count,
                        '현재 모션 회차 완료 후 자동 반복 정지',
                    )
                    return
                if repeat_mode == 'dwell' and dwell_sec > 0.0:
                    if not self._wait_between_cycles(
                        plan,
                        motion_started_at,
                        cycle_count,
                        dwell_sec,
                    ):
                        return
                elif repeat_mode == 'reinitialize':
                    if initialization_plan is None:
                        raise RuntimeError('반복 초기위치 이동 계획이 없습니다')
                    self._run_initialization(initialization_plan)
                    if self._stop_event.is_set():
                        return
                    if self.status().get('state') != 'initialized':
                        raise RuntimeError(
                            self.status().get('message')
                            or '반복 초기위치 이동 실패'
                        )
                    if automation_run and self._graceful_stop_event.is_set():
                        self._finish_cycle_stop(
                            plan,
                            motion_started_at,
                            cycle_count,
                            '반복 초기위치 이동 완료 후 자동 반복 정지',
                        )
                        return
                    self._require_playback_command_allowed()
                    motors = self._current_motors()
                    self._prepare_motion_stream(motors, plan['axes'])
                    self._restore_running_status(
                        plan,
                        motion_started_at,
                        cycle_count,
                    )

            final_positions = samples[-1]['positions'] if samples else {}
            if final_positions:
                self._publish_motion_setpoints(
                    motors,
                    plan['axes'],
                    final_positions,
                    samples[-1].get('motion_values'),
                )
                status = self._status_from_plan('verifying', '모션 최종 위치 확인 중', plan)
                status['phase'] = 'verifying'
                status['phase_started_at'] = motion_started_at
                status['phase_finished_at'] = None
                status['lifecycle'] = self._current_lifecycle()
                status['progress'] = {
                    'elapsed_sec': float(plan['summary']['duration_sec']),
                    'duration_sec': float(plan['summary']['duration_sec']),
                    'ratio': 1.0,
                    'sample_index': len(samples),
                    'active_axis_count': len(final_positions),
                }
                self._set_status(status)
                reached, message = self._wait_for_targets(
                    plan['axes'],
                    final_positions,
                    self._target_settle_timeout_sec(),
                )
                if not reached:
                    raise RuntimeError(f'모션 최종 위치 도달 확인 실패: {message}')
            motion_finished_at = time.time()
            status = self._status_from_plan('completed', '모션 실행 완료', plan)
            status['phase'] = 'completed'
            status['phase_started_at'] = motion_started_at
            status['phase_finished_at'] = motion_finished_at
            status['lifecycle'] = {
                **self._current_lifecycle(),
                'motion_started_at': motion_started_at,
                'motion_finished_at': motion_finished_at,
            }
            status['progress'] = {
                'elapsed_sec': float(plan['summary']['duration_sec']),
                'duration_sec': float(plan['summary']['duration_sec']),
                'ratio': 1.0,
                'sample_index': len(samples),
                'active_axis_count': len(plan.get('axes', [])),
            }
            status['cycle_count'] = cycle_count
            self._set_status(status)
        except Exception as exc:
            self.get_logger().error(f'motion run failed\n{traceback.format_exc()}')
            status = self._status_from_plan('error', f'모션 실행 실패: {exc}', plan)
            status['phase'] = 'error'
            status['phase_finished_at'] = time.time()
            status['lifecycle'] = self._current_lifecycle()
            self._set_status(status)
            if bool(plan.get('automation_run')):
                self._automation_failure(str(exc))

    def _finish_cycle_stop(
        self,
        plan: Dict[str, Any],
        motion_started_at: float,
        cycle_count: int,
        message: str,
        *,
        state: str = 'stopped',
    ) -> None:
        status = self._status_from_plan(state, message, plan)
        status['phase'] = state
        status['phase_started_at'] = motion_started_at
        status['phase_finished_at'] = time.time()
        status['lifecycle'] = self._current_lifecycle()
        status['cycle_count'] = cycle_count
        status['current_cycle'] = cycle_count
        with self._run_lock:
            self._automation_runtime.update({
                'state': 'waiting',
                'message': status['message'],
            })
        self._set_status(status)
        self._graceful_stop_event.clear()
        if state != 'error':
            with self._run_lock:
                enabled = bool(self._automation_state.get('enabled'))
                self._automation_runtime.update({
                    'state': 'ready' if enabled else 'off',
                    'message': message,
                    'stop_after_cycle': False,
                })

    def _wait_between_cycles(
        self,
        plan: Dict[str, Any],
        motion_started_at: float,
        cycle_count: int,
        dwell_sec: float,
    ) -> bool:
        started_at = time.time()
        status = self._status_from_plan(
            'waiting',
            f'자동 반복 대기 중 · {dwell_sec:g}초',
            plan,
        )
        status['phase'] = 'repeat_waiting'
        status['phase_started_at'] = started_at
        status['phase_finished_at'] = None
        status['lifecycle'] = self._current_lifecycle()
        status['cycle_count'] = cycle_count
        status['current_cycle'] = cycle_count
        duration_sec = float(plan['summary']['duration_sec'])
        status['progress'] = {
            'elapsed_sec': duration_sec,
            'duration_sec': duration_sec,
            'ratio': 1.0,
            'sample_index': len(plan.get('samples') or []),
            'active_axis_count': len(plan.get('axes') or []),
        }
        status['repeat_wait'] = {
            'duration_sec': dwell_sec,
            'remaining_sec': dwell_sec,
        }
        self._set_status(status)
        deadline = time.monotonic() + dwell_sec
        while time.monotonic() < deadline:
            if self._stop_event.is_set():
                self._finish_cycle_stop(
                    plan,
                    motion_started_at,
                    cycle_count,
                    '자동 반복 대기 중 즉시 정지',
                )
                return False
            if self._graceful_stop_event.is_set():
                self._finish_cycle_stop(
                    plan,
                    motion_started_at,
                    cycle_count,
                    '자동 반복 대기 취소 후 정지',
                )
                return False
            remaining = max(deadline - time.monotonic(), 0.0)
            self._update_status({
                'state': 'waiting',
                'phase': 'repeat_waiting',
                'message': f'다음 모션까지 {remaining:.1f}초',
                'repeat_wait': {
                    'duration_sec': dwell_sec,
                    'remaining_sec': remaining,
                },
            })
            time.sleep(min(0.1, remaining))
        self._restore_running_status(plan, motion_started_at, cycle_count)
        return True

    def _restore_running_status(
        self,
        plan: Dict[str, Any],
        motion_started_at: float,
        cycle_count: int,
    ) -> None:
        message = (
            '자동 반복 모션 실행 중'
            if plan.get('automation_run')
            else '연속 모션 실행 중'
        )
        status = self._status_from_plan('running', message, plan)
        cycle_started_at = time.time()
        status['phase'] = 'running'
        status['phase_started_at'] = cycle_started_at
        status['phase_finished_at'] = None
        status['lifecycle'] = self._current_lifecycle()
        status['cycle_count'] = cycle_count
        status['current_cycle'] = cycle_count + 1
        if plan.get('automation_run'):
            with self._run_lock:
                self._automation_runtime.update({
                    'state': 'running',
                    'message': message,
                })
        self._set_status(status)

    def _current_servo_alarm_grade(self) -> int:
        lock = getattr(self, '_safety_status_lock', None)
        if lock is None:
            return 0
        with lock:
            status = getattr(self, '_latest_safety_status', None)
            payload = dict(status) if isinstance(status, dict) else {}
        try:
            grade = int(payload.get('servo_alarm_grade') or 0)
        except (TypeError, ValueError):
            return 0
        return grade if grade in (1, 2, 3) else 0

    @staticmethod
    def _motion_auto_start_guard_error(plan: Dict[str, Any]) -> str:
        if (
            plan.get('run_mode') == 'continuous'
            and plan.get('repeat_mode') != 'reinitialize'
        ):
            capability = plan.get('capabilities', {}).get('continuous_run', {})
            if not capability.get('available'):
                return str(capability.get('reason') or '모션 시작값과 끝값이 달라 연속 동작할 수 없습니다')
        return ''

    def _run_initial_position_stream(
        self,
        motors: List[Dict[str, Any]],
        axes: List[Dict[str, Any]],
        starts: Dict[int, float],
        targets: Dict[int, float],
        durations: Dict[int, float],
        max_duration: float,
    ) -> None:
        """Move all initial axes with one combined command per control tick.

        Keeping all axes in one MotorStatus message prevents per-axis action
        threads from overwriting each other when many motors move together.
        """
        duration = max(float(max_duration), self.period_sec)
        has_ac_axes = self._has_ac_axes(axes)
        clear_sec = self._setpoint_clear_sec() if has_ac_axes else 0.0
        tick_sec = self.period_sec + clear_sec if has_ac_axes else self.period_sec
        steps = max(1, int(math.ceil(duration / tick_sec)))
        start_time = time.monotonic()

        for step in range(steps + 1):
            if self._stop_event.is_set():
                raise InterruptedError()
            self._require_playback_command_allowed()

            elapsed = min(step * tick_sec, duration)
            positions: Dict[int, float] = {}
            for axis_plan in axes:
                motor_axis = int(axis_plan['motor_axis'])
                start = float(starts[motor_axis])
                target = float(targets[motor_axis])
                axis_duration = max(float(durations.get(motor_axis, duration)), self.period_sec)
                ratio = min(max(elapsed / axis_duration, 0.0), 1.0)
                positions[motor_axis] = start + ((target - start) * self._smoothstep(ratio))

            self._publish_initial_positions(motors, axes, positions, has_ac_axes, clear_sec)
            self._update_progress(
                'initializing',
                elapsed,
                duration,
                step,
                len(positions),
            )

            if step >= steps:
                break
            self._sleep_until(start_time + ((step + 1) * tick_sec))

        self._publish_initial_positions(motors, axes, targets, has_ac_axes, clear_sec)

    def _publish_initial_positions(
        self,
        motors: List[Dict[str, Any]],
        axes: List[Dict[str, Any]],
        positions: Dict[int, float],
        has_ac_axes: bool,
        clear_sec: float,
    ) -> None:
        if has_ac_axes:
            self._publish_ac_enable_for_axes(motors, axes, positions)
            self._sleep_until(time.monotonic() + max(float(clear_sec), 0.0))
        self._publish_motion_setpoints(motors, axes, positions)

    def _publish_initial_action_request(
        self,
        axis_plan: Dict[str, Any],
        target_position: float,
        duration_sec: float,
    ) -> str:
        motor_axis = int(axis_plan['motor_axis'])
        motor_type = str(axis_plan.get('motor_type') or '')
        if motor_type == 'ac_servo':
            command = 'ac_servo_absolute_move'
        elif motor_type == 'dynamixel':
            command = 'dynamixel_absolute_move'
        else:
            raise RuntimeError(f'Axis {motor_axis} unsupported motor type for initialization: {motor_type}')

        generation = int(self._execution_context.get('project_generation') or 0)
        request_id = f'motion-init-g{generation}-{motor_axis}-{time.time_ns()}'
        payload = {
            'request_id': request_id,
            'project_generation': generation,
            'command': command,
            'axis': motor_axis,
            'target_deg': float(target_position),
            'duration_sec': float(duration_sec),
        }
        self._clear_action_results(request_id)
        self._action_request_pub.publish(
            String(data=json.dumps(payload, ensure_ascii=False, separators=(',', ':')))
        )
        return request_id

    def _wait_for_initial_action_start(
        self,
        requests: List[Dict[str, Any]],
    ) -> Dict[str, Dict[str, Any]]:
        pending: Dict[str, Dict[str, Any]] = {}
        deadline = time.monotonic() + 3.0
        for request in requests:
            request_id = str(request['request_id'])
            result = self._wait_for_action_result(request_id, deadline, terminal_only=False)
            if result is None:
                raise RuntimeError(f'Axis {request["axis"]} 초기 위치 동작 시작 응답이 없습니다')
            if not bool(result.get('success')):
                raise RuntimeError(str(result.get('message') or f'Axis {request["axis"]} 초기 위치 동작 시작 실패'))
            if not self._is_terminal_action_result(result):
                pending[request_id] = request
        return pending

    def _wait_for_initial_action_completion(
        self,
        pending: Dict[str, Dict[str, Any]],
        duration_sec: float,
    ) -> None:
        start_time = time.monotonic()
        duration = max(float(duration_sec), self.period_sec)
        step = 0
        while True:
            if self._stop_event.is_set():
                raise InterruptedError()
            elapsed = max(time.monotonic() - start_time, 0.0)
            self._update_progress(
                'initializing',
                min(elapsed, duration),
                duration,
                step,
                len(pending),
            )
            if elapsed >= duration:
                return
            step += 1
            time.sleep(min(max(self.period_sec, 0.02), 0.1))

    def _clear_action_results(self, request_id: str) -> None:
        with self._action_result_lock:
            self._action_results.pop(request_id, None)

    def _wait_for_action_result(
        self,
        request_id: str,
        deadline: float,
        terminal_only: bool,
    ) -> Optional[Dict[str, Any]]:
        while time.monotonic() < deadline:
            result = self._take_action_result(request_id, terminal_only=terminal_only)
            if result is not None:
                return result
            if self._stop_event.is_set():
                return None
            time.sleep(min(max(self.period_sec, 0.01), 0.05))
        return None

    def _take_action_result(
        self,
        request_id: str,
        terminal_only: bool,
    ) -> Optional[Dict[str, Any]]:
        with self._action_result_lock:
            values = self._action_results.get(request_id)
            if not values:
                return None
            for index, payload in enumerate(values):
                if terminal_only and not self._is_terminal_action_result(payload):
                    continue
                result = values.pop(index)
                if not values:
                    self._action_results.pop(request_id, None)
                return result
        return None

    @staticmethod
    def _is_terminal_action_result(payload: Dict[str, Any]) -> bool:
        if not bool(payload.get('success')):
            return True
        message = str(payload.get('message') or '').lower()
        return 'completed' in message or 'did not reach target' in message

    def _build_plan(
        self,
        payload: Dict[str, Any],
        *,
        initialization_only: bool = False,
    ) -> Dict[str, Any]:
        run_mode = str(payload.get('run_mode') or 'once').strip().lower()
        if run_mode not in ('once', 'continuous'):
            raise ValueError('run_mode must be once or continuous')
        automation_run = bool(payload.get('automation_run', False))
        repeat_mode = str(payload.get('repeat_mode') or 'direct').strip().lower()
        if repeat_mode not in REPEAT_MODES:
            raise ValueError(f'지원하지 않는 자동 반복 방식입니다: {repeat_mode}')
        dwell_sec = self._finite_float(payload.get('dwell_sec'))
        dwell_sec = 0.0 if dwell_sec is None else dwell_sec
        if dwell_sec < 0.0:
            raise ValueError('자동 반복 대기 시간은 0초 이상이어야 합니다')
        countdown_sec = self._finite_float(payload.get('countdown_sec'))
        countdown_sec = 0.0 if countdown_sec is None else countdown_sec
        if countdown_sec < 0.0 or countdown_sec > 10.0:
            raise ValueError('모션 시작 대기 시간은 0초 이상 10초 이하여야 합니다')
        try:
            operation_generation = int(payload.get('operation_generation') or 0)
        except (TypeError, ValueError) as exc:
            raise ValueError('작업 세대 값이 올바르지 않습니다') from exc
        if operation_generation < 0:
            raise ValueError('작업 세대 값은 0 이상이어야 합니다')
        if not automation_run:
            repeat_mode = 'direct'
            dwell_sec = 0.0
        motion_file_id = str(payload.get('motion_file_id') or '').strip()
        mapping_file_id = str(payload.get('mapping_file_id') or '').strip()
        request_source = str(payload.get('request_source') or 'motion_run').strip()
        studio_request = request_source == 'motion_studio'
        requested_motion_ids = {
            str(value or '').strip()
            for value in (payload.get('active_motion_ids') or [])
            if str(value or '').strip()
        }
        initial_move_time_override = self._initial_move_time_override_sec(payload)
        if hasattr(self, 'motion_projects_dir'):
            project_id, motion_files_dir, mappings_dir = self._project_asset_dirs(payload)
            motion_directory = motion_files_dir
            if studio_request and motion_file_id.startswith('__studio_'):
                motion_directory = motion_files_dir.parent / 'runtime' / 'studio_runtime'
            motion_file_path = (
                self._motion_file_path(motion_file_id, motion_directory)
                if motion_file_id
                else None
            )
            mapping_path = self._mapping_file_path(mapping_file_id, mappings_dir)
        else:
            # Compatibility for isolated unit tests that replace the path
            # helpers without constructing a ROS node.
            project_id = ''
            motion_file_path = (
                self._motion_file_path(motion_file_id)
                if motion_file_id
                else None
            )
            mapping_path = self._mapping_file_path(mapping_file_id)
        if not motion_file_id and not initialization_only:
            raise ValueError('motion file_id is required')
        motion_records = (
            self._load_motion_records(motion_file_path)
            if motion_file_id
            else []
        )
        source_motion_data_available = bool(motion_records)
        mapping = self._load_mapping(mapping_path)

        mapping_motion_file_id = str(mapping.get('motion_file_id') or '').strip()
        if (
            mapping_motion_file_id
            and mapping_motion_file_id != motion_file_id
            and not studio_request
            and not (initialization_only and not motion_file_id)
        ):
            raise ValueError(
                f'mapping file expects motion file {mapping_motion_file_id}, not {motion_file_id}'
            )

        groups = self._motion_groups(motion_records)
        if request_source != 'motion_studio':
            requested_motion_ids = (
                set()
                if initialization_only
                else {str(motion_id) for motion_id in groups}
            )
        initialization_fallback_used = False
        motors = self._current_motors()
        if not motors:
            raise ValueError('current motion_state is unavailable')

        rows = mapping.get('mappings')
        if not isinstance(rows, list):
            rows = []
        axes = []
        errors = []
        warnings = []
        for row in rows:
            if not isinstance(row, dict) or row.get('enabled') is False:
                continue
            motion_id = str(row.get('motion_id') or '').strip()
            if requested_motion_ids and motion_id not in requested_motion_ids:
                continue
            if not motion_id:
                errors.append('enabled mapping row without motion_id')
                continue
            motor_ref = str(row.get('motor_ref') or '').strip()
            motor_axis = self._optional_int(row.get('motor_axis'))
            motor = None
            if motor_ref:
                matches = self._motors_for_ref(motor_ref, motors)
                if len(matches) == 0:
                    errors.append(f'Motion ID {motion_id}: Motor {motor_ref} not found')
                    continue
                if len(matches) > 1:
                    errors.append(f'Motion ID {motion_id}: Motor {motor_ref} is duplicated')
                    continue
                motor = matches[0]
                motor_axis = self._optional_int(motor.get('controller_index'))
            elif motor_axis is not None:
                # Backward compatibility for mapping files saved before motor_ref.
                motor = self._motor_for_axis(motor_axis, motors)
            if motor_axis is None:
                errors.append(f'Motion ID {motion_id}: motor_ref is required')
                continue
            missing_motion_data = motion_id not in groups
            if missing_motion_data:
                if not initialization_only:
                    errors.append(f'Motion ID {motion_id}: motion file data not found')
                    continue
                initial_mode = str(row.get('initial_mode') or 'first_frame')
                fallback_value = (
                    self._finite_float(row.get('initial_motion_position_deg')) or 0.0
                    if initial_mode == 'manual'
                    else 0.0
                )
                fallback_record = {
                    'frame': 0,
                    'time_sec': 0.0,
                    'motion_id': motion_id,
                    'value': float(fallback_value),
                    'row_index': len(motion_records),
                }
                motion_records.append(fallback_record)
                groups[motion_id] = [fallback_record]
                initialization_fallback_used = True
                warnings.append(
                    f'Motion ID {motion_id}: '
                    + (
                        f'모션 데이터가 없어 수동 초기위치 {fallback_value:.3f}°를 사용'
                        if initial_mode == 'manual'
                        else '첫 프레임 데이터가 없어 모션 0°를 초기위치로 사용'
                    )
                )
            if motor is None:
                errors.append(f'Motion ID {motion_id}: Axis {motor_axis} not found')
                continue
            motor_error = self._motor_ready_error(motor)
            if motor_error:
                errors.append(f'Motion ID {motion_id}: {motor_error}')

            motion_values = [record['value'] for record in groups[motion_id]]
            motion_min = min(motion_values)
            motion_max = max(motion_values)
            lower = self._finite_float(row.get('motion_lower_deg'))
            upper = self._finite_float(row.get('motion_upper_deg'))
            if lower is not None and upper is not None and lower > upper:
                errors.append(f'Motion ID {motion_id}: motion min limit must be <= max limit')
                continue
            if missing_motion_data and (
                (lower is not None and motion_values[0] < lower)
                or (upper is not None and motion_values[0] > upper)
            ):
                errors.append(
                    f'Motion ID {motion_id}: 초기 모션값 {motion_values[0]:.3f}°가 '
                    '모션 설정 범위 밖입니다'
                )
                continue
            if lower is not None and motion_min < lower:
                warnings.append(
                    f'Motion ID {motion_id}: {motion_min:.3f}° 이하 데이터는 {lower:.3f}°로 제한'
                )
            if upper is not None and motion_max > upper:
                warnings.append(
                    f'Motion ID {motion_id}: {motion_max:.3f}° 이상 데이터는 {upper:.3f}°로 제한'
                )

            command_motion_min = self._clamp_motion_value(motion_min, lower, upper)
            command_motion_max = self._clamp_motion_value(motion_max, lower, upper)

            target_min = self._motor_target(row, command_motion_min)
            target_max = self._motor_target(row, command_motion_max)
            target_low = min(target_min, target_max)
            target_high = max(target_min, target_max)
            limit_error = self._target_range_limit_error(motor, target_low, target_high)
            if limit_error:
                errors.append(f'Motion ID {motion_id}: {limit_error}')

            initial_motion_source_value = self._initial_motion_value(row, groups[motion_id])
            initial_motion_value = self._clamp_motion_value(
                initial_motion_source_value,
                lower,
                upper,
            )
            row_initial_time = max(
                self._finite_float(row.get('initial_move_time_sec')) or 0.0,
                0.0,
            )
            initial_move_time = (
                initial_move_time_override
                if initial_move_time_override is not None
                else row_initial_time
            )
            axis_plan = {
                'motion_id': motion_id,
                'motor_ref': motor_ref,
                'motor_axis': motor_axis,
                'motor_type': self._motor_type(motor),
                'initial_move_time_sec': initial_move_time,
                'initial_motion_source_position_deg': initial_motion_source_value,
                'initial_motion_position_deg': initial_motion_value,
                'initial_motor_target_deg': self._motor_target(row, initial_motion_value),
                'motion_limit_lower_deg': lower,
                'motion_limit_upper_deg': upper,
                'source_motion_min_deg': motion_min,
                'source_motion_max_deg': motion_max,
                'command_motion_min_deg': command_motion_min,
                'command_motion_max_deg': command_motion_max,
                'motion_clamped': command_motion_min != motion_min or command_motion_max != motion_max,
                'target_min_deg': target_low,
                'target_max_deg': target_high,
                'loop_start_motion_deg': self._clamp_motion_value(motion_values[0], lower, upper),
                'loop_end_motion_deg': self._clamp_motion_value(motion_values[-1], lower, upper),
                'row': row,
            }
            axis_plan['loop_start_target_deg'] = self._motor_target(
                row,
                axis_plan['loop_start_motion_deg'],
            )
            axis_plan['loop_end_target_deg'] = self._motor_target(
                row,
                axis_plan['loop_end_motion_deg'],
            )
            axis_plan['loop_delta_deg'] = abs(
                float(axis_plan['loop_end_motion_deg']) - float(axis_plan['loop_start_motion_deg'])
            )
            axis_plan['loop_motor_delta_deg'] = abs(
                float(axis_plan['loop_end_target_deg']) - float(axis_plan['loop_start_target_deg'])
            )
            axis_plan['loop_tolerance_deg'] = CONTINUOUS_LOOP_TOLERANCE_DEG
            axes.append(axis_plan)

        if not axes:
            errors.append('enabled motion mappings not found')
        if requested_motion_ids:
            planned_motion_ids = {str(axis['motion_id']) for axis in axes}
            missing_requested = sorted(requested_motion_ids - planned_motion_ids)
            if missing_requested:
                errors.append(
                    'requested Motion ID is unavailable: '
                    + ', '.join(missing_requested)
                )
        duplicate_axes = self._duplicate_axis_text(axes)
        if duplicate_axes:
            errors.append(f'duplicate motor axis in enabled mappings: {duplicate_axes}')
        if errors:
            raise ValueError('; '.join(errors[:8]))

        start_time = min(record['time_sec'] for record in motion_records)
        end_time = max(record['time_sec'] for record in motion_records)
        duration = max(end_time - start_time, 0.0)
        sample_count = max(1, int(math.floor(duration / self.period_sec)) + 1)
        last_time = start_time + ((sample_count - 1) * self.period_sec)
        if end_time - last_time > 0.001:
            sample_count += 1

        samples = []
        for index in range(sample_count):
            sample_time = min(start_time + (index * self.period_sec), end_time)
            positions = {}
            motion_values = {}
            for axis in axes:
                motion_value = self._interpolated_value(groups[axis['motion_id']], sample_time)
                motion_value = self._clamp_motion_value(
                    motion_value,
                    axis.get('motion_limit_lower_deg'),
                    axis.get('motion_limit_upper_deg'),
                )
                positions[int(axis['motor_axis'])] = self._motor_target(axis['row'], motion_value)
                motion_values[str(axis['motion_id'])] = float(motion_value)
            samples.append({
                'time_sec': sample_time - start_time,
                'absolute_time_sec': sample_time,
                'positions': positions,
                'motion_values': motion_values,
            })

        complete_motion_data_available = (
            source_motion_data_available and not initialization_fallback_used
        )
        continuous_capability = (
            self._continuous_capability(axes)
            if complete_motion_data_available
            else {
                'available': False,
                'reason': '실제 모션 데이터가 없어 초기 위치 이동만 가능합니다',
            }
        )
        capabilities = {
            'initial_position': {
                'available': True,
                'reason': '모터 상태·매핑·초기 목표 검사 통과',
            },
            'single_run': {
                'available': complete_motion_data_available,
                'reason': (
                    '모터 상태·매핑 검사 통과, 모션 범위 초과값은 Min/Max로 제한'
                    if complete_motion_data_available
                    else '실제 모션 데이터가 없어 재생할 수 없습니다'
                ),
            },
            'continuous_run': {
                **continuous_capability,
            },
        }

        return {
            'project_id': project_id,
            'request_source': request_source,
            'motion_file_id': motion_file_id,
            'mapping_file_id': mapping_file_id,
            'run_mode': run_mode,
            'automation_run': automation_run,
            'repeat_mode': repeat_mode,
            'dwell_sec': dwell_sec,
            'countdown_sec': countdown_sec,
            'operation_generation': operation_generation,
            'motion_file_path': str(motion_file_path) if motion_file_path else '',
            'mapping_path': str(mapping_path),
            'axes': axes,
            'samples': samples,
            'warnings': warnings,
            'capabilities': capabilities,
            'summary': {
                'request_source': request_source,
                'motion_file_id': motion_file_id,
                'mapping_file_id': mapping_file_id,
                'axis_count': len(axes),
                'duration_sec': duration,
                'period_sec': self.period_sec,
                'sample_count': len(samples),
                'initial_move_time_sec': initial_move_time_override,
                'continuous_available': continuous_capability['available'],
                'clamped_axis_count': sum(1 for axis in axes if axis.get('motion_clamped')),
                'automation_run': automation_run,
                'repeat_mode': repeat_mode,
                'dwell_sec': dwell_sec,
                'countdown_sec': countdown_sec,
                'operation_generation': operation_generation,
            },
        }

    @staticmethod
    def _continuous_capability(axes: List[Dict[str, Any]]) -> Dict[str, Any]:
        mismatched = [
            axis for axis in axes
            if float(axis['loop_delta_deg']) > float(axis['loop_tolerance_deg'])
        ]
        if not mismatched:
            return {
                'available': True,
                'reason': '모든 축의 모션 시작·종료값이 5° 이내입니다',
            }
        details = ', '.join(
            f"Axis {axis['motor_axis']} 모션값 차이 {axis['loop_delta_deg']:.3f}° "
            f"(허용 {axis['loop_tolerance_deg']:.3f}°)"
            for axis in mismatched[:4]
        )
        return {
            'available': False,
            'reason': f'모션 시작·종료값 차이가 5°를 초과합니다: {details}',
        }

    @staticmethod
    def _clamp_motion_value(
        value: float,
        lower: Optional[float],
        upper: Optional[float],
    ) -> float:
        result = float(value)
        if lower is not None:
            result = max(result, float(lower))
        if upper is not None:
            result = min(result, float(upper))
        return result

    @staticmethod
    def _unavailable_capabilities(reason: str) -> Dict[str, Dict[str, Any]]:
        message = str(reason or '실행 준비 검사 실패')
        return {
            'initial_position': {'available': False, 'reason': message},
            'single_run': {'available': False, 'reason': message},
            'continuous_run': {'available': False, 'reason': message},
        }

    def _publish_motion_setpoints(
        self,
        motors: List[Dict[str, Any]],
        axes: List[Dict[str, Any]],
        positions: Dict[int, float],
        motion_values: Optional[Dict[str, float]] = None,
    ) -> None:
        if not positions:
            return
        self._publish_positions(motors, axes, positions)
        if motion_values:
            self._publish_motion_values(motion_values)

    def _publish_motion_values(self, values: Dict[str, float]) -> None:
        publisher = getattr(self, '_motion_value_pub', None)
        if publisher is None:
            return
        cleaned = {}
        for motion_id, value in values.items():
            number = self._finite_float(value)
            key = str(motion_id or '').strip()
            if key and number is not None:
                cleaned[key] = float(number)
        if not cleaned:
            return
        payload = {
            'source': 'motion_run',
            'project_id': str(self._execution_context.get('project_id') or ''),
            'project_generation': int(
                self._execution_context.get('project_generation') or 0
            ),
            'stamp': time.time(),
            'values': cleaned,
        }
        publisher.publish(String(data=json.dumps(payload, ensure_ascii=False)))

    def _playback_ownership_error(self) -> str:
        """Return why runtime commands cannot currently own motor output."""
        lock = getattr(self, '_safety_status_lock', None)
        if lock is None:
            # Lightweight unit-test instances created with __new__ predate this
            # runtime subscription. Normal ROS nodes always initialize the lock.
            return ''
        with lock:
            payload = getattr(self, '_latest_safety_status', None)
            received_at = getattr(self, '_latest_safety_status_at', None)
            status = dict(payload) if isinstance(payload, dict) else None
        if status is None or received_at is None:
            return '모션 Supervisor 상태를 아직 받지 못했습니다'
        if time.monotonic() - float(received_at) > SAFETY_STATUS_TIMEOUT_SEC:
            return '모션 Supervisor 상태가 갱신되지 않았습니다'
        if bool(status.get('emergency_latched')):
            return '긴급정지 잠김 상태입니다. 상위 프로그램 재시작이 필요합니다'
        if bool(status.get('commands_blocked')):
            return str(status.get('message') or '모터 명령이 일시 차단된 상태입니다')
        owner = str(status.get('command_owner') or 'none').strip().lower()
        if owner not in ('none', 'playback'):
            owner_names = {
                'midi': 'MIDI 제어',
                'manual': '수동 제어',
            }
            return f"{owner_names.get(owner, owner)}가 사용 중이어서 모션을 시작할 수 없습니다"
        return ''

    def _require_playback_command_allowed(self) -> None:
        error = self._playback_ownership_error()
        if error:
            raise RuntimeError(error)

    def _prepare_motion_stream(
        self,
        motors: List[Dict[str, Any]],
        axes: List[Dict[str, Any]],
    ) -> None:
        """Prime AC servo axes once before frame-by-frame motion streaming."""
        if self._has_ac_axes(axes):
            self._publish_ac_enable_for_axes(motors, axes)
            time.sleep(self._setpoint_clear_sec())

    def _publish_positions(
        self,
        motors: List[Dict[str, Any]],
        axes: List[Dict[str, Any]],
        positions: Dict[int, float],
    ) -> None:
        target_axes = self._sorted_controller_axes(positions.keys())
        command = self._empty_motor_command(target_axes)
        axes_by_index = {int(axis['motor_axis']): axis for axis in axes}
        for slot, motor_axis in enumerate(target_axes):
            target = positions.get(motor_axis)
            if target is None:
                continue
            axis_plan = axes_by_index.get(int(motor_axis), {})
            command.number_of_target_interfaces[slot] = 2
            command.target_interface_id[slot] = Int8MultiArray(
                data=[ID_CONTROLWORD, ID_TARGET_POSITION]
            )
            command.controlword[slot] = (
                DYNAMIXEL_TORQUE_ENABLE
                if axis_plan.get('motor_type') == 'dynamixel'
                else CW_NEW_SET_POINT_MINAS
            )
            command.position[slot] = float(target)
        self._command_pub.publish(command)

    def _publish_ac_enable_for_axes(
        self,
        motors: List[Dict[str, Any]],
        axes: List[Dict[str, Any]],
        positions: Optional[Dict[int, float]] = None,
    ) -> None:
        target_axes = set(int(axis) for axis in positions.keys()) if positions is not None else None
        ac_axes = [
            int(axis['motor_axis'])
            for axis in axes
            if axis.get('motor_type') == 'ac_servo'
            and (target_axes is None or int(axis['motor_axis']) in target_axes)
        ]
        if not ac_axes:
            return
        ac_axes = self._sorted_controller_axes(ac_axes)
        command = self._empty_motor_command(ac_axes)
        for slot, _axis in enumerate(ac_axes):
            command.number_of_target_interfaces[slot] = 1
            command.target_interface_id[slot] = Int8MultiArray(data=[ID_CONTROLWORD])
            command.controlword[slot] = CW_ENABLE_OPERATION_MINAS
        self._command_pub.publish(command)

    @staticmethod
    def _has_ac_axes(axes: List[Dict[str, Any]]) -> bool:
        return any(axis.get('motor_type') == 'ac_servo' for axis in axes)

    def _setpoint_clear_sec(self) -> float:
        return max(self.period_sec + 0.002, 0.002)

    def _wait_for_targets(
        self,
        axes: List[Dict[str, Any]],
        targets: Dict[int, float],
        timeout_sec: float,
    ) -> tuple[bool, str]:
        deadline = time.monotonic() + max(float(timeout_sec), 0.0)
        last_message = ''
        while True:
            motors = self._current_motors()
            ok = True
            messages = []
            for axis_plan in axes:
                motor_axis = int(axis_plan['motor_axis'])
                if motor_axis not in targets:
                    continue
                motor = self._motor_for_axis(motor_axis, motors)
                ready_error = self._motor_ready_error(
                    motor or {'controller_index': motor_axis}
                )
                if ready_error:
                    return False, ready_error
                current = self._motor_position_deg(motor)
                target = float(targets[motor_axis])
                tolerance = self._target_tolerance_deg(axis_plan)
                if current is None:
                    ok = False
                    messages.append(f'Axis {motor_axis} current position is unavailable')
                    continue
                error = abs(current - target)
                if error > tolerance:
                    ok = False
                    messages.append(
                        f'Axis {motor_axis} current {current:.3f} deg, '
                        f'target {target:.3f} deg, error {error:.3f} deg'
                    )
            if ok:
                return True, 'targets reached'
            last_message = '; '.join(messages[:4])
            if time.monotonic() >= deadline:
                return False, last_message or 'target position was not reached'
            time.sleep(min(max(self.period_sec, 0.01), 0.05))

    def _target_tolerance_deg(self, axis_plan: Dict[str, Any]) -> float:
        if axis_plan.get('motor_type') == 'dynamixel':
            return self._runtime_float_parameter(
                'dynamixel_target_tolerance_deg',
                self.dynamixel_target_tolerance_deg,
            )
        return self._runtime_float_parameter(
            'ac_target_tolerance_deg',
            self.ac_target_tolerance_deg,
        )

    def _target_settle_timeout_sec(self) -> float:
        return self._runtime_float_parameter(
            'target_settle_timeout_sec',
            self.target_settle_timeout_sec,
        )

    def _runtime_float_parameter(self, name: str, fallback: float) -> float:
        try:
            value = float(self.get_parameter(name).value)
        except Exception:
            value = float(fallback)
        if not math.isfinite(value):
            return float(fallback)
        return max(value, 0.0)

    def _empty_motor_command(
        self,
        controller_axes: List[int],
    ) -> MotorStatus:
        indexes = self._sorted_controller_axes(controller_axes)
        size = len(indexes)
        command = MotorStatus()
        command.number_of_target_interfaces = [0] * size
        command.target_interface_id = [Int8MultiArray(data=[]) for _ in range(size)]
        command.controller_index = indexes
        command.controlword = [0] * size
        command.statusword = [0] * size
        command.errorcode = [0] * size
        command.position = [0.0] * size
        command.velocity = [0.0] * size
        command.effort = [0.0] * size
        return command

    @staticmethod
    def _sorted_controller_axes(values: Any) -> List[int]:
        axes = []
        for value in values:
            try:
                axis = int(value)
            except (TypeError, ValueError):
                continue
            if axis >= 0 and axis not in axes:
                axes.append(axis)
        return sorted(axes)

    def _current_motors(self) -> List[Dict[str, Any]]:
        with self._state_lock:
            state = self._latest_state
            received_at = self._latest_state_at
        if state is None or received_at is None:
            return []
        if time.time() - received_at > STATE_TIMEOUT_SEC:
            return []
        motors = state.get('motors', [])
        return [motor for motor in motors if isinstance(motor, dict)] if isinstance(motors, list) else []

    def _motor_for_axis(
        self,
        axis: int,
        motors: Optional[List[Dict[str, Any]]] = None,
    ) -> Optional[Dict[str, Any]]:
        for motor in motors if motors is not None else self._current_motors():
            if self._optional_int(motor.get('controller_index')) == axis:
                return motor
        return None

    def _motor_ref_for_motor(self, motor: Dict[str, Any]) -> str:
        motor_type = self._motor_type(motor)
        if motor_type == 'ac_servo':
            alias = self._optional_int(
                motor.get('alias', motor.get('ethercat_alias'))
            )
            return f'ac_servo:alias:{alias}' if alias is not None else ''
        if motor_type == 'dynamixel':
            bus_id = self._optional_int(
                motor.get('bus_id', motor.get('node_id'))
            )
            return f'dynamixel:id:{bus_id}' if bus_id is not None else ''
        return ''

    def _motors_for_ref(
        self,
        motor_ref: Any,
        motors: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        target = str(motor_ref or '').strip().lower()
        if not target:
            return []
        return [
            motor for motor in motors
            if self._motor_ref_for_motor(motor).lower() == target
        ]

    def _motor_ready_error(self, motor: Dict[str, Any]) -> str:
        axis = self._optional_int(motor.get('controller_index'))
        if str(motor.get('state') or '') != 'detected':
            return f'Axis {axis} is not detected'
        errorcode = self._optional_int(motor.get('errorcode')) or 0
        if errorcode:
            error_hex = str(motor.get('errorcode_hex') or f'0x{errorcode & 0xFFFF:04X}')
            error_text = str(motor.get('error_text') or '').strip()
            detail = f' ({error_text})' if error_text else ''
            return f'Axis {axis} motor alarm {error_hex}{detail}'
        if bool(motor.get('fault', False)):
            return f'Axis {axis} has error'
        if self._motor_type(motor) == 'ac_servo' and motor.get('servo_on') is not True:
            return f'Axis {axis} servo is OFF'
        return ''

    def _target_range_limit_error(
        self,
        motor: Dict[str, Any],
        target_min: float,
        target_max: float,
    ) -> str:
        lower = self._finite_float(motor.get('lower'))
        upper = self._finite_float(motor.get('upper'))
        axis = self._optional_int(motor.get('controller_index'))
        if lower is not None and target_min < lower:
            return f'Axis {axis} target min {target_min:.3f} < lower {lower:.3f}'
        if upper is not None and target_max > upper:
            return f'Axis {axis} target max {target_max:.3f} > upper {upper:.3f}'
        return ''

    def _motor_position_deg(self, motor: Optional[Dict[str, Any]]) -> Optional[float]:
        if motor is None:
            return None
        for key in (
            'position_deg',
            'position_actual_deg',
            'output_position_deg',
            'present_position_deg',
            'position_actual',
            'position',
        ):
            number = self._finite_float(motor.get(key))
            if number is not None:
                return number
        return None

    def _motor_type(self, motor: Dict[str, Any]) -> str:
        values = [
            motor.get('motor_type'),
            motor.get('motor_type_label'),
            motor.get('driver_model'),
            motor.get('driver_name'),
            motor.get('transport'),
        ]
        text = ' '.join(str(value or '').lower() for value in values)
        if 'dynamixel' in text:
            return 'dynamixel'
        if 'minas' in text or 'ac servo' in text or 'ac_servo' in text:
            return 'ac_servo'
        return 'unknown'

    def _motor_target(self, row: Dict[str, Any], motion_value: float) -> float:
        sign = -1.0 if bool(row.get('invert')) else 1.0
        reference = self._finite_float(row.get('reference_position_deg')) or 0.0
        if row.get('reference_enabled') is False:
            reference = 0.0
        offset = self._finite_float(row.get('offset_deg')) or 0.0
        scale = self._finite_float(row.get('scale')) or 1.0
        gear_ratio = self._finite_float(row.get('gear_ratio')) or 1.0
        output_axis_value = (float(motion_value) + offset) * scale * sign
        return reference + (output_axis_value * gear_ratio)

    def _initial_motion_value(
        self,
        row: Dict[str, Any],
        records: List[Dict[str, Any]],
    ) -> float:
        if str(row.get('initial_mode') or 'first_frame') == 'manual':
            return self._finite_float(row.get('initial_motion_position_deg')) or 0.0
        return float(records[0]['value'])

    def _load_mapping(self, path: Path) -> Dict[str, Any]:
        data = yaml.safe_load(path.read_text(encoding='utf-8')) or {}
        if not isinstance(data, dict):
            raise ValueError('motion mapping root must be an object')
        return data

    def _initial_move_time_override_sec(self, payload: Dict[str, Any]) -> Optional[float]:
        value = self._finite_float(payload.get('initial_move_time_sec'))
        if value is None:
            return None
        for option in INITIAL_MOVE_TIME_OPTIONS_SEC:
            if math.isclose(value, option, rel_tol=0.0, abs_tol=1e-6):
                return option
        allowed = ', '.join(f'{option:g}' for option in INITIAL_MOVE_TIME_OPTIONS_SEC)
        raise ValueError(f'initial_move_time_sec must be one of: {allowed}')

    def _load_motion_records(self, path: Path) -> List[Dict[str, Any]]:
        content = path.read_text(encoding='utf-8')
        rows, headers = self._extract_motion_rows(content)
        records = []
        for index, row in enumerate(rows):
            record = self._parse_motion_row(row, headers)
            if record is None:
                continue
            record['row_index'] = index
            records.append(record)
        if not records:
            raise ValueError('motion file has no valid records')
        return sorted(records, key=lambda item: (item['time_sec'], str(item['motion_id']), item['row_index']))

    def _extract_motion_rows(self, content: str) -> tuple[List[Any], List[str]]:
        try:
            payload = json.loads(content)
        except json.JSONDecodeError:
            return self._extract_motion_rows_from_text(content)

        if isinstance(payload, dict):
            headers = payload.get('header', payload.get('headers', payload.get('columns', [])))
            headers = [str(item) for item in headers] if isinstance(headers, list) else []
            for key in ('data', 'rows', 'records', 'motion_data', 'motions', 'frames', 'values'):
                value = payload.get(key)
                if isinstance(value, list):
                    if value and isinstance(value[0], list) and self._header_has_required(value[0]):
                        return self._expand_pair_rows(value[1:]), [str(item) for item in value[0]]
                    return self._expand_pair_rows(value), headers
            return [payload], headers
        if isinstance(payload, list):
            if payload and isinstance(payload[0], list) and self._header_has_required(payload[0]):
                return self._expand_pair_rows(payload[1:]), [str(item) for item in payload[0]]
            return self._expand_pair_rows(payload), []
        return [], []

    def _extract_motion_rows_from_text(self, content: str) -> tuple[List[Any], List[str]]:
        lines = [
            line.strip()
            for line in content.splitlines()
            if line.strip() and not line.strip().startswith('#')
        ]
        if not lines:
            return [], []

        headers = self._parse_header_line(lines[0])
        rows = []
        for line in lines[1:]:
            item = self._parse_text_row(line)
            if item is not None:
                rows.append(item)
        return self._expand_pair_rows(rows), headers

    def _parse_motion_row(
        self,
        row: Any,
        headers: List[str],
    ) -> Optional[Dict[str, Any]]:
        if isinstance(row, dict):
            frame = self._column_value(row, 'frame')
            time_sec = self._column_value(row, 'time')
            motion_id = self._column_value(row, 'motion_id')
            value = self._column_value(row, 'value')
        elif isinstance(row, list):
            mapping = self._header_map(headers)
            if not mapping and len(row) >= 4:
                mapping = {'frame': 0, 'time': 1, 'motion_id': 2, 'value': 3}
            try:
                frame = row[mapping['frame']]
                time_sec = row[mapping['time']]
                motion_id = row[mapping['motion_id']]
                value = row[mapping['value']]
            except (KeyError, IndexError):
                return None
        else:
            return None

        frame_value = self._finite_float(frame)
        time_value = self._finite_float(time_sec)
        value_number = self._finite_float(value)
        if frame_value is None or time_value is None or value_number is None:
            return None
        motion_text = str(motion_id).strip() if motion_id is not None else ''
        if not motion_text:
            return None
        return {
            'frame': int(round(frame_value)),
            'time_sec': float(time_value),
            'motion_id': motion_text,
            'value': float(value_number),
        }

    def _parse_header_line(self, line: str) -> List[str]:
        text = line.strip().strip('\ufeff').rstrip(',').strip()
        if text.startswith('{') and text.endswith('}'):
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                try:
                    data = ast.literal_eval(text)
                except (ValueError, SyntaxError):
                    data = {}
            fields = data.get('fields') if isinstance(data, dict) else None
            if isinstance(fields, list):
                return [str(item).strip() for item in fields]
        parsed = self._parse_text_row(text)
        if isinstance(parsed, list):
            return [str(item).strip() for item in parsed]
        return ['frame', 'time(sec)', 'id', 'value']

    def _parse_text_row(self, line: str) -> Optional[List[Any]]:
        text = line.strip().rstrip(',').strip()
        if not text:
            return None
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            try:
                parsed = ast.literal_eval(text)
            except (ValueError, SyntaxError):
                parsed = None
        if isinstance(parsed, list):
            return parsed
        return None

    def _expand_pair_rows(self, rows: List[Any]) -> List[Any]:
        expanded = []
        changed = False
        for row in rows:
            if not isinstance(row, list) or len(row) <= 4:
                expanded.append(row)
                continue
            if (len(row) - 2) % 2 != 0:
                expanded.append(row)
                continue
            frame = row[0]
            time_sec = row[1]
            for index in range(2, len(row), 2):
                expanded.append([frame, time_sec, row[index], row[index + 1]])
            changed = True
        return expanded if changed else rows

    def _motion_groups(
        self,
        records: List[Dict[str, Any]],
    ) -> Dict[str, List[Dict[str, Any]]]:
        groups: Dict[str, List[Dict[str, Any]]] = {}
        for record in records:
            groups.setdefault(str(record['motion_id']), []).append(record)
        for key in list(groups):
            groups[key] = sorted(groups[key], key=lambda item: item['time_sec'])
        return groups

    def _interpolated_value(self, records: List[Dict[str, Any]], time_sec: float) -> float:
        if not records:
            return 0.0
        if time_sec <= records[0]['time_sec']:
            return float(records[0]['value'])
        if time_sec >= records[-1]['time_sec']:
            return float(records[-1]['value'])
        for index in range(1, len(records)):
            before = records[index - 1]
            after = records[index]
            if time_sec <= after['time_sec']:
                span = max(float(after['time_sec'] - before['time_sec']), 1e-9)
                ratio = (time_sec - before['time_sec']) / span
                return float(before['value']) + ((float(after['value']) - float(before['value'])) * ratio)
        return float(records[-1]['value'])

    def _project_asset_dirs(self, payload: Dict[str, Any]) -> tuple[str, Path, Path]:
        project_id = str(payload.get('project_id') or '').strip()
        if (
            not project_id
            or project_id != Path(project_id).name
            or project_id.startswith('.')
            or '/' in project_id
            or '\\' in project_id
        ):
            raise ValueError('유효한 통합 프로젝트 ID가 필요합니다')
        root = self.motion_projects_dir.resolve()
        project_dir = (root / project_id).resolve()
        if project_dir.parent != root or not (project_dir / 'project.json').is_file():
            raise ValueError(f'통합 프로젝트를 찾을 수 없습니다: {project_id}')
        return project_id, project_dir / 'motions', project_dir / 'motion_axis_matching'

    def _mapping_file_path(self, file_id: Any, directory: Optional[Path] = None) -> Path:
        name = str(file_id or '').strip()
        if not name:
            raise ValueError('mapping file_id is required')
        if name != Path(name).name or '/' in name or '\\' in name:
            raise ValueError('invalid mapping file id')
        if not name.lower().endswith(('.yaml', '.yml')):
            name = f'{name}.yaml'
        path = (directory or self.mappings_dir) / name
        if not path.is_file():
            raise ValueError(f'motion mapping not found: {name}')
        return path

    def _motion_file_path(self, file_id: Any, directory: Optional[Path] = None) -> Path:
        name = str(file_id or '').strip()
        if not name:
            raise ValueError('motion file_id is required')
        if name != Path(name).name or '/' in name or '\\' in name:
            raise ValueError('invalid motion file id')
        path = (directory or self.motion_files_dir) / name
        if not path.is_file():
            raise ValueError(f'motion file not found: {name}')
        return path

    def _status_from_plan(self, state: str, message: str, plan: Dict[str, Any]) -> Dict[str, Any]:
        return {
            **self._empty_status(),
            'state': state,
            'message': message,
            'project_id': plan.get('project_id', ''),
            'motion_file_id': plan.get('motion_file_id', ''),
            'mapping_file_id': plan.get('mapping_file_id', ''),
            'run_mode': plan.get('run_mode', 'once'),
            'automation_run': bool(plan.get('automation_run')),
            'repeat_mode': plan.get('repeat_mode', 'direct'),
            'dwell_sec': float(plan.get('dwell_sec') or 0.0),
            'countdown_sec': float(plan.get('countdown_sec') or 0.0),
            'operation_generation': int(plan.get('operation_generation') or 0),
            'request_source': plan.get('request_source', 'motion_run'),
            'cycle_count': 0,
            'current_cycle': 0,
            'summary': plan.get('summary', {}),
            'warnings': plan.get('warnings', []),
            'capabilities': plan.get('capabilities', {}),
            'axes': [
                {
                    'motion_id': axis['motion_id'],
                    'motor_axis': axis['motor_axis'],
                    'motor_type': axis['motor_type'],
                    'initial_motion_source_position_deg': axis['initial_motion_source_position_deg'],
                    'initial_motion_position_deg': axis['initial_motion_position_deg'],
                    'initial_motor_target_deg': axis['initial_motor_target_deg'],
                    'motion_limit_lower_deg': axis['motion_limit_lower_deg'],
                    'motion_limit_upper_deg': axis['motion_limit_upper_deg'],
                    'source_motion_min_deg': axis['source_motion_min_deg'],
                    'source_motion_max_deg': axis['source_motion_max_deg'],
                    'command_motion_min_deg': axis['command_motion_min_deg'],
                    'command_motion_max_deg': axis['command_motion_max_deg'],
                    'motion_clamped': axis['motion_clamped'],
                    'target_min_deg': axis['target_min_deg'],
                    'target_max_deg': axis['target_max_deg'],
                    'loop_start_motion_deg': axis['loop_start_motion_deg'],
                    'loop_end_motion_deg': axis['loop_end_motion_deg'],
                    'loop_start_target_deg': axis['loop_start_target_deg'],
                    'loop_end_target_deg': axis['loop_end_target_deg'],
                    'loop_delta_deg': axis['loop_delta_deg'],
                    'loop_motor_delta_deg': axis['loop_motor_delta_deg'],
                    'loop_tolerance_deg': axis['loop_tolerance_deg'],
                }
                for axis in plan.get('axes', [])
            ],
            'updated_at': time.time(),
        }

    def _empty_status(self) -> Dict[str, Any]:
        return {
            'state': 'idle',
            'message': 'motion run idle',
            'project_id': '',
            'motion_file_id': '',
            'mapping_file_id': '',
            'run_mode': 'once',
            'automation_run': False,
            'repeat_mode': 'direct',
            'dwell_sec': 0.0,
            'countdown_sec': 0.0,
            'operation_generation': 0,
            'request_source': 'motion_run',
            'cycle_count': 0,
            'current_cycle': 0,
            'summary': {},
            'warnings': [],
            'capabilities': {},
            'axes': [],
            'phase': 'idle',
            'phase_started_at': None,
            'phase_finished_at': None,
            'lifecycle': {
                'checked_at': None,
                'initial_started_at': None,
                'initial_finished_at': None,
                'motion_started_at': None,
                'motion_finished_at': None,
            },
            'progress': {
                'elapsed_sec': 0.0,
                'duration_sec': 0.0,
                'ratio': 0.0,
                'sample_index': 0,
                'active_axis_count': 0,
            },
            'updated_at': time.time(),
        }

    def _set_status(self, status: Dict[str, Any]) -> None:
        with self._run_lock:
            self._status = status
        self._publish_status()

    def _update_status(self, values: Dict[str, Any]) -> None:
        with self._run_lock:
            self._status = {
                **self._status,
                **values,
                'updated_at': time.time(),
            }
        self._publish_status()

    def _current_lifecycle(self) -> Dict[str, Any]:
        with self._run_lock:
            lifecycle = self._status.get('lifecycle', {})
        return dict(lifecycle) if isinstance(lifecycle, dict) else {}

    def _update_progress(
        self,
        state: str,
        elapsed_sec: float,
        duration_sec: float,
        sample_index: int,
        active_axis_count: int,
        run_mode: Optional[str] = None,
        cycle_count: Optional[int] = None,
        current_cycle: Optional[int] = None,
    ) -> None:
        duration = max(float(duration_sec), 1e-9)
        with self._run_lock:
            self._status = {
                **self._status,
                'state': state,
                'progress': {
                    'elapsed_sec': float(elapsed_sec),
                    'duration_sec': float(duration_sec),
                    'ratio': min(max(float(elapsed_sec) / duration, 0.0), 1.0),
                    'sample_index': int(sample_index),
                    'active_axis_count': int(active_axis_count),
                },
                'updated_at': time.time(),
            }
            if run_mode is not None:
                self._status['run_mode'] = run_mode
            if cycle_count is not None:
                self._status['cycle_count'] = int(cycle_count)
            if current_cycle is not None:
                self._status['current_cycle'] = int(current_cycle)

    def status(self) -> Dict[str, Any]:
        with self._run_lock:
            result = json.loads(json.dumps(self._status, ensure_ascii=False))
            result['execution_context'] = {
                **self._execution_context,
                'ready': self._execution_context_ready,
            }
            result['automation'] = self._automation_snapshot()
            return result

    def _publish_response(self, payload: Dict[str, Any]) -> None:
        msg = String()
        msg.data = json.dumps(payload, ensure_ascii=False)
        self._response_pub.publish(msg)

    def _publish_status(self) -> None:
        msg = String()
        msg.data = json.dumps(self.status(), ensure_ascii=False)
        self._status_pub.publish(msg)

    def _load_period_sec(self) -> float:
        period = self._finite_float(
            self.declare_parameter('command_period_sec', DEFAULT_PERIOD_SEC).value
        )
        if period is None or period <= 0:
            return DEFAULT_PERIOD_SEC
        return max(period, 0.001)

    def _sleep_until(self, deadline: float) -> None:
        delay = deadline - time.monotonic()
        if delay > 0.0:
            time.sleep(delay)

    @staticmethod
    def _smoothstep(value: float) -> float:
        clamped = min(max(float(value), 0.0), 1.0)
        return (clamped * clamped) * (3.0 - (2.0 * clamped))

    @staticmethod
    def _duplicate_axis_text(axes: List[Dict[str, Any]]) -> str:
        counts: Dict[int, int] = {}
        for axis in axes:
            key = int(axis['motor_axis'])
            counts[key] = counts.get(key, 0) + 1
        duplicates = [str(axis) for axis, count in counts.items() if count > 1]
        return ', '.join(duplicates)

    @staticmethod
    def _column_value(row: Dict[str, Any], target: str) -> Any:
        for key, value in row.items():
            if MotionRunManager._column_key(str(key)) == target:
                return value
        return None

    @staticmethod
    def _header_map(headers: List[str]) -> Dict[str, int]:
        mapping: Dict[str, int] = {}
        for index, header in enumerate(headers):
            key = MotionRunManager._column_key(str(header))
            if key in ('frame', 'time', 'motion_id', 'value') and key not in mapping:
                mapping[key] = index
        return mapping if all(key in mapping for key in ('frame', 'time', 'motion_id', 'value')) else {}

    @staticmethod
    def _header_has_required(headers: List[Any]) -> bool:
        return bool(MotionRunManager._header_map([str(item) for item in headers]))

    @staticmethod
    def _column_key(label: str) -> str:
        compact = ''.join(char for char in label.lower() if char.isalnum())
        if compact in ('frame', 'frameid', 'frameindex'):
            return 'frame'
        if compact in ('time', 'times', 'timesec', 'seconds', 'sec', 'timestamp'):
            return 'time'
        if compact in ('motionid', 'motion', 'id', 'jointid', 'channelid'):
            return 'motion_id'
        if compact in ('value', 'angle', 'angledeg', 'deg', 'position', 'positiondeg'):
            return 'value'
        return compact

    @staticmethod
    def _optional_int(value: Any) -> Optional[int]:
        if value is None or value == '':
            return None
        try:
            return int(str(value), 0)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _finite_float(value: Any) -> Optional[float]:
        if value is None or value == '':
            return None
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if math.isfinite(number) else None


def main(args=None) -> None:
    rclpy.init(args=args)
    node = MotionRunManager()
    try:
        rclpy.spin(node)
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()
