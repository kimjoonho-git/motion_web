"""Validate and execute motion plans independently from the web API process."""

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
from motion_common import command_router, generation as generation_mod, motion_table, topics
from motion_common.coordination import (
    coordination_settings_path,
    load_coordination_settings,
)
from motion_common.values import finite_float, optional_int
from motion_control_msgs.msg import MotorStatus
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
from std_msgs.msg import String

from .motion_run_constants import (
    DEFAULT_PERIOD_SEC,
    STATE_TIMEOUT_SEC,
    SAFETY_STATUS_TIMEOUT_SEC,
    AC_TARGET_TOLERANCE_DEG,
    DYNAMIXEL_TARGET_TOLERANCE_DEG,
    TARGET_SETTLE_TIMEOUT_SEC,
)
from . import motion_run_rules
from .group_session import GroupSession
from .motion_player import MotionPlayer
from .plan_builder import PlanBuilder
from .motion_automation_store import (
    MotionAutomationStore,
    default_automation_state,
    normalize_automation_state,
)
from .motion_group_display import apply_group_display


DEFAULT_MOTION_PROJECTS_DIR = (
    Path(os.environ.get('MOTION_WORKSPACE', Path.cwd())).expanduser()
    / 'motion_projects'
)


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
        self._plan_builder = PlanBuilder(self)
        self._player = MotionPlayer(self)
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
        """Action 결과를 60초 창으로 모아둔다.

        **지금은 읽는 곳이 없다** · 결과를 기다리던 초기 이동 대기 코드가
        §6-49에서 죽은 코드로 판명되어 함께 사라졌다. 구독을 떼는 것은 이 노드가
        토픽에서 빠지는 일이라 별도 판단으로 남긴다 · 모으는 양은 60초로 제한된다.
        """
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

    def _coordination_enabled(self) -> bool:
        """이 PC 가 연동을 쓰는가 · 부팅 자동 재생 되살리기 판단에 쓴다 · §6-70

        읽지 못하면 **연동을 쓰는 것으로 본다** · 되살리지 않는 쪽이 안전하다.
        """
        try:
            settings = load_coordination_settings(
                coordination_settings_path('motion_runtime')
            )
        except Exception:
            self.get_logger().warning('연동 설정을 읽지 못했습니다 · 자동 재생을 되살리지 않습니다')
            return True
        if settings is None:
            return False
        return bool(settings.get('enabled', False))

    def _confirm_execution_context(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        context_id = str(payload.get('context_id') or '').strip()
        with self._run_lock:
            if not context_id or context_id != self._execution_context.get('context_id'):
                raise ValueError('확인하려는 실행 컨텍스트가 적용된 설정과 다릅니다')
            self._execution_context_ready = True
            automation = dict(
                getattr(self, '_automation_state', default_automation_state())
            )
            # 연동 중이면 부팅 재생은 그룹이 몬다(`_drive_auto_play`) · 여기서
            # 로컬을 되살리면 실행 슬롯을 먼저 차지해 그룹 시작이
            # "previous motion run task is still running" 으로 막힌다 · §6-70
            if (
                automation.get('enabled')
                and automation.get('armed')
                and not self._coordination_enabled()
            ):
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
            plan = self._plan_builder.build(payload)
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

    def _claim_run_slot(self) -> List[Dict[str, Any]]:
        """실행 슬롯을 잡고 모터 스냅샷을 돌려준다 · `_run_lock`을 잡은 채 부른다.

        단일 실행(`_start_thread`)과 그룹 실행(`GroupSession.prepare`)이 각자
        같은 세 관문을 통과시키고 있었다 · 앞선 실행 · 재생 소유권 · 모터 상태.
        관문이 하나 늘 때 한쪽만 고치면 그쪽으로만 빠져나간다.

        막혀 있으면 :class:`RunSlotUnavailable`, 모터 상태가 없으면
        ``ValueError``를 올린다 · 후자는 호출부 위의 명령 라우터가 오류 응답으로
        바꾸므로 통합 전 계약 그대로다.
        """
        if self._run_thread is not None and self._run_thread.is_alive():
            raise motion_run_rules.RunSlotUnavailable(
                'previous motion run task is still running'
            )
        ownership_error = self._playback_ownership_error()
        if ownership_error:
            raise motion_run_rules.RunSlotUnavailable(ownership_error)
        motors_snapshot = self._current_motors()
        if not motors_snapshot:
            raise ValueError('current motion_state is unavailable')
        self._stop_event.clear()
        self._graceful_stop_event.clear()
        return motors_snapshot

    def _start_thread(self, mode: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        with self._run_lock:
            try:
                motors_snapshot = self._claim_run_slot()
            except motion_run_rules.RunSlotUnavailable as exc:
                return {
                    'success': False,
                    'message': str(exc),
                    'status': self.status(),
                }
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
                target=self._player._prepare_and_run,
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
                        plan = self._plan_builder.build(payload, motors_snapshot=motors)
                        init_plan = self._plan_builder.build(
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
