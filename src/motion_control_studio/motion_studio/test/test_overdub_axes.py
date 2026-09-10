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


def test_overdub_narrows_the_eligible_set_at_start():
    """시작할 때 대상 축을 좁힌다 · 끝난 뒤에 알면 한 번을 헛돌린다."""
    source = _source()
    start = source.index('def start(')
    body = source[start:source.index('\n    def ', start)]
    assert "if mode == 'overdub':" in body
    assert 'project_motion_ids(project)' in body
    assert '추가 녹화할 축이 없습니다' in body


def test_append_stays_parked_until_layers_have_a_start_time():
    """이어 녹화는 레이어 시작 시각 개념이 있어야 한다 · 지금은 모두 0초 시작."""
    source = _source()
    start = source.index('def start(')
    body = source[start:source.index('\n    def ', start)]
    assert "if mode == 'append'" in body
    assert '레이어 시작 시각' in body
    # 추가 녹화는 더 이상 함께 막히지 않는다
    assert "mode in {'overdub', 'append'}" not in body
