"""Shared conversion helpers for Motion Studio mapping rows."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Dict


def mapping_rows(source: Any) -> list[dict[str, Any]]:
    if isinstance(source, dict):
        values = source.get('rows')
        if values is None:
            values = source.get('mapping_rows')
    else:
        values = source
    if not isinstance(values, Iterable) or isinstance(values, (str, bytes, dict)):
        return []
    return [row for row in values if isinstance(row, dict)]


def motion_ranges(source: Any) -> Dict[str, tuple[float, float]]:
    return {
        str(row['motion_id']): (
            float(row.get('motion_lower_deg', -180.0)),
            float(row.get('motion_upper_deg', 180.0)),
        )
        for row in mapping_rows(source)
        if row.get('motion_id')
    }


def manual_initial_values(source: Any) -> Dict[str, float]:
    return {
        str(row['motion_id']): float(row.get('initial_motion_position_deg', 0.0))
        for row in mapping_rows(source)
        if row.get('motion_id')
        and str(row.get('initial_mode') or 'first_frame') == 'manual'
    }
