"""MIDI 화면 표현 · 현재 상태를 한 장의 사전으로 만든다.

`midi_control_node`에서 떼어냈다 · §6-41

읽기만 한다. 채널 8개의 매핑·필터·SELECT·Pickup·모터 명령 상태를 화면과 응답이
쓰는 형태로 모은다 · 판정하지 않고 이미 정해진 것을 옮겨 담는다.

노드의 `_lock` 아래에서 불린다 · 호출자가 잠근다.

§5 목표안의 `MidiDecoder`는 아직이다. 입력 해석이 `_midi_callback`의 459줄짜리
채널 루프 안에서 파킹·SELECT 판정과 엇갈려 있어, 루프를 먼저 국면별로 가르지
않으면 상태 16개를 검증 직후의 핫패스에서 끌어내는 일이 된다 · 별도 항목으로 둔다.
"""

from __future__ import annotations

import hashlib
import time
from typing import Any, Dict, List

from midi_control.bank_manager import (
    FILTER_LEVEL_MAX,
    MIDI_CHANNEL_COUNT,
    MIDI_VALUE_MAX,
    MIDI_VALUE_MIN,
    mapping_motion_ids,
)
from midi_control.motion_value_map import (
    FILTER_MAX_TIME_CONSTANT_SEC,
    FILTER_ORDER,
    _finite_float,
    motion_value_display,
    motion_value_from_output,
    raw_fader_for_motion,
    safe_motion_range_for_group,
)


def build_snapshot(node: Any) -> Dict[str, Any]:
    now_monotonic = time.monotonic()
    with node._lock:
        node._faders._ensure_fader_parking_state_locked()
        node._service_playback_follow_locked(now_monotonic)
        node._refresh_axis_registry_locked(now_monotonic)
        if node._execution_context_ready and node._bank_config_file is not None:
            expected_sha = str(node._execution_context.get('mapping_sha256') or '')
            try:
                actual_sha = hashlib.sha256(
                    node._bank_config_file.read_bytes()
                ).hexdigest()
            except OSError:
                actual_sha = ''
            if not expected_sha or actual_sha != expected_sha:
                node._execution_context_ready = False
                node._reset_bank_change_state_locked()
        last_monotonic = node._last_received_monotonic
        last_wall = node._last_received_wall
        physical_input_monotonic = node._last_physical_input_monotonic
        physical_input_wall = node._last_physical_input_wall
        device_connected = node._device_connected
        surface_owned = bool(getattr(node, '_surface_owned', True))
        surface_remote = bool(getattr(node, '_surface_remote', False))
        device_connection_message = node._device_connection_message
        device_last_connected_at = node._device_last_connected_at
        device_last_disconnected_at = node._device_last_disconnected_at
        device_last_power_reconnected_at = (
            node._device_last_power_reconnected_at
        )
        device_connection_count = node._device_connection_count
        device_power_reconnect_count = node._device_power_reconnect_count
        raw_values = list(node._raw_channels)
        observed_raw_values = list(getattr(
            node, '_observed_raw_channels', node._raw_channels
        ))
        filtered_values = list(node._channels)
        touch = list(node._touch)
        physical_touch = list(node._physical_touch)
        fader_moving = list(node._fader_moving)
        bridge_fader_syncing = list(node._bridge_fader_syncing)
        node._faders._ensure_fader_input_generation_locked()
        fader_input_generation = list(node._faders.input_generation)
        dial = list(node._dial)
        buttons = [
            [node._btn0[index], node._btn1[index], node._btn2[index], node._btn3[index]]
            for index in range(MIDI_CHANNEL_COUNT)
        ]
        confirmed = list(node._confirmed)
        control_enabled = list(node._control_enabled)
        node._ensure_playback_follow_state_locked()
        playback_follow_enabled = list(node._playback_follow_enabled)
        select_enabled = [
            bool(control_enabled[channel] or playback_follow_enabled[channel])
            for channel in range(MIDI_CHANNEL_COUNT)
        ]
        playback_phase = node._playback_phase
        motion_value_mode = list(node._motor_angle_mode)
        motion_state = dict(node._latest_motion_state)
        source_context = (
            str(node._project_id or ''),
            int(node._execution_context.get('project_generation') or 0),
        )
        source_motion_values = (
            dict(getattr(node, '_source_motion_values', {}))
            if getattr(node, '_source_motion_value_context', ('', 0))
            == source_context
            else {}
        )
        bank_state = node._banks.snapshot()
        bank_export = node._banks.export_state()
        mappings = bank_state['active_bank']['mappings']
        motion_id_groups = [mapping_motion_ids(mapping) for mapping in mappings]
        matched_axis_groups = [
            [node._axis_registry.motor_axis(motion_id) for motion_id in motion_ids]
            for motion_ids in motion_id_groups
        ]
        matched_row_groups = [
            [node._axis_registry.mapping(motion_id) for motion_id in motion_ids]
            for motion_ids in motion_id_groups
        ]
        axis_groups_matched = []
        group_valid = []
        group_messages = []
        group_safe_ranges: List[tuple[float, float] | None] = []
        for mapping, motion_ids, axes, rows in zip(
            mappings, motion_id_groups, matched_axis_groups, matched_row_groups
        ):
            matched = bool(axes) and all(axis is not None for axis in axes)
            matched = matched and all(row is not None for row in rows)
            valid = matched
            message = ''
            if not matched:
                missing = [
                    motion_id for motion_id, axis, row in zip(motion_ids, axes, rows)
                    if axis is None or row is None
                ]
                message = '모션축 매칭 없음: ' + ', '.join(missing)
            if matched:
                try:
                    group = node._mapping_group_locked(mapping)
                    safe_range = safe_motion_range_for_group(group)
                except ValueError as exc:
                    valid = False
                    message = str(exc)
                    safe_range = None
            else:
                safe_range = None
            axis_groups_matched.append(matched)
            group_valid.append(valid)
            group_messages.append(message)
            group_safe_ranges.append(safe_range)
        matched_axes = [axes[0] if axes else None for axes in matched_axis_groups]
        matched_rows = [rows[0] if rows else None for rows in matched_row_groups]
        # An activation error is a live condition. If the motor later moves
        # into this line's representable range (or the mapping is repaired),
        # clear the stale red/error state without requiring another SELECT.
        for channel, mapping in enumerate(mappings):
            if node._motor_command_state[channel] != 'activation_rejected':
                continue
            if (
                mapping.get('enabled') is False
                or not group_valid[channel]
            ):
                # A missing/disabled mapping is the current condition.
                # Do not keep a historical SELECT rejection that makes
                # duplicate Motion IDs display different states.
                node._motor_command_state[channel] = 'inactive'
                node._motor_command_message[channel] = ''
                continue
            try:
                group = node._mapping_group_locked(mapping)
                motion_value = node._logical_motion_value_for_group_locked(group)
                raw_fader_for_motion(
                    motion_value,
                    group[0]['row'],
                    mapping,
                    safe_motion_range_for_group(group),
                )
            except ValueError:
                continue
            node._motor_command_state[channel] = 'inactive'
            node._motor_command_message[channel] = ''
        for channel, valid in enumerate(group_valid):
            if not valid:
                node._control_enabled[channel] = False
                node._motor_angle_mode[channel] = False
                control_enabled[channel] = False
                motion_value_mode[channel] = False
        for channel, mapping in enumerate(mappings):
            if control_enabled[channel]:
                node._final_output_values[channel] = node._filtered_output_14bit(
                    filtered_values[channel], mapping
                )
        final_output_values = list(node._final_output_values)
        motor_command_states = list(node._motor_command_state)
        motor_command_messages = list(node._motor_command_message)
        awaiting_fader_sync = list(node._faders.awaiting_sync)
        fader_sync_targets = list(node._faders.sync_targets)
        fader_parking = list(node._faders.parking)
        select_lock_reason = node._select_lock_reason_locked()
        node._ensure_approved_command_state_locked()
        node._ensure_current_motion_state_locked()
        node._pickup._ensure_pickup_state_locked()
        current_motion_values = dict(node._current_motion_values)
        pickup_pending = list(node._pickup.pending)
        pickup_reference_motion = list(node._pickup.reference_motion)
        pickup_reference_source = list(node._pickup.reference_source)
        approved_motion_values = [
            dict(values) for values in node._approved_motion_values
        ]
        approved_motor_targets = [
            dict(values) for values in node._approved_motor_targets
        ]
        mapping_file_id = node._axis_registry.file_id
    bridge_age_sec = (
        None if last_monotonic is None else max(0.0, now_monotonic - last_monotonic)
    )
    age_sec = (
        None
        if physical_input_monotonic is None
        else max(0.0, now_monotonic - physical_input_monotonic)
    )
    connected = (
        device_connected
        and age_sec is not None
        and age_sec <= node.stale_timeout_sec
    )
    channels = []
    for channel, mapping in enumerate(mappings):
        raw_value = raw_values[channel]
        observed_raw_value = observed_raw_values[channel]
        display_raw_value = (
            int(fader_sync_targets[channel])
            if (
                awaiting_fader_sync[channel]
                and fader_sync_targets[channel] is not None
            )
            else observed_raw_value
        )
        filtered_value = filtered_values[channel]
        final_output_value = final_output_values[channel]
        motor_axis = matched_axes[channel]
        motor_angle = (
            node._position_for_axis(motion_state, motor_axis)
            if motor_axis is not None else None
        )
        row = matched_rows[channel]
        safe_range = group_safe_ranges[channel]
        motion_ids = motion_id_groups[channel]
        group_axes = matched_axis_groups[channel]
        try:
            requested_motion_value = (
                motion_value_from_output(final_output_value, row, safe_range)
                if row is not None and safe_range is not None else None
            )
        except ValueError:
            requested_motion_value = None
        # SELECT may be enabled before any logical source topic has been
        # published. During Pickup the motor-feedback-derived reference is
        # the authoritative current value; showing the old raw/final output
        # here produced values such as -12° while the actual axis was 0°.
        pickup_display_value = (
            _finite_float(pickup_reference_motion[channel])
            if control_enabled[channel] and pickup_pending[channel]
            else None
        )
        displayed_motion_value, motion_display_text, motion_display_status = (
            motion_value_display(
                motion_ids,
                source_motion_values,
                control_enabled=control_enabled[channel],
                estimated_value=(
                    pickup_display_value
                    if pickup_display_value is not None
                    else requested_motion_value
                ),
            )
        )
        approved_values = approved_motion_values[channel]
        approved_targets = approved_motor_targets[channel]
        approved_complete = bool(motion_ids) and all(
            motion_id in approved_values for motion_id in motion_ids
        )
        logical_values = {
            motion_id: value
            for motion_id in motion_ids
            if (value := _finite_float(current_motion_values.get(motion_id)))
            is not None
        }
        motion_value = (
            sum(logical_values.values()) / len(logical_values)
            if motion_ids and len(logical_values) == len(motion_ids)
            else None
        )
        motor_target = (
            approved_targets.get(int(motor_axis))
            if motor_axis is not None else None
        )
        channels.append({
            **mapping,
            'channel_number': channel + 1,
            'raw_value': raw_value,
            'observed_raw_value': observed_raw_value,
            # The LCD should show the SELECT pickup/park target as soon as
            # it is requested. Keep observed_raw_value unchanged so
            # physical-arrival and retry decisions still use device state.
            'display_raw_value': display_raw_value,
            'filtered_value': round(filtered_value, 6),
            'final_output_value': round(final_output_value, 6),
            'raw_normalized': round(raw_value / MIDI_VALUE_MAX, 6),
            'filtered_normalized': round(filtered_value / MIDI_VALUE_MAX, 6),
            'normalized': round(final_output_value / MIDI_VALUE_MAX, 6),
            'value_confirmed': confirmed[channel],
            'touch': touch[channel],
            'physical_touch': physical_touch[channel],
            'fader_moving': fader_moving[channel],
            'fader_input_generation': fader_input_generation[channel],
            'input_valid': touch[channel],
            'dial': dial[channel],
            'buttons': buttons[channel],
            'control_enabled': control_enabled[channel],
            'select_enabled': select_enabled[channel],
            'playback_follow_enabled': playback_follow_enabled[channel],
            'pickup_pending': pickup_pending[channel],
            'pickup_complete': bool(
                control_enabled[channel] and not pickup_pending[channel]
            ),
            'pickup_reference_motion_deg': pickup_reference_motion[channel],
            'pickup_reference_source': pickup_reference_source[channel],
            'motion_ids': motion_ids,
            'motion_axis_matched': axis_groups_matched[channel],
            'motion_group_valid': group_valid[channel],
            'motion_group_message': group_messages[channel],
            'matched_motor_axis': motor_axis,
            'matched_motor_axes': group_axes,
            'display_motion_value': motion_value_mode[channel],
            'motor_angle_deg': None if motor_angle is None else round(motor_angle, 6),
            'source_motion_value_deg': (
                None
                if motion_display_status != 'confirmed'
                else round(float(displayed_motion_value), 6)
            ),
            'displayed_motion_value_deg': (
                None
                if displayed_motion_value is None
                else round(displayed_motion_value, 6)
            ),
            'motion_value_display_text': motion_display_text,
            'motion_value_display_status': motion_display_status,
            'motion_value_deg': None if motion_value is None else round(motion_value, 6),
            'requested_motion_value_deg': (
                None
                if requested_motion_value is None
                else round(requested_motion_value, 6)
            ),
            'safe_motion_lower_deg': (
                None if safe_range is None else round(safe_range[0], 6)
            ),
            'safe_motion_upper_deg': (
                None if safe_range is None else round(safe_range[1], 6)
            ),
            'motion_command_valid': bool(
                control_enabled[channel]
                and group_valid[channel]
                and approved_complete
                and motor_command_states[channel] not in {
                    'rejected', 'activation_rejected'
                }
            ),
            'motion_values_deg': {
                motion_id: round(logical_values[motion_id], 6)
                for motion_id in motion_ids
                if motion_id in logical_values
            },
            'motor_target_deg': None if motor_target is None else round(motor_target, 6),
            'fader_syncing': (
                awaiting_fader_sync[channel]
                or bridge_fader_syncing[channel]
                or fader_parking[channel]
            ),
            'fader_parking': fader_parking[channel],
            'motor_command_state': motor_command_states[channel],
            'motor_command_message': motor_command_messages[channel],
        })
    return {
        'success': True,
        'node_state': 'ok',
        'project_id': node._project_id,
        'execution_context': {
            **node._execution_context,
            'ready': node._execution_context_ready,
        },
        'connected': connected,
        'device_connected': device_connected,
        # 왜 안 되는지 화면이 구분할 수 있어야 한다 · §6-94 · 장치가 빠진
        # 것과 다른 PC 가 쓰는 것은 다른 일이다
        'surface_owned': surface_owned,
        'surface_remote': surface_remote,
        'device_connection_message': device_connection_message,
        'device_last_connected_at': device_last_connected_at,
        'device_last_disconnected_at': device_last_disconnected_at,
        'device_last_power_reconnected_at': (
            device_last_power_reconnected_at
        ),
        'device_connection_count': device_connection_count,
        'device_power_reconnect_count': device_power_reconnect_count,
        'message': (
            'MIDI 데이터 수신 정상'
            if connected else (
                device_connection_message or 'MIDI 데이터 수신 대기'
            )
        ),
        'input_topic': node.input_topic,
        'last_received_at': physical_input_wall,
        'age_sec': None if age_sec is None else round(age_sec, 3),
        'bridge_publish_age_sec': (
            None if bridge_age_sec is None else round(bridge_age_sec, 3)
        ),
        'value_bits': 14,
        'value_min': MIDI_VALUE_MIN,
        'value_max': MIDI_VALUE_MAX,
        'unit': '14bit',
        'motor_output_enabled': node._execution_context_ready,
        'motor_output_path': 'motion_supervisor',
        'select_locked': bool(select_lock_reason),
        'select_lock_reason': select_lock_reason,
        'playback_phase': playback_phase,
        'motion_mapping_file_id': mapping_file_id,
        'touch_gated_input': True,
        'filter_order': FILTER_ORDER,
        'filter_level_min': 0,
        'filter_level_max': FILTER_LEVEL_MAX,
        'filter_max_time_constant_sec': FILTER_MAX_TIME_CONSTANT_SEC,
        'bank_storage': 'motion_mapping_yaml',
        'bank_persistent': node._bank_file_loaded and not node._bank_file_dirty,
        'bank_config_file': (
            str(node._bank_config_file) if node._bank_config_file is not None else ''
        ),
        'max_banks': bank_state['max_banks'],
        'active_bank_id': bank_state['active_bank_id'],
        'active_bank': bank_state['active_bank'],
        'banks': bank_state['banks'],
        'bank_state': bank_export,
        'channels': channels,
    }
