import threading
import time
from types import SimpleNamespace

import pytest

from midi_control.bank_manager import MIDI_CHANNEL_COUNT, MidiBankManager
from midi_control.midi_control_node import (
    MIDI_VALUE_MAX,
    MidiControlNode,
    second_order_low_pass,
)


def test_14bit_filtered_output_range_and_reverse():
    mapping = {
        'enabled': True,
        'min_14bit': 1000,
        'max_14bit': 15000,
        'reversed': False,
    }
    assert MidiControlNode._filtered_output_14bit(0, mapping) == 1000
    assert MidiControlNode._filtered_output_14bit(MIDI_VALUE_MAX, mapping) == 15000

    mapping['reversed'] = True
    assert MidiControlNode._filtered_output_14bit(0, mapping) == 15000
    assert MidiControlNode._filtered_output_14bit(MIDI_VALUE_MAX, mapping) == 1000


def test_mapping_rejects_invalid_14bit_limits():
    node = MidiControlNode.__new__(MidiControlNode)
    with pytest.raises(ValueError, match='less than'):
        node._validated_mapping([{
            'channel': 0,
            'motion_id': '1-1',
            'min_14bit': 10000,
            'max_14bit': 10000,
        }])

    with pytest.raises(ValueError, match='integer 0..16383'):
        node._validated_mapping([{
            'channel': 0,
            'motion_id': '1-1',
            'min_14bit': -1,
            'max_14bit': MIDI_VALUE_MAX,
        }])


@pytest.mark.parametrize('filter_level', [-1, 14, 1.5])
def test_mapping_rejects_filter_level_outside_integer_zero_to_thirteen(filter_level):
    node = MidiControlNode.__new__(MidiControlNode)
    with pytest.raises(ValueError, match='integer 0..13'):
        node._validated_mapping([{
            'channel': 0,
            'motion_id': '1-1',
            'min_14bit': 0,
            'max_14bit': MIDI_VALUE_MAX,
            'filter_level': filter_level,
        }])


def test_second_order_filter_level_zero_is_exact_passthrough():
    output, stage1, stage2 = second_order_low_pass(
        12000.0, 0, 0.005, 1000.0, 500.0
    )
    assert output == 12000.0
    assert stage1 == 12000.0
    assert stage2 == 12000.0


def test_higher_second_order_filter_level_responds_more_slowly():
    weak_output, _, _ = second_order_low_pass(
        float(MIDI_VALUE_MAX), 3, 0.005, 0.0, 0.0
    )
    strong_output, _, _ = second_order_low_pass(
        float(MIDI_VALUE_MAX), 10, 0.005, 0.0, 0.0
    )
    assert 0.0 < strong_output < weak_output < MIDI_VALUE_MAX


def test_second_order_filter_converges_without_overshoot():
    stage1 = 0.0
    stage2 = 0.0
    outputs = []
    for _ in range(2000):
        output, stage1, stage2 = second_order_low_pass(
            float(MIDI_VALUE_MAX), 7, 0.005, stage1, stage2
        )
        outputs.append(output)

    assert outputs == sorted(outputs)
    assert outputs[-1] == pytest.approx(MIDI_VALUE_MAX, rel=1e-6)
    assert all(value <= MIDI_VALUE_MAX for value in outputs)


def test_filter_keeps_converging_after_touch_release_without_accepting_untouched_raw():
    node = MidiControlNode.__new__(MidiControlNode)
    node._lock = threading.Lock()
    node._banks = MidiBankManager()
    mappings = node._banks.active_bank()['mappings']
    mappings[0]['filter_level'] = 13
    node._banks.update_bank('bank_1', mappings=mappings)
    node._raw_channels = [0] * MIDI_CHANNEL_COUNT
    node._channels = [0.0] * MIDI_CHANNEL_COUNT
    node._filter_stage1 = [0.0] * MIDI_CHANNEL_COUNT
    node._filter_stage2 = [0.0] * MIDI_CHANNEL_COUNT
    node._filter_last_at = [time.monotonic() - 0.005] * MIDI_CHANNEL_COUNT
    node._touch = [False] * MIDI_CHANNEL_COUNT
    node._dial = [0] * MIDI_CHANNEL_COUNT
    node._btn0 = [False] * MIDI_CHANNEL_COUNT
    node._btn1 = [False] * MIDI_CHANNEL_COUNT
    node._btn2 = [False] * MIDI_CHANNEL_COUNT
    node._btn3 = [False] * MIDI_CHANNEL_COUNT
    node._confirmed = [False] * MIDI_CHANNEL_COUNT

    touched = SimpleNamespace(
        channel=[MIDI_VALUE_MAX] + [0] * (MIDI_CHANNEL_COUNT - 1),
        touch=[True] + [False] * (MIDI_CHANNEL_COUNT - 1),
        dial=[0] * MIDI_CHANNEL_COUNT,
        btn0=[False] * MIDI_CHANNEL_COUNT,
        btn1=[False] * MIDI_CHANNEL_COUNT,
        btn2=[False] * MIDI_CHANNEL_COUNT,
        btn3=[False] * MIDI_CHANNEL_COUNT,
    )
    node._midi_callback(touched)
    value_while_touched = node._channels[0]

    node._filter_last_at[0] = time.monotonic() - 0.005
    released_with_device_zero = SimpleNamespace(
        channel=[0] * MIDI_CHANNEL_COUNT,
        touch=[False] * MIDI_CHANNEL_COUNT,
        dial=[0] * MIDI_CHANNEL_COUNT,
        btn0=[False] * MIDI_CHANNEL_COUNT,
        btn1=[False] * MIDI_CHANNEL_COUNT,
        btn2=[False] * MIDI_CHANNEL_COUNT,
        btn3=[False] * MIDI_CHANNEL_COUNT,
    )
    node._midi_callback(released_with_device_zero)

    assert node._raw_channels[0] == MIDI_VALUE_MAX
    assert value_while_touched < node._channels[0] < MIDI_VALUE_MAX
