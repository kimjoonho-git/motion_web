"""모션 값 변환과 범위 검사 · 순수 함수.

`midi_control_node`에서 떼어냈다 · §5 분해 목표안의 `MotionValueMapper` · §6-38

페이더 원시값 ↔ 모션 값 ↔ 모터 목표각을 오가는 변환과, 링크된 Motion ID들의
범위가 어긋났는지 보는 검사만 모았다. **상태도 노드 참조도 없다.**

노드는 이 이름들을 다시 내보낸다 · 기존 호출자와 시험이 `midi_control_node`
경유로 쓰고 있어 그 통로를 유지한다.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List

from midi_control.bank_manager import (
    FILTER_LEVEL_MAX,
    MIDI_VALUE_MAX,
)
from motion_common import values

FILTER_ORDER = 2
FILTER_MAX_TIME_CONSTANT_SEC = 0.5
FILTER_MAX_STEP_SEC = 0.05
SELECT_RANGE_TOLERANCE_PERCENT = 0.25
LINKED_RANGE_TOLERANCE_DEG = 1e-6
LINKED_MOTION_VALUE_TOLERANCE_DEG = 1e-6


class LinkedMotionRangeMismatch(ValueError):
    """Raised when one MIDI fader links Motion IDs with different ranges."""

def motion_value_display(
    motion_ids: List[str],
    source_values: Dict[str, float],
    *,
    control_enabled: bool = False,
    estimated_value: float | None = None,
) -> tuple[float | None, str, str]:
    """Prefer confirmed source values, then an explicitly marked SELECT preview."""
    if not motion_ids:
        return None, 'NO DATA', 'missing'
    values = []
    for motion_id in motion_ids:
        value = _finite_float(source_values.get(str(motion_id)))
        if value is None:
            values = []
            break
        values.append(value)
    if values:
        if max(values) - min(values) > LINKED_MOTION_VALUE_TOLERANCE_DEG:
            return None, 'DIFF', 'different'
        value = sum(values) / len(values)
        return value, _motion_lcd_number(value), 'confirmed'
    preview = _finite_float(estimated_value)
    if control_enabled and preview is not None:
        return preview, _motion_lcd_number(preview, prefix='~'), 'estimated'
    return None, 'NO DATA', 'missing'

def _motion_lcd_number(value: float, prefix: str = '') -> str:
    for decimals in (3, 2, 1, 0):
        text = f'{value:.{decimals}f}'
        if len(prefix) + len(text) <= 7:
            return prefix + text
    return (prefix + f'{value:.1e}')[:7]

def second_order_low_pass(
    input_value: float,
    filter_level: float,
    dt_sec: float,
    stage1_previous: float,
    stage2_previous: float,
) -> tuple[float, float, float]:
    """Apply two cascaded first-order sections as a stable second-order LPF."""
    level = max(0, min(FILTER_LEVEL_MAX, int(filter_level)))
    if level <= 0:
        value = float(input_value)
        return value, value, value
    normalized_level = level / FILTER_LEVEL_MAX
    tau_sec = normalized_level * FILTER_MAX_TIME_CONSTANT_SEC
    dt_sec = max(1e-6, min(float(dt_sec), FILTER_MAX_STEP_SEC))
    alpha = 1.0 - math.exp(-dt_sec / tau_sec)
    stage1 = stage1_previous + alpha * (input_value - stage1_previous)
    stage2 = stage2_previous + alpha * (stage1 - stage2_previous)
    return stage2, stage1, stage2

def _finite_float(value: Any) -> float | None:
    return values.finite_float(value)

def motion_value_from_output(
    output_14bit: float,
    row: Dict[str, Any],
    motion_range: tuple[float, float] | None = None,
) -> float:
    """Convert the final 14-bit MIDI output into motion-space degrees."""
    lower = _finite_float(row.get('motion_lower_deg'))
    upper = _finite_float(row.get('motion_upper_deg'))
    if motion_range is not None:
        lower, upper = motion_range
    if lower is None or upper is None or upper <= lower:
        raise ValueError('motion-axis Min/Max angle is invalid')
    normalized = max(0.0, min(1.0, float(output_14bit) / MIDI_VALUE_MAX))
    return lower + ((upper - lower) * normalized)

def motor_target_from_motion(motion_value: float, row: Dict[str, Any]) -> float:
    """Use the same mapping equation as motion_runtime."""
    sign = -1.0 if bool(row.get('invert')) else 1.0
    reference = _finite_float(row.get('reference_position_deg')) or 0.0
    if row.get('reference_enabled') is False:
        reference = 0.0
    offset = _finite_float(row.get('offset_deg')) or 0.0
    scale = _finite_float(row.get('scale')) or 1.0
    gear_ratio = _finite_float(row.get('gear_ratio')) or 1.0
    return reference + ((float(motion_value) + offset) * scale * sign * gear_ratio)

def motion_value_from_motor(motor_position: float, row: Dict[str, Any]) -> float:
    """Invert actual motor feedback into the configured logical motion value."""
    sign = -1.0 if bool(row.get('invert')) else 1.0
    reference = _finite_float(row.get('reference_position_deg')) or 0.0
    if row.get('reference_enabled') is False:
        reference = 0.0
    offset = _finite_float(row.get('offset_deg')) or 0.0
    scale = _finite_float(row.get('scale')) or 1.0
    gear_ratio = _finite_float(row.get('gear_ratio')) or 1.0
    factor = scale * sign * gear_ratio
    if math.isclose(factor, 0.0, abs_tol=1e-12):
        raise ValueError('motion-axis scale/gear ratio is zero')
    return ((float(motor_position) - reference) / factor) - offset

def raw_fader_for_motion(
    motion_value: float,
    row: Dict[str, Any],
    bank_mapping: Dict[str, Any],
    motion_range: tuple[float, float] | None = None,
) -> int:
    """Invert motion range and bank Min/Max/reverse into a physical fader value."""
    lower = _finite_float(row.get('motion_lower_deg'))
    upper = _finite_float(row.get('motion_upper_deg'))
    if motion_range is not None:
        lower, upper = motion_range
    if lower is None or upper is None or upper <= lower:
        raise ValueError('motion-axis Min/Max angle is invalid')
    output_percent = 100.0 * ((motion_value - lower) / (upper - lower))
    minimum = float(bank_mapping['min_percent'])
    maximum = float(bank_mapping['max_percent'])
    span = maximum - minimum
    if span <= 0.0:
        raise ValueError('MIDI Min/Max percent is invalid')
    representable_min = max(0.0, min(100.0, minimum))
    representable_max = max(0.0, min(100.0, maximum))
    if (
        output_percent < representable_min - SELECT_RANGE_TOLERANCE_PERCENT
        or output_percent > representable_max + SELECT_RANGE_TOLERANCE_PERCENT
    ):
        allowed_lower = lower + ((upper - lower) * representable_min / 100.0)
        allowed_upper = lower + ((upper - lower) * representable_max / 100.0)
        raise ValueError(
            f'활성화 불가: 현재 위치 {motion_value:.2f}°가 '
            f'이 라인의 제어 범위 {allowed_lower:.2f}°~{allowed_upper:.2f}° 밖입니다'
        )
    output_percent = max(representable_min, min(representable_max, output_percent))
    normalized = max(0.0, min(1.0, (output_percent - minimum) / span))
    if bank_mapping['reversed']:
        normalized = 1.0 - normalized
    return int(round(MIDI_VALUE_MAX * normalized))

def require_same_motion_ranges(rows: List[Dict[str, Any]]) -> tuple[float, float]:
    """Return the shared range, rejecting linked axes with different ranges."""
    if not rows:
        raise ValueError('모션축 설정을 확인할 수 없습니다')
    ranges = []
    for row in rows:
        lower = _finite_float(row.get('motion_lower_deg'))
        upper = _finite_float(row.get('motion_upper_deg'))
        if lower is None or upper is None or upper <= lower:
            raise ValueError('모션축 Min/Max 각도를 확인하세요')
        ranges.append((lower, upper))
    first_lower, first_upper = ranges[0]
    if any(
        abs(lower - first_lower) > LINKED_RANGE_TOLERANCE_DEG
        or abs(upper - first_upper) > LINKED_RANGE_TOLERANCE_DEG
        for lower, upper in ranges[1:]
    ):
        raise LinkedMotionRangeMismatch(
            '연동 Motion ID의 모션 범위가 서로 다릅니다'
        )
    return first_lower, first_upper

def safe_motion_range_for_motor(
    row: Dict[str, Any],
    motor: Dict[str, Any],
) -> tuple[float, float]:
    """Return the configured motion range intersected with motor limits."""
    motion_lower = _finite_float(row.get('motion_lower_deg'))
    motion_upper = _finite_float(row.get('motion_upper_deg'))
    if (
        motion_lower is None
        or motion_upper is None
        or motion_upper <= motion_lower
    ):
        raise ValueError('모션축 Min/Max 각도를 확인하세요')

    motor_lower = _finite_float(motor.get('lower'))
    motor_upper = _finite_float(motor.get('upper'))
    if motor_lower is None or motor_upper is None:
        raise ValueError('모터축 Lower/Upper 제한을 확인할 수 없습니다')
    if motor_upper < motor_lower:
        raise ValueError('모터축 Lower/Upper 설정을 확인하세요')

    safe_from_motor = sorted((
        motion_value_from_motor(motor_lower, row),
        motion_value_from_motor(motor_upper, row),
    ))
    safe_lower = max(motion_lower, safe_from_motor[0])
    safe_upper = min(motion_upper, safe_from_motor[1])
    if safe_upper < safe_lower:
        raise ValueError(
            f'모션범위 {motion_lower:.3f}°~{motion_upper:.3f}°와 '
            f'모터범위 {motor_lower:.3f}°~{motor_upper:.3f}°가 겹치지 않습니다'
        )
    return safe_lower, safe_upper

def require_motion_value_within_limits(
    motion_id: Any,
    motion_value: float,
    row: Dict[str, Any],
    motor: Dict[str, Any],
) -> float:
    """Return a motor target only when one motion command passes both limits."""
    motion_lower = _finite_float(row.get('motion_lower_deg'))
    motion_upper = _finite_float(row.get('motion_upper_deg'))
    if motion_lower is None or motion_upper is None:
        raise ValueError('모션축 Min/Max 각도를 확인하세요')
    tolerance = 1e-6
    value = float(motion_value)
    if value < motion_lower - tolerance or value > motion_upper + tolerance:
        raise ValueError(
            f'{motion_id}: 모션 명령 {value:.3f}°가 모션범위 '
            f'{motion_lower:.3f}°~{motion_upper:.3f}°를 벗어납니다'
        )
    target = motor_target_from_motion(value, row)
    motor_lower = _finite_float(motor.get('lower'))
    motor_upper = _finite_float(motor.get('upper'))
    if motor_lower is not None and target < motor_lower - tolerance:
        raise ValueError(
            f'{motion_id}: 모터 목표 {target:.3f}°가 Lower '
            f'{motor_lower:.3f}°보다 작습니다'
        )
    if motor_upper is not None and target > motor_upper + tolerance:
        axis = motor.get('controller_index')
        raise ValueError(
            f'{motion_id}: 모터축 {axis} 목표 {target:.3f}°가 Upper '
            f'{motor_upper:.3f}°보다 큽니다'
        )
    return target

def safe_motion_range_for_group(
    group: List[Dict[str, Any]],
) -> tuple[float, float]:
    """Return one shared MIDI range safe for every linked motor axis."""
    safe_ranges = []
    for item in group:
        row = item['row']
        motor = item.get('motor')
        if isinstance(motor, dict):
            safe_ranges.append(safe_motion_range_for_motor(row, motor))
        else:
            lower = _finite_float(row.get('motion_lower_deg'))
            upper = _finite_float(row.get('motion_upper_deg'))
            if lower is None or upper is None:
                raise ValueError('모션축 Min/Max 각도를 확인하세요')
            safe_ranges.append((lower, upper))
    if not safe_ranges:
        raise ValueError('안전범위를 계산할 모션축이 없습니다')
    safe_lower = max(item[0] for item in safe_ranges)
    safe_upper = min(item[1] for item in safe_ranges)
    if safe_upper <= safe_lower:
        raise ValueError('연동 축이 함께 사용할 수 있는 안전 모션범위가 없습니다')
    return safe_lower, safe_upper
