"""Pure curve calculations used only by the motion-studio editor boundary.

The functions in this module have no ROS, filesystem, project, MIDI, or motor
dependencies.  They turn editor descriptions into deterministic 20 ms motion
samples so every graph-editing operation uses one calculation source.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Sequence

from .constants import DEFAULT_PERIOD_SEC

EPSILON = 1e-9
MAX_TIME_SCALE = 100.0


def point_curve_order(curve: Dict[str, Any]) -> int:
    """Return a validated curve order, including legacy straight curves."""
    raw_order = curve.get('interpolation_order')
    if raw_order is None:
        points = [point for point in curve.get('points') or [] if isinstance(point, dict)]
        if points and all(point.get('tangent_mode') == 'linear' for point in points):
            return 1
        return 3
    try:
        order = int(raw_order)
    except (TypeError, ValueError) as exc:
        raise ValueError('포인트 곡선은 직선, 3차, 5차 중 하나여야 합니다') from exc
    if order not in {1, 3, 5}:
        raise ValueError('포인트 곡선은 직선, 3차, 5차 중 하나여야 합니다')
    return order


def finite(value: Any, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f'{label} 값이 올바르지 않습니다') from exc
    if not math.isfinite(number):
        raise ValueError(f'{label} 값이 올바르지 않습니다')
    return number


def frame_time(value: Any) -> float:
    return round(
        max(0.0, finite(value, '시간')) / DEFAULT_PERIOD_SEC
    ) * DEFAULT_PERIOD_SEC


def linear_sample(points: Sequence[tuple[float, float]], time_sec: float) -> float:
    if len(points) == 1:
        return float(points[0][1])
    for index, point in enumerate(points):
        if time_sec <= point[0] + EPSILON:
            if index == 0:
                return float(point[1])
            before = points[index - 1]
            span = point[0] - before[0]
            if span <= EPSILON:
                return float(point[1])
            ratio = (time_sec - before[0]) / span
            return float(before[1] + ((point[1] - before[1]) * ratio))
    return float(points[-1][1])


def interpolation_ratio(progress: float, order: int) -> float:
    progress = max(0.0, min(1.0, float(progress)))
    if order == 1:
        return progress
    if order == 3:
        return (3.0 * (progress ** 2)) - (2.0 * (progress ** 3))
    if order == 5:
        return (
            (10.0 * (progress ** 3))
            - (15.0 * (progress ** 4))
            + (6.0 * (progress ** 5))
        )
    raise ValueError('보간 그래프는 1차, 3차, 5차 중 하나여야 합니다')


def interpolate_range(
    points: Sequence[tuple[float, float]],
    start_sec: float,
    end_sec: float,
    order: int,
) -> List[tuple[float, float]]:
    if end_sec - start_sec < DEFAULT_PERIOD_SEC - EPSILON:
        raise ValueError('보간 구간은 최소 20ms 이상이어야 합니다')
    boundaries = {
        round(point_time, 9): float(value) for point_time, value in points
    }
    start_value = boundaries.get(round(start_sec, 9))
    end_value = boundaries.get(round(end_sec, 9))
    if start_value is None or end_value is None:
        raise ValueError('보간 시작점과 끝점에는 실제 모션 데이터가 있어야 합니다')
    count = int(round((end_sec - start_sec) / DEFAULT_PERIOD_SEC))
    result = []
    for index in range(count + 1):
        time_sec = round(start_sec + (index * DEFAULT_PERIOD_SEC), 9)
        ratio = interpolation_ratio(index / count, order)
        result.append((time_sec, start_value + ((end_value - start_value) * ratio)))
    result[0] = (start_sec, start_value)
    result[-1] = (end_sec, end_value)
    return result


def scale_time_segment(
    points: Sequence[tuple[float, float]], anchor: float, factor: float
) -> List[tuple[float, float]]:
    if factor <= 0.0 or factor > MAX_TIME_SCALE:
        raise ValueError(f'시간 배율은 0보다 크고 {MAX_TIME_SCALE:g} 이하여야 합니다')
    transformed = [
        (anchor + ((time_sec - anchor) * factor), value)
        for time_sec, value in points
    ]
    start = frame_time(transformed[0][0])
    end = frame_time(transformed[-1][0])
    count = max(0, int(round((end - start) / DEFAULT_PERIOD_SEC)))
    result = []
    for index in range(count + 1):
        time_sec = round(start + (index * DEFAULT_PERIOD_SEC), 9)
        original_time = anchor + ((time_sec - anchor) / factor)
        result.append((time_sec, linear_sample(points, original_time)))
    if result:
        result[0] = (result[0][0], float(points[0][1]))
        result[-1] = (result[-1][0], float(points[-1][1]))
    return result


def _automatic_slope(points: Sequence[Dict[str, Any]], index: int) -> float:
    if len(points) < 2:
        return 0.0
    before = points[max(0, index - 1)]
    after = points[min(len(points) - 1, index + 1)]
    span = float(after['time_sec']) - float(before['time_sec'])
    if span <= EPSILON:
        return 0.0
    return (float(after['value_deg']) - float(before['value_deg'])) / span


def _automatic_acceleration(points: Sequence[Dict[str, Any]], index: int) -> float:
    if index <= 0 or index >= len(points) - 1:
        return 0.0
    before, point, after = points[index - 1], points[index], points[index + 1]
    previous_span = float(point['time_sec']) - float(before['time_sec'])
    following_span = float(after['time_sec']) - float(point['time_sec'])
    if previous_span <= EPSILON or following_span <= EPSILON:
        return 0.0
    previous_slope = (
        float(point['value_deg']) - float(before['value_deg'])
    ) / previous_span
    following_slope = (
        float(after['value_deg']) - float(point['value_deg'])
    ) / following_span
    return 2.0 * (following_slope - previous_slope) / (
        previous_span + following_span
    )


def prepare_curve_points(
    raw_points: Sequence[Any], interpolation_order: int = 3
) -> List[Dict[str, Any]]:
    """Normalize user-created points and their data-coordinate handles."""
    points = []
    for index, raw in enumerate(raw_points):
        if not isinstance(raw, dict):
            raise ValueError('포인트 데이터가 올바르지 않습니다')
        point = {
            'point_id': str(raw.get('point_id') or f'point_{index + 1}'),
            'time_sec': round(frame_time(raw.get('time_sec')), 9),
            'value_deg': finite(raw.get('value_deg'), '포인트 모션값'),
            'tangent_mode': str(raw.get('tangent_mode') or 'auto'),
            'in_handle': dict(raw.get('in_handle') or {}),
            'out_handle': dict(raw.get('out_handle') or {}),
        }
        if point['tangent_mode'] not in {'auto', 'smooth', 'broken', 'linear'}:
            raise ValueError('탄젠트 방식은 자동, 부드럽게, 분리 중 하나여야 합니다')
        points.append(point)
    points.sort(key=lambda item: (item['time_sec'], item['point_id']))
    if len(points) < 2:
        raise ValueError('포인트 곡선에는 포인트가 두 개 이상 필요합니다')
    if any(
        points[index]['time_sec'] - points[index - 1]['time_sec']
        < DEFAULT_PERIOD_SEC - EPSILON
        for index in range(1, len(points))
    ):
        raise ValueError('포인트 시간은 서로 최소 20ms 이상 떨어져야 합니다')

    if interpolation_order not in {1, 3, 5}:
        raise ValueError('포인트 곡선은 직선, 3차, 5차 중 하나여야 합니다')

    for index, point in enumerate(points):
        previous_span = (
            point['time_sec'] - points[index - 1]['time_sec'] if index else 0.0
        )
        following_span = (
            points[index + 1]['time_sec'] - point['time_sec']
            if index + 1 < len(points) else 0.0
        )
        slope = _automatic_slope(points, index)
        mode = point['tangent_mode']
        is_boundary = index == 0 or index == len(points) - 1
        # Legacy projects stored "linear" as a tangent mode.  It remains
        # readable, while new projects store straightness at curve level.
        if mode == 'linear':
            mode = 'auto'
        if interpolation_order == 1:
            in_slope = (
                (point['value_deg'] - points[index - 1]['value_deg']) / previous_span
                if previous_span else 0.0
            )
            out_slope = (
                (points[index + 1]['value_deg'] - point['value_deg']) / following_span
                if following_span else 0.0
            )
            in_dt = -(previous_span / 3.0) if previous_span else 0.0
            out_dt = (following_span / 3.0) if following_span else 0.0
            point['in_handle'] = {'dt_sec': in_dt, 'dv_deg': in_slope * in_dt}
            point['out_handle'] = {'dt_sec': out_dt, 'dv_deg': out_slope * out_dt}
            continue
        if mode in {'auto', 'broken'} or is_boundary:
            in_dt = -(previous_span / 3.0) if previous_span else 0.0
            out_dt = (following_span / 3.0) if following_span else 0.0
            applied_slope = 0.0 if mode == 'broken' or is_boundary else slope
            point['in_handle'] = {'dt_sec': in_dt, 'dv_deg': applied_slope * in_dt}
            point['out_handle'] = {'dt_sec': out_dt, 'dv_deg': applied_slope * out_dt}
            continue

        in_dt = max(-previous_span / 2.0, min(0.0, finite(
            point['in_handle'].get('dt_sec', -(previous_span / 3.0)), '들어오는 핸들 시간'
        ))) if previous_span else 0.0
        out_dt = min(following_span / 2.0, max(0.0, finite(
            point['out_handle'].get('dt_sec', following_span / 3.0), '나가는 핸들 시간'
        ))) if following_span else 0.0
        in_dv = finite(point['in_handle'].get('dv_deg', slope * in_dt), '들어오는 핸들 값')
        out_dv = finite(point['out_handle'].get('dv_deg', slope * out_dt), '나가는 핸들 값')
        slopes = []
        if abs(in_dt) > EPSILON:
            slopes.append(in_dv / in_dt)
        if abs(out_dt) > EPSILON:
            slopes.append(out_dv / out_dt)
        shared = sum(slopes) / len(slopes) if slopes else slope
        in_dv, out_dv = shared * in_dt, shared * out_dt
        point['in_handle'] = {'dt_sec': in_dt, 'dv_deg': in_dv}
        point['out_handle'] = {'dt_sec': out_dt, 'dv_deg': out_dv}
    return points


def _point_slope(points: Sequence[Dict[str, Any]], index: int) -> float:
    if index == 0 or index == len(points) - 1:
        return 0.0
    point = points[index]
    if point['tangent_mode'] == 'broken':
        return 0.0
    handle = point.get('out_handle') or point.get('in_handle') or {}
    dt_sec = float(handle.get('dt_sec') or 0.0)
    if abs(dt_sec) <= EPSILON:
        return _automatic_slope(points, index)
    return float(handle.get('dv_deg') or 0.0) / dt_sec


def _sample_polynomial_segment(
    points: Sequence[Dict[str, Any]], index: int, time_sec: float,
    interpolation_order: int,
) -> float:
    first, second = points[index], points[index + 1]
    start = float(first['time_sec'])
    end = float(second['time_sec'])
    span = end - start
    ratio = max(0.0, min(1.0, (time_sec - start) / span))
    first_value = float(first['value_deg'])
    second_value = float(second['value_deg'])
    if interpolation_order == 1:
        return first_value + ((second_value - first_value) * ratio)

    first_slope = _point_slope(points, index)
    second_slope = _point_slope(points, index + 1)
    if interpolation_order == 3:
        ratio2, ratio3 = ratio * ratio, ratio * ratio * ratio
        return (
            ((2 * ratio3) - (3 * ratio2) + 1) * first_value
            + (ratio3 - (2 * ratio2) + ratio) * span * first_slope
            + ((-2 * ratio3) + (3 * ratio2)) * second_value
            + (ratio3 - ratio2) * span * second_slope
        )

    first_acceleration = (
        0.0 if first['tangent_mode'] == 'broken'
        else _automatic_acceleration(points, index)
    )
    second_acceleration = (
        0.0 if second['tangent_mode'] == 'broken'
        else _automatic_acceleration(points, index + 1)
    )
    delta = second_value - first_value
    c0 = first_value
    c1 = first_slope * span
    c2 = 0.5 * first_acceleration * span * span
    remaining_value = delta - c1 - c2
    remaining_slope = (second_slope * span) - c1 - (2.0 * c2)
    remaining_acceleration = (
        (second_acceleration * span * span) - (2.0 * c2)
    )
    c3 = (
        (10.0 * remaining_value) - (4.0 * remaining_slope)
        + (0.5 * remaining_acceleration)
    )
    c4 = (
        (-15.0 * remaining_value) + (7.0 * remaining_slope)
        - remaining_acceleration
    )
    c5 = (
        (6.0 * remaining_value) - (3.0 * remaining_slope)
        + (0.5 * remaining_acceleration)
    )
    return (
        c0 + (c1 * ratio) + (c2 * ratio ** 2) + (c3 * ratio ** 3)
        + (c4 * ratio ** 4) + (c5 * ratio ** 5)
    )


def render_point_curve(
    raw_points: Sequence[Any], interpolation_order: int = 3
) -> tuple[List[Dict[str, Any]], List[tuple[float, float]]]:
    """Return normalized editor points and deterministic 20 ms samples."""
    points = prepare_curve_points(raw_points, interpolation_order)
    start = float(points[0]['time_sec'])
    end = float(points[-1]['time_sec'])
    count = int(round((end - start) / DEFAULT_PERIOD_SEC))
    samples = []
    segment_index = 0
    for index in range(count + 1):
        time_sec = round(start + (index * DEFAULT_PERIOD_SEC), 9)
        while (
            segment_index + 1 < len(points) - 1
            and time_sec > points[segment_index + 1]['time_sec'] + EPSILON
        ):
            segment_index += 1
        samples.append((
            time_sec,
            _sample_polynomial_segment(
                points, segment_index, time_sec, interpolation_order
            ),
        ))
    samples[0] = (start, float(points[0]['value_deg']))
    samples[-1] = (end, float(points[-1]['value_deg']))
    return points, samples
