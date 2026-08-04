"""User-facing diagnostics for a Motor Manager restart failure."""

from typing import Any, Dict, List


def diagnose_motor_restart_failure(
    operation: Dict[str, Any],
    motion_state: Dict[str, Any],
    runtime_status: Dict[str, Any],
) -> Dict[str, Any]:
    """Describe why configured axes did not become ready after a restart."""
    details = operation.get('details')
    details = dict(details) if isinstance(details, dict) else {}
    expected_axes = details.get('expected_axes')
    if not isinstance(expected_axes, list):
        expected_axes = []
    try:
        expected = sorted(set(int(axis) for axis in expected_axes))
    except (TypeError, ValueError):
        expected = []

    motors = motion_state.get('motors')
    motors = motors if isinstance(motors, list) else []
    by_axis: Dict[int, Dict[str, Any]] = {}
    for motor in motors:
        if not isinstance(motor, dict):
            continue
        try:
            by_axis[int(motor.get('controller_index'))] = motor
        except (TypeError, ValueError):
            continue

    pending: List[Dict[str, Any]] = []
    for axis in expected:
        motor = by_axis.get(axis)
        if motor is not None and _motor_is_ready(motor):
            continue
        pending.append(_pending_axis(axis, motor))

    runtime_phase = str(runtime_status.get('phase') or '')
    runtime_message = str(runtime_status.get('message') or '').strip()
    if runtime_phase in {
        'motor_manager_start_blocked',
        'motor_manager_disabled',
        'runtime_config_mismatch',
    }:
        message = runtime_message or 'Motor Manager 실행 상태를 확인하지 못했습니다.'
        return {
            'failure_code': runtime_phase,
            'message': message,
            'pending_axes': [item['controller_index'] for item in pending],
            'pending_connections': pending,
        }

    ethercat_pending = [
        item for item in pending
        if item['transport'] == 'ethercat' or item['state'] == 'bus_down'
    ]
    serial_pending = [item for item in pending if item['transport'] == 'serial']
    summary = _pending_summary(pending)
    if ethercat_pending:
        message = 'AC Servo EtherCAT 연결을 확인하지 못했습니다.'
        if summary:
            message += f' 미연결: {summary}.'
        message += ' 서보 전원, EtherCAT 케이블 및 Master 상태를 확인하세요.'
        failure_code = 'ethercat_not_ready'
    elif serial_pending:
        message = 'Dynamixel 통신 연결을 확인하지 못했습니다.'
        if summary:
            message += f' 미연결: {summary}.'
        message += ' 모터 전원, USB/직렬 케이블 및 포트 상태를 확인하세요.'
        failure_code = 'serial_not_ready'
    elif pending:
        message = f'설정된 모터 연결을 확인하지 못했습니다. 미연결: {summary}.'
        failure_code = 'motor_not_ready'
    else:
        message = (
            runtime_message
            or 'Motor Manager 재시작 후 새로운 모터 상태를 확인하지 못했습니다.'
        )
        failure_code = 'motor_feedback_unavailable'

    return {
        'failure_code': failure_code,
        'message': message,
        'pending_axes': [item['controller_index'] for item in pending],
        'pending_connections': pending,
    }


def motor_restart_service_failure(error: Any) -> str:
    """Add concise recovery guidance without claiming a hardware diagnosis."""
    detail = str(error or '').strip().rstrip(' .')
    prefix = 'Motor Manager 서비스를 재시작하지 못했습니다.'
    if detail:
        prefix += f' 원인: {detail}.'
    return (
        f'{prefix} 서비스 상태를 확인하고, AC Servo 사용 시 '
        '서보 전원·EtherCAT 케이블·Master 상태를 확인하세요.'
    )


def _motor_is_ready(motor: Dict[str, Any]) -> bool:
    return bool(
        motor.get('connection_connected') is True
        and str(motor.get('connection_state') or '') == 'online'
        and motor.get('fault') is not True
    )


def _pending_axis(axis: int, motor: Any) -> Dict[str, Any]:
    if not isinstance(motor, dict):
        return {
            'controller_index': axis,
            'label': f'축 {axis}',
            'transport': '',
            'state': 'missing',
            'reason': '상태 미수신',
        }
    transport = str(motor.get('transport') or '').lower()
    state = str(motor.get('connection_state') or 'unknown').lower()
    label = str(motor.get('display_name') or '').strip() or f'축 {axis}'
    if motor.get('fault') is True:
        reason = '모터 오류'
    elif state == 'bus_down':
        reason = 'EtherCAT 버스 미연결'
    elif state == 'stale':
        reason = '상태 수신 중단'
    elif transport == 'ethercat':
        reason = 'EtherCAT 응답 없음'
    elif transport == 'serial':
        reason = '직렬 통신 응답 없음'
    else:
        reason = str(motor.get('connection_message') or '').strip() or '연결되지 않음'
    return {
        'controller_index': axis,
        'label': label,
        'transport': transport,
        'state': state,
        'reason': reason,
    }


def _pending_summary(pending: List[Dict[str, Any]]) -> str:
    visible = pending[:6]
    text = ', '.join(f"{item['label']} ({item['reason']})" for item in visible)
    remaining = len(pending) - len(visible)
    return f'{text}, 외 {remaining}축' if remaining > 0 else text
