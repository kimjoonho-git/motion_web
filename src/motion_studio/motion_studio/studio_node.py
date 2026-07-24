"""ROS boundary for independent motion-data recording and project management."""

from __future__ import annotations

import json
import hashlib
import copy
import os
import re
import threading
import time
import traceback
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

import rclpy
from rclpy.node import Node
from std_msgs.msg import String

from .layer_validation import (
    point_curve_frame_mismatches,
    project_point_curve_frame_mismatches,
    validate_ranges,
)
from .operation_state import StudioOperationStateMachine
from .project_commands import StudioProjectCommands
from .project_store import DEFAULT_PERIOD_SEC, ProjectStore, normalize_layer
from .ros_gateway import StudioRosGateway
from .timeline import (
    layer_conflicts,
    layer_transition_warnings,
    motion_file_text,
    project_motion_ids,
    recording_values,
    render_project,
)


DEFAULT_MOTION_PROJECTS_DIR = str(
    Path(os.environ.get('MOTION_WORKSPACE', Path.cwd())).expanduser()
    / 'motion_projects'
)


def next_numbered_layer_name(layers: List[Dict[str, Any]], label: str) -> str:
    """Return a stable new name even after layers are deleted or reordered."""
    pattern = re.compile(rf'^{re.escape(label)}\s+(\d+)$')
    numbers = []
    for layer in layers:
        if not isinstance(layer, dict):
            continue
        matched = pattern.fullmatch(str(layer.get('name') or '').strip())
        if matched:
            numbers.append(int(matched.group(1)))
    return f'{label} {max(numbers, default=0) + 1}'


def project_initial_motion_values(
    project: Dict[str, Any], motion_ids: List[str]
) -> Dict[str, float]:
    """Return each enabled track's earliest recorded value for S-curve initialization."""
    selected = set(motion_ids)
    earliest: Dict[str, tuple[float, float]] = {}
    for layer in project.get('layers') or []:
        if not isinstance(layer, dict) or layer.get('enabled') is False:
            continue
        for frame in layer.get('frames') or []:
            try:
                time_sec = float(frame.get('time_sec') or 0.0)
            except (AttributeError, TypeError, ValueError):
                continue
            for motion_id, raw_value in (frame.get('values') or {}).items():
                motion_id = str(motion_id)
                if motion_id not in selected:
                    continue
                current = earliest.get(motion_id)
                if current is None or time_sec < current[0]:
                    earliest[motion_id] = (time_sec, float(raw_value))
    return {
        motion_id: earliest[motion_id][1]
        for motion_id in motion_ids
        if motion_id in earliest
    }


class MotionStudioNode(Node):
    """Own editable motion projects; delegate all motor work to existing nodes."""

    def __init__(self) -> None:
        super().__init__('motion_studio_node')
        self.motion_projects_dir = Path(str(self.declare_parameter(
            'motion_projects_dir', DEFAULT_MOTION_PROJECTS_DIR
        ).value)).expanduser().resolve()
        self.request_topic = str(self.declare_parameter(
            'request_topic', '/motion_studio/request'
        ).value)
        self.response_topic = str(self.declare_parameter(
            'response_topic', '/motion_studio/response'
        ).value)
        self.status_topic = str(self.declare_parameter(
            'status_topic', '/motion_studio/status'
        ).value)
        self.midi_state_topic = str(self.declare_parameter(
            'midi_state_topic', '/motion_web/midi_monitor/state'
        ).value)
        self.motion_run_request_topic = str(self.declare_parameter(
            'motion_run_request_topic', '/motion_control/motion_run_request'
        ).value)
        self.motion_run_response_topic = str(self.declare_parameter(
            'motion_run_response_topic', '/motion_control/motion_run_response'
        ).value)
        self.motion_run_status_topic = str(self.declare_parameter(
            'motion_run_status_topic', '/motion_control/motion_run_status'
        ).value)
        self.midi_request_topic = str(self.declare_parameter(
            'midi_request_topic', '/motion_web/midi_monitor/request'
        ).value)
        self.midi_response_topic = str(self.declare_parameter(
            'midi_response_topic', '/motion_web/midi_monitor/response'
        ).value)

        self._store = ProjectStore()
        self._workspace_project_id = ''
        self._execution_context: Dict[str, Any] = {}
        self._execution_context_ready = False
        self._project_generation = 0
        self._lock = threading.RLock()
        self._current_project: Optional[Dict[str, Any]] = None
        self._midi_state: Dict[str, Any] = {}
        self._motion_run_status: Dict[str, Any] = {}
        self._run_results: Dict[str, Dict[str, Any]] = {}
        self._midi_results: Dict[str, Dict[str, Any]] = {}
        self._record_started = 0.0
        self._record_frames: List[Dict[str, Any]] = []
        self._record_eligible_motion_ids: set[str] = set()
        self._recorded_motion_ids: set[str] = set()
        self._record_mode = 'record'
        self._operation_state = StudioOperationStateMachine()
        self._status = self._empty_status()
        self._project_commands = StudioProjectCommands(self)
        self._ros_gateway = StudioRosGateway(self)

        self._request_pub = self.create_publisher(String, self.motion_run_request_topic, 10)
        self._midi_request_pub = self.create_publisher(String, self.midi_request_topic, 10)
        self._response_pub = self.create_publisher(String, self.response_topic, 10)
        self._status_pub = self.create_publisher(String, self.status_topic, 10)
        self.create_subscription(String, self.request_topic, self._request_callback, 10)
        self.create_subscription(String, self.midi_state_topic, self._midi_callback, 10)
        self.create_subscription(String, self.midi_response_topic, self._midi_response_callback, 10)
        self.create_subscription(
            String, self.motion_run_response_topic, self._run_response_callback, 10
        )
        self.create_subscription(
            String, self.motion_run_status_topic, self._run_status_callback, 10
        )
        self.create_timer(DEFAULT_PERIOD_SEC, self._record_tick)
        self.create_timer(0.5, self._publish_status)
        self.get_logger().info(
            f'motion_studio_node started: projects={self.motion_projects_dir}, '
            f'request={self.request_topic}'
        )

    def _empty_status(self) -> Dict[str, Any]:
        return {
            'success': True,
            'node_state': 'ok',
            'state': 'idle',
            'phase': 'idle',
            'message': '모션 스튜디오 대기',
            'project': None,
            'record_mode': None,
            'elapsed_sec': 0.0,
            'recorded_frames': 0,
            'selected_motion_ids': [],
            'recording_motion_ids': [],
            'updated_at': time.time(),
        }

    def _midi_callback(self, msg: String) -> None:
        try:
            payload = json.loads(msg.data)
        except json.JSONDecodeError:
            return
        if isinstance(payload, dict):
            with self._lock:
                context = payload.get('execution_context')
                if not isinstance(context, dict):
                    context = {}
                project_id = str(
                    payload.get('project_id')
                    or context.get('project_id')
                    or ''
                )
                if (
                    self._execution_context_ready
                    and project_id == self._workspace_project_id
                ):
                    self._midi_state = payload

    def _run_response_callback(self, msg: String) -> None:
        try:
            payload = json.loads(msg.data)
        except json.JSONDecodeError:
            return
        self._gateway().accept_run_response(payload)

    def _midi_response_callback(self, msg: String) -> None:
        try:
            payload = json.loads(msg.data)
        except json.JSONDecodeError:
            return
        self._gateway().accept_midi_response(payload)

    def _run_status_callback(self, msg: String) -> None:
        try:
            payload = json.loads(msg.data)
        except json.JSONDecodeError:
            return
        if isinstance(payload, dict):
            with self._lock:
                if str(payload.get('project_id') or '') != self._workspace_project_id:
                    return
                context = payload.get('execution_context')
                if not isinstance(context, dict):
                    return
                try:
                    if int(context.get('project_generation')) != self._context_generation():
                        return
                except (TypeError, ValueError):
                    return
                self._motion_run_status = payload
                studio_state = str(self._status.get('state') or '')
                run_state = str(payload.get('state') or '')
                progress = payload.get('progress')
                if (
                    payload.get('request_source') == 'motion_studio'
                    and studio_state in {'initializing', 'playing', 'stopping'}
                    and isinstance(progress, dict)
                ):
                    self._status['runtime_progress'] = dict(progress)
                    self._status['updated_at'] = time.time()
                    if studio_state == 'initializing' and run_state in {'initializing', 'initialized'}:
                        self._status['initialization_progress'] = dict(progress)
                    elif studio_state == 'playing' and run_state in {'running', 'verifying'}:
                        self._status['elapsed_sec'] = float(progress.get('elapsed_sec') or 0.0)
                        self._status['playback_duration_sec'] = float(
                            progress.get('duration_sec')
                            or self._status.get('playback_duration_sec')
                            or 0.0
                        )
                if (
                    studio_state == 'playing'
                    and payload.get('request_source') == 'motion_studio'
                    and payload.get('state') in {'completed', 'error', 'stopped'}
                ):
                    final_progress = dict(progress) if isinstance(progress, dict) else {}
                    self._set_status_locked(
                        'idle' if payload.get('state') != 'error' else 'error',
                        str(payload.get('message') or '합성 미리보기 종료'),
                    )
                    self._status['runtime_progress'] = final_progress
                    self._status['elapsed_sec'] = float(final_progress.get('elapsed_sec') or 0.0)
                    self._status['playback_duration_sec'] = float(
                        final_progress.get('duration_sec')
                        or self._status.get('playback_duration_sec')
                        or 0.0
                    )

    def _request_callback(self, msg: String) -> None:
        try:
            request = json.loads(msg.data)
        except json.JSONDecodeError:
            return
        if not isinstance(request, dict):
            return
        request_id = str(request.get('request_id') or '')
        project_generation = request.get('project_generation')
        command = str(request.get('command') or 'status').strip()
        payload = request.get('payload') if isinstance(request.get('payload'), dict) else {}
        try:
            self._validate_request_generation(command, project_generation, payload)
            result = self._handle(command, payload)
        except Exception as exc:
            self.get_logger().error(f'studio command failed: {command}\n{traceback.format_exc()}')
            result = {'success': False, 'message': str(exc)}
        result['request_id'] = request_id
        result['project_generation'] = project_generation
        self._publish_json(self._response_pub, result)
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

    def _handle(self, command: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        self._select_workspace(payload)
        if command == 'apply_context':
            return self._apply_execution_context(payload)
        if command == 'confirm_context':
            context_id = str(payload.get('context_id') or '').strip()
            with self._lock:
                if (
                    not context_id
                    or context_id != self._execution_context.get('context_id')
                ):
                    raise ValueError('확인하려는 실행 컨텍스트가 적용된 설정과 다릅니다')
                self._execution_context_ready = True
                confirmed_context = dict(self._execution_context)
            return {
                'success': True,
                'message': '모션 스튜디오 사용 허용',
                **confirmed_context,
                'status': self.snapshot(),
            }
        if command == 'invalidate_context':
            with self._lock:
                self._operation_machine().cancel()
                self._store = ProjectStore()
                self._workspace_project_id = ''
                self._current_project = None
                self._execution_context = {}
                self._execution_context_ready = False
                self._midi_state = {}
                self._motion_run_status = {}
                self._run_results.clear()
                self._midi_results.clear()
                self._record_started = 0.0
                self._record_frames = []
                self._record_eligible_motion_ids = set()
                self._recorded_motion_ids = set()
                self._status = self._empty_status()
            return {
                'success': True,
                'message': '모션 스튜디오 프로젝트 메모리 폐기',
                'project_id': '',
                'context_id': '',
                'status': self.snapshot(),
            }
        project_commands = getattr(self, '_project_commands', None)
        if project_commands is None:
            project_commands = StudioProjectCommands(self)
            self._project_commands = project_commands
        if project_commands.handles(command):
            return project_commands.handle(command, payload)
        if command == 'record':
            self._require_execution_context()
            return self._start_record(payload)
        if command == 'initialize':
            self._require_execution_context()
            return self._start_initial_position(payload)
        if command == 'play':
            self._require_execution_context()
            return self._start_playback(payload)
        if command == 'stop':
            return self._stop()
        if command == 'export':
            return self._export(payload)
        raise ValueError(f'지원하지 않는 모션 스튜디오 명령: {command}')

    def _apply_execution_context(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        self._select_workspace(payload)
        context_id = str(payload.get('context_id') or '').strip()
        mapping_file_id = str(payload.get('mapping_file_id') or '').strip()
        mapping_sha256 = str(payload.get('mapping_sha256') or '').strip()
        if not context_id or not mapping_file_id or not mapping_sha256:
            raise ValueError('실행 컨텍스트 ID와 모션축 설정 버전이 필요합니다')
        path = self.motion_projects_dir / self._workspace_project_id / 'motion_axis_matching' / mapping_file_id
        if path.parent != (
            self.motion_projects_dir / self._workspace_project_id / 'motion_axis_matching'
        ) or not path.is_file():
            raise ValueError('현재 프로젝트의 모션축 설정 파일을 찾을 수 없습니다')
        actual_sha = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual_sha != mapping_sha256:
            raise ValueError('모션축 설정 파일 버전이 실행 컨텍스트와 다릅니다')
        with self._lock:
            next_context = {
                'context_id': context_id,
                'project_id': self._workspace_project_id,
                'project_generation': int(payload.get('project_generation') or 0),
                'mapping_file_id': mapping_file_id,
                'mapping_sha256': actual_sha,
            }
            same_context = self._execution_context == next_context
            self._execution_context = next_context
            if not same_context:
                self._execution_context_ready = False
        return {
            'success': True,
            'message': '모션 스튜디오 실행 컨텍스트 적용 확인 완료',
            **self._execution_context,
            'status': self.snapshot(),
        }

    def _require_execution_context(self) -> None:
        with self._lock:
            ready = self._execution_context_ready
            project_id = self._execution_context.get('project_id')
        if (
            not ready
            or project_id != self._workspace_project_id
            or self._context_generation() != int(self._project_generation or 0)
        ):
            raise ValueError('현재 프로젝트 실행 컨텍스트 적용 대기 중입니다')
        path = (
            self.motion_projects_dir / project_id / 'motion_axis_matching'
            / str(self._execution_context.get('mapping_file_id') or '')
        )
        if (
            not path.is_file()
            or hashlib.sha256(path.read_bytes()).hexdigest()
            != self._execution_context.get('mapping_sha256')
        ):
            with self._lock:
                self._execution_context_ready = False
            raise ValueError('모션축 설정 파일이 변경되어 실행 컨텍스트 재적용이 필요합니다')

    def _select_workspace(self, payload: Dict[str, Any]) -> None:
        project_id = str(
            payload.get('project_id') or payload.get('workspace_project_id') or ''
        ).strip()
        if (
            not project_id
            or project_id != Path(project_id).name
            or project_id.startswith('.')
            or '/' in project_id
            or '\\' in project_id
        ):
            raise ValueError('유효한 통합 프로젝트 ID가 필요합니다')
        project_dir = (self.motion_projects_dir / project_id).resolve()
        if (
            project_dir.parent != self.motion_projects_dir
            or not (project_dir / 'project.json').is_file()
        ):
            raise ValueError(f'통합 프로젝트를 찾을 수 없습니다: {project_id}')
        if project_id == self._workspace_project_id:
            return
        with self._lock:
            self._require_idle_locked()
            self._store.use_workspace(project_dir)
            self._workspace_project_id = project_id
            self._current_project = None

    def _project_result(self, project: Dict[str, Any], message: str = '완료') -> Dict[str, Any]:
        mapping = self._store.mapping_check(project)
        motion_ranges = self._motion_ranges(mapping)
        conflicts = layer_conflicts(project)
        transition_warnings = layer_transition_warnings(
            project, motion_ranges, self._manual_initial_values(mapping)
        )
        curve_mismatches = project_point_curve_frame_mismatches(project)
        range_warnings = [
            {
                **issue,
                'layer_id': str(layer.get('layer_id') or ''),
                'layer_name': str(layer.get('name') or ''),
            }
            for layer in project.get('layers') or []
            if isinstance(layer, dict)
            for issue in validate_ranges(layer, motion_ranges)
        ]
        return {
            'success': True,
            'message': message,
            'project': project,
            'mapping': mapping,
            'status': self.snapshot(),
            'composition': {
                'conflicts': conflicts,
                'transition_warnings': transition_warnings,
                'range_warnings': range_warnings,
                'point_curve_mismatches': curve_mismatches,
                'conflict_free': not conflicts and not transition_warnings and not curve_mismatches,
            },
        }

    @staticmethod
    def _require_point_curve_consistency(project: Dict[str, Any], action: str) -> None:
        mismatches = project_point_curve_frame_mismatches(project)
        if not mismatches:
            return
        first = mismatches[0]
        raise ValueError(
            f'{action} 차단: {first["layer_name"]}의 {first["motion_id"]} '
            '포인트 곡선과 20ms 프레임이 다릅니다. '
            '포인트 기준 재계산 또는 현재 프레임 유지를 선택하세요'
        )

    def _start_record(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        mode = str(payload.get('mode') or 'record').strip().lower()
        if mode not in {'record', 'overdub', 'append'}:
            raise ValueError('녹화 모드는 record, overdub, append 중 하나여야 합니다')
        with self._lock:
            self._require_idle_locked()
            project = self._require_project_locked()
            self._validate_mapping_locked(project)
            if mode in {'overdub', 'append'} and project.get('layers'):
                raise ValueError(
                    '오버더빙/이어 녹화는 축별 충돌 중재가 완성된 뒤 활성화됩니다. '
                    '현재는 안전을 위해 일반 모션 녹화만 허용합니다'
                )
            mapping = self._store.mapping_check(project)
            motion_ids = list(mapping.get('motion_ids') or [])
            if not motion_ids:
                raise ValueError('모션축 설정에 녹화 가능한 Motion ID가 없습니다')
            self._record_mode = mode
            self._record_frames = []
            self._record_eligible_motion_ids = set(motion_ids)
            self._recorded_motion_ids = set()
            operation_generation = self._operation_machine().begin(
                str(self._status.get('state') or '')
            )
            self._set_status_locked('initializing', '초기 위치 이동 준비 중')
        thread = threading.Thread(
            target=self._prepare_record,
            args=(float(payload.get('initial_move_time_sec') or 5.0), operation_generation),
            daemon=True,
        )
        thread.start()
        return {'success': True, 'message': '자동 초기 위치 이동을 시작합니다', 'status': self.snapshot()}

    def _prepare_record(self, move_time: float, operation_generation: int) -> None:
        midi_locked = False
        try:
            self._require_active_operation(operation_generation, 'initializing')
            midi_prepare = self._request_midi('studio_recording_prepare', {}, 5.0)
            midi_locked = True
            if not midi_prepare.get('success'):
                raise ValueError(
                    midi_prepare.get('message') or 'MIDI 녹화 초기화 준비 실패'
                )
            self._wait_for_midi_faders_zero(8.0)
            self._require_active_operation(operation_generation, 'initializing')
            with self._lock:
                project = dict(self._require_project_locked())
                motion_ids = list(self._record_eligible_motion_ids)
            zero_frames = [{'frame': 1, 'time_sec': DEFAULT_PERIOD_SEC,
                            'values': {motion_id: 0.0 for motion_id in motion_ids}}]
            file_id = self._store.write_motion_file(
                f'{project["project_id"]}_record_init',
                motion_file_text(project, zero_frames),
                hidden=True,
            )
            run_payload = self._run_payload(project, file_id, motion_ids, move_time)
            response = self._request_run_for_operation(
                'initialize', run_payload, 30.0, operation_generation, 'initializing'
            )
            if not response.get('success'):
                raise ValueError(response.get('message') or '초기 위치 이동 실패')
            deadline = time.monotonic() + max(15.0, move_time + 10.0)
            while time.monotonic() < deadline:
                with self._lock:
                    if operation_generation != self._operation_generation:
                        return
                    status = dict(self._motion_run_status)
                if status.get('state') == 'initialized':
                    break
                if status.get('state') == 'error':
                    raise ValueError(status.get('message') or '초기 위치 이동 실패')
                time.sleep(0.05)
            else:
                raise ValueError('초기 위치 도착 확인 시간 초과')
            if not self._countdown('녹화', operation_generation):
                return
            self._require_active_operation(operation_generation, 'initializing')
            midi_ready = self._request_midi('studio_recording_ready', {}, 5.0)
            if not midi_ready.get('success'):
                raise ValueError(midi_ready.get('message') or 'MIDI SELECT 잠금 해제 실패')
            midi_locked = False
            with self._lock:
                self._record_started = time.monotonic()
                self._record_frames = []
                self._recorded_motion_ids = set()
                self._set_status_locked(
                    'recording',
                    '모션 녹화 중 · MIDI SELECT로 움직이는 축을 자동 기록합니다',
                )
        except Exception as exc:
            with self._lock:
                if operation_generation == self._operation_generation:
                    self._set_status_locked('error', str(exc))
        finally:
            if midi_locked:
                self._request_midi('studio_recording_ready', {}, 2.0)

    def _wait_for_midi_faders_zero(self, timeout: float) -> None:
        """Block motor initialization until all physical MIDI faders are at zero."""
        deadline = time.monotonic() + max(0.1, float(timeout))
        last_message = 'MIDI 페이더 물리 0 복귀 확인 중'
        while time.monotonic() < deadline:
            response = self._request_midi(
                'studio_recording_zero_status', {}, min(2.0, timeout)
            )
            if not response.get('success'):
                raise ValueError(
                    response.get('message') or 'MIDI 페이더 0 위치 확인 실패'
                )
            if not response.get('device_connected', True):
                raise ValueError('MIDI 장치 연결이 끊겨 녹화를 시작할 수 없습니다')
            if response.get('ready'):
                return
            last_message = str(
                response.get('message') or 'MIDI 페이더 물리 0 복귀 확인 중'
            )
            with self._lock:
                if self._status.get('state') != 'initializing':
                    raise ValueError('녹화 초기화가 취소되었습니다')
                self._status['phase'] = 'midi_zero_wait'
                self._status['message'] = last_message
                self._status['updated_at'] = time.time()
            self._publish_status()
            time.sleep(0.05)
        raise ValueError(f'{last_message} · 제한 시간 초과로 모터 이동을 차단했습니다')

    def _record_tick(self) -> None:
        with self._lock:
            if self._status.get('state') != 'recording':
                return
            selected = self._selected_motion_values_locked()
            values = recording_values(selected, self._record_eligible_motion_ids)
            self._recorded_motion_ids.update(values)
            index = len(self._record_frames) + 1
            frame = {
                'frame': index,
                'time_sec': round(index * DEFAULT_PERIOD_SEC, 9),
                'values': values,
            }
            self._record_frames.append(frame)
            self._status['elapsed_sec'] = frame['time_sec']
            self._status['recorded_frames'] = index
            self._status['updated_at'] = time.time()

    def _finish_record_locked(self, message: str = '모션 녹화 완료') -> None:
        if not self._record_frames or not self._recorded_motion_ids:
            self._record_frames = []
            self._set_status_locked(
                'idle',
                '기록된 축이 없어 레이어를 만들지 않았습니다 · 녹화 중 MIDI SELECT 축을 움직이세요',
            )
            return
        project = self._require_project_locked()
        layers = project.setdefault('layers', [])
        layer_name = next_numbered_layer_name(
            layers, self._mode_label(self._record_mode)
        )
        layers.append({
            'layer_id': f'layer_{uuid.uuid4().hex[:8]}',
            'name': layer_name,
            'enabled': True,
            'locked': False,
            'created_at': time.time(),
            'frames': list(self._record_frames),
        })
        self._current_project = self._store.save_project(project)
        count = len(self._record_frames)
        motion_id_count = len(self._recorded_motion_ids)
        self._record_frames = []
        self._recorded_motion_ids = set()
        self._set_status_locked(
            'idle', f'{message} · {motion_id_count}개 축 · {count} 프레임 저장'
        )

    def _start_playback(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            self._require_idle_locked()
            project = self._require_project_locked()
            self._validate_mapping_locked(project)
            self._require_point_curve_consistency(project, '합성 미리보기')
            motion_ids = project_motion_ids(project)
            if not motion_ids:
                raise ValueError('재생할 모션 데이터가 없습니다')
            mapping = self._store.mapping_check(project)
            frames = render_project(
                project,
                motion_ids=motion_ids,
                motion_ranges_deg=self._motion_ranges(mapping),
                initial_motion_values_deg=self._manual_initial_values(mapping),
            )
            file_id = self._store.write_motion_file(
                f'{project["project_id"]}_preview', motion_file_text(project, frames), hidden=True
            )
            operation_generation = self._operation_machine().begin(
                str(self._status.get('state') or '')
            )
            self._set_status_locked('initializing', '합성 미리보기 초기 위치 이동 중')
            self._status['elapsed_sec'] = 0.0
            self._status['playback_duration_sec'] = max(
                (float(frame.get('time_sec') or 0.0) for frame in frames),
                default=0.0,
            )
            self._status['playback_layer_count'] = sum(
                1 for layer in project.get('layers', []) if layer.get('enabled', True)
            )
            self._status['runtime_progress'] = {}
            self._status['initialization_progress'] = {}
        thread = threading.Thread(
            target=self._prepare_playback,
            args=(
                project,
                file_id,
                motion_ids,
                float(payload.get('initial_move_time_sec') or 5.0),
                operation_generation,
            ),
            daemon=True,
        )
        thread.start()
        return {'success': True, 'message': '초기 위치 이동 후 합성 미리보기를 재생합니다'}

    def _start_initial_position(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Move to the composed project's initial position without starting playback."""
        with self._lock:
            self._require_idle_locked()
            project = self._require_project_locked()
            self._validate_mapping_locked(project)
            self._require_point_curve_consistency(project, '초기 위치 이동')
            mapping = self._store.mapping_check(project)
            motion_ids = project_motion_ids(project)
            if motion_ids:
                conflicts = layer_conflicts(project)
                if conflicts:
                    first = conflicts[0]
                    raise ValueError(
                        '초기 위치 계산 불가: '
                        f"{first['motion_id']} 레이어 시간이 겹칩니다"
                    )
                frames = [{
                    'frame': 1,
                    'time_sec': 0.0,
                    'values': project_initial_motion_values(project, motion_ids),
                }]
            else:
                motion_ids = list(mapping.get('motion_ids') or [])
                if not motion_ids:
                    raise ValueError('초기 위치를 계산할 모션축 설정이 없습니다')
                frames = [{
                    'frame': 1,
                    'time_sec': 0.0,
                    'values': {motion_id: 0.0 for motion_id in motion_ids},
                }]
            file_id = self._store.write_motion_file(
                f'{project["project_id"]}_initial_position',
                motion_file_text(project, frames),
                hidden=True,
            )
            move_time = float(payload.get('initial_move_time_sec') or 5.0)
            operation_generation = self._operation_machine().begin(
                str(self._status.get('state') or '')
            )
            self._set_status_locked('initializing', '초기 위치 이동 중')
            self._status['elapsed_sec'] = 0.0
            self._status['runtime_progress'] = {}
            self._status['initialization_progress'] = {}
        threading.Thread(
            target=self._prepare_initial_position,
            args=(project, file_id, motion_ids, move_time, operation_generation),
            daemon=True,
        ).start()
        return {'success': True, 'message': '초기 위치 이동을 시작합니다', 'status': self.snapshot()}

    def _prepare_initial_position(
        self,
        project: Dict[str, Any],
        file_id: str,
        motion_ids: List[str],
        move_time: float,
        operation_generation: int,
    ) -> None:
        try:
            self._require_active_operation(operation_generation, 'initializing')
            payload = self._run_payload(project, file_id, motion_ids, move_time)
            result = self._request_run_for_operation(
                'initialize', payload, 30.0, operation_generation, 'initializing'
            )
            if not result.get('success'):
                raise ValueError(result.get('message') or '초기 위치 이동 실패')
            deadline = time.monotonic() + max(15.0, move_time + 10.0)
            while time.monotonic() < deadline:
                with self._lock:
                    if operation_generation != self._operation_generation:
                        return
                    state = self._motion_run_status.get('state')
                    run_message = self._motion_run_status.get('message')
                if state == 'initialized':
                    with self._lock:
                        if operation_generation == self._operation_generation:
                            self._set_status_locked('idle', '초기 위치 이동 완료')
                    return
                if state == 'error':
                    raise ValueError(run_message or '초기 위치 이동 실패')
                time.sleep(0.05)
            raise ValueError('초기 위치 도착 확인 시간 초과')
        except Exception as exc:
            with self._lock:
                if operation_generation == self._operation_generation:
                    self._set_status_locked('error', str(exc))

    def _prepare_playback(
        self,
        project: Dict[str, Any],
        file_id: str,
        motion_ids: List[str],
        move_time: float,
        operation_generation: int,
    ) -> None:
        try:
            self._require_active_operation(operation_generation, 'initializing')
            payload = self._run_payload(project, file_id, motion_ids, move_time)
            result = self._request_run_for_operation(
                'initialize', payload, 30.0, operation_generation, 'initializing'
            )
            if not result.get('success'):
                raise ValueError(result.get('message') or '초기 위치 이동 실패')
            deadline = time.monotonic() + max(15.0, move_time + 10.0)
            while time.monotonic() < deadline:
                with self._lock:
                    if operation_generation != self._operation_generation:
                        return
                    state = self._motion_run_status.get('state')
                    run_message = self._motion_run_status.get('message')
                if state == 'initialized':
                    break
                if state == 'error':
                    raise ValueError(run_message or '초기 위치 이동 실패')
                time.sleep(0.05)
            else:
                raise ValueError('초기 위치 도착 확인 시간 초과')
            if not self._countdown('재생', operation_generation):
                return
            self._require_active_operation(operation_generation, 'initializing')
            result = self._request_run_for_operation(
                'start', payload, 5.0, operation_generation, 'initializing'
            )
            if not result.get('success'):
                raise ValueError(result.get('message') or '합성 미리보기 시작 실패')
            with self._lock:
                if operation_generation == self._operation_generation:
                    self._set_status_locked('playing', '레이어 합성 미리보기 재생 중')
        except Exception as exc:
            with self._lock:
                if operation_generation == self._operation_generation:
                    self._set_status_locked('error', str(exc))

    def _stop(self) -> Dict[str, Any]:
        with self._lock:
            state = self._status.get('state')
            stop_generation = self._operation_machine().cancel()
            project = None
            completion_message = '모션 스튜디오 정지 완료'
            if state == 'recording':
                self._finish_record_locked()
                completion_message = self._status['message']
                project = self._current_project
            self._set_status_locked('stopping', '정지 명령 전달 중')
            status = self.snapshot()
        threading.Thread(
            target=self._finish_stop,
            args=(stop_generation, completion_message),
            daemon=True,
        ).start()
        result = {
            'success': True,
            'message': '정지 명령을 즉시 전달했습니다',
            'status': status,
        }
        if project is not None:
            result['project'] = project
        return result

    def _finish_stop(self, stop_generation: int, completion_message: str) -> None:
        """Stop motor output first, then release recording-only MIDI state."""
        run_result = self._request_run('stop', {}, 3.0)
        midi_result = self._request_midi('studio_recording_ready', {}, 2.0)
        with self._lock:
            if stop_generation != self._operation_generation:
                return
            if not run_result.get('success'):
                self._set_status_locked(
                    'error',
                    str(run_result.get('message') or '모션 정지 명령 확인 실패'),
                )
                return
            if not midi_result.get('success'):
                completion_message = (
                    f'{completion_message} · MIDI 제어 복구 확인 필요: '
                    f'{midi_result.get("message") or "응답 없음"}'
                )
            self._set_status_locked('idle', completion_message)

    def _export(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            self._require_idle_locked()
            project = self._require_project_locked()
            self._validate_mapping_locked(project)
            self._require_point_curve_consistency(project, '모션 파일 내보내기')
            mapping = self._store.mapping_check(project)
            frames = render_project(
                project,
                motion_ranges_deg=self._motion_ranges(mapping),
                initial_motion_values_deg=self._manual_initial_values(mapping),
            )
            file_id = self._store.write_motion_file(
                payload.get('file_id') or project['name'], motion_file_text(project, frames)
            )
        return {
            'success': True,
            'message': '모션 파일 내보내기 완료',
            'file_id': file_id,
            'frame_count': len(frames),
        }

    def _update_layer(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        layer_id = str(payload.get('layer_id') or '')
        with self._lock:
            self._require_idle_locked()
            project = self._require_project_locked()
            layer = next((item for item in project.get('layers') or []
                          if item.get('layer_id') == layer_id), None)
            if layer is None:
                raise ValueError('레이어를 찾을 수 없습니다')
            if layer.get('locked') and payload.get('locked') is not False and any(
                key in payload for key in ('enabled', 'name')
            ):
                raise ValueError('잠긴 레이어는 재생 선택 상태나 이름을 변경할 수 없습니다')
            if 'enabled' in payload:
                layer['enabled'] = bool(payload['enabled'])
            if 'locked' in payload:
                layer['locked'] = bool(payload['locked'])
            if 'name' in payload:
                layer['name'] = (
                    str(payload.get('name') or '').strip()[:40] or layer['name']
                )
            self._current_project = self._store.save_project(project)
            project = self._current_project
        return self._project_result(project, '레이어 설정 저장 완료')

    def _create_layer(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            self._require_idle_locked()
            project = self._require_project_locked()
            layers = project.setdefault('layers', [])
            requested_name = str(payload.get('name') or '').strip()[:40]
            layer = normalize_layer({
                'layer_id': f'layer_{uuid.uuid4().hex[:8]}',
                'name': requested_name or next_numbered_layer_name(layers, '새 레이어'),
                'enabled': False,
                'locked': False,
                'created_at': time.time(),
                'edit_revision': 0,
                'point_curves': [],
                'frames': [],
            }, len(layers))
            layers.append(layer)
            self._current_project = self._store.save_project(project)
            project = self._current_project
        result = self._project_result(
            project,
            '빈 레이어를 생성했습니다 · 편집 후 재생 선택하세요',
        )
        result['layer_id'] = layer['layer_id']
        return result

    def _replace_layer_data(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        layer_id = str(payload.get('layer_id') or '')
        replacement = payload.get('layer')
        if not isinstance(replacement, dict):
            raise ValueError('저장할 레이어 데이터가 필요합니다')
        with self._lock:
            self._require_idle_locked()
            project = self._require_project_locked()
            index = next((
                order for order, item in enumerate(project.get('layers') or [])
                if str(item.get('layer_id') or '') == layer_id
            ), None)
            if index is None:
                raise ValueError('레이어를 찾을 수 없습니다')
            original = project['layers'][index]
            if original.get('locked'):
                raise ValueError('잠긴 레이어는 편집할 수 없습니다')
            try:
                original_revision = int(payload.get('original_revision') or 0)
            except (TypeError, ValueError) as exc:
                raise ValueError('원본 레이어 편집 버전이 올바르지 않습니다') from exc
            if original_revision != int(original.get('edit_revision') or 0):
                raise ValueError(
                    '편집 중 원본 레이어가 변경되었습니다. 편집 창을 다시 열어 작업하세요'
                )
            updated = dict(replacement)
            updated['layer_id'] = layer_id
            updated['enabled'] = original.get('enabled') is not False
            updated['locked'] = False
            updated['created_at'] = original.get('created_at')
            mapping = self._store.mapping_check(project)
            available_ids = set(mapping.get('motion_ids') or [])
            edited_ids = {
                str(motion_id)
                for frame in updated.get('frames') or []
                if isinstance(frame, dict)
                for motion_id in (frame.get('values') or {})
            }
            missing = sorted(edited_ids - available_ids)
            if missing:
                raise ValueError('모션축 설정에 없는 Motion ID: ' + ', '.join(missing))
            range_issues = validate_ranges(updated, self._motion_ranges(mapping))
            curve_mismatches = point_curve_frame_mismatches(updated)
            if curve_mismatches:
                first = curve_mismatches[0]
                raise ValueError(
                    f"{first['motion_id']} 포인트 곡선과 20ms 프레임이 다릅니다"
                )
            project['layers'][index] = updated
            self._current_project = self._store.save_project(project)
            project = self._current_project
        result = self._project_result(project, '편집한 레이어를 저장했습니다')
        result['range_warnings'] = range_issues
        return result

    def _delete_layer(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        layer_id = str(payload.get('layer_id') or '')
        with self._lock:
            self._require_idle_locked()
            project = self._require_project_locked()
            layer = next((
                item for item in project.get('layers') or []
                if str(item.get('layer_id') or '') == layer_id
            ), None)
            if layer is None:
                raise ValueError('레이어를 찾을 수 없습니다')
            if layer.get('locked'):
                raise ValueError('잠긴 레이어는 삭제할 수 없습니다')
            project['layers'] = [
                item for item in project.get('layers') or []
                if str(item.get('layer_id') or '') != layer_id
            ]
            self._current_project = self._store.save_project(project)
            project = self._current_project
        return self._project_result(project, '레이어를 삭제했습니다')

    def _duplicate_layer(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        layer_id = str(payload.get('layer_id') or '')
        with self._lock:
            self._require_idle_locked()
            project = self._require_project_locked()
            source = next((
                item for item in project.get('layers') or []
                if str(item.get('layer_id') or '') == layer_id
            ), None)
            if source is None:
                raise ValueError('복사할 레이어를 찾을 수 없습니다')
            duplicate = copy.deepcopy(source)
            duplicate['layer_id'] = f'layer_{uuid.uuid4().hex[:8]}'
            duplicate['name'] = (
                str(payload.get('name') or f"{source.get('name') or '레이어'} 복사본")
                .strip()[:40] or '레이어 복사본'
            )
            duplicate['enabled'] = False
            duplicate['locked'] = False
            duplicate['created_at'] = time.time()
            duplicate['copied_from_layer_id'] = layer_id
            duplicate['edit_revision'] = 0
            for curve in duplicate.get('point_curves') or []:
                curve['curve_id'] = f'curve_{uuid.uuid4().hex[:8]}'
                for point in curve.get('points') or []:
                    point['point_id'] = f'point_{uuid.uuid4().hex[:8]}'
            project.setdefault('layers', []).append(normalize_layer(duplicate))
            self._current_project = self._store.save_project(project)
            project = self._current_project
        result = self._project_result(
            project, '레이어를 독립 복사했습니다 · 재생 미선택 상태입니다'
        )
        result['layer_id'] = duplicate['layer_id']
        return result

    def _commit_merged_layer(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        source_ids = {
            str(value) for value in payload.get('source_layer_ids') or [] if str(value)
        }
        if len(source_ids) < 2:
            raise ValueError('합칠 원본 레이어 정보가 필요합니다')
        with self._lock:
            self._require_idle_locked()
            project = self._require_project_locked()
            sources = [
                item for item in project.get('layers') or []
                if str(item.get('layer_id') or '') in source_ids
            ]
            if len(sources) != len(source_ids):
                raise ValueError('합칠 원본 레이어를 찾을 수 없습니다')
            if any(item.get('locked') for item in sources):
                raise ValueError('잠긴 레이어는 합치기에 사용할 수 없습니다')
            provided = payload.get('layer')
            if not isinstance(provided, dict):
                raise ValueError('계산 노드가 만든 합성 미리보기 데이터가 필요합니다')
            expected_revisions = payload.get('source_revisions') or {}
            for item in sources:
                item_id = str(item.get('layer_id') or '')
                try:
                    expected = int(expected_revisions.get(item_id, -1))
                except (TypeError, ValueError) as exc:
                    raise ValueError('합칠 원본 레이어 버전이 올바르지 않습니다') from exc
                if expected != int(item.get('edit_revision') or 0):
                    raise ValueError('합성 미리보기 이후 원본 레이어가 변경되었습니다')
            mapping = self._store.mapping_check(project)
            merged = normalize_layer(copy.deepcopy(provided))
            if set(merged.get('source_layer_ids') or []) != source_ids:
                raise ValueError('합성 결과의 원본 레이어 정보가 일치하지 않습니다')
            range_issues = validate_ranges(merged, self._motion_ranges(mapping))
            merged = dict(merged)
            merged['layer_id'] = f'merged_{uuid.uuid4().hex[:8]}'
            merged['name'] = str(payload.get('name') or merged.get('name') or '합친 레이어')[:40]
            merged['source_layer_ids'] = sorted(source_ids)
            merged['enabled'] = False
            merged['locked'] = False
            project.setdefault('layers', []).append(merged)
            self._current_project = self._store.save_project(project)
            project = self._current_project
        result = self._project_result(
            project, '선택 레이어를 새 레이어로 합쳤습니다 · 결과는 재생 미선택 상태입니다'
        )
        result['layer_id'] = merged['layer_id']
        result['range_warnings'] = range_issues
        return result

    @staticmethod
    def _motion_ranges(mapping: Dict[str, Any]) -> Dict[str, tuple[float, float]]:
        return {
            str(row['motion_id']): (
                float(row.get('motion_lower_deg', -180.0)),
                float(row.get('motion_upper_deg', 180.0)),
            )
            for row in mapping.get('rows') or []
            if row.get('motion_id')
        }

    @staticmethod
    def _manual_initial_values(mapping: Dict[str, Any]) -> Dict[str, float]:
        return {
            str(row['motion_id']): float(row.get('initial_motion_position_deg', 0.0))
            for row in mapping.get('rows') or []
            if row.get('motion_id') and str(row.get('initial_mode') or 'first_frame') == 'manual'
        }

    def _run_payload(
        self, project: Dict[str, Any], file_id: str, motion_ids: List[str], move_time: float
    ) -> Dict[str, Any]:
        return {
            'project_id': self._workspace_project_id,
            'context_id': self._execution_context.get('context_id', ''),
            'project_generation': self._context_generation(),
            'request_source': 'motion_studio',
            'motion_file_id': file_id,
            'mapping_file_id': project['mapping_file_id'],
            'active_motion_ids': motion_ids,
            'initial_move_time_sec': move_time,
        }

    def _context_generation(self) -> int:
        try:
            return int(self._execution_context.get('project_generation') or 0)
        except (AttributeError, TypeError, ValueError):
            return 0

    def _response_generation_matches(self, payload: Any) -> bool:
        if not isinstance(payload, dict):
            return False
        try:
            return int(payload.get('project_generation')) == self._context_generation()
        except (TypeError, ValueError):
            return False

    def _request_run(self, command: str, payload: Dict[str, Any], timeout: float) -> Dict[str, Any]:
        return self._gateway().request_run(command, payload, timeout)

    def _request_run_for_operation(
        self,
        command: str,
        payload: Dict[str, Any],
        timeout: float,
        operation_generation: int,
        expected_state: str,
    ) -> Dict[str, Any]:
        return self._gateway().request_run_for_operation(
            command,
            payload,
            timeout,
            operation_generation,
            expected_state,
        )

    def _wait_for_run_result(self, request_id: str, timeout: float) -> Dict[str, Any]:
        return self._gateway().wait_for_run_result(request_id, timeout)

    def _request_midi(self, command: str, payload: Dict[str, Any], timeout: float) -> Dict[str, Any]:
        return self._gateway().request_midi(command, payload, timeout)

    def _gateway(self) -> StudioRosGateway:
        gateway = getattr(self, '_ros_gateway', None)
        if gateway is None:
            gateway = StudioRosGateway(self)
            self._ros_gateway = gateway
        return gateway

    def _require_active_operation(
        self, operation_generation: int, expected_state: str
    ) -> None:
        with self._lock:
            self._operation_machine().require_active(
                operation_generation,
                str(self._status.get('state') or ''),
                expected_state,
            )

    def _countdown(self, action_label: str, operation_generation: int) -> bool:
        for count in (3, 2, 1):
            with self._lock:
                if (
                    operation_generation != self._operation_generation
                    or self._status.get('state') != 'initializing'
                ):
                    return False
                self._status['message'] = f'{action_label} 시작 {count}초 전'
                self._status['phase'] = 'countdown'
                self._status['updated_at'] = time.time()
            self._publish_status()
            time.sleep(1.0)
        return True

    def _selected_motion_values_locked(self) -> Dict[str, float]:
        result = {}
        for channel in self._midi_state.get('channels') or []:
            if (
                not isinstance(channel, dict)
                or not channel.get('control_enabled')
                or channel.get('motion_group_valid') is False
                or channel.get('motion_command_valid') is False
            ):
                continue
            linked_values = channel.get('motion_values_deg')
            if isinstance(linked_values, dict):
                for motion_id, value in linked_values.items():
                    motion_id = str(motion_id or '')
                    if motion_id and isinstance(value, (int, float)):
                        result[motion_id] = float(value)
                if linked_values:
                    continue
            motion_id = str(channel.get('motion_id') or '')
            value = channel.get('motion_value_deg')
            if motion_id and isinstance(value, (int, float)):
                result[motion_id] = float(value)
        return result

    def _validate_mapping_locked(self, project: Dict[str, Any]) -> None:
        check = self._store.mapping_check(project)
        if not check['matches_project']:
            raise ValueError('모션축 설정 파일이 변경되었습니다. 통합 프로젝트를 다시 여세요')

    def _require_project_locked(self) -> Dict[str, Any]:
        if self._current_project is None:
            raise ValueError('먼저 왼쪽에서 통합 프로젝트를 선택하세요')
        return self._current_project

    def _require_idle_locked(self) -> None:
        self._operation_machine().require_idle(
            str(self._status.get('state') or '')
        )

    def _operation_machine(self) -> StudioOperationStateMachine:
        machine = getattr(self, '_operation_state', None)
        if machine is None:
            machine = StudioOperationStateMachine()
            self._operation_state = machine
        return machine

    @property
    def _operation_generation(self) -> int:
        return self._operation_machine().generation

    @_operation_generation.setter
    def _operation_generation(self, value: int) -> None:
        self._operation_state = StudioOperationStateMachine(value)

    def _set_status_locked(self, state: str, message: str) -> None:
        self._status.update({
            'state': state,
            'phase': state,
            'message': message,
            'updated_at': time.time(),
        })
        if state not in {'recording'}:
            self._status['elapsed_sec'] = 0.0
        project = self._current_project
        self._status['project'] = (
            self._store.summary(project) if project is not None else None
        )
        self._status['selected_motion_ids'] = list(self._selected_motion_values_locked())
        self._status['recording_motion_ids'] = sorted(self._recorded_motion_ids)
        self._status['record_mode'] = self._record_mode if state == 'recording' else None

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            result = dict(self._status)
            project = self._current_project
            result['project'] = self._store.summary(project) if project else None
            result['selected_motion_ids'] = list(self._selected_motion_values_locked())
            result['recording_motion_ids'] = sorted(self._recorded_motion_ids)
            result['recorded_frames'] = len(self._record_frames)
            result['execution_context'] = {
                **getattr(self, '_execution_context', {}),
                'ready': bool(getattr(self, '_execution_context_ready', False)),
            }
            if result.get('state') == 'recording':
                result['elapsed_sec'] = round(len(self._record_frames) * DEFAULT_PERIOD_SEC, 3)
                preview_limit = 240
                frame_count = len(self._record_frames)
                stride = max(1, (frame_count + preview_limit - 1) // preview_limit)
                preview_frames = self._record_frames[::stride]
                if self._record_frames and preview_frames[-1] is not self._record_frames[-1]:
                    preview_frames = [*preview_frames, self._record_frames[-1]]
                result['recording_preview_frames'] = [
                    {
                        'time_sec': float(frame.get('time_sec') or 0.0),
                        'values': dict(frame.get('values') or {}),
                    }
                    for frame in preview_frames
                ]
                result['recording_preview_stride'] = stride
            return result

    def _publish_status(self) -> None:
        self._publish_json(self._status_pub, self.snapshot())

    @staticmethod
    def _publish_json(publisher: Any, payload: Dict[str, Any]) -> None:
        publisher.publish(String(data=json.dumps(payload, ensure_ascii=False, separators=(',', ':'))))

    @staticmethod
    def _mode_label(mode: str) -> str:
        return {'overdub': '오버더빙', 'append': '이어 녹화'}.get(mode, '녹화')


def main(args: List[str] | None = None) -> None:
    rclpy.init(args=args)
    node = MotionStudioNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()
