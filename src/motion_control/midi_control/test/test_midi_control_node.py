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
    require_motion_value_within_limits,
    require_same_motion_ranges,
    safe_motion_range_for_motor,
    second_order_low_pass,
)


class CapturePublisher:
    def __init__(self):
        self.messages = []

    def publish(self, message):
        self.messages.append(message)


def add_motor_control_state(node):
    node._execution_context = {'context_id': 'test-context'}
    node._execution_context_ready = True
    node._studio_select_locked = False
    node._studio_zero_fader_targets = [0] * MIDI_CHANNEL_COUNT
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
    node._latest_motion_state = {'motors': [{
        'controller_index': 2,
        'position_deg': 10.0,
        'lower': -180.0,
        'upper': 180.0,
    }]}
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


def test_bank_change_clears_select_and_parks_all_faders_at_zero():
    node = MidiControlNode.__new__(MidiControlNode)
    node._control_enabled = [True] * MIDI_CHANNEL_COUNT
    node._final_output_values = [1234.0] * MIDI_CHANNEL_COUNT
    node._pending_fader_positions = [4321] * MIDI_CHANNEL_COUNT
    node._fader_sync_targets = [4321] * MIDI_CHANNEL_COUNT
    node._awaiting_fader_sync = [True] * MIDI_CHANNEL_COUNT
    node._fader_sync_not_before = [1.0] * MIDI_CHANNEL_COUNT
    node._last_select_toggle_at = [1.0] * MIDI_CHANNEL_COUNT
    node._last_motor_command_at = [1.0] * MIDI_CHANNEL_COUNT
    node._last_motor_target = [10.0] * MIDI_CHANNEL_COUNT
    node._pending_motor_requests = {0: {'axis': 0}}
    node._motor_follow_active = [True] * MIDI_CHANNEL_COUNT
    node._motor_command_state = ['commanding'] * MIDI_CHANNEL_COUNT
    node._motor_command_message = ['moving'] * MIDI_CHANNEL_COUNT
    node._motor_angle_mode = [True] * MIDI_CHANNEL_COUNT
    node._last_feedback = [('old',)] * MIDI_CHANNEL_COUNT
    node._raw_channels = [8000] * MIDI_CHANNEL_COUNT
    node._channels = [8000.0] * MIDI_CHANNEL_COUNT
    node._filter_stage1 = [8000.0] * MIDI_CHANNEL_COUNT
    node._filter_stage2 = [8000.0] * MIDI_CHANNEL_COUNT
    node._filter_last_at = [1.0] * MIDI_CHANNEL_COUNT
    node._confirmed = [True] * MIDI_CHANNEL_COUNT
    node._touch = [True] * MIDI_CHANNEL_COUNT
    node._physical_touch = [True] * MIDI_CHANNEL_COUNT
    node._fader_moving = [True] * MIDI_CHANNEL_COUNT
    node._bridge_fader_syncing = [True] * MIDI_CHANNEL_COUNT
    node._previous_dial = [0] * MIDI_CHANNEL_COUNT
    node._dial = [0] * MIDI_CHANNEL_COUNT
    node._btn3 = [True, False] * (MIDI_CHANNEL_COUNT // 2)
    node._previous_btn3 = [False] * MIDI_CHANNEL_COUNT

    node._reset_bank_change_state_locked()

    assert node._control_enabled == [False] * MIDI_CHANNEL_COUNT
    assert node._raw_channels == [0] * MIDI_CHANNEL_COUNT
    assert node._channels == [0.0] * MIDI_CHANNEL_COUNT
    assert node._pending_fader_positions == [0] * MIDI_CHANNEL_COUNT
    assert node._previous_btn3 == node._btn3


def test_filter_only_bank_change_keeps_select_and_fader_ownership():
    node = MidiControlNode.__new__(MidiControlNode)
    node._banks = MidiBankManager()
    node._control_enabled = [True] + [False] * (MIDI_CHANNEL_COUNT - 1)
    node._pending_fader_positions = [2345] + [None] * (MIDI_CHANNEL_COUNT - 1)
    node._raw_channels = [2345] * MIDI_CHANNEL_COUNT
    node._channels = [2345.0] * MIDI_CHANNEL_COUNT
    node._filter_stage1 = [1200.0] * MIDI_CHANNEL_COUNT
    node._filter_stage2 = [1200.0] * MIDI_CHANNEL_COUNT
    node._filter_last_at = [1.0] * MIDI_CHANNEL_COUNT
    previous = node._banks.snapshot()
    mappings = node._banks.active_bank()['mappings']
    mappings[0]['filter_level'] = 7
    node._banks.update_bank('bank_1', mappings=mappings)

    reset_select = node._finish_bank_settings_change_locked(previous)

    assert reset_select is False
    assert node._control_enabled[0] is True
    assert node._pending_fader_positions[0] == 2345
    assert node._filter_stage1 == [2345.0] * MIDI_CHANNEL_COUNT


def test_non_filter_bank_change_requires_select_reset():
    node = MidiControlNode.__new__(MidiControlNode)
    node._banks = MidiBankManager()
    previous = node._banks.snapshot()
    mappings = node._banks.active_bank()['mappings']
    mappings[0]['motion_id'] = '3-1'
    node._banks.update_bank('bank_1', mappings=mappings)

    assert (
        node._active_bank_control_signature(previous)
        != node._active_bank_control_signature(node._banks.snapshot())
    )


def test_repeated_same_project_context_does_not_release_select(tmp_path):
    project_id = 'project-1'
    mapping_name = 'mapping.yaml'
    project_dir = tmp_path / project_id
    mappings_dir = project_dir / 'motion_axis_matching'
    mappings_dir.mkdir(parents=True)
    (project_dir / 'project.json').write_text('{}\n', encoding='utf-8')
    (mappings_dir / mapping_name).write_text('mappings: []\n', encoding='utf-8')

    node = MidiControlNode.__new__(MidiControlNode)
    node._lock = threading.Lock()
    node._motion_projects_dir = tmp_path
    node._project_id = project_id
    node._mappings_dir = mappings_dir
    node._latest_motion_state = {}
    node._selected_mapping_file_id = mapping_name
    node._preferred_mapping_file_id = mapping_name
    node._axis_registry = SimpleNamespace(file_id=mapping_name)
    node._bank_config_file = mappings_dir / mapping_name
    node._banks = MidiBankManager()
    node._bank_file_loaded = False
    node._bank_file_dirty = False
    node._execution_context = {'context_id': ''}
    node._execution_context_ready = True
    node._control_enabled = [True] + [False] * (MIDI_CHANNEL_COUNT - 1)
    reset_calls = []
    node._reset_bank_change_state_locked = lambda: reset_calls.append(True)
    node._snapshot = lambda: {
        'success': True,
        'project_id': node._project_id,
        'motion_mapping_file_id': node._selected_mapping_file_id,
    }
    node._response_publisher = CapturePublisher()

    node._request_callback(SimpleNamespace(data=json.dumps({
        'request_id': 'same-context',
        'command': 'select_project',
        'payload': {
            'project_id': project_id,
            'mapping_file_id': mapping_name,
        },
    })))

    response = json.loads(node._response_publisher.messages[-1].data)
    assert response['success'] is True
    assert response['context_changed'] is False
    assert response['context_id'] == ''
    assert response['project_id'] == project_id
    assert response['mapping_file_id'] == mapping_name
    assert node._control_enabled[0] is True
    assert reset_calls == []


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
    assert payload['atomic_channels'] == []


def test_only_supervisor_approved_motion_values_become_recording_source():
    node = MidiControlNode.__new__(MidiControlNode)
    node._lock = threading.Lock()
    node._banks = MidiBankManager()
    node._axis_registry = SimpleNamespace(file_id='selected.yaml')
    node._fader_parking = [False] * MIDI_CHANNEL_COUNT
    node._motor_command_state = ['inactive'] * MIDI_CHANNEL_COUNT
    node._motor_command_message = [''] * MIDI_CHANNEL_COUNT

    node._apply_motor_results([{
        'channel': 0,
        'axis': 2,
        'motion_id': '1-1',
        'mapping_file_id': 'selected.yaml',
        'motion_deg': 170.0,
        'target_deg': 180.0,
        'success': True,
        'message': 'accepted',
    }], 10.0)

    assert node._approved_motion_values[0] == {'1-1': 170.0}
    assert node._approved_motor_targets[0] == {2: 180.0}

    node._apply_motor_results([{
        'channel': 0,
        'axis': 2,
        'motion_id': '1-1',
        'mapping_file_id': 'selected.yaml',
        'motion_deg': 179.934,
        'target_deg': 189.934,
        'success': False,
        'message': 'upper limit exceeded',
    }], 11.0)

    assert node._approved_motion_values[0] == {}
    assert node._approved_motor_targets[0] == {}
    assert node._motor_command_state[0] == 'rejected'


def test_linked_targets_mark_the_channel_as_atomic():
    node = MidiControlNode.__new__(MidiControlNode)
    node._lock = threading.Lock()
    node._pending_motor_requests = {
        (0, 1): {'channel': 0, 'axis': 1, 'target_deg': 10.0},
        (0, 2): {'channel': 0, 'axis': 2, 'target_deg': 10.0},
        (0, 3): {'channel': 0, 'axis': 3, 'target_deg': 10.0},
    }
    node._last_motor_command_at = [0.0] * MIDI_CHANNEL_COUNT
    node._motor_command_message = [''] * MIDI_CHANNEL_COUNT
    node._request_sequence = 0
    node._motor_request_publisher = CapturePublisher()

    node._publish_motor_request_batch()

    payload = json.loads(node._motor_request_publisher.messages[0].data)
    assert payload['atomic_channels'] == [0]
    assert [target['axis'] for target in payload['targets']] == [1, 2, 3]


def test_linked_axes_current_version_requires_identical_motion_ranges():
    rows = [
        {'motion_lower_deg': -20, 'motion_upper_deg': 20},
        {'motion_lower_deg': -20, 'motion_upper_deg': 20},
    ]
    assert require_same_motion_ranges(rows) == (-20.0, 20.0)

    rows[1]['motion_upper_deg'] = 30
    with pytest.raises(ValueError, match='모션 범위가 서로 다릅니다'):
        require_same_motion_ranges(rows)


def parking_node():
    node = MidiControlNode.__new__(MidiControlNode)
    node._control_enabled = [False] * MIDI_CHANNEL_COUNT
    node._control_enabled[0] = True
    node._pending_fader_positions = [None] * MIDI_CHANNEL_COUNT
    node._fader_sync_targets = [None] * MIDI_CHANNEL_COUNT
    node._awaiting_fader_sync = [False] * MIDI_CHANNEL_COUNT
    node._fader_sync_not_before = [0.0] * MIDI_CHANNEL_COUNT
    node._last_motor_target = [None] * MIDI_CHANNEL_COUNT
    node._last_group_motor_targets = [{} for _ in range(MIDI_CHANNEL_COUNT)]
    node._last_group_motor_targets[0] = {2: 10.0, 3: 10.0}
    node._pending_motor_requests = {
        (0, 2): {'channel': 0, 'axis': 2},
        (0, 3): {'channel': 0, 'axis': 3},
    }
    node._motor_follow_active = [False] * MIDI_CHANNEL_COUNT
    node._motor_command_state = ['inactive'] * MIDI_CHANNEL_COUNT
    node._motor_command_message = [''] * MIDI_CHANNEL_COUNT
    node._last_feedback = [None] * MIDI_CHANNEL_COUNT
    node._physical_touch = [False] * MIDI_CHANNEL_COUNT
    node._fader_moving = [False] * MIDI_CHANNEL_COUNT
    node._bridge_fader_syncing = [False] * MIDI_CHANNEL_COUNT
    node._raw_channels = [8000] * MIDI_CHANNEL_COUNT
    node._channels = [8000.0] * MIDI_CHANNEL_COUNT
    node._filter_stage1 = [8000.0] * MIDI_CHANNEL_COUNT
    node._filter_stage2 = [8000.0] * MIDI_CHANNEL_COUNT
    node._request_sequence = 0
    node._motor_request_publisher = CapturePublisher()
    return node


def test_select_off_requests_motor_hold_and_enters_mandatory_zero_parking():
    node = parking_node()

    node._deactivate_control_channel_locked(0)

    assert node._control_enabled[0] is False
    assert node._fader_parking[0] is True
    assert node._pending_fader_positions[0] == 0
    assert node._pending_motor_requests == {}
    payload = json.loads(node._motor_request_publisher.messages[0].data)
    assert payload['hold_axes'] == [2, 3]


def test_fader_parking_waits_for_hand_release_retries_zero_and_confirms_arrival():
    node = parking_node()
    node._deactivate_control_channel_locked(0)
    started = node._fader_park_last_command_at[0]
    node._pending_fader_positions[0] = None
    node._physical_touch[0] = True

    assert node._update_fader_parking_locked(0, 7000, started + 1.0) is True
    assert node._pending_fader_positions[0] is None
    assert node._fader_parking[0] is True

    node._physical_touch[0] = False
    node._update_fader_parking_locked(0, 7000, started + 1.1)
    assert node._pending_fader_positions[0] == 0
    assert node._fader_parking[0] is True

    node._pending_fader_positions[0] = None
    node._update_fader_parking_locked(0, 0, started + 1.2)
    assert node._fader_parking[0] is False
    assert node._raw_channels[0] == 0


def test_studio_select_is_ignored_without_restarting_zero_fader_command():
    node = parking_node()
    node._lock = threading.Lock()
    node._banks = MidiBankManager()
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
    node._final_output_values = [0.0] * MIDI_CHANNEL_COUNT
    node._motor_angle_mode = [False] * MIDI_CHANNEL_COUNT
    node._studio_select_locked = True
    node._studio_zero_fader_targets = [0] * MIDI_CHANNEL_COUNT
    node._last_select_toggle_at = [0.0] * MIDI_CHANNEL_COUNT
    node._last_motor_command_at = [0.0] * MIDI_CHANNEL_COUNT
    node._latest_motion_state = {}
    node._device_connected = True

    node._deactivate_control_channel_locked(0, request_motor_hold=False)
    node._pending_fader_positions[0] = None
    node._fader_moving[0] = True
    message = SimpleNamespace(
        channel=[0] * MIDI_CHANNEL_COUNT,
        touch=[False] * MIDI_CHANNEL_COUNT,
        dial=[0] * MIDI_CHANNEL_COUNT,
        btn0=[False] * MIDI_CHANNEL_COUNT,
        btn1=[False] * MIDI_CHANNEL_COUNT,
        btn2=[False] * MIDI_CHANNEL_COUNT,
        btn3=[True] + [False] * (MIDI_CHANNEL_COUNT - 1),
    )

    node._midi_callback(message)

    assert node._control_enabled[0] is False
    assert node._pending_fader_positions[0] is None
    assert node._fader_parking[0] is True
    assert node._motor_command_state[0] == 'studio_initializing'
    assert 'SELECT 입력 무시됨' in node._motor_command_message[0]

    node._fader_moving[0] = False
    node._midi_callback(message)
    assert node._fader_parking[0] is False
    assert node._studio_recording_zero_status_locked()['ready'] is True

    node._finish_studio_recording_initialization_locked()
    assert node._studio_select_locked is False
    assert node._control_enabled == [False] * MIDI_CHANNEL_COUNT
    assert node._previous_btn3[0] is True


def test_one_selected_fader_creates_same_motion_value_for_two_linked_axes():
    node = MidiControlNode.__new__(MidiControlNode)
    node._lock = threading.Lock()
    node._banks = MidiBankManager()
    mappings = node._banks.active_bank()['mappings']
    mappings[0]['motion_id'] = '1-1'
    mappings[0]['linked_motion_ids'] = ['1-2']
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
    node._latest_motion_state = {
        'motors': [
            {'controller_index': 2, 'position_deg': 0.0, 'lower': -180.0, 'upper': 180.0},
            {'controller_index': 3, 'position_deg': 0.0, 'lower': -180.0, 'upper': 180.0},
        ],
    }
    row = {
        'motion_lower_deg': -20,
        'motion_upper_deg': 20,
        'reference_position_deg': 0,
        'gear_ratio': 1,
        'scale': 1,
    }
    axes = {'1-1': 2, '1-2': 3}
    node._axis_registry = SimpleNamespace(
        motor_axis=lambda motion_id: axes.get(motion_id),
        mapping=lambda motion_id: row if motion_id in axes else None,
        file_id='selected.yaml',
    )

    def message(*, select=False, touch=False, value=0):
        return SimpleNamespace(
            channel=[value] + [0] * (MIDI_CHANNEL_COUNT - 1),
            touch=[touch] + [False] * (MIDI_CHANNEL_COUNT - 1),
            dial=[0] * MIDI_CHANNEL_COUNT,
            btn0=[False] * MIDI_CHANNEL_COUNT,
            btn1=[False] * MIDI_CHANNEL_COUNT,
            btn2=[False] * MIDI_CHANNEL_COUNT,
            btn3=[select] + [False] * (MIDI_CHANNEL_COUNT - 1),
        )

    node._midi_callback(message(select=True))
    node._midi_callback(message())
    node._awaiting_fader_sync[0] = False
    node._midi_callback(message(touch=True, value=MIDI_VALUE_MAX))

    targets = list(node._pending_motor_requests.values())
    assert {target['axis'] for target in targets} == {2, 3}
    assert {target['motion_id'] for target in targets} == {'1-1', '1-2'}
    assert {target['motion_deg'] for target in targets} == {20.0}


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


def test_safe_motion_range_includes_reference_position_and_motor_limits():
    row = {
        'motion_lower_deg': -180.0,
        'motion_upper_deg': 180.0,
        'reference_enabled': True,
        'reference_position_deg': 10.0,
        'offset_deg': 0.0,
        'scale': 1.0,
        'gear_ratio': 1.0,
        'invert': False,
    }
    motor = {
        'controller_index': 2,
        'lower': -180.0,
        'upper': 180.0,
    }

    safe_range = safe_motion_range_for_motor(row, motor)
    assert safe_range == pytest.approx((-180.0, 170.0))
    safe_motion_value = motion_value_from_output(MIDI_VALUE_MAX, row, safe_range)
    assert safe_motion_value == 170.0
    assert require_motion_value_within_limits('3-1', 170.0, row, motor) == 180.0
    with pytest.raises(ValueError, match='3-1.*목표 189.934°.*Upper 180.000°'):
        require_motion_value_within_limits('3-1', 179.934, row, motor)


def test_safe_motion_range_supports_inverted_scaled_mapping():
    row = {
        'motion_lower_deg': -100.0,
        'motion_upper_deg': 100.0,
        'reference_enabled': True,
        'reference_position_deg': 20.0,
        'offset_deg': 5.0,
        'scale': 2.0,
        'gear_ratio': 1.0,
        'invert': True,
    }
    motor = {'lower': -100.0, 'upper': 100.0}

    assert safe_motion_range_for_motor(row, motor) == pytest.approx((-45.0, 55.0))


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


def test_studio_recording_prepare_clears_select_and_parks_at_physical_zero():
    mappings = [{
        'channel': channel,
        'motion_id': '1-1' if channel == 0 else f'9-{channel + 1}',
        'enabled': channel == 0,
        'min_percent': 0.0,
        'max_percent': 100.0,
        'reversed': False,
        'filter_level': 0,
    } for channel in range(MIDI_CHANNEL_COUNT)]

    class Banks:
        @staticmethod
        def snapshot():
            return {'active_bank': {'mappings': mappings}}

    class Registry:
        @staticmethod
        def refresh(_preferred=None, _motion_state=None):
            return None

        @staticmethod
        def mapping(motion_id):
            return (
                {'motion_lower_deg': -20.0, 'motion_upper_deg': 20.0}
                if motion_id == '1-1' else None
            )

        @staticmethod
        def motor_axis(motion_id):
            return 0 if motion_id == '1-1' else None

    node = MidiControlNode.__new__(MidiControlNode)
    node._banks = Banks()
    node._axis_registry = Registry()
    node._preferred_mapping_file_id = ''
    node._last_axis_registry_refresh = 0.0
    node._latest_motion_state = {'motors': [{
        'controller_index': 0,
        'position_deg': 0.0,
        'lower': -180.0,
        'upper': 180.0,
    }]}
    node._control_enabled = [True] * MIDI_CHANNEL_COUNT
    node._studio_select_locked = False
    node._studio_zero_fader_targets = [0] * MIDI_CHANNEL_COUNT
    node._raw_channels = [100] * MIDI_CHANNEL_COUNT
    node._channels = [100.0] * MIDI_CHANNEL_COUNT
    node._filter_stage1 = [100.0] * MIDI_CHANNEL_COUNT
    node._filter_stage2 = [100.0] * MIDI_CHANNEL_COUNT
    node._filter_last_at = [None] * MIDI_CHANNEL_COUNT
    node._pending_fader_positions = [None] * MIDI_CHANNEL_COUNT
    node._fader_sync_targets = [None] * MIDI_CHANNEL_COUNT
    node._awaiting_fader_sync = [False] * MIDI_CHANNEL_COUNT
    node._fader_sync_not_before = [0.0] * MIDI_CHANNEL_COUNT
    node._last_motor_target = [1.0] * MIDI_CHANNEL_COUNT
    node._pending_motor_requests = {0: {'target': 1.0}}
    node._motor_follow_active = [True] * MIDI_CHANNEL_COUNT
    node._motor_command_state = ['commanding'] * MIDI_CHANNEL_COUNT
    node._motor_command_message = ['moving'] * MIDI_CHANNEL_COUNT
    node._last_feedback = [('old',)] * MIDI_CHANNEL_COUNT
    node._btn3 = [False] * MIDI_CHANNEL_COUNT
    node._previous_btn3 = [True] * MIDI_CHANNEL_COUNT
    node._request_sequence = 0
    node._motor_request_publisher = CapturePublisher()
    node._physical_touch = [False] * MIDI_CHANNEL_COUNT
    node._fader_moving = [False] * MIDI_CHANNEL_COUNT
    node._bridge_fader_syncing = [False] * MIDI_CHANNEL_COUNT

    result = node._prepare_studio_recording_locked()

    assert result['errors'] == []
    assert node._studio_select_locked is True
    assert node._control_enabled == [False] * MIDI_CHANNEL_COUNT
    assert node._pending_motor_requests == {}
    assert node._pending_fader_positions == [0] * MIDI_CHANNEL_COUNT
    assert node._studio_zero_fader_targets == [0] * MIDI_CHANNEL_COUNT
    assert node._raw_channels == [0] * MIDI_CHANNEL_COUNT
    assert node._motor_request_publisher.messages == []


def test_studio_recording_zero_status_waits_for_physical_parking_completion():
    node = MidiControlNode.__new__(MidiControlNode)
    node._device_connected = True
    node._raw_channels = [0] * MIDI_CHANNEL_COUNT
    node._fader_parking = [False] * MIDI_CHANNEL_COUNT
    node._fader_parking[2] = True
    node._fader_park_last_command_at = [0.0] * MIDI_CHANNEL_COUNT
    node._physical_touch = [False] * MIDI_CHANNEL_COUNT
    node._fader_moving = [False] * MIDI_CHANNEL_COUNT
    node._bridge_fader_syncing = [False] * MIDI_CHANNEL_COUNT

    pending = node._studio_recording_zero_status_locked()

    assert pending['ready'] is False
    assert pending['pending_channels'] == [{
        'channel': 3,
        'raw': 0,
        'parking': True,
        'busy': False,
        'physical_touch': False,
        'fader_moving': False,
        'fader_syncing': False,
    }]

    node._fader_parking[2] = False
    ready = node._studio_recording_zero_status_locked()

    assert ready['ready'] is True
    assert ready['pending_channels'] == []


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

    def message(*, select=False, dial=0, value=0):
        return SimpleNamespace(
            channel=[value] + [0] * (MIDI_CHANNEL_COUNT - 1),
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
    node._midi_callback(message(select=False, dial=4, value=8000))

    # A rapid re-press while the physical fader has not reached zero must not
    # overwrite the mandatory park command with a pickup position.
    node._last_select_toggle_at[0] = time.monotonic() - 1.0
    node._midi_callback(message(select=True, dial=4, value=8000))
    assert node._control_enabled[0] is False
    assert node._fader_parking[0] is True
    assert node._pending_fader_positions[0] == 0
    node._midi_callback(message(select=False, dial=4, value=0))
    assert node._fader_parking[0] is False

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
        'motors': [{
            'controller_index': 2,
            'position_deg': -5.0,
            'lower': -180.0,
            'upper': 180.0,
        }]
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
