"""한 상황을 **네 곳이 똑같이** 판단하는가 · §6-275

소유는 축 × 시간이고, 그 사실을 쓰는 곳이 넷이다.

    ① 녹화에서 버리기   스튜디오   `drop_owned_values`
    ② 모터 발행         실행 노드   `_owned_positions`
    ③ 재생 계속 판정    실행 노드   `_playback_axes`
    ④ MIDI SELECT       MIDI 노드   `_channel_follows_playback_locked`

**넷이 각자 판정을 들고 있었고 그중 둘이 시간을 빠뜨렸다.** 경계마다 시험은
있었지만 *한 상황을 넷이 같이* 보는 시험이 없어서, 한쪽만 고쳐져도 초록불이
켜졌다 · 9월 16일에 ③ 을 축별로 고치면서 시간을 안 넣은 것이 그렇게 살아남아
오늘 「1-4 를 잡으면 1-1·1-2·1-3 이 통째로 멈춘다」로 나왔다.

여기 시험은 패키지 경계를 넘는다 · 그것이 목적이다.
"""

import types

import pytest

from motion_common import axis_ownership


# 실측에서 나온 그 상황 · 1-4 는 두 번에 나눠 녹화돼 있고 그 사이가 비었다
MOTION_ID = '1-4'
MOTOR_AXIS = 3
SPANS = [(3.2, 25.1), (27.8, 45.4)]

#: (시각, 재생이 쥐는가)
MOMENTS = [
    (1.0, False),    # 녹화 시작 전 · 사람 차례
    (10.0, True),    # 첫 구간 안 · 재생 차례
    (25.1, True),    # 끝 시각 · 아직 재생 것이다
    (26.0, False),   # 두 구간 사이 · 여기서 죽었다
    (30.0, True),    # 둘째 구간 안 · 재생이 도로 가져간다
    (50.0, False),   # 다 끝난 뒤 · 사람 차례
]


def _by_motion_id():
    return {MOTION_ID: list(SPANS)}


def _by_motor_axis():
    return {MOTOR_AXIS: list(SPANS)}


@pytest.mark.parametrize('time_sec,playbacks', MOMENTS)
def test_the_shared_answer(time_sec, playbacks):
    """먼저 공용 판정이 맞아야 한다 · 나머지 셋은 이것을 부른다."""
    assert axis_ownership.owned_at(SPANS, time_sec) is playbacks


@pytest.mark.parametrize('time_sec,playbacks', MOMENTS)
def test_recording_drops_exactly_what_playback_holds(time_sec, playbacks):
    """① 재생이 쥔 시간에는 사람이 페이더를 만져도 기록하지 않는다."""
    from motion_studio.recording_session import StudioRecordingSession

    studio = types.SimpleNamespace(
        _take=types.SimpleNamespace(ownership=_by_motion_id()),
    )
    session = StudioRecordingSession.__new__(StudioRecordingSession)
    session.studio = studio

    kept = session.drop_owned_values({MOTION_ID: 12.3}, time_sec)

    assert (MOTION_ID not in kept) is playbacks


@pytest.mark.parametrize('time_sec,playbacks', MOMENTS)
def test_publishing_commands_exactly_what_playback_holds(time_sec, playbacks):
    """② 재생이 쥔 시간에만 그 축에 모터 명령을 낸다."""
    from motion_runtime.motion_player import MotionPlayer

    plan = {'axis_playback_spans': _by_motor_axis()}

    published = MotionPlayer._owned_positions(plan, {MOTOR_AXIS: 1.0}, time_sec)

    assert (MOTOR_AXIS in published) is playbacks


@pytest.mark.parametrize('time_sec,playbacks', MOMENTS)
def test_judging_matches_publishing(time_sec, playbacks):
    """③ "내 축을 남이 쥐었나" 를 물을 때도 같은 축만 본다.

    이것이 어긋나면 남의 차례인 축을 자기 축이라 여기고 **실행 전체를 오류로
    끝낸다** · 다른 축들의 재생까지 같이 죽는다.
    """
    from motion_runtime.motion_player import MotionPlayer

    plan = {
        'axes': [{'motor_axis': MOTOR_AXIS}],
        'axis_playback_spans': _by_motor_axis(),
    }

    judged = [
        int(axis_plan['motor_axis'])
        for axis_plan in MotionPlayer._playback_axes(plan, time_sec)
    ]

    assert (MOTOR_AXIS in judged) is playbacks


@pytest.mark.parametrize('time_sec,playbacks', MOMENTS)
def test_midi_select_matches_the_same_answer(time_sec, playbacks):
    """④ 재생이 쥔 시간에는 SELECT 가 추종, 밖에서는 조종이어야 한다."""
    from midi_control.midi_control_node import MidiControlNode

    node = MidiControlNode.__new__(MidiControlNode)
    node._studio_playback_spans = _by_motion_id()
    node._playback_running = False
    node._playback_elapsed_sec = time_sec
    node._playback_elapsed_at = 0.0

    follows = node._channel_follows_playback_locked(
        MOTOR_AXIS, {'motion_id': MOTION_ID},
    )

    assert follows is playbacks


def test_all_four_agree_at_every_moment():
    """넷이 **같은 답**을 내는지 한 줄로 본다 · 하나만 고쳐지면 여기서 걸린다."""
    from midi_control.midi_control_node import MidiControlNode
    from motion_runtime.motion_player import MotionPlayer
    from motion_studio.recording_session import StudioRecordingSession

    session = StudioRecordingSession.__new__(StudioRecordingSession)
    session.studio = types.SimpleNamespace(
        _take=types.SimpleNamespace(ownership=_by_motion_id()),
    )
    node = MidiControlNode.__new__(MidiControlNode)
    node._studio_playback_spans = _by_motion_id()
    node._playback_running = False
    node._playback_elapsed_at = 0.0
    plan = {
        'axes': [{'motor_axis': MOTOR_AXIS}],
        'axis_playback_spans': _by_motor_axis(),
    }

    for time_sec, expected in MOMENTS:
        node._playback_elapsed_sec = time_sec
        answers = {
            '①녹화버림': MOTION_ID not in session.drop_owned_values(
                {MOTION_ID: 1.0}, time_sec,
            ),
            '②발행': MOTOR_AXIS in MotionPlayer._owned_positions(
                plan, {MOTOR_AXIS: 1.0}, time_sec,
            ),
            '③재생판정': MOTOR_AXIS in [
                int(a['motor_axis'])
                for a in MotionPlayer._playback_axes(plan, time_sec)
            ],
            '④MIDI': node._channel_follows_playback_locked(
                MOTOR_AXIS, {'motion_id': MOTION_ID},
            ),
        }
        disagreed = {name: value for name, value in answers.items() if value is not expected}
        assert not disagreed, (
            f'{time_sec}초에 답이 갈렸다 · 재생이 쥐는가={expected} · 어긋난 곳={disagreed}'
        )
