"""추가 녹화 · 어느 축을 녹화할 수 있는가 · §6-71

기존 레이어를 재생하며 **다른 축**을 이어 녹화한다. 1단계에서는 재생을 함께
돌리지 않으므로(모터는 새 축만 움직인다) 축이 겹치면 두 레이어가 같은 축·같은
시간을 갖게 되어 `layer_conflicts` 가 합성을 거절한다 · 녹화가 끝난 뒤에 알면
한 번을 헛돌리므로 **시작할 때** 막고, 화면에는 미리 알린다.
"""

import pytest

from motion_studio.recording_session import StudioRecordingSession


class FakeStore:
    def __init__(self, motion_ids):
        self._motion_ids = motion_ids

    def mapping_check(self, project):
        return {'matches_project': True, 'motion_ids': list(self._motion_ids)}


class FakeStudio:
    def __init__(self, motion_ids, layers):
        self._store = FakeStore(motion_ids)
        self._current_project = {'layers': layers}


def _layer(motion_ids, enabled=True):
    return {
        'enabled': enabled,
        'frames': [{'frame': 1, 'time_sec': 0.02,
                    'values': {mid: 0.0 for mid in motion_ids}}],
    }


def test_candidates_exclude_axes_already_recorded():
    studio = FakeStudio(['1-1', '1-2', '1-3'], [_layer(['1-1', '1-2'])])
    session = StudioRecordingSession(studio)
    assert session.overdub_candidates_locked() == ['1-3']


def test_disabled_layers_do_not_reserve_axes():
    """레이어를 끄면 그 축을 다시 녹화할 수 있어야 한다."""
    studio = FakeStudio(['1-1', '1-2'], [_layer(['1-1'], enabled=False)])
    session = StudioRecordingSession(studio)
    assert session.overdub_candidates_locked() == ['1-1', '1-2']


def test_no_candidates_when_every_axis_is_taken():
    studio = FakeStudio(['1-1'], [_layer(['1-1'])])
    session = StudioRecordingSession(studio)
    assert session.overdub_candidates_locked() == []


def test_no_project_gives_no_candidates():
    session = StudioRecordingSession(FakeStudio([], []))
    session.studio._current_project = None
    assert session.overdub_candidates_locked() == []


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
    assert 'start - 1e-9 <= time_sec <= end + 1e-9' in body, '시간 구간을 보지 않는다'

    tick = source[source.index('def record_tick('):]
    assert 'self.drop_owned_values(values, time_sec)' in tick[:900]


def test_append_stays_parked_until_layers_have_a_start_time():
    """이어 녹화는 레이어 시작 시각 개념이 있어야 한다 · 지금은 모두 0초 시작."""
    source = _source()
    start = source.index('def start(')
    body = source[start:source.index('\n    def ', start)]
    assert "if mode == 'append'" in body
    assert '레이어 시작 시각' in body
    # 추가 녹화는 더 이상 함께 막히지 않는다
    assert "mode in {'overdub', 'append'}" not in body


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


def test_overdub_starts_playback_with_per_axis_release():
    """재생과 녹화가 함께 돈다 · 재생은 축이 끝나면 놓는다 · §6-74"""
    source = _source()
    start = source.index('def start_overdub_playback(')
    body = source[start:source.index('\n    def ', start)]

    assert "studio._record_mode != 'overdub'" in body, '일반 녹화에서도 재생한다'
    assert 'render_project(' in body, '레이어를 합성하지 않는다'
    assert "'start', payload" in body, '재생을 시작하지 않는다'
    assert "'axis_release_sec'" in body, '축별 종료 시각을 주지 않는다'
    # 축마다 마지막 소유 시각까지만 몬다
    assert 'max(end for _start, end in spans)' in body


def test_plain_recording_does_not_start_playback():
    source = _source()
    start = source.index('def prepare(')
    body = source[start:source.index('\n    def ', start)]
    assert 'self.start_overdub_playback(operation_generation)' in body
