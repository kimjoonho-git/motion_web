import json

import pytest

from motion_studio.timeline import (
    layer_conflicts,
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


def test_same_motion_id_in_non_overlapping_time_ranges_is_allowed():
    payload = project()
    payload['layers'][0]['frames'] = [
        {'frame': 1, 'time_sec': 0.02, 'values': {'1-1': 1.0}},
        {'frame': 2, 'time_sec': 0.04, 'values': {'1-1': 2.0}},
    ]
    payload['layers'][1]['frames'] = [
        {'frame': 3, 'time_sec': 0.06, 'values': {'1-1': 20.0}},
        {'frame': 4, 'time_sec': 0.08, 'values': {'1-1': 30.0}},
    ]

    frames = render_project(payload)

    assert layer_conflicts(payload) == []
    assert [frame['values']['1-1'] for frame in frames] == [1.0, 2.0, 20.0, 30.0]


def test_recording_gap_ends_layer_ownership_instead_of_holding_last_value():
    payload = project()
    payload['layers'] = [{
        'layer_id': 'take',
        'enabled': True,
        'frames': [
            {'frame': 1, 'time_sec': 0.02, 'values': {'1-1': 1.0}},
            {'frame': 2, 'time_sec': 0.04, 'values': {'1-1': 2.0}},
            {'frame': 3, 'time_sec': 0.06, 'values': {}},
            {'frame': 4, 'time_sec': 0.08, 'values': {'1-1': 8.0}},
        ],
    }]

    frames = render_project(payload)

    assert [frame['values']['1-1'] for frame in frames] == [1.0, 2.0, 0.0, 8.0]


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


def test_empty_project_has_no_artificial_recording_tracks():
    empty = {**project(), 'layers': []}

    assert render_project(empty) == []


def test_recording_keeps_any_selected_axis_in_mapping_without_arm_list():
    selected = {'1-1': 2.5, '1-2': -3.0, '9-9': 99.0}

    assert recording_values(selected, ['1-1', '1-2']) == {
        '1-1': 2.5,
        '1-2': -3.0,
    }
