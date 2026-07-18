"""ROS boundary for independent motion-data recording and project management."""

from __future__ import annotations

import json
import os
import threading
import time
import traceback
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

import rclpy
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node
from std_msgs.msg import String

from .project_store import DEFAULT_PERIOD_SEC, ProjectStore
from .timeline import (
    layer_conflicts,
    motion_file_text,
    project_motion_ids,
    recording_values,
    render_project,
)


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
        self._status = self._empty_status()

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
                self._midi_state = payload

    def _run_response_callback(self, msg: String) -> None:
        try:
            payload = json.loads(msg.data)
        except json.JSONDecodeError:
            return
        request_id = str(payload.get('request_id') or '') if isinstance(payload, dict) else ''
        if request_id:
            with self._lock:
                self._run_results[request_id] = payload

    def _midi_response_callback(self, msg: String) -> None:
        try:
            payload = json.loads(msg.data)
        except json.JSONDecodeError:
            return
        request_id = str(payload.get('request_id') or '') if isinstance(payload, dict) else ''
        if request_id:
            with self._lock:
                self._midi_results[request_id] = payload

    def _run_status_callback(self, msg: String) -> None:
        try:
            payload = json.loads(msg.data)
        except json.JSONDecodeError:
            return
        if isinstance(payload, dict):
            with self._lock:
                self._motion_run_status = payload
                if (
                    self._status.get('state') == 'playing'
                    and payload.get('request_source') == 'motion_studio'
                    and payload.get('state') in {'completed', 'error', 'stopped'}
                ):
                    self._set_status_locked(
                        'idle' if payload.get('state') != 'error' else 'error',
                        str(payload.get('message') or '합성 미리보기 종료'),
                    )

    def _request_callback(self, msg: String) -> None:
        try:
            request = json.loads(msg.data)
        except json.JSONDecodeError:
            return
        if not isinstance(request, dict):
            return
        request_id = str(request.get('request_id') or '')
        command = str(request.get('command') or 'status').strip()
        payload = request.get('payload') if isinstance(request.get('payload'), dict) else {}
        try:
            result = self._handle(command, payload)
        except Exception as exc:
            self.get_logger().error(f'studio command failed: {command}\n{traceback.format_exc()}')
            result = {'success': False, 'message': str(exc)}
        result['request_id'] = request_id
        self._publish_json(self._response_pub, result)
        self._publish_status()

    def _handle(self, command: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        if command == 'status':
            self._select_workspace(payload)
            return self.snapshot()
        self._select_workspace(payload)
        if command == 'list':
            with self._lock:
                current_project = self._current_project
            return {
                'success': True,
                'projects': self._store.list_projects(),
                'mappings': self._store.list_mappings(),
                'motion_files': self._store.list_motion_files(),
                'project': current_project,
                'status': self.snapshot(),
            }
        if command == 'open_workspace':
            with self._lock:
                self._require_idle_locked()
            workspace_project_id = str(payload.get('workspace_project_id') or '').strip()
            if not workspace_project_id:
                raise ValueError('통합 프로젝트 ID가 필요합니다')
            mapping = self._store.inspect_mapping(payload.get('mapping_file_id'))
            summary = next(
                (
                    item for item in self._store.list_projects()
                    if item.get('workspace_project_id') == workspace_project_id
                ),
                None,
            )
            if summary:
                project = self._store.load_project(summary['project_id'])
            else:
                project = self._store.create_project(
                    payload.get('name'), mapping['file_id']
                )
            project['workspace_project_id'] = workspace_project_id
            project['name'] = str(payload.get('name') or project['name']).strip() or project['name']
            project['mapping_file_id'] = mapping['file_id']
            project['mapping_sha256'] = mapping['sha256']
            project['layers'] = [
                dict(layer) for layer in payload.get('layers') or []
                if isinstance(layer, dict)
            ]
            project = self._store.save_project(project)
            with self._lock:
                self._current_project = project
                self._set_status_locked('idle', '통합 프로젝트를 모션 스튜디오에 연결했습니다')
            result = self._project_result(project, '통합 프로젝트 연결 완료')
            result.update({
                'projects': self._store.list_projects(),
                'mappings': self._store.list_mappings(),
                'motion_files': self._store.list_motion_files(),
            })
            return result
        if command == 'create':
            with self._lock:
                self._require_idle_locked()
            project = self._store.create_project(payload.get('name'), payload.get('mapping_file_id'))
            with self._lock:
                self._current_project = project
                self._set_status_locked('idle', '새 모션 프로젝트를 만들었습니다')
            return {'success': True, 'project': project, 'status': self.snapshot()}
        if command == 'load':
            project = self._store.load_project(payload.get('project_id'))
            with self._lock:
                self._require_idle_locked()
                self._current_project = project
                self._set_status_locked('idle', '모션 프로젝트를 불러왔습니다')
            return self._project_result(project)
        if command == 'import_motion_file':
            with self._lock:
                self._require_idle_locked()
            project = self._store.import_motion_file(
                payload.get('motion_file_id'),
                payload.get('mapping_file_id'),
                payload.get('name'),
            )
            with self._lock:
                self._current_project = project
                self._set_status_locked(
                    'idle', '모션 파일을 단일 레이어 프로젝트로 가져왔습니다'
                )
            return self._project_result(
                project, '모션 파일 가져오기 완료 · 단일 레이어로 변환했습니다'
            )
        if command == 'import_motion_layer':
            with self._lock:
                self._require_idle_locked()
                project = self._require_project_locked()
                project = self._store.append_motion_file(
                    project, payload.get('motion_file_id')
                )
                self._current_project = project
                self._set_status_locked('idle', '모션 파일을 현재 프로젝트 레이어로 가져왔습니다')
            return self._project_result(project, '모션 파일 레이어 가져오기 완료')
        if command == 'save':
            with self._lock:
                self._require_idle_locked()
                project = self._require_project_locked()
                if 'name' in payload:
                    project['name'] = str(payload.get('name') or '').strip() or project['name']
                project = self._store.save_project(project)
                self._current_project = project
            return self._project_result(project, '프로젝트 저장 완료')
        if command == 'update_layer':
            return self._update_layer(payload)
        if command == 'clear_layers':
            with self._lock:
                self._require_idle_locked()
                project = self._require_project_locked()
                project['layers'] = []
                self._current_project = self._store.save_project(project)
                project = self._current_project
                self._set_status_locked('idle', '레이어를 전체 초기화했습니다')
            return self._project_result(project, '레이어 전체 초기화 완료')
        if command == 'delete':
            with self._lock:
                self._require_idle_locked()
                project = self._require_project_locked()
                self._store.delete_project(project['project_id'])
                self._current_project = None
                self._set_status_locked('idle', '프로젝트를 삭제했습니다')
            return {'success': True, 'message': '프로젝트 삭제 완료'}
        if command == 'record':
            return self._start_record(payload)
        if command == 'play':
            return self._start_playback(payload)
        if command == 'stop':
            return self._stop()
        if command == 'export':
            return self._export(payload)
        raise ValueError(f'지원하지 않는 모션 스튜디오 명령: {command}')

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
        conflicts = layer_conflicts(project)
        return {
            'success': True,
            'message': message,
            'project': project,
            'mapping': self._store.mapping_check(project),
            'status': self.snapshot(),
            'composition': {
                'conflicts': conflicts,
                'conflict_free': not conflicts,
            },
        }

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
            self._set_status_locked('initializing', '초기 위치 이동 준비 중')
        thread = threading.Thread(
            target=self._prepare_record,
            args=(float(payload.get('initial_move_time_sec') or 5.0),),
            daemon=True,
        )
        thread.start()
        return {'success': True, 'message': '자동 초기 위치 이동을 시작합니다', 'status': self.snapshot()}

    def _prepare_record(self, move_time: float) -> None:
        midi_locked = False
        try:
            midi_prepare = self._request_midi('studio_recording_prepare', {}, 5.0)
            midi_locked = True
            if not midi_prepare.get('success'):
                raise ValueError(
                    midi_prepare.get('message') or 'MIDI 녹화 초기화 준비 실패'
                )
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
            response = self._request_run('initialize', run_payload, 30.0)
            if not response.get('success'):
                raise ValueError(response.get('message') or '초기 위치 이동 실패')
            deadline = time.monotonic() + max(15.0, move_time + 10.0)
            while time.monotonic() < deadline:
                with self._lock:
                    status = dict(self._motion_run_status)
                if status.get('state') == 'initialized':
                    break
                if status.get('state') == 'error':
                    raise ValueError(status.get('message') or '초기 위치 이동 실패')
                time.sleep(0.05)
            else:
                raise ValueError('초기 위치 도착 확인 시간 초과')
            if not self._countdown('녹화'):
                return
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
                self._set_status_locked('error', str(exc))
        finally:
            if midi_locked:
                self._request_midi('studio_recording_ready', {}, 2.0)

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
        layer_number = len(project.get('layers') or []) + 1
        project.setdefault('layers', []).append({
            'layer_id': f'layer_{uuid.uuid4().hex[:8]}',
            'name': f'{self._mode_label(self._record_mode)} {layer_number}',
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
            motion_ids = project_motion_ids(project)
            if not motion_ids:
                raise ValueError('재생할 모션 데이터가 없습니다')
            frames = render_project(project, motion_ids=motion_ids)
            file_id = self._store.write_motion_file(
                f'{project["project_id"]}_preview', motion_file_text(project, frames), hidden=True
            )
            self._set_status_locked('initializing', '합성 미리보기 초기 위치 이동 중')
        thread = threading.Thread(
            target=self._prepare_playback,
            args=(project, file_id, motion_ids, float(payload.get('initial_move_time_sec') or 5.0)),
            daemon=True,
        )
        thread.start()
        return {'success': True, 'message': '초기 위치 이동 후 합성 미리보기를 재생합니다'}

    def _prepare_playback(
        self, project: Dict[str, Any], file_id: str, motion_ids: List[str], move_time: float
    ) -> None:
        try:
            payload = self._run_payload(project, file_id, motion_ids, move_time)
            result = self._request_run('initialize', payload, 30.0)
            if not result.get('success'):
                raise ValueError(result.get('message') or '초기 위치 이동 실패')
            deadline = time.monotonic() + max(15.0, move_time + 10.0)
            while time.monotonic() < deadline:
                with self._lock:
                    state = self._motion_run_status.get('state')
                    run_message = self._motion_run_status.get('message')
                if state == 'initialized':
                    break
                if state == 'error':
                    raise ValueError(run_message or '초기 위치 이동 실패')
                time.sleep(0.05)
            else:
                raise ValueError('초기 위치 도착 확인 시간 초과')
            if not self._countdown('재생'):
                return
            result = self._request_run('start', payload, 5.0)
            if not result.get('success'):
                raise ValueError(result.get('message') or '합성 미리보기 시작 실패')
            with self._lock:
                self._set_status_locked('playing', '레이어 합성 미리보기 재생 중')
        except Exception as exc:
            with self._lock:
                self._set_status_locked('error', str(exc))

    def _stop(self) -> Dict[str, Any]:
        with self._lock:
            state = self._status.get('state')
            if state == 'recording':
                self._finish_record_locked()
                return {
                    'success': True,
                    'message': self._status['message'],
                    'project': self._current_project,
                    'status': self.snapshot(),
                }
            self._set_status_locked('stopping', '정지 요청 중')
        self._request_midi('studio_recording_ready', {}, 2.0)
        result = self._request_run('stop', {}, 3.0)
        with self._lock:
            self._set_status_locked('idle', '모션 스튜디오 정지 완료')
        return {'success': bool(result.get('success', True)), 'message': '정지 완료', 'status': self.snapshot()}

    def _export(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            self._require_idle_locked()
            project = self._require_project_locked()
            self._validate_mapping_locked(project)
            frames = render_project(project)
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
            if 'enabled' in payload:
                layer['enabled'] = bool(payload['enabled'])
            if 'locked' in payload:
                layer['locked'] = bool(payload['locked'])
            if 'name' in payload:
                layer['name'] = str(payload.get('name') or '').strip() or layer['name']
            self._current_project = self._store.save_project(project)
            project = self._current_project
        return self._project_result(project, '레이어 설정 저장 완료')

    def _run_payload(
        self, project: Dict[str, Any], file_id: str, motion_ids: List[str], move_time: float
    ) -> Dict[str, Any]:
        return {
            'project_id': self._workspace_project_id,
            'request_source': 'motion_studio',
            'motion_file_id': file_id,
            'mapping_file_id': project['mapping_file_id'],
            'active_motion_ids': motion_ids,
            'initial_move_time_sec': move_time,
        }

    def _request_run(self, command: str, payload: Dict[str, Any], timeout: float) -> Dict[str, Any]:
        request_id = f'studio-run-{time.time_ns()}'
        self._publish_json(self._request_pub, {
            'request_id': request_id, 'command': command, 'payload': payload
        })
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._lock:
                result = self._run_results.pop(request_id, None)
            if result is not None:
                return result
            time.sleep(0.02)
        return {'success': False, 'message': 'motion_run_manager 응답 시간 초과'}

    def _request_midi(self, command: str, payload: Dict[str, Any], timeout: float) -> Dict[str, Any]:
        request_id = f'studio-midi-{time.time_ns()}'
        payload = dict(payload)
        payload['project_id'] = self._workspace_project_id
        self._publish_json(self._midi_request_pub, {
            'request_id': request_id, 'command': command, 'payload': payload
        })
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._lock:
                result = self._midi_results.pop(request_id, None)
            if result is not None:
                return result
            time.sleep(0.02)
        return {'success': False, 'message': 'midi_control_node 응답 시간 초과'}

    def _countdown(self, action_label: str) -> bool:
        for count in (3, 2, 1):
            with self._lock:
                if self._status.get('state') != 'initializing':
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
            if not isinstance(channel, dict) or not channel.get('control_enabled'):
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
        if self._status.get('state') not in {'idle', 'error'}:
            raise ValueError('녹화 또는 재생 중에는 프로젝트를 변경할 수 없습니다')

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
            if result.get('state') == 'recording':
                result['elapsed_sec'] = round(len(self._record_frames) * DEFAULT_PERIOD_SEC, 3)
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
    executor = MultiThreadedExecutor(num_threads=4)
    executor.add_node(node)
    try:
        executor.spin()
    except KeyboardInterrupt:
        pass
    finally:
        executor.shutdown()
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()
