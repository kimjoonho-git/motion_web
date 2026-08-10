"""Relay operator-facing motion display fields from motion_run / heartbeat."""

from __future__ import annotations

from typing import Any, Dict, Mapping

_PROGRESS_PHASES = {
    'running', 'initializing', 'preparing', 'countdown',
    'group_preparing', 'group_armed', 'group_start_scheduled',
    'group_initialize_scheduled', 'group_cycle_initialize_scheduled',
}
_PROGRESS_STATES = {
    'running', 'initializing', 'preparing', 'countdown', 'armed',
    'start_scheduled', 'waiting',
}


def _text(value: Any) -> str:
    return str(value or '').strip()


def _progress_text(peer: Mapping[str, Any]) -> str:
    phase = _text(peer.get('motion_phase'))
    state = _text(peer.get('motion_state'))
    show = phase in _PROGRESS_PHASES or state in _PROGRESS_STATES
    duration = float(peer.get('motion_duration_sec') or 0.0)
    elapsed = float(peer.get('motion_elapsed_sec') or 0.0)
    if not show or duration <= 0.0:
        return '-'
    percent = round(float(peer.get('motion_progress_ratio') or 0.0) * 100.0)
    return f'{elapsed:.2f} / {duration:.2f}초 · {percent}%'


def enrich_peer_row(
    peer: Mapping[str, Any],
    *,
    execution_active: bool,
) -> Dict[str, Any]:
    if not execution_active:
        return {
            **dict(peer),
            'motion_cycle': 0,
            'motion_cycle_text': '-',
            'motion_step': '그룹 대기',
            'motion_progress': '-',
        }
    cycle = int(peer.get('display_cycle') or peer.get('current_cycle') or 0)
    step = _text(peer.get('display_step')) or '확인 중'
    return {
        **dict(peer),
        'display_cycle': cycle,
        'display_step': step,
        'motion_cycle': cycle,
        'motion_cycle_text': f'{cycle}회차' if cycle > 0 else '-',
        'motion_step': step,
        'motion_progress': _progress_text(peer),
    }
