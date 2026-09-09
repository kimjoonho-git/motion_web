"""Validate and execute motion plans independently from the web API process."""

import hashlib
import json
import math
import os
import threading
import time
import traceback
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

import rclpy
import yaml
from motion_common import command_router, generation as generation_mod, motion_table, topics
from motion_common.values import finite_float, optional_int
from motion_control_msgs.msg import MotorStatus
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
from std_msgs.msg import Int8MultiArray, String

from . import motion_run_rules
from .group_session import GroupSession
from .motion_automation_store import (
    MotionAutomationStore,
    REPEAT_MODES,
    default_automation_state,
    normalize_automation_state,
)
from .motion_group_display import apply_group_display
from motion_common.timing import CONTROL_PERIOD_SEC


ID_CONTROLWORD = 0
ID_TARGET_POSITION = 1
CW_ENABLE_OPERATION_MINAS = 0x000F
CW_NEW_SET_POINT_MINAS = 0x003F
DYNAMIXEL_TORQUE_ENABLE = 1
DEFAULT_MOTION_PROJECTS_DIR = (
    Path(os.environ.get('MOTION_WORKSPACE', Path.cwd())).expanduser()
    / 'motion_projects'
)
DEFAULT_PERIOD_SEC = CONTROL_PERIOD_SEC
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
            self.declare_parameter('motion_state_topic', topics.MOTION_STATE).value
        )
        # 이 노드의 출력은 최종 하드웨어 명령이 아니라 supervisor로 보내는 합산 요청이다.
        # motion_supervisor의 동명 파라미터(motor_command_topic)는 최종 출력 토픽이므로
        # 이름을 분리해 launch 재정의 시 오배선을 막는다.
        self.motion_run_command_topic = str(
            self.declare_parameter(
                'motion_run_command_topic',
                topics.MOTION_RUN_COMMAND,
            ).value
        )
        self.request_topic = str(
            self.declare_parameter(
                'request_topic',
                topics.MOTION_RUN_REQUEST,
            ).value
        )
        self.response_topic = str(
            self.declare_parameter(
                'response_topic',
                topics.MOTION_RUN_RESPONSE,
            ).value
        )
        self.status_topic = str(
            self.declare_parameter(
                'status_topic',
                topics.MOTION_RUN_STATUS,
            ).value
        )
        self.motion_value_topic = str(
            self.declare_parameter(
                'motion_value_topic',
                topics.MOTION_VALUE_STATE,
            ).value
        )
        self.safety_status_topic = str(
            self.declare_parameter(
                'safety_status_topic',
                topics.SAFETY_STATUS,
            ).value
        )
        self.action_request_topic = str(
            self.declare_parameter(
                'action_request_topic',
                topics.MANUAL_ACTION_REQUEST,
            ).value
        )
        self.action_result_topic = str(
            self.declare_parameter(
                'action_result_topic',
                topics.MANUAL_ACTION_RESULT,
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
        # 그룹 세션은 별도 객체가 갖는다 · 조건변수는 실행 락 위에 선다 (§6-29)
        self._group = GroupSession(self, run_lock=self._run_lock)
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
        self._status: Dict[str, Any] = motion_run_rules._empty_status()
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
        self._command_pub = self.create_publisher(MotorStatus, self.motion_run_command_topic, qos)
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
        self._automation_timer = None

        self.get_logger().info(
            f'motion_run_manager started: state={self.motion_state_topic}, '
            f'command={self.motion_run_command_topic}, request={self.request_topic}, '
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
                    last_stamp = finite_float(values[-1].get('stamp')) or now
                if now - last_stamp > 60.0:
                    self._action_results.pop(key, None)

    #: 실행 컨텍스트가 서 있어야 처리하는 명령
    COMMANDS_REQUIRING_CONTEXT = frozenset({
        'automation_configure',
        'automation_start',
        'automation_reserve',
        'check',
        'initialize',
        'start',
        'group_prepare',
        'group_start_at',
        'group_initialize_at',
    })

    def _command_router(self) -> command_router.CommandRouter:
        """처리기 표 · 처음 쓸 때 만든다.

        노드를 띄우지 않고 (`__new__`) 콜백만 검증하는 테스트에서도 동작하도록
        `__init__`에 의존하지 않는다.
        """
        router = getattr(self, '_router', None)
        if router is None:
            router = self._build_router()
            self._router = router
        return router

    def _build_router(self) -> command_router.CommandRouter:
        """명령 → 처리기 표 · 처리기는 payload를 받아 응답 dict를 돌려준다."""
        router = command_router.CommandRouter(context_commands=self.CONTEXT_COMMANDS)
        router.register('apply_context', self._apply_execution_context)
        router.register('confirm_context', self._confirm_execution_context)
        router.register('invalidate_context', self._invalidate_execution_context)
        router.register('status', lambda payload: {
            'success': True, 'message': 'motion run status', 'status': self.status(),
        })
        router.register('automation_configure', self._configure_automation)
        router.register('automation_start', self._start_automation)
        router.register('automation_reserve', self._reserve_automation)
        router.register('automation_disable', self._disable_automation)
        router.register('check', self._handle_check)
        router.register('initialize', lambda payload: self._start_thread('initialize', payload))
        router.register('start', lambda payload: self._start_thread('run', payload))
        router.register('group_prepare', self._group.prepare)
        router.register('group_start_at', self._group.schedule_cycle)
        router.register('group_initialize_at', self._group.schedule_initialization)
        router.register('group_cancel', self._group.cancel)
        router.register('stop', lambda payload: self._handle_stop())
        router.register('stop_after_cycle', lambda payload: self._handle_stop_after_cycle())
        return router

    def _invalidate_execution_context(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """실행 컨텍스트와 자동화 상태를 버린다 · 동작 중에는 거부한다."""
        with self._run_lock:
            if self._run_thread is not None and self._run_thread.is_alive():
                raise ValueError('모션 동작 중에는 프로젝트 메모리를 폐기할 수 없습니다')
            self._execution_context = {}
            self._execution_context_ready = False
            self.motion_files_dir = self.motion_projects_dir
            self.mappings_dir = self.motion_projects_dir
            self._status = motion_run_rules._empty_status()
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
        return {
            'success': True,
            'message': '모션 실행 프로젝트 메모리 폐기',
            'project_id': '',
            'context_id': '',
            'status': self.status(),
        }

    def _request_callback(self, msg: String) -> None:
        request = command_router.parse_request(msg.data)
        if request is None:
            self.get_logger().warn('invalid motion run request JSON')
            return

        command = request.command
        payload = request.payload

        try:
            self._validate_request_generation(command, request.generation, payload)
            handler = self._command_router().resolve(command)
            if handler is None:
                response = command_router.error_response(
                    f'unknown motion run command: {command}'
                )
            else:
                if command in self.COMMANDS_REQUIRING_CONTEXT:
                    self._require_execution_context(payload)
                response = handler(payload)
        except Exception as exc:  # Defensive boundary for the web bridge.
            self.get_logger().error(
                f'motion run command failed: {command}\n{traceback.format_exc()}'
            )
            response = command_router.error_response(
                f'motion run command failed: {exc}'
            )

        self._publish_response(command_router.finalize(response, request))
        self._publish_status()

    #: 실행 컨텍스트를 새로 세우는 명령 · 이때만 세대가 오를 수 있다
    CONTEXT_COMMANDS = frozenset({'apply_context', 'invalidate_context'})

    def _validate_request_generation(
        self, command: str, request_generation: Any, payload: Dict[str, Any]
    ) -> int:
        advancing = command in self.CONTEXT_COMMANDS
        value = generation_mod.validate_request_generation(
            request_generation,
            payload,
            current_generation=getattr(self, '_project_generation', 0),
            advances_context=advancing,
        )
        if advancing:
            self._project_generation = value
        return value

    def _apply_execution_context(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        project_id, _, mappings_dir = self._project_asset_dirs(payload)
        context_id = str(payload.get('context_id') or '').strip()
        mapping_file_id = str(payload.get('mapping_file_id') or '').strip()
        mapping_sha256 = ''
        if not context_id or not mapping_file_id:
            raise ValueError('실행 컨텍스트 ID와 모션축 설정 버전이 필요합니다')
        mapping_path = self._mapping_file_path(mapping_file_id, mappings_dir)
        actual_sha = ''
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
        actual_sha = ''
        if False:
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
            context = dict(self._execution_context)

        enabled = bool(payload.get('enabled', current.get('enabled', False)))
        if not enabled:
            return self._disable_automation(payload)

        repeat_mode = str(
            payload.get('repeat_mode') or current.get('repeat_mode') or 'reinitialize'
        ).strip()
        try:
            dwell_sec = float(payload.get('dwell_sec', current.get('dwell_sec', 0.0)))
        except (TypeError, ValueError):
            dwell_sec = 0.0

        motion_file_id = str(
            payload.get('motion_file_id')
            or current.get('motion_file_id')
            or ''
        ).strip()
        mapping_file_id = str(
            payload.get('mapping_file_id')
            or current.get('mapping_file_id')
            or ''
        ).strip()

        motion_sha = ''
        mapping_sha = ''
        armed = False

        if motion_file_id and mapping_file_id:
            armed = True

        candidate = normalize_automation_state({
            **current,
            'enabled': True,
            'armed': armed,
            'repeat_mode': repeat_mode,
            'dwell_sec': dwell_sec,
            'motion_file_id': motion_file_id if armed else '',
            'mapping_file_id': mapping_file_id if armed else '',
            'motion_sha256': motion_sha,
            'mapping_sha256': mapping_sha,
            'last_error': '',
        })

        saved = self._save_automation(
            candidate,
            runtime_state='ready' if armed else 'blocked',
            runtime_message=(
                '부팅 시 자동 재생 예약 완료'
                if armed
                else '재생 등록 모션 및 매핑 파일이 필요합니다'
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
                'motion_sha256': '',
                'mapping_sha256': '',
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

    def _reserve_automation(self, payload: Dict[str, Any]) -> Dict[str, Any]:
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
            runtime_state='ready',
            runtime_message='부팅 시 자동 반복이 예약되었습니다',
        )
        return {
            'success': True,
            'message': '부팅 시 자동 시작 예약 완료',
            'automation': self._automation_snapshot(),
            'status': self.status(),
        }

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
            resume_started = getattr(self, '_automation_resume_started_at', None)
            timeout_sec = getattr(self, 'automation_startup_timeout_sec', 120.0)
            if (
                getattr(self, '_automation_resume_pending', False)
                and resume_started is not None
                and time.monotonic() - resume_started > timeout_sec
            ):
                self._automation_resume_pending = False
                if hasattr(self, '_automation_runtime') and isinstance(self._automation_runtime, dict):
                    self._automation_runtime.update({
                        'state': 'blocked',
                        'message': '자동 모션 복구 준비 시간 초과',
                        'resume_pending': False,
                    })
            return {
                **getattr(self, '_automation_state', {}),
                **getattr(self, '_automation_runtime', {}),
                'project_id': getattr(self, '_automation_project_id', ''),
            }



    def _handle_check(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        try:
            plan = self._build_plan(payload)
        except Exception as exc:
            reason = str(exc) or '실행 준비 검사 실패'
            status = motion_run_rules._empty_status()
            status.update({
                'state': 'error',
                'phase': 'error',
                'message': f'실행 준비 검사 실패: {reason}',
                'project_id': str(payload.get('project_id') or ''),
                'motion_file_id': str(payload.get('motion_file_id') or ''),
                'mapping_file_id': str(payload.get('mapping_file_id') or ''),
                'capabilities': motion_run_rules._unavailable_capabilities(reason),
                'updated_at': time.time(),
            })
            self._set_status(status)
            return {
                'success': False,
                'message': reason,
                'status': self.status(),
                'summary': {},
            }
        status = motion_run_rules._status_from_plan('ready', '실행 준비 검사 완료', plan)
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
            motors_snapshot = self._current_motors()
            if not motors_snapshot:
                raise ValueError('current motion_state is unavailable')
            self._stop_event.clear()
            self._graceful_stop_event.clear()
            self._automation_resume_pending = False
            if hasattr(self, '_automation_runtime') and isinstance(self._automation_runtime, dict):
                self._automation_runtime['resume_pending'] = False
            preparing_status = motion_run_rules._empty_status()
            preparing_status.update({
                'state': 'preparing',
                'phase': 'preparing',
                'message': (
                    '초기 위치 이동 계획 생성 중'
                    if mode == 'initialize'
                    else '모션 실행 계획 생성 중'
                ),
                'project_id': str(payload.get('project_id') or ''),
                'motion_file_id': str(payload.get('motion_file_id') or ''),
                'mapping_file_id': str(payload.get('mapping_file_id') or ''),
                'run_mode': str(payload.get('run_mode') or 'once'),
                'automation_run': bool(payload.get('automation_run')),
                'operation_generation': int(
                    payload.get('operation_generation') or 0
                ),
                'request_source': str(
                    payload.get('request_source') or 'motion_run'
                ),
                'phase_started_at': time.time(),
            })
            self._set_status(preparing_status)
            self._run_thread = threading.Thread(
                target=self._prepare_and_run,
                args=(mode, dict(payload), list(motors_snapshot)),
                daemon=True,
            )
            self._run_thread.start()

        return {
            'success': True,
            'message': (
                'initial position move preparation started'
                if mode == 'initialize'
                else 'motion run preparation started'
            ),
            'status': self.status(),
            'summary': {},
        }

    def _wait_group_deadline(
        self,
        scheduled_monotonic: float,
        *,
        phase: str,
        message: str,
        execution_id: str,
        cycle_number: int = 0,
    ) -> None:
        remaining = float(scheduled_monotonic) - time.monotonic()
        if remaining < -self.period_sec:
            raise RuntimeError('그룹 예약 시작시각을 놓쳤습니다')
        deadline = float(scheduled_monotonic)
        self._update_status({
            'state': 'waiting',
            'phase': phase,
            'message': message,
            'group_execution': True,
            'execution_id': execution_id,
            'current_cycle': cycle_number,
            'group_cycle_number': cycle_number,
            'scheduled_start_monotonic': float(scheduled_monotonic),
        })
        while time.monotonic() < deadline:
            if self._stop_event.is_set():
                raise InterruptedError()
            time.sleep(min(0.02, deadline - time.monotonic()))

    def _wait_for_automation_ready(self, payload: Dict[str, Any], timeout_sec: float = 60.0) -> None:
        deadline = time.monotonic() + timeout_sec
        last_error = ''
        while True:
            if self._stop_event.is_set():
                raise InterruptedError()
            error = self._playback_ownership_error()
            if not error:
                motors = self._current_motors()
                if not motors:
                    error = '모터 통신 대기 중'
                else:
                    try:
                        plan = self._build_plan(payload, motors_snapshot=motors)
                        init_plan = self._build_plan(
                            payload,
                            initialization_only=True,
                            motors_snapshot=motors,
                        )
                        axes_to_check = {int(axis['motor_axis']) for axis in plan.get('axes', [])}
                        axes_to_check.update(int(axis['motor_axis']) for axis in init_plan.get('axes', []))
                        
                        for motor_axis in sorted(axes_to_check):
                            motor = self._motor_for_axis(motor_axis, motors)
                            motor_error = motion_run_rules._motor_ready_error(
                                motor or {'controller_index': motor_axis}
                            )
                            if motor_error:
                                error = motor_error
                                break
                            if motion_run_rules._motor_position_deg(motor) is None:
                                error = f'Axis {motor_axis} position is unavailable'
                                break
                    except ValueError as exc:
                        error = str(exc)
            if not error:
                return
            if time.monotonic() > deadline:
                raise ValueError(f"자동 반복 준비 대기 시간 초과: {error}")
            if error != last_error:
                last_error = error
                with self._run_lock:
                    self._automation_runtime['message'] = f"자동 반복 준비 대기 중: {error}"
                self._publish_status()
            time.sleep(0.5)

    def _prepare_and_run(
        self,
        mode: str,
        payload: Dict[str, Any],
        motors_snapshot: List[Dict[str, Any]],
    ) -> None:
        try:
            if bool(payload.get('automation_run')):
                self._wait_for_automation_ready(payload)
                motors_snapshot = self._current_motors()
                with self._run_lock:
                    self._automation_runtime['resume_pending'] = False

            if mode == 'initialize':
                plan = self._build_plan(
                    payload,
                    initialization_only=True,
                    motors_snapshot=motors_snapshot,
                )
                if self._stop_event.is_set():
                    return
                self._run_initialization(plan)
                return

            plan = self._build_plan(
                payload,
                motors_snapshot=motors_snapshot,
            )
            initialization_plan = self._build_plan(
                payload,
                initialization_only=True,
                motors_snapshot=motors_snapshot,
            )
            if self._stop_event.is_set():
                return
            ownership_error = self._playback_ownership_error()
            if ownership_error:
                raise ValueError(ownership_error)
            guard_error = motion_run_rules._motion_auto_start_guard_error(plan)
            if guard_error:
                raise ValueError(guard_error)
            self._run_initialization_then_motion(initialization_plan, plan)
        except InterruptedError:
            return
        except Exception as exc:
            if self._stop_event.is_set():
                return
            self.get_logger().error(
                f'motion run preparation failed: {mode}\n{traceback.format_exc()}'
            )
            status = motion_run_rules._empty_status()
            status.update({
                'state': 'error',
                'phase': 'error',
                'message': f'모션 실행 준비 실패: {exc}',
                'project_id': str(payload.get('project_id') or ''),
                'motion_file_id': str(payload.get('motion_file_id') or ''),
                'mapping_file_id': str(payload.get('mapping_file_id') or ''),
                'run_mode': str(payload.get('run_mode') or 'once'),
                'automation_run': bool(payload.get('automation_run')),
                'operation_generation': int(
                    payload.get('operation_generation') or 0
                ),
                'request_source': str(
                    payload.get('request_source') or 'motion_run'
                ),
                'phase_finished_at': time.time(),
            })
            self._set_status(status)
            if bool(payload.get('automation_run')):
                self._automation_failure(str(exc))

    def _handle_stop(self) -> Dict[str, Any]:
        current = self.status()
        if current.get('automation_run') or current.get('automation', {}).get('armed'):
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
        self._group.mark_stopping()
        if current.get('state') == 'preparing':
            self._update_status({
                'state': 'stopped',
                'phase': 'stopped',
                'phase_finished_at': time.time(),
                'message': 'stop requested during plan preparation',
            })
        elif current.get('state') in (
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

    def _handle_stop_after_cycle(self) -> Dict[str, Any]:
        current = self.status()
        if current.get('group_execution'):
            if not self._group.request_stop_after_cycle(current):
                return {
                    'success': False,
                    'message': '활성 그룹 실행이 없습니다',
                    'status': current,
                }
            return {
                'success': True,
                'message': '현재 그룹 모션 회차 완료 후 정지 요청',
                'status': self.status(),
            }
        is_sync_repeat = bool(int(current.get('synchronized_repeat_count') or 0))
        is_continuous = current.get('run_mode') == 'continuous'
        is_automation = bool(current.get('automation_run'))

        if not (is_sync_repeat or is_continuous or is_automation):
            return {
                'success': False,
                'message': '반복 모션 실행 중일 때만 현재 회차 후 정지를 사용할 수 있습니다',
                'status': current,
            }
        self._graceful_stop_event.set()
        return {
            'success': True,
            'message': '현재 동기 반복 회차 완료 후 정지 요청',
            'status': self.status(),
        }

    def _group_cycle_context(self, plan: Mapping[str, Any]) -> Dict[str, int]:
        current = self.status()
        cycle = int(
            plan.get('group_cycle_number')
            or current.get('group_cycle_number')
            or current.get('current_cycle')
            or 0
        )
        if cycle <= 0:
            return {}
        return {
            'group_execution': True,
            'execution_id': str(
                plan.get('execution_id') or current.get('execution_id') or ''
            ),
            'group_cycle_number': cycle,
            'current_cycle': cycle,
        }

    def _run_initialization(self, plan: Dict[str, Any]) -> None:
        try:
            if self._stop_event.is_set():
                raise InterruptedError()
            group_cycle_context = self._group_cycle_context(plan)
            init_axes = list(plan['axes'])
            if not init_axes:
                now = time.time()
                status = motion_run_rules._status_from_plan('initialized', '초기 위치 이동 대상이 없습니다', plan)
                status['phase'] = 'initialized'
                status.update(group_cycle_context)
                status['phase_started_at'] = now
                status['phase_finished_at'] = now
                status['lifecycle'] = {
                    **self._current_lifecycle(),
                    'initial_started_at': now,
                    'initial_finished_at': now,
                }
                self._set_status(status)
                return

            motors = self._wait_for_current_motors()
            starts: Dict[int, float] = {}
            targets: Dict[int, float] = {}
            durations: Dict[int, float] = {}
            for axis in init_axes:
                motor_axis = int(axis['motor_axis'])
                motor = self._motor_for_axis(motor_axis, motors)
                motor_error = motion_run_rules._motor_ready_error(
                    motor or {'controller_index': motor_axis}
                )
                if motor_error:
                    raise RuntimeError(motor_error)
                current = motion_run_rules._motor_position_deg(motor)
                if current is None:
                    raise RuntimeError(f'Axis {motor_axis} current position is unavailable')
                starts[motor_axis] = current
                targets[motor_axis] = float(axis['initial_motor_target_deg'])
                durations[motor_axis] = max(float(axis.get('initial_move_time_sec') or 0.0), self.period_sec)

            max_duration = max(durations.values()) if durations else self.period_sec
            initial_started_at = time.time()
            status = motion_run_rules._status_from_plan('initializing', '초기 위치 이동 중', plan)
            status['phase'] = 'initializing'
            status.update(group_cycle_context)
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
            status = motion_run_rules._status_from_plan('initialized', '초기 위치 이동 완료', plan)
            status['phase'] = 'initialized'
            status.update(group_cycle_context)
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
            status = motion_run_rules._status_from_plan('stopped', '초기 위치 이동 정지', plan)
            status['phase'] = 'stopped'
            status['phase_finished_at'] = time.time()
            status['lifecycle'] = self._current_lifecycle()
            self._set_status(status)
        except Exception as exc:
            self.get_logger().error(f'initial position move failed\n{traceback.format_exc()}')
            status = motion_run_rules._status_from_plan('error', f'초기 위치 이동 실패: {exc}', plan)
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
        if motion_plan.get('repeat_mode') in {'reinitialize', 'dwell_reinitialize'}:
            self._run_motion(motion_plan, initialization_plan)
        else:
            self._run_motion(motion_plan)

    def _run_countdown(self, plan: Dict[str, Any]) -> bool:
        scheduled_at = float(plan.get('scheduled_start_at') or 0.0)
        duration = max(float(plan.get('countdown_sec') or 0.0), 0.0)
        if scheduled_at > 0.0:
            duration = max(scheduled_at - time.time(), 0.0)
            if duration <= 0.0:
                status = motion_run_rules._status_from_plan('error', '예약 시작 시각이 이미 지났습니다', plan)
                status['phase'] = 'error'
                self._set_status(status)
                return False
        if duration <= 0.0:
            return True
        started_at = time.time()
        deadline = time.monotonic() + duration
        status = motion_run_rules._status_from_plan('countdown', '모션 시작 대기', plan)
        status['phase'] = 'countdown'
        status['phase_started_at'] = started_at
        status['phase_finished_at'] = None
        status['lifecycle'] = self._current_lifecycle()
        self._set_status(status)
        while True:
            if self._stop_event.is_set():
                status = motion_run_rules._status_from_plan(
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
            motion_started_monotonic = time.monotonic()
            running_message = (
                '자동 반복 모션 실행 중'
                if automation_run
                else ('연속 모션 실행 중' if continuous else '모션 1회 실행 중')
            )
            status = motion_run_rules._status_from_plan('running', running_message, plan)
            status['phase'] = 'running'
            status['phase_started_at'] = motion_started_at
            status['phase_finished_at'] = None
            status['lifecycle'] = {
                **self._current_lifecycle(),
                'motion_started_at': motion_started_at,
                'motion_started_monotonic': motion_started_monotonic,
                'motion_finished_at': None,
            }
            requested_start_at = float(plan.get('scheduled_start_at') or 0.0)
            if requested_start_at:
                status['requested_start_at'] = requested_start_at
                status['actual_start_at'] = motion_started_at
                status['start_error_ms'] = round(
                    (motion_started_at - requested_start_at) * 1000.0, 3
                )
            if automation_run:
                with self._run_lock:
                    self._automation_runtime.update({
                        'state': 'running',
                        'message': running_message,
                    })
            playback_cycle = motion_run_rules._playback_cycle_number(plan, 0)
            if playback_cycle > 0:
                status['current_cycle'] = playback_cycle
            self._set_status(status)
            samples = plan['samples']
            cycle_count = 0
            grade1_seen = False
            while True:
                cycle_started = time.monotonic()
                for index, sample in enumerate(samples):
                    if self._stop_event.is_set():
                        status = motion_run_rules._status_from_plan('stopped', '연속 모션 정지' if continuous else '모션 실행 정지', plan)
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
                        current_cycle=motion_run_rules._playback_cycle_number(
                            plan, cycle_count,
                        ),
                    )
                    motion_run_rules._sleep_until(cycle_started + ((index + 1) * self.period_sec))
                cycle_count += 1
                synchronized_count = int(plan.get('synchronized_repeat_count') or 0)
                if synchronized_count:
                    if self._graceful_stop_event.is_set():
                        self._finish_cycle_stop(
                            plan, motion_started_at, cycle_count,
                            '현재 동기 반복 회차 완료 후 정지',
                        )
                        return
                    if cycle_count >= synchronized_count:
                        break
                    if not self._wait_synchronized_boundary(
                        plan, motors, samples, cycle_count
                    ):
                        return
                    continue
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
                target_cycle_count = int(plan['summary'].get('target_cycle_count') or 0)
                reached_target = target_cycle_count > 0 and cycle_count >= target_cycle_count
                if self._graceful_stop_event.is_set() or reached_target:
                    stop_message = '설정된 목표 회차 도달로 자동 정지' if reached_target and not self._graceful_stop_event.is_set() else '현재 모션 회차 완료 후 정지'
                    if automation_run:
                        self._finish_cycle_stop(
                            plan,
                            motion_started_at,
                            cycle_count,
                            stop_message,
                        )
                        return
                    else:
                        break
                if repeat_mode in {'dwell', 'dwell_reinitialize'} and dwell_sec > 0.0:
                    if not self._wait_between_cycles(
                        plan,
                        motion_started_at,
                        cycle_count,
                        dwell_sec,
                    ):
                        return
                if repeat_mode in {'reinitialize', 'dwell_reinitialize'}:
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
                    if self._graceful_stop_event.is_set():
                        self._finish_cycle_stop(
                            plan,
                            motion_started_at,
                            cycle_count,
                            '반복 초기위치 이동 완료 후 정지',
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
                status = motion_run_rules._status_from_plan('verifying', '모션 최종 위치 확인 중', plan)
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
            status = motion_run_rules._status_from_plan('completed', '모션 실행 완료', plan)
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
            status = motion_run_rules._status_from_plan('error', f'모션 실행 실패: {exc}', plan)
            status['phase'] = 'error'
            status['phase_finished_at'] = time.time()
            status['lifecycle'] = self._current_lifecycle()
            self._set_status(status)
            if bool(plan.get('automation_run')):
                self._automation_failure(str(exc))

    def _wait_synchronized_boundary(
        self, plan: Dict[str, Any], motors: List[Dict[str, Any]],
        samples: List[Dict[str, Any]], cycle_count: int,
    ) -> bool:
        """Hold the final target until an absolute cycle boundary."""
        first_start = float(plan.get('scheduled_start_at') or 0.0)
        cycle_sec = float(plan.get('synchronized_cycle_sec') or 0.0)
        deadline_wall = first_start + (cycle_count * cycle_sec)
        remaining = deadline_wall - time.time()
        if remaining < -self.period_sec:
            raise RuntimeError('동기 반복 시작 시각을 놓쳤습니다')
        deadline = time.monotonic() + max(remaining, 0.0)
        final_sample = samples[-1] if samples else {}
        self._update_status({
            'state': 'waiting', 'phase': 'waiting',
            'message': '다음 동기 반복 시작 대기',
            'next_start_at': deadline_wall,
        })
        while time.monotonic() < deadline:
            if self._stop_event.is_set():
                return False
            if self._graceful_stop_event.is_set():
                status = motion_run_rules._status_from_plan(
                    'stopped', '다음 동기 반복 시작 전 정지', plan
                )
                status['phase'] = 'stopped'
                status['cycle_count'] = cycle_count
                status['phase_finished_at'] = time.time()
                self._set_status(status)
                return False
            if final_sample and plan.get('hold_final_until_cycle'):
                self._publish_motion_setpoints(
                    motors, plan['axes'], final_sample['positions'],
                    final_sample.get('motion_values'),
                )
            motion_run_rules._sleep_until(min(time.monotonic() + self.period_sec, deadline))
        self._restore_running_status(plan, time.time(), cycle_count)
        return True

    def _finish_cycle_stop(
        self,
        plan: Dict[str, Any],
        motion_started_at: float,
        cycle_count: int,
        message: str,
        *,
        state: str = 'stopped',
    ) -> None:
        status = motion_run_rules._status_from_plan(state, message, plan)
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
        status = motion_run_rules._status_from_plan(
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
        status = motion_run_rules._status_from_plan('running', message, plan)
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
        has_ac_axes = motion_run_rules._has_ac_axes(axes)
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
                positions[motor_axis] = start + ((target - start) * motion_run_rules._smoothstep(ratio))

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
            motion_run_rules._sleep_until(start_time + ((step + 1) * tick_sec))

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
            motion_run_rules._sleep_until(time.monotonic() + max(float(clear_sec), 0.0))
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
        request_id = generation_mod.new_request_id(
            'motion-init', generation, f'{motor_axis}-{time.time_ns()}'
        )
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
            if not motion_run_rules._is_terminal_action_result(result):
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
                if terminal_only and not motion_run_rules._is_terminal_action_result(payload):
                    continue
                result = values.pop(index)
                if not values:
                    self._action_results.pop(request_id, None)
                return result
        return None

    def _build_plan(
        self,
        payload: Dict[str, Any],
        *,
        initialization_only: bool = False,
        motors_snapshot: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        run_mode = str(payload.get('run_mode') or 'once').strip().lower()
        if run_mode not in ('once', 'continuous'):
            raise ValueError('run_mode must be once or continuous')
        automation_run = bool(payload.get('automation_run', False))
        repeat_mode = str(payload.get('repeat_mode') or 'direct').strip().lower()
        if repeat_mode not in REPEAT_MODES:
            raise ValueError(f'지원하지 않는 자동 반복 방식입니다: {repeat_mode}')
        dwell_sec = finite_float(payload.get('dwell_sec'))
        dwell_sec = 0.0 if dwell_sec is None else dwell_sec
        if dwell_sec < 0.0:
            raise ValueError('자동 반복 대기 시간은 0초 이상이어야 합니다')
        countdown_sec = finite_float(payload.get('countdown_sec'))
        countdown_sec = 0.0 if countdown_sec is None else countdown_sec
        if countdown_sec < 0.0 or countdown_sec > 10.0:
            raise ValueError('모션 시작 대기 시간은 0초 이상 10초 이하여야 합니다')
        scheduled_start_at = finite_float(payload.get('scheduled_start_at'))
        scheduled_start_at = 0.0 if scheduled_start_at is None else scheduled_start_at
        synchronized_cycle_sec = finite_float(payload.get('synchronized_cycle_sec'))
        synchronized_cycle_sec = 0.0 if synchronized_cycle_sec is None else synchronized_cycle_sec
        try:
            synchronized_repeat_count = int(payload.get('synchronized_repeat_count') or 0)
        except (TypeError, ValueError) as exc:
            raise ValueError('동기 반복 횟수가 올바르지 않습니다') from exc
        try:
            target_cycle_count = int(payload.get('target_cycle_count') or 0)
        except (TypeError, ValueError) as exc:
            raise ValueError('목표 정지 회차가 올바르지 않습니다') from exc
        if target_cycle_count < 0:
            raise ValueError('목표 정지 회차는 0 이상이어야 합니다')
        if synchronized_repeat_count and (
            scheduled_start_at <= time.time() or synchronized_cycle_sec <= 0.0
            or not 1 <= synchronized_repeat_count <= 10000
        ):
            raise ValueError('동기 예약 시작 시각·주기·반복 횟수를 확인하세요')
        try:
            operation_generation = int(payload.get('operation_generation') or 0)
        except (TypeError, ValueError) as exc:
            raise ValueError('작업 세대 값이 올바르지 않습니다') from exc
        if operation_generation < 0:
            raise ValueError('작업 세대 값은 0 이상이어야 합니다')
        group_execution = bool(payload.get('group_execution'))
        if not automation_run and not group_execution and run_mode != 'continuous':
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
        initial_move_time_override = motion_run_rules._initial_move_time_override_sec(payload)
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
        motors = (
            list(motors_snapshot)
            if motors_snapshot is not None
            else self._current_motors()
        )
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

        groups = motion_run_rules._motion_groups(motion_records)
        if request_source != 'motion_studio':
            requested_motion_ids = (
                set()
                if initialization_only
                else {str(motion_id) for motion_id in groups}
            )
        initialization_fallback_used = False
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
            motor_axis = optional_int(row.get('motor_axis'))
            motor = None
            if motor_ref:
                matches = motion_run_rules._motors_for_ref(motor_ref, motors)
                if len(matches) == 0:
                    errors.append(f'Motion ID {motion_id}: Motor {motor_ref} not found')
                    continue
                if len(matches) > 1:
                    errors.append(f'Motion ID {motion_id}: Motor {motor_ref} is duplicated')
                    continue
                motor = matches[0]
                motor_axis = optional_int(motor.get('controller_index'))
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
                    finite_float(row.get('initial_motion_position_deg')) or 0.0
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
            motor_error = motion_run_rules._motor_ready_error(motor)
            if motor_error and not automation_run:
                errors.append(f'Motion ID {motion_id}: {motor_error}')

            motion_values = [record['value'] for record in groups[motion_id]]
            motion_min = min(motion_values)
            motion_max = max(motion_values)
            lower = finite_float(row.get('motion_lower_deg'))
            upper = finite_float(row.get('motion_upper_deg'))
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

            command_motion_min = motion_run_rules._clamp_motion_value(motion_min, lower, upper)
            command_motion_max = motion_run_rules._clamp_motion_value(motion_max, lower, upper)

            target_min = motion_run_rules._motor_target(row, command_motion_min)
            target_max = motion_run_rules._motor_target(row, command_motion_max)
            target_low = min(target_min, target_max)
            target_high = max(target_min, target_max)
            limit_error = motion_run_rules._target_range_limit_error(motor, target_low, target_high)
            if limit_error:
                errors.append(f'Motion ID {motion_id}: {limit_error}')

            initial_motion_source_value = motion_run_rules._initial_motion_value(row, groups[motion_id])
            initial_motion_value = motion_run_rules._clamp_motion_value(
                initial_motion_source_value,
                lower,
                upper,
            )
            row_initial_time = max(
                finite_float(row.get('initial_move_time_sec')) or 0.0,
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
                'motor_type': motion_run_rules._motor_type(motor),
                'initial_move_time_sec': initial_move_time,
                'initial_motion_source_position_deg': initial_motion_source_value,
                'initial_motion_position_deg': initial_motion_value,
                'initial_motor_target_deg': motion_run_rules._motor_target(row, initial_motion_value),
                'motion_limit_lower_deg': lower,
                'motion_limit_upper_deg': upper,
                'source_motion_min_deg': motion_min,
                'source_motion_max_deg': motion_max,
                'command_motion_min_deg': command_motion_min,
                'command_motion_max_deg': command_motion_max,
                'motion_clamped': command_motion_min != motion_min or command_motion_max != motion_max,
                'target_min_deg': target_low,
                'target_max_deg': target_high,
                'loop_start_motion_deg': motion_run_rules._clamp_motion_value(motion_values[0], lower, upper),
                'loop_end_motion_deg': motion_run_rules._clamp_motion_value(motion_values[-1], lower, upper),
                'row': row,
            }
            axis_plan['loop_start_target_deg'] = motion_run_rules._motor_target(
                row,
                axis_plan['loop_start_motion_deg'],
            )
            axis_plan['loop_end_target_deg'] = motion_run_rules._motor_target(
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
        duplicate_axes = motion_run_rules._duplicate_axis_text(axes)
        if duplicate_axes:
            errors.append(f'duplicate motor axis in enabled mappings: {duplicate_axes}')
        if errors:
            raise ValueError('; '.join(errors[:8]))

        start_time = min(record['time_sec'] for record in motion_records)
        end_time = max(record['time_sec'] for record in motion_records)
        duration = max(end_time - start_time, 0.0)
        samples = []
        if not initialization_only:
            sample_count = max(1, int(math.floor(duration / self.period_sec)) + 1)
            last_time = start_time + ((sample_count - 1) * self.period_sec)
            if end_time - last_time > 0.001:
                sample_count += 1
            group_times = {
                motion_id: [float(record['time_sec']) for record in records]
                for motion_id, records in groups.items()
            }
            for index in range(sample_count):
                sample_time = min(start_time + (index * self.period_sec), end_time)
                positions = {}
                motion_values = {}
                for axis in axes:
                    motion_id = str(axis['motion_id'])
                    motion_value = motion_run_rules._interpolated_value(
                        groups[motion_id],
                        group_times[motion_id],
                        sample_time,
                    )
                    motion_value = motion_run_rules._clamp_motion_value(
                        motion_value,
                        axis.get('motion_limit_lower_deg'),
                        axis.get('motion_limit_upper_deg'),
                    )
                    positions[int(axis['motor_axis'])] = motion_run_rules._motor_target(
                        axis['row'],
                        motion_value,
                    )
                    motion_values[motion_id] = float(motion_value)
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
            motion_run_rules._continuous_capability(axes)
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
            'group_execution': group_execution,
            'execution_id': str(payload.get('execution_id') or ''),
            'group_cycle_number': int(payload.get('group_cycle_number') or 0),
            'motion_file_id': motion_file_id,
            'mapping_file_id': mapping_file_id,
            'run_mode': run_mode,
            'automation_run': automation_run,
            'repeat_mode': repeat_mode,
            'dwell_sec': dwell_sec,
            'countdown_sec': countdown_sec,
            'scheduled_start_at': scheduled_start_at,
            'synchronized_cycle_sec': synchronized_cycle_sec,
            'synchronized_repeat_count': synchronized_repeat_count,
            'hold_final_until_cycle': bool(payload.get('hold_final_until_cycle')),
            'network_operation_id': str(payload.get('network_operation_id') or ''),
            'network_lease_id': str(payload.get('network_lease_id') or ''),
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
                'initialization_duration_sec': max(
                    (
                        float(axis.get('initial_move_time_sec') or self.period_sec)
                        for axis in axes
                    ),
                    default=0.0,
                ),
                'continuous_available': continuous_capability['available'],
                'clamped_axis_count': sum(1 for axis in axes if axis.get('motion_clamped')),
                'automation_run': automation_run,
                'repeat_mode': repeat_mode,
                'dwell_sec': dwell_sec,
                'countdown_sec': countdown_sec,
                'scheduled_start_at': scheduled_start_at,
                'synchronized_cycle_sec': synchronized_cycle_sec,
                'synchronized_repeat_count': synchronized_repeat_count,
                'operation_generation': operation_generation,
                'target_cycle_count': target_cycle_count,
            },
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
            number = finite_float(value)
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
        if motion_run_rules._has_ac_axes(axes):
            self._publish_ac_enable_for_axes(motors, axes)
            time.sleep(self._setpoint_clear_sec())

    def _publish_positions(
        self,
        motors: List[Dict[str, Any]],
        axes: List[Dict[str, Any]],
        positions: Dict[int, float],
    ) -> None:
        target_axes = motion_run_rules._sorted_controller_axes(positions.keys())
        command = motion_run_rules._empty_motor_command(target_axes)
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
        ac_axes = motion_run_rules._sorted_controller_axes(ac_axes)
        command = motion_run_rules._empty_motor_command(ac_axes)
        for slot, _axis in enumerate(ac_axes):
            command.number_of_target_interfaces[slot] = 1
            command.target_interface_id[slot] = Int8MultiArray(data=[ID_CONTROLWORD])
            command.controlword[slot] = CW_ENABLE_OPERATION_MINAS
        self._command_pub.publish(command)

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
                ready_error = motion_run_rules._motor_ready_error(
                    motor or {'controller_index': motor_axis}
                )
                if ready_error:
                    return False, ready_error
                current = motion_run_rules._motor_position_deg(motor)
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

    def _wait_for_current_motors(
        self,
        timeout_sec: float = STATE_TIMEOUT_SEC,
    ) -> List[Dict[str, Any]]:
        deadline = time.monotonic() + max(float(timeout_sec), 0.0)
        while True:
            motors = self._current_motors()
            if motors:
                return motors
            if self._stop_event.is_set():
                raise InterruptedError()
            remaining = deadline - time.monotonic()
            if remaining <= 0.0:
                return []
            time.sleep(min(max(self.period_sec, 0.01), 0.05, remaining))

    def _motor_for_axis(
        self,
        axis: int,
        motors: Optional[List[Dict[str, Any]]] = None,
    ) -> Optional[Dict[str, Any]]:
        for motor in motors if motors is not None else self._current_motors():
            if optional_int(motor.get('controller_index')) == axis:
                return motor
        return None

    def _load_motion_records(self, path: Path) -> List[Dict[str, Any]]:
        first_line = ''
        with path.open('r', encoding='utf-8') as handle:
            for raw_line in handle:
                line = raw_line.strip()
                if line and not line.startswith('#'):
                    first_line = line
                    break
            try:
                first_payload = json.loads(first_line)
            except json.JSONDecodeError:
                first_payload = None
            if (
                isinstance(first_payload, dict)
                and first_payload.get('type') == 'motion_header'
            ):
                headers = first_payload.get(
                    'fields',
                    first_payload.get('headers', first_payload.get('columns', [])),
                )
                headers = (
                    [str(item) for item in headers]
                    if isinstance(headers, list)
                    else []
                )
                records = []
                row_index = 0
                for raw_line in handle:
                    line = raw_line.strip()
                    if not line or line.startswith('#'):
                        continue
                    parsed = motion_table.parse_text_row(line)
                    if parsed is None:
                        continue
                    for row in motion_table.expand_pair_rows([parsed]):
                        record, _row_error = motion_table.parse_row(row, headers)
                        if record is None:
                            continue
                        record['row_index'] = row_index
                        row_index += 1
                        records.append(record)
                if not records:
                    raise ValueError('motion file has no valid records')
                return sorted(
                    records,
                    key=lambda item: (
                        item['time_sec'],
                        str(item['motion_id']),
                        item['row_index'],
                    ),
                )

        content = path.read_text(encoding='utf-8')
        rows, headers = motion_run_rules._extract_motion_rows(content)
        records = []
        for index, row in enumerate(rows):
            record, _row_error = motion_table.parse_row(row, headers)
            if record is None:
                continue
            record['row_index'] = index
            records.append(record)
        if not records:
            raise ValueError('motion file has no valid records')
        return sorted(records, key=lambda item: (item['time_sec'], str(item['motion_id']), item['row_index']))

    def _load_mapping(self, path: Path) -> Dict[str, Any]:
        data = yaml.safe_load(path.read_text(encoding='utf-8')) or {}
        if not isinstance(data, dict):
            raise ValueError('motion mapping root must be an object')
        return data

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
        with self._run_lock:
            self._status = apply_group_display(dict(self._status))
        msg = String()
        msg.data = json.dumps(self.status(), ensure_ascii=False)
        self._status_pub.publish(msg)

    def _load_period_sec(self) -> float:
        period = finite_float(
            self.declare_parameter('command_period_sec', DEFAULT_PERIOD_SEC).value
        )
        if period is None or period <= 0:
            return DEFAULT_PERIOD_SEC
        return max(period, 0.001)


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
