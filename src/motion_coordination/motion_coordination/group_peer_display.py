"""Single source of truth for group peer motion display fields."""

from __future__ import annotations

from typing import Any, Dict, Mapping

_MOTION_STEP_LABELS = {
    'idle': '대기',
    'preparing': '준비 확인',
    'initializing': '초기위치 이동',
    'initialized': '초기화 완료',
    'armed': '시작 대기',
    'start_scheduled': '시작 예약',
    'waiting': '예약 대기',
    'running': '모션 실행 중',
    'countdown': '시작 카운트',
    'motion_completed': '회차 완료',
    'cycle_ready': '다음 회차 대기',
    'waiting_cycle_ready': '회차 준비 중',
    'stop_after_cycle': '현재 회차 후 정지 대기',
    'releasing': '그룹 실행 정리 중',
    'stopped': '정지',
    'error': '오류',
    'ready': '그룹 대기',
    'group_preparing': '그룹 준비',
    'group_armed': '시작 대기',
    'group_start_scheduled': '시작 예약',
    'group_initialize_scheduled': '초기 위치 이동 예약',
    'group_motion_completed': '회차 완료',
    'group_cycle_initialized': '다음 회차 준비',
    'group_cycle_initialize_scheduled': '회차 초기화 예약',
}

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


def motion_step_label(
    *,
    motion_phase: str,
    motion_state: str,
    execution_active: bool,
) -> str:
    phase = _text(motion_phase)
    state = _text(motion_state)
    if phase == 'initializing' or (
        phase in {'group_initialize_scheduled', 'group_cycle_initialize_scheduled'}
        and state in {'waiting', 'initializing'}
    ):
        return '초기위치 이동'
    if phase in _MOTION_STEP_LABELS:
        return _MOTION_STEP_LABELS[phase]
    if state in _MOTION_STEP_LABELS:
        return _MOTION_STEP_LABELS[state]
    if execution_active:
        return '확인 중'
    return '그룹 대기'


def motion_cycle_number(
    *,
    motion_phase: str,
    motion_state: str,
    current_cycle: int,
    execution_cycle: int,
    execution_active: bool,
) -> int:
    if not execution_active:
        return 0
    phase = _text(motion_phase)
    state = _text(motion_state)
    base = max(int(current_cycle or 0), int(execution_cycle or 0))

    if phase == 'group_start_scheduled' and int(current_cycle or 0) > 0:
        return int(current_cycle)
    if phase in {'running', 'group_motion_completed', 'motion_completed'} and base > 0:
        return base
    if phase == 'group_cycle_initialize_scheduled' and base > 0:
        return base + 1
    if phase == 'initializing' and base > 0:
        if int(current_cycle or 0) > int(execution_cycle or 0):
            return int(current_cycle)
        return base + 1
    if phase == 'group_cycle_initialized' and base > 0:
        return base + 1
    if phase in {'group_armed', 'armed', 'cycle_ready', 'start_scheduled'} and base > 0:
        return base + 1 if phase in {'group_cycle_initialized', 'cycle_ready'} else base
    if base > 0:
        return base
    return 0


def motion_progress_text(
    *,
    motion_phase: str,
    motion_state: str,
    elapsed_sec: float,
    duration_sec: float,
    progress_ratio: float,
) -> str:
    phase = _text(motion_phase)
    state = _text(motion_state)
    show = phase in _PROGRESS_PHASES or state in _PROGRESS_STATES
    duration = float(duration_sec or 0.0)
    elapsed = float(elapsed_sec or 0.0)
    if not show or duration <= 0.0:
        return '-'
    percent = round(float(progress_ratio or 0.0) * 100.0)
    return f'{elapsed:.2f} / {duration:.2f}초 · {percent}%'


def build_peer_motion_view(
    *,
    motion_phase: str = '',
    motion_state: str = '',
    current_cycle: int = 0,
    execution_cycle: int = 0,
    execution_active: bool = False,
    elapsed_sec: float = 0.0,
    duration_sec: float = 0.0,
    progress_ratio: float = 0.0,
) -> Dict[str, Any]:
    cycle = motion_cycle_number(
        motion_phase=motion_phase,
        motion_state=motion_state,
        current_cycle=int(current_cycle or 0),
        execution_cycle=int(execution_cycle or 0),
        execution_active=bool(execution_active),
    )
    step = motion_step_label(
        motion_phase=motion_phase,
        motion_state=motion_state,
        execution_active=execution_active,
    )
    progress = motion_progress_text(
        motion_phase=motion_phase,
        motion_state=motion_state,
        elapsed_sec=elapsed_sec,
        duration_sec=duration_sec,
        progress_ratio=progress_ratio,
    )
    return {
        'motion_cycle': cycle,
        'motion_cycle_text': f'{cycle}회차' if cycle > 0 else '-',
        'motion_step': step,
        'motion_progress': progress,
    }


def enrich_peer_row(
    peer: Mapping[str, Any],
    *,
    execution_active: bool,
    execution_cycle: int,
) -> Dict[str, Any]:
    view = build_peer_motion_view(
        motion_phase=_text(peer.get('motion_phase')),
        motion_state=_text(peer.get('motion_state')),
        current_cycle=int(peer.get('current_cycle') or 0),
        execution_cycle=int(execution_cycle or 0),
        execution_active=execution_active,
        elapsed_sec=float(peer.get('motion_elapsed_sec') or 0.0),
        duration_sec=float(peer.get('motion_duration_sec') or 0.0),
        progress_ratio=float(peer.get('motion_progress_ratio') or 0.0),
    )
    return {**dict(peer), **view}
