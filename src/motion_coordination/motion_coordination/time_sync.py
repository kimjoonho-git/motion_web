"""Small chrony inspection helper used only for synchronized starts."""

import re
import subprocess
from typing import Any, Callable, Dict


_OFFSET = re.compile(
    r'^(?:Last offset|System time)\s*:\s*([+-]?[0-9.eE+-]+)\s+seconds',
    re.MULTILINE,
)
_LEAP = re.compile(r'^Leap status\s*:\s*(.+)$', re.MULTILINE)


def inspect_time_sync(
    runner: Callable[..., Any] = subprocess.run, *, max_offset_ms: float = 10.0,
) -> Dict[str, Any]:
    """Return a safe clock-sync summary without host or server identifiers."""
    try:
        completed = runner(
            ['chronyc', 'tracking'], check=False, capture_output=True,
            text=True, timeout=2.0,
        )
    except (OSError, subprocess.SubprocessError):
        return {'clock_sync_state': 'unavailable', 'clock_source': 'chrony'}
    text = str(completed.stdout or '')
    offset_match = _OFFSET.search(text)
    leap_match = _LEAP.search(text)
    if completed.returncode != 0 or not offset_match:
        return {'clock_sync_state': 'unavailable', 'clock_source': 'chrony'}
    offset_ms = abs(float(offset_match.group(1))) * 1000.0
    normal = not leap_match or leap_match.group(1).strip().lower() == 'normal'
    return {
        'clock_sync_state': (
            'ready' if normal and offset_ms <= max_offset_ms else 'out_of_tolerance'
        ),
        'clock_offset_ms': round(offset_ms, 6),
        'clock_source': 'chrony',
    }
