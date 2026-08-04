"""ROS boundary for independent motion-data recording and project management."""

from __future__ import annotations

import json
import os
import threading
import time
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional

import rclpy
from rclpy.node import Node
from std_msgs.msg import String

from .export_service import StudioExportService
from .layer_commands import (
    StudioLayerCommands,
    next_numbered_layer_name,
)
from .operation_state import StudioOperationStateMachine
from .constants import DEFAULT_PERIOD_SEC
from .mapping_model import manual_initial_values, motion_ranges
from .motion_model import layer_motion_ids
from .playback_session import StudioPlaybackSession, project_initial_motion_values
from .project_commands import StudioProjectCommands
from .project_store import ProjectStore
from .recording_session import StudioRecordingSession
from .ros_gateway import StudioRosGateway
from .workspace_session import StudioWorkspaceSession


DEFAULT_MOTION_PROJECTS_DIR = str(
    Path(os.environ.get('MOTION_WORKSPACE', Path.cwd())).expanduser()
    / 'motion_projects'
)


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
        self._composition_cache_project_id = ''
        self._composition_cache: Dict[str, Any] = {}
        self._workspace_catalog_cache: Optional[Dict[str, Any]] = None
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
        self._layer_commands = StudioLayerCommands(self)
        self._recording_session = StudioRecordingSession(self)
        self._playback_session = StudioPlaybackSession(self)
        self._workspace_session = StudioWorkspaceSession(self)
        self._export_service = StudioExportService(self)
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
                if payload.get('request_source') == 'motion_studio':
                    try:
                        operation_generation = int(
                            payload.get('operation_generation') or 0
                        )
                    except (TypeError, ValueError):
                        return
                    if (
                        operation_generation
                        and operation_generation != self._operation_generation
                    ):
                        return
                self._motion_run_status = payload
                studio_state = str(self._status.get('state') or '')
                run_state = str(payload.get('state') or '')
                progress = payload.get('progress')
                if (
                    payload.get('request_source') == 'motion_studio'
                    and studio_state == 'initializing'
                    and run_state in {'running', 'verifying'}
                ):
                    self._set_status_locked(
                        'playing',
                        '레이어 합성 미리보기 재생 중',
                    )
                    studio_state = 'playing'
                elif (
                    payload.get('request_source') == 'motion_studio'
                    and studio_state == 'initializing'
                    and run_state == 'countdown'
                ):
                    self._status['phase'] = 'countdown'
                    self._status['message'] = str(
                        payload.get('message') or '모션 시작 대기'
                    )
                if (
                    payload.get('request_source') == 'motion_studio'
                    and studio_state in {'initializing', 'playing', 'stopping'}
                    and isinstance(progress, dict)
                ):
                    self._status['runtime_progress'] = dict(progress)
                    self._status['updated_at'] = time.time()
                    if studio_state == 'initializing' and run_state in {'initializing', 'initialized'}:
                        self._status['initialization_progress'] = dict(progress)
                    elif studio_state == 'initializing' and run_state == 'countdown':
                        self._status['countdown_progress'] = dict(progress)
                    elif studio_state == 'playing' and run_state in {'running', 'verifying'}:
                        self._status['elapsed_sec'] = float(progress.get('elapsed_sec') or 0.0)
                        self._status['playback_duration_sec'] = float(
                            progress.get('duration_sec')
                            or self._status.get('playback_duration_sec')
                            or 0.0
                        )
                if (
                    studio_state in {'initializing', 'playing'}
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
            return self._workspace().confirm_execution_context(payload)
        if command == 'invalidate_context':
            return self._workspace().invalidate()
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
        return self._workspace().apply_execution_context(payload)

    def _require_execution_context(self) -> None:
        self._workspace().require_execution_context()

    def _select_workspace(self, payload: Dict[str, Any]) -> None:
        self._workspace().select(payload)

    def _project_composition(
        self,
        project: Dict[str, Any],
        mapping: Dict[str, Any],
        *,
        affected_motion_ids: set[str] | None = None,
        affected_layer_ids: set[str] | None = None,
    ) -> Dict[str, Any]:
        return self._workspace().composition(
            project,
            mapping,
            affected_motion_ids=affected_motion_ids,
            affected_layer_ids=affected_layer_ids,
        )

    def _project_result(
        self,
        project: Dict[str, Any],
        message: str = '완료',
        *,
        affected_motion_ids: set[str] | None = None,
        affected_layer_ids: set[str] | None = None,
    ) -> Dict[str, Any]:
        return self._workspace().project_result(
            project,
            message,
            affected_motion_ids=affected_motion_ids,
            affected_layer_ids=affected_layer_ids,
        )

    @staticmethod
    def _require_point_curve_consistency(project: Dict[str, Any], action: str) -> None:
        StudioWorkspaceSession.require_point_curve_consistency(
            project, action
        )

    def _start_record(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self._recording().start(payload)

    def _prepare_record(self, move_time: float, operation_generation: int) -> None:
        self._recording().prepare(move_time, operation_generation)

    def _wait_for_midi_faders_zero(self, timeout: float) -> None:
        self._recording().wait_for_midi_faders_zero(timeout)

    def _record_tick(self) -> None:
        self._recording().record_tick()

    def _finish_record_locked(self, message: str = '모션 녹화 완료') -> str:
        return self._recording().finish_locked(message)

    def _start_playback(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self._playback().start_playback(payload)

    def _start_initial_position(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self._playback().start_initial_position(payload)

    def _prepare_initial_position(
        self,
        project: Dict[str, Any],
        file_id: str,
        motion_ids: List[str],
        move_time: float,
        operation_generation: int,
    ) -> None:
        self._playback().prepare_initial_position(
            project, file_id, motion_ids, move_time, operation_generation
        )

    def _prepare_playback(
        self,
        project: Dict[str, Any],
        file_id: str,
        motion_ids: List[str],
        move_time: float,
        operation_generation: int,
    ) -> None:
        self._playback().prepare_playback(
            project, file_id, motion_ids, move_time, operation_generation
        )

    def _stop(self) -> Dict[str, Any]:
        return self._playback().stop()

    def _finish_stop(self, stop_generation: int, completion_message: str) -> None:
        self._playback().finish_stop(
            stop_generation, completion_message
        )

    def _export(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self._exporter().export(payload)

    def _update_layer(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self._layers().update(payload)

    def _create_layer(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self._layers().create(payload)

    def _replace_layer_data(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self._layers().replace_data(payload)

    def _delete_layer(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self._layers().delete(payload)

    def _duplicate_layer(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self._layers().duplicate(payload)

    def _commit_merged_layer(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self._layers().commit_merged(payload)

    _motion_ranges = staticmethod(motion_ranges)
    _manual_initial_values = staticmethod(manual_initial_values)

    def _run_payload(
        self, project: Dict[str, Any], file_id: str, motion_ids: List[str], move_time: float
    ) -> Dict[str, Any]:
        return self._playback().run_payload(project, file_id, motion_ids, move_time)

    def _recording(self) -> StudioRecordingSession:
        return self._service('_recording_session', StudioRecordingSession)

    def _playback(self) -> StudioPlaybackSession:
        return self._service('_playback_session', StudioPlaybackSession)

    def _layers(self) -> StudioLayerCommands:
        return self._service('_layer_commands', StudioLayerCommands)

    def _workspace(self) -> StudioWorkspaceSession:
        return self._service('_workspace_session', StudioWorkspaceSession)

    def _exporter(self) -> StudioExportService:
        return self._service('_export_service', StudioExportService)

    def _service(self, attribute: str, factory: Any) -> Any:
        service = getattr(self, attribute, None)
        if service is None:
            service = factory(self)
            setattr(self, attribute, service)
        return service

    def _context_generation(self) -> int:
        return self._workspace().context_generation()

    def _response_generation_matches(self, payload: Any) -> bool:
        return self._workspace().response_generation_matches(payload)

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
        return self._recording().selected_motion_values_locked()

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
        if state in {'idle', 'error'}:
            self._status['runtime_progress'] = {}
            self._status['initialization_progress'] = {}
            self._status['countdown_progress'] = {}
            self._status['playback_duration_sec'] = 0.0
            self._status['playback_layer_count'] = 0
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
            result['execution_context'] = {
                **getattr(self, '_execution_context', {}),
                'ready': bool(getattr(self, '_execution_context_ready', False)),
            }
            self._recording().update_snapshot(result)
            return result

    def _publish_status(self) -> None:
        self._publish_json(self._status_pub, self.snapshot())

    @staticmethod
    def _publish_json(publisher: Any, payload: Dict[str, Any]) -> None:
        publisher.publish(String(data=json.dumps(payload, ensure_ascii=False, separators=(',', ':'))))

    @staticmethod
    def _mode_label(mode: str) -> str:
        return StudioRecordingSession.mode_label(mode)


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
