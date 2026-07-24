"""Shared, read-only validation for calculated or persisted motion layers."""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping, Sequence

from .curve_engine import point_curve_order, render_point_curve


EPSILON = 1e-9


def validate_ranges(
    layer: Dict[str, Any], motion_ranges_deg: Mapping[str, Sequence[float]]
) -> List[Dict[str, Any]]:
    issues = []
    for frame in layer.get('frames') or []:
        for motion_id, raw_value in (frame.get('values') or {}).items():
            limits = motion_ranges_deg.get(str(motion_id))
            if not limits or len(limits) < 2:
                continue
            value = float(raw_value)
            lower, upper = sorted((float(limits[0]), float(limits[1])))
            if value < lower - EPSILON or value > upper + EPSILON:
                issues.append({
                    'motion_id': str(motion_id),
                    'time_sec': float(frame.get('time_sec') or 0.0),
                    'value_deg': value,
                    'lower_deg': lower,
                    'upper_deg': upper,
                })
    return issues


def point_curve_frame_mismatches(
    layer: Dict[str, Any], *, tolerance_deg: float = 1e-6
) -> List[Dict[str, Any]]:
    """Report point curves whose persisted 20 ms frames are not their result."""
    frame_values: Dict[tuple[float, str], float] = {}
    for frame in layer.get('frames') or []:
        time_sec = round(float(frame.get('time_sec') or 0.0), 9)
        for motion_id, value in (frame.get('values') or {}).items():
            frame_values[(time_sec, str(motion_id))] = float(value)

    issues = []
    for curve in layer.get('point_curves') or []:
        motion_id = str(curve.get('motion_id') or '')
        normalized_points, expected_samples = render_point_curve(
            curve.get('points') or [], point_curve_order(curve)
        )
        mismatch_count = 0
        maximum_delta = 0.0
        first_mismatch = None
        for time_sec, expected_value in expected_samples:
            actual_value = frame_values.get((round(time_sec, 9), motion_id))
            delta = (
                abs(float(actual_value) - float(expected_value))
                if actual_value is not None else float('inf')
            )
            if delta <= tolerance_deg:
                continue
            mismatch_count += 1
            maximum_delta = max(maximum_delta, delta)
            if first_mismatch is None:
                first_mismatch = {
                    'time_sec': round(float(time_sec), 9),
                    'point_value_deg': round(float(expected_value), 6),
                    'frame_value_deg': (
                        round(float(actual_value), 6) if actual_value is not None else None
                    ),
                }
        if mismatch_count:
            issues.append({
                'curve_id': str(curve.get('curve_id') or ''),
                'motion_id': motion_id,
                'start_sec': float(normalized_points[0]['time_sec']),
                'end_sec': float(normalized_points[-1]['time_sec']),
                'mismatch_count': mismatch_count,
                'max_delta_deg': (
                    None if maximum_delta == float('inf') else round(maximum_delta, 6)
                ),
                'first_mismatch': first_mismatch,
            })
    return issues


def project_point_curve_frame_mismatches(
    project: Dict[str, Any], layer_ids: Iterable[Any] | None = None
) -> List[Dict[str, Any]]:
    selected = {str(value) for value in layer_ids or [] if str(value)}
    result = []
    for layer in project.get('layers') or []:
        layer_id = str(layer.get('layer_id') or '')
        if selected and layer_id not in selected:
            continue
        for issue in point_curve_frame_mismatches(layer):
            result.append({
                **issue,
                'layer_id': layer_id,
                'layer_name': str(layer.get('name') or layer_id or '이름 없는 레이어'),
            })
    return result
