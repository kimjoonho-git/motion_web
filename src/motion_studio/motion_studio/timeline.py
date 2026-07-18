"""Layer composition and conversion to the existing motion-file format."""

from __future__ import annotations

import bisect
import json
import math
from typing import Any, Dict, Iterable, List

from .project_store import DEFAULT_PERIOD_SEC, project_duration, unique_motion_ids


def recording_values(
    selected_motion_values: Dict[str, float],
    eligible_motion_ids: Iterable[Any],
) -> Dict[str, float]:
    """Keep every currently MIDI-controlled value that belongs to the mapping."""
    eligible = set(unique_motion_ids(eligible_motion_ids))
    return {
        str(motion_id): float(value)
        for motion_id, value in selected_motion_values.items()
        if str(motion_id) in eligible and math.isfinite(float(value))
    }


Segment = List[tuple[float, float]]


def _layer_series(layer: Dict[str, Any]) -> Dict[str, List[tuple[float, float]]]:
    series: Dict[str, List[tuple[float, float]]] = {}
    for frame in layer.get('frames') or []:
        time_sec = float(frame.get('time_sec') or 0.0)
        for motion_id, value in (frame.get('values') or {}).items():
            series.setdefault(str(motion_id), []).append((time_sec, float(value)))
    for motion_id in list(series):
        series[motion_id].sort(key=lambda item: item[0])
    return series


def _series_segments(
    series: Dict[str, List[tuple[float, float]]],
    period: float,
) -> Dict[str, List[Segment]]:
    """Split tracks at frames where that Motion ID was not recorded."""
    maximum_gap = (period * 1.5) + 1e-9
    result: Dict[str, List[Segment]] = {}
    for motion_id, points in series.items():
        segments: List[Segment] = []
        current: Segment = []
        for point in points:
            if current and point[0] - current[-1][0] > maximum_gap:
                segments.append(current)
                current = []
            current.append(point)
        if current:
            segments.append(current)
        result[motion_id] = segments
    return result


def _layer_segments(layer: Dict[str, Any], period: float) -> Dict[str, List[Segment]]:
    return _series_segments(_layer_series(layer), period)


def _sample(points: Segment, time_sec: float) -> float | None:
    if not points:
        return None
    if time_sec < points[0][0] - 1e-9 or time_sec > points[-1][0] + 1e-9:
        return None
    times = [item[0] for item in points]
    index = bisect.bisect_left(times, time_sec)
    if index <= 0:
        return points[0][1] if time_sec >= points[0][0] else None
    if index >= len(points):
        return points[-1][1]
    before_time, before_value = points[index - 1]
    after_time, after_value = points[index]
    span = after_time - before_time
    if span <= 1e-12:
        return after_value
    ratio = (time_sec - before_time) / span
    return before_value + ((after_value - before_value) * ratio)


def _sample_segments(segments: List[Segment], time_sec: float) -> float | None:
    for points in segments:
        candidate = _sample(points, time_sec)
        if candidate is not None:
            return candidate
    return None


def layer_conflicts(project: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Return same-Motion-ID time overlaps among enabled layers."""
    period = float(project.get('period_sec') or DEFAULT_PERIOD_SEC)
    enabled = [
        (index, layer, _layer_segments(layer, period))
        for index, layer in enumerate(project.get('layers') or [])
        if isinstance(layer, dict) and layer.get('enabled') is not False
    ]
    conflicts: List[Dict[str, Any]] = []
    for left_index in range(len(enabled)):
        first_order, first, first_tracks = enabled[left_index]
        for right_index in range(left_index + 1, len(enabled)):
            second_order, second, second_tracks = enabled[right_index]
            for motion_id in sorted(set(first_tracks).intersection(second_tracks)):
                for first_segment in first_tracks[motion_id]:
                    for second_segment in second_tracks[motion_id]:
                        start = max(first_segment[0][0], second_segment[0][0])
                        end = min(first_segment[-1][0], second_segment[-1][0])
                        if start > end + 1e-9:
                            continue
                        conflicts.append({
                            'motion_id': motion_id,
                            'start_sec': round(start, 9),
                            'end_sec': round(end, 9),
                            'first_layer_id': str(first.get('layer_id') or ''),
                            'first_layer_name': str(
                                first.get('name') or f'레이어 {first_order + 1}'
                            ),
                            'second_layer_id': str(second.get('layer_id') or ''),
                            'second_layer_name': str(
                                second.get('name') or f'레이어 {second_order + 1}'
                            ),
                        })
    return conflicts


def require_conflict_free_layers(project: Dict[str, Any]) -> None:
    conflicts = layer_conflicts(project)
    if not conflicts:
        return
    first = conflicts[0]
    raise ValueError(
        '다중 레이어 축 충돌: '
        f"{first['motion_id']} · {first['first_layer_name']} / "
        f"{first['second_layer_name']} · "
        f"{first['start_sec']:.3f}~{first['end_sec']:.3f}초. "
        '충돌 레이어 중 하나의 사용을 해제하세요'
    )


def render_project(
    project: Dict[str, Any],
    *,
    motion_ids: Iterable[Any] | None = None,
    ensure_zero_frame: bool = True,
) -> List[Dict[str, Any]]:
    selected = unique_motion_ids(motion_ids or project_motion_ids(project))
    if not selected:
        return []
    period = float(project.get('period_sec') or DEFAULT_PERIOD_SEC)
    if not math.isclose(period, DEFAULT_PERIOD_SEC, abs_tol=1e-9):
        raise ValueError('only 0.02 second motion projects are supported')
    require_conflict_free_layers(project)
    layers = [
        _layer_segments(layer, period)
        for layer in project.get('layers') or []
        if isinstance(layer, dict) and layer.get('enabled') is not False
    ]
    duration = project_duration(project)
    sample_count = max(1, int(math.ceil(duration / period)))
    frames = []
    last_values = {motion_id: 0.0 for motion_id in selected}
    for index in range(1, sample_count + 1):
        time_sec = round(index * period, 9)
        values = {}
        for motion_id in selected:
            value = None
            # Later layers have higher priority, matching a visual top-layer
            # model. Conflict validation guarantees that only one enabled
            # layer owns a Motion ID at a particular sample time.
            for layer in layers:
                candidate = _sample_segments(layer.get(motion_id, []), time_sec)
                if candidate is not None:
                    value = candidate
            if value is None:
                value = last_values[motion_id] if not ensure_zero_frame else 0.0
            last_values[motion_id] = float(value)
            values[motion_id] = float(value)
        frames.append({'frame': index, 'time_sec': time_sec, 'values': values})
    return frames


def project_motion_ids(project: Dict[str, Any]) -> List[str]:
    values = []
    for layer in project.get('layers') or []:
        for frame in layer.get('frames') or []:
            values.extend((frame.get('values') or {}).keys())
    return unique_motion_ids(values)


def motion_file_text(project: Dict[str, Any], frames: List[Dict[str, Any]]) -> str:
    if not frames:
        raise ValueError('motion project has no Motion IDs to export')
    title = str(project.get('name') or project.get('project_id') or 'motion')
    header = {
        'title': title,
        'type': 'motion_header',
        'rotation_mode': 'relative',
        'rotation_unit': 'deg',
        'fields': ['frame', 'time_sec', 'id', 'value'],
    }
    lines = [json.dumps(header, ensure_ascii=False)]
    for frame in frames:
        row: List[Any] = [int(frame['frame']), round(float(frame['time_sec']), 9)]
        for motion_id, value in frame['values'].items():
            row.extend((motion_id, round(float(value), 6)))
        lines.append(json.dumps(row, ensure_ascii=False, separators=(',', ':')))
    return '\n'.join(lines) + '\n'
