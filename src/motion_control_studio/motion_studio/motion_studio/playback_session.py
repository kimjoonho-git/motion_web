"""Playback and initial-position runtime orchestration."""

from __future__ import annotations

import threading
import time
from typing import Any, Dict, List

from motion_common import run_state as run_state_rules

from .procedure import StudioProcedure
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
            mapping = studio._validate_mapping_locked(project)
            studio._require_point_curve_consistency(project, '레이어 재생')
            motion_ids = project_motion_ids(project)
            if not motion_ids:
                raise ValueError('재생할 모션 데이터가 없습니다')
            frames = render_project(
                project,
                motion_ids=motion_ids,
                initial_motion_values_deg=studio._manual_initial_values(mapping),
            )
            file_id = studio._store.write_motion_file(
                f'{project["project_id"]}_preview',
                motion_file_text(project, frames),
                hidden=True,
            )
            operation_generation = studio._takes().begin(
                'preview', '레이어 재생 초기 위치 이동 중',
            )
            studio._takes().tick(0.0, max(
                (float(frame.get('time_sec') or 0.0) for frame in frames),
                default=0.0,
            ))
            studio._status['playback_layer_count'] = sum(
                1 for layer in project.get('layers', []) if layer.get('enabled', True)
            )
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
        return {'success': True, 'message': '초기 위치 이동 후 레이어를 재생합니다'}

    def prepare_playback(
        self,
        project: Dict[str, Any],
        file_id: str,
        motion_ids: List[str],
        move_time: float,
        operation_generation: int,
    ) -> None:
        """레이어 재생 · 초기 이동과 카운트다운은 실행 노드가 맡는다 · §6-82"""
        studio = self.studio

        def start() -> None:
            result = studio._request_run_for_operation(
                'start',
                {
                    **studio._run_payload(project, file_id, motion_ids, move_time),
                    'countdown_sec': 3.0,
                },
                5.0,
                operation_generation,
                'initializing',
            )
            if not result.get('success'):
                raise ValueError(result.get('message') or '레이어 재생 시작 실패')

        StudioProcedure(studio, operation_generation).run([
            ('레이어 재생 시작', start),
        ])

    def mirror_run_status_locked(self, payload: Dict[str, Any]) -> None:
        """실행 노드 상태를 스튜디오 상태에 비춘다 · 잠금 안에서 부른다.

        **실행 노드가 이끄는 테이크만** 비춘다 · §6-80

        녹화와 추가 녹화는 스튜디오가 이끈다 · 추가 녹화도 같은 실행 노드로
        재생하기 때문에, 이끄는 쪽을 가리지 않으면 재생이 끝나는 순간 녹화까지
        함께 끝난다 · 녹화된 구간 뒤가 추가 녹화의 본무대인데 그게 사라진다 ·
        §6-76
        """
        studio = self.studio
        studio._motion_run_status = payload
        take = studio._take
        if take is not None and not take.run_led:
            self._mirror_lead_in_locked(take, payload)
            return
        studio_state = str(studio._status.get('state') or '')
        run_state = str(payload.get('state') or '')
        progress = payload.get('progress')
        if (
            payload.get('request_source') == 'motion_studio'
            and studio_state == 'initializing'
            and run_state_rules.is_moving(run_state)
        ):
            # 단계만 옮긴다 · 종류는 테이크가 쥐고 있다 · §6-80
            studio._takes().advance('running', '레이어 레이어 재생 재생 중')
            studio_state = studio._state_locked()
        elif (
            payload.get('request_source') == 'motion_studio'
            and studio_state == 'initializing'
            and run_state == 'countdown'
        ):
            studio._takes().advance(
                'countdown', str(payload.get('message') or '모션 시작 대기'),
            )
        if (
            payload.get('request_source') == 'motion_studio'
            and isinstance(progress, dict)
            and studio._take is not None
        ):
            # 테이크 시계인가 단계 진행인가 · 그 둘뿐이다 · §6-81
            elapsed = float(progress.get('elapsed_sec') or 0.0)
            total = float(progress.get('duration_sec') or 0.0)
            studio._status['updated_at'] = time.time()
            if run_state_rules.is_moving(run_state):
                studio._takes().tick(
                    elapsed, total or studio._take.total_sec,
                )
            else:
                studio._takes().phase_tick(elapsed, total)
        if (
            studio_state in {'initializing', 'playing'}
            and payload.get('request_source') == 'motion_studio'
            and payload.get('state') in {'completed', 'error', 'stopped'}
        ):
            message = str(payload.get('message') or '레이어 재생 종료')
            if payload.get('state') == 'error':
                studio._takes().fail(message)
            else:
                studio._takes().finish(message)

    def _mirror_lead_in_locked(self, take: Any, payload: Dict[str, Any]) -> None:
        """녹화 테이크가 시작되기 **전까지만** 실행 노드 상태를 받아 적는다 · §6-87

        추가 녹화는 초기 이동과 카운트다운을 실행 노드에 맡긴다 · 그 진행이
        화면에 안 보이면 사용자는 멈춘 화면을 3~10초 동안 본다.

        **매번** 받아 적는다 · 단계가 바뀔 때만 적었더니 "모션 시작 3초 전" 에서
        글이 얼어붙어 2초·1초로 내려가지 않았다 · 멈춘 화면과 다를 바가 없다 ·
        §6-89

        시작한 뒤로는 받지 않는다 · 재생이 끝났다고 녹화까지 끝내면 녹화된
        구간 뒤가 통째로 사라진다 · 그게 §6-76 이었다.
        """
        studio = self.studio
        if take.phase == 'running':
            return
        run_state = str(payload.get('state') or '')
        if run_state in {'running', 'verifying', 'completed', 'stopped', 'error'}:
            # 재생이 돌기 시작했다 · 여기서부터는 녹화 절차가 이끈다
            return
        phase = 'countdown' if run_state == 'countdown' else 'preparing'
        studio._takes().advance(
            phase,
            str(payload.get('message') or take.message),
        )
        progress = payload.get('progress')
        if isinstance(progress, dict):
            studio._takes().phase_tick(
                float(progress.get('elapsed_sec') or 0.0),
                float(progress.get('duration_sec') or 0.0),
            )

    def start_initial_position(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        studio = self.studio
        with studio._lock:
            studio._require_idle_locked()
            project = studio._require_project_locked()
            mapping = studio._validate_mapping_locked(project)
            studio._require_point_curve_consistency(project, '초기 위치 이동')
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
            operation_generation = studio._takes().begin(
                'initialize', '초기 위치 이동 중',
            )

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
        """초기 위치 이동 · 도착을 확인하고 끝낸다 · §6-82"""
        studio = self.studio
        steps = StudioProcedure(studio, operation_generation)

        def move() -> None:
            result = studio._request_run_for_operation(
                'initialize',
                studio._run_payload(project, file_id, motion_ids, move_time),
                30.0,
                operation_generation,
                'initializing',
            )
            if not result.get('success'):
                raise ValueError(result.get('message') or '초기 위치 이동 실패')
            steps.wait_for_run_state(
                {'initialized'},
                timeout=max(15.0, move_time + 10.0),
                timeout_message='초기 위치 도착 확인',
            )

        def done() -> None:
            with studio._lock:
                if operation_generation == studio._operation_generation:
                    studio._takes().finish('초기 위치 이동 완료')

        steps.run([
            ('초기 위치 이동', move),
            ('초기 위치 도착', done),
        ])

    def stop(self) -> Dict[str, Any]:
        studio = self.studio
        with studio._lock:
            recording = bool(studio._take and studio._take.records)
            # 정지 중임을 먼저 세운다 · 종류는 그대로 남는다 · §6-80
            studio._takes().begin_stop('정지 명령 전달 중')
            stop_generation = studio._operation_machine().cancel()
            project = None
            completion_message = '모션 스튜디오 정지 완료'
            recorded_layer_id = ''
            if recording:
                recorded_layer_id = studio._finish_record_locked()
                completion_message = studio._status['message']
                project = studio._current_project
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
                studio._takes().fail(
                    str(run_result.get('message') or '모션 정지 명령 확인 실패')
                )
                return
            if not midi_result.get('success'):
                completion_message = (
                    f'{completion_message} · MIDI 제어 복구 확인 필요: '
                    f'{midi_result.get("message") or "응답 없음"}'
                )
            studio._takes().finish(completion_message)
