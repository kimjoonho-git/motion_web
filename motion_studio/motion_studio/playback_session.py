"""Playback and initial-position runtime orchestration."""

from __future__ import annotations

import threading
import time
from typing import Any, Dict, List

from .timeline import (
    layer_conflicts,
    motion_file_text,
    project_motion_ids,
    render_project,
)


def project_initial_motion_values(
    project: Dict[str, Any], motion_ids: List[str]
) -> Dict[str, float]:
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
        for motion_id in motion_ids if motion_id in earliest
    }


class StudioPlaybackSession:
    def __init__(self, studio: Any) -> None:
        self.studio = studio

    def run_payload(
        self,
        project: Dict[str, Any],
        file_id: str,
        motion_ids: List[str],
        move_time: float,
    ) -> Dict[str, Any]:
        studio = self.studio
        return {
            'project_id': studio._workspace_project_id,
            'context_id': studio._execution_context.get('context_id', ''),
            'project_generation': studio._context_generation(),
            'request_source': 'motion_studio',
            'motion_file_id': file_id,
            'mapping_file_id': project['mapping_file_id'],
            'active_motion_ids': motion_ids,
            'initial_move_time_sec': move_time,
        }

    def start_playback(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        studio = self.studio
        with studio._lock:
            studio._require_idle_locked()
            project = studio._require_project_locked()
            studio._validate_mapping_locked(project)
            studio._require_point_curve_consistency(project, '합성 미리보기')
            motion_ids = project_motion_ids(project)
            if not motion_ids:
                raise ValueError('재생할 모션 데이터가 없습니다')
            mapping = studio._store.mapping_check(project)
            frames = render_project(
                project,
                motion_ids=motion_ids,
                motion_ranges_deg=studio._motion_ranges(mapping),
                initial_motion_values_deg=studio._manual_initial_values(mapping),
            )
            file_id = studio._store.write_motion_file(
                f'{project["project_id"]}_preview',
                motion_file_text(project, frames),
                hidden=True,
            )
            operation_generation = studio._operation_machine().begin(
                str(studio._status.get('state') or '')
            )
            studio._set_status_locked('initializing', '합성 미리보기 초기 위치 이동 중')
            studio._status.update({
                'elapsed_sec': 0.0,
                'playback_duration_sec': max(
                    (float(frame.get('time_sec') or 0.0) for frame in frames),
                    default=0.0,
                ),
                'playback_layer_count': sum(
                    1 for layer in project.get('layers', []) if layer.get('enabled', True)
                ),
                'runtime_progress': {},
                'initialization_progress': {},
            })
        threading.Thread(
            target=self.prepare_playback,
            args=(
                project,
                file_id,
                motion_ids,
                float(payload.get('initial_move_time_sec') or 5.0),
                operation_generation,
            ),
            daemon=True,
        ).start()
        return {'success': True, 'message': '초기 위치 이동 후 합성 미리보기를 재생합니다'}

    def prepare_playback(
        self,
        project: Dict[str, Any],
        file_id: str,
        motion_ids: List[str],
        move_time: float,
        operation_generation: int,
    ) -> None:
        studio = self.studio
        try:
            studio._require_active_operation(operation_generation, 'initializing')
            payload = {
                **studio._run_payload(project, file_id, motion_ids, move_time),
                'countdown_sec': 3.0,
            }
            result = studio._request_run_for_operation(
                'start', payload, 5.0, operation_generation, 'initializing'
            )
            if not result.get('success'):
                raise ValueError(result.get('message') or '합성 미리보기 시작 실패')
        except Exception as exc:
            with studio._lock:
                if operation_generation == studio._operation_generation:
                    studio._set_status_locked('error', str(exc))

    def start_initial_position(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        studio = self.studio
        with studio._lock:
            studio._require_idle_locked()
            project = studio._require_project_locked()
            studio._validate_mapping_locked(project)
            studio._require_point_curve_consistency(project, '초기 위치 이동')
            mapping = studio._store.mapping_check(project)
            motion_ids = project_motion_ids(project)
            if motion_ids:
                conflicts = layer_conflicts(project)
                if conflicts:
                    raise ValueError(
                        '초기 위치 계산 불가: '
                        f"{conflicts[0]['motion_id']} 레이어 시간이 겹칩니다"
                    )
                values = project_initial_motion_values(project, motion_ids)
            else:
                motion_ids = list(mapping.get('motion_ids') or [])
                if not motion_ids:
                    raise ValueError('초기 위치를 계산할 모션축 설정이 없습니다')
                values = {motion_id: 0.0 for motion_id in motion_ids}
            frames = [{'frame': 1, 'time_sec': 0.0, 'values': values}]
            file_id = studio._store.write_motion_file(
                f'{project["project_id"]}_initial_position',
                motion_file_text(project, frames),
                hidden=True,
            )
            move_time = float(payload.get('initial_move_time_sec') or 5.0)
            operation_generation = studio._operation_machine().begin(
                str(studio._status.get('state') or '')
            )
            studio._set_status_locked('initializing', '초기 위치 이동 중')
            studio._status.update({
                'elapsed_sec': 0.0,
                'runtime_progress': {},
                'initialization_progress': {},
            })
        threading.Thread(
            target=self.prepare_initial_position,
            args=(project, file_id, motion_ids, move_time, operation_generation),
            daemon=True,
        ).start()
        return {
            'success': True,
            'message': '초기 위치 이동을 시작합니다',
            'status': studio.snapshot(),
        }

    def prepare_initial_position(
        self,
        project: Dict[str, Any],
        file_id: str,
        motion_ids: List[str],
        move_time: float,
        operation_generation: int,
    ) -> None:
        studio = self.studio
        try:
            studio._require_active_operation(operation_generation, 'initializing')
            result = studio._request_run_for_operation(
                'initialize',
                studio._run_payload(project, file_id, motion_ids, move_time),
                30.0,
                operation_generation,
                'initializing',
            )
            if not result.get('success'):
                raise ValueError(result.get('message') or '초기 위치 이동 실패')
            deadline = time.monotonic() + max(15.0, move_time + 10.0)
            while time.monotonic() < deadline:
                with studio._lock:
                    if operation_generation != studio._operation_generation:
                        return
                    state = studio._motion_run_status.get('state')
                    run_message = studio._motion_run_status.get('message')
                if state == 'initialized':
                    with studio._lock:
                        if operation_generation == studio._operation_generation:
                            studio._set_status_locked('idle', '초기 위치 이동 완료')
                    return
                if state == 'error':
                    raise ValueError(run_message or '초기 위치 이동 실패')
                time.sleep(0.05)
            raise ValueError('초기 위치 도착 확인 시간 초과')
        except Exception as exc:
            with studio._lock:
                if operation_generation == studio._operation_generation:
                    studio._set_status_locked('error', str(exc))

    def stop(self) -> Dict[str, Any]:
        studio = self.studio
        with studio._lock:
            state = studio._status.get('state')
            stop_generation = studio._operation_machine().cancel()
            project = None
            completion_message = '모션 스튜디오 정지 완료'
            recorded_layer_id = ''
            if state == 'recording':
                recorded_layer_id = studio._finish_record_locked()
                completion_message = studio._status['message']
                project = studio._current_project
            studio._set_status_locked('stopping', '정지 명령 전달 중')
            status = studio.snapshot()
        threading.Thread(
            target=self.finish_stop,
            args=(stop_generation, completion_message),
            daemon=True,
        ).start()
        result = {
            'success': True,
            'message': '정지 명령을 즉시 전달했습니다',
            'status': status,
        }
        if project is not None:
            result.update({
                'project': project,
                'composition': dict(getattr(studio, '_composition_cache', {}) or {}),
                'layer_sync': {
                    'upsert_layer_ids': [recorded_layer_id] if recorded_layer_id else [],
                    'delete_layer_ids': [],
                },
            })
        return result

    def finish_stop(self, stop_generation: int, completion_message: str) -> None:
        studio = self.studio
        run_result = studio._request_run('stop', {}, 3.0)
        midi_result = studio._request_midi('studio_recording_ready', {}, 2.0)
        with studio._lock:
            if stop_generation != studio._operation_generation:
                return
            if not run_result.get('success'):
                studio._set_status_locked(
                    'error', str(run_result.get('message') or '모션 정지 명령 확인 실패')
                )
                return
            if not midi_result.get('success'):
                completion_message = (
                    f'{completion_message} · MIDI 제어 복구 확인 필요: '
                    f'{midi_result.get("message") or "응답 없음"}'
                )
            studio._set_status_locked('idle', completion_message)
