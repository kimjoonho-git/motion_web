import json

import pytest

from motion_studio.timeline import (
    final_export_layer,
    layer_conflicts,
    layer_transition_warnings,
    motion_file_text,
    recording_values,
    render_project,
)


def project():
    return {
        'project_id': 'demo',
        'name': 'Demo',
        'period_sec': 0.02,
        'layers': [
            {
                'layer_id': 'base',
                'enabled': True,
                'frames': [
                    {'frame': 1, 'time_sec': 0.02, 'values': {'1-1': 0.0, '1-2': 0.0}},
                    {'frame': 2, 'time_sec': 0.04, 'values': {'1-1': 2.0, '1-2': 4.0}},
                ],
            },
            {
                'layer_id': 'overdub',
                'enabled': True,
                'frames': [
                    {'frame': 1, 'time_sec': 0.02, 'values': {'1-2': 10.0}},
                    {'frame': 2, 'time_sec': 0.04, 'values': {'1-2': 12.0}},
                ],
            },
        ],
    }


def test_same_motion_id_time_overlap_is_reported_and_rejected():
    payload = project()
    conflicts = layer_conflicts(payload)

    assert conflicts == [{
        'motion_id': '1-2',
        'start_sec': 0.02,
        'end_sec': 0.04,
        'first_layer_id': 'base',
        'first_layer_name': '레이어 1',
        'second_layer_id': 'overdub',
        'second_layer_name': '레이어 2',
    }]
    with pytest.raises(ValueError, match='다중 레이어 축 충돌.*1-2'):
        render_project(payload)


def test_composition_checks_can_be_limited_to_affected_motion_ids():
    payload = project()
    payload['layers'][1]['frames'][0]['values']['1-1'] = 8.0
    payload['layers'][1]['frames'][1]['values']['1-1'] = 9.0

    all_conflicts = layer_conflicts(payload)
    selected_conflicts = layer_conflicts(payload, motion_ids={'1-1'})

    assert {item['motion_id'] for item in all_conflicts} == {'1-1', '1-2'}
    assert {item['motion_id'] for item in selected_conflicts} == {'1-1'}

    payload['layers'][1]['frames'] = [
        {'frame': 3, 'time_sec': 0.06, 'values': {'1-1': 30.0, '1-2': 40.0}},
    ]
    selected_warnings = layer_transition_warnings(
        payload, motion_ids={'1-2'}
    )

    assert selected_warnings
    assert {item['motion_id'] for item in selected_warnings} == {'1-2'}


def test_same_motion_id_in_non_overlapping_time_ranges_with_safe_transition_is_allowed():
    payload = project()
    payload['layers'][0]['frames'] = [
        {'frame': 1, 'time_sec': 0.02, 'values': {'1-1': 1.0}},
        {'frame': 2, 'time_sec': 0.04, 'values': {'1-1': 2.0}},
    ]
    payload['layers'][1]['frames'] = [
        {'frame': 3, 'time_sec': 0.06, 'values': {'1-1': 5.0}},
        {'frame': 4, 'time_sec': 0.08, 'values': {'1-1': 7.0}},
    ]

    frames = render_project(payload)

    assert layer_conflicts(payload) == []
    assert layer_transition_warnings(payload) == []
    assert [frame['values']['1-1'] for frame in frames] == [1.0, 2.0, 5.0, 7.0]


def test_large_value_jump_between_non_overlapping_layers_is_rejected():
    payload = project()
    payload['layers'][0]['name'] = '앞 구간'
    payload['layers'][0]['frames'] = [
        {'frame': 1, 'time_sec': 0.02, 'values': {'1-1': 1.0}},
        {'frame': 2, 'time_sec': 0.04, 'values': {'1-1': 2.0}},
    ]
    payload['layers'][1]['name'] = '뒤 구간'
    payload['layers'][1]['frames'] = [
        {'frame': 3, 'time_sec': 0.06, 'values': {'1-1': 20.0}},
        {'frame': 4, 'time_sec': 0.08, 'values': {'1-1': 22.0}},
    ]

    warnings = layer_transition_warnings(payload)

    assert len(warnings) == 1
    assert warnings[0]['motion_id'] == '1-1'
    assert warnings[0]['first_layer_name'] == '앞 구간'
    assert warnings[0]['second_layer_name'] == '뒤 구간'
    assert warnings[0]['jump_deg'] == 18.0
    assert warnings[0]['safety_level'] == 4
    assert warnings[0]['limit_deg'] == 4.0
    with pytest.raises(ValueError, match='합성 모션값 급변.*1-1'):
        render_project(payload)


def test_empty_time_gap_holds_last_value_and_checks_next_transition():
    payload = project()
    payload['layers'][0]['frames'] = [
        {'frame': 1, 'time_sec': 0.02, 'values': {'1-1': 30.0}},
        {'frame': 2, 'time_sec': 0.04, 'values': {'1-1': 31.0}},
    ]
    payload['layers'][1]['frames'] = [
        {'frame': 5, 'time_sec': 0.10, 'values': {'1-1': 50.0}},
    ]

    warnings = layer_transition_warnings(payload)

    assert len(warnings) == 1
    assert warnings[0]['kind'] == 'segment_transition'
    assert warnings[0]['jump_deg'] == 19.0


def test_late_first_frame_value_is_held_from_playback_start():
    payload = project()
    payload['layers'] = [{
        'layer_id': 'late',
        'name': '늦은 시작',
        'enabled': True,
        'frames': [
            {'frame': 150, 'time_sec': 3.0, 'values': {'1-1': 25.0}},
            {'frame': 151, 'time_sec': 3.02, 'values': {'1-1': 26.0}},
        ],
    }]

    warnings = layer_transition_warnings(payload)
    frames = render_project(payload)

    assert warnings == []
    assert frames[0]['values']['1-1'] == 25.0
    assert frames[148]['values']['1-1'] == 25.0
    assert frames[149]['values']['1-1'] == 25.0
    assert frames[150]['values']['1-1'] == 26.0


def test_late_manual_initial_value_is_held_and_transition_is_checked():
    payload = project()
    payload['layers'] = [{
        'layer_id': 'late',
        'name': '늦은 수동 시작',
        'enabled': True,
        'frames': [
            {'frame': 150, 'time_sec': 3.0, 'values': {'1-1': 30.0}},
            {'frame': 151, 'time_sec': 3.02, 'values': {'1-1': 31.0}},
        ],
    }]

    warnings = layer_transition_warnings(payload, initial_motion_values_deg={'1-1': 10.0})

    assert len(warnings) == 1
    assert warnings[0]['kind'] == 'late_start'
    assert warnings[0]['from_value_deg'] == 10.0
    assert warnings[0]['to_value_deg'] == 30.0
    with pytest.raises(ValueError, match='합성 모션값 급변.*1-1'):
        render_project(payload, initial_motion_values_deg={'1-1': 10.0})

    safe_frames = render_project(payload, initial_motion_values_deg={'1-1': 30.0})
    assert safe_frames[0]['values']['1-1'] == 30.0
    assert safe_frames[148]['values']['1-1'] == 30.0


def test_large_step_inside_one_layer_is_rejected():
    payload = project()
    payload['layers'] = [{
        'layer_id': 'take',
        'name': '단일 레이어',
        'enabled': True,
        'frames': [
            {'frame': 1, 'time_sec': 0.02, 'values': {'1-1': 1.0}},
            {'frame': 2, 'time_sec': 0.04, 'values': {'1-1': 20.0}},
        ],
    }]

    warnings = layer_transition_warnings(payload)

    assert len(warnings) == 1
    assert warnings[0]['kind'] == 'frame_step'
    assert warnings[0]['jump_deg'] == 19.0


def test_manual_initial_position_to_first_frame_jump_is_rejected():
    payload = project()
    payload['layers'] = [{
        'layer_id': 'take',
        'enabled': True,
        'frames': [
            {'frame': 1, 'time_sec': 0.02, 'values': {'1-1': 20.0}},
        ],
    }]

    warnings = layer_transition_warnings(
        payload,
        {'1-1': (-180.0, 180.0)},
        {'1-1': 0.0},
    )

    assert len(warnings) == 1
    assert warnings[0]['kind'] == 'manual_initial'
    assert warnings[0]['from_value_deg'] == 0.0
    assert warnings[0]['to_value_deg'] == 20.0


def test_safety_level_uses_larger_of_degrees_or_axis_range_percent():
    payload = project()
    payload['transition_safety_level'] = 4
    payload['layers'][0]['frames'] = [
        {'frame': 1, 'time_sec': 0.02, 'values': {'1-1': 1.0}},
        {'frame': 2, 'time_sec': 0.04, 'values': {'1-1': 2.0}},
    ]
    payload['layers'][1]['frames'] = [
        {'frame': 3, 'time_sec': 0.06, 'values': {'1-1': 14.0}},
    ]

    warnings = layer_transition_warnings(payload, {'1-1': (-180.0, 180.0)})

    assert warnings == []
    payload['layers'][1]['frames'][0]['values']['1-1'] = 20.0
    warnings = layer_transition_warnings(payload, {'1-1': (-180.0, 180.0)})
    assert len(warnings) == 1
    assert warnings[0]['range_percent_limit_deg'] == 14.4
    assert warnings[0]['limit_deg'] == 14.4


def test_recording_gap_holds_last_output_value():
    payload = project()
    payload['layers'] = [{
        'layer_id': 'take',
        'enabled': True,
        'frames': [
            {'frame': 1, 'time_sec': 0.02, 'values': {'1-1': 1.0}},
            {'frame': 2, 'time_sec': 0.04, 'values': {'1-1': 2.0}},
            {'frame': 3, 'time_sec': 0.06, 'values': {}},
            {'frame': 4, 'time_sec': 0.08, 'values': {'1-1': 3.0}},
        ],
    }]

    frames = render_project(payload)

    assert [frame['values']['1-1'] for frame in frames] == [1.0, 2.0, 2.0, 3.0]


def test_export_matches_header_plus_pair_row_format():
    payload = project()
    payload['layers'][1]['frames'] = [
        {'frame': 1, 'time_sec': 0.02, 'values': {'1-3': 10.0}},
        {'frame': 2, 'time_sec': 0.04, 'values': {'1-3': 12.0}},
    ]
    frames = render_project(payload)
    text = motion_file_text(payload, frames)
    lines = text.splitlines()

    assert json.loads(lines[0])['fields'] == ['frame', 'time_sec', 'id', 'value']
    assert json.loads(lines[1]) == [1, 0.02, '1-1', 0.0, '1-2', 0.0, '1-3', 10.0]


def test_final_export_requires_exactly_one_enabled_layer():
    payload = project()

    with pytest.raises(ValueError, match='정확히 1개'):
        final_export_layer(payload)

    payload['layers'][1]['enabled'] = False

    assert final_export_layer(payload)['layer_id'] == 'base'


def test_final_export_embeds_optional_editor_metadata_without_changing_rows():
    payload = project()
    payload['layers'][1]['enabled'] = False
    layer = final_export_layer(payload)
    layer['point_curves'] = [{
        'curve_id': 'curve_1',
        'motion_id': '1-1',
        'interpolation_order': 3,
        'points': [
            {'point_id': 'p1', 'time_sec': 0.02, 'value_deg': 0.0},
            {'point_id': 'p2', 'time_sec': 0.04, 'value_deg': 2.0},
        ],
    }]
    frames = render_project(payload)

    text = motion_file_text(
        payload,
        frames,
        editor_layer=layer,
        file_title='모션 테스트 1',
    )
    lines = text.splitlines()
    header = json.loads(lines[0])

    assert header['editor']['source_project_id'] == 'demo'
    assert header['file_title'] == '모션 테스트 1'
    assert header['editor']['layer']['point_curves'][0]['curve_id'] == 'curve_1'
    assert json.loads(lines[1]) == [1, 0.02, '1-1', 0.0, '1-2', 0.0]


def test_empty_project_has_no_artificial_recording_tracks():
    empty = {**project(), 'layers': []}

    assert render_project(empty) == []


def test_disabled_layers_do_not_extend_composition_or_add_motion_ids():
    payload = {
        'project_id': 'disabled-layer-isolation',
        'period_sec': 0.02,
        'layers': [
            {
                'layer_id': 'enabled',
                'enabled': True,
                'frames': [
                    {'frame': 1, 'time_sec': 0.02, 'values': {'1-1': 1.0}},
                ],
            },
            {
                'layer_id': 'disabled',
                'enabled': False,
                'frames': [
                    {'frame': 5, 'time_sec': 0.10, 'values': {'1-2': 50.0}},
                ],
            },
        ],
    }

    frames = render_project(payload)

    assert len(frames) == 1
    assert frames[0]['values'] == {'1-1': 1.0}
    assert layer_transition_warnings(payload) == []


def test_all_disabled_layers_render_as_empty_project():
    payload = project()
    for layer in payload['layers']:
        layer['enabled'] = False

    assert render_project(payload) == []


def test_recording_keeps_any_selected_axis_in_mapping_without_arm_list():
    selected = {'1-1': 2.5, '1-2': -3.0, '9-9': 99.0}

    assert recording_values(selected, ['1-1', '1-2']) == {
        '1-1': 2.5,
        '1-2': -3.0,
    }
