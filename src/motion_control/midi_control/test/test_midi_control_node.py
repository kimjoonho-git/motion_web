import json
import threading
import time
from types import SimpleNamespace

import pytest

from midi_control.bank_manager import MIDI_CHANNEL_COUNT, MidiBankManager
from midi_control.midi_control_node import (
    MIDI_VALUE_MAX,
    MidiControlNode,
    motion_value_from_motor,
    motion_value_from_output,
    motor_target_from_motion,
    raw_fader_for_motion,
    second_order_low_pass,
)


class CapturePublisher:
    def __init__(self):
        self.messages = []

    def publish(self, message):
        self.messages.append(message)


def add_motor_control_state(node):
    node._physical_touch = [False] * MIDI_CHANNEL_COUNT
    node._fader_moving = [False] * MIDI_CHANNEL_COUNT
    node._bridge_fader_syncing = [False] * MIDI_CHANNEL_COUNT
    node._fader_sync_targets = [None] * MIDI_CHANNEL_COUNT
    node._awaiting_fader_sync = [False] * MIDI_CHANNEL_COUNT
    node._fader_sync_not_before = [0.0] * MIDI_CHANNEL_COUNT
    node._last_select_toggle_at = [0.0] * MIDI_CHANNEL_COUNT
    node._last_motor_command_at = [0.0] * MIDI_CHANNEL_COUNT
    node._last_motor_target = [None] * MIDI_CHANNEL_COUNT
    node._pending_motor_requests = {}
    node._motor_follow_active = [False] * MIDI_CHANNEL_COUNT
    node._motor_command_state = ['inactive'] * MIDI_CHANNEL_COUNT
    node._motor_command_message = [''] * MIDI_CHANNEL_COUNT
    node._request_sequence = 0
    node._latest_motion_state = {'motors': [{'controller_index': 2, 'position_deg': 10.0}]}
    node._motor_request_publisher = CapturePublisher()


def test_input_state_keeps_physical_touch_movement_and_sync_separate():
    node = MidiControlNode.__new__(MidiControlNode)
    node._lock = threading.Lock()
    node._physical_touch = [False] * MIDI_CHANNEL_COUNT
    node._fader_moving = [False] * MIDI_CHANNEL_COUNT
    node._bridge_fader_syncing = [False] * MIDI_CHANNEL_COUNT

    node._input_state_callback(SimpleNamespace(data=(
        '{"physical_touch":[true,false],'
        '"fader_moving":[false,true],'
        '"fader_syncing":[false,true]}'
    )))

    assert node._physical_touch[:2] == [True, False]
    assert node._fader_moving[:2] == [False, True]
    assert node._bridge_fader_syncing[:2] == [False, True]


def test_pending_motor_targets_are_published_as_one_batch():
    node = MidiControlNode.__new__(MidiControlNode)
    node._lock = threading.Lock()
    node._pending_motor_requests = {
        1: {'channel': 1, 'axis': 1, 'target_deg': 10.0},
        3: {'channel': 3, 'axis': 3, 'target_deg': -20.0},
    }
    node._last_motor_command_at = [0.0] * MIDI_CHANNEL_COUNT
    node._motor_command_message = [''] * MIDI_CHANNEL_COUNT
    node._request_sequence = 7
    node._motor_request_publisher = CapturePublisher()

    node._publish_motor_request_batch()

    assert node._pending_motor_requests == {}
    assert len(node._motor_request_publisher.messages) == 1
    payload = json.loads(node._motor_request_publisher.messages[0].data)
    assert payload['request_id'].startswith('midi-batch-')
    assert [target['axis'] for target in payload['targets']] == [1, 3]


def test_percent_output_range_and_reverse():
    mapping = {
        'enabled': True,
        'min_percent': 50.0,
        'max_percent': 100.0,
        'reversed': False,
    }
    assert MidiControlNode._filtered_output_14bit(0, mapping) == pytest.approx(MIDI_VALUE_MAX / 2)
    assert MidiControlNode._filtered_output_14bit(MIDI_VALUE_MAX, mapping) == MIDI_VALUE_MAX

    mapping['reversed'] = True
    assert MidiControlNode._filtered_output_14bit(0, mapping) == MIDI_VALUE_MAX
    assert MidiControlNode._filtered_output_14bit(
        MIDI_VALUE_MAX, mapping
    ) == pytest.approx(MIDI_VALUE_MAX / 2)


def test_motion_and_motor_angle_conversion_uses_runtime_mapping_equation():
    row = {
        'motion_lower_deg': -20,
        'motion_upper_deg': 20,
        'reference_enabled': True,
        'reference_position_deg': 10,
        'offset_deg': 2,
        'scale': 1.5,
        'gear_ratio': 100,
        'invert': True,
    }
    assert motion_value_from_output(0, row) == -20
    assert motion_value_from_output(MIDI_VALUE_MAX, row) == 20
    target = motor_target_from_motion(4, row)
    assert target == pytest.approx(-890)
    assert motion_value_from_motor(target, row) == pytest.approx(4)


def test_select_fader_inverse_includes_bank_min_max_and_reverse():
    row = {'motion_lower_deg': -20, 'motion_upper_deg': 20}
    mapping = {'min_percent': 50, 'max_percent': 100, 'reversed': False}
    assert raw_fader_for_motion(0, row, mapping) == 0
    assert raw_fader_for_motion(20, row, mapping) == MIDI_VALUE_MAX
    mapping['reversed'] = True
    assert raw_fader_for_motion(0, row, mapping) == MIDI_VALUE_MAX


def test_select_fader_inverse_rejects_position_outside_line_range():
    row = {'motion_lower_deg': -20, 'motion_upper_deg': 20}
    mapping = {'min_percent': 50, 'max_percent': 100, 'reversed': False}

    with pytest.raises(ValueError, match='활성화 불가.*제어 범위'):
        raw_fader_for_motion(-10, row, mapping)


def test_zero_to_two_hundred_percent_reaches_full_output_at_half_fader():
    mapping = {
        'enabled': True,
        'min_percent': 0.0,
        'max_percent': 200.0,
        'reversed': False,
    }

    assert MidiControlNode._filtered_output_14bit(MIDI_VALUE_MAX / 2, mapping) == MIDI_VALUE_MAX
    assert MidiControlNode._filtered_output_14bit(MIDI_VALUE_MAX, mapping) == MIDI_VALUE_MAX


def test_mapping_validates_percent_limits_and_forces_min_zero_above_one_hundred():
    node = MidiControlNode.__new__(MidiControlNode)
    with pytest.raises(ValueError, match='less than'):
        node._validated_mapping([{
            'channel': 0,
            'motion_id': '1-1',
            'min_percent': 50,
            'max_percent': 50,
        }])

    with pytest.raises(ValueError, match='<= 200'):
        node._validated_mapping([{
            'channel': 0,
            'motion_id': '1-1',
            'min_percent': 0,
            'max_percent': 201,
        }])

    mappings = node._validated_mapping([{
        'channel': 0,
        'motion_id': '1-1',
        'min_percent': 75,
        'max_percent': 200,
    }])
    assert mappings[0]['min_percent'] == 0
    assert mappings[0]['max_percent'] == 200


@pytest.mark.parametrize('filter_level', [-1, 14, 1.5])
def test_mapping_rejects_filter_level_outside_integer_zero_to_thirteen(filter_level):
    node = MidiControlNode.__new__(MidiControlNode)
    with pytest.raises(ValueError, match='integer 0..13'):
        node._validated_mapping([{
            'channel': 0,
            'motion_id': '1-1',
            'min_percent': 0,
            'max_percent': 100,
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
    node._previous_btn0 = [False] * MIDI_CHANNEL_COUNT
    node._previous_btn3 = [False] * MIDI_CHANNEL_COUNT
    node._previous_dial = [0] * MIDI_CHANNEL_COUNT
    node._confirmed = [False] * MIDI_CHANNEL_COUNT
    node._control_enabled = [False] * MIDI_CHANNEL_COUNT
    node._final_output_values = [0.0] * MIDI_CHANNEL_COUNT
    node._pending_fader_positions = [None] * MIDI_CHANNEL_COUNT
    node._motor_angle_mode = [False] * MIDI_CHANNEL_COUNT
    node._bank_file_dirty = False
    add_motor_control_state(node)
    node._axis_registry = SimpleNamespace(
        motor_axis=lambda motion_id: 0,
        mapping=lambda motion_id: {
            'motor_axis': 0,
            'motion_lower_deg': -20,
            'motion_upper_deg': 20,
        },
    )

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


def test_select_requires_matching_motion_axis_and_dial_updates_filter():
    node = MidiControlNode.__new__(MidiControlNode)
    node._lock = threading.Lock()
    node._banks = MidiBankManager()
    node._raw_channels = [0] * MIDI_CHANNEL_COUNT
    node._channels = [0.0] * MIDI_CHANNEL_COUNT
    node._filter_stage1 = [0.0] * MIDI_CHANNEL_COUNT
    node._filter_stage2 = [0.0] * MIDI_CHANNEL_COUNT
    node._filter_last_at = [None] * MIDI_CHANNEL_COUNT
    node._touch = [False] * MIDI_CHANNEL_COUNT
    node._dial = [0] * MIDI_CHANNEL_COUNT
    node._btn0 = [False] * MIDI_CHANNEL_COUNT
    node._btn1 = [False] * MIDI_CHANNEL_COUNT
    node._btn2 = [False] * MIDI_CHANNEL_COUNT
    node._btn3 = [False] * MIDI_CHANNEL_COUNT
    node._previous_btn0 = [False] * MIDI_CHANNEL_COUNT
    node._previous_btn3 = [False] * MIDI_CHANNEL_COUNT
    node._previous_dial = [0] * MIDI_CHANNEL_COUNT
    node._confirmed = [False] * MIDI_CHANNEL_COUNT
    node._control_enabled = [False] * MIDI_CHANNEL_COUNT
    node._final_output_values = [0.0] * MIDI_CHANNEL_COUNT
    node._pending_fader_positions = [None] * MIDI_CHANNEL_COUNT
    node._motor_angle_mode = [False] * MIDI_CHANNEL_COUNT
    node._bank_file_dirty = False
    add_motor_control_state(node)
    matched = {'1-1': 2}
    row = {
        'motor_axis': 2,
        'motion_lower_deg': -20,
        'motion_upper_deg': 20,
        'reference_position_deg': 10,
        'gear_ratio': 1,
        'scale': 1,
    }
    node._axis_registry = SimpleNamespace(
        motor_axis=lambda motion_id: matched.get(motion_id),
        mapping=lambda motion_id: row if motion_id in matched else None,
        file_id='selected.yaml',
    )
    node._raw_channels[0] = 4321

    def message(*, select=False, dial=0):
        return SimpleNamespace(
            channel=[0] * MIDI_CHANNEL_COUNT,
            touch=[False] * MIDI_CHANNEL_COUNT,
            dial=[dial] + [0] * (MIDI_CHANNEL_COUNT - 1),
            btn0=[False] * MIDI_CHANNEL_COUNT,
            btn1=[False] * MIDI_CHANNEL_COUNT,
            btn2=[False] * MIDI_CHANNEL_COUNT,
            btn3=[select] + [False] * (MIDI_CHANNEL_COUNT - 1),
        )

    node._midi_callback(message(select=True))
    assert node._control_enabled[0] is True
    assert node._pending_fader_positions[0] == pytest.approx(MIDI_VALUE_MAX / 2, abs=1)
    # An immediate SELECT LED echo must not toggle the channel back OFF.
    node._midi_callback(message(select=False))
    node._midi_callback(message(select=True))
    assert node._control_enabled[0] is True
    node._midi_callback(message(select=False, dial=4))
    assert node._banks.active_bank()['mappings'][0]['filter_level'] == 4
    node._last_select_toggle_at[0] = time.monotonic() - 1.0
    node._midi_callback(message(select=True, dial=4))
    assert node._control_enabled[0] is False
    assert node._pending_fader_positions[0] == 0
    node._midi_callback(message(select=False, dial=4))

    matched.clear()
    node._last_select_toggle_at[0] = time.monotonic() - 1.0
    node._midi_callback(message(select=True, dial=4))
    assert node._control_enabled[0] is False
    assert node._pending_fader_positions[0] == 0


def test_only_one_selected_midi_line_can_own_the_same_motion_axis():
    node = MidiControlNode.__new__(MidiControlNode)
    node._lock = threading.Lock()
    node._banks = MidiBankManager()
    mappings = node._banks.active_bank()['mappings']
    mappings[0]['motion_id'] = '1-1'
    mappings[4]['motion_id'] = '1-1'
    node._banks.update_bank('bank_1', mappings=mappings)
    node._raw_channels = [0] * MIDI_CHANNEL_COUNT
    node._channels = [0.0] * MIDI_CHANNEL_COUNT
    node._filter_stage1 = [0.0] * MIDI_CHANNEL_COUNT
    node._filter_stage2 = [0.0] * MIDI_CHANNEL_COUNT
    node._filter_last_at = [None] * MIDI_CHANNEL_COUNT
    node._touch = [False] * MIDI_CHANNEL_COUNT
    node._dial = [0] * MIDI_CHANNEL_COUNT
    node._btn0 = [False] * MIDI_CHANNEL_COUNT
    node._btn1 = [False] * MIDI_CHANNEL_COUNT
    node._btn2 = [False] * MIDI_CHANNEL_COUNT
    node._btn3 = [False] * MIDI_CHANNEL_COUNT
    node._previous_btn0 = [False] * MIDI_CHANNEL_COUNT
    node._previous_btn3 = [False] * MIDI_CHANNEL_COUNT
    node._previous_dial = [0] * MIDI_CHANNEL_COUNT
    node._confirmed = [False] * MIDI_CHANNEL_COUNT
    node._control_enabled = [False] * MIDI_CHANNEL_COUNT
    node._final_output_values = [0.0] * MIDI_CHANNEL_COUNT
    node._pending_fader_positions = [None] * MIDI_CHANNEL_COUNT
    node._motor_angle_mode = [False] * MIDI_CHANNEL_COUNT
    node._bank_file_dirty = False
    add_motor_control_state(node)
    row = {
        'motor_axis': 2,
        'motion_lower_deg': -20,
        'motion_upper_deg': 20,
        'reference_position_deg': 10,
        'gear_ratio': 1,
        'scale': 1,
    }
    node._axis_registry = SimpleNamespace(
        motor_axis=lambda motion_id: 2 if motion_id == '1-1' else None,
        mapping=lambda motion_id: row if motion_id == '1-1' else None,
        file_id='selected.yaml',
    )

    def select_message(selected_channel=None):
        buttons = [False] * MIDI_CHANNEL_COUNT
        if selected_channel is not None:
            buttons[selected_channel] = True
        return SimpleNamespace(
            channel=[0] * MIDI_CHANNEL_COUNT,
            touch=[False] * MIDI_CHANNEL_COUNT,
            dial=[0] * MIDI_CHANNEL_COUNT,
            btn0=[False] * MIDI_CHANNEL_COUNT,
            btn1=[False] * MIDI_CHANNEL_COUNT,
            btn2=[False] * MIDI_CHANNEL_COUNT,
            btn3=buttons,
        )

    node._midi_callback(select_message(0))
    node._midi_callback(select_message())
    assert node._control_enabled[0] is True

    node._midi_callback(select_message(4))

    assert node._control_enabled[0] is False
    assert node._control_enabled[4] is True
    assert node._pending_fader_positions[0] == 0


def test_unsafe_same_axis_handover_keeps_existing_line_selected():
    node = MidiControlNode.__new__(MidiControlNode)
    node._lock = threading.Lock()
    node._banks = MidiBankManager()
    mappings = node._banks.active_bank()['mappings']
    mappings[0]['motion_id'] = '1-1'
    mappings[4]['motion_id'] = '1-1'
    mappings[4]['min_percent'] = 50
    mappings[4]['max_percent'] = 100
    node._banks.update_bank('bank_1', mappings=mappings)
    node._raw_channels = [0] * MIDI_CHANNEL_COUNT
    node._channels = [0.0] * MIDI_CHANNEL_COUNT
    node._filter_stage1 = [0.0] * MIDI_CHANNEL_COUNT
    node._filter_stage2 = [0.0] * MIDI_CHANNEL_COUNT
    node._filter_last_at = [None] * MIDI_CHANNEL_COUNT
    node._touch = [False] * MIDI_CHANNEL_COUNT
    node._dial = [0] * MIDI_CHANNEL_COUNT
    node._btn0 = [False] * MIDI_CHANNEL_COUNT
    node._btn1 = [False] * MIDI_CHANNEL_COUNT
    node._btn2 = [False] * MIDI_CHANNEL_COUNT
    node._btn3 = [False] * MIDI_CHANNEL_COUNT
    node._previous_btn0 = [False] * MIDI_CHANNEL_COUNT
    node._previous_btn3 = [False] * MIDI_CHANNEL_COUNT
    node._previous_dial = [0] * MIDI_CHANNEL_COUNT
    node._confirmed = [False] * MIDI_CHANNEL_COUNT
    node._control_enabled = [False] * MIDI_CHANNEL_COUNT
    node._final_output_values = [0.0] * MIDI_CHANNEL_COUNT
    node._pending_fader_positions = [None] * MIDI_CHANNEL_COUNT
    node._motor_angle_mode = [False] * MIDI_CHANNEL_COUNT
    node._bank_file_dirty = False
    add_motor_control_state(node)
    # motor=-5 with reference=10 maps to motion=-15 (12.5%), which cannot
    # be represented by the incoming 50..100% line.
    node._latest_motion_state = {
        'motors': [{'controller_index': 2, 'position_deg': -5.0}]
    }
    row = {
        'motor_axis': 2,
        'motion_lower_deg': -20,
        'motion_upper_deg': 20,
        'reference_position_deg': 10,
        'gear_ratio': 1,
        'scale': 1,
    }
    node._axis_registry = SimpleNamespace(
        motor_axis=lambda motion_id: 2 if motion_id == '1-1' else None,
        mapping=lambda motion_id: row if motion_id == '1-1' else None,
        file_id='selected.yaml',
    )

    def select_message(selected_channel=None):
        buttons = [False] * MIDI_CHANNEL_COUNT
        if selected_channel is not None:
            buttons[selected_channel] = True
        return SimpleNamespace(
            channel=[0] * MIDI_CHANNEL_COUNT,
            touch=[False] * MIDI_CHANNEL_COUNT,
            dial=[0] * MIDI_CHANNEL_COUNT,
            btn0=[False] * MIDI_CHANNEL_COUNT,
            btn1=[False] * MIDI_CHANNEL_COUNT,
            btn2=[False] * MIDI_CHANNEL_COUNT,
            btn3=buttons,
        )

    node._midi_callback(select_message(0))
    node._midi_callback(select_message())
    assert node._control_enabled[0] is True

    node._midi_callback(select_message(4))

    assert node._control_enabled[0] is True
    assert node._control_enabled[4] is False
    assert node._motor_command_state[4] == 'activation_rejected'
    assert '제어 범위' in node._motor_command_message[4]
    assert node._pending_fader_positions[0] != 0


def test_selected_mapping_context_is_used_when_no_run_mapping_is_active(tmp_path):
    node = MidiControlNode.__new__(MidiControlNode)
    node._lock = threading.Lock()
    node._mappings_dir = tmp_path
    node._selected_mapping_file_id = ''
    node._run_mapping_file_id = ''
    node._preferred_mapping_file_id = ''
    node._bank_config_file = None
    node._bank_file_loaded = False
    node._bank_file_dirty = False
    selected = tmp_path / 'selected.yaml'
    selected.write_text('mappings: []\n', encoding='utf-8')

    node._motion_mapping_response_callback(SimpleNamespace(
        data='{"success": true, "file": {"id": "selected.yaml"}}'
    ))
    assert node._preferred_mapping_file_id == 'selected.yaml'
    assert node._bank_config_file == selected


    node._motion_run_status_callback(SimpleNamespace(
        data='{"mapping_file_id": "running.yaml"}'
    ))
    assert node._preferred_mapping_file_id == 'running.yaml'
    assert node._bank_config_file == selected
    assert node._requested_mapping_file({
        'config_file': '/home/joonho_test/ros2_ws/config/active_motor_config.yaml'
    }) == selected

    node._motion_run_status_callback(SimpleNamespace(data='{"mapping_file_id": ""}'))
    assert node._preferred_mapping_file_id == 'selected.yaml'


def test_reset_live_values_keeps_bank_settings_but_clears_runtime_state():
    node = MidiControlNode.__new__(MidiControlNode)
    node._raw_channels = [1234] * MIDI_CHANNEL_COUNT
    node._channels = [1200.0] * MIDI_CHANNEL_COUNT
    node._filter_stage1 = [1200.0] * MIDI_CHANNEL_COUNT
    node._filter_stage2 = [1100.0] * MIDI_CHANNEL_COUNT
    node._filter_last_at = [time.monotonic()] * MIDI_CHANNEL_COUNT
    node._confirmed = [True] * MIDI_CHANNEL_COUNT
    node._touch = [True] * MIDI_CHANNEL_COUNT
    node._dial = [4] * MIDI_CHANNEL_COUNT
    node._previous_dial = [2] * MIDI_CHANNEL_COUNT
    node._control_enabled = [True] * MIDI_CHANNEL_COUNT
    node._final_output_values = [999.0] * MIDI_CHANNEL_COUNT
    node._pending_fader_positions = [None] * MIDI_CHANNEL_COUNT
    node._motor_angle_mode = [True] * MIDI_CHANNEL_COUNT
    node._last_feedback = [('old',)] * MIDI_CHANNEL_COUNT

    node._reset_live_values_locked()

    assert node._raw_channels == [0] * MIDI_CHANNEL_COUNT
    assert node._channels == [0.0] * MIDI_CHANNEL_COUNT
    assert node._confirmed == [False] * MIDI_CHANNEL_COUNT
    assert node._control_enabled == [False] * MIDI_CHANNEL_COUNT
    assert node._final_output_values == [0.0] * MIDI_CHANNEL_COUNT
    assert node._pending_fader_positions == [0] * MIDI_CHANNEL_COUNT
    assert node._previous_dial == node._dial
