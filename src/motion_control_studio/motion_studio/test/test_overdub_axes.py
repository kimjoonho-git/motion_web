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
    assert '_record_ownership' in body
    assert 'owned_at(' in body, '재생과 같은 판정을 쓰지 않는다'

    tick = source[source.index('def record_tick('):]
    assert 'self.drop_owned_values(values, time_sec)' in tick[:900]


class _OwningStudio:
    """`drop_owned_values` 만 보기 위한 최소 대역 · §6-74"""

    def __init__(self, ownership):
        self._record_ownership = ownership


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

    assert "studio._record_mode != 'overdub'" in body, '일반 녹화에서도 재생한다'
    assert 'render_project(' in body, '레이어를 합성하지 않는다'
    assert "'start', payload" in body, '재생을 시작하지 않는다'
    assert "'axis_playback_spans'" in body, '축별 소유 구간을 주지 않는다'
    assert 'max(end' not in body, '끝 시각만 넘기면 시작 전 구간이 빈다'


def test_plain_recording_does_not_start_playback():
    source = _source()
    start = source.index('def prepare(')
    body = source[start:source.index('\n    def ', start)]
    assert 'self.start_overdub_playback(operation_generation, move_time)' in body


# --------------------------------------------------------------------- #
# 녹화 시계의 0 초는 재생의 0 초여야 한다 · §6-76
# --------------------------------------------------------------------- #

def _clock_node():
    """녹화 시계만 보기 위한 최소한의 스튜디오."""
    node = MotionStudioNode.__new__(MotionStudioNode)
    node._lock = threading.RLock()
    node._operation_generation = 7
    node._motion_run_status = {}
    return node


def test_recording_clock_waits_for_playback_to_actually_run():
    """요청이 받아들여진 순간부터 재면 계획 생성과 초기 이동에 걸린 시간만큼
    새 레이어가 통째로 밀린다 · 사용자가 본 움직임과 저장된 것이 어긋난다."""
    node = _clock_node()
    session = StudioRecordingSession(node)
    node._motion_run_status = {'state': 'preparing'}

    started = threading.Event()

    def flip():
        time.sleep(0.05)
        with node._lock:
            node._motion_run_status = {'state': 'running'}
        started.set()

    threading.Thread(target=flip, daemon=True).start()
    begin = time.monotonic()
    session.wait_for_playback_running(7, 5.0)
    waited = time.monotonic() - begin

    assert started.is_set(), '재생이 돌기도 전에 녹화 시계가 출발했다'
    assert waited >= 0.04


def test_recording_clock_gives_up_when_playback_errors():
    """재생이 실패했는데 녹화만 도는 일은 없어야 한다."""
    node = _clock_node()
    node._motion_run_status = {'state': 'error', 'message': '계획 생성 실패'}
    session = StudioRecordingSession(node)

    with pytest.raises(ValueError, match='계획 생성 실패'):
        session.wait_for_playback_running(7, 1.0)


def test_recording_clock_stops_waiting_when_the_operation_is_replaced():
    """사용자가 중지하면 기다림도 끝난다 · 멈춘 자리에서 계속 돌면 안 된다."""
    node = _clock_node()
    node._motion_run_status = {'state': 'preparing'}
    session = StudioRecordingSession(node)
    node._operation_generation = 8

    session.wait_for_playback_running(7, 5.0)


def test_a_finished_take_stops_being_an_overdub_take():
    """녹화 모드가 남아 있으면 다음 합성 미리보기가 추가 녹화로 오인돼
    상태 전이를 통째로 잃는다."""
    node = MotionStudioNode.__new__(MotionStudioNode)
    node._record_mode = 'overdub'
    node._record_ownership = {'1-1': [(0.0, 8.92)]}
    session = StudioRecordingSession(node)

    assert session.overdub_take_locked() is True
    session.clear_take_locked()
    assert session.overdub_take_locked() is False
    assert node._record_ownership == {}


def test_the_recording_clock_gate_is_actually_wired_into_prepare():
    """`wait_for_playback_running` 은 있으나 마나가 되기 쉽다 · 호출 한 줄만
    지워도 함수 자체의 검사는 그대로 통과한다 · 그래서 호출 지점을 못 박는다.

    모터가 걸린 확인은 사용자가 직접 한다 · 여기서는 순서만 본다.
    """
    source = (
        Path(__file__).resolve().parents[1]
        / 'motion_studio' / 'recording_session.py'
    ).read_text(encoding='utf-8')

    gate = source.index('self.wait_for_playback_running(')
    clock = source.index('studio._record_started = time.monotonic()')
    assert gate < clock, '녹화 시계가 재생보다 먼저 출발한다'


# --------------------------------------------------------------------- #
# 추가 녹화 재생 요청이 실행 노드의 규칙을 지키는가 · §6-78
# --------------------------------------------------------------------- #

class _PayloadStudio:
    """`start_overdub_playback` 이 만드는 요청만 들여다보기 위한 대역."""

    def __init__(self, move_time):
        self._lock = threading.RLock()
        self._record_mode = 'overdub'
        self._record_ownership = {'1-1': [(0.02, 8.92)]}
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
        return {'motion_file_id': file_id, 'initial_move_time_sec': move_time}

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


def test_the_run_node_rejects_a_zero_move_time():
    """이 규칙 때문에 추가 녹화가 막혔다 · 규칙 쪽을 못 박아 둔다."""
    with pytest.raises(ValueError, match='initial_move_time_sec'):
        _initial_move_time_override_sec({'initial_move_time_sec': 0.0})
