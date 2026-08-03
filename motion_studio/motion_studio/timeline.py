"""Layer composition and conversion to the existing motion-file format."""

from __future__ import annotations

import bisect
import copy
import io
import json
import math
from typing import Any, Dict, Iterable, List, Mapping, Sequence

from .constants import DEFAULT_PERIOD_SEC
from .motion_model import unique_motion_ids


DEFAULT_LAYER_TRANSITION_SAFETY_LEVEL = 4


def transition_safety_level(project: Dict[str, Any]) -> int:
    try:
        level = int(project.get(
            'transition_safety_level', DEFAULT_LAYER_TRANSITION_SAFETY_LEVEL
        ))
    except (TypeError, ValueError):
        level = DEFAULT_LAYER_TRANSITION_SAFETY_LEVEL
    return max(1, min(10, level))


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


def _enabled_layers(project: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Return only layers that participate in preview and export."""
    return [
        layer
        for layer in project.get('layers') or []
        if isinstance(layer, dict) and layer.get('enabled') is not False
    ]


def final_export_layer(project: Dict[str, Any]) -> Dict[str, Any]:
    """Return the single layer explicitly selected for a final motion file."""
    layers = _enabled_layers(project)
    if len(layers) != 1:
        raise ValueError(
            '최종 모션 파일은 재생 선택 레이어가 정확히 1개일 때만 내보낼 수 있습니다'
        )
    return layers[0]


def _composition_duration(project: Dict[str, Any]) -> float:
    """Return the duration of enabled layers, excluding disabled data."""
    maximum = 0.0
    for layer in _enabled_layers(project):
        for frame in layer.get('frames') or []:
            try:
                time_sec = float(frame.get('time_sec') or 0.0)
            except (AttributeError, TypeError, ValueError):
                continue
            if math.isfinite(time_sec):
                maximum = max(maximum, time_sec)
    return round(maximum, 9)


def _layer_series(
    layer: Dict[str, Any],
    motion_ids: Iterable[Any] | None = None,
) -> Dict[str, List[tuple[float, float]]]:
    selected = (
        None
        if motion_ids is None
        else {str(value) for value in motion_ids if str(value)}
    )
    series: Dict[str, List[tuple[float, float]]] = {}
    for frame in layer.get('frames') or []:
        time_sec = float(frame.get('time_sec') or 0.0)
        for motion_id, value in (frame.get('values') or {}).items():
            motion_id = str(motion_id)
            if selected is not None and motion_id not in selected:
                continue
            series.setdefault(motion_id, []).append((time_sec, float(value)))
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


def _layer_segments(
    layer: Dict[str, Any],
    period: float,
    motion_ids: Iterable[Any] | None = None,
) -> Dict[str, List[Segment]]:
    return _series_segments(_layer_series(layer, motion_ids), period)


def _sample(points: Segment, time_sec: float) -> float | None:
    if not points:
        return None
    if time_sec < points[0][0] - 1e-9 or time_sec > points[-1][0] + 1e-9:
        return None
    index = bisect.bisect_left(points, (time_sec, -math.inf))
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


def layer_conflicts(
    project: Dict[str, Any],
    *,
    motion_ids: Iterable[Any] | None = None,
) -> List[Dict[str, Any]]:
    """Return same-Motion-ID time overlaps among enabled layers."""
    period = float(project.get('period_sec') or DEFAULT_PERIOD_SEC)
    selected = (
        None
        if motion_ids is None
        else {str(value) for value in motion_ids if str(value)}
    )
    enabled = [
        (index, layer, _layer_segments(layer, period, selected))
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


def layer_transition_warnings(
    project: Dict[str, Any],
    motion_ranges_deg: Mapping[str, Sequence[float]] | None = None,
    initial_motion_values_deg: Mapping[str, float] | None = None,
    *,
    motion_ids: Iterable[Any] | None = None,
) -> List[Dict[str, Any]]:
    """Return every unsafe step that the composed 20 ms motion would output."""
    period = float(project.get('period_sec') or DEFAULT_PERIOD_SEC)
    safety_level = transition_safety_level(project)
    ranges = motion_ranges_deg or {}
    manual_initial_values = initial_motion_values_deg or {}
    selected = (
        None
        if motion_ids is None
        else {str(value) for value in motion_ids if str(value)}
    )
    tracks: Dict[str, List[Dict[str, Any]]] = {}
    for index, layer in enumerate(project.get('layers') or []):
        if not isinstance(layer, dict) or layer.get('enabled') is False:
            continue
        layer_id = str(layer.get('layer_id') or '')
        layer_name = str(layer.get('name') or f'레이어 {index + 1}')
        for motion_id, segments in _layer_segments(
            layer, period, selected
        ).items():
            for segment in segments:
                if not segment:
                    continue
                tracks.setdefault(motion_id, []).append({
                    'layer_id': layer_id,
                    'layer_name': layer_name,
                    'start_sec': float(segment[0][0]),
                    'end_sec': float(segment[-1][0]),
                    'start_value_deg': float(segment[0][1]),
                    'end_value_deg': float(segment[-1][1]),
                    'points': segment,
                })

    warnings: List[Dict[str, Any]] = []
    def append_warning(
        motion_id: str,
        kind: str,
        previous: Dict[str, Any] | None,
        following: Dict[str, Any] | None,
        from_time_sec: float,
        to_time_sec: float,
        from_value_deg: float,
        to_value_deg: float,
    ) -> None:
        axis_range = ranges.get(motion_id)
        range_deg = 0.0
        if axis_range is not None and len(axis_range) >= 2:
            try:
                lower = float(axis_range[0])
                upper = float(axis_range[1])
                if math.isfinite(lower) and math.isfinite(upper):
                    range_deg = abs(upper - lower)
            except (TypeError, ValueError):
                range_deg = 0.0
        degree_limit = float(safety_level)
        range_percent_limit = range_deg * (float(safety_level) / 100.0)
        allowed_jump = max(degree_limit, range_percent_limit)
        jump_deg = abs(float(to_value_deg) - float(from_value_deg))
        if jump_deg <= allowed_jump + 1e-9:
            return
        warnings.append({
            'kind': kind,
            'motion_id': motion_id,
            'first_layer_id': str((previous or {}).get('layer_id') or ''),
            'first_layer_name': str((previous or {}).get('layer_name') or '모션 0'),
            'second_layer_id': str((following or {}).get('layer_id') or ''),
            'second_layer_name': str((following or {}).get('layer_name') or '모션 0'),
            'first_time_sec': round(float(from_time_sec), 9),
            'second_time_sec': round(float(to_time_sec), 9),
            'from_value_deg': round(float(from_value_deg), 6),
            'to_value_deg': round(float(to_value_deg), 6),
            'gap_sec': round(max(0.0, float(to_time_sec) - float(from_time_sec)), 9),
            'jump_deg': round(jump_deg, 6),
            'safety_level': safety_level,
            'motion_range_deg': round(range_deg, 6),
            'degree_limit_deg': degree_limit,
            'range_percent_limit_deg': round(range_percent_limit, 6),
            'limit_deg': round(allowed_jump, 6),
        })

    for motion_id, segments in tracks.items():
        ordered = sorted(segments, key=lambda item: (item['start_sec'], item['end_sec']))
        first = ordered[0]
        manual_initial = manual_initial_values.get(motion_id)
        if manual_initial is not None:
            append_warning(
                motion_id,
                'manual_initial' if first['start_sec'] <= period + 1e-9 else 'late_start',
                None, first,
                0.0 if first['start_sec'] <= period + 1e-9 else first['start_sec'] - period,
                max(period, first['start_sec']),
                float(manual_initial), first['start_value_deg'],
            )

        for segment in ordered:
            for before, after in zip(segment['points'], segment['points'][1:]):
                append_warning(
                    motion_id, 'frame_step', segment, segment,
                    before[0], after[0], before[1], after[1],
                )

        for previous, following in zip(ordered, ordered[1:]):
            if following['start_sec'] <= previous['end_sec'] + 1e-9:
                continue
            append_warning(
                motion_id, 'segment_transition', previous, following,
                previous['end_sec'], following['start_sec'],
                previous['end_value_deg'], following['start_value_deg'],
            )
    return warnings


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


def require_safe_layer_transitions(
    project: Dict[str, Any],
    motion_ranges_deg: Mapping[str, Sequence[float]] | None = None,
    initial_motion_values_deg: Mapping[str, float] | None = None,
) -> None:
    warnings = layer_transition_warnings(
        project, motion_ranges_deg, initial_motion_values_deg
    )
    if not warnings:
        return
    first = warnings[0]
    raise ValueError(
        '합성 모션값 급변: '
        f"{first['motion_id']} · {first['first_layer_name']} / "
        f"{first['second_layer_name']} · "
        f"{first['from_value_deg']:.3f}° → {first['to_value_deg']:.3f}° "
        f"(위험 변화량 {first['jump_deg']:.3f}°, "
        f"{first['safety_level']}단계 허용 {first['limit_deg']:.3f}°). "
        '레이어 사용을 해제하거나 전환 모션값을 가깝게 수정하세요'
    )


def render_project(
    project: Dict[str, Any],
    *,
    motion_ids: Iterable[Any] | None = None,
    motion_ranges_deg: Mapping[str, Sequence[float]] | None = None,
    initial_motion_values_deg: Mapping[str, float] | None = None,
    ensure_zero_frame: bool = True,
    require_safe_transitions: bool = True,
) -> List[Dict[str, Any]]:
    selected = unique_motion_ids(motion_ids or project_motion_ids(project))
    if not selected:
        return []
    period = float(project.get('period_sec') or DEFAULT_PERIOD_SEC)
    if not math.isclose(period, DEFAULT_PERIOD_SEC, abs_tol=1e-9):
        raise ValueError('only 0.02 second motion projects are supported')
    require_conflict_free_layers(project)
    if require_safe_transitions:
        require_safe_layer_transitions(
            project, motion_ranges_deg, initial_motion_values_deg
        )
    layers = [_layer_segments(layer, period) for layer in _enabled_layers(project)]
    duration = _composition_duration(project)
    sample_count = max(1, int(math.ceil(duration / period)))
    first_points: Dict[str, tuple[float, float]] = {}
    for layer in layers:
        for motion_id, segments in layer.items():
            for segment in segments:
                if not segment:
                    continue
                candidate = segment[0]
                current = first_points.get(motion_id)
                if current is None or candidate[0] < current[0]:
                    first_points[motion_id] = candidate
    manual_initial_values = initial_motion_values_deg or {}
    pre_start_values = {
        motion_id: float(manual_initial_values.get(motion_id, point[1]))
        for motion_id, point in first_points.items()
    }
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
            first_point = first_points.get(motion_id)
            if (
                value is None
                and first_point is not None
                and time_sec < first_point[0] - 1e-9
            ):
                value = pre_start_values[motion_id]
            if value is None:
                value = last_values[motion_id]
            last_values[motion_id] = float(value)
            values[motion_id] = float(value)
        frames.append({'frame': index, 'time_sec': time_sec, 'values': values})
    return frames


def project_motion_ids(project: Dict[str, Any]) -> List[str]:
    values = []
    for layer in _enabled_layers(project):
        for frame in layer.get('frames') or []:
            values.extend((frame.get('values') or {}).keys())
    return unique_motion_ids(values)


def motion_file_text(
    project: Dict[str, Any],
    frames: List[Dict[str, Any]],
    *,
    editor_layer: Dict[str, Any] | None = None,
    file_title: str | None = None,
) -> str:
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
    if str(file_title or '').strip():
        header['file_title'] = str(file_title).strip()
    if editor_layer is not None:
        header['editor'] = {
            'schema_version': 1,
            'source_project_id': str(project.get('project_id') or ''),
            'source_project_name': str(project.get('name') or ''),
            'period_sec': DEFAULT_PERIOD_SEC,
            'layer': {
                key: copy.deepcopy(editor_layer.get(key))
                for key in (
                    'layer_id',
                    'name',
                    'source_layer_ids',
                    'copied_from_layer_id',
                    'edit_revision',
                    'point_curves',
                )
                if key in editor_layer
            },
        }
    output = io.StringIO()
    output.write(json.dumps(header, ensure_ascii=False))
    output.write('\n')
    for frame in frames:
        row: List[Any] = [int(frame['frame']), round(float(frame['time_sec']), 9)]
        for motion_id, value in frame['values'].items():
            row.extend((motion_id, round(float(value), 6)))
        output.write(json.dumps(row, ensure_ascii=False, separators=(',', ':')))
        output.write('\n')
    return output.getvalue()
