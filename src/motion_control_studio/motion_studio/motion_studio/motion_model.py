"""Shared normalization and inspection helpers for Motion Studio data."""

from __future__ import annotations

import math
import re
import time
from typing import Any, Dict, Iterable, List, Mapping

from .constants import DEFAULT_PERIOD_SEC


MOTION_ID_PATTERN = re.compile(r'^[1-9]\d*-[1-9]\d*$')


def safe_name(value: Any, fallback: str = 'motion_project') -> str:
    text = str(value or '').strip()
    cleaned = ''.join(
        character if character.isalnum() or character in ('-', '_') else '_'
        for character in text
    ).strip('_')
    return cleaned or fallback


def finite_float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def nonnegative_int(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def unique_motion_ids(values: Iterable[Any]) -> List[str]:
    result = []
    seen = set()
    for value in values:
        motion_id = str(value or '').strip()
        if not motion_id or motion_id in seen:
            continue
        if not MOTION_ID_PATTERN.match(motion_id):
            raise ValueError(f'invalid Motion ID: {motion_id}')
        seen.add(motion_id)
        result.append(motion_id)
    return result


def layer_motion_ids(layer: Mapping[str, Any]) -> set[str]:
    result = {
        str(motion_id)
        for frame in layer.get('frames') or []
        if isinstance(frame, Mapping)
        for motion_id in (frame.get('values') or {})
        if str(motion_id)
    }
    result.update(
        str(curve.get('motion_id') or '')
        for curve in layer.get('point_curves') or []
        if isinstance(curve, Mapping) and str(curve.get('motion_id') or '')
    )
    return result


def point_curve_bounds(curve: Mapping[str, Any]) -> tuple[float, float]:
    points = curve.get('points') or []
    if not points:
        raise ValueError('point curve requires at least one point')
    times = [finite_float(point.get('time_sec'), math.nan) for point in points]
    if any(not math.isfinite(value) for value in times):
        raise ValueError('point curve contains a non-finite time')
    return min(times), max(times)


def normalize_layer(layer: Any, index: int = 0) -> Dict[str, Any]:
    if not isinstance(layer, dict):
        raise ValueError('layer must be an object')
    frames = []
    for frame_index, frame in enumerate(layer.get('frames') or [], start=1):
        if not isinstance(frame, dict):
            continue
        values = {}
        source_values = frame.get('values') if isinstance(frame.get('values'), dict) else {}
        for motion_id, value in source_values.items():
            text = str(motion_id or '').strip()
            if not MOTION_ID_PATTERN.match(text):
                raise ValueError(f'invalid Motion ID in layer: {text}')
            number = finite_float(value, math.nan)
            if not math.isfinite(number):
                raise ValueError(f'non-finite motion value: {text}')
            values[text] = number
        frames.append({
            'frame': int(frame.get('frame') or frame_index),
            'time_sec': round(finite_float(
                frame.get('time_sec'), frame_index * DEFAULT_PERIOD_SEC
            ), 9),
            'values': values,
        })
    frames.sort(key=lambda item: (item['time_sec'], item['frame']))
    point_curves = []
    seen_curve_ids = set()
    for curve_index, curve in enumerate(layer.get('point_curves') or []):
        if not isinstance(curve, dict):
            continue
        curve_id = safe_name(curve.get('curve_id'), f'curve_{curve_index + 1}')
        if curve_id in seen_curve_ids:
            raise ValueError(f'duplicated point curve id: {curve_id}')
        seen_curve_ids.add(curve_id)
        motion_id = str(curve.get('motion_id') or '').strip()
        if not MOTION_ID_PATTERN.match(motion_id):
            raise ValueError(f'invalid Motion ID in point curve: {motion_id}')
        interpolation_order = curve.get('interpolation_order')
        if interpolation_order is None:
            legacy_points = [
                point for point in curve.get('points') or [] if isinstance(point, dict)
            ]
            interpolation_order = (
                1 if legacy_points and all(
                    point.get('tangent_mode') == 'linear' for point in legacy_points
                ) else 3
            )
        try:
            interpolation_order = int(interpolation_order)
        except (TypeError, ValueError) as exc:
            raise ValueError(f'invalid point curve order: {interpolation_order}') from exc
        if interpolation_order not in {1, 3, 5}:
            raise ValueError(f'invalid point curve order: {interpolation_order}')
        points = []
        seen_point_ids = set()
        for point_index, point in enumerate(curve.get('points') or []):
            if not isinstance(point, dict):
                continue
            point_id = safe_name(point.get('point_id'), f'point_{point_index + 1}')
            if point_id in seen_point_ids:
                raise ValueError(f'duplicated point id in curve: {point_id}')
            seen_point_ids.add(point_id)
            tangent_mode = str(point.get('tangent_mode') or 'auto')
            if tangent_mode not in {'auto', 'smooth', 'broken', 'linear'}:
                raise ValueError(f'invalid tangent mode: {tangent_mode}')
            in_handle = point.get('in_handle') if isinstance(point.get('in_handle'), dict) else {}
            out_handle = point.get('out_handle') if isinstance(point.get('out_handle'), dict) else {}
            points.append({
                'point_id': point_id,
                'time_sec': round(finite_float(point.get('time_sec'), 0.0), 9),
                'value_deg': finite_float(point.get('value_deg'), 0.0),
                'tangent_mode': tangent_mode,
                'in_handle': {
                    'dt_sec': finite_float(in_handle.get('dt_sec'), 0.0),
                    'dv_deg': finite_float(in_handle.get('dv_deg'), 0.0),
                },
                'out_handle': {
                    'dt_sec': finite_float(out_handle.get('dt_sec'), 0.0),
                    'dv_deg': finite_float(out_handle.get('dv_deg'), 0.0),
                },
            })
        points.sort(key=lambda item: (item['time_sec'], item['point_id']))
        if len(points) < 2:
            raise ValueError('point curve requires at least two points')
        point_curves.append({
            'curve_id': curve_id,
            'motion_id': motion_id,
            'interpolation_order': interpolation_order,
            'points': points,
        })
    return {
        'layer_id': safe_name(layer.get('layer_id'), f'layer_{index + 1}'),
        'name': str(layer.get('name') or f'레이어 {index + 1}').strip()[:40]
        or f'레이어 {index + 1}',
        'enabled': layer.get('enabled') is not False,
        'locked': bool(layer.get('locked', False)),
        'created_at': finite_float(layer.get('created_at'), time.time()),
        'source_motion_file_id': str(layer.get('source_motion_file_id') or ''),
        'source_layer_ids': [
            str(value) for value in layer.get('source_layer_ids') or [] if str(value)
        ],
        'copied_from_layer_id': str(layer.get('copied_from_layer_id') or ''),
        'edit_revision': nonnegative_int(layer.get('edit_revision')),
        'point_curves': point_curves,
        'frames': frames,
    }
