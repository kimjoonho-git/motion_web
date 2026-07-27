"""Point-curve edit operations shared by the layer command dispatcher."""

from __future__ import annotations

import copy
from typing import Any, Dict, List, Sequence

from .curve_engine import (
    EPSILON,
    frame_time,
    point_curve_order,
    render_point_curve,
)


def curve_bounds(curve: Dict[str, Any]) -> tuple[float, float]:
    points = curve.get('points') or []
    return float(points[0]['time_sec']), float(points[-1]['time_sec'])


def transform_point_curve(
    curve: Dict[str, Any],
    operation: str,
    *,
    start_sec: float,
    end_sec: float,
    delta_sec: float = 0.0,
    factor: float = 1.0,
    time_pivot: float = 0.0,
    offset_deg: float = 0.0,
    value_pivot: float = 0.0,
) -> tuple[Dict[str, Any], List[tuple[float, float]]]:
    """Transform point metadata and render matching 20 ms samples."""
    transformed = copy.deepcopy(curve)
    normalized_points, _rendered = render_point_curve(
        transformed.get('points') or [], point_curve_order(transformed)
    )
    transformed['points'] = normalized_points
    for point in transformed['points']:
        point_time = float(point['time_sec'])
        if not start_sec - EPSILON <= point_time <= end_sec + EPSILON:
            continue
        if operation == 'time_shift':
            target = point_time + delta_sec
            if target < -EPSILON:
                raise ValueError('편집 결과가 0초보다 앞으로 이동합니다')
            point['time_sec'] = frame_time(target)
        elif operation == 'time_scale':
            point['time_sec'] = frame_time(
                time_pivot + ((point_time - time_pivot) * factor)
            )
        elif operation == 'value_offset':
            point['value_deg'] = float(point['value_deg']) + offset_deg
        elif operation == 'value_scale':
            point['value_deg'] = value_pivot + (
                (float(point['value_deg']) - value_pivot) * factor
            )
        for handle_name in ('in_handle', 'out_handle'):
            handle = point.get(handle_name)
            if not isinstance(handle, dict):
                continue
            if operation == 'time_scale' and handle.get('dt_sec') is not None:
                handle['dt_sec'] = float(handle['dt_sec']) * factor
            if operation == 'value_scale' and handle.get('dv_deg') is not None:
                handle['dv_deg'] = float(handle['dv_deg']) * factor
    normalized_points, rendered = render_point_curve(
        transformed.get('points') or [], point_curve_order(transformed)
    )
    transformed['points'] = normalized_points
    return transformed, rendered


def validate_point_curve_overlaps(curves: Sequence[Dict[str, Any]]) -> None:
    by_motion_id: Dict[str, List[tuple[float, float]]] = {}
    for curve in curves:
        by_motion_id.setdefault(str(curve.get('motion_id') or ''), []).append(
            curve_bounds(curve)
        )
    for motion_id, ranges in by_motion_id.items():
        ordered = sorted(ranges)
        for previous, following in zip(ordered, ordered[1:]):
            if following[0] <= previous[1] + EPSILON:
                raise ValueError(
                    f'{motion_id}의 포인트 곡선 구간이 이동 후 서로 겹칩니다'
                )
