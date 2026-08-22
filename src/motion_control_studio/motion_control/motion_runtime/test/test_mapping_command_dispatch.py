"""모션축 설정 노드의 명령 디스패치 계약.

`if/elif` 사슬을 처리기 표로 바꾸면서 순서가 달라지기 쉬운 지점을 고정한다.

- 어떤 명령을 다루는지 (표에서 빠지면 '알 수 없는 명령'으로 응답한다)
- `_select_project`를 언제 부르는지 (컨텍스트를 버릴 때는 부르지 않는다)
- 응답에 요청 식별자와 세대가 되붙는지 (빠지면 요청 측이 시간 초과로 끝난다)
"""

import json

import pytest

from motion_runtime.motion_mapping_manager import MotionMappingManager


EXPECTED_COMMANDS = {
    'apply_context',
    'invalidate_context',
    'list',
    'load',
    'save',
    'validate',
    'delete',
    'load_midi_banks',
    'save_midi_banks',
}


class Recorder:
    """노드를 띄우지 않고 디스패치만 확인하기 위한 최소 대역."""

    def __init__(self) -> None:
        self.selected = []
        self.published = []
        self.logged = []


def _manager(monkeypatch) -> tuple:
    manager = MotionMappingManager.__new__(MotionMappingManager)
    recorder = Recorder()

    manager._project_generation = 1
    manager.motion_projects_dir = object()
    manager.mappings_dir = None
    manager.motion_files_dir = None
    manager._execution_context = {'stale': True}

    manager._select_project = lambda payload: recorder.selected.append(payload)
    manager._publish = lambda payload: recorder.published.append(payload)
    manager.get_logger = lambda: type(
        'L', (), {
            'warn': lambda _s, m: recorder.logged.append(('warn', m)),
            'error': lambda _s, m: recorder.logged.append(('error', m)),
        },
    )()

    # 표에 실리는 처리기를 전부 기록용으로 바꾼다
    for name in (
        '_apply_context', '_list_mappings', '_load_mapping', '_save_mapping',
        '_validate_mapping_request', '_delete_mapping', '_load_midi_banks',
        '_save_midi_banks',
    ):
        setattr(manager, name, (
            lambda label: lambda *args, **kwargs: {'success': True, 'called': label}
        )(name))

    manager._router = manager._build_router()
    return manager, recorder


def _send(manager, command, *, generation=1, request_id='req-1', payload=None):
    manager._request_callback(type('Msg', (), {'data': json.dumps({
        'request_id': request_id,
        'project_generation': generation,
        'command': command,
        'payload': {'project_generation': generation, **(payload or {})},
    })})())


# --------------------------------------------------------------------------- #
# 표 구성
# --------------------------------------------------------------------------- #

def test_router_registers_exactly_the_expected_commands(monkeypatch):
    manager, _ = _manager(monkeypatch)
    assert manager._router.commands() == EXPECTED_COMMANDS


@pytest.mark.parametrize('command', sorted(EXPECTED_COMMANDS))
def test_every_command_is_dispatched(monkeypatch, command):
    manager, recorder = _manager(monkeypatch)
    _send(manager, command)
    assert len(recorder.published) == 1
    assert recorder.published[0]['success'] is True


def test_unknown_command_reports_itself(monkeypatch):
    manager, recorder = _manager(monkeypatch)
    _send(manager, 'nonexistent')
    response = recorder.published[0]
    assert response['success'] is False
    assert 'unknown mapping command: nonexistent' in response['message']


# --------------------------------------------------------------------------- #
# 프로젝트 선택 순서
# --------------------------------------------------------------------------- #

def test_invalidate_context_does_not_select_a_project(monkeypatch):
    """컨텍스트를 버리는 중이라 고를 프로젝트가 없다."""
    manager, recorder = _manager(monkeypatch)
    _send(manager, 'invalidate_context')
    assert recorder.selected == []
    assert manager._execution_context == {}
    assert manager.mappings_dir is manager.motion_projects_dir


@pytest.mark.parametrize('command', sorted(EXPECTED_COMMANDS - {'invalidate_context'}))
def test_other_commands_select_a_project_first(monkeypatch, command):
    manager, recorder = _manager(monkeypatch)
    _send(manager, command)
    assert len(recorder.selected) == 1


def test_unknown_command_still_selects_a_project(monkeypatch):
    """사슬이던 시절의 순서를 유지한다 · 프로젝트 오류가 먼저 드러난다."""
    manager, recorder = _manager(monkeypatch)
    _send(manager, 'nonexistent')
    assert len(recorder.selected) == 1


# --------------------------------------------------------------------------- #
# 봉투
# --------------------------------------------------------------------------- #

def test_response_carries_request_identity(monkeypatch):
    manager, recorder = _manager(monkeypatch)
    _send(manager, 'list', request_id='req-42', generation=1)
    response = recorder.published[0]
    assert response['request_id'] == 'req-42'
    assert response['project_generation'] == 1


def test_stale_generation_is_rejected_without_dispatching(monkeypatch):
    manager, recorder = _manager(monkeypatch)
    _send(manager, 'list', generation=0)
    assert recorder.selected == []
    assert recorder.published[0]['success'] is False


def test_handler_failure_becomes_a_response_not_a_crash(monkeypatch):
    """한 요청이 실패해도 노드는 계속 살아 있어야 한다."""
    manager, recorder = _manager(monkeypatch)

    def explode(payload):
        raise RuntimeError('디스크 없음')

    manager._save_mapping = explode
    manager._router = manager._build_router()

    _send(manager, 'save')
    response = recorder.published[0]
    assert response['success'] is False
    assert '디스크 없음' in response['message']
    assert response['request_id'] == 'req-1'


def test_invalid_json_publishes_a_shape_error(monkeypatch):
    manager, recorder = _manager(monkeypatch)
    manager._publish_response = lambda rid, ok, msg: recorder.published.append(
        {'request_id': rid, 'success': ok, 'message': msg}
    )
    manager._request_callback(type('Msg', (), {'data': 'not json'})())
    assert recorder.published[0]['success'] is False
    assert recorder.logged[0][0] == 'warn'
