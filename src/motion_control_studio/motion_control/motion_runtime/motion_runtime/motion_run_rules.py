"""모션 실행 판정 규칙 · 상태 비의존.

`MotionRunManager`에서 떼어낸 순수 함수 모음이다 · §6-25
노드의 상태도 락도 건드리지 않고 인자로 받은 값만 보고 판단하므로, 노드를 띄우지
않고 단위 테스트할 수 있다.

여기 있는 것 · 실행 상태 초안 · 모션 파일 해석 · 모터 참조 해석 · 목표값 판정 ·
보간과 클램프 · 재생 주기 계산.
"""

from __future__ import annotations

import math
import time
from bisect import bisect_left
from typing import Any, Dict, List, Mapping, Optional
from urllib.parse import quote

from motion_control_msgs.msg import MotorStatus
from std_msgs.msg import Int8MultiArray

from motion_common import motion_table
from motion_common.values import finite_float, optional_int

from .motion_run_constants import INITIAL_MOVE_TIME_OPTIONS_SEC


def _status_from_plan(state: str, message: str, plan: Dict[str, Any]) -> Dict[str, Any]:
    return {
        **_empty_status(),
        'state': state,
        'message': message,
        'project_id': plan.get('project_id', ''),
        'motion_file_id': plan.get('motion_file_id', ''),
        'mapping_file_id': plan.get('mapping_file_id', ''),
        'run_mode': plan.get('run_mode', 'once'),
        'automation_run': bool(plan.get('automation_run')),
        'repeat_mode': plan.get('repeat_mode', 'direct'),
        'dwell_sec': float(plan.get('dwell_sec') or 0.0),
        'countdown_sec': float(plan.get('countdown_sec') or 0.0),
        'scheduled_start_at': float(plan.get('scheduled_start_at') or 0.0),
        'synchronized_cycle_sec': float(plan.get('synchronized_cycle_sec') or 0.0),
        'synchronized_repeat_count': int(plan.get('synchronized_repeat_count') or 0),
        'network_operation_id': plan.get('network_operation_id', ''),
        'operation_generation': int(plan.get('operation_generation') or 0),
        'request_source': plan.get('request_source', 'motion_run'),
        'group_execution': bool(plan.get('group_execution')),
        'execution_id': str(plan.get('execution_id') or ''),
        'group_cycle_number': int(plan.get('group_cycle_number') or 0),
        'cycle_count': 0,
        'current_cycle': 0,
        'summary': plan.get('summary', {}),
        'warnings': plan.get('warnings', []),
        'capabilities': plan.get('capabilities', {}),
        'axes': [
            {
                'motion_id': axis['motion_id'],
                'motor_axis': axis['motor_axis'],
                'motor_type': axis['motor_type'],
                'initial_motion_source_position_deg': axis['initial_motion_source_position_deg'],
                'initial_motion_position_deg': axis['initial_motion_position_deg'],
                'initial_motor_target_deg': axis['initial_motor_target_deg'],
                'motion_limit_lower_deg': axis['motion_limit_lower_deg'],
                'motion_limit_upper_deg': axis['motion_limit_upper_deg'],
                'source_motion_min_deg': axis['source_motion_min_deg'],
                'source_motion_max_deg': axis['source_motion_max_deg'],
                'command_motion_min_deg': axis['command_motion_min_deg'],
                'command_motion_max_deg': axis['command_motion_max_deg'],
                'motion_clamped': axis['motion_clamped'],
                'target_min_deg': axis['target_min_deg'],
                'target_max_deg': axis['target_max_deg'],
                'loop_start_motion_deg': axis['loop_start_motion_deg'],
                'loop_end_motion_deg': axis['loop_end_motion_deg'],
                'loop_start_target_deg': axis['loop_start_target_deg'],
                'loop_end_target_deg': axis['loop_end_target_deg'],
                'loop_delta_deg': axis['loop_delta_deg'],
                'loop_motor_delta_deg': axis['loop_motor_delta_deg'],
                'loop_tolerance_deg': axis['loop_tolerance_deg'],
            }
            for axis in plan.get('axes', [])
        ],
        'updated_at': time.time(),
    }

def _empty_status() -> Dict[str, Any]:
    return {
        'state': 'idle',
        'message': 'motion run idle',
        'project_id': '',
        'motion_file_id': '',
        'mapping_file_id': '',
        'run_mode': 'once',
        'automation_run': False,
        'repeat_mode': 'direct',
        'dwell_sec': 0.0,
        'countdown_sec': 0.0,
        'operation_generation': 0,
        'request_source': 'motion_run',
        'group_execution': False,
        'execution_id': '',
        'group_cycle_number': 0,
        'cycle_count': 0,
        'current_cycle': 0,
        'summary': {},
        'warnings': [],
        'capabilities': {},
        'axes': [],
        'phase': 'idle',
        'phase_started_at': None,
        'phase_finished_at': None,
        'lifecycle': {
            'checked_at': None,
            'initial_started_at': None,
            'initial_finished_at': None,
            'motion_started_at': None,
            'motion_finished_at': None,
        },
        'progress': {
            'elapsed_sec': 0.0,
            'duration_sec': 0.0,
            'ratio': 0.0,
            'sample_index': 0,
            'active_axis_count': 0,
        },
        'updated_at': time.time(),
    }

def _motor_ref_for_motor(motor: Dict[str, Any]) -> str:
    motor_type = _motor_type(motor)
    if motor_type == 'ac_servo':
        alias = optional_int(
            motor.get('alias', motor.get('ethercat_alias'))
        )
        master_index = optional_int(
            motor.get('ethercat_master_index')
        )
        if master_index is None:
            master_index = 0
        if alias is not None and alias > 0 and master_index >= 0:
            return f'ac_servo:master:{master_index}:alias:{alias}'
        slave_position = optional_int(motor.get('slave_position'))
        return (
            f'ac_servo:master:{master_index}:slave:{slave_position}'
            if slave_position is not None
            and slave_position >= 0
            and master_index >= 0
            else ''
        )
    if motor_type == 'dynamixel':
        bus_id = optional_int(
            motor.get('bus_id', motor.get('node_id'))
        )
        serial_port = str(motor.get('serial_port') or '').strip()
        return (
            f'dynamixel:port:{quote(serial_port, safe="")}:id:{bus_id}'
            if bus_id is not None and bus_id >= 0 and serial_port else ''
        )
    return ''

def _interpolated_value(
    records: List[Dict[str, Any]],
    record_times: List[float],
    time_sec: float,
) -> float:
    if not records:
        return 0.0
    if time_sec <= records[0]['time_sec']:
        return float(records[0]['value'])
    if time_sec >= records[-1]['time_sec']:
        return float(records[-1]['value'])
    after_index = bisect_left(record_times, time_sec)
    before = records[after_index - 1]
    after = records[after_index]
    span = max(float(after['time_sec'] - before['time_sec']), 1e-9)
    ratio = (time_sec - before['time_sec']) / span
    return float(before['value']) + (
        (float(after['value']) - float(before['value'])) * ratio
    )

def _continuous_capability(axes: List[Dict[str, Any]]) -> Dict[str, Any]:
    mismatched = [
        axis for axis in axes
        if float(axis['loop_delta_deg']) > float(axis['loop_tolerance_deg'])
    ]
    if not mismatched:
        return {
            'available': True,
            'reason': '모든 축의 모션 시작·종료값이 5° 이내입니다',
        }
    details = ', '.join(
        f"Axis {axis['motor_axis']} 모션값 차이 {axis['loop_delta_deg']:.3f}° "
        f"(허용 {axis['loop_tolerance_deg']:.3f}°)"
        for axis in mismatched[:4]
    )
    return {
        'available': False,
        'reason': f'모션 시작·종료값 차이가 5°를 초과합니다: {details}',
    }

def _empty_motor_command(
    controller_axes: List[int],
) -> MotorStatus:
    indexes = _sorted_controller_axes(controller_axes)
    size = len(indexes)
    command = MotorStatus()
    command.number_of_target_interfaces = [0] * size
    command.target_interface_id = [Int8MultiArray(data=[]) for _ in range(size)]
    command.controller_index = indexes
    command.controlword = [0] * size
    command.statusword = [0] * size
    command.errorcode = [0] * size
    command.position = [0.0] * size
    command.velocity = [0.0] * size
    command.effort = [0.0] * size
    return command

def _motor_ready_error(motor: Dict[str, Any]) -> str:
    axis = optional_int(motor.get('controller_index'))
    if str(motor.get('state') or '') != 'detected':
        return f'Axis {axis} is not detected'
    errorcode = optional_int(motor.get('errorcode')) or 0
    if errorcode:
        error_hex = str(motor.get('errorcode_hex') or f'0x{errorcode & 0xFFFF:04X}')
        error_text = str(motor.get('error_text') or '').strip()
        detail = f' ({error_text})' if error_text else ''
        return f'Axis {axis} motor alarm {error_hex}{detail}'
    if bool(motor.get('fault', False)):
        return f'Axis {axis} has error'
    if _motor_type(motor) == 'ac_servo' and motor.get('servo_on') is not True:
        return f'Axis {axis} servo is OFF'
    return ''

def _motor_position_deg(motor: Optional[Dict[str, Any]]) -> Optional[float]:
    if motor is None:
        return None
    for key in (
        'position_deg',
        'position_actual_deg',
        'output_position_deg',
        'present_position_deg',
        'position_actual',
        'position',
    ):
        number = finite_float(motor.get(key))
        if number is not None:
            return number
    return None

def _target_range_limit_error(
    motor: Dict[str, Any],
    target_min: float,
    target_max: float,
) -> str:
    lower = finite_float(motor.get('lower'))
    upper = finite_float(motor.get('upper'))
    axis = optional_int(motor.get('controller_index'))
    if lower is not None and target_min < lower:
        return f'Axis {axis} target min {target_min:.3f} < lower {lower:.3f}'
    if upper is not None and target_max > upper:
        return f'Axis {axis} target max {target_max:.3f} > upper {upper:.3f}'
    return ''

def _motors_for_ref(
    motor_ref: Any,
    motors: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    target = str(motor_ref or '').strip().lower()
    if not target:
        return []
    return [
        motor for motor in motors
        if target in {
            ref.lower() for ref in _motor_refs_for_motor(motor)
        }
    ]

def _motor_type(motor: Dict[str, Any]) -> str:
    values = [
        motor.get('motor_type'),
        motor.get('motor_type_label'),
        motor.get('driver_model'),
        motor.get('driver_name'),
        motor.get('transport'),
    ]
    text = ' '.join(str(value or '').lower() for value in values)
    if 'dynamixel' in text:
        return 'dynamixel'
    if 'minas' in text or 'ac servo' in text or 'ac_servo' in text:
        return 'ac_servo'
    return 'unknown'

def _motor_refs_for_motor(motor: Dict[str, Any]) -> List[str]:
    canonical = _motor_ref_for_motor(motor)
    if _motor_type(motor) == 'ac_servo':
        alias = optional_int(
            motor.get('alias', motor.get('ethercat_alias'))
        )
        legacy = f'ac_servo:alias:{alias}' if alias is not None and alias > 0 else ''
    elif _motor_type(motor) == 'dynamixel':
        bus_id = optional_int(motor.get('bus_id', motor.get('node_id')))
        legacy = f'dynamixel:id:{bus_id}' if bus_id is not None and bus_id >= 0 else ''
    else:
        legacy = ''
    return [item for item in (canonical, legacy) if item]

def _clamp_motion_value(
    value: float,
    lower: Optional[float],
    upper: Optional[float],
) -> float:
    result = float(value)
    if lower is not None:
        result = max(result, float(lower))
    if upper is not None:
        result = min(result, float(upper))
    return result

def _sorted_controller_axes(values: Any) -> List[int]:
    axes = []
    for value in values:
        try:
            axis = int(value)
        except (TypeError, ValueError):
            continue
        if axis >= 0 and axis not in axes:
            axes.append(axis)
    return sorted(axes)

def _motor_target(row: Dict[str, Any], motion_value: float) -> float:
    sign = -1.0 if bool(row.get('invert')) else 1.0
    reference = finite_float(row.get('reference_position_deg')) or 0.0
    if row.get('reference_enabled') is False:
        reference = 0.0
    offset = finite_float(row.get('offset_deg')) or 0.0
    scale = finite_float(row.get('scale')) or 1.0
    gear_ratio = finite_float(row.get('gear_ratio')) or 1.0
    output_axis_value = (float(motion_value) + offset) * scale * sign
    return reference + (output_axis_value * gear_ratio)

def _motion_groups(
    records: List[Dict[str, Any]],
) -> Dict[str, List[Dict[str, Any]]]:
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for record in records:
        groups.setdefault(str(record['motion_id']), []).append(record)
    for key in list(groups):
        groups[key] = sorted(groups[key], key=lambda item: item['time_sec'])
    return groups

def _motion_auto_start_guard_error(plan: Dict[str, Any]) -> str:
    if (
        plan.get('run_mode') == 'continuous'
        and plan.get('repeat_mode') not in {'reinitialize', 'dwell_reinitialize'}
    ):
        capability = plan.get('capabilities', {}).get('continuous_run', {})
        if not capability.get('available'):
            return str(capability.get('reason') or '모션 시작값과 끝값이 달라 연속 동작할 수 없습니다')
    return ''

def _initial_move_time_override_sec(payload: Dict[str, Any]) -> Optional[float]:
    value = finite_float(payload.get('initial_move_time_sec'))
    if value is None:
        return None
    for option in INITIAL_MOVE_TIME_OPTIONS_SEC:
        if math.isclose(value, option, rel_tol=0.0, abs_tol=1e-6):
            return option
    allowed = ', '.join(f'{option:g}' for option in INITIAL_MOVE_TIME_OPTIONS_SEC)
    raise ValueError(f'initial_move_time_sec must be one of: {allowed}')

def _initial_motion_value(
    row: Dict[str, Any],
    records: List[Dict[str, Any]],
) -> float:
    if str(row.get('initial_mode') or 'first_frame') == 'manual':
        return finite_float(row.get('initial_motion_position_deg')) or 0.0
    return float(records[0]['value'])

def _unavailable_capabilities(reason: str) -> Dict[str, Dict[str, Any]]:
    message = str(reason or '실행 준비 검사 실패')
    return {
        'initial_position': {'available': False, 'reason': message},
        'single_run': {'available': False, 'reason': message},
        'continuous_run': {'available': False, 'reason': message},
    }

def _playback_cycle_number(plan: Mapping[str, Any], cycle_count: int) -> int:
    """Return the user-visible motion cycle for playback progress."""
    if bool(plan.get('group_execution')):
        group_cycle = int(plan.get('group_cycle_number') or 0)
        if group_cycle > 0:
            return group_cycle
    return int(cycle_count) + 1

def _duplicate_axis_text(axes: List[Dict[str, Any]]) -> str:
    counts: Dict[int, int] = {}
    for axis in axes:
        key = int(axis['motor_axis'])
        counts[key] = counts.get(key, 0) + 1
    duplicates = [str(axis) for axis, count in counts.items() if count > 1]
    return ', '.join(duplicates)

def _is_terminal_action_result(payload: Dict[str, Any]) -> bool:
    if not bool(payload.get('success')):
        return True
    message = str(payload.get('message') or '').lower()
    return 'completed' in message or 'did not reach target' in message

def _sleep_until(deadline: float) -> None:
    delay = deadline - time.monotonic()
    if delay > 0.0:
        time.sleep(delay)

def _smoothstep(value: float) -> float:
    clamped = min(max(float(value), 0.0), 1.0)
    return (clamped * clamped) * (3.0 - (2.0 * clamped))

def _extract_motion_rows(content: str) -> tuple[List[Any], List[str]]:
    rows, headers, _source, _warning = motion_table.extract_rows_from_content(content)
    return rows, headers

def _has_ac_axes(axes: List[Dict[str, Any]]) -> bool:
    return any(axis.get('motor_type') == 'ac_servo' for axis in axes)
