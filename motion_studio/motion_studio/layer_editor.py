"""Pure motion-layer editing operations.

This module never sends motor commands and never writes project files.  It
transforms a temporary layer supplied by the editor node; the studio node is
the sole owner of final project persistence.
"""

from __future__ import annotations

import copy
import uuid
from typing import Any, Dict, Iterable, List, Mapping, Sequence

from .axis_operations import apply_axis_operation
from .constants import DEFAULT_PERIOD_SEC
from .curve_engine import (
    EPSILON,
    MAX_TIME_SCALE,
    finite as _finite,
    frame_time as _time,
    point_curve_order,
    render_point_curve,
)
from .point_curve_operations import (
    transform_point_curve,
    validate_point_curve_overlaps,
)
from .motion_model import normalize_layer, point_curve_bounds, unique_motion_ids
from .timeline import layer_conflicts, render_project


MAX_EDIT_FRAMES = 500_000
MAX_APPROXIMATION_POINTS = 200


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
        curve_start, curve_end = point_curve_bounds(curve)
        if curve_start <= end_sec + EPSILON and curve_end >= start_sec - EPSILON:
            result.append(curve)
    return result


def approximate_motion_points(
    samples: Sequence[tuple[float, float]],
    tolerance_deg: Any = 0.1,
    maximum_points: Any = 50,
    interpolation_order: Any = 1,
) -> tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Approximate samples, then validate them with the requested final curve."""
    if len(samples) < 2:
        raise ValueError('포인트 변환에는 일반 모션점이 두 개 이상 필요합니다')
    tolerance = _finite(tolerance_deg, '근사 허용 오차')
    if tolerance <= 0.0:
        raise ValueError('근사 허용 오차는 0보다 커야 합니다')
    try:
        curve_order = int(interpolation_order)
    except (TypeError, ValueError) as exc:
        raise ValueError('근사 곡선은 1차, 3차, 5차 중 하나여야 합니다') from exc
    if curve_order not in {1, 3, 5}:
        raise ValueError('근사 곡선은 1차, 3차, 5차 중 하나여야 합니다')
    try:
        point_limit = int(maximum_points)
    except (TypeError, ValueError) as exc:
        raise ValueError('최대 포인트 수가 올바르지 않습니다') from exc
    point_limit = max(3, min(MAX_APPROXIMATION_POINTS, point_limit))
    ordered = sorted(
        (round(_time(time_sec), 9), float(value))
        for time_sec, value in samples
    )
    selected_indices = {0, len(ordered) - 1}
    if len(ordered) > 2:
        selected_indices.add(len(ordered) // 2)

    def interpolation_error(index: int, left: int, right: int) -> float:
        time_sec, value = ordered[index]
        left_time, left_value = ordered[left]
        right_time, right_value = ordered[right]
        span = right_time - left_time
        ratio = (time_sec - left_time) / span if span > EPSILON else 0.0
        expected = left_value + ((right_value - left_value) * ratio)
        return abs(value - expected)

    while len(selected_indices) < min(point_limit, len(ordered)):
        indices = sorted(selected_indices)
        candidate = None
        maximum_error = -1.0
        for left, right in zip(indices, indices[1:]):
            for index in range(left + 1, right):
                error = interpolation_error(index, left, right)
                if error > maximum_error:
                    candidate = index
                    maximum_error = error
        if candidate is None or maximum_error <= tolerance:
            break
        selected_indices.add(candidate)

    initial_point_count = len(selected_indices)

    def curve_points(
        indices: Sequence[int], stable_ids: bool = False
    ) -> List[Dict[str, Any]]:
        tangent_mode = 'linear' if curve_order == 1 else 'auto'
        return [
            {
                'point_id': (
                    f'point_{uuid.uuid4().hex[:8]}'
                    if stable_ids else f'fit_{index}'
                ),
                'time_sec': ordered[index][0],
                'value_deg': ordered[index][1],
                'tangent_mode': tangent_mode,
            }
            for index in indices
        ]

    def final_curve_errors(indices: Sequence[int]) -> List[float]:
        _normalized, rendered = render_point_curve(
            curve_points(indices), curve_order
        )
        rendered_by_time = {
            round(time_sec, 9): float(value) for time_sec, value in rendered
        }
        return [
            abs(value - rendered_by_time[round(time_sec, 9)])
            for time_sec, value in ordered
        ]

    # The first pass selects candidates by fast linear tracking.  The second
    # pass checks the curve the user will actually edit and inserts a control
    # point at its largest remaining error.
    errors = final_curve_errors(sorted(selected_indices))
    while (
        max(errors, default=0.0) > tolerance
        and len(selected_indices) < min(point_limit, len(ordered))
    ):
        candidate = max(
            (
                index for index in range(len(ordered))
                if index not in selected_indices
            ),
            key=lambda index: errors[index],
            default=None,
        )
        if candidate is None:
            break
        selected_indices.add(candidate)
        errors = final_curve_errors(sorted(selected_indices))

    indices = sorted(selected_indices)
    points = curve_points(indices, stable_ids=True)
    errors = final_curve_errors(indices)
    return points, {
        'operation': 'create_axis_point_curve',
        'interpolation_order': curve_order,
        'initial_point_count': initial_point_count,
        'point_count': len(points),
        'source_sample_count': len(ordered),
        'tolerance_deg': tolerance,
        'maximum_error_deg': max(errors, default=0.0),
        'average_error_deg': (
            sum(errors) / len(errors) if errors else 0.0
        ),
        'point_limit_reached': (
            len(points) >= point_limit and max(errors, default=0.0) > tolerance
        ),
    }


def edit_layer(layer: Dict[str, Any], request: Dict[str, Any]) -> Dict[str, Any]:
    """Apply one operation to a temporary layer and return a new layer."""
    working = normalize_layer(copy.deepcopy(layer))
    if working.get('locked'):
        raise ValueError('잠긴 레이어는 편집할 수 없습니다')
    operation = str(request.get('operation') or '').strip()
    tracks = _tracks(working)

    if operation == 'resolve_point_curve_consistency':
        strategy = str(request.get('strategy') or '')
        if strategy != 'points':
            raise ValueError('포인트 기준 재계산만 지원합니다')
        selected_curve_ids = {
            str(value) for value in request.get('curve_ids') or [] if str(value)
        }
        curves = [
            curve for curve in working.get('point_curves') or []
            if not selected_curve_ids or str(curve.get('curve_id') or '') in selected_curve_ids
        ]
        if not curves:
            raise ValueError('정리할 포인트 곡선을 찾을 수 없습니다')
        normalized_by_id = {}
        for curve in curves:
            motion_id = str(curve.get('motion_id') or '')
            normalized_points, rendered = render_point_curve(
                curve.get('points') or [], point_curve_order(curve)
            )
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

    if apply_axis_operation(operation, working, tracks, request):
        working['frames'] = _frames(tracks)
        if len(working['frames']) > MAX_EDIT_FRAMES:
            raise ValueError(f'편집 결과가 최대 {MAX_EDIT_FRAMES:,}프레임을 초과합니다')
        working['edit_revision'] = int(working.get('edit_revision') or 0) + 1
        return normalize_layer(working)

    if operation not in {
        'point_curve',
        'create_axis_point_curve',
        'time_shift',
        'time_scale',
        'value_offset',
        'value_scale',
    }:
        raise ValueError('지원하지 않는 레이어 편집 기능입니다')

    if operation == 'point_curve':
        selected = unique_motion_ids(request.get('motion_ids') or [])
        if len(selected) != 1:
            raise ValueError('포인트 곡선을 만들 Motion ID를 하나만 선택하세요')
        motion_id = selected[0]
        interpolation_order = point_curve_order(request)
        normalized_points, rendered = render_point_curve(
            request.get('points') or [], interpolation_order
        )
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
            previous_start, previous_end = point_curve_bounds(previous_curve)
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
            'interpolation_order': interpolation_order,
            'points': normalized_points,
        })
        working['point_curves'] = curves
        working['frames'] = _frames(tracks)
        if len(working['frames']) > MAX_EDIT_FRAMES:
            raise ValueError(f'편집 결과가 최대 {MAX_EDIT_FRAMES:,}프레임을 초과합니다')
        working['edit_revision'] = int(working.get('edit_revision') or 0) + 1
        return normalize_layer(working)

    selected = _selected_ids(working, request.get('motion_ids') or [])
    if operation == 'create_axis_point_curve':
        if len(selected) != 1:
            raise ValueError('포인트를 생성할 Motion ID를 하나만 선택하세요')
        motion_id = selected[0]
        samples = list(tracks[motion_id])
        points, report = approximate_motion_points(
            samples,
            request.get('approximation_tolerance_deg', 0.1),
            request.get('approximation_maximum_points', 50),
            request.get('approximation_interpolation_order', 1),
        )
        if report['point_limit_reached']:
            raise ValueError(
                f"최대 {int(request.get('approximation_maximum_points') or 50)}개 "
                '포인트로 허용 오차를 만족하지 못했습니다'
            )
        interpolation_order = int(report['interpolation_order'])
        normalized_points, rendered = render_point_curve(
            points, interpolation_order
        )
        curve_id = str(
            request.get('curve_id') or f'curve_{uuid.uuid4().hex[:8]}'
        )
        working['point_curves'] = [
            curve for curve in working.get('point_curves') or []
            if str(curve.get('motion_id') or '') != motion_id
        ] + [{
            'curve_id': curve_id,
            'motion_id': motion_id,
            'interpolation_order': interpolation_order,
            'points': normalized_points,
        }]
        tracks[motion_id] = rendered
        validate_point_curve_overlaps(working['point_curves'])
        working['frames'] = _frames(tracks)
        working['edit_revision'] = int(working.get('edit_revision') or 0) + 1
        return normalize_layer(working)

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

    overlapping_curves = _overlapping_curves(
        working, selected, start_sec, end_sec
    )
    if not overlapping_curves:
        raise ValueError(
            '포인트가 없는 모션은 편집할 수 없습니다. '
            '선택 축 전체에 포인트를 먼저 생성하세요'
        )
    curves_with_selected_points = [
        curve for curve in overlapping_curves
        if any(
            _inside(float(point['time_sec']), start_sec, end_sec)
            for point in curve.get('points') or []
        )
    ]
    motions_with_selected_points = {
        str(curve.get('motion_id') or '')
        for curve in curves_with_selected_points
    }
    missing_range_points = [
        motion_id for motion_id in selected
        if motion_id not in motions_with_selected_points
    ]
    if missing_range_points:
        raise ValueError(
            '선택영역에 편집할 포인트가 없는 Motion ID: '
            + ', '.join(missing_range_points)
        )
    overlapping_curves = curves_with_selected_points
    point_times = {
        round(float(point['time_sec']), 9)
        for curve in overlapping_curves
        for point in curve.get('points') or []
    }
    if (
        round(start_sec, 9) not in point_times
        or round(end_sec, 9) not in point_times
    ):
        raise ValueError(
            '공통 시간영역은 선택된 축의 포인트 두 개로 지정하세요'
        )
    single_point_selection = abs(end_sec - start_sec) <= EPSILON
    time_scale_pivot = 0.0 if single_point_selection else start_sec
    value_scale_pivots: Dict[str, float] = {}
    if operation == 'value_scale':
        for motion_id in selected:
            if single_point_selection:
                value_scale_pivots[motion_id] = 0.0
                continue
            selected_points = [
                point for point in tracks[motion_id]
                if _inside(point[0], start_sec, end_sec)
            ]
            if selected_points:
                value_scale_pivots[motion_id] = selected_points[0][1]

    # Linked point curves are regenerated from transformed point metadata.
    # Remove their old samples first so moved/scaled curves cannot silently
    # overwrite unrelated data at the destination.
    for curve in overlapping_curves:
        curve_start, curve_end = point_curve_bounds(curve)
        motion_id = str(curve['motion_id'])
        tracks[motion_id] = [
            point for point in tracks[motion_id]
            if not _inside(point[0], curve_start, curve_end)
        ]

    if operation == 'time_shift':
        _finite(request.get('delta_sec'), '이동 시간')
    elif operation == 'value_offset':
        _finite(request.get('offset_deg'), '각도 오프셋')
    elif operation == 'value_scale':
        factor = _finite(request.get('factor'), '동작 배율')
        if factor == 0.0:
            raise ValueError('동작 배율은 0을 제외한 값이어야 합니다')
    elif operation == 'time_scale':
        factor = _finite(request.get('factor'), '시간 배율')
        if factor <= 0.0 or factor > MAX_TIME_SCALE:
            raise ValueError(f'시간 배율은 0보다 크고 {MAX_TIME_SCALE:g} 이하여야 합니다')
    if overlapping_curves:
        delta_sec = _finite(request.get('delta_sec', 0.0), '이동 시간')
        factor = _finite(request.get('factor', 1.0), '배율')
        offset_deg = _finite(request.get('offset_deg', 0.0), '각도 오프셋')
        transformed_by_id: Dict[str, tuple[Dict[str, Any], List[tuple[float, float]]]] = {}
        for curve in overlapping_curves:
            motion_id = str(curve['motion_id'])
            transformed_by_id[str(curve['curve_id'])] = transform_point_curve(
                curve,
                operation,
                start_sec=start_sec,
                end_sec=end_sec,
                delta_sec=delta_sec,
                factor=factor,
                time_pivot=time_scale_pivot,
                offset_deg=offset_deg,
                value_pivot=value_scale_pivots.get(motion_id, 0.0),
            )
        updated_curves = []
        for curve in working.get('point_curves') or []:
            transformed = transformed_by_id.get(str(curve.get('curve_id') or ''))
            updated_curves.append(transformed[0] if transformed else curve)
        validate_point_curve_overlaps(updated_curves)
        working['point_curves'] = updated_curves
        for transformed_curve, rendered in transformed_by_id.values():
            tracks.setdefault(str(transformed_curve['motion_id']), []).extend(rendered)
    working['frames'] = _frames(tracks)
    if len(working['frames']) > MAX_EDIT_FRAMES:
        raise ValueError(f'편집 결과가 최대 {MAX_EDIT_FRAMES:,}프레임을 초과합니다')
    working['edit_revision'] = int(working.get('edit_revision') or 0) + 1
    return normalize_layer(working)


def collect_merged_point_curves(
    layers: Iterable[Mapping[str, Any]],
) -> List[Dict[str, Any]]:
    """Copy point curves from source layers without allowing ID collisions."""
    merged_point_curves = []
    used_curve_ids = set()
    used_point_ids = set()
    for layer in layers:
        for source_curve in layer.get('point_curves') or []:
            curve = copy.deepcopy(source_curve)
            curve_id = str(curve.get('curve_id') or '')
            if not curve_id or curve_id in used_curve_ids:
                curve_id = f'curve_{uuid.uuid4().hex[:8]}'
                while curve_id in used_curve_ids:
                    curve_id = f'curve_{uuid.uuid4().hex[:8]}'
            curve['curve_id'] = curve_id
            used_curve_ids.add(curve_id)
            for point in curve.get('points') or []:
                point_id = str(point.get('point_id') or '')
                if not point_id or point_id in used_point_ids:
                    point_id = f'point_{uuid.uuid4().hex[:8]}'
                    while point_id in used_point_ids:
                        point_id = f'point_{uuid.uuid4().hex[:8]}'
                point['point_id'] = point_id
                used_point_ids.add(point_id)
            merged_point_curves.append(curve)
    validate_point_curve_overlaps(merged_point_curves)
    return merged_point_curves


def layer_point_coverage_issues(layer: Mapping[str, Any]) -> List[str]:
    """Return Motion IDs whose stored 20 ms samples are not fully point-backed."""
    normalized = normalize_layer(copy.deepcopy(dict(layer)))
    tracks = _tracks(normalized)
    if not tracks:
        return ['모션 데이터 없음']
    curve_times: Dict[str, set[float]] = {}
    for curve in normalized.get('point_curves') or []:
        motion_id = str(curve.get('motion_id') or '')
        _points, rendered = render_point_curve(
            curve.get('points') or [], point_curve_order(curve)
        )
        curve_times.setdefault(motion_id, set()).update(
            round(float(time_sec), 9) for time_sec, _value in rendered
        )
    return [
        motion_id
        for motion_id, samples in sorted(tracks.items())
        if {
            round(float(time_sec), 9) for time_sec, _value in samples
        } != curve_times.get(motion_id, set())
    ]


def _layer_time_bounds(layer: Mapping[str, Any]) -> tuple[float, float]:
    times = [
        _finite(frame.get('time_sec'), '프레임 시간')
        for frame in layer.get('frames') or []
        if isinstance(frame, Mapping)
    ]
    if not times:
        raise ValueError(
            f"레이어 합치기 중단 · '{layer.get('name') or layer.get('layer_id')}'의 "
            '모션 데이터가 없습니다'
        )
    return min(times), max(times)


def _shift_layer_time(layer: Mapping[str, Any], offset_sec: float) -> Dict[str, Any]:
    shifted = copy.deepcopy(dict(layer))
    offset = round(max(0.0, float(offset_sec)), 9)
    if offset <= EPSILON:
        return shifted
    for frame in shifted.get('frames') or []:
        frame['time_sec'] = round(
            _finite(frame.get('time_sec'), '프레임 시간') + offset, 9
        )
    for curve in shifted.get('point_curves') or []:
        for point in curve.get('points') or []:
            point['time_sec'] = round(
                _finite(point.get('time_sec'), '포인트 시간') + offset, 9
            )
    return shifted


def merge_layers(
    project: Dict[str, Any],
    layer_ids: Iterable[Any],
    *,
    name: Any = '합친 레이어',
    append_layer_id: Any = '',
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
        uncovered = layer_point_coverage_issues(layer)
        if uncovered:
            raise ValueError(
                f"레이어 합치기 중단 · '{layer.get('name') or layer.get('layer_id')}'의 "
                '전체 모션축에 포인트를 먼저 생성하세요: '
                + ', '.join(uncovered)
            )
    append_id = str(append_layer_id or '')
    append_offset_sec = 0.0
    if append_id:
        if append_id not in selected_ids:
            raise ValueError('뒤로 이동할 레이어가 합치기 대상에 포함되지 않았습니다')
        append_index = next(
            index for index, layer in enumerate(selected_layers)
            if str(layer.get('layer_id') or '') == append_id
        )
        stationary_layers = [
            layer for index, layer in enumerate(selected_layers)
            if index != append_index
        ]
        stationary_end = max(
            _layer_time_bounds(layer)[1] for layer in stationary_layers
        )
        append_start, _append_end = _layer_time_bounds(selected_layers[append_index])
        period_sec = _finite(
            project.get('period_sec') or DEFAULT_PERIOD_SEC, '모션 주기'
        )
        if period_sec <= 0.0:
            raise ValueError('모션 주기는 0보다 커야 합니다')
        append_offset_sec = round(max(
            0.0, stationary_end + period_sec - append_start
        ), 9)
        selected_layers[append_index] = _shift_layer_time(
            selected_layers[append_index], append_offset_sec
        )
    temporary = {
        'period_sec': DEFAULT_PERIOD_SEC,
        'transition_safety_level': project.get('transition_safety_level', 4),
        'layers': selected_layers,
    }
    conflicts = layer_conflicts(temporary)
    if conflicts:
        first = conflicts[0]
        raise ValueError(
            '레이어 합치기 중단 · 시간 충돌: '
            f"{first['motion_id']} · {first['first_layer_name']} / "
            f"{first['second_layer_name']} · "
            f"{first['start_sec']:.3f}~{first['end_sec']:.3f}초"
        )
    frames = render_project(
        temporary,
        motion_ranges_deg=motion_ranges_deg,
        initial_motion_values_deg=initial_motion_values_deg,
        require_safe_transitions=False,
    )
    merged_point_curves = collect_merged_point_curves(selected_layers)
    merged = normalize_layer({
        'layer_id': f'merged_{uuid.uuid4().hex[:8]}',
        'name': str(name or '합친 레이어').strip()[:40] or '합친 레이어',
        'enabled': True,
        'locked': False,
        'source_layer_ids': sorted(selected_ids),
        'frames': frames,
        'point_curves': merged_point_curves,
    })
    merged['merge_report'] = {
        'mode': 'append' if append_id else 'preserve',
        'append_layer_id': append_id,
        'append_offset_sec': append_offset_sec,
    }
    return merged
