"""`motor_readiness` 동치 검증 · 통합 전 열 곳의 판정을 그대로 재현하는가.

각 검사는 통합 전 원본 코드를 그대로 옮겨 적은 참조 구현과 대조한다. 문구가
한 글자라도 달라지면 화면과 기존 테스트가 깨지므로 메시지까지 비교한다.
"""

from __future__ import annotations

import itertools

from motion_common import motor_readiness


# --------------------------------------------------------------------------- #
# 통합 전 원본 판정 · 참조 구현
# --------------------------------------------------------------------------- #

def _legacy_motion_run(motor, axis, is_ac_servo):
    if str(motor.get('state') or '') != 'detected':
        return f'Axis {axis} is not detected'
    errorcode = int(str(motor.get('errorcode') or 0), 0)
    if errorcode:
        error_hex = str(motor.get('errorcode_hex') or f'0x{errorcode & 0xFFFF:04X}')
        error_text = str(motor.get('error_text') or '').strip()
        detail = f' ({error_text})' if error_text else ''
        return f'Axis {axis} motor alarm {error_hex}{detail}'
    if bool(motor.get('fault', False)):
        return f'Axis {axis} has error'
    if is_ac_servo and motor.get('servo_on') is not True:
        return f'Axis {axis} servo is OFF'
    return ''


def _legacy_midi(motor, axis, is_ac_servo, limit_active):
    if str(motor.get('state') or '') != 'detected':
        return f'Axis {axis} is not detected'
    if bool(motor.get('fault', False)):
        return f'Axis {axis} has error'
    if is_ac_servo:
        if motor.get('servo_on') is not True:
            return f'Axis {axis} servo is OFF'
        if limit_active:
            return (
                f'Axis {axis} internal limit is active; '
                'check POT/NOT, emergency stop, torque limit, and software limit'
            )
    return ''


def _legacy_manual(motor, axis, is_ac_servo):
    if str(motor.get('state') or '') != 'detected':
        return f'Axis {axis} is not detected'
    if is_ac_servo and motor.get('servo_on') is not True:
        return f'Axis {axis} servo is OFF'
    if bool(motor.get('fault', False)):
        return f'Axis {axis} has error'
    return ''


def _motor_cases():
    """상태 조합 전수 · 검사 항목이 서로를 가리는 순서까지 훑는다."""
    for state, fault, servo_on, errorcode in itertools.product(
        ('detected', 'missing', ''),
        (True, False),
        (True, False, None),
        (0, '0x81'),
    ):
        yield {
            'controller_index': 3,
            'state': state,
            'fault': fault,
            'servo_on': servo_on,
            'errorcode': errorcode,
            'error_text': 'over voltage' if errorcode else '',
        }


def test_motion_run_order_matches_legacy():
    for motor in _motor_cases():
        for is_ac_servo in (True, False):
            assert motor_readiness.readiness_error(
                motor,
                order=motor_readiness.MOTION_RUN_ORDER,
                is_ac_servo=is_ac_servo,
            ) == _legacy_motion_run(motor, 3, is_ac_servo)


def test_midi_order_matches_legacy():
    for motor in _motor_cases():
        for is_ac_servo, limit in itertools.product((True, False), (True, False)):
            assert motor_readiness.readiness_error(
                motor,
                order=motor_readiness.MIDI_ORDER,
                axis=3,
                is_ac_servo=is_ac_servo,
                internal_limit_active=limit,
            ) == _legacy_midi(motor, 3, is_ac_servo, limit)


def test_manual_order_matches_legacy():
    for motor in _motor_cases():
        for is_ac_servo in (True, False):
            assert motor_readiness.readiness_error(
                motor,
                order=motor_readiness.MANUAL_ORDER,
                axis=3,
                is_ac_servo=is_ac_servo,
            ) == _legacy_manual(motor, 3, is_ac_servo)


def test_manual_ignores_internal_limit():
    """조그로 리밋에 걸린 축을 빼낼 수 있어야 한다 · 의도된 예외."""
    motor = {'controller_index': 3, 'state': 'detected', 'fault': False, 'servo_on': True}
    assert motor_readiness.readiness_error(
        motor,
        order=motor_readiness.MANUAL_ORDER,
        axis=3,
        is_ac_servo=True,
        internal_limit_active=True,
    ) == ''


def test_only_motion_run_checks_alarm_code():
    """알람 검사는 실행 경로에만 있다 · 2026-09-10 확정된 설계다.

    조그·MIDI는 사람이 화면을 보며 축 하나를 움직이는 중이라 알람이 그 자리에서
    보이고, 알람 축을 조그로 빼내는 길도 열려 있어야 한다. 파일 재생만 사람이
    없는 동안 여러 축이 동시에 돌므로 사전에 막는다.

    이 테스트가 깨진다면 그 판단을 뒤집는 변경이다 · 문구만 맞추지 말 것.
    """
    motor = {
        'controller_index': 3, 'state': 'detected', 'fault': False,
        'servo_on': True, 'errorcode': '0x81',
    }
    assert 'motor alarm' in motor_readiness.readiness_error(
        motor, order=motor_readiness.MOTION_RUN_ORDER, is_ac_servo=True,
    )
    assert motor_readiness.readiness_error(
        motor, order=motor_readiness.MIDI_ORDER, axis=3, is_ac_servo=True,
    ) == ''
    assert motor_readiness.readiness_error(
        motor, order=motor_readiness.MANUAL_ORDER, axis=3, is_ac_servo=True,
    ) == ''


def test_axis_falls_back_to_controller_index():
    motor = {'controller_index': 7, 'state': 'missing'}
    assert motor_readiness.readiness_error(
        motor, order=motor_readiness.MANUAL_ORDER,
    ) == 'Axis 7 is not detected'


def test_unknown_check_is_rejected():
    try:
        motor_readiness.readiness_error(
            {'controller_index': 3, 'state': 'detected'},
            order=('detected', 'nope'),
        )
    except ValueError as exc:
        assert 'nope' in str(exc)
    else:
        raise AssertionError('알 수 없는 검사 항목을 통과시켰습니다')
