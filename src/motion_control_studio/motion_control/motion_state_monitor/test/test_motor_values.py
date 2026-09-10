"""모터 값 변환 계약 · §6-34 · §6-49.

여기 있는 것은 전부 화면에 그대로 나가는 값이다. 상태어 한 칸이 어긋나면
운전 중인 축이 정지로 보이고, 오류 코드 표기가 어긋나면 없는 고장을 만든다.

`unchecked_float`은 `motion_common.values.optional_float`과 **일부러 다르다** ·
그 차이를 시험이 붙잡아 둔다.
"""

import math

import pytest
from motion_common import values as common_values

from motion_state_monitor import motor_values


# --------------------------------------------------------------------------- #
# 상태어 · MINAS 서보 CiA402
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(('statusword', 'expected'), [
    (0x0637, 'Operation enabled'),      # 실기에서 관측된 값 (§6-36)
    (0x0633, 'Switched on'),
    (0x0631, 'Ready to switch on'),
    (0x0040, 'Switch on disabled'),
    (0x0607, 'Quick stop active'),
    (0x0000, 'Not ready to switch on'),
])
def test_statusword_text_maps_known_states(statusword, expected):
    assert motor_values.statusword_text(statusword) == expected


def test_fault_bit_wins_over_every_other_state():
    """고장은 다른 무엇보다 먼저 보여야 한다 · 비트 3이 서면 그것이 답이다."""
    assert motor_values.statusword_text(0x0637 | 0x0008) == 'Fault'


def test_dynamixel_statusword_reports_torque_only():
    assert motor_values.dynamixel_statusword_text(0x01) == 'Torque enabled'
    assert motor_values.dynamixel_statusword_text(0x00) == 'Torque disabled'


# --------------------------------------------------------------------------- #
# 오류 코드
# --------------------------------------------------------------------------- #

def test_error_text_says_no_error_only_for_zero():
    assert motor_values.error_text(0, '') == 'No error'
    assert motor_values.error_text(0x21, '') == 'Error 33.0'


def test_minas_high_byte_marker_is_stripped():
    """MINAS는 상위 바이트에 표식을 얹는다 · 그대로 보이면 없는 코드가 된다."""
    metadata = {'motor_type': 'minas'}
    assert motor_values.normalized_errorcode(0xFF21, metadata) == 0x21
    # 하위 바이트가 0이면 표식이 아니라 값이다 · 건드리지 않는다
    assert motor_values.normalized_errorcode(0xFF00, metadata) == 0xFF00
    # 다른 기종은 손대지 않는다
    assert motor_values.normalized_errorcode(0xFF21, {'motor_type': 'zeroerr'}) == 0xFF21


def test_hex16_is_always_four_digits():
    assert motor_values.hex16(0) == '0x0000'
    assert motor_values.hex16(0x21) == '0x0021'
    assert motor_values.hex16(0x1FFFF) == '0xFFFF'


# --------------------------------------------------------------------------- #
# `unchecked_float` · 흡수하지 않은 변형 (§6-34)
# --------------------------------------------------------------------------- #

def test_unchecked_float_passes_infinity_unlike_the_common_one():
    """이름이 다른 이유다 · 합치면 `inf`가 조용히 `None`이 된다."""
    assert motor_values.unchecked_float('inf') == math.inf
    assert common_values.optional_float('inf') is None


def test_unchecked_float_returns_none_for_unparsable():
    assert motor_values.unchecked_float(None) is None
    assert motor_values.unchecked_float('') is None
    assert motor_values.unchecked_float('abc') is None
    assert motor_values.unchecked_float('1.5') == 1.5


def test_parse_int_is_the_shared_implementation():
    """§6-34에서 `motion_common`으로 합쳤다 · 다시 갈라지면 안 된다."""
    assert motor_values.parse_int is common_values.optional_int
    assert motor_values.parse_int('0x10') == 16


# --------------------------------------------------------------------------- #
# 라벨 · 집계 · 배열
# --------------------------------------------------------------------------- #

def test_labels_fall_back_to_the_raw_value():
    assert motor_values.motor_type_label('minas') == 'AC Servo'
    assert motor_values.transport_label('ethercat') == 'EtherCAT'
    assert motor_values.motor_type_label('처음보는것') == '처음보는것'
    assert motor_values.transport_label('') == 'Unknown'


def test_count_values_groups_missing_as_unknown():
    rows = [{'k': 'a'}, {'k': 'a'}, {'k': None}, {}]
    assert motor_values.count_values(rows, 'k') == {'a': 2, 'Unknown': 2}


def test_array_value_falls_back_past_the_end():
    message = type('Msg', (), {'channel': [7, 8]})()
    assert motor_values.array_value(message, 'channel', 1, -1) == 8
    assert motor_values.array_value(message, 'channel', 5, -1) == -1
    assert motor_values.array_value(message, 'absent', 0, -1) == -1


# --------------------------------------------------------------------------- #
# Dynamixel 위치 환산
# --------------------------------------------------------------------------- #

def test_dynamixel_position_raw_uses_explicit_scale_first():
    metadata = {'position_raw_per_degree': 10.0, 'dynamixel_zero_position_raw': 2048}
    assert motor_values.dynamixel_position_raw(0.0, metadata) == 2048
    assert motor_values.dynamixel_position_raw(10.0, metadata) == 2148


def test_dynamixel_position_raw_derives_scale_from_pulses():
    metadata = {'pulse_per_revolution': 4096}
    # 영점은 한 바퀴의 절반 · 0도가 가운데다
    assert motor_values.dynamixel_position_raw(0.0, metadata) == 2048
    assert motor_values.dynamixel_position_raw(180.0, metadata) == 4096


def test_dynamixel_position_raw_clamps_to_the_declared_range():
    """범위를 넘겨 보내면 장치가 거부하거나 끝으로 튄다 · 여기서 자른다."""
    metadata = {
        'pulse_per_revolution': 4096,
        'dynamixel_min_position_raw': 1000,
        'dynamixel_max_position_raw': 3000,
    }
    assert motor_values.dynamixel_position_raw(180.0, metadata) == 3000
    assert motor_values.dynamixel_position_raw(-180.0, metadata) == 1000


def test_dynamixel_position_raw_gives_up_without_any_scale():
    assert motor_values.dynamixel_position_raw(10.0, {}) is None
