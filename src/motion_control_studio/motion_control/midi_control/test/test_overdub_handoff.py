"""추가 녹화 중 SELECT 이어받기 · §6-86

전에는 실행 노드가 끝나는 순간 MIDI 가 모든 SELECT 를 껐다 · 하필 사용자가
이어서 녹화하려는 바로 그 지점이라, 다시 눌러야 했다.

그렇다고 그냥 켜 두면 위험하다 · 페이더는 사용자가 놓아둔 자리에 있고 모터는
재생을 따라 저만치 가 있다 · 구간이 끝나 MIDI 명령이 통하는 순간 모터가 페이더
자리로 **튄다**.

여기서 확인하는 것은 그 둘이다 · 꺼지지 않는가, 그리고 튀지 않는가.
"""

import pytest

from midi_control.overdub_handoff import OverdubHandoff, owned_at


def _status(**extra):
    return {
        'state': 'recording',
        'record_mode': 'overdub',
        'elapsed_sec': 0.0,
        'overdub_spans': {'1-1': [[2.36, 8.92]]},
        **extra,
    }


# --------------------------------------------------------------------- #
# 언제 붙잡는가
# --------------------------------------------------------------------- #

def test_it_holds_the_line_only_while_playback_owns_the_axis():
    handoff = OverdubHandoff()
    seen = []
    for elapsed in (1.0, 3.0, 8.92, 9.0, 12.0):
        handoff.update(_status(elapsed_sec=elapsed))
        seen.append(handoff.owned_channels([['1-1'], ['1-2']]))

    assert seen == [[], [0], [0], [], []], f'붙잡는 구간이 어긋났다 · {seen}'


def test_the_boundary_moment_still_belongs_to_playback():
    """경계에서 먼저 놓으면 그 순간 모터가 튄다 · 늦게 놓는 쪽이 안전하다."""
    handoff = OverdubHandoff()
    handoff.update(_status(elapsed_sec=8.92))
    assert handoff.owned_channels([['1-1']]) == [0]


def test_an_axis_with_no_recorded_data_is_never_held():
    """레이어에 없는 축은 처음부터 끝까지 MIDI 차지다."""
    handoff = OverdubHandoff()
    handoff.update(_status(elapsed_sec=3.0))
    assert handoff.owned_channels([['1-2']]) == []


def test_a_line_that_carries_any_held_axis_is_held():
    """한 라인이 여러 축을 몰 수 있다 · 하나라도 재생 중이면 그 라인은 붙잡는다."""
    handoff = OverdubHandoff()
    handoff.update(_status(elapsed_sec=3.0))
    assert handoff.owned_channels([['1-2', '1-1']]) == [0]


def test_a_gap_between_two_layers_belongs_to_midi():
    handoff = OverdubHandoff()
    handoff.update(_status(
        elapsed_sec=12.0, overdub_spans={'1-1': [[0.0, 5.0], [20.0, 30.0]]},
    ))
    assert handoff.owned_channels([['1-1']]) == []


# --------------------------------------------------------------------- #
# 추가 녹화가 아닐 때는 아무 일도 하지 않는다
# --------------------------------------------------------------------- #

@pytest.mark.parametrize('payload', [
    {'state': 'playing', 'record_mode': None, 'overdub_spans': {'1-1': [[0, 9]]}},
    {'state': 'recording', 'record_mode': 'record', 'overdub_spans': {}},
    {'state': 'idle', 'record_mode': None, 'overdub_spans': {}},
    {'state': 'recording', 'record_mode': 'overdub', 'overdub_spans': {}},
])
def test_it_stays_out_of_the_way_outside_an_overdub_take(payload):
    handoff = OverdubHandoff()
    handoff.update({'elapsed_sec': 3.0, **payload})
    assert handoff.active is False
    assert handoff.owned_channels([['1-1']]) == []


def test_a_finished_take_leaves_nothing_behind():
    """지난 테이크의 구간이 남으면 다음 녹화에서 엉뚱한 축이 막힌다."""
    handoff = OverdubHandoff()
    handoff.update(_status(elapsed_sec=3.0))
    assert handoff.owned_channels([['1-1']]) == [0]

    handoff.update({'state': 'idle', 'record_mode': None, 'overdub_spans': {}})
    assert handoff.spans == {}
    assert handoff.owned_channels([['1-1']]) == []


def test_a_broken_span_does_not_take_the_node_down():
    handoff = OverdubHandoff()
    handoff.update(_status(overdub_spans={'1-1': [[None, 'x'], [1.0], [2.0, 4.0]]}))
    handoff.elapsed_sec = 3.0
    assert handoff.owned_channels([['1-1']]) == [0]


# --------------------------------------------------------------------- #
# 소유 판정은 스튜디오와 같은 규칙이어야 한다
# --------------------------------------------------------------------- #

def test_the_ownership_rule_matches_the_studio():
    """한쪽이 "재생 소유" 라고 보고 다른 쪽이 "MIDI 차례" 라고 보면 그 축은 두
    주인이 동시에 밀거나 아무도 안 민다."""
    from motion_studio.timeline import owned_at as studio_owned_at

    spans = [(2.36, 8.92), (20.0, 30.0)]
    for step in range(0, 1700):
        time_sec = round(step * 0.02, 9)
        assert owned_at(spans, time_sec) == studio_owned_at(spans, time_sec), (
            f'{time_sec}초에서 판정이 갈린다'
        )
