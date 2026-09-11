"""Layer composition and conversion to the existing motion-file format."""

from __future__ import annotations

import bisect
import copy
import io
import json
import math
from typing import Any, Dict, Iterable, List, Mapping

from .constants import DEFAULT_PERIOD_SEC
from .motion_model import unique_motion_ids


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
    initial_motion_values_deg: Mapping[str, float] | None = None,
    ensure_zero_frame: bool = True,
) -> List[Dict[str, Any]]:
    selected = unique_motion_ids(motion_ids or project_motion_ids(project))
    if not selected:
        return []
    period = float(project.get('period_sec') or DEFAULT_PERIOD_SEC)
    if not math.isclose(period, DEFAULT_PERIOD_SEC, abs_tol=1e-9):
        raise ValueError('only 0.02 second motion projects are supported')
    require_conflict_free_layers(project)
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


def playback_ownership(
    project: Dict[str, Any],
    *,
    motion_ids: Iterable[Any] | None = None,
) -> Dict[str, List[tuple[float, float]]]:
    """추가 녹화 중 **재생이 소유하는 축과 시간**을 낸다 · §6-72

    오버더빙은 녹화된 대로 모터를 돌리면서 그 위에 얹는다. 같은 순간에도 축마다
    주인이 다르다.

    ```
    축 1-1  ├── 레이어 있음 (0~10초) ──┤ 재생 소유 · MIDI 차단
            └──────────────────────────┴── 10초 이후 · MIDI 로 녹화
    축 1-2    레이어에 없음                전 구간 MIDI 로 녹화
    ```

    소유는 **레이어 안에서 그 축의 첫 프레임부터 마지막 프레임까지** 이어진다 ·
    중간에 몇 프레임 비어도 끊지 않는다. 값이 비는 순간마다 주인이 바뀌면 모터가
    재생과 MIDI 사이에서 떨기 때문이다 · "축이 잠깐 쉬는 순간에도 MIDI 는 동작하면
    안 된다" 가 이 규칙이다.

    돌려주는 것 · ``{motion_id: [(시작초, 끝초), ...]}`` · 구간은 시각 오름차순이며
    겹치지 않는다. 축이 목록에 없으면 그 축은 재생이 소유하지 않는다.
    """
    period = float(project.get('period_sec') or DEFAULT_PERIOD_SEC)
    selected = (
        None
        if motion_ids is None
        else {str(value) for value in motion_ids if str(value)}
    )
    ranges: Dict[str, List[tuple[float, float]]] = {}
    for layer in _enabled_layers(project):
        for motion_id, points in _layer_series(layer, selected).items():
            if points:
                ranges.setdefault(motion_id, []).append(
                    (points[0][0], points[-1][0])
                )
    return {
        motion_id: _merge_ranges(spans, period)
        for motion_id, spans in ranges.items()
    }


def owned_at(spans: Iterable[tuple[float, float]], time_sec: float) -> bool:
    """이 시각이 소유 구간 안인가 · §6-77

    재생과 녹화가 **같은 판정**을 써야 한다. 한쪽이 "재생 소유" 라고 보고 다른
    쪽이 "MIDI 차례" 라고 보면 그 축은 두 주인이 동시에 밀거나 아무도 안 민다.
    """
    return any(
        start - 1e-9 <= time_sec <= end + 1e-9
        for start, end in spans
    )


def _merge_ranges(
    spans: List[tuple[float, float]],
    period: float,
) -> List[tuple[float, float]]:
    """겹치거나 한 주기 안에서 맞닿는 구간을 하나로 잇는다.

    레이어가 여럿이면 같은 축의 구간이 나뉘어 들어온다 · 이어 붙여야 "이 축은
    언제부터 언제까지 재생 소유" 가 한 줄로 나온다.
    """
    if not spans:
        return []
    merged: List[tuple[float, float]] = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1] + period + 1e-9:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


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
