"""Convert local application state into a project-neutral status payload."""

import copy
import re
from typing import Any, Dict, Mapping


STATUS_PAYLOAD_VERSION = 1
COORDINATION_MODES = {'off', 'status', 'participant'}
COORDINATION_ROLES = {'peer', 'coordinator'}
READINESS_STATES = {'ready', 'rejected', 'unavailable'}
_MACHINE_ID = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$')

_MOTION_STATE_MAP = {
    'idle': 'ready',
    'ready': 'ready',
    'initialized': 'ready',
    'configuration_required': 'configuration_required',
    'preparing': 'initializing',
    'initializing': 'initializing',
    'countdown': 'waiting_start',
    'running': 'running',
    'verifying': 'running',
    'waiting': 'waiting',
    'stopping': 'stopping',
    'stopped': 'completed',
    'completed': 'completed',
    'error': 'error',
    'failed': 'error',
}
_RUNTIME_ERROR_PHASES = {
    'motor_manager_start_blocked',
    'motor_manager_disabled',
    'runtime_config_mismatch',
    'error',
    'failed',
}


def adapt_status(
    local_status: Mapping[str, Any],
    *,
    display_name: str = '',
    coordination_mode: str = 'off',
    coordination_role: str = 'peer',
    coordinator_machine_id: str = '',
    program_session_id: str = '',
    readiness_session_id: str = '',
) -> Dict[str, Any]:
    """Return only the fields approved for the version-1 status payload."""
    if not isinstance(local_status, Mapping):
        local_status = {}
    payload: Dict[str, Any] = {
        'status_payload_version': STATUS_PAYLOAD_VERSION,
        'program': _program_status(local_status),
        'configuration': _configuration_status(local_status),
        'motors': _motor_status(local_status),
        'safety': _safety_status(local_status),
        'motion': _motion_status(local_status),
        'coordination': _coordination_status(
            coordination_mode,
            coordination_role,
            coordinator_machine_id,
        ),
    }
    clean_name = str(display_name or '').strip()
    if clean_name:
        payload['display_name'] = clean_name[:128]
    if program_session_id and readiness_session_id:
        payload['session'] = {
            'program_session_id': str(program_session_id),
            'readiness_session_id': str(readiness_session_id),
        }
    return payload


def validate_status_payload(value: Mapping[str, Any]) -> Dict[str, Any]:
    """Validate an untrusted version-1 status payload."""
    if not isinstance(value, Mapping) or value.get('status_payload_version') != 1:
        raise ValueError('status payload version이 올바르지 않습니다')
    _only_keys(value, {
        'status_payload_version', 'display_name', 'program', 'configuration',
        'motors', 'safety', 'motion', 'coordination',
        'session',
    }, 'status')
    _state(value, 'program', {'ready', 'error', 'unknown'})
    configuration = _mapping(value.get('configuration'))
    _only_keys(configuration, {'motor', 'motion'}, 'configuration')
    for key in ('motor', 'motion'):
        if configuration.get(key) not in {
            'ready', 'configuration_required', 'error', 'unknown'
        }:
            raise ValueError(f'configuration.{key} 상태가 올바르지 않습니다')
    motors = _mapping(value.get('motors'))
    _only_keys(
        motors,
        {'state', 'total_count', 'online_count', 'fault_count'},
        'motors',
    )
    if motors.get('state') not in {'online', 'offline', 'error', 'unknown'}:
        raise ValueError('motors.state가 올바르지 않습니다')
    counts = []
    for key in ('total_count', 'online_count', 'fault_count'):
        count = motors.get(key)
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise ValueError(f'motors.{key}는 0 이상의 정수여야 합니다')
        counts.append(count)
    if counts[1] > counts[0] or counts[2] > counts[0]:
        raise ValueError('모터 상태 개수가 전체 축 수보다 많습니다')
    _state(value, 'safety', {'ready', 'blocked', 'unknown'})
    _state(value, 'motion', set(_MOTION_STATE_MAP.values()) | {'unknown'})
    coordination = _mapping(value.get('coordination'))
    _only_keys(
        coordination,
        {'mode', 'role', 'coordinator_machine_id'},
        'coordination',
    )
    if coordination.get('mode') not in COORDINATION_MODES:
        raise ValueError('coordination.mode가 올바르지 않습니다')
    if coordination.get('role') not in COORDINATION_ROLES:
        raise ValueError('coordination.role이 올바르지 않습니다')
    coordinator_id = coordination.get('coordinator_machine_id')
    if not isinstance(coordinator_id, str) or (
        coordinator_id and not _MACHINE_ID.fullmatch(coordinator_id)
    ):
        raise ValueError('coordinator_machine_id 형식이 올바르지 않습니다')
    display_name = value.get('display_name')
    if display_name is not None and (
        not isinstance(display_name, str) or not display_name or len(display_name) > 128
    ):
        raise ValueError('display_name 형식이 올바르지 않습니다')
    session = value.get('session')
    if session is not None:
        session = _mapping(session)
        _only_keys(
            session,
            {'program_session_id', 'readiness_session_id'},
            'session',
        )
        for field in ('program_session_id', 'readiness_session_id'):
            if not isinstance(session.get(field), str) or not _MACHINE_ID.fullmatch(
                session.get(field)
            ):
                raise ValueError(f'session.{field} 형식이 올바르지 않습니다')
    return copy.deepcopy(dict(value))


def adapt_readiness_result(value: Mapping[str, Any]) -> Dict[str, Any]:
    """Reduce one local motion-run check to a non-project network result."""
    result = value if isinstance(value, Mapping) else {}
    if result.get('success') is True:
        ready = {
            'readiness_version': 1,
            'state': 'ready',
            'reason_code': 'ready',
            'message': '실행 준비 완료',
        }
        summary = result.get('summary')
        if isinstance(summary, Mapping):
            try:
                duration = float(summary.get('duration_sec'))
            except (TypeError, ValueError):
                duration = 0.0
            if 0.0 < duration <= 86400.0:
                ready['motion_duration_sec'] = duration
            try:
                initialization = float(summary.get('initialization_duration_sec'))
            except (TypeError, ValueError):
                initialization = 0.0
            if 0.0 <= initialization <= 3600.0:
                ready['initialization_duration_sec'] = initialization
        return ready
    raw = str(result.get('message') or '').lower()
    categories = (
        (('project', '프로젝트'), 'project_required', '로컬 프로젝트 설정 필요'),
        (('context', '컨텍스트'), 'context_required', '로컬 실행 설정 적용 필요'),
        (('mapping', '매핑', '모션축'), 'mapping_required', '로컬 모션축 설정 확인 필요'),
        (('motion file', '모션 파일'), 'motion_required', '로컬 모션 파일 확인 필요'),
        (('motor alarm', 'fault', '오류 축'), 'motor_fault', '로컬 모터 오류 확인 필요'),
        (('offline', 'online', '연결'), 'motor_offline', '로컬 모터 연결 확인 필요'),
        (('safety', '안전', '차단'), 'safety_blocked', '로컬 안전 차단 확인 필요'),
        (('timeout', '응답 없음'), 'local_timeout', '로컬 실행 관리자 응답 없음'),
        (('running', '진행 중', 'busy'), 'busy', '로컬 모션 작업 진행 중'),
    )
    for keywords, code, message in categories:
        if any(keyword.lower() in raw for keyword in keywords):
            break
    else:
        code, message = 'local_check_failed', '로컬 실행 준비 확인 실패'
    return {
        'readiness_version': 1,
        'state': 'rejected',
        'reason_code': code,
        'message': message,
    }


def validate_readiness_payload(value: Mapping[str, Any]) -> Dict[str, Any]:
    """Validate one untrusted project-neutral readiness result."""
    if not isinstance(value, Mapping) or value.get('readiness_version') != 1:
        raise ValueError('readiness payload version이 올바르지 않습니다')
    _only_keys(
        value,
        {
            'readiness_version', 'state', 'reason_code', 'message',
            'readiness_session_id',
            'motion_duration_sec',
            'initialization_duration_sec',
            'clock_sync_state', 'clock_offset_ms', 'clock_source',
        },
        'readiness',
    )
    if value.get('state') not in READINESS_STATES:
        raise ValueError('readiness.state가 올바르지 않습니다')
    for field in ('reason_code', 'message'):
        text = value.get(field)
        if not isinstance(text, str) or not text or len(text) > 128:
            raise ValueError(f'readiness.{field} 형식이 올바르지 않습니다')
    session_id = value.get('readiness_session_id')
    if session_id is not None and (
        not isinstance(session_id, str) or not _MACHINE_ID.fullmatch(session_id)
    ):
        raise ValueError('readiness.readiness_session_id 형식이 올바르지 않습니다')
    duration = value.get('motion_duration_sec')
    if duration is not None and (
        isinstance(duration, bool) or not isinstance(duration, (int, float))
        or duration <= 0 or duration > 86400
    ):
        raise ValueError('readiness.motion_duration_sec 형식이 올바르지 않습니다')
    initialization = value.get('initialization_duration_sec')
    if initialization is not None and (
        isinstance(initialization, bool)
        or not isinstance(initialization, (int, float))
        or initialization < 0 or initialization > 3600
    ):
        raise ValueError('readiness.initialization_duration_sec 형식이 올바르지 않습니다')
    clock_state = value.get('clock_sync_state')
    if clock_state is not None and clock_state not in {
        'ready', 'out_of_tolerance', 'unavailable'
    }:
        raise ValueError('readiness.clock_sync_state 형식이 올바르지 않습니다')
    offset = value.get('clock_offset_ms')
    if offset is not None and (
        isinstance(offset, bool) or not isinstance(offset, (int, float))
        or offset < 0 or offset > 60000
    ):
        raise ValueError('readiness.clock_offset_ms 형식이 올바르지 않습니다')
    source = value.get('clock_source')
    if source is not None and source != 'chrony':
        raise ValueError('readiness.clock_source 형식이 올바르지 않습니다')
    return copy.deepcopy(dict(value))


def _program_status(local_status: Mapping[str, Any]) -> Dict[str, str]:
    bridge_state = str(local_status.get('bridge_state') or '').lower()
    services = _mapping(local_status.get('service_management'))
    runtime = _mapping(services.get('runtime'))
    runtime_phase = str(runtime.get('phase') or '').lower()
    if bridge_state and bridge_state != 'ok':
        state = 'error'
    elif runtime_phase in _RUNTIME_ERROR_PHASES:
        state = 'error'
    elif bridge_state == 'ok':
        state = 'ready'
    else:
        state = 'unknown'
    return {'state': state}


def _configuration_status(local_status: Mapping[str, Any]) -> Dict[str, str]:
    project_scope = _mapping(local_status.get('project_scope'))
    execution = _mapping(local_status.get('execution_context'))
    motor_applied = project_scope.get('motor_config_applied')
    if motor_applied is True:
        motor_state = 'ready'
    elif motor_applied is False:
        motor_state = 'configuration_required'
    else:
        motor_state = 'unknown'

    execution_state = str(execution.get('state') or '').lower()
    if execution.get('ready') is True:
        motion_state = 'ready'
    elif execution_state in {'error', 'failed', 'invalid'}:
        motion_state = 'error'
    elif execution:
        motion_state = 'configuration_required'
    else:
        motion_state = 'unknown'
    return {'motor': motor_state, 'motion': motion_state}


def _motor_status(local_status: Mapping[str, Any]) -> Dict[str, Any]:
    motion_state = _mapping(local_status.get('motion_state'))
    raw_motors = motion_state.get('motors')
    if not isinstance(raw_motors, list):
        return {
            'state': 'unknown',
            'total_count': 0,
            'online_count': 0,
            'fault_count': 0,
        }
    motors = [motor for motor in raw_motors if isinstance(motor, Mapping)]
    online = sum(
        1 for motor in motors
        if motor.get('connection_connected') is True
        and str(motor.get('connection_state') or '') == 'online'
    )
    faulted = sum(1 for motor in motors if motor.get('fault') is True)
    if faulted:
        state = 'error'
    elif motors and online == len(motors):
        state = 'online'
    elif motors:
        state = 'offline'
    else:
        state = 'unknown'
    return {
        'state': state,
        'total_count': len(motors),
        'online_count': online,
        'fault_count': faulted,
    }


def _safety_status(local_status: Mapping[str, Any]) -> Dict[str, str]:
    safety = _mapping(local_status.get('safety_status'))
    blocked = safety.get('commands_blocked')
    if blocked is True:
        state = 'blocked'
    elif blocked is False:
        state = 'ready'
    else:
        state = 'unknown'
    return {'state': state}


def _motion_status(local_status: Mapping[str, Any]) -> Dict[str, str]:
    run = _mapping(local_status.get('motion_run_status'))
    internal = str(run.get('state') or '').lower()
    return {'state': _MOTION_STATE_MAP.get(internal, 'unknown')}


def _coordination_status(mode: str, role: str, coordinator_id: str) -> Dict[str, str]:
    clean_mode = str(mode or '').strip().lower()
    clean_role = str(role or '').strip().lower()
    if clean_mode not in COORDINATION_MODES:
        clean_mode = 'off'
    if clean_role not in COORDINATION_ROLES:
        clean_role = 'peer'
    return {
        'mode': clean_mode,
        'role': clean_role,
        'coordinator_machine_id': str(coordinator_id or '').strip(),
    }


def _state(value: Mapping[str, Any], key: str, allowed: set[str]) -> None:
    section = _mapping(value.get(key))
    _only_keys(section, {'state'}, key)
    if section.get('state') not in allowed:
        raise ValueError(f'{key}.state가 올바르지 않습니다')


def _only_keys(value: Mapping[str, Any], allowed: set[str], field: str) -> None:
    unknown = sorted(set(value).difference(allowed))
    if unknown:
        raise ValueError(f'{field}에 허용되지 않은 필드가 있습니다: {unknown[0]}')


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}
