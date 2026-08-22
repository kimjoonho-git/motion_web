"""명령 요청 봉투 검증.

네 노드가 각자 펼치던 봉투를 흡수한 결과다. 봉투가 어긋나면 요청한 쪽이 자기
응답을 알아보지 못하고 시간 초과로 끝나므로, 형식 보존이 핵심이다.
"""

import json

import pytest

from motion_common import command_router


# --------------------------------------------------------------------------- #
# parse_request
# --------------------------------------------------------------------------- #

def test_parses_a_full_request():
    data = json.dumps({
        'request_id': 'req-1',
        'project_generation': 7,
        'command': 'load',
        'payload': {'file_id': 'a.yaml'},
    })
    request = command_router.parse_request(data)
    assert request.request_id == 'req-1'
    assert request.command == 'load'
    assert request.payload == {'file_id': 'a.yaml'}
    assert request.generation == 7


def test_returns_none_for_non_json():
    assert command_router.parse_request('not json') is None
    assert command_router.parse_request('') is None
    assert command_router.parse_request(None) is None


def test_returns_none_for_non_object_json():
    assert command_router.parse_request('[1, 2]') is None
    assert command_router.parse_request('"text"') is None
    assert command_router.parse_request('5') is None


def test_missing_fields_become_empty_defaults():
    request = command_router.parse_request('{}')
    assert request.request_id == ''
    assert request.command == ''
    assert request.payload == {}
    assert request.generation is None


def test_default_command_applies_only_when_absent():
    assert command_router.parse_request('{}', default_command='status').command == 'status'
    data = json.dumps({'command': 'load'})
    assert command_router.parse_request(data, default_command='status').command == 'load'


def test_command_is_stripped():
    data = json.dumps({'command': '  load  '})
    assert command_router.parse_request(data).command == 'load'


def test_non_dict_payload_is_replaced_with_empty_dict():
    for bad in ([1, 2], 'text', 5, None):
        data = json.dumps({'command': 'x', 'payload': bad})
        assert command_router.parse_request(data).payload == {}


def test_payload_is_copied_not_aliased():
    """처리기가 payload를 고쳐도 원본 요청은 그대로여야 한다."""
    data = json.dumps({'command': 'x', 'payload': {'a': 1}})
    request = command_router.parse_request(data)
    request.payload['a'] = 2
    assert request.raw['payload'] == {'a': 1}


def test_raw_keeps_unknown_fields():
    data = json.dumps({'command': 'x', 'extra': 'kept'})
    assert command_router.parse_request(data).raw['extra'] == 'kept'


# --------------------------------------------------------------------------- #
# finalize
# --------------------------------------------------------------------------- #

def test_finalize_attaches_identity():
    request = command_router.parse_request(json.dumps({
        'request_id': 'req-1', 'project_generation': 3, 'command': 'x',
    }))
    result = command_router.finalize({'success': True}, request)
    assert result == {'success': True, 'request_id': 'req-1', 'project_generation': 3}


def test_finalize_overwrites_any_identity_the_handler_set():
    """처리기가 잘못된 식별자를 넣어도 봉투가 바로잡는다."""
    request = command_router.parse_request(json.dumps({
        'request_id': 'correct', 'project_generation': 3, 'command': 'x',
    }))
    result = command_router.finalize(
        {'success': True, 'request_id': 'wrong', 'project_generation': 99}, request
    )
    assert result['request_id'] == 'correct'
    assert result['project_generation'] == 3


def test_finalize_rescues_a_handler_that_returned_no_dict():
    request = command_router.parse_request(json.dumps({'request_id': 'r', 'command': 'x'}))
    for bad in (None, 'text', 5, [1]):
        result = command_router.finalize(bad, request)
        assert result['success'] is False
        assert result['request_id'] == 'r'


# --------------------------------------------------------------------------- #
# error_response
# --------------------------------------------------------------------------- #

def test_error_response_shape():
    assert command_router.error_response('실패') == {'success': False, 'message': '실패'}


def test_error_response_accepts_exception_and_extras():
    result = command_router.error_response(ValueError('원인'), status={'x': 1})
    assert result['success'] is False
    assert result['message'] == '원인'
    assert result['status'] == {'x': 1}


# --------------------------------------------------------------------------- #
# CommandRouter
# --------------------------------------------------------------------------- #

def test_register_and_resolve():
    router = command_router.CommandRouter()
    router.register('load', lambda payload: {'ok': True})
    assert router.handles('load')
    assert router.resolve('load') is not None
    assert router.resolve('missing') is None


def test_decorator_registers_multiple_commands():
    router = command_router.CommandRouter()

    @router.handler('record', 'play')
    def _handle(payload):
        return {'ok': True}

    assert router.commands() == {'record', 'play'}
    assert router.resolve('record') is router.resolve('play')


def test_duplicate_registration_is_rejected():
    """사슬을 표로 바꿀 때 같은 명령을 두 번 적는 실수를 잡는다."""
    router = command_router.CommandRouter()
    router.register('load', lambda p: None)
    with pytest.raises(ValueError, match='중복'):
        router.register('load', lambda p: None)


def test_context_commands_default_and_override():
    router = command_router.CommandRouter()
    assert router.advances_context('apply_context')
    assert not router.advances_context('select_project')

    midi = command_router.CommandRouter(
        context_commands={'select_project', 'invalidate_context'}
    )
    assert midi.advances_context('select_project')
    assert not midi.advances_context('apply_context')


def test_container_protocol():
    router = command_router.CommandRouter()
    router.register('a', lambda p: None)
    assert 'a' in router
    assert 'b' not in router
    assert len(router) == 1
