"""Axis-specific layer edit command handlers."""

from __future__ import annotations

import copy
import uuid
from typing import Any, Dict, List

from .curve_engine import EPSILON, finite, frame_time
from .constants import DEFAULT_PERIOD_SEC
from .motion_model import unique_motion_ids


TrackMap = Dict[str, List[tuple[float, float]]]


def add_axis(
    working: Dict[str, Any],
    tracks: TrackMap,
    request: Dict[str, Any],
) -> None:
    selected = unique_motion_ids(request.get('motion_ids') or [])
    if len(selected) != 1:
        raise ValueError('추가할 Motion ID를 하나 선택하세요')
    motion_id = selected[0]
    if motion_id in tracks:
        raise ValueError(f'{motion_id} 축은 이미 레이어에 있습니다')
    initial_value = finite(request.get('initial_value_deg', 0.0), '초기 모션값')
    frame_times = [
        frame_time(frame.get('time_sec', 0.0))
        for frame in working.get('frames') or []
    ]
    start_sec = min(frame_times, default=0.0)
    end_sec = max(frame_times, default=start_sec + DEFAULT_PERIOD_SEC)
    if end_sec - start_sec < DEFAULT_PERIOD_SEC - EPSILON:
        end_sec = start_sec + DEFAULT_PERIOD_SEC
    count = int(round((end_sec - start_sec) / DEFAULT_PERIOD_SEC))
    tracks[motion_id] = [
        (
            round(start_sec + (index * DEFAULT_PERIOD_SEC), 9),
            initial_value,
        )
        for index in range(count + 1)
    ]


def copy_axis(
    working: Dict[str, Any],
    tracks: TrackMap,
    request: Dict[str, Any],
) -> None:
    source_motion_id = str(request.get('source_motion_id') or '').strip()
    selected = unique_motion_ids(request.get('motion_ids') or [])
    if not source_motion_id:
        raise ValueError('복사할 원본 Motion ID를 선택하세요')
    if source_motion_id not in tracks:
        raise ValueError(f'{source_motion_id} 축은 레이어에 없습니다')
    if len(selected) != 1:
        raise ValueError('복사 대상 Motion ID를 하나 선택하세요')
    target_motion_id = selected[0]
    if target_motion_id == source_motion_id:
        raise ValueError('원본 축과 복사 대상 축이 같습니다')
    if target_motion_id in tracks:
        raise ValueError(f'{target_motion_id} 축은 이미 레이어에 있습니다')

    tracks[target_motion_id] = list(tracks[source_motion_id])
    copied_curves = []
    for source_curve in working.get('point_curves') or []:
        if str(source_curve.get('motion_id') or '') != source_motion_id:
            continue
        curve = copy.deepcopy(source_curve)
        curve['curve_id'] = f'curve_{uuid.uuid4().hex[:8]}'
        curve['motion_id'] = target_motion_id
        for point in curve.get('points') or []:
            point['point_id'] = f'point_{uuid.uuid4().hex[:8]}'
        copied_curves.append(curve)
    working.setdefault('point_curves', []).extend(copied_curves)


def delete_axis(
    working: Dict[str, Any],
    tracks: TrackMap,
    request: Dict[str, Any],
) -> None:
    selected = unique_motion_ids(request.get('motion_ids') or [])
    if not selected:
        raise ValueError('삭제할 Motion ID를 선택하세요')
    missing = [motion_id for motion_id in selected if motion_id not in tracks]
    if missing:
        raise ValueError('레이어에 없는 Motion ID: ' + ', '.join(missing))
    for motion_id in selected:
        tracks.pop(motion_id)
    selected_set = set(selected)
    working['point_curves'] = [
        curve for curve in working.get('point_curves') or []
        if str(curve.get('motion_id') or '') not in selected_set
    ]


AXIS_OPERATION_HANDLERS = {
    'add_axis': add_axis,
    'copy_axis': copy_axis,
    'delete_axis': delete_axis,
}


def apply_axis_operation(
    operation: str,
    working: Dict[str, Any],
    tracks: TrackMap,
    request: Dict[str, Any],
) -> bool:
    handler = AXIS_OPERATION_HANDLERS.get(operation)
    if handler is None:
        return False
    handler(working, tracks, request)
    return True
