"""실행을 막는 것과 **열어 둔 뱅크를 버리는 것**은 다르다 · §6-265

MIDI 노드는 사실을 두 개 들고 있다.

    어느 프로젝트의 뱅크인가   `_banks`               화면에 보이고 손으로 고친다
    실행해도 되는가            `_execution_context`   모터로 내보내도 되는가

`invalidate_context` 의 뜻은 두 번째인데 첫 번째까지 버렸다.

브릿지는 실행 컨텍스트가 준비되지 않으면 **1초마다** 이것을 보낸다 · 실측으로
12초에 11번 왔고, 그때마다 뱅크가 빈 것이 됐다가 뒤이은 `select_project` 가
파일에서 다시 읽었다 · 15초 동안 활성 뱅크가 `bank_1` ↔ `bank_2` 로 696번
뒤집혔고, 화면에서는 활성 뱅크가 보였다 안 보였다 했다.
"""

import threading


class _Banks:
    def __init__(self, tag):
        self.tag = tag

    def export_state(self):
        return {'tag': self.tag}

    def snapshot(self):
        return {'active_bank_id': self.tag, 'banks': [{'bank_id': self.tag}]}


class _Node:
    """`_cmd_invalidate_context` 가 만지는 칸만 가진 가짜 노드."""

    def __init__(self):
        self._lock = threading.RLock()
        self._project_id = '개선테스트-6781fd05'
        self._mappings_dir = 'mappings'
        self._axis_registry = 'registry'
        self._selected_mapping_file_id = '12354.yaml'
        self._preferred_mapping_file_id = '12354.yaml'
        self._run_mapping_file_id = '12354.yaml'
        self._latest_motion_state = {'a': 1}
        self._bank_config_file = 'banks.yaml'
        self._banks = _Banks('bank_2')
        self._execution_context = {'context_id': 'abc'}
        self._execution_context_ready = True
        self._bank_file_loaded = True
        self._bank_file_dirty = True
        self._current_motion_values = {'1-1': 3.0}
        self._reset_calls = 0

    def _reset_bank_change_state_locked(self):
        self._reset_calls += 1


def _invalidate(node, snapshot_calls):
    """노드의 실제 처리기를 가짜 노드에 그대로 태운다."""
    from midi_control import midi_control_node as module

    original = module.build_snapshot
    module.build_snapshot = lambda self: snapshot_calls.append(self) or {}
    try:
        return module.MidiControlNode._cmd_invalidate_context(node, {})
    finally:
        module.build_snapshot = original


def test_the_open_banks_survive():
    node = _Node()

    _invalidate(node, [])

    assert node._banks.tag == 'bank_2'
    assert node._bank_config_file == 'banks.yaml'
    assert node._bank_file_loaded is True


def test_unsaved_edits_are_not_forgotten():
    """저장 안 한 편집이 있다는 표시까지 지우면 경고도 안 뜬다."""
    node = _Node()

    _invalidate(node, [])

    assert node._bank_file_dirty is True


def test_the_project_and_mapping_stay():
    node = _Node()

    _invalidate(node, [])

    assert node._project_id == '개선테스트-6781fd05'
    assert node._selected_mapping_file_id == '12354.yaml'
    assert node._axis_registry == 'registry'


def test_running_is_still_blocked():
    node = _Node()

    _invalidate(node, [])

    assert node._execution_context == {}
    assert node._execution_context_ready is False
    assert node._run_mapping_file_id == ''


def test_live_values_are_cleared():
    node = _Node()

    _invalidate(node, [])

    assert node._latest_motion_state == {}
    assert node._current_motion_values == {}
    assert node._reset_calls == 1


def test_the_answer_names_the_project_it_kept():
    node = _Node()

    response = _invalidate(node, [])

    assert response['project_id'] == '개선테스트-6781fd05'
    assert '유지' in response['message']
