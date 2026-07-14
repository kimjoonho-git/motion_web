import pytest

from midi_control.midi_control_node import (
    MIDI_VALUE_MAX,
    MidiControlNode,
    second_order_low_pass,
)


def test_14bit_degree_conversion_and_reverse():
    mapping = {
        'enabled': True,
        'min_deg': -90.0,
        'max_deg': 90.0,
        'reversed': False,
    }
    assert MidiControlNode._motion_degrees(0, mapping) == -90.0
    assert MidiControlNode._motion_degrees(MIDI_VALUE_MAX, mapping) == 90.0

    mapping['reversed'] = True
    assert MidiControlNode._motion_degrees(0, mapping) == 90.0
    assert MidiControlNode._motion_degrees(MIDI_VALUE_MAX, mapping) == -90.0


def test_disabled_channel_has_no_motion_value():
    mapping = {
        'enabled': False,
        'min_deg': -180.0,
        'max_deg': 180.0,
        'reversed': False,
    }
    assert MidiControlNode._motion_degrees(8192, mapping) is None


def test_mapping_rejects_equal_degree_limits():
    node = MidiControlNode.__new__(MidiControlNode)
    with pytest.raises(ValueError, match='must differ'):
        node._validated_mapping([{
            'channel': 0,
            'motion_id': '1-1',
            'min_deg': 10.0,
            'max_deg': 10.0,
        }])


def test_mapping_rejects_filter_level_outside_zero_to_one():
    node = MidiControlNode.__new__(MidiControlNode)
    with pytest.raises(ValueError, match='filter_level must be 0..1'):
        node._validated_mapping([{
            'channel': 0,
            'motion_id': '1-1',
            'min_deg': -10.0,
            'max_deg': 10.0,
            'filter_level': 1.1,
        }])


def test_second_order_filter_level_zero_is_exact_passthrough():
    output, stage1, stage2 = second_order_low_pass(
        12000.0, 0.0, 0.005, 1000.0, 500.0
    )
    assert output == 12000.0
    assert stage1 == 12000.0
    assert stage2 == 12000.0


def test_higher_second_order_filter_level_responds_more_slowly():
    weak_output, _, _ = second_order_low_pass(
        float(MIDI_VALUE_MAX), 0.2, 0.005, 0.0, 0.0
    )
    strong_output, _, _ = second_order_low_pass(
        float(MIDI_VALUE_MAX), 0.8, 0.005, 0.0, 0.0
    )
    assert 0.0 < strong_output < weak_output < MIDI_VALUE_MAX


def test_second_order_filter_converges_without_overshoot():
    stage1 = 0.0
    stage2 = 0.0
    outputs = []
    for _ in range(2000):
        output, stage1, stage2 = second_order_low_pass(
            float(MIDI_VALUE_MAX), 0.5, 0.005, stage1, stage2
        )
        outputs.append(output)

    assert outputs == sorted(outputs)
    assert outputs[-1] == pytest.approx(MIDI_VALUE_MAX, rel=1e-6)
    assert all(value <= MIDI_VALUE_MAX for value in outputs)
