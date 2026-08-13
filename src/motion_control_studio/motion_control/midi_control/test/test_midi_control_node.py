import json
import threading
import time
from types import SimpleNamespace

import pytest

from midi_control.bank_manager import MIDI_CHANNEL_COUNT, MidiBankManager
from midi_control.midi_control_node import (
    MIDI_VALUE_MAX,
    MidiControlNode,
    motion_value_display,
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


def test_motion_value_display_uses_source_values_and_reports_missing_or_diff():
    value, text, status = motion_value_display(
        ['1-1', '1-2'],
        {'1-1': 1.2345, '1-2': 1.2345},
    )
    assert value == pytest.approx(1.2345)
    assert text == '1.234'
    assert status == 'confirmed'
    assert motion_value_display(['1-1'], {}) == (None, 'NO DATA', 'missing')
    assert motion_value_display(
        ['1-1', '1-2'],
        {'1-1': 1.0, '1-2': 2.0},
    ) == (None, 'DIFF', 'different')


def test_motion_value_preview_is_available_only_while_select_is_enabled():
    assert motion_value_display(
        ['2-1'],
        {},
        control_enabled=False,
        estimated_value=-10.0,
    ) == (None, 'NO DATA', 'missing')
    assert motion_value_display(
        ['2-1'],
        {},
        control_enabled=True,
        estimated_value=-10.0,
    ) == (-10.0, '~-10.00', 'estimated')

    # A confirmed source-topic value always takes priority over the preview.
    assert motion_value_display(
        ['2-1'],
        {'2-1': 4.25},
        control_enabled=True,
        estimated_value=-10.0,
    ) == (4.25, '4.250', 'confirmed')


def test_motion_value_topic_cache_accepts_only_current_project_generation():
    node = MidiControlNode.__new__(MidiControlNode)
    node._lock = threading.Lock()
    node._project_id = 'project-1'
    node._execution_context = {'project_generation': 3}
    node._source_motion_values = {}
    node._source_motion_value_stamps = {}
    node._source_motion_value_context = ('', 0)

    node._motion_value_callback(SimpleNamespace(data=json.dumps({
        'project_id': 'project-1',
        'project_generation': 3,
        'stamp': 10.0,
        'values': {'2-1': 4.25},
    })))
    node._motion_value_callback(SimpleNamespace(data=json.dumps({
        'project_id': 'project-1',
        'project_generation': 2,
        'stamp': 20.0,
        'values': {'2-1': 9.0},
    })))

    assert node._source_motion_values == {'2-1': 4.25}


def test_rec_mode_sends_source_motion_text_and_off_mode_keeps_14bit_text():
    node = MidiControlNode.__new__(MidiControlNode)
    node._state_publisher = CapturePublisher()
    node._feedback_publisher = CapturePublisher()
    node._lock = threading.Lock()
    node._pending_fader_positions = [4321] + [None] * (MIDI_CHANNEL_COUNT - 1)
    node._fader_input_generation = [8] + [0] * (MIDI_CHANNEL_COUNT - 1)
    node._pending_fader_input_generations = [7] + [0] * (
        MIDI_CHANNEL_COUNT - 1
    )
    node._last_feedback = [None] * MIDI_CHANNEL_COUNT
    channel = {
        'channel': 0,
        'control_enabled': False,
        'display_motion_value': True,
        'filter_level': 4,
        'motion_id': '2-1',
        'motion_value_display_text': '4.250',
        'raw_value': 12345,
        'observed_raw_value': 8192,
        'final_output_value': 12345.0,
    }
    node._snapshot = lambda: {'channels': [dict(channel)]}

    node._publish_state()
    fields = node._feedback_publisher.messages[-1].data.split('\t')
    assert len(fields) == 8
    assert fields[2] == '1'
    assert fields[5] == '4.250'
    assert fields[6] == '4321'
    assert fields[7] == '7'

    channel['display_motion_value'] = False
    channel['display_raw_value'] = 0
    node._snapshot = lambda: {'channels': [dict(channel)]}
    node._publish_state()
    fields = node._feedback_publisher.messages[-1].data.split('\t')
    assert fields[2] == '0'
    # A SELECT pickup/park target is shown immediately; the independent
    # observed value remains available for physical-arrival checks.
    assert fields[5] == '0'
    assert fields[6] == '-1'
    assert fields[7] == '8'


def test_midi_node_rejects_previous_project_generation():
    node = MidiControlNode.__new__(MidiControlNode)
    node._project_generation = 6

    with pytest.raises(ValueError, match='이전 프로젝트 세대'):
        node._validate_request_generation(
            'invalidate_context', 5, {'project_generation': 5}
        )

    with pytest.raises(ValueError, match='현재 프로젝트 세대'):
        node._validate_request_generation(
            'update_bank', 7, {'project_generation': 7}
        )


def add_motor_control_state(node):
    node._execution_context = {'context_id': 'test-context'}
    node._execution_context_ready = True
    node._studio_select_locked = False
    node._studio_zero_fader_targets = [0] * MIDI_CHANNEL_COUNT
    node._physical_touch = [False] * MIDI_CHANNEL_COUNT
    node._fader_moving = [False] * MIDI_CHANNEL_COUNT
    node._bridge_fader_syncing = [False] * MIDI_CHANNEL_COUNT
    node._fader_input_generation = [0] * MIDI_CHANNEL_COUNT
    node._pending_fader_input_generations = [0] * MIDI_CHANNEL_COUNT
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
    node._fader_input_generation = [0] * MIDI_CHANNEL_COUNT
    node._pending_fader_input_generations = [0] * MIDI_CHANNEL_COUNT
    node._last_physical_input_monotonic = None
    node._last_physical_input_wall = None

    node._input_state_callback(SimpleNamespace(data=(
        '{"physical_touch":[true,false],'
        '"fader_moving":[false,true],'
        '"fader_syncing":[false,true],'
        '"fader_input_generation":[12,34],'
        '"input_event_seen":true,"last_input_event_age_ms":25}'
    )))

    assert node._physical_touch[:2] == [True, False]
    assert node._fader_moving[:2] == [False, True]
    assert node._bridge_fader_syncing[:2] == [False, True]
    assert node._fader_input_generation[:2] == [12, 34]
    assert node._last_physical_input_monotonic is not None
    assert time.monotonic() - node._last_physical_input_monotonic < 0.1


def test_old_generation_motor_result_and_motion_state_are_discarded():
    node = MidiControlNode.__new__(MidiControlNode)
    node._lock = threading.Lock()
    node._project_id = 'project-1'
    node._execution_context = {'project_generation': 4}
    node._execution_context_ready = True
    node._latest_motion_state = {'marker': 'current'}
    node._motor_command_state = ['inactive'] * MIDI_CHANNEL_COUNT

    node._motor_result_callback(SimpleNamespace(data=json.dumps({
        'project_generation': 3,
        'channel': 0,
        'success': False,
        'message': 'stale failure',
    })))
    assert node._motor_command_state[0] == 'inactive'

    node._motion_state_callback(SimpleNamespace(data=json.dumps({
        'project_id': 'project-1',
        'project_generation': 3,
        'motors': [],
    })))
    assert node._latest_motion_state == {'marker': 'current'}

    node._motion_state_callback(SimpleNamespace(data=json.dumps({
        'project_id': 'project-1',
        'project_generation': 4,
        'motors': [{'controller_index': 2, 'position_deg': 12.5}],
    })))
    assert node._latest_motion_state['motors'][0]['position_deg'] == 12.5


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
    node._execution_context = {'context_id': '', 'project_generation': 1}
    node._execution_context_ready = True
    node._project_generation = 1
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
        'project_generation': 1,
        'command': 'select_project',
        'payload': {
            'project_id': project_id,
            'mapping_file_id': mapping_name,
            'project_generation': 1,
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

    def reset_for_changed_context():
        reset_calls.append(True)
        node._control_enabled = [False] * MIDI_CHANNEL_COUNT

    node._reset_bank_change_state_locked = reset_for_changed_context
    node._request_callback(SimpleNamespace(data=json.dumps({
        'request_id': 'changed-context',
        'project_generation': 1,
        'command': 'select_project',
        'payload': {
            'project_id': project_id,
            'mapping_file_id': mapping_name,
            'project_generation': 1,
            'context_id': 'changed-context',
        },
    })))

    changed_response = json.loads(node._response_publisher.messages[-1].data)
    assert changed_response['success'] is True
    assert changed_response['context_changed'] is True
    assert node._control_enabled == [False] * MIDI_CHANNEL_COUNT
    assert reset_calls == [True]


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
    node._project_id = 'project-1'
    node._execution_context = {'project_generation': 7}
    node._motion_value_publisher = CapturePublisher()
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
    assert node._current_motion_values == {'1-1': 170.0}
    motion_state = json.loads(node._motion_value_publisher.messages[-1].data)
    assert motion_state['source'] == 'midi'
    assert motion_state['project_generation'] == 7
    assert motion_state['values'] == {'1-1': 170.0}

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
    assert len(node._motion_value_publisher.messages) == 1


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


def test_linked_select_uses_logical_motion_values_not_motor_positions():
    node = MidiControlNode.__new__(MidiControlNode)
    node._current_motion_values = {'1-1': 4.0, '1-2': 4.0}
    row = {
        'motion_lower_deg': -180.0,
        'motion_upper_deg': 180.0,
        'reference_position_deg': 0.0,
        'scale': 1.0,
        'gear_ratio': 1.0,
    }
    group = [
        {
            'motion_id': '1-1',
            'row': row,
            'motor': {'controller_index': 1, 'position_deg': 4.0},
        },
        {
            'motion_id': '1-2',
            'row': row,
            'motor': {'controller_index': 2, 'position_deg': 4.0},
        },
    ]

    assert node._logical_motion_value_for_group_locked(group) == 4.0

    node._current_motion_values['1-2'] = 4.1
    # An inconsistent logical group is not trusted; equal live feedback is
    # inverted instead of averaging or retaining the conflicting values.
    assert node._logical_motion_value_for_group_locked(group) == 4.0


def test_unknown_logical_motion_value_falls_back_to_motor_feedback():
    node = MidiControlNode.__new__(MidiControlNode)
    group = [{
        'motion_id': '1-1',
        'row': {
            'motion_lower_deg': -180.0,
            'motion_upper_deg': 180.0,
            'reference_position_deg': 10.0,
            'scale': 2.0,
            'gear_ratio': 1.0,
        },
        'motor': {
            'controller_index': 2,
            'position_deg': 30.0,
            'lower': -180.0,
            'upper': 180.0,
        },
    }]

    assert node._logical_motion_value_for_group_locked(group) == 10.0


def test_pickup_prefers_current_source_value_but_rejects_feedback_mismatch():
    node = MidiControlNode.__new__(MidiControlNode)
    node._project_id = 'project-1'
    node._execution_context = {'project_generation': 3}
    node._source_motion_value_context = ('project-1', 3)
    node._source_motion_values = {'1-1': 5.0}
    node._current_motion_values = {}
    row = {
        'motion_lower_deg': -20.0,
        'motion_upper_deg': 20.0,
        'reference_position_deg': 0.0,
        'scale': 1.0,
        'gear_ratio': 1.0,
    }
    motor = {
        'controller_index': 2,
        'position_deg': 5.0,
        'lower': -180.0,
        'upper': 180.0,
        'connection_state': 'online',
        'state': 'detected',
        'age_sec': 0.01,
    }
    group = [{'motion_id': '1-1', 'row': row, 'motor': motor}]

    assert node._pickup_reference_for_group_locked(group) == (5.0, 'source_topic')

    motor['position_deg'] = 10.0
    assert node._pickup_reference_for_group_locked(group) == (
        10.0,
        'motor_feedback',
    )


def test_mapping_change_recalculates_fader_from_feedback_with_new_ratio_and_range():
    node = MidiControlNode.__new__(MidiControlNode)
    node._project_id = 'project-1'
    node._execution_context = {'project_generation': 3}
    node._source_motion_value_context = ('project-1', 3)
    node._source_motion_values = {'1-1': 5.0}
    node._current_motion_values = {'1-1': 5.0}
    row = {
        'motion_lower_deg': -10.0,
        'motion_upper_deg': 10.0,
        'reference_enabled': True,
        'reference_position_deg': 100.0,
        'offset_deg': 0.0,
        'scale': 1.0,
        'gear_ratio': 50.0,
        'invert': False,
    }
    motor = {
        'controller_index': 0,
        'position_deg': 105.0,
        'lower': -1000.0,
        'upper': 1000.0,
        'connection_state': 'online',
        'state': 'detected',
        'age_sec': 0.01,
        'fault': False,
    }
    group = [{'motion_id': '1-1', 'row': row, 'motor': motor}]
    bank_mapping = {
        'min_percent': 0.0,
        'max_percent': 100.0,
        'reversed': False,
    }

    motion_value, source = node._pickup_reference_for_group_locked(group)
    new_raw = raw_fader_for_motion(motion_value, row, bank_mapping)
    old_raw = raw_fader_for_motion(5.0, row, bank_mapping)

    assert source == 'motor_feedback'
    assert motion_value == pytest.approx(0.1)
    assert motor_target_from_motion(motion_value, row) == pytest.approx(105.0)
    assert new_raw == round(MIDI_VALUE_MAX * 0.505)
    assert new_raw != old_raw


def test_pickup_rejects_stale_feedback_and_detects_crossing():
    node = MidiControlNode.__new__(MidiControlNode)
    node._project_id = 'project-1'
    node._execution_context = {'project_generation': 3}
    node._source_motion_value_context = ('project-1', 3)
    node._source_motion_values = {'1-1': 5.0}
    node._current_motion_values = {}
    group = [{
        'motion_id': '1-1',
        'row': {
            'motion_lower_deg': -20.0,
            'motion_upper_deg': 20.0,
            'reference_position_deg': 0.0,
            'scale': 1.0,
            'gear_ratio': 1.0,
        },
        'motor': {
            'controller_index': 2,
            'position_deg': 5.0,
            'lower': -180.0,
            'upper': 180.0,
            'connection_state': 'stale',
            'age_sec': 2.0,
        },
    }]

    with pytest.raises(ValueError, match='최신 모터 피드백'):
        node._pickup_reference_for_group_locked(group)

    assert node._pickup_reached(-2.0, 2.0, 0.0, 0.1) is True
    assert node._pickup_reached(None, 2.0, 0.0, 0.1) is False


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


def test_select_off_requests_motor_hold_and_retries_fader_zero():
    node = parking_node()

    node._deactivate_control_channel_locked(0)

    assert node._control_enabled[0] is False
    assert node._fader_parking[0] is True
    assert node._pending_fader_positions[0] == 0
    assert node._pending_motor_requests == {}
    payload = json.loads(node._motor_request_publisher.messages[0].data)
    assert payload['hold_axes'] == [2, 3]

    # Simulate the bridge dropping the first zero command while the last hand
    # movement is still active. SELECT OFF must retry after release.
    started = node._fader_park_last_command_at[0]
    node._pending_fader_positions[0] = None
    node._fader_moving[0] = True
    node._update_fader_parking_locked(0, 7000, started + 0.2)
    assert node._pending_fader_positions[0] is None

    node._fader_moving[0] = False
    node._update_fader_parking_locked(0, 7000, started + 0.3)
    assert node._pending_fader_positions[0] == 0


def playback_lock_node():
    node = parking_node()
    node._lock = threading.Lock()
    node._project_id = 'project-1'
    node._execution_context = {'project_generation': 4}
    node._selected_mapping_file_id = 'mapping.yaml'
    node._run_mapping_file_id = ''
    node._preferred_mapping_file_id = 'mapping.yaml'
    node._studio_select_locked = False
    node._btn3 = [False] * MIDI_CHANNEL_COUNT
    node._previous_btn3 = [False] * MIDI_CHANNEL_COUNT
    node._motion_run_state = 'idle'
    node._motion_run_request_source = ''
    node._motion_studio_state = 'idle'
    node._playback_phase = 'idle'
    node._playback_follow_enabled = [False] * MIDI_CHANNEL_COUNT
    node._playback_follow_targets = [None] * MIDI_CHANNEL_COUNT
    node._playback_follow_resume_not_before = [0.0] * MIDI_CHANNEL_COUNT
    return node


def playback_follow_node():
    node = playback_lock_node()
    node._banks = MidiBankManager()
    mappings = node._banks.active_bank()['mappings']
    mappings[0]['motion_id'] = '1-1'
    node._banks.update_bank('bank_1', mappings=mappings)
    node._execution_context_ready = True
    node._raw_channels = [8000] * MIDI_CHANNEL_COUNT
    node._observed_raw_channels = [8000] * MIDI_CHANNEL_COUNT
    node._channels = [8000.0] * MIDI_CHANNEL_COUNT
    node._filter_stage1 = [8000.0] * MIDI_CHANNEL_COUNT
    node._filter_stage2 = [8000.0] * MIDI_CHANNEL_COUNT
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
    node._bank_file_dirty = False
    node._fader_input_generation = [0] * MIDI_CHANNEL_COUNT
    node._pending_fader_input_generations = [0] * MIDI_CHANNEL_COUNT
    node._last_select_toggle_at = [0.0] * MIDI_CHANNEL_COUNT
    node._last_received_monotonic = None
    node._last_received_wall = None
    node._pickup_pending = [False] * MIDI_CHANNEL_COUNT
    node._pickup_reference_motion = [None] * MIDI_CHANNEL_COUNT
    node._pickup_previous_motion = [None] * MIDI_CHANNEL_COUNT
    node._pickup_reference_source = [''] * MIDI_CHANNEL_COUNT
    node._source_motion_values = {'1-1': 0.0}
    node._source_motion_value_stamps = {'1-1': 1.0}
    node._source_motion_value_context = ('project-1', 4)
    row = {
        'motor_axis': 2,
        'motion_lower_deg': -20,
        'motion_upper_deg': 20,
        'reference_position_deg': 0,
        'gear_ratio': 1,
        'scale': 1,
    }
    node._latest_motion_state = {'motors': [{
        'controller_index': 2,
        'position_deg': 0.0,
        'lower': -180.0,
        'upper': 180.0,
    }]}
    node._axis_registry = SimpleNamespace(
        motor_axis=lambda motion_id: 2 if motion_id == '1-1' else None,
        mapping=lambda motion_id: row if motion_id == '1-1' else None,
        file_id='mapping.yaml',
    )
    return node


def midi_message(*, select=False, touched=False, value=0):
    return SimpleNamespace(
        channel=[value] + [0] * (MIDI_CHANNEL_COUNT - 1),
        touch=[touched] + [False] * (MIDI_CHANNEL_COUNT - 1),
        dial=[0] * MIDI_CHANNEL_COUNT,
        btn0=[False] * MIDI_CHANNEL_COUNT,
        btn1=[False] * MIDI_CHANNEL_COUNT,
        btn2=[False] * MIDI_CHANNEL_COUNT,
        btn3=[select] + [False] * (MIDI_CHANNEL_COUNT - 1),
    )


def test_motion_playback_start_releases_owner_without_locking_select():
    node = playback_lock_node()
    running = {
        'state': 'running',
        'project_id': 'project-1',
        'mapping_file_id': 'mapping.yaml',
        'execution_context': {'project_generation': 4},
    }

    node._motion_run_status_callback(
        SimpleNamespace(data=json.dumps(running))
    )

    assert node._playback_phase == 'playing'
    assert node._select_lock_reason_locked() == ''
    assert node._control_enabled[0] is False
    assert node._fader_parking[0] is True
    assert node._pending_fader_positions[0] == 0
    # Playback already owns the robot motor, so SELECT release must not send
    # a competing MIDI hold request.
    assert node._motor_request_publisher.messages == []

    running['state'] = 'completed'
    node._motion_run_status_callback(
        SimpleNamespace(data=json.dumps(running))
    )
    assert node._playback_phase == 'idle'
    assert node._select_lock_reason_locked() == ''


def test_layer_initial_move_locks_select_but_recording_allows_select():
    node = playback_lock_node()
    status = {
        'state': 'initializing',
        'execution_context': {
            'project_id': 'project-1',
            'project_generation': 4,
        },
    }

    node._motion_studio_status_callback(
        SimpleNamespace(data=json.dumps(status))
    )

    assert node._playback_phase == 'initializing'
    assert node._select_lock_reason_locked() == '초기 위치 이동 중'
    assert node._control_enabled[0] is False
    assert node._motor_request_publisher.messages == []

    status['state'] = 'recording'
    node._motion_studio_status_callback(
        SimpleNamespace(data=json.dumps(status))
    )
    assert node._playback_phase == 'idle'
    assert node._select_lock_reason_locked() == ''


def test_initial_move_blocks_select_and_preview_playback_allows_read_only_follow():
    node = playback_follow_node()
    studio = {
        'state': 'initializing',
        'execution_context': {
            'project_id': 'project-1',
            'project_generation': 4,
        },
    }
    run = {
        'state': 'initializing',
        'project_id': 'project-1',
        'mapping_file_id': 'mapping.yaml',
        'request_source': 'motion_studio',
        'execution_context': {'project_generation': 4},
    }
    node._motion_studio_status_callback(
        SimpleNamespace(data=json.dumps(studio))
    )
    node._motion_run_status_callback(SimpleNamespace(data=json.dumps(run)))
    node._midi_callback(midi_message(select=True, value=8000))

    assert node._playback_phase == 'initializing'
    assert node._select_lock_reason_locked() == '초기 위치 이동 중'
    assert node._control_enabled[0] is False
    assert node._playback_follow_enabled[0] is False

    node._midi_callback(midi_message(select=False, value=8000))
    run['state'] = 'running'
    node._motion_run_status_callback(SimpleNamespace(data=json.dumps(run)))
    assert node._playback_phase == 'initializing'
    studio['state'] = 'playing'
    node._motion_studio_status_callback(
        SimpleNamespace(data=json.dumps(studio))
    )
    node._last_select_toggle_at[0] = time.monotonic() - 1.0
    node._midi_callback(midi_message(select=True, value=8000))

    assert node._playback_phase == 'playing'
    assert node._select_lock_reason_locked() == ''
    assert node._playback_follow_enabled[0] is True
    assert node._control_enabled[0] is False
    assert node._pending_fader_positions[0] == round(MIDI_VALUE_MAX / 2)
    assert node._pending_motor_requests == {}
    assert node._motor_request_publisher.messages == []


def test_playback_touch_never_commands_motor_and_release_resumes_latest_target():
    node = playback_follow_node()
    node._motion_run_state = 'running'
    node._update_playback_phase_locked()
    node._set_playback_follow_enabled_locked(
        0, True, node._banks.active_bank()['mappings'][0]
    )
    node._pending_fader_positions[0] = None

    node._input_state_callback(SimpleNamespace(data=json.dumps({
        'physical_touch': [True],
        'fader_moving': [True],
        'fader_syncing': [False],
        'fader_input_generation': [2],
    })))
    node._midi_callback(midi_message(touched=True, value=12000))
    node._motion_value_callback(SimpleNamespace(data=json.dumps({
        'project_id': 'project-1',
        'project_generation': 4,
        'stamp': 2.0,
        'values': {'1-1': 10.0},
    })))

    assert node._pending_motor_requests == {}
    assert node._pending_fader_positions[0] is None
    assert node._playback_follow_targets[0] == round(MIDI_VALUE_MAX * 0.75)

    node._input_state_callback(SimpleNamespace(data=json.dumps({
        'physical_touch': [False],
        'fader_moving': [False],
        'fader_syncing': [False],
        'fader_input_generation': [2],
    })))
    assert node._pending_fader_positions[0] is None
    node._playback_follow_resume_not_before[0] = time.monotonic() - 0.01
    node._service_playback_follow_locked(time.monotonic())

    assert node._pending_fader_positions[0] == round(MIDI_VALUE_MAX * 0.75)
    assert node._control_enabled[0] is False
    assert node._pending_motor_requests == {}


def test_playback_follow_keeps_streaming_while_bridge_settles_previous_command():
    node = playback_follow_node()
    node._motion_run_state = 'running'
    node._update_playback_phase_locked()
    node._set_playback_follow_enabled_locked(
        0, True, node._banks.active_bank()['mappings'][0]
    )
    node._pending_fader_positions[0] = None
    node._bridge_fader_syncing[0] = True
    node._source_motion_values['1-1'] = 5.0

    node._service_playback_follow_locked(time.monotonic())

    assert node._pending_fader_positions[0] == round(MIDI_VALUE_MAX * 0.625)
    assert node._motor_command_state[0] == 'playback_follow'
    assert node._pending_motor_requests == {}


def test_general_and_preview_playback_end_force_follow_select_off():
    node = playback_follow_node()
    run = {
        'state': 'running',
        'project_id': 'project-1',
        'mapping_file_id': 'mapping.yaml',
        'execution_context': {'project_generation': 4},
    }
    node._motion_run_status_callback(SimpleNamespace(data=json.dumps(run)))
    node._set_playback_follow_enabled_locked(
        0, True, node._banks.active_bank()['mappings'][0]
    )
    run['state'] = 'completed'
    node._motion_run_status_callback(SimpleNamespace(data=json.dumps(run)))
    assert node._playback_phase == 'idle'
    assert node._playback_follow_enabled == [False] * MIDI_CHANNEL_COUNT
    assert node._pending_fader_positions[0] == 0

    studio = {
        'state': 'playing',
        'execution_context': {
            'project_id': 'project-1',
            'project_generation': 4,
        },
    }
    run['state'] = 'running'
    run['request_source'] = 'motion_studio'
    node._motion_run_status_callback(SimpleNamespace(data=json.dumps(run)))
    node._motion_studio_status_callback(
        SimpleNamespace(data=json.dumps(studio))
    )
    node._set_playback_follow_enabled_locked(
        0, True, node._banks.active_bank()['mappings'][0]
    )
    run['state'] = 'stopped'
    node._motion_run_status_callback(SimpleNamespace(data=json.dumps(run)))
    assert node._playback_phase == 'idle'
    assert node._playback_follow_enabled == [False] * MIDI_CHANNEL_COUNT
    assert node._pending_fader_positions[0] == 0


def test_playback_status_isolated_between_projects():
    node = playback_follow_node()
    other = {
        'state': 'running',
        'project_id': 'project-2',
        'mapping_file_id': 'other.yaml',
        'execution_context': {'project_generation': 4},
    }
    node._motion_run_status_callback(SimpleNamespace(data=json.dumps(other)))
    assert node._playback_phase == 'idle'

    other['project_id'] = 'project-1'
    other['execution_context']['project_generation'] = 3
    node._motion_run_status_callback(SimpleNamespace(data=json.dumps(other)))
    assert node._playback_phase == 'idle'

    other['execution_context']['project_generation'] = 4
    node._motion_run_status_callback(SimpleNamespace(data=json.dumps(other)))
    assert node._playback_phase == 'playing'


def test_fader_parking_waits_for_hand_release_retries_zero_and_confirms_arrival():
    node = parking_node()
    node._start_fader_parking_locked(0, time.monotonic())
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


def test_failed_normal_fader_parking_times_out_and_allows_select_retry():
    node = parking_node()
    node._studio_select_locked = False
    node._start_fader_parking_locked(0, time.monotonic())
    started = node._fader_park_started_at[0]

    was_parking = node._update_fader_parking_locked(
        0, 2692, started + 2.1
    )

    assert was_parking is False
    assert node._fader_parking[0] is False
    assert node._pending_fader_positions[0] is None
    assert node._motor_command_state[0] == 'fader_park_failed'


def test_select_off_nonzero_input_resends_zero_once_and_latches_failure():
    node = playback_follow_node()
    node._control_enabled[0] = False
    node._raw_channels[0] = 0
    node._channels[0] = 0.0
    node._filter_stage1[0] = 0.0
    node._filter_stage2[0] = 0.0
    node._pending_motor_requests = {}
    started = time.monotonic()
    node._start_fader_parking_locked(0, started)
    node._update_fader_parking_locked(0, 0, started + 0.1)

    node._midi_callback(midi_message(touched=True, value=2600))

    assert node._fader_parking[0] is False
    assert node._pending_fader_positions[0] == 0
    assert node._motor_command_state[0] == 'fader_park_failed'
    assert '0 재명령 후 정지' in node._motor_command_message[0]

    node._pending_fader_positions[0] = None
    node._midi_callback(midi_message(touched=True, value=2600))

    assert node._fader_parking[0] is False
    assert node._pending_fader_positions[0] is None
    assert node._motor_command_state[0] == 'fader_park_failed'


def test_studio_fader_parking_never_bypasses_physical_zero_requirement():
    node = parking_node()
    node._studio_select_locked = True
    node._start_fader_parking_locked(0, time.monotonic())
    started = node._fader_park_started_at[0]

    was_parking = node._update_fader_parking_locked(
        0, 2692, started + 20.0
    )

    assert was_parking is True
    assert node._fader_parking[0] is True


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
    node._start_fader_parking_locked(0, time.monotonic())
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
    node._midi_callback(message(touch=True, value=round(MIDI_VALUE_MAX / 2)))
    assert node._pickup_pending[0] is False
    assert node._pending_motor_requests == {}
    node._midi_callback(message(touch=True, value=MIDI_VALUE_MAX))

    assert node._pending_motor_requests == {}
    request = json.loads(node._motor_request_publisher.messages[-1].data)
    targets = request['targets']
    assert {target['axis'] for target in targets} == {2, 3}
    assert {target['motion_id'] for target in targets} == {'1-1', '1-2'}
    assert {target['motion_deg'] for target in targets} == {20.0}
    assert request['atomic_channels'] == [0]


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


def test_studio_recording_prepare_skips_linked_channel_with_mismatched_ranges():
    mappings = [{
        'channel': channel,
        'motion_id': (
            '1-1' if channel == 0
            else '1-2' if channel == 1
            else f'9-{channel + 1}'
        ),
        'linked_motion_ids': ['1-3'] if channel == 1 else [],
        'enabled': channel < 2,
        'min_percent': 0.0,
        'max_percent': 100.0,
        'reversed': False,
        'filter_level': 0,
    } for channel in range(MIDI_CHANNEL_COUNT)]

    class Banks:
        @staticmethod
        def snapshot():
            return {'active_bank': {'mappings': mappings}}

    rows = {
        '1-1': {'motion_lower_deg': -20.0, 'motion_upper_deg': 20.0},
        '1-2': {'motion_lower_deg': -10.0, 'motion_upper_deg': 15.0},
        '1-3': {'motion_lower_deg': -15.0, 'motion_upper_deg': 10.0},
    }
    axes = {'1-1': 0, '1-2': 1, '1-3': 2}

    class Registry:
        @staticmethod
        def refresh(_preferred=None, _motion_state=None):
            return None

        @staticmethod
        def mapping(motion_id):
            return rows.get(motion_id)

        @staticmethod
        def motor_axis(motion_id):
            return axes.get(motion_id)

    node = parking_node()
    node._banks = Banks()
    node._axis_registry = Registry()
    node._preferred_mapping_file_id = ''
    node._last_axis_registry_refresh = 0.0
    node._latest_motion_state = {
        'motors': [
            {
                'controller_index': axis,
                'position_deg': 0.0,
                'lower': -180.0,
                'upper': 180.0,
            }
            for axis in range(3)
        ]
    }
    node._studio_select_locked = False
    node._studio_zero_fader_targets = [0] * MIDI_CHANNEL_COUNT
    node._filter_last_at = [None] * MIDI_CHANNEL_COUNT
    node._last_feedback = [None] * MIDI_CHANNEL_COUNT
    node._btn3 = [False] * MIDI_CHANNEL_COUNT
    node._previous_btn3 = [False] * MIDI_CHANNEL_COUNT

    result = node._prepare_studio_recording_locked()

    assert result['errors'] == []
    assert result['unavailable_channels'] == [{
        'channel': 2,
        'motion_ids': ['1-2', '1-3'],
        'message': '연동 Motion ID의 모션 범위가 서로 다릅니다',
    }]
    assert node._studio_select_locked is True
    assert node._pending_fader_positions == [0] * MIDI_CHANNEL_COUNT


def test_connection_state_keeps_midi_power_reconnect_timestamps():
    node = MidiControlNode.__new__(MidiControlNode)
    node._lock = threading.Lock()
    node._device_connected = True
    node._device_connection_message = ''
    node._device_last_connected_at = None
    node._device_last_disconnected_at = None
    node._device_last_power_reconnected_at = None
    node._device_connection_count = 0
    node._device_power_reconnect_count = 0
    message = SimpleNamespace(data=json.dumps({
        'connected': True,
        'message': 'X-Touch connected',
        'last_connected_at': 1234.5,
        'last_disconnected_at': 1200.25,
        'last_power_reconnected_at': 1234.5,
        'connection_count': 3,
        'power_reconnect_count': 2,
    }))

    node._connection_state_callback(message)

    assert node._device_last_connected_at == 1234.5
    assert node._device_last_disconnected_at == 1200.25
    assert node._device_last_power_reconnected_at == 1234.5
    assert node._device_connection_count == 3
    assert node._device_power_reconnect_count == 2


def test_device_reconnect_parks_every_select_off_fader_at_zero():
    node = MidiControlNode.__new__(MidiControlNode)
    node._lock = threading.Lock()
    node._device_connected = False
    node._device_connection_message = ''
    node._device_last_connected_at = None
    node._device_last_disconnected_at = None
    node._device_last_power_reconnected_at = None
    node._device_connection_count = 0
    node._device_power_reconnect_count = 0
    node._fader_input_generation = list(range(MIDI_CHANNEL_COUNT))
    node._pending_fader_positions = [None] * MIDI_CHANNEL_COUNT
    node._pending_fader_input_generations = [0] * MIDI_CHANNEL_COUNT
    reset_calls = []
    parking_calls = []

    def reset_runtime_controls():
        reset_calls.append(True)
        node._pending_fader_positions = [0] * MIDI_CHANNEL_COUNT
        node._pending_fader_input_generations = list(
            node._fader_input_generation
        )

    node._reset_runtime_controls_locked = reset_runtime_controls

    def start_fader_parking(channel, _now):
        parking_calls.append(channel)
        node._pending_fader_positions[channel] = 0

    node._start_fader_parking_locked = start_fader_parking
    node._connection_state_callback(SimpleNamespace(data=json.dumps({
        'connected': True,
        'message': 'X-Touch connected',
        'connection_count': 1,
        'power_reconnect_count': 0,
    })))

    assert reset_calls == [True]
    assert parking_calls == list(range(MIDI_CHANNEL_COUNT))
    assert node._pending_fader_positions == [0] * MIDI_CHANNEL_COUNT
    assert node._pending_fader_input_generations == list(range(MIDI_CHANNEL_COUNT))


def test_device_disconnect_does_not_leave_undeliverable_zero_commands():
    node = MidiControlNode.__new__(MidiControlNode)
    node._lock = threading.Lock()
    node._device_connected = True
    node._device_connection_message = ''
    node._device_last_connected_at = None
    node._device_last_disconnected_at = None
    node._device_last_power_reconnected_at = None
    node._device_connection_count = 1
    node._device_power_reconnect_count = 0
    node._fader_input_generation = list(range(MIDI_CHANNEL_COUNT))
    node._pending_fader_positions = [None] * MIDI_CHANNEL_COUNT
    node._pending_fader_input_generations = [0] * MIDI_CHANNEL_COUNT
    node._touch = [True] * MIDI_CHANNEL_COUNT
    node._physical_touch = [True] * MIDI_CHANNEL_COUNT
    node._fader_moving = [True] * MIDI_CHANNEL_COUNT
    node._bridge_fader_syncing = [True] * MIDI_CHANNEL_COUNT

    def reset_runtime_controls():
        node._pending_fader_positions = [0] * MIDI_CHANNEL_COUNT

    node._reset_runtime_controls_locked = reset_runtime_controls
    node._connection_state_callback(SimpleNamespace(data=json.dumps({
        'connected': False,
        'message': 'X-Touch disconnected',
        'connection_count': 1,
        'power_reconnect_count': 0,
    })))

    assert node._pending_fader_positions == [None] * MIDI_CHANNEL_COUNT
    assert node._touch == [False] * MIDI_CHANNEL_COUNT
    assert node._physical_touch == [False] * MIDI_CHANNEL_COUNT
    assert node._fader_moving == [False] * MIDI_CHANNEL_COUNT
    assert node._bridge_fader_syncing == [False] * MIDI_CHANNEL_COUNT


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
    # With no accepted logical value, SELECT derives Motion 20 from actual
    # motor position 30 and reference position 10.
    node._latest_motion_state['motors'][0]['position_deg'] = 30.0
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
    assert node._pending_fader_positions[0] == MIDI_VALUE_MAX
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

    # A rapid re-press cancels normal SELECT-off parking and performs a fresh
    # pickup. The user must not lose this press merely because the motorized
    # fader has not reached zero.
    node._last_select_toggle_at[0] = time.monotonic() - 1.0
    node._midi_callback(message(select=True, dial=4, value=8000))
    assert node._control_enabled[0] is True
    assert node._fader_parking[0] is False
    assert node._pending_fader_positions[0] == MIDI_VALUE_MAX

    node._midi_callback(message(select=False, dial=4, value=8000))
    node._last_select_toggle_at[0] = time.monotonic() - 1.0
    node._midi_callback(message(select=True, dial=4, value=8000))
    assert node._control_enabled[0] is False

    matched.clear()
    node._last_select_toggle_at[0] = time.monotonic() - 1.0
    node._midi_callback(message(select=True, dial=4))
    assert node._control_enabled[0] is False
    assert node._pending_fader_positions[0] == 0


def test_hand_movement_commands_only_after_soft_takeover_pickup():
    node = MidiControlNode.__new__(MidiControlNode)
    node._lock = threading.Lock()
    node._banks = MidiBankManager()
    mappings = node._banks.active_bank()['mappings']
    mappings[0]['motion_id'] = '1-1'
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

    def message(*, select=False, touched=False, value=0):
        return SimpleNamespace(
            channel=[value] + [0] * (MIDI_CHANNEL_COUNT - 1),
            touch=[touched] + [False] * (MIDI_CHANNEL_COUNT - 1),
            dial=[0] * MIDI_CHANNEL_COUNT,
            btn0=[False] * MIDI_CHANNEL_COUNT,
            btn1=[False] * MIDI_CHANNEL_COUNT,
            btn2=[False] * MIDI_CHANNEL_COUNT,
            btn3=[select] + [False] * (MIDI_CHANNEL_COUNT - 1),
        )

    node._midi_callback(message(select=True))
    assert node._awaiting_fader_sync[0] is True
    node._midi_callback(message(select=False, touched=True, value=12000))

    assert node._awaiting_fader_sync[0] is False
    assert node._pending_fader_positions[0] is None
    assert node._raw_channels[0] == 12000
    assert node._pickup_pending[0] is True
    assert node._motor_follow_active[0] is False
    assert (0, 2) not in node._pending_motor_requests

    node._midi_callback(message(select=False, touched=True, value=8192))
    assert node._pickup_pending[0] is False
    assert node._motor_follow_active[0] is False
    assert (0, 2) not in node._pending_motor_requests

    node._midi_callback(message(select=False, touched=True, value=9000))
    assert node._motor_follow_active[0] is True
    assert node._pending_motor_requests == {}
    assert len(node._motor_request_publisher.messages) == 1
    request = json.loads(node._motor_request_publisher.messages[0].data)
    assert request['targets'][0]['axis'] == 2
    assert request['targets'][0]['motion_id'] == '1-1'
    assert node._motor_command_message[0] == '다축 모터 위치 명령 전달 중'


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
    # Logical motion -15 (12.5%) cannot be represented by the incoming
    # 50..100% line. The actual motor position is intentionally unrelated.
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
    node._current_motion_values = {'1-1': -15.0}

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
    node._project_id = 'project-1'
    node._selected_mapping_file_id = ''
    node._run_mapping_file_id = ''
    node._preferred_mapping_file_id = ''
    node._bank_config_file = None
    node._bank_file_loaded = False
    node._bank_file_dirty = False
    node._execution_context = {'project_generation': 1}
    selected = tmp_path / 'selected.yaml'
    selected.write_text('mappings: []\n', encoding='utf-8')

    node._motion_mapping_response_callback(SimpleNamespace(
        data='{"success": true, "project_generation": 1, "file": {"id": "selected.yaml"}}'
    ))
    assert node._preferred_mapping_file_id == 'selected.yaml'

    node._motion_run_status_callback(SimpleNamespace(
        data='{"project_id": "other-project", "mapping_file_id": "other.yaml"}'
    ))
    assert node._preferred_mapping_file_id == 'selected.yaml'
    assert node._bank_config_file == selected

    node._motion_run_status_callback(SimpleNamespace(
        data=(
            '{"project_id": "project-1", "mapping_file_id": "running.yaml", '
            '"execution_context": {"project_generation": 1}}'
        )
    ))
    assert node._preferred_mapping_file_id == 'running.yaml'
    assert node._bank_config_file == selected
    assert node._requested_mapping_file({
        'config_file': '/mock_workspace/config/active_motor_config.yaml'
    }) == selected

    node._motion_run_status_callback(SimpleNamespace(
        data=(
            '{"project_id": "project-1", "mapping_file_id": "", '
            '"execution_context": {"project_generation": 1}}'
        )
    ))
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
