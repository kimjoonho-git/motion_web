"""추가 녹화 · 녹화된 것을 재생하면서 그 위에 얹는다 · §6-74 §6-76 §6-77

녹화된 축은 그 구간 동안 재생이 몰고(모터가 실제로 움직인다), 그 구간 **밖**은
같은 축이라도 MIDI 로 녹화한다 · 소유는 **축 × 시간**으로 갈린다.

재생 쪽과 녹화 쪽이 같은 구간을 봐야 한다 · 한쪽이 "재생 소유" 라고 보고 다른
쪽이 "MIDI 차례" 라고 보면 그 축은 두 주인이 동시에 밀거나 아무도 안 민다.
"""

import threading
import time
from pathlib import Path

import pytest

from motion_runtime.motion_run_constants import INITIAL_MOVE_TIME_OPTIONS_SEC
from motion_runtime.motion_run_rules import _initial_move_time_override_sec
from motion_studio.recording_session import StudioRecordingSession
from motion_studio.studio_node import MotionStudioNode
from motion_studio.take import StudioTake, StudioTakeBoard


def _source():
    from pathlib import Path
    return (
        Path(__file__).parents[1] / 'motion_studio' / 'recording_session.py'
    ).read_text(encoding='utf-8')


def test_overdub_keeps_every_axis_and_owns_by_time():
    """축을 빼지 않는다 · 재생이 쥐는 것은 축이 아니라 축×시간이다 · §6-74

    축 1-1 이 10초에 끝나면 10초 이후에는 같은 축을 MIDI 로 이어 녹화할 수 있어야
    한다 · 축을 통째로 빼면 그게 불가능하다.
    """
    source = _source()
    start = source.index('def start(')
    body = source[start:source.index('\n    def ', start)]
    assert "if mode == 'overdub':" in body
    assert 'playback_ownership(project)' in body
    assert '녹화된 레이어가 있어야' in body
    # 축을 통째로 빼던 옛 규칙은 없어야 한다
    assert 'project_motion_ids(project)' not in body


def test_recording_drops_axes_owned_at_that_moment():
    source = _source()
    start = source.index('def drop_owned_values(')
    body = source[start:source.index('\n    def ', start)]
    assert 'take.ownership' in body
    assert 'owned_at(' in body, '재생과 같은 판정을 쓰지 않는다'

    tick = source[source.index('def record_tick('):]
    assert 'self.drop_owned_values(values, time_sec)' in tick[:900]


class _OwningStudio:
    """`drop_owned_values` 만 보기 위한 최소 대역 · 소유는 테이크가 쥔다 · §6-80"""

    def __init__(self, ownership):
        self._take = StudioTake('overdub', 'running', 1, '', ownership or {})


def _drop(ownership, values, time_sec):
    return StudioRecordingSession(_OwningStudio(ownership)).drop_owned_values(
        values, time_sec,
    )


def test_owned_axis_is_not_recorded_while_playback_holds_it():
    owned = {'1-1': [(0.02, 10.0)]}
    assert _drop(owned, {'1-1': 5.0, '1-2': 7.0}, 4.0) == {'1-2': 7.0}


def test_the_same_axis_records_again_after_playback_ends():
    """축 1-1 이 10초에 끝나면 10초 이후부터 MIDI 로 이어 녹화된다."""
    owned = {'1-1': [(0.02, 10.0)]}
    assert _drop(owned, {'1-1': 5.0}, 10.02) == {'1-1': 5.0}


def test_the_boundary_moment_still_belongs_to_playback():
    owned = {'1-1': [(0.02, 10.0)]}
    assert _drop(owned, {'1-1': 5.0}, 10.0) == {}


def test_a_gap_between_owned_ranges_is_recordable():
    owned = {'1-1': [(0.02, 1.0), (5.0, 6.0)]}
    assert _drop(owned, {'1-1': 5.0}, 3.0) == {'1-1': 5.0}
    assert _drop(owned, {'1-1': 5.0}, 5.5) == {}


def test_plain_recording_drops_nothing():
    """일반 녹화는 소유가 없다 · 지금과 똑같이 전부 기록한다."""
    assert _drop({}, {'1-1': 5.0}, 3.0) == {'1-1': 5.0}
    assert _drop(None, {'1-1': 5.0}, 3.0) == {'1-1': 5.0}


def test_overdub_starts_playback_with_the_same_spans_recording_uses():
    """재생과 녹화가 함께 돌고, **같은 소유 구간**을 본다 · §6-77

    끝 시각만 넘기면 시작 전이 빈다 · 합성은 모든 축을 매 순간 채우므로 10 초부터
    데이터가 있는 축도 0 초부터 명령돼 그 앞을 MIDI 가 못 쓴다.
    """
    source = _source()
    start = source.index('def start_overdub_playback(')
    body = source[start:source.index('\n    def ', start)]

    assert 'take.overdub' in body, '일반 녹화에서도 재생한다'
    assert 'render_project(' in body, '레이어를 합성하지 않는다'
    assert "'start', payload" in body, '재생을 시작하지 않는다'
    assert "'axis_playback_spans'" in body, '축별 소유 구간을 주지 않는다'
    assert 'max(end' not in body, '끝 시각만 넘기면 시작 전 구간이 빈다'


def test_plain_recording_does_not_start_playback():
    source = _source()
    start = source.index('def prepare(')
    body = source[start:source.index('\n    def ', start)]
    assert 'self.start_overdub_playback(' in body
    assert "('추가 녹화 재생 시작', start_playback)" in body, '단계 목록에 없다'


# --------------------------------------------------------------------- #
# 녹화 시계의 0 초는 재생의 0 초여야 한다 · §6-76
# --------------------------------------------------------------------- #

def test_the_recording_clock_waits_for_playback_in_the_step_list():
    """녹화 시계의 0 초는 **재생의 0 초**여야 한다 · §6-76 §6-82

    요청이 받아들여진 순간부터 재면 계획 생성과 초기 이동에 걸린 시간만큼 새
    레이어가 통째로 밀린다 · 사용자가 본 움직임과 저장된 것이 어긋난다.

    기다림 자체는 `StudioProcedure` 가 맡는다 · 여기서는 절차가 **기다린 뒤에**
    시계를 켜는지 순서만 본다.
    """
    source = _source()
    start = source.index('def prepare(')
    body = source[start:source.index('\n    def ', start)]

    gate = body.index('wait_for_run_state(')
    clock = body.index("studio._record_started = time.monotonic()")
    assert gate < clock, '녹화 시계가 재생보다 먼저 출발한다'
    # 상태 목록의 주인은 `run_state` 다 · §6-165 · 여기서는 그것을 기다리는지만
    # 본다 · 목록을 다시 적으면 주인이 둘이 된다
    assert 'run_state_rules.MOVING_STATES' in body, '재생이 도는 것을 확인하지 않는다'


def test_a_finished_take_takes_its_ownership_with_it():
    """소유 구간을 테이크 밖에 따로 두면 다음 작업까지 남아 새는 길이 생긴다 ·
    테이크가 쥐고 있으면 끝나는 순간 함께 사라진다 · §6-80"""
    board = StudioTakeBoard(_BoardStudio())
    board.begin('overdub', '준비', {'1-1': [(0.0, 8.92)]})
    assert board.take.overdub is True
    assert board.take.ownership

    board.finish('완료')
    assert board.take is None


# --------------------------------------------------------------------- #
# 추가 녹화 재생 요청이 실행 노드의 규칙을 지키는가 · §6-78
# --------------------------------------------------------------------- #

class _PayloadStudio:
    """`start_overdub_playback` 이 만드는 요청만 들여다보기 위한 대역."""

    def __init__(self, move_time):
        self._lock = threading.RLock()
        self._take = StudioTake(
            'overdub', 'preparing', 1, '', {'1-1': [(0.02, 8.92)]},
        )
        # 레이어에 없는 1-2 도 녹화 대상이다 · 0 도로 함께 맞춰야 한다
        self._record_eligible_motion_ids = {'1-1', '1-2'}
        self._move_time = move_time
        self.sent = None
        self._store = self

    # 스튜디오가 내어 주는 것들
    def _require_project_locked(self):
        return {
            'project_id': 'p1',
            'mapping_file_id': 'm.yaml',
            'period_sec': 0.02,
            'layers': [{
                'enabled': True,
                'frames': [
                    {'frame': 1, 'time_sec': 0.02, 'values': {'1-1': 0.0}},
                    {'frame': 2, 'time_sec': 8.92, 'values': {'1-1': 3.0}},
                ],
            }],
        }

    def _validate_mapping_locked(self, project):
        return {'motion_ids': ['1-1']}

    def _manual_initial_values(self, mapping):
        return {}

    def write_motion_file(self, name, text, hidden=False):
        return f'__{name}.json'

    def _run_payload(self, project, file_id, motion_ids, move_time):
        return {
            'motion_file_id': file_id,
            'initial_move_time_sec': move_time,
            'active_motion_ids': list(motion_ids),
        }

    def _request_run_for_operation(self, command, payload, timeout, gen, state):
        self.sent = payload
        return {'success': True}


@pytest.mark.parametrize('move_time', INITIAL_MOVE_TIME_OPTIONS_SEC)
def test_overdub_playback_uses_a_move_time_the_run_node_accepts(move_time):
    """실행 노드는 5·7·10 초만 받는다 · 0 을 주면 "모션 실행 준비 실패" 로 끝나고
    추가 녹화가 시작조차 못 한다.

    합성의 0 초 값은 방금 맞춘 0 도와 다를 수 있으니 이동 자체는 필요하다 ·
    사용자가 고른 값을 그대로 넘긴다.
    """
    studio = _PayloadStudio(move_time)
    assert StudioRecordingSession(studio).start_overdub_playback(1, move_time)

    sent = studio.sent
    assert sent['initial_move_time_sec'] == move_time
    # 실행 노드 자신의 규칙으로 확인한다 · 값만 베껴 적으면 규칙이 바뀔 때 갈린다
    assert _initial_move_time_override_sec(sent) == move_time


def test_the_overdub_request_carries_the_countdown_so_the_move_happens_once():
    """전에는 스튜디오가 0 도 이동을 따로 시키고 카운트다운도 직접 돌린 뒤
    재생을 시켰다 · 실행 노드의 `start` 가 원래 그 셋을 이어서 하므로 **초기
    이동이 두 번** 일어났다 · §6-87"""
    studio = _PayloadStudio(5.0)
    assert StudioRecordingSession(studio).start_overdub_playback(1, 5.0)

    assert studio.sent['countdown_sec'] == 3.0, '카운트다운을 실행 노드에 맡기지 않는다'


def test_axes_with_no_layer_data_move_with_the_rest_but_are_never_played():
    """레이어에 없는 축도 초기 이동에는 나서야 한다 · 0 도로 맞춰야 MIDI
    절대값이 맞는다 · 그 뒤로는 재생이 건드리면 안 된다."""
    studio = _PayloadStudio(5.0)
    assert StudioRecordingSession(studio).start_overdub_playback(1, 5.0)

    assert studio.sent['active_motion_ids'] == ['1-1', '1-2'], '빈 축이 초기 이동에서 빠졌다'
    spans = studio.sent['axis_playback_spans']
    assert spans['1-1'] == [[0.02, 8.92]]
    assert spans['1-2'] == [], '레이어에 없는 축을 재생이 쥐고 있다'


def test_the_run_node_rejects_a_zero_move_time():
    """이 규칙 때문에 추가 녹화가 막혔다 · 규칙 쪽을 못 박아 둔다."""
    with pytest.raises(ValueError, match='initial_move_time_sec'):
        _initial_move_time_override_sec({'initial_move_time_sec': 0.0})


class _BoardStudio:
    """테이크 판만 돌리기 위한 최소 대역."""

    def __init__(self):
        self._generation = 0

    def _operation_machine(self):
        studio = self

        class _Machine:
            def begin(self, state):
                studio._generation += 1
                return studio._generation

        return _Machine()

    def _project_status_locked(self, message):
        pass


# --------------------------------------------------------------------------- #
# 새 레이어는 기존 레이어와 **절대 겹치지 않는다** · §6-277
#
# 겹치면 `require_conflict_free_layers` 가 그 프로젝트의 재생을 통째로 거부한다 ·
# 한 번 겹쳐 두면 그 뒤로 추가 녹화가 아예 안 걸린다 · 실측으로 그렇게 막혔다.
#
# `record_tick` 이 매 순간 버리지만, 녹화 시계와 재생 시계가 한 프레임 어긋나도
# 값이 새지 않도록 레이어를 만들기 직전에 한 번 더 거른다.
# --------------------------------------------------------------------------- #

def _session_with(ownership):
    import types
    from motion_studio.recording_session import StudioRecordingSession

    session = StudioRecordingSession.__new__(StudioRecordingSession)
    session.studio = types.SimpleNamespace(
        _take=types.SimpleNamespace(ownership=ownership),
    )
    return session


def test_values_inside_a_recorded_span_never_reach_the_layer():
    session = _session_with({'1-4': [(3.2, 25.1)]})
    frames = [
        {'frame': 1, 'time_sec': 10.0, 'values': {'1-4': 1.0}},
        {'frame': 2, 'time_sec': 26.0, 'values': {'1-4': 2.0}},
    ]

    kept = session.without_owned_values(frames)

    assert kept[0]['values'] == {}, '이미 녹화된 시간인데 값이 들어갔다'
    assert kept[1]['values'] == {'1-4': 2.0}, '빈 시간의 값까지 버렸다'


def test_two_recorded_spans_leave_only_the_gap():
    session = _session_with({'1-4': [(3.2, 25.1), (27.8, 45.4)]})
    frames = [
        {'frame': 1, 'time_sec': 26.5, 'values': {'1-4': 1.0}},
        {'frame': 2, 'time_sec': 30.0, 'values': {'1-4': 2.0}},
        {'frame': 3, 'time_sec': 50.0, 'values': {'1-4': 3.0}},
    ]

    kept = session.without_owned_values(frames)

    assert [frame['values'] for frame in kept] == [
        {'1-4': 1.0}, {}, {'1-4': 3.0},
    ]


def test_a_recording_entirely_inside_a_span_makes_no_layer():
    """다 버려지면 레이어를 만들지 않는다 · 빈 레이어가 쌓이지 않는다."""
    session = _session_with({'1-4': [(0.0, 60.0)]})
    frames = [{'frame': 1, 'time_sec': 10.0, 'values': {'1-4': 1.0}}]

    assert session.without_owned_values(frames) == []


def test_a_plain_recording_keeps_everything():
    """보통 녹화는 소유 구간이 없다 · 그대로 둔다."""
    session = _session_with({})
    frames = [{'frame': 1, 'time_sec': 10.0, 'values': {'1-1': 1.0}}]

    assert session.without_owned_values(frames) == frames
