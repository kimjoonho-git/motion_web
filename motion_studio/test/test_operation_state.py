import pytest

from motion_studio.operation_state import StudioOperationStateMachine


def test_operation_tokens_change_on_start_and_stop():
    machine = StudioOperationStateMachine()

    started = machine.begin('idle')
    stopped = machine.cancel()

    assert started == 1
    assert stopped == 2
    assert machine.is_active(started, 'initializing', 'initializing') is False


def test_busy_state_cannot_start_another_operation():
    machine = StudioOperationStateMachine()

    with pytest.raises(ValueError, match='녹화 또는 재생 중'):
        machine.begin('playing')


def test_active_operation_requires_matching_token_and_state():
    machine = StudioOperationStateMachine(4)

    machine.require_active(4, 'initializing', 'initializing')
    with pytest.raises(RuntimeError, match='정지'):
        machine.require_active(3, 'initializing', 'initializing')
    with pytest.raises(RuntimeError, match='정지'):
        machine.require_active(4, 'stopping', 'initializing')
