"""수치 변환 단일 구현 검증.

노드마다 흩어져 있던 구현을 흡수한 결과라, 흡수 대상들이 공유하던 계약을
그대로 지키는지가 핵심이다.
"""

import math

from motion_common import values


# --------------------------------------------------------------------------- #
# optional_float
# --------------------------------------------------------------------------- #

def test_optional_float_converts_numbers_and_numeric_strings():
    assert values.optional_float(1.5) == 1.5
    assert values.optional_float('1.5') == 1.5
    assert values.optional_float(3) == 3.0
    assert values.optional_float('  2.5  ') == 2.5


def test_optional_float_treats_none_and_blank_as_absent():
    assert values.optional_float(None) is None
    assert values.optional_float('') is None
    assert values.optional_float(None, 9.0) == 9.0
    assert values.optional_float('', 9.0) == 9.0


def test_optional_float_rejects_non_finite():
    assert values.optional_float(float('inf')) is None
    assert values.optional_float(float('-inf')) is None
    assert values.optional_float(float('nan')) is None
    assert values.optional_float('inf', 7.0) == 7.0
    assert values.optional_float(float('nan'), 7.0) == 7.0


def test_optional_float_rejects_unconvertible():
    assert values.optional_float('abc') is None
    assert values.optional_float([1, 2]) is None
    assert values.optional_float({}, -1.0) == -1.0


def test_optional_float_accepts_zero_and_negative():
    # 거짓값이라고 걸러지면 안 된다
    assert values.optional_float(0) == 0.0
    assert values.optional_float('0') == 0.0
    assert values.optional_float(-3.5) == -3.5


# --------------------------------------------------------------------------- #
# finite_float
# --------------------------------------------------------------------------- #

def test_finite_float_is_optional_float_without_default():
    for probe in (1.5, '2', '', None, 'abc', float('inf'), 0):
        assert values.finite_float(probe) == values.optional_float(probe, None)


def test_finite_float_result_is_always_finite_or_none():
    for probe in (1.5, float('inf'), float('nan'), 'abc', None):
        result = values.finite_float(probe)
        assert result is None or math.isfinite(result)


# --------------------------------------------------------------------------- #
# optional_int
# --------------------------------------------------------------------------- #

def test_optional_int_converts_decimal():
    assert values.optional_int(5) == 5
    assert values.optional_int('5') == 5
    assert values.optional_int('-5') == -5


def test_optional_int_honours_base_prefix():
    # 모터 레지스터 주소가 16진 문자열로 들어오는 경우가 있다
    assert values.optional_int('0x10') == 16
    assert values.optional_int('0b101') == 5
    assert values.optional_int('0o17') == 15


def test_optional_int_treats_none_and_blank_as_absent():
    assert values.optional_int(None) is None
    assert values.optional_int('') is None
    assert values.optional_int(None, 3) == 3
    assert values.optional_int('', 3) == 3


def test_optional_int_rejects_fractional_text():
    # 절사하지 않고 실패로 처리한다
    assert values.optional_int('3.7') is None
    assert values.optional_int('3.7', -1) == -1


def test_optional_int_rejects_unconvertible():
    assert values.optional_int('abc') is None
    assert values.optional_int([1], 0) == 0


def test_optional_int_accepts_zero():
    assert values.optional_int(0) == 0
    assert values.optional_int('0') == 0


# --------------------------------------------------------------------------- #
# 흡수 대상과의 동치
# --------------------------------------------------------------------------- #

PROBES = [
    None, '', 0, '0', 1, '1', -1, 1.5, '1.5', '3.7', 'abc', '0x10',
    float('inf'), float('nan'), [1], {},
]


def test_matches_absorbed_finite_float_implementations():
    """흡수한 _finite_float 구현들과 모든 입력에서 같은 답을 낸다."""
    def absorbed(value):
        if value is None or value == '':
            return None
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if math.isfinite(number) else None

    for probe in PROBES:
        assert values.finite_float(probe) == absorbed(probe), probe


def test_matches_absorbed_optional_int_implementations():
    """흡수한 _optional_int 구현들과 모든 입력에서 같은 답을 낸다."""
    def absorbed(value, default):
        if value is None or value == '':
            return default
        try:
            return int(str(value), 0)
        except (TypeError, ValueError):
            return default

    for probe in PROBES:
        assert values.optional_int(probe, None) == absorbed(probe, None), probe


def test_motion_table_reuses_the_same_implementation():
    from motion_common import motion_table
    assert motion_table.finite_float is values.finite_float
