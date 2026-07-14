import pytest

from motion_web_bridge.midi_monitor_node import MIDI_VALUE_MAX, MidiMonitorNode


def test_14bit_degree_conversion_and_reverse():
    mapping = {
        'enabled': True,
        'min_deg': -90.0,
        'max_deg': 90.0,
        'reversed': False,
    }
    assert MidiMonitorNode._motion_degrees(0, mapping) == -90.0
    assert MidiMonitorNode._motion_degrees(MIDI_VALUE_MAX, mapping) == 90.0

    mapping['reversed'] = True
    assert MidiMonitorNode._motion_degrees(0, mapping) == 90.0
    assert MidiMonitorNode._motion_degrees(MIDI_VALUE_MAX, mapping) == -90.0


def test_disabled_channel_has_no_motion_value():
    mapping = {
        'enabled': False,
        'min_deg': -180.0,
        'max_deg': 180.0,
        'reversed': False,
    }
    assert MidiMonitorNode._motion_degrees(8192, mapping) is None


def test_mapping_rejects_equal_degree_limits():
    node = MidiMonitorNode.__new__(MidiMonitorNode)
    with pytest.raises(ValueError, match='must differ'):
        node._validated_mapping([{
            'channel': 0,
            'motion_id': '1',
            'min_deg': 10.0,
            'max_deg': 10.0,
        }])
