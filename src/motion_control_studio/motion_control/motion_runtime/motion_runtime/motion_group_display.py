"""Operator-facing group motion display fields for motion_run status."""

from __future__ import annotations

from typing import Any, Dict, Mapping

_GROUP_STEP_BY_PHASE = {
    'group_preparing': '그룹 준비',
    'group_initialize_scheduled': '초기 위치 이동 예약',
    'group_armed': '시작 대기',
    'group_start_scheduled': '시작 예약',
    'group_motion_completed': '회차 완료',
    'group_cycle_initialize_scheduled': '회차 초기화 예약',
    'group_cycle_initialized': '다음 회차 준비',
    'initializing': '초기위치 이동',
    'initialized': '초기화 완료',
    'running': '모션 실행 중',
    'countdown': '시작 카운트',
    'armed': '시작 대기',
    'waiting': '예약 대기',
    'preparing': '준비 확인',
    'motion_completed': '회차 완료',
    'cycle_ready': '다음 회차 대기',
    'stopped': '정지',
    'error': '오류',
    'idle': '대기',
}

_GROUP_STEP_BY_STATE = {
    'initializing': '초기위치 이동',
    'initialized': '초기화 완료',
    'running': '모션 실행 중',
    'waiting': '예약 대기',
    'motion_completed': '회차 완료',
    'cycle_ready': '다음 회차 대기',
    'preparing': '준비 확인',
    'armed': '시작 대기',
    'stopped': '정지',
    'error': '오류',
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


def resolve_display_cycle(status: Mapping[str, Any]) -> int:
    if not bool(status.get('group_execution')):
        return 0
    if not _text(status.get('execution_id')):
        return 0
    cycle = int(status.get('group_cycle_number') or 0)
    if cycle > 0:
        return cycle
    cycle = int(status.get('current_cycle') or 0)
    return cycle if cycle > 0 else 0


def resolve_display_step(status: Mapping[str, Any]) -> str:
    if not bool(status.get('group_execution')):
        return '그룹 대기'
    if not _text(status.get('execution_id')):
        return '그룹 대기'
    phase = _text(status.get('phase'))
    state = _text(status.get('state'))
    if phase in _GROUP_STEP_BY_PHASE:
        return _GROUP_STEP_BY_PHASE[phase]
    if state in _GROUP_STEP_BY_STATE:
        return _GROUP_STEP_BY_STATE[state]
    return '확인 중'


def resolve_display_progress(status: Mapping[str, Any]) -> str:
    phase = _text(status.get('phase'))
    state = _text(status.get('state'))
    progress = status.get('progress')
    progress = progress if isinstance(progress, Mapping) else {}
    duration = float(progress.get('duration_sec') or 0.0)
    elapsed = float(progress.get('elapsed_sec') or 0.0)
    ratio = float(progress.get('ratio') or 0.0)
    show = phase in _PROGRESS_PHASES or state in _PROGRESS_STATES
    if not show or duration <= 0.0:
        return '-'
    return f'{elapsed:.2f} / {duration:.2f}초 · {round(ratio * 100.0)}%'


def apply_group_display(status: Dict[str, Any]) -> Dict[str, Any]:
    """Attach display_cycle, display_step, and keep cycle fields aligned."""
    result = dict(status)
    if not bool(result.get('group_execution')):
        result['display_cycle'] = 0
        result['display_step'] = '그룹 대기'
        return result
    display_cycle = resolve_display_cycle(result)
    display_step = resolve_display_step(result)
    result['display_cycle'] = display_cycle
    result['display_step'] = display_step
    if display_cycle > 0:
        result['current_cycle'] = display_cycle
        result['group_cycle_number'] = display_cycle
    return result
