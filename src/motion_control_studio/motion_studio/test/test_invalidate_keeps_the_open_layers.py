"""실행을 막는 것과 **열어 둔 파일을 덮는 것**은 다르다 · §6-257

스튜디오 노드는 사실을 두 개 들고 있다.

    어느 프로젝트의 파일인가   `_current_project`      레이어 목록·편집·저장
    실행해도 되는가            `_execution_context`    재생·녹화·초기이동

`invalidate_context` 의 뜻은 두 번째다 · 그런데 첫 번째까지 지웠다.

브릿지는 모터가 준비되지 않으면 이것을 **1초마다** 보낸다 · 실측으로 모터
네 대가 전부 오프라인일 때 15초 동안 14번 왔다 · 그동안 스튜디오 상태 신호
44개 전부가 「프로젝트 없음」이었다 · 저장은 그 1초 창과 경주했고, 사람에게는
「먼저 왼쪽에서 통합 프로젝트를 선택하세요」로 보였다.
"""

from pathlib import Path

import pytest

from motion_studio.workspace_session import (
    ExecutionContextNotReady,
    StudioWorkspaceSession,
)


class _Store:
    def use_workspace(self, project_dir):
        self.workspace = project_dir


class _Machine:
    def __init__(self):
        self.cancelled = False

    def cancel(self):
        self.cancelled = True


class _Studio:
    """`invalidate` 가 만지는 칸만 가진 가짜 노드."""

    def __init__(self):
        import threading
        self._lock = threading.RLock()
        self._machine = _Machine()
        self._workspace_project_id = '개선테스트-6781fd05'
        self._current_project = {'project_id': '개선테스트-5c1c819b', 'layers': [1, 2]}
        self._store = _Store()
        self._workspace_catalog_cache = {'projects': []}
        self._execution_context = {'context_id': 'abc', 'project_id': '개선테스트-6781fd05'}
        self._execution_context_ready = True
        self._midi_state = {'a': 1}
        self._motion_run_status = {'b': 2}
        self._run_results = {'c': 3}
        self._midi_results = {'d': 4}
        self._record_started = 12.5
        self._record_frames = [1, 2, 3]
        self._record_eligible_motion_ids = {'1-1'}
        self._recorded_motion_ids = {'1-1'}
        self._status = {'state': 'idle'}
        self._project_generation = 101
        self.motion_projects_dir = Path('/tmp/motion_projects_test')

    def _require_idle_locked(self):
        return None

    def use_workspace(self, project_dir):
        self._workspace_dir = project_dir

    def _operation_machine(self):
        return self._machine

    def _empty_status(self):
        return {}

    def snapshot(self):
        return {'project': self._current_project}


def _session():
    studio = _Studio()
    session = StudioWorkspaceSession(studio)
    return studio, session


def test_the_open_layers_survive():
    studio, session = _session()

    session.invalidate()

    assert studio._current_project is not None
    assert studio._current_project['layers'] == [1, 2]


def test_the_workspace_we_are_in_survives():
    studio, session = _session()

    session.invalidate()

    assert studio._workspace_project_id == '개선테스트-6781fd05'
    assert studio._store is not None


def test_running_is_still_blocked():
    studio, session = _session()

    session.invalidate()

    assert studio._execution_context == {}
    assert studio._execution_context_ready is False


def test_a_running_operation_is_cancelled():
    studio, session = _session()

    session.invalidate()

    assert studio._machine.cancelled is True


def test_recording_leftovers_are_cleared():
    studio, session = _session()

    session.invalidate()

    assert studio._record_frames == []
    assert studio._recorded_motion_ids == set()
    assert studio._midi_state == {}


def test_running_asks_with_its_own_mark():
    """막은 쪽을 알아볼 표시를 단다 · 문구 글자로 맞춰 보지 않는다 · §6-258"""
    studio, session = _session()
    session.invalidate()

    with pytest.raises(ExecutionContextNotReady):
        session.require_execution_context()


def test_changing_the_project_still_wipes_the_layers(tmp_path):
    """파일을 지우는 일은 `select` 가 한다 · 거기는 그대로다."""
    other = tmp_path / '다른프로젝트-00000000'
    other.mkdir()
    (other / 'project.json').write_text('{}', encoding='utf-8')
    studio, session = _session()
    studio.motion_projects_dir = tmp_path

    session.select({'project_id': '다른프로젝트-00000000'})

    assert studio._current_project is None
    assert studio._workspace_project_id == '다른프로젝트-00000000'
