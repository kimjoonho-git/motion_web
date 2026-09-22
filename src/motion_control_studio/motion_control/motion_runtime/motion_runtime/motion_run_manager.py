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
from motion_common.execution_context import confirm_context_id
from motion_common.paths import project_dir_for
from motion_common import command_router, generation as generation_mod, motion_table, topics
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


#: 재생이 **비켜 주지 않아도 되는** 주인 · §6-290
#:
#: 중재기는 재생이 MIDI 를 뺏도록 돼 있다(`_PREEMPTS`) · 그런데 시작 판정은
#: 그보다 엄격해서, 어느 PC 에서 페이더 하나만 잡고 있어도 그 PC 가 「준비 안
#: 됨」으로 답했고 **그룹 전체가 취소**됐다 · 실측으로 pc-a 의 MIDI 때문에
#: 세 대짜리 그룹이 못 떴다.
#:
#: 모션 실행은 MIDI 와 상관없이 시작한다 · 축은 중재기가 넘겨준다.
#:
#: 수동 조그는 그대로 막는다 · 재생이 그것은 못 뺏는다 · 사람이 손으로 움직이는
#: 중에 모션이 끼어들면 안 된다.
PLAYBACK_MAY_TAKE_FROM = ('none', 'playback', 'midi')


class MotionRunManager(Node):
    """Runs a saved motion file through a saved motion-axis mapping.

    This node owns the motion-file lifecycle but never publishes to the final
    hardware command topic. Combined setpoints are submitted to the supervisor
    through /motion_run/command.
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
            'stop_after_cycle': False,
        }
        self._automation_project_id = ''
        self._automation_last_attempt_at = 0.0
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
    #: **모터를 움직이는 명령만** 여기 넣는다 · §6-269
    #:
    #: `automation_configure` 가 여기 있었다 · 그것은 반복 방식을 파일에 적는
    #: 일이고 모터를 건드리지 않는다 · 처리기도 실행 컨텍스트를 읽기만 하고
    #: 쓰지 않는다 · 그런데 모터가 준비되지 않으면 저장이 「현재 프로젝트 실행
    #: 컨텍스트 적용 대기 중입니다」로 거절됐다 (실측 3회 전부 실패).
    #:
    #: 화면은 드롭다운을 회색으로 만들지도 않는다 · 고를 수는 있는데 저장만
    #: 실패했다 · 같은 성격인 스튜디오 레이어 저장·모션축 설정 읽기·MIDI 뱅크
    #: 편집은 모두 열려 있다.
    COMMANDS_REQUIRING_CONTEXT = frozenset({
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
        """**실행 허용만 거둔다 · 사람이 저장한 설정은 그대로 둔다** · §6-267

        브릿지는 실행 컨텍스트가 준비되지 않으면 **1초마다** 이것을 보낸다 ·
        전에는 그때마다 자동 반복 설정과 그것이 어느 프로젝트 것인지까지
        지웠다.

        `_automation_project_id` 가 비면 저장이 **「자동 반복을 저장할 현재
        프로젝트가 없습니다」**로 거절된다 · 사람이 설정을 고치는 동안 1초마다
        그 상태가 되었다.

        자동 반복 설정은 파일에 저장되고 `select_project` 가 다시 읽는다 ·
        여기서 지울 이유가 없다 · 스튜디오 §6-257 · MIDI §6-265 와 같은 원칙.

        동작 중에는 여전히 거부한다.
        """
        with self._run_lock:
            if self._run_thread is not None and self._run_thread.is_alive():
                raise ValueError('모션 동작 중에는 프로젝트 메모리를 폐기할 수 없습니다')
            self._execution_context = {}
            self._execution_context_ready = False
            self._automation_runtime = {
                'state': 'off',
                'message': '',
                'stop_after_cycle': False,
            }
            project_id = self._automation_project_id
        return {
            'success': True,
            'message': '모션 실행 대기 · 저장된 자동 반복 설정은 유지',
            'project_id': project_id,
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
            command_router.log_command_failure(
                self.get_logger(), f'motion run command failed: {command}', exc,
            )
            response = command_router.error_response(
                f'motion run command failed: {exc}'
            )

        self._publish_response(command_router.finalize(response, request))
        self._publish_status()

    #: 실행 컨텍스트를 새로 세우는 명령 · 이때만 세대가 오를 수 있다
    #:
    #: 목록의 주인은 `generation` 이다 · §6-166 · 세 노드가 똑같이 적어 두고
    #: 있었다 · 새 명령이 생기면 세 곳을 고쳐야 하고, 빠뜨린 노드만 세대를
    #: 안 올려 그 노드의 응답이 「이전 프로젝트의 늦은 응답」으로 버려진다.
    #:
    #: MIDI 노드는 `select_project` 를 쓰므로 **진짜로 다르다** · 거기는
    #: 제 목록을 갖는다.
    CONTEXT_COMMANDS = generation_mod.CONTEXT_COMMANDS

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
        with self._run_lock:
            context_id = confirm_context_id(self._execution_context, payload)
            # 부팅 때 스스로 시작하는 기능은 없앴다 · §6-134
            #
            # 켜는 곳이 둘이었고(이 PC · 그룹) 서로 배타적이었다 · 연동을 켜면
            # 로컬이 스스로 꺼지고, 그룹은 필수 PC 가 2대 미만이면 안 떴다 ·
            # 그래서 혼자 쓰는 PC 가 연동을 켜 두면 아무것도 안 됐다.
            #
            # 시작은 사람이 누르거나 스케줄이 시킨다 · 판단 주체가 하나다.
            self._execution_context_ready = True
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
            self._automation_runtime = {
                'state': 'blocked' if error else 'ready',
                'message': error,
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

        # 반복 방식만 저장한다 · 부팅 자동 재생은 없앴다 · §6-134
        files_ready = bool(motion_file_id and mapping_file_id)

        candidate = normalize_automation_state({
            **current,
            'repeat_mode': repeat_mode,
            'dwell_sec': dwell_sec,
            'motion_file_id': motion_file_id if files_ready else '',
            'mapping_file_id': mapping_file_id if files_ready else '',
            'motion_sha256': '',
            'mapping_sha256': '',
            'last_error': '',
        })

        saved = self._save_automation(
            candidate,
            runtime_state='ready' if files_ready else 'blocked',
            runtime_message=(
                '자동 반복 설정 저장 완료'
                if files_ready
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

    def _automation_snapshot(self) -> Dict[str, Any]:
        with self._run_lock:
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
                # **누가 켰는지 기억한다** · §6-270
                #
                # 전에는 스케줄이 보낸 `schedule_id` 를 받고도 버렸다 · 그래서
                # 스케줄은 돌고 있는 모션이 자기가 켠 것인지 알 수 없었고,
                # 구간이 끝나도 멈출 근거가 없었다 · 실측으로 16~18시 스케줄이
                # 17:58 에 켠 모션이 18:16 까지 돌았다.
                'schedule_id': str(payload.get('schedule_id') or ''),
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
            # 시계를 두 번 본다 · 위의 `while` 에서 한 번, 여기서 또 한 번.
            # 그 사이에 마감이 지나가면 남은 시간이 음수가 되고
            # `time.sleep()` 이 터진다 · 기다림의 **마지막 한 바퀴**는 늘
            # 남은 시간이 0 에 가까우므로 매 회차가 이 외줄을 한 번씩 탄다.
            #
            # 실제로 터졌다 · 2026-09-18 15:04 · 그룹 11회차의 「회차 후
            # 초기화 대기」에서 `sleep length must be non-negative` 가 나고
            # 세 대 연동이 통째로 정지했다 · 그전에는 6회차였다 · 확률이
            # 낮아 재현이 안 됐다.
            #
            # 이미 지났으면 안 자고 넘어가면 된다 · 바로 다음 `while` 이
            # 거짓이 되어 빠져나간다.
            time.sleep(max(min(0.02, deadline - time.monotonic()), 0.0))

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
                                error = f'{motor_axis}번 축의 현재 위치를 읽을 수 없습니다'
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
        if current.get('automation_run'):
            try:
                self._save_automation(
                    {
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

    def _axes_held_by_others(self, axes) -> Dict[int, str]:
        """이 축들 중 **남이 쥔 것**과 그 주인 · §6-276

        재생이 그 축만 놓고 나머지는 계속 몰 수 있도록, 이유가 아니라 **축
        목록**을 돌려준다 · 판정 자체는 감독자가 축별로 들고 있는 표를 본다.
        """
        lock = getattr(self, '_safety_status_lock', None)
        if lock is None:
            return {}
        with lock:
            payload = getattr(self, '_latest_safety_status', None)
            status = dict(payload) if isinstance(payload, dict) else None
        axis_owners = status.get('command_axis_owners') if status else None
        if not isinstance(axis_owners, dict) or axes is None:
            return {}
        held: Dict[int, str] = {}
        for axis in axes:
            holder = str(axis_owners.get(str(int(axis))) or 'none').strip().lower()
            if holder not in PLAYBACK_MAY_TAKE_FROM:
                held[int(axis)] = holder
        return held

    def _playback_ownership_error(self, axes=None) -> str:
        """재생이 지금 모터를 몰 수 없는 이유 · 없으면 빈 문자열.

        `axes` 를 주면 **그 축들만** 본다 · 안 주면 지금까지대로 대표 주인을
        본다(옛 호출부).
        """
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
        owner_names = {
            'midi': 'MIDI 제어',
            'manual': '수동 제어',
        }
        # 내가 쓰는 축의 주인만 본다 · §6-106
        #
        # `command_owner` 는 **대표 하나로 줄인 축약형**이다 · 추가 녹화에서
        # MIDI 가 축 하나를 잡으면 대표가 `midi` 로 바뀐다 · 그러면 다른 축을
        # 몰던 재생이 매 프레임 이 검사에 걸려 **스스로 멈췄다** · 레이어에
        # 있는 축의 재생이 끊기고, 그 위에 얹어 녹화하는 것이 불가능했다.
        #
        # 소유는 `CommandArbiter` 가 **축별**로 갖고 있다 · 축약형은 화면에
        # 쓰고, 계속할지 말지는 축별로 묻는다.
        axis_owners = status.get('command_axis_owners')
        if isinstance(axis_owners, dict) and axes is not None:
            blanket = str(axis_owners.get('all') or 'none').strip().lower()
            if blanket not in PLAYBACK_MAY_TAKE_FROM:
                return f"{owner_names.get(blanket, blanket)}가 사용 중이어서 모션을 시작할 수 없습니다"
            taken = self._axes_held_by_others(axes)
            # **한 축이라도 남았으면 계속한다** · §6-276
            #
            # 전에는 축 하나만 남이 쥐어도 곧바로 실행 전체를 오류로 끝냈다 ·
            # 추가 녹화에서 사람이 1-4 를 잡는 순간 1-1·1-2·1-3 의 재생까지
            # 같이 죽었다 · 축 하나의 다툼이 전부를 세울 이유가 없다.
            #
            # 몰 축이 하나도 안 남았을 때만 멈춘다 · 그때는 정말로 남이
            # 이 모션을 대신 몰고 있는 것이다.
            if taken and len(taken) >= len(set(int(axis) for axis in axes)):
                axis, holder = sorted(taken.items())[0]
                return (
                    f"{owner_names.get(holder, holder)}가 축 {axis}를 "
                    '사용 중이어서 모션을 시작할 수 없습니다'
                )
            return ''
        # 축을 모르는 옛 호출 · 표를 못 받은 상태 · 지금까지대로 축약형을 본다
        owner = str(status.get('command_owner') or 'none').strip().lower()
        if owner not in PLAYBACK_MAY_TAKE_FROM:
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

    #: 읽어 둔 모션 파일을 몇 개나 들고 있을 것인가 · §6-173
    #:
    #: 한 번 시작할 때 계획을 여러 번 만든다 · 단독 재생은 둘(재생 + 초기
    #: 이동), 연동은 셋(검증 + 재생 + 초기 이동) · 그때마다 같은 파일을 다시
    #: 읽고 파싱했다 · 183KB 짜리로 재 보니 **한 번에 45ms**, 세 번이면 135ms 다.
    #:
    #: 그 135ms 가 하필 **그룹 동기 시작 직전**에 놓인다.
    #:
    #: 파일이 바뀌면 자동으로 버린다 (`mtime` 과 크기를 같이 본다) · 그래서
    #: 스튜디오가 새로 내보낸 파일을 옛것으로 돌릴 일이 없다.
    MOTION_CACHE_SIZE = 4

    def _load_motion_records(self, path: Path) -> List[Dict[str, Any]]:
        """모션 파일을 읽어 행 목록으로 · 같은 파일이면 다시 안 읽는다 · §6-173

        **돌려주는 목록은 매번 새로 만든다** · 부르는 쪽이 여기에 덧붙이기
        때문이다 (`plan_builder` 가 초기 이동 대체값을 넣는다) · 같은 목록을
        돌려주면 그 값이 다음 계획에 쌓인다.

        행 하나하나(`dict`)는 나눠 쓴다 · 읽은 뒤에 그것을 고치는 곳은 없다.
        """
        try:
            stat = path.stat()
            key = (str(path), stat.st_mtime_ns, stat.st_size)
        except OSError:
            key = None
        if key is not None:
            cache = getattr(self, '_motion_record_cache', None)
            if cache is None:
                cache = {}
                self._motion_record_cache = cache
            cached = cache.get(key)
            if cached is not None:
                return list(cached)

        records = self._parse_motion_records(path)

        if key is not None:
            cache = self._motion_record_cache
            cache[key] = records
            while len(cache) > self.MOTION_CACHE_SIZE:
                cache.pop(next(iter(cache)))
        return list(records)

    def _parse_motion_records(self, path: Path) -> List[Dict[str, Any]]:
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
        project_dir = project_dir_for(self.motion_projects_dir, project_id)
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
