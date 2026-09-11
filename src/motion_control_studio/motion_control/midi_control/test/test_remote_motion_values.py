"""연동된 PC 로 보내는 실시간 모션값 · §6-93

모터 명령이 아니라 **모션값(각도)** 을 보낸다 · 받는 PC 가 자기 매핑과 한계값으로
자기 모터축에 옮긴다 · 그래야 PC 마다 기구가 달라도 된다.

여기서는 **무엇을 보내느냐** 를 본다 · 잘못 보내면 남의 모터가 움직인다.
"""

from types import SimpleNamespace

from midi_control.bank_manager import MIDI_CHANNEL_COUNT
from midi_control.midi_control_node import MidiControlNode


def _node(*, enabled=(), pending=(), approved=None):
    node = MidiControlNode.__new__(MidiControlNode)
    node._control_enabled = [i in enabled for i in range(MIDI_CHANNEL_COUNT)]
    node._pickup = SimpleNamespace(
        pending=[i in pending for i in range(MIDI_CHANNEL_COUNT)],
    )
    node._approved_motion_values = [
        dict((approved or {}).get(i, {})) for i in range(MIDI_CHANNEL_COUNT)
    ]
    node._approved_motor_targets = [{} for _ in range(MIDI_CHANNEL_COUNT)]
    node._approved_command_stamp = [0.0] * MIDI_CHANNEL_COUNT
    return node


def test_it_sends_what_midi_is_actually_driving():
    node = _node(enabled={0}, approved={0: {'1-1': 12.5}})
    assert node._live_motion_values_locked() == {'1-1': 12.5}


def test_a_line_without_select_sends_nothing():
    """SELECT 가 꺼진 라인은 모터를 안 몬다 · 그 값을 보내면 남의 PC 만 움직인다."""
    node = _node(enabled=set(), approved={0: {'1-1': 12.5}})
    assert node._live_motion_values_locked() == {}


def test_a_line_waiting_for_its_fader_sends_nothing():
    """Pickup 대기 중인 값은 물리 페이더가 아직 모터를 못 따라잡은 값이다 ·
    그대로 보내면 **받는 PC 의 모터가 튄다**."""
    node = _node(enabled={0}, pending={0}, approved={0: {'1-1': 12.5}})
    assert node._live_motion_values_locked() == {}


def test_several_lines_are_merged():
    node = _node(
        enabled={0, 2},
        approved={0: {'1-1': 1.0}, 2: {'1-2': 2.0, '1-3': 3.0}},
    )
    assert node._live_motion_values_locked() == {'1-1': 1.0, '1-2': 2.0, '1-3': 3.0}


def test_only_the_ready_lines_of_a_mixed_set_are_sent():
    node = _node(
        enabled={0, 1, 2},
        pending={1},
        approved={0: {'1-1': 1.0}, 1: {'1-2': 2.0}, 2: {'1-3': 3.0}},
    )
    assert node._live_motion_values_locked() == {'1-1': 1.0, '1-3': 3.0}


def test_a_broken_value_is_dropped_not_sent_as_garbage():
    node = _node(
        enabled={0},
        approved={0: {'1-1': float('nan'), '1-2': None, '1-3': 4.0}},
    )
    assert node._live_motion_values_locked() == {'1-3': 4.0}


def test_a_bare_node_sends_nothing_instead_of_failing():
    """노드가 다 서기 전에도 타이머가 돌 수 있다."""
    node = MidiControlNode.__new__(MidiControlNode)
    assert node._live_motion_values_locked() == {}
