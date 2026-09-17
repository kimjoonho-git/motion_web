"""반복 방식의 기본값은 한 곳에서만 나온다 · §6-135."""

import pytest

from motion_common import repeat_policy


def test_default_is_reinitialize():
    """화면 선택칸의 기본값(`value="reinitialize" selected`)과 같아야 한다.

    전에는 여덟 곳에 적혀 있었고 답이 두 가지였다 · 화면은 「초기 위치 이동
    후 다음」인데 스케줄은 `direct` 로 쐈고, 시작값과 끝값이 5° 이상 벌어진
    모션은 "연속 동작할 수 없습니다" 로 죽었다.
    """
    assert repeat_policy.DEFAULT_REPEAT_MODE == 'reinitialize'


@pytest.mark.parametrize('value', ('', None, '  ', '이상한값', 'DIRECTED'))
def test_unknown_values_fall_back_to_the_default(value):
    assert repeat_policy.normalize_repeat_mode(value) == 'reinitialize'


@pytest.mark.parametrize('value', ('direct', 'dwell', 'reinitialize', 'dwell_reinitialize'))
def test_known_values_pass_through(value):
    assert repeat_policy.normalize_repeat_mode(value) == value
    assert repeat_policy.normalize_repeat_mode(value.upper()) == value


def test_only_non_reinitializing_modes_need_matching_ends():
    """초기 위치로 돌아가면 시작값과 끝값이 달라도 된다.

    `direct` · `dwell` 만 끝값에서 시작값으로 곧바로 튄다 · 그 둘만 검사한다.
    """
    assert repeat_policy.needs_loop_value_match('direct') is True
    assert repeat_policy.needs_loop_value_match('dwell') is True
    assert repeat_policy.needs_loop_value_match('reinitialize') is False
    assert repeat_policy.needs_loop_value_match('dwell_reinitialize') is False
    # 안 적힌 값은 **검사하는 쪽**이 안전하다 · 설정 기본값과는 다른 질문이다 ·
    # 검사를 건너뛴 채 시작하면 모터가 끝값에서 시작값으로 튄다
    assert repeat_policy.needs_loop_value_match(None) is True
    assert repeat_policy.needs_loop_value_match('이상한값') is True
    assert repeat_policy.normalize_repeat_mode(None) == 'reinitialize'
