import threading
import time

import pytest

from motion_runtime.motion_player import MotionPlayer
from motion_runtime.motion_run_constants import (
    SAFETY_STATUS_TIMEOUT_SEC,
)
from motion_runtime.motion_run_manager import (
    MotionRunManager,
)


def run_manager_with_safety_status(status=None, age_sec=0.0):
    manager = MotionRunManager.__new__(MotionRunManager)
    manager._player = MotionPlayer(manager)
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
        manager._player._require_playback_command_allowed()


def test_playback_keeps_running_when_midi_owns_a_different_axis():
    """추가 녹화의 바탕 · 내 축이 비어 있으면 계속한다 · §6-106

    `command_owner` 는 **대표 하나로 줄인 축약형**이다 · MIDI 가 축 하나만
    잡아도 대표가 `midi` 로 바뀐다. 재생이 매 프레임 그것을 보고 있어서,
    다른 축을 몰던 재생이 **스스로 멈췄다** · 레이어에 있는 축의 재생이 끊겨
    그 위에 얹어 녹화하는 것이 불가능했다.
    """
    manager = run_manager_with_safety_status({
        'command_owner': 'midi',                       # 축약형은 midi
        'command_axis_owners': {'0': 'midi', '1': 'playback'},
        'commands_blocked': False,
        'emergency_latched': False,
    })

    assert manager._playback_ownership_error(axes=[1]) == '', '내 축은 비어 있다'
    assert '축 0' in manager._playback_ownership_error(axes=[0]), '남이 쥔 축은 막는다'


def test_a_blanket_owner_still_blocks_every_axis():
    """축을 지정하지 않고 전체를 쥔 주인은 어느 축이든 막는다."""
    manager = run_manager_with_safety_status({
        'command_owner': 'manual',
        'command_axis_owners': {'all': 'manual'},
        'commands_blocked': False,
        'emergency_latched': False,
    })

    assert '수동 제어' in manager._playback_ownership_error(axes=[1])


def test_without_the_axis_table_the_old_summary_still_decides():
    """표를 못 받은 상대(옛 supervisor)와도 돈다 · 지금까지대로 축약형을 본다."""
    manager = run_manager_with_safety_status({
        'command_owner': 'midi',
        'commands_blocked': False,
        'emergency_latched': False,
    })

    assert 'MIDI 제어' in manager._playback_ownership_error(axes=[1])
