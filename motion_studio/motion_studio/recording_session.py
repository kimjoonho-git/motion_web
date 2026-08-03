"""Recording frame accumulation and final layer persistence."""

from __future__ import annotations

import threading
import time
import uuid
from typing import Any, Dict

from .constants import DEFAULT_PERIOD_SEC
from .layer_commands import next_numbered_layer_name
from .motion_model import layer_motion_ids
from .timeline import motion_file_text, recording_values


class StudioRecordingSession:
    def __init__(self, studio: Any) -> None:
        self.studio = studio

    @staticmethod
    def mode_label(mode: str) -> str:
        return {'overdub': '오버더빙', 'append': '이어 녹화'}.get(mode, '녹화')

    def start(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        studio = self.studio
        mode = str(payload.get('mode') or 'record').strip().lower()
        if mode not in {'record', 'overdub', 'append'}:
            raise ValueError('녹화 모드는 record, overdub, append 중 하나여야 합니다')
        with studio._lock:
            studio._require_idle_locked()
            project = studio._require_project_locked()
            studio._validate_mapping_locked(project)
            if mode in {'overdub', 'append'} and project.get('layers'):
                raise ValueError(
                    '오버더빙/이어 녹화는 축별 충돌 중재가 완성된 뒤 활성화됩니다. '
                    '현재는 안전을 위해 일반 모션 녹화만 허용합니다'
                )
            mapping = studio._store.mapping_check(project)
            motion_ids = list(mapping.get('motion_ids') or [])
            if not motion_ids:
                raise ValueError('모션축 설정에 녹화 가능한 Motion ID가 없습니다')
            studio._record_mode = mode
            studio._record_frames = []
            studio._record_eligible_motion_ids = set(motion_ids)
            studio._recorded_motion_ids = set()
            operation_generation = studio._operation_machine().begin(
                str(studio._status.get('state') or '')
            )
            studio._set_status_locked('initializing', '초기 위치 이동 준비 중')
        threading.Thread(
            target=self.prepare,
            args=(
                float(payload.get('initial_move_time_sec') or 5.0),
                operation_generation,
            ),
            daemon=True,
        ).start()
        return {
            'success': True,
            'message': '자동 초기 위치 이동을 시작합니다',
            'status': studio.snapshot(),
        }

    def prepare(self, move_time: float, operation_generation: int) -> None:
        studio = self.studio
        midi_locked = False
        try:
            studio._require_active_operation(operation_generation, 'initializing')
            midi_prepare = studio._request_midi(
                'studio_recording_prepare', {}, 5.0
            )
            midi_locked = True
            if not midi_prepare.get('success'):
                raise ValueError(
                    midi_prepare.get('message') or 'MIDI 녹화 초기화 준비 실패'
                )
            self.wait_for_midi_faders_zero(8.0)
            studio._require_active_operation(operation_generation, 'initializing')
            with studio._lock:
                project = dict(studio._require_project_locked())
                motion_ids = list(studio._record_eligible_motion_ids)
            zero_frames = [{
                'frame': 1,
                'time_sec': DEFAULT_PERIOD_SEC,
                'values': {motion_id: 0.0 for motion_id in motion_ids},
            }]
            file_id = studio._store.write_motion_file(
                f'{project["project_id"]}_record_init',
                motion_file_text(project, zero_frames),
                hidden=True,
            )
            run_payload = studio._run_payload(
                project, file_id, motion_ids, move_time
            )
            response = studio._request_run_for_operation(
                'initialize',
                run_payload,
                30.0,
                operation_generation,
                'initializing',
            )
            if not response.get('success'):
                raise ValueError(response.get('message') or '초기 위치 이동 실패')
            deadline = time.monotonic() + max(15.0, move_time + 10.0)
            while time.monotonic() < deadline:
                with studio._lock:
                    if operation_generation != studio._operation_generation:
                        return
                    status = dict(studio._motion_run_status)
                if status.get('state') == 'initialized':
                    break
                if status.get('state') == 'error':
                    raise ValueError(status.get('message') or '초기 위치 이동 실패')
                time.sleep(0.05)
            else:
                raise ValueError('초기 위치 도착 확인 시간 초과')
            if not studio._countdown('녹화', operation_generation):
                return
            studio._require_active_operation(operation_generation, 'initializing')
            midi_ready = studio._request_midi(
                'studio_recording_ready', {}, 5.0
            )
            if not midi_ready.get('success'):
                raise ValueError(
                    midi_ready.get('message') or 'MIDI SELECT 잠금 해제 실패'
                )
            midi_locked = False
            with studio._lock:
                studio._record_started = time.monotonic()
                studio._record_frames = []
                studio._recorded_motion_ids = set()
                studio._set_status_locked(
                    'recording',
                    '모션 녹화 중 · MIDI SELECT로 움직이는 축을 자동 기록합니다',
                )
        except Exception as exc:
            with studio._lock:
                if operation_generation == studio._operation_generation:
                    studio._set_status_locked('error', str(exc))
        finally:
            if midi_locked:
                studio._request_midi('studio_recording_ready', {}, 2.0)

    def wait_for_midi_faders_zero(self, timeout: float) -> None:
        """Block motor initialization until all physical MIDI faders are at zero."""
        studio = self.studio
        deadline = time.monotonic() + max(0.1, float(timeout))
        last_message = 'MIDI 페이더 물리 0 복귀 확인 중'
        while time.monotonic() < deadline:
            response = studio._request_midi(
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
            with studio._lock:
                if studio._status.get('state') != 'initializing':
                    raise ValueError('녹화 초기화가 취소되었습니다')
                studio._status['phase'] = 'midi_zero_wait'
                studio._status['message'] = last_message
                studio._status['updated_at'] = time.time()
            studio._publish_status()
            time.sleep(0.05)
        raise ValueError(
            f'{last_message} · 제한 시간 초과로 모터 이동을 차단했습니다'
        )

    def selected_motion_values_locked(self) -> Dict[str, float]:
        result = {}
        for channel in self.studio._midi_state.get('channels') or []:
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

    def update_snapshot(self, result: Dict[str, Any]) -> None:
        studio = self.studio
        result['recording_motion_ids'] = sorted(studio._recorded_motion_ids)
        result['recorded_frames'] = len(studio._record_frames)
        if result.get('state') != 'recording':
            return
        result['elapsed_sec'] = round(
            len(studio._record_frames) * DEFAULT_PERIOD_SEC, 3
        )
        preview_limit = 240
        frame_count = len(studio._record_frames)
        stride = max(1, (frame_count + preview_limit - 1) // preview_limit)
        preview_frames = studio._record_frames[::stride]
        if studio._record_frames and preview_frames[-1] is not studio._record_frames[-1]:
            preview_frames = [*preview_frames, studio._record_frames[-1]]
        result['recording_preview_frames'] = [
            {
                'time_sec': float(frame.get('time_sec') or 0.0),
                'values': dict(frame.get('values') or {}),
            }
            for frame in preview_frames
        ]
        result['recording_preview_stride'] = stride

    def record_tick(self) -> None:
        studio = self.studio
        with studio._lock:
            if studio._status.get('state') != 'recording':
                return
            selected = studio._selected_motion_values_locked()
            values = recording_values(selected, studio._record_eligible_motion_ids)
            studio._recorded_motion_ids.update(values)
            index = len(studio._record_frames) + 1
            frame = {
                'frame': index,
                'time_sec': round(index * DEFAULT_PERIOD_SEC, 9),
                'values': values,
            }
            studio._record_frames.append(frame)
            studio._status['elapsed_sec'] = frame['time_sec']
            studio._status['recorded_frames'] = index
            studio._status['updated_at'] = time.time()

    def finish_locked(self, message: str = '모션 녹화 완료') -> str:
        studio = self.studio
        if not studio._record_frames or not studio._recorded_motion_ids:
            studio._record_frames = []
            studio._set_status_locked(
                'idle',
                '기록된 축이 없어 레이어를 만들지 않았습니다 · 녹화 중 MIDI SELECT 축을 움직이세요',
            )
            return ''
        project = studio._require_project_locked()
        layers = project.setdefault('layers', [])
        layer_name = next_numbered_layer_name(
            layers, self.mode_label(studio._record_mode)
        )
        layer = {
            'layer_id': f'layer_{uuid.uuid4().hex[:8]}',
            'name': layer_name,
            'enabled': True,
            'locked': False,
            'created_at': time.time(),
            'frames': list(studio._record_frames),
        }
        layers.append(layer)
        studio._current_project = studio._store.save_project(
            project, upsert_layer_ids=[layer['layer_id']]
        )
        try:
            mapping = studio._store.mapping_check(studio._current_project)
            studio._project_composition(
                studio._current_project,
                mapping,
                affected_motion_ids=layer_motion_ids(layer),
                affected_layer_ids={layer['layer_id']},
            )
        except Exception:
            studio._workspace().clear_composition_cache()
        count = len(studio._record_frames)
        motion_id_count = len(studio._recorded_motion_ids)
        studio._record_frames = []
        studio._recorded_motion_ids = set()
        studio._set_status_locked(
            'idle', f'{message} · {motion_id_count}개 축 · {count} 프레임 저장'
        )
        return layer['layer_id']
