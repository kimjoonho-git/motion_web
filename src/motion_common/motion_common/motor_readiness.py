"""모터 준비 상태 검사 단일 구현.

같은 "이 축에 명령을 보내도 되는가" 판단이 열 곳에 복사돼 있었다. 복사본마다
검사 항목과 순서가 조금씩 달라서, 같은 고장에도 경로에 따라 다른 메시지가
나오거나 아예 통과했다.

이 모듈은 그 차이를 지우지 않는다. 차이가 **의도된 것**이기 때문이다.

    모션 실행 · 초기화  detected → 알람코드 → fault → servo_on
    MIDI               detected → fault → servo_on → 내부리밋
    수동 조그 · 절대이동 detected → servo_on → fault

세 목록의 차이는 전부 의도다 · 기준은 **감시자의 유무**다.

조그와 절대이동이 내부리밋을 검사하지 않는 것은, 리밋에 걸린 축을 빼내는 수단이
조그이기 때문이다. 여기서 막으면 복구 방법이 사라진다.

알람코드를 실행 경로에서만 보는 것도 같은 판단이다. 조그·MIDI는 사람이 화면을
보며 축 하나를 움직이는 중이라 알람이 그 자리에서 보이고, 알람 축을 빼내는 길도
열려 있어야 한다. 파일 재생은 사람이 자리를 뜬 동안에도 여러 축이 동시에 돌므로
여기서만 사전에 막는다.

모터 종류 판정과 내부리밋 판정은 호출부가 한다. 노드마다 판정 근거가 다르고
(`_is_ac_servo` · `motor_config_rules.is_ac_servo_motor` · 매핑의 `motor_type`),
그것까지 여기로 끌어오면 이 모듈이 모터 모델을 알아야 한다.

메시지 문구는 통합 전과 한 글자도 다르지 않다. 화면과 테스트가 문구를 본다.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Sequence, Tuple

from .values import optional_int

__all__ = [
    'MOTION_RUN_ORDER',
    'MIDI_ORDER',
    'MANUAL_ORDER',
    'readiness_error',
]

#: 모션 실행·초기화 · 알람코드까지 본다 · 가장 엄격하다
MOTION_RUN_ORDER: Tuple[str, ...] = ('detected', 'alarm', 'fault', 'servo_on')

#: MIDI 실시간 제어 · 내부리밋까지 본다
MIDI_ORDER: Tuple[str, ...] = ('detected', 'fault', 'servo_on', 'internal_limit')

#: 수동 조그·절대이동 · 내부리밋을 **일부러** 보지 않는다
MANUAL_ORDER: Tuple[str, ...] = ('detected', 'servo_on', 'fault')

INTERNAL_LIMIT_MESSAGE = (
    'internal limit is active; '
    'check POT/NOT, emergency stop, torque limit, and software limit'
)


def _alarm_error(motor: Dict[str, Any], axis: Optional[int]) -> str:
    errorcode = optional_int(motor.get('errorcode')) or 0
    if not errorcode:
        return ''
    error_hex = str(motor.get('errorcode_hex') or f'0x{errorcode & 0xFFFF:04X}')
    error_text = str(motor.get('error_text') or '').strip()
    detail = f' ({error_text})' if error_text else ''
    return f'Axis {axis} motor alarm {error_hex}{detail}'


def readiness_error(
    motor: Dict[str, Any],
    *,
    order: Sequence[str],
    axis: Optional[int] = None,
    is_ac_servo: bool = False,
    internal_limit_active: bool = False,
) -> str:
    """준비되지 않았으면 사유를, 준비됐으면 빈 문자열을 돌려준다.

    `order`가 검사 항목과 순서를 모두 정한다. 목록에 없는 항목은 검사하지
    않는다 — 조그 경로가 `internal_limit`을 빼는 방식이 이것이다.

    `servo_on`과 `internal_limit`은 AC 서보에만 해당한다. `is_ac_servo`가
    거짓이면 목록에 있어도 건너뛴다. Dynamixel 경로가 servo_on을 검사하지
    않는 것이 이 규칙으로 표현된다.

    호출부에서 축 번호를 주지 않으면 `controller_index`에서 읽는다.
    """
    if axis is None:
        axis = optional_int(motor.get('controller_index'))

    for check in order:
        if check == 'detected':
            if str(motor.get('state') or '') != 'detected':
                return f'Axis {axis} is not detected'
        elif check == 'alarm':
            message = _alarm_error(motor, axis)
            if message:
                return message
        elif check == 'fault':
            if bool(motor.get('fault', False)):
                return f'Axis {axis} has error'
        elif check == 'servo_on':
            if is_ac_servo and motor.get('servo_on') is not True:
                return f'Axis {axis} servo is OFF'
        elif check == 'internal_limit':
            if is_ac_servo and internal_limit_active:
                return f'Axis {axis} {INTERNAL_LIMIT_MESSAGE}'
        else:
            raise ValueError(f'알 수 없는 준비 검사 항목: {check}')
    return ''
