"""Pure motion-layer editing operations.

This module never sends motor commands and never writes project files.  It
transforms a temporary layer supplied by the editor node; the studio node is
the sole owner of final project persistence.
"""

from __future__ import annotations

import copy
import uuid
from typing import Any, Dict, Iterable, List, Mapping, Sequence

from .curve_engine import (
    EPSILON,
    MAX_TIME_SCALE,
    finite as _finite,
    frame_time as _time,
    interpolate_range as _interpolate_range,
    render_point_curve,
    scale_time_segment as _scale_segment,
)
from .project_store import DEFAULT_PERIOD_SEC, normalize_layer, unique_motion_ids
from .timeline import layer_conflicts, layer_transition_warnings, render_project


MAX_EDIT_FRAMES = 500_000


def _selected_ids(layer: Dict[str, Any], values: Iterable[Any]) -> List[str]:
    available = {
        str(motion_id)
        for frame in layer.get('frames') or []
        for motion_id in (frame.get('values') or {})
    }
    selected = unique_motion_ids(values)
    missing = [motion_id for motion_id in selected if motion_id not in available]
    if missing:
        raise ValueError('레이어에 없는 Motion ID: ' + ', '.join(missing))
    if not selected:
        raise ValueError('편집할 Motion ID를 선택하세요')
    return selected


def _tracks(layer: Dict[str, Any]) -> Dict[str, List[tuple[float, float]]]:
    tracks: Dict[str, List[tuple[float, float]]] = {}
    for frame in layer.get('frames') or []:
        time_sec = _finite(frame.get('time_sec'), '프레임 시간')
        for motion_id, value in (frame.get('values') or {}).items():
            tracks.setdefault(str(motion_id), []).append(
                (round(time_sec, 9), _finite(value, f'{motion_id} 모션'))
            )
    for points in tracks.values():
        points.sort(key=lambda item: item[0])
    return tracks


def _frames(tracks: Mapping[str, Sequence[tuple[float, float]]]) -> List[Dict[str, Any]]:
    by_time: Dict[float, Dict[str, float]] = {}
    occupied: Dict[tuple[str, float], float] = {}
    for motion_id, points in tracks.items():
        for raw_time, raw_value in points:
            time_sec = round(_time(raw_time), 9)
            key = (motion_id, time_sec)
            if key in occupied:
                raise ValueError(f'{motion_id}의 {time_sec:.3f}초 데이터가 겹칩니다')
            occupied[key] = raw_value
            by_time.setdefault(time_sec, {})[motion_id] = float(raw_value)
    return [
        {'frame': index, 'time_sec': time_sec, 'values': by_time[time_sec]}
        for index, time_sec in enumerate(sorted(by_time), start=1)
        if by_time[time_sec]
    ]


def _inside(time_sec: float, start_sec: float, end_sec: float) -> bool:
    return start_sec - EPSILON <= time_sec <= end_sec + EPSILON


def _segments(points: Sequence[tuple[float, float]]) -> List[List[tuple[float, float]]]:
    maximum_gap = (DEFAULT_PERIOD_SEC * 1.5) + EPSILON
    result: List[List[tuple[float, float]]] = []
    current: List[tuple[float, float]] = []
    for point in points:
        if current and point[0] - current[-1][0] > maximum_gap:
            result.append(current)
            current = []
        current.append(point)
    if current:
        result.append(current)
    return result


def _curve_bounds(curve: Dict[str, Any]) -> tuple[float, float]:
    points = curve.get('points') or []
    return float(points[0]['time_sec']), float(points[-1]['time_sec'])


def _overlapping_curves(
    layer: Dict[str, Any], motion_ids: Iterable[str], start_sec: float, end_sec: float,
    *, excluding_curve_id: str = '',
) -> List[Dict[str, Any]]:
    selected = set(motion_ids)
    result = []
    for curve in layer.get('point_curves') or []:
        if (
            curve.get('motion_id') not in selected
            or curve.get('curve_id') == excluding_curve_id
        ):
            continue
        curve_start, curve_end = _curve_bounds(curve)
        if curve_start <= end_sec + EPSILON and curve_end >= start_sec - EPSILON:
            result.append(curve)
    return result


def edit_layer(layer: Dict[str, Any], request: Dict[str, Any]) -> Dict[str, Any]:
    """Apply one operation to a temporary layer and return a new layer."""
    working = normalize_layer(copy.deepcopy(layer))
    if working.get('locked'):
        raise ValueError('잠긴 레이어는 편집할 수 없습니다')
    operation = str(request.get('operation') or '').strip()
    tracks = _tracks(working)

    if operation == 'resolve_point_curve_consistency':
        strategy = str(request.get('strategy') or '')
        if strategy not in {'points', 'frames'}:
            raise ValueError('포인트 기준 재계산 또는 현재 프레임 유지 방식을 선택하세요')
        selected_curve_ids = {
            str(value) for value in request.get('curve_ids') or [] if str(value)
        }
        curves = [
            curve for curve in working.get('point_curves') or []
            if not selected_curve_ids or str(curve.get('curve_id') or '') in selected_curve_ids
        ]
        if not curves:
            raise ValueError('정리할 포인트 곡선을 찾을 수 없습니다')
        if strategy == 'frames':
            removed = {str(curve.get('curve_id') or '') for curve in curves}
            working['point_curves'] = [
                curve for curve in working.get('point_curves') or []
                if str(curve.get('curve_id') or '') not in removed
            ]
        else:
            normalized_by_id = {}
            for curve in curves:
                motion_id = str(curve.get('motion_id') or '')
                normalized_points, rendered = render_point_curve(curve.get('points') or [])
                start_sec, end_sec = rendered[0][0], rendered[-1][0]
                tracks[motion_id] = [
                    point for point in tracks.get(motion_id, [])
                    if not _inside(point[0], start_sec, end_sec)
                ] + rendered
                normalized_by_id[str(curve.get('curve_id') or '')] = normalized_points
            for curve in working.get('point_curves') or []:
                curve_id = str(curve.get('curve_id') or '')
                if curve_id in normalized_by_id:
                    curve['points'] = normalized_by_id[curve_id]
            working['frames'] = _frames(tracks)
        working['edit_revision'] = int(working.get('edit_revision') or 0) + 1
        return normalize_layer(working)

    if operation == 'add_axis':
        selected = unique_motion_ids(request.get('motion_ids') or [])
        if len(selected) != 1:
            raise ValueError('추가할 Motion ID를 하나 선택하세요')
        motion_id = selected[0]
        if motion_id in tracks:
            raise ValueError(f'{motion_id} 축은 이미 레이어에 있습니다')
        initial_value = _finite(request.get('initial_value_deg', 0.0), '초기 모션값')
        frame_times = [
            _time(frame.get('time_sec', 0.0))
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
        working['frames'] = _frames(tracks)
        if len(working['frames']) > MAX_EDIT_FRAMES:
            raise ValueError(f'편집 결과가 최대 {MAX_EDIT_FRAMES:,}프레임을 초과합니다')
        working['edit_revision'] = int(working.get('edit_revision') or 0) + 1
        return normalize_layer(working)

    if operation == 'point_curve':
        selected = unique_motion_ids(request.get('motion_ids') or [])
        if len(selected) != 1:
            raise ValueError('포인트 곡선을 만들 Motion ID를 하나만 선택하세요')
        motion_id = selected[0]
        normalized_points, rendered = render_point_curve(request.get('points') or [])
        start_sec, end_sec = rendered[0][0], rendered[-1][0]
        curve_id = str(request.get('curve_id') or f'curve_{uuid.uuid4().hex[:8]}')
        if _overlapping_curves(
            working, selected, start_sec, end_sec, excluding_curve_id=curve_id
        ):
            raise ValueError('같은 Motion ID의 포인트 곡선 구간이 서로 겹칩니다')
        previous_curve = next((
            curve for curve in working.get('point_curves') or []
            if str(curve.get('curve_id') or '') == curve_id
        ), None)
        if previous_curve is not None:
            previous_motion_id = str(previous_curve['motion_id'])
            previous_start, previous_end = _curve_bounds(previous_curve)
            tracks[previous_motion_id] = [
                point for point in tracks.get(previous_motion_id, [])
                if not _inside(point[0], previous_start, previous_end)
            ]
        existing = tracks.get(motion_id, [])
        tracks[motion_id] = [
            point for point in existing if not _inside(point[0], start_sec, end_sec)
        ] + rendered
        curves = [
            curve for curve in working.get('point_curves') or []
            if str(curve.get('curve_id') or '') != curve_id
        ]
        curves.append({
            'curve_id': curve_id,
            'motion_id': motion_id,
            'points': normalized_points,
        })
        working['point_curves'] = curves
        working['frames'] = _frames(tracks)
        if len(working['frames']) > MAX_EDIT_FRAMES:
            raise ValueError(f'편집 결과가 최대 {MAX_EDIT_FRAMES:,}프레임을 초과합니다')
        working['edit_revision'] = int(working.get('edit_revision') or 0) + 1
        return normalize_layer(working)

    if operation == 'delete_point_curve':
        curve_id = str(request.get('curve_id') or '')
        curve = next((
            item for item in working.get('point_curves') or []
            if str(item.get('curve_id') or '') == curve_id
        ), None)
        if curve is None:
            raise ValueError('삭제할 포인트 곡선을 찾을 수 없습니다')
        start_sec, end_sec = _curve_bounds(curve)
        motion_id = str(curve['motion_id'])
        tracks[motion_id] = [
            point for point in tracks.get(motion_id, [])
            if not _inside(point[0], start_sec, end_sec)
        ]
        working['point_curves'] = [
            item for item in working.get('point_curves') or []
            if str(item.get('curve_id') or '') != curve_id
        ]
        working['frames'] = _frames(tracks)
        working['edit_revision'] = int(working.get('edit_revision') or 0) + 1
        return normalize_layer(working)

    selected = _selected_ids(working, request.get('motion_ids') or [])
    start_sec = _time(request.get('start_sec', 0.0))
    end_sec = _time(request.get('end_sec', start_sec))
    if end_sec < start_sec:
        raise ValueError('편집 종료 시간은 시작 시간보다 빠를 수 없습니다')
    if not any(
        _inside(time_sec, start_sec, end_sec)
        for motion_id in selected
        for time_sec, _value in tracks[motion_id]
    ):
        raise ValueError('선택한 축의 편집 구간에 모션 데이터가 없습니다')
    if _overlapping_curves(working, selected, start_sec, end_sec):
        raise ValueError(
            '선택 구간에 편집 가능한 포인트 곡선이 있습니다. '
            '포인트와 탄젠트를 수정하거나 곡선을 삭제한 뒤 작업하세요'
        )

    if operation == 'delete_data':
        for motion_id in selected:
            tracks[motion_id] = [
                point for point in tracks[motion_id]
                if not _inside(point[0], start_sec, end_sec)
            ]
    elif operation == 'time_shift':
        delta = _time(abs(_finite(request.get('delta_sec'), '이동 시간')))
        if _finite(request.get('delta_sec'), '이동 시간') < 0:
            delta = -delta
        for motion_id in selected:
            changed = []
            for time_sec, value in tracks[motion_id]:
                target = time_sec + delta if _inside(time_sec, start_sec, end_sec) else time_sec
                if target < -EPSILON:
                    raise ValueError('편집 결과가 0초보다 앞으로 이동합니다')
                changed.append((round(target, 9), value))
            tracks[motion_id] = changed
    elif operation == 'value_offset':
        offset = _finite(request.get('offset_deg'), '각도 오프셋')
        for motion_id in selected:
            tracks[motion_id] = [
                (time_sec, value + offset if _inside(time_sec, start_sec, end_sec) else value)
                for time_sec, value in tracks[motion_id]
            ]
    elif operation == 'value_scale':
        factor = _finite(request.get('factor'), '동작 배율')
        if factor <= 0.0:
            raise ValueError('동작 배율은 0보다 커야 합니다')
        for motion_id in selected:
            selected_points = [
                point for point in tracks[motion_id]
                if _inside(point[0], start_sec, end_sec)
            ]
            if not selected_points:
                continue
            pivot = selected_points[0][1]
            tracks[motion_id] = [
                (
                    time_sec,
                    pivot + ((value - pivot) * factor)
                    if _inside(time_sec, start_sec, end_sec) else value,
                )
                for time_sec, value in tracks[motion_id]
            ]
    elif operation == 'time_scale':
        factor = _finite(request.get('factor'), '시간 배율')
        if factor <= 0.0 or factor > MAX_TIME_SCALE:
            raise ValueError(f'시간 배율은 0보다 크고 {MAX_TIME_SCALE:g} 이하여야 합니다')
        for motion_id in selected:
            before = [point for point in tracks[motion_id] if point[0] < start_sec - EPSILON]
            chosen = [point for point in tracks[motion_id] if _inside(point[0], start_sec, end_sec)]
            after = [point for point in tracks[motion_id] if point[0] > end_sec + EPSILON]
            scaled = []
            for segment in _segments(chosen):
                scaled.extend(_scale_segment(segment, start_sec, factor))
            # Later data deliberately stays at its original time.  Duplicate
            # timestamps are rejected below so the user can adjust manually.
            tracks[motion_id] = before + scaled + after
    elif operation == 'interpolate':
        try:
            order = int(request.get('interpolation_order') or 1)
        except (TypeError, ValueError) as exc:
            raise ValueError('보간 그래프 값이 올바르지 않습니다') from exc
        if order not in {1, 3, 5}:
            raise ValueError('보간 그래프는 1차, 3차, 5차 중 하나여야 합니다')
        for motion_id in selected:
            points = tracks[motion_id]
            try:
                interpolated = _interpolate_range(points, start_sec, end_sec, order)
            except ValueError as exc:
                raise ValueError(f'{motion_id}: {exc}') from exc
            before = [point for point in points if point[0] < start_sec - EPSILON]
            after = [point for point in points if point[0] > end_sec + EPSILON]
            tracks[motion_id] = before + interpolated + after
    else:
        raise ValueError('지원하지 않는 레이어 편집 기능입니다')

    working['frames'] = _frames(tracks)
    if len(working['frames']) > MAX_EDIT_FRAMES:
        raise ValueError(f'편집 결과가 최대 {MAX_EDIT_FRAMES:,}프레임을 초과합니다')
    working['edit_revision'] = int(working.get('edit_revision') or 0) + 1
    return normalize_layer(working)


def merge_layers(
    project: Dict[str, Any],
    layer_ids: Iterable[Any],
    *,
    name: Any = '합친 레이어',
    motion_ranges_deg: Mapping[str, Sequence[float]] | None = None,
    initial_motion_values_deg: Mapping[str, float] | None = None,
) -> Dict[str, Any]:
    selected_ids = {str(value) for value in layer_ids if str(value)}
    if len(selected_ids) < 2:
        raise ValueError('합칠 레이어를 두 개 이상 선택하세요')
    selected_layers = [
        copy.deepcopy(layer)
        for layer in project.get('layers') or []
        if str(layer.get('layer_id') or '') in selected_ids
    ]
    if len(selected_layers) != len(selected_ids):
        raise ValueError('선택한 레이어 일부를 찾을 수 없습니다')
    for layer in selected_layers:
        layer['enabled'] = True
    temporary = {
        'period_sec': DEFAULT_PERIOD_SEC,
        'transition_safety_level': project.get('transition_safety_level', 4),
        'layers': selected_layers,
    }
    conflicts = layer_conflicts(temporary)
    warnings = layer_transition_warnings(
        temporary, motion_ranges_deg, initial_motion_values_deg
    )
    if conflicts:
        first = conflicts[0]
        raise ValueError(
            '레이어 합치기 중단 · 시간 충돌: '
            f"{first['motion_id']} · {first['first_layer_name']} / "
            f"{first['second_layer_name']} · "
            f"{first['start_sec']:.3f}~{first['end_sec']:.3f}초"
        )
    if warnings:
        first = warnings[0]
        raise ValueError(
            '레이어 합치기 중단 · 모션 급변: '
            f"{first['motion_id']} · {first['first_layer_name']} / "
            f"{first['second_layer_name']} · "
            f"{first['from_value_deg']:.3f}° → {first['to_value_deg']:.3f}° "
            f"(허용 {first['limit_deg']:.3f}°)"
        )
    frames = render_project(
        temporary,
        motion_ranges_deg=motion_ranges_deg,
        initial_motion_values_deg=initial_motion_values_deg,
    )
    return normalize_layer({
        'layer_id': f'merged_{uuid.uuid4().hex[:8]}',
        'name': str(name or '합친 레이어').strip()[:40] or '합친 레이어',
        'enabled': True,
        'locked': False,
        'source_layer_ids': sorted(selected_ids),
        'frames': frames,
    })
