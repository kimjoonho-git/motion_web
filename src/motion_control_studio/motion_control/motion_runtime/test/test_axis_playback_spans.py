"""축별 재생 소유 구간 · 오버더빙 · §6-73 §6-77

재생이 소유 구간 밖에서도 축을 계속 명령하면 `CommandArbiter` 가 그 축을 계속
쥐고 있어 MIDI 가 영영 못 들어온다 · 오버더빙이 성립하지 않는다.

구간 밖은 **끝난 뒤만이 아니다** · 합성은 모든 축을 매 순간 채우므로 10 초부터
데이터가 있는 축도 0 초부터 값이 나온다 · 시작 전도 거른다.

`plan_builder` 의 샘플 계산은 그대로 두고 **발행 직전에만** 거른다 ·
로컬·그룹 실행은 `axis_playback_spans` 를 주지 않으므로 아무 영향이 없다.
"""

from motion_runtime.motion_player import MotionPlayer
from motion_runtime.plan_builder import _axis_playback_spans


_filter = MotionPlayer._owned_positions


def test_without_spans_nothing_is_filtered():
    """로컬·그룹 실행은 이 값을 주지 않는다 · 지금과 똑같이 돌아야 한다."""
    positions = {0: 1.0, 1: 2.0}
    assert _filter({}, positions, 5.0) == positions
    assert _filter({'axis_playback_spans': {}}, positions, 5.0) == positions


def test_an_axis_is_dropped_after_its_span_ends():
    plan = {'axis_playback_spans': {0: [(0.0, 10.0)]}}
    assert _filter(plan, {0: 1.0, 1: 2.0}, 9.99) == {0: 1.0, 1: 2.0}
    assert _filter(plan, {0: 1.0, 1: 2.0}, 10.0) == {0: 1.0, 1: 2.0}
    assert _filter(plan, {0: 1.0, 1: 2.0}, 10.02) == {1: 2.0}


def test_an_axis_is_dropped_before_its_span_starts():
    """10 초부터 녹화된 축은 그 앞 구간을 MIDI 가 써야 한다 · 합성이 0 초부터
    값을 채운다고 재생이 쥐고 있으면 그 앞에 아무것도 얹을 수 없다."""
    plan = {'axis_playback_spans': {0: [(10.0, 20.0)]}}
    assert _filter(plan, {0: 1.0}, 0.02) == {}
    assert _filter(plan, {0: 1.0}, 9.98) == {}
    assert _filter(plan, {0: 1.0}, 10.0) == {0: 1.0}


def test_a_gap_between_two_layers_belongs_to_midi():
    """같은 축에 레이어가 둘 · 그 사이 빈 시간은 MIDI 차례다.

    한 레이어 안의 짧은 공백은 `playback_ownership` 이 이미 메워서 하나의
    구간으로 넘긴다 · 여기 오는 구간은 진짜로 떨어져 있는 것들이다.
    """
    plan = {'axis_playback_spans': {0: [(0.0, 5.0), (20.0, 30.0)]}}
    assert _filter(plan, {0: 1.0}, 5.0) == {0: 1.0}
    assert _filter(plan, {0: 1.0}, 12.0) == {}
    assert _filter(plan, {0: 1.0}, 20.0) == {0: 1.0}


def test_axes_are_released_independently():
    """축마다 끝나는 시각이 다르다 · 먼저 끝난 축부터 MIDI 가 가져간다."""
    plan = {'axis_playback_spans': {0: [(0.0, 5.0)], 1: [(0.0, 20.0)]}}
    assert _filter(plan, {0: 1.0, 1: 2.0, 2: 3.0}, 10.0) == {1: 2.0, 2: 3.0}


def test_an_axis_without_an_entry_is_never_released():
    """구간이 없는 축은 재생이 끝까지 몬다."""
    plan = {'axis_playback_spans': {0: [(0.0, 1.0)]}}
    assert _filter(plan, {0: 1.0, 7: 9.0}, 100.0) == {7: 9.0}


def test_motion_ids_are_translated_to_motor_axes():
    """부르는 쪽은 모션 ID 로 말하고 발행부는 모터축으로 움직인다."""
    axes = [
        {'motion_id': '1-1', 'motor_axis': 0},
        {'motion_id': '1-2', 'motor_axis': 3},
    ]
    payload = {'axis_playback_spans': {'1-1': [[0.0, 10.0]], '9-9': [[0.0, 5.0]]}}
    assert _axis_playback_spans(payload, axes) == {0: [(0.0, 10.0)]}


def test_a_missing_or_bad_span_request_is_ignored():
    axes = [{'motion_id': '1-1', 'motor_axis': 0}]
    assert _axis_playback_spans({}, axes) == {}
    assert _axis_playback_spans({'axis_playback_spans': None}, axes) == {}
    assert _axis_playback_spans({'axis_playback_spans': {'1-1': 'x'}}, axes) == {}
    # 망가진 구간은 버린다 · 남는 것은 "한 번도 안 쥔다" 는 빈 목록이다
    for broken in ([[1.0]], [['x', 1.0]], [[5.0, 1.0]]):
        assert _axis_playback_spans(
            {'axis_playback_spans': {'1-1': broken}}, axes) == {0: []}


def test_an_empty_span_list_means_the_axis_is_never_played():
    """레이어에 없는 축도 초기 이동에는 함께 나서야 한다 · 0도로 맞춰야 MIDI
    절대값이 맞는다 · 그 뒤로는 재생이 건드리면 안 된다 · §6-87"""
    axes = [{'motion_id': '1-1', 'motor_axis': 0}, {'motion_id': '1-2', 'motor_axis': 3}]
    spans = _axis_playback_spans(
        {'axis_playback_spans': {'1-1': [[2.36, 8.92]], '1-2': []}}, axes,
    )
    assert spans == {0: [(2.36, 8.92)], 3: []}

    plan = {'axis_playback_spans': spans}
    assert _filter(plan, {0: 1.0, 3: 2.0}, 5.0) == {0: 1.0}, '빈 목록인 축을 몰고 있다'
    assert _filter(plan, {0: 1.0, 3: 2.0}, 0.02) == {}


# 발행과 판정은 같은 표를 본다 · §6-107
#
# 계획의 `axes` 는 모션에 적힌 축 전부다 · 추가 녹화에서는 그중 일부만 재생이
# 몰고 나머지는 지금 MIDI 로 녹화하는 축이다 · 전부를 두고 "MIDI 가 쓰는
# 중이냐" 를 물으면, 녹화 중인 축 때문에 재생이 스스로 멈춘다.

_owned_axes = MotionPlayer._playback_axes


def _plan(axes, spans=None):
    plan = {'axes': [{'motor_axis': axis} for axis in axes]}
    if spans is not None:
        plan['axis_playback_spans'] = spans
    return plan


def test_the_axis_being_recorded_now_is_not_my_axis():
    """추가 녹화의 모양 · 축 1 은 재생, 축 0 은 지금 MIDI 로 녹화한다."""
    plan = _plan([0, 1], {0: [], 1: [(0.0, 10.0)]})
    assert [a['motor_axis'] for a in _owned_axes(plan)] == [1]


def test_without_spans_every_plan_axis_is_mine():
    """로컬·그룹 실행은 이 표를 주지 않는다 · 지금 그대로 전부가 재생의 축이다."""
    assert [a['motor_axis'] for a in _owned_axes(_plan([0, 1]))] == [0, 1]
    assert [a['motor_axis'] for a in _owned_axes(_plan([0, 1], {}))] == [0, 1]


def test_an_axis_missing_from_the_table_is_mine():
    plan = _plan([0, 1], {1: [(0.0, 10.0)]})
    assert [a['motor_axis'] for a in _owned_axes(plan)] == [0, 1]


def test_judging_and_publishing_agree_on_the_same_axis():
    """발행이 거르는 축은 판정도 남의 축으로 봐야 한다 · 어긋나면 재생이 죽는다."""
    plan = _plan([0, 1], {0: [], 1: [(0.0, 10.0)]})
    published = _filter(plan, {0: 1.0, 1: 2.0}, 5.0)
    judged = {a['motor_axis'] for a in _owned_axes(plan)}
    assert set(published) == judged
