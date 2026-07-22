import threading
import time

import pytest

from motion_runtime.motion_run_manager import (
    SAFETY_STATUS_TIMEOUT_SEC,
    MotionRunManager,
)


def run_manager_with_safety_status(status=None, age_sec=0.0):
    manager = MotionRunManager.__new__(MotionRunManager)
    manager._safety_status_lock = threading.Lock()
    manager._latest_safety_status = status
    manager._latest_safety_status_at = (
        None if status is None else time.monotonic() - float(age_sec)
    )
    return manager


@pytest.mark.parametrize('owner', ['none', 'playback'])
def test_runtime_allows_idle_or_existing_playback_owner(owner):
    manager = run_manager_with_safety_status({
        'command_owner': owner,
        'commands_blocked': False,
        'emergency_latched': False,
    })

    assert manager._playback_ownership_error() == ''


@pytest.mark.parametrize(
    ('owner', 'expected'),
    [('midi', 'MIDI 제어'), ('manual', '수동 제어')],
)
def test_runtime_rejects_incompatible_command_owner(owner, expected):
    manager = run_manager_with_safety_status({
        'command_owner': owner,
        'commands_blocked': False,
        'emergency_latched': False,
    })

    error = manager._playback_ownership_error()

    assert expected in error
    assert '모션을 시작할 수 없습니다' in error


def test_runtime_rejects_emergency_or_temporarily_blocked_commands():
    emergency = run_manager_with_safety_status({
        'command_owner': 'none',
        'commands_blocked': True,
        'emergency_latched': True,
    })
    settling = run_manager_with_safety_status({
        'command_owner': 'none',
        'commands_blocked': True,
        'emergency_latched': False,
        'message': '전체 동작 정지 처리 중',
    })

    assert '긴급정지 잠김' in emergency._playback_ownership_error()
    assert settling._playback_ownership_error() == '전체 동작 정지 처리 중'


def test_runtime_rejects_missing_or_stale_supervisor_status():
    missing = run_manager_with_safety_status()
    stale = run_manager_with_safety_status(
        {'command_owner': 'none'},
        age_sec=SAFETY_STATUS_TIMEOUT_SEC + 0.1,
    )

    assert '아직 받지 못했습니다' in missing._playback_ownership_error()
    assert '갱신되지 않았습니다' in stale._playback_ownership_error()


def test_runtime_stream_guard_raises_before_publishing_for_midi_owner():
    manager = run_manager_with_safety_status({
        'command_owner': 'midi',
        'commands_blocked': False,
        'emergency_latched': False,
    })

    with pytest.raises(RuntimeError, match='MIDI 제어'):
        manager._require_playback_command_allowed()
