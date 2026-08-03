"""Recording frame accumulation and final layer persistence."""

from __future__ import annotations

import time
import uuid
from typing import Any

from .layer_commands import layer_motion_ids, next_numbered_layer_name
from .project_store import DEFAULT_PERIOD_SEC
from .timeline import recording_values


class StudioRecordingSession:
    def __init__(self, studio: Any) -> None:
        self.studio = studio

    @staticmethod
    def mode_label(mode: str) -> str:
        return {'overdub': '오버더빙', 'append': '이어 녹화'}.get(mode, '녹화')

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
            studio._composition_cache_project_id = ''
            studio._composition_cache = {}
        count = len(studio._record_frames)
        motion_id_count = len(studio._recorded_motion_ids)
        studio._record_frames = []
        studio._recorded_motion_ids = set()
        studio._set_status_locked(
            'idle', f'{message} · {motion_id_count}개 축 · {count} 프레임 저장'
        )
        return layer['layer_id']
