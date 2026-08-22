"""프로젝트 세대 검증 단일 구현 검증.

이 검증의 목적은 프로젝트를 바꾼 뒤 이전 세대의 요청·응답이 뒤늦게 도착해
새 프로젝트의 모터를 움직이는 일을 막는 것이다. 네 노드에 복제돼 있던 구현을
흡수했으므로 원래 계약을 그대로 지키는지가 핵심.
"""

import pytest

from motion_common import generation


# --------------------------------------------------------------------------- #
# 요청 식별자
# --------------------------------------------------------------------------- #

def test_marker_format_is_stable():
    """표식 형식이 바뀌면 노드끼리 응답을 서로 못 알아본다."""
    assert generation.generation_marker(12) == '-g12-'
    assert generation.generation_marker('7') == '-g7-'


def test_new_request_id_embeds_generation():
    value = generation.new_request_id('studio-run', 7)
    assert value.startswith('studio-run-g7-')
    assert generation.request_id_matches(value, 7)


def test_new_request_id_accepts_explicit_suffix():
    assert generation.new_request_id('midi-hold', 3, '5-42') == 'midi-hold-g3-5-42'


def test_new_request_ids_are_unique_by_default():
    ids = {generation.new_request_id('p', 1) for _ in range(200)}
    assert len(ids) > 1


def test_request_id_matches_rejects_other_generations():
    value = generation.new_request_id('p', 7)
    assert generation.request_id_matches(value, 7)
    assert not generation.request_id_matches(value, 8)
    assert not generation.request_id_matches(value, 70)


def test_request_id_matches_handles_junk():
    assert not generation.request_id_matches(None, 1)
    assert not generation.request_id_matches('', 1)
    assert not generation.request_id_matches('no-marker', 1)
    assert not generation.request_id_matches('p-g1-x', 'abc')


def test_generation_is_not_matched_by_prefix_collision():
    """``-g1-``이 ``-g11-``에 걸려들면 안 된다."""
    value = generation.new_request_id('p', 11)
    assert not generation.request_id_matches(value, 1)


# --------------------------------------------------------------------------- #
# 페이로드 검사
# --------------------------------------------------------------------------- #

def test_payload_generation_extracts_int():
    assert generation.payload_generation({'project_generation': 5}) == 5
    assert generation.payload_generation({'project_generation': '5'}) == 5


def test_payload_generation_returns_none_for_missing_or_bad():
    assert generation.payload_generation({}) is None
    assert generation.payload_generation({'project_generation': 'x'}) is None
    assert generation.payload_generation(None) is None
    assert generation.payload_generation('text') is None


def test_response_matches_requires_both_generation_and_marker():
    payload = {'project_generation': 7, 'request_id': generation.new_request_id('p', 7)}
    assert generation.response_matches(payload, 7)

    # 세대는 맞지만 식별자 표식이 다르다
    assert not generation.response_matches(
        {'project_generation': 7, 'request_id': 'p-g6-1'}, 7
    )
    # 식별자는 맞지만 세대 필드가 다르다
    assert not generation.response_matches(
        {'project_generation': 6, 'request_id': generation.new_request_id('p', 7)}, 7
    )


def test_response_matches_rejects_non_mapping_and_missing_fields():
    assert not generation.response_matches(None, 1)
    assert not generation.response_matches('text', 1)
    assert not generation.response_matches({}, 1)
    assert not generation.response_matches({'project_generation': 1}, 1)


# --------------------------------------------------------------------------- #
# 요청 검증
# --------------------------------------------------------------------------- #

def test_accepts_matching_generation():
    assert generation.validate_request_generation(
        5, {'project_generation': 5}, current_generation=5, advances_context=False
    ) == 5


def test_rejects_generation_mismatch_between_envelope_and_payload():
    with pytest.raises(ValueError, match='일치하지 않습니다'):
        generation.validate_request_generation(
            5, {'project_generation': 4}, current_generation=5, advances_context=False
        )


def test_rejects_missing_or_non_numeric_generation():
    for bad in (None, '', 'abc', {}):
        with pytest.raises(ValueError, match='필요합니다'):
            generation.validate_request_generation(
                bad, {'project_generation': bad},
                current_generation=1, advances_context=False,
            )


def test_rejects_zero_and_negative_generations():
    for bad in (0, -1):
        with pytest.raises(ValueError, match='일치하지 않습니다'):
            generation.validate_request_generation(
                bad, {'project_generation': bad},
                current_generation=bad, advances_context=False,
            )


def test_rejects_stale_request_when_not_advancing():
    with pytest.raises(ValueError, match='다른 요청을 폐기'):
        generation.validate_request_generation(
            4, {'project_generation': 4}, current_generation=5, advances_context=False
        )


def test_rejects_future_request_when_not_advancing():
    with pytest.raises(ValueError, match='다른 요청을 폐기'):
        generation.validate_request_generation(
            6, {'project_generation': 6}, current_generation=5, advances_context=False
        )


def test_context_command_accepts_a_newer_generation():
    """실행 컨텍스트를 새로 세우는 명령만 세대를 올릴 수 있다."""
    assert generation.validate_request_generation(
        6, {'project_generation': 6}, current_generation=5, advances_context=True
    ) == 6


def test_context_command_still_rejects_an_older_generation():
    with pytest.raises(ValueError, match='이전 프로젝트 세대'):
        generation.validate_request_generation(
            4, {'project_generation': 4}, current_generation=5, advances_context=True
        )


def test_missing_current_generation_is_treated_as_zero():
    assert generation.validate_request_generation(
        1, {'project_generation': 1}, current_generation=None, advances_context=True
    ) == 1


def test_validation_does_not_mutate_caller_state():
    """상태 반영은 호출부의 몫 · 이 함수는 값만 돌려준다."""
    payload = {'project_generation': 3}
    generation.validate_request_generation(
        3, payload, current_generation=3, advances_context=False
    )
    assert payload == {'project_generation': 3}


# --------------------------------------------------------------------------- #
# 명령 분류
# --------------------------------------------------------------------------- #

def test_default_context_commands():
    assert generation.advances_context('apply_context')
    assert generation.advances_context('invalidate_context')
    assert not generation.advances_context('list')


def test_custom_command_set_for_midi_control():
    commands = {'select_project', 'invalidate_context'}
    assert generation.advances_context('select_project', commands)
    assert not generation.advances_context('apply_context', commands)
