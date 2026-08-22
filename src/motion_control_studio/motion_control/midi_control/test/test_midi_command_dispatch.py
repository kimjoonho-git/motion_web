"""MIDI 노드의 명령 디스패치 계약.

283줄짜리 `if/elif` 사슬을 처리기 표로 옮기면서, 명령 하나가 조용히 빠지거나
봉투가 어긋나는 것을 막는다. 각 처리기의 내부 동작은 기존 노드 테스트가 본다.
"""

import json

import pytest

from midi_control.midi_control_node import MidiControlNode


EXPECTED_COMMANDS = {
    'select_project',
    'confirm_context',
    'invalidate_context',
    'save_mapping',
    'update_bank',
    'create_bank',
    'select_bank',
    'delete_bank',
    'save_banks_to_file',
    'apply_banks',
    'load_banks_from_file',
    'reset_runtime_values',
    'resync_selected_faders',
    'studio_recording_prepare',
    'studio_recording_zero_status',
    'studio_recording_ready',
    'connect_device',
    'disconnect_device',
    'status',
}

#: 같은 처리기를 공유하는 명령 쌍
ALIASES = [
    ('save_mapping', 'update_bank'),
    ('apply_banks', 'load_banks_from_file'),
]


def _node() -> MidiControlNode:
    return MidiControlNode.__new__(MidiControlNode)


def _router(node=None):
    return (node or _node())._command_router()


# --------------------------------------------------------------------------- #
# 표 구성
# --------------------------------------------------------------------------- #

def test_router_registers_exactly_the_expected_commands():
    assert _router().commands() == EXPECTED_COMMANDS


def _underlying(handler):
    """바인드 메서드는 접근할 때마다 새 객체이므로 실제 함수로 비교한다."""
    return getattr(handler, '__func__', handler)


@pytest.mark.parametrize('first,second', ALIASES)
def test_aliased_commands_share_one_handler(first, second):
    router = _router()
    assert _underlying(router.resolve(first)) is _underlying(router.resolve(second))


def test_connect_and_disconnect_pass_their_own_command_name():
    """같은 메서드를 쓰지만 연결과 해제를 구분해 전달해야 한다."""
    node = _node()
    seen = []
    node._cmd_connect_device = lambda payload, command: seen.append(command) or {}
    node._router = node._build_router()

    node._router.resolve('connect_device')({})
    node._router.resolve('disconnect_device')({})
    assert seen == ['connect_device', 'disconnect_device']


def test_context_commands_match_this_node():
    """MIDI 노드는 apply_context가 아니라 select_project로 세대를 올린다."""
    router = _router()
    assert router.advances_context('select_project')
    assert router.advances_context('invalidate_context')
    assert not router.advances_context('apply_context')


def test_router_is_built_lazily_without_init():
    """__init__을 거치지 않은 노드에서도 디스패치가 동작해야 한다."""
    node = _node()
    assert not hasattr(node, '_router')
    assert len(node._command_router()) == len(EXPECTED_COMMANDS)
    # 두 번째 호출은 같은 표를 재사용한다
    assert node._command_router() is node._router


# --------------------------------------------------------------------------- #
# 봉투
# --------------------------------------------------------------------------- #

def _send(node, command, *, generation=1, request_id='req-1', payload=None):
    published = []
    node._publish_json = lambda _pub, response: published.append(response)
    node._response_publisher = None
    node._request_callback(type('Msg', (), {'data': json.dumps({
        'request_id': request_id,
        'project_generation': generation,
        'command': command,
        'payload': {'project_generation': generation, **(payload or {})},
    })})())
    return published


def test_unknown_command_reports_itself():
    node = _node()
    node._project_generation = 1
    published = _send(node, 'nonexistent')
    assert published[0]['success'] is False
    assert 'unsupported command: nonexistent' in published[0]['message']


def test_response_carries_request_identity():
    node = _node()
    node._project_generation = 1
    published = _send(node, 'nonexistent', request_id='req-42', generation=1)
    assert published[0]['request_id'] == 'req-42'
    assert published[0]['project_generation'] == 1


def test_stale_generation_is_rejected_before_dispatch():
    node = _node()
    node._project_generation = 5
    published = _send(node, 'status', generation=1)
    assert published[0]['success'] is False


def test_invalid_json_is_ignored_without_publishing():
    node = _node()
    published = []
    node._publish_json = lambda _pub, response: published.append(response)
    node._request_callback(type('Msg', (), {'data': 'not json'})())
    assert published == []


def test_handler_value_error_becomes_a_response():
    """한 명령이 실패해도 노드는 계속 살아 있어야 한다."""
    node = _node()
    node._project_generation = 1

    def explode(payload):
        raise ValueError('장치 없음')

    node._router = node._build_router()
    node._router._handlers['status'] = explode

    published = _send(node, 'status')
    assert published[0]['success'] is False
    assert published[0]['message'] == '장치 없음'
    assert published[0]['request_id'] == 'req-1'
