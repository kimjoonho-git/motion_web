import pytest

from motion_studio.curve_engine import interpolation_ratio, render_point_curve
from motion_studio.layer_editor import (
    approximate_motion_points,
    edit_layer,
    merge_layers,
    spike_correction_report,
)
from motion_studio.layer_validation import point_curve_frame_mismatches


def layer():
    return {
        'layer_id': 'take', 'name': '테스트', 'enabled': True, 'locked': False,
        'frames': [
            {'frame': 1, 'time_sec': 0.02, 'values': {'1-1': 10.0, '1-2': 0.0}},
            {'frame': 2, 'time_sec': 0.04, 'values': {'1-1': 20.0, '1-2': 2.0}},
            {'frame': 3, 'time_sec': 0.06, 'values': {'1-1': 30.0, '1-2': 4.0}},
            {'frame': 4, 'time_sec': 0.08, 'values': {'1-1': 40.0, '1-2': 6.0}},
        ],
    }


def request(operation, **values):
    return {
        'operation': operation, 'motion_ids': ['1-1'],
        'start_sec': 0.04, 'end_sec': 0.06, **values,
    }


def values(result, motion_id='1-1'):
    return [
        frame['values'][motion_id]
        for frame in result['frames'] if motion_id in frame['values']
    ]


def spike_layer(*track_values):
    return {
        'layer_id': 'spikes', 'name': '튀짐', 'enabled': True, 'locked': False,
        'frames': [
            {
                'frame': index + 1,
                'time_sec': round(index * 0.02, 2),
                'values': {'1-1': value},
            }
            for index, value in enumerate(track_values)
        ],
    }


def spike_request(**overrides):
    return {
        'operation': 'repair_spikes',
        'motion_ids': ['1-1'],
        'start_sec': 0.0,
        'end_sec': 0.12,
        'spike_detection_threshold_deg': 0.1,
        'spike_maximum_correction_deg': 2.0,
        **overrides,
    }


def test_repair_spikes_changes_only_an_isolated_interior_frame():
    source = spike_layer(0.0, 1.0, 2.0, 4.0, 4.0, 5.0, 6.0)
    report = spike_correction_report(
        source, ['1-1'], 0.0, 0.12, 0.1, 2.0,
    )
    result = edit_layer(source, spike_request())

    assert report['changed_count'] == 1
    assert report['excluded_count'] == 0
    changed = report['changed'][0]
    assert changed['motion_id'] == '1-1'
    assert {
        key: changed[key] for key in (
            'time_sec', 'before_deg', 'after_deg', 'change_deg', 'correction_deg'
        )
    } == pytest.approx({
        'time_sec': 0.06,
        'before_deg': 4.0,
        'after_deg': 3.0,
        'change_deg': -1.0,
        'correction_deg': 1.0,
    })
    assert values(result) == pytest.approx([0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0])


def test_repair_spikes_excludes_values_over_the_maximum_and_changes_nothing():
    source = spike_layer(0.0, 1.0, 2.0, 4.0, 4.0, 5.0, 6.0)
    report = spike_correction_report(
        source, ['1-1'], 0.0, 0.12, 0.1, 0.5,
    )
    result = edit_layer(source, spike_request(spike_maximum_correction_deg=0.5))

    assert report['changed_count'] == 0
    assert report['excluded_count'] == 1
    assert report['excluded'][0]['reason'] == 'maximum_correction'
    assert values(result) == pytest.approx([0.0, 1.0, 2.0, 4.0, 4.0, 5.0, 6.0])
    assert result['edit_revision'] == 0


def test_repair_spikes_excludes_consecutive_candidates():
    source = spike_layer(0.0, 1.0, 5.0, -1.0, 4.0, 5.0, 6.0)
    report = spike_correction_report(
        source, ['1-1'], 0.0, 0.12, 0.1, 10.0,
    )

    assert report['changed_count'] == 0
    assert report['excluded_count'] == 3
    assert {item['reason'] for item in report['excluded']} == {'consecutive_candidates'}


def test_repair_spikes_preserves_a_smooth_accelerating_curve():
    source = spike_layer(0.0, 0.1, 0.4, 0.9, 1.6, 2.5, 3.6)
    report = spike_correction_report(
        source, ['1-1'], 0.0, 0.12, 0.1, 1.0,
    )

    assert report['changed_count'] == 0
    assert report['excluded_count'] == 0


def test_repair_spikes_rejects_point_curve_ranges_without_removing_points():
    source = edit_layer(spike_layer(0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0), {
        'operation': 'point_curve',
        'motion_ids': ['1-1'],
        'curve_id': 'editable',
        'interpolation_order': 3,
        'points': [
            {'point_id': 'p1', 'time_sec': 0.02, 'value_deg': 1.0},
            {'point_id': 'p2', 'time_sec': 0.10, 'value_deg': 5.0},
        ],
    })

    with pytest.raises(ValueError, match='포인트와 탄젠트를 직접 수정'):
        edit_layer(source, spike_request())
    assert len(source['point_curves']) == 1


def test_offset_and_scale_touch_only_selected_axis_and_range():
    offset = edit_layer(layer(), request('value_offset', offset_deg=-10))
    assert values(offset) == [10.0, 10.0, 20.0, 40.0]
    assert values(offset, '1-2') == [0.0, 2.0, 4.0, 6.0]

    scaled = edit_layer(layer(), request('value_scale', factor=0.5))
    assert values(scaled) == [10.0, 20.0, 25.0, 40.0]


def test_delete_selected_axis_data_only_in_range():
    result = edit_layer(layer(), request('delete_data'))
    assert values(result) == [10.0, 40.0]
    assert values(result, '1-2') == [0.0, 2.0, 4.0, 6.0]


def test_delete_to_sample_before_layer_end_removes_trailing_frame_too():
    source = layer()
    result = edit_layer(source, {
        'operation': 'delete_data',
        'motion_ids': ['1-1', '1-2'],
        'start_sec': 0.06,
        'end_sec': 0.06,
    })

    assert result['frames'][-1]['time_sec'] == 0.04


def test_delete_not_near_layer_end_preserves_later_frames():
    source = layer()
    result = edit_layer(source, {
        'operation': 'delete_data',
        'motion_ids': ['1-1', '1-2'],
        'start_sec': 0.02,
        'end_sec': 0.04,
    })

    assert [frame['time_sec'] for frame in result['frames']] == [0.06, 0.08]


def test_multiple_axes_are_edited_together_with_one_request():
    result = edit_layer(layer(), {
        'operation': 'value_offset',
        'motion_ids': ['1-1', '1-2'],
        'start_sec': 0.04,
        'end_sec': 0.06,
        'offset_deg': 5.0,
    })

    assert values(result) == [10.0, 25.0, 35.0, 40.0]
    assert values(result, '1-2') == [0.0, 7.0, 9.0, 6.0]


def test_add_axis_fills_layer_time_range_at_20ms_without_changing_existing_axes():
    result = edit_layer(layer(), {
        'operation': 'add_axis',
        'motion_ids': ['3-1'],
        'initial_value_deg': 12.5,
    })

    assert [frame['time_sec'] for frame in result['frames']] == [0.02, 0.04, 0.06, 0.08]
    assert values(result) == [10.0, 20.0, 30.0, 40.0]
    assert values(result, '3-1') == [12.5, 12.5, 12.5, 12.5]


def test_add_axis_rejects_existing_axis():
    with pytest.raises(ValueError, match='이미 레이어에 있습니다'):
        edit_layer(layer(), {
            'operation': 'add_axis',
            'motion_ids': ['1-1'],
            'initial_value_deg': 0.0,
        })


def test_point_curve_can_extend_an_axis_beyond_its_existing_layer_duration():
    empty = {
        'layer_id': 'empty', 'name': '빈 레이어',
        'enabled': True, 'locked': False, 'frames': [],
    }
    added = edit_layer(empty, {
        'operation': 'add_axis',
        'motion_ids': ['3-1'],
        'initial_value_deg': 0.0,
    })
    assert [frame['time_sec'] for frame in added['frames']] == [0.0, 0.02]

    extended = edit_layer(added, {
        'operation': 'point_curve',
        'motion_ids': ['3-1'],
        'interpolation_order': 3,
        'curve_id': 'curve_extended',
        'points': [
            {'point_id': 'start', 'time_sec': 0.0, 'value_deg': 0.0},
            {'point_id': 'middle', 'time_sec': 2.5, 'value_deg': 10.0},
            {'point_id': 'end', 'time_sec': 5.0, 'value_deg': -5.0},
        ],
    })

    assert extended['frames'][-1]['time_sec'] == 5.0
    assert extended['frames'][-1]['values']['3-1'] == pytest.approx(-5.0)
    assert extended['point_curves'][0]['curve_id'] == 'curve_extended'
    assert point_curve_frame_mismatches(extended) == []


def test_copy_axis_copies_all_frame_values_without_changing_source():
    result = edit_layer(layer(), {
        'operation': 'copy_axis',
        'source_motion_id': '1-1',
        'motion_ids': ['3-1'],
    })

    assert values(result, '1-1') == [10.0, 20.0, 30.0, 40.0]
    assert values(result, '3-1') == [10.0, 20.0, 30.0, 40.0]


def test_copy_axis_duplicates_user_point_curves_as_independent_curves():
    source = edit_layer(layer(), {
        'operation': 'point_curve', 'motion_ids': ['1-1'], 'curve_id': 'curve_user',
        'points': [
            {'point_id': 'p1', 'time_sec': 0.02, 'value_deg': 10.0},
            {'point_id': 'p2', 'time_sec': 0.08, 'value_deg': 40.0},
        ],
    })

    result = edit_layer(source, {
        'operation': 'copy_axis',
        'source_motion_id': '1-1',
        'motion_ids': ['3-1'],
    })

    source_curve, copied_curve = result['point_curves']
    assert source_curve['curve_id'] == 'curve_user'
    assert source_curve['motion_id'] == '1-1'
    assert copied_curve['curve_id'] != source_curve['curve_id']
    assert copied_curve['motion_id'] == '3-1'
    assert [point['value_deg'] for point in copied_curve['points']] == [10.0, 40.0]
    assert [point['point_id'] for point in copied_curve['points']] != ['p1', 'p2']
    assert point_curve_frame_mismatches(result) == []


def test_copy_axis_rejects_missing_source_or_existing_target():
    with pytest.raises(ValueError, match='레이어에 없습니다'):
        edit_layer(layer(), {
            'operation': 'copy_axis',
            'source_motion_id': '9-9',
            'motion_ids': ['3-1'],
        })
    with pytest.raises(ValueError, match='이미 레이어에 있습니다'):
        edit_layer(layer(), {
            'operation': 'copy_axis',
            'source_motion_id': '1-1',
            'motion_ids': ['1-2'],
        })


def test_edit_range_without_selected_axis_data_is_rejected():
    with pytest.raises(ValueError, match='편집 구간에 모션 데이터가 없습니다'):
        edit_layer(layer(), request('value_offset', start_sec=1.0, end_sec=2.0, offset_deg=1.0))


def test_time_shift_rejects_overlap_with_unchanged_data():
    with pytest.raises(ValueError, match='데이터가 겹칩니다'):
        edit_layer(layer(), request('time_shift', delta_sec=0.02))


def test_time_scale_keeps_later_data_at_original_time_and_detects_overlap():
    with pytest.raises(ValueError, match='데이터가 겹칩니다'):
        edit_layer(layer(), request('time_scale', factor=2.0))

    result = edit_layer(layer(), request('time_scale', start_sec=0.02, end_sec=0.08, factor=2.0))
    assert result['frames'][-1]['time_sec'] == 0.14
    assert len(values(result)) == 7

    spaced = layer()
    spaced['frames'].append(
        {'frame': 5, 'time_sec': 0.30, 'values': {'1-1': 50.0}}
    )
    result = edit_layer(spaced, request('time_scale', start_sec=0.02, end_sec=0.08, factor=2.0))
    assert result['frames'][-1]['time_sec'] == 0.30
    assert values(result)[-1] == 50.0


def test_time_and_motion_scale_use_selected_start_as_anchor():
    source = {
        'layer_id': 'anchor', 'name': '기준점', 'enabled': True, 'locked': False,
        'frames': [
            {'frame': 1, 'time_sec': 1.00, 'values': {'1-1': 10.0}},
            {'frame': 2, 'time_sec': 2.00, 'values': {'1-1': 20.0}},
        ],
    }
    time_scaled = edit_layer(source, {
        'operation': 'time_scale', 'motion_ids': ['1-1'],
        'start_sec': 1.00, 'end_sec': 2.00, 'factor': 0.90,
    })
    assert time_scaled['frames'][0]['time_sec'] == 1.00
    assert time_scaled['frames'][-1]['time_sec'] == 1.90

    motion_scaled = edit_layer(source, {
        'operation': 'value_scale', 'motion_ids': ['1-1'],
        'start_sec': 1.00, 'end_sec': 2.00, 'factor': 1.10,
    })
    assert values(motion_scaled) == [10.0, 21.0]


def test_time_and_motion_move_shift_the_whole_selected_range():
    source = {
        'layer_id': 'move', 'name': '이동', 'enabled': True, 'locked': False,
        'frames': [
            {'frame': 1, 'time_sec': 1.00, 'values': {'1-1': 10.0}},
            {'frame': 2, 'time_sec': 2.00, 'values': {'1-1': 20.0}},
        ],
    }
    time_moved = edit_layer(source, {
        'operation': 'time_shift', 'motion_ids': ['1-1'],
        'start_sec': 1.00, 'end_sec': 2.00, 'delta_sec': -0.300,
    })
    assert [frame['time_sec'] for frame in time_moved['frames']] == [0.70, 1.70]

    motion_moved = edit_layer(source, {
        'operation': 'value_offset', 'motion_ids': ['1-1'],
        'start_sec': 1.00, 'end_sec': 2.00, 'offset_deg': -10.0,
    })
    assert values(motion_moved) == [0.0, 10.0]


@pytest.mark.parametrize('order, first_value', [
    (1, 2.5),
    (3, 1.5625),
    (5, 1.03515625),
])
def test_manual_interpolation_rebuilds_selected_gap_at_20ms(order, first_value):
    source = {
        'layer_id': 'gap', 'name': '빈 구간', 'enabled': True, 'locked': False,
        'frames': [
            {'frame': 1, 'time_sec': 0.02, 'values': {'1-1': 0.0}},
            {'frame': 2, 'time_sec': 0.10, 'values': {'1-1': 10.0}},
        ],
    }
    result = edit_layer(source, {
        'operation': 'interpolate', 'motion_ids': ['1-1'],
        'start_sec': 0.02, 'end_sec': 0.10, 'interpolation_order': order,
    })
    assert [frame['time_sec'] for frame in result['frames']] == [0.02, 0.04, 0.06, 0.08, 0.10]
    assert values(result)[0] == 0.0
    assert values(result)[1] == pytest.approx(first_value)
    assert values(result)[2] == pytest.approx(5.0)
    assert values(result)[-1] == 10.0


def test_manual_interpolation_requires_real_boundary_points():
    with pytest.raises(ValueError, match='시작점과 끝점에는 실제 모션 데이터'):
        edit_layer(layer(), request(
            'interpolate', start_sec=0.08, end_sec=0.10, interpolation_order=3,
        ))


def test_user_point_curve_is_saved_and_rendered_without_marking_recorded_frames():
    source = layer()
    assert source.get('point_curves') is None

    result = edit_layer(source, {
        'operation': 'point_curve',
        'motion_ids': ['1-1'],
        'curve_id': 'curve_user',
        'points': [
            {'point_id': 'p1', 'time_sec': 0.02, 'value_deg': 10.0,
             'tangent_mode': 'linear'},
            {'point_id': 'p2', 'time_sec': 0.08, 'value_deg': 40.0,
             'tangent_mode': 'linear'},
        ],
    })

    assert result['point_curves'][0]['curve_id'] == 'curve_user'
    assert result['point_curves'][0]['interpolation_order'] == 1
    assert [point['point_id'] for point in result['point_curves'][0]['points']] == ['p1', 'p2']
    assert values(result) == pytest.approx([10.0, 20.0, 30.0, 40.0])


@pytest.mark.parametrize('order', [1, 3, 5])
def test_point_curve_stores_and_renders_each_curve_order(order):
    result = edit_layer(layer(), {
        'operation': 'point_curve',
        'motion_ids': ['1-1'],
        'curve_id': f'curve_{order}',
        'interpolation_order': order,
        'points': [
            {'point_id': 'p1', 'time_sec': 0.02, 'value_deg': 0.0},
            {'point_id': 'p2', 'time_sec': 0.08, 'value_deg': 9.0},
        ],
    })

    assert result['point_curves'][0]['interpolation_order'] == order
    expected = [9.0 * interpolation_ratio(index / 3, order) for index in range(4)]
    assert values(result) == pytest.approx(expected)


def test_curve_boundaries_and_broken_points_stop_with_zero_slope():
    points = [
        {'point_id': 'p1', 'time_sec': 0.00, 'value_deg': 0.0,
         'tangent_mode': 'auto'},
        {'point_id': 'p2', 'time_sec': 0.10, 'value_deg': 10.0,
         'tangent_mode': 'broken'},
        {'point_id': 'p3', 'time_sec': 0.20, 'value_deg': 0.0,
         'tangent_mode': 'smooth',
         'in_handle': {'dt_sec': -0.03, 'dv_deg': 10.0},
         'out_handle': {'dt_sec': 0.03, 'dv_deg': 10.0}},
    ]

    cubic_points, cubic = render_point_curve(points, 3)
    quintic_points, quintic = render_point_curve(points, 5)

    for normalized in (cubic_points, quintic_points):
        assert normalized[0]['out_handle']['dv_deg'] == pytest.approx(0.0)
        assert normalized[1]['in_handle']['dv_deg'] == pytest.approx(0.0)
        assert normalized[1]['out_handle']['dv_deg'] == pytest.approx(0.0)
        assert normalized[-1]['in_handle']['dv_deg'] == pytest.approx(0.0)
    assert [value for _, value in cubic[:6]] == pytest.approx([
        10.0 * interpolation_ratio(index / 5, 3) for index in range(6)
    ])
    assert [value for _, value in quintic[:6]] == pytest.approx([
        10.0 * interpolation_ratio(index / 5, 5) for index in range(6)
    ])


def linked_point_curve_layer():
    return edit_layer({
        'layer_id': 'linked', 'name': '연동 곡선',
        'enabled': True, 'locked': False,
        'frames': [
            {'frame': 1, 'time_sec': 1.0, 'values': {'1-1': 10.0}},
            {'frame': 2, 'time_sec': 2.0, 'values': {'1-1': 20.0}},
        ],
    }, {
        'operation': 'point_curve',
        'motion_ids': ['1-1'],
        'curve_id': 'curve_linked',
        'interpolation_order': 3,
        'points': [
            {'point_id': 'p1', 'time_sec': 1.0, 'value_deg': 10.0},
            {
                'point_id': 'p2', 'time_sec': 1.5, 'value_deg': 30.0,
                'tangent_mode': 'smooth',
                'in_handle': {'dt_sec': -0.1, 'dv_deg': -2.0},
                'out_handle': {'dt_sec': 0.1, 'dv_deg': 2.0},
            },
            {'point_id': 'p3', 'time_sec': 2.0, 'value_deg': 20.0},
        ],
    })


def test_time_shift_moves_point_metadata_and_rendered_curve_together():
    result = edit_layer(linked_point_curve_layer(), {
        'operation': 'time_shift', 'motion_ids': ['1-1'],
        'start_sec': 1.0, 'end_sec': 2.0, 'delta_sec': 0.2,
    })

    curve = result['point_curves'][0]
    assert [point['time_sec'] for point in curve['points']] == [1.2, 1.7, 2.2]
    assert result['frames'][0]['time_sec'] == 1.2
    assert result['frames'][-1]['time_sec'] == 2.2
    assert point_curve_frame_mismatches(result) == []


def test_time_scale_moves_point_times_and_handle_times_together():
    result = edit_layer(linked_point_curve_layer(), {
        'operation': 'time_scale', 'motion_ids': ['1-1'],
        'start_sec': 1.0, 'end_sec': 2.0, 'factor': 0.5,
    })

    points = result['point_curves'][0]['points']
    assert [point['time_sec'] for point in points] == [1.0, 1.24, 1.5]
    assert points[1]['out_handle']['dt_sec'] == pytest.approx(0.05)
    assert result['frames'][-1]['time_sec'] == 1.5
    assert point_curve_frame_mismatches(result) == []


def test_value_edits_move_point_values_and_handles_together():
    offset = edit_layer(linked_point_curve_layer(), {
        'operation': 'value_offset', 'motion_ids': ['1-1'],
        'start_sec': 1.0, 'end_sec': 2.0, 'offset_deg': -5.0,
    })
    assert [
        point['value_deg'] for point in offset['point_curves'][0]['points']
    ] == [5.0, 25.0, 15.0]
    assert point_curve_frame_mismatches(offset) == []

    scaled = edit_layer(linked_point_curve_layer(), {
        'operation': 'value_scale', 'motion_ids': ['1-1'],
        'start_sec': 1.0, 'end_sec': 2.0, 'factor': 0.5,
    })
    points = scaled['point_curves'][0]['points']
    assert [point['value_deg'] for point in points] == [10.0, 20.0, 15.0]
    assert points[1]['out_handle']['dv_deg'] == pytest.approx(1.0)
    assert point_curve_frame_mismatches(scaled) == []


def test_point_to_point_partial_edit_moves_only_existing_point_controls():
    result = edit_layer(linked_point_curve_layer(), {
        'operation': 'time_shift', 'motion_ids': ['1-1'],
        'start_sec': 1.0, 'end_sec': 1.5, 'delta_sec': 0.2,
        'selection_kind': 'point',
    })

    points = result['point_curves'][0]['points']
    assert [point['point_id'] for point in points] == ['p1', 'p2', 'p3']
    assert [point['time_sec'] for point in points] == [1.2, 1.7, 2.0]
    assert point_curve_frame_mismatches(result) == []


def test_motion_point_selection_is_locked_until_point_conversion():
    with pytest.raises(ValueError, match='일반 모션은 직접 편집할 수 없습니다'):
        edit_layer(linked_point_curve_layer(), {
            'operation': 'time_scale', 'motion_ids': ['1-1'],
            'start_sec': 1.2, 'end_sec': 1.8, 'factor': 0.5,
            'selection_kind': 'motion',
        })


def test_point_selection_requires_two_real_point_controls():
    with pytest.raises(ValueError, match='포인트 두 개'):
        edit_layer(linked_point_curve_layer(), {
            'operation': 'value_scale', 'motion_ids': ['1-1'],
            'start_sec': 1.2, 'end_sec': 1.8, 'factor': 0.5,
            'selection_kind': 'point',
        })


def test_recorded_motion_is_approximated_only_when_conversion_is_requested():
    source = {
        'layer_id': 'recorded', 'name': 'MIDI 녹화',
        'enabled': True, 'locked': False,
        'frames': [
            {
                'frame': index + 1,
                'time_sec': index * 0.02,
                'values': {'1-1': value},
            }
            for index, value in enumerate([0.0, 0.0, 5.0, 10.0, 10.0])
        ],
    }
    converted = edit_layer(source, {
        'operation': 'convert_motion_to_point_curve',
        'motion_ids': ['1-1'],
        'selection_kind': 'motion',
        'start_sec': 0.0,
        'end_sec': 0.08,
        'approximation_tolerance_deg': 0.01,
        'approximation_maximum_points': 20,
        'curve_id': 'curve_fitted',
    })

    curve = converted['point_curves'][0]
    assert curve['curve_id'] == 'curve_fitted'
    assert curve['interpolation_order'] == 1
    assert 3 <= len(curve['points']) <= 5
    assert point_curve_frame_mismatches(converted) == []


def test_automatic_approximation_uses_more_points_for_complex_motion():
    simple, simple_report = approximate_motion_points([
        (index * 0.02, float(index))
        for index in range(21)
    ], tolerance_deg=0.01, maximum_points=50)
    complex_points, complex_report = approximate_motion_points([
        (index * 0.02, 10.0 if index % 2 else 0.0)
        for index in range(21)
    ], tolerance_deg=0.01, maximum_points=50)

    assert len(simple) == 3
    assert len(complex_points) > len(simple)
    assert simple_report['maximum_error_deg'] <= 0.01
    assert complex_report['maximum_error_deg'] <= 0.01


@pytest.mark.parametrize('interpolation_order', [3, 5])
def test_automatic_approximation_rechecks_the_selected_curve_order(
    interpolation_order,
):
    samples = [
        (index * 0.02, float(index))
        for index in range(21)
    ]

    points, report = approximate_motion_points(
        samples,
        tolerance_deg=0.1,
        maximum_points=50,
        interpolation_order=interpolation_order,
    )

    assert report['interpolation_order'] == interpolation_order
    assert report['initial_point_count'] == 3
    assert len(points) > report['initial_point_count']
    assert report['maximum_error_deg'] <= 0.1
    assert {point['tangent_mode'] for point in points} == {'auto'}


def test_conversion_stores_the_selected_approximation_curve_order():
    source = {
        'layer_id': 'recorded-order', 'name': '차수 선택',
        'enabled': True, 'locked': False,
        'frames': [
            {
                'frame': index + 1,
                'time_sec': index * 0.02,
                'values': {'1-1': float(index)},
            }
            for index in range(21)
        ],
    }

    converted = edit_layer(source, {
        'operation': 'convert_motion_to_point_curve',
        'motion_ids': ['1-1'],
        'selection_kind': 'motion',
        'start_sec': 0.0,
        'end_sec': 0.4,
        'approximation_tolerance_deg': 0.1,
        'approximation_maximum_points': 50,
        'approximation_interpolation_order': 3,
        'curve_id': 'curve_cubic',
    })

    assert converted['point_curves'][0]['interpolation_order'] == 3
    assert point_curve_frame_mismatches(converted) == []


def test_point_motion_can_be_baked_back_to_locked_general_motion():
    source = linked_point_curve_layer()
    result = edit_layer(source, {
        'operation': 'convert_point_curve_to_motion',
        'curve_id': 'curve_linked',
    })

    assert result['point_curves'] == []
    assert result['frames'] == source['frames']


def test_multiple_point_and_general_ranges_coexist_and_convert_independently():
    source = {
        'layer_id': 'mixed', 'name': '혼합 구간',
        'enabled': True, 'locked': False,
        'frames': [
            {
                'frame': index + 1,
                'time_sec': index * 0.02,
                'values': {'1-1': float(index % 4)},
            }
            for index in range(16)
        ],
    }
    first = edit_layer(source, {
        'operation': 'convert_motion_to_point_curve',
        'motion_ids': ['1-1'], 'start_sec': 0.0, 'end_sec': 0.08,
        'curve_id': 'point_range_1',
        'approximation_tolerance_deg': 0.01,
    })
    mixed = edit_layer(first, {
        'operation': 'convert_motion_to_point_curve',
        'motion_ids': ['1-1'], 'start_sec': 0.16, 'end_sec': 0.24,
        'curve_id': 'point_range_2',
        'approximation_tolerance_deg': 0.01,
    })

    assert [
        curve['curve_id'] for curve in mixed['point_curves']
    ] == ['point_range_1', 'point_range_2']
    baked = edit_layer(mixed, {
        'operation': 'convert_point_curve_to_motion',
        'curve_id': 'point_range_1',
    })
    assert [
        curve['curve_id'] for curve in baked['point_curves']
    ] == ['point_range_2']
    assert point_curve_frame_mismatches(baked) == []


def test_detach_point_curve_keeps_rendered_frames():
    source = linked_point_curve_layer()
    result = edit_layer(source, {
        'operation': 'detach_point_curve',
        'curve_id': 'curve_linked',
    })

    assert result['point_curves'] == []
    assert result['frames'] == source['frames']


def test_point_curves_for_same_axis_cannot_overlap():
    source = edit_layer(layer(), {
        'operation': 'point_curve', 'motion_ids': ['1-1'], 'curve_id': 'first',
        'points': [
            {'point_id': 'p1', 'time_sec': 0.02, 'value_deg': 10.0},
            {'point_id': 'p2', 'time_sec': 0.06, 'value_deg': 30.0},
        ],
    })
    with pytest.raises(ValueError, match='서로 겹칩니다'):
        edit_layer(source, {
            'operation': 'point_curve', 'motion_ids': ['1-1'], 'curve_id': 'second',
            'points': [
                {'point_id': 'p3', 'time_sec': 0.04, 'value_deg': 20.0},
                {'point_id': 'p4', 'time_sec': 0.08, 'value_deg': 40.0},
            ],
        })


def test_updating_point_curve_removes_its_previous_rendered_range():
    source = edit_layer(layer(), {
        'operation': 'point_curve', 'motion_ids': ['1-1'], 'curve_id': 'curve_user',
        'points': [
            {'point_id': 'p1', 'time_sec': 0.02, 'value_deg': 10.0},
            {'point_id': 'p2', 'time_sec': 0.08, 'value_deg': 40.0},
        ],
    })
    result = edit_layer(source, {
        'operation': 'point_curve', 'motion_ids': ['1-1'], 'curve_id': 'curve_user',
        'points': [
            {'point_id': 'p1', 'time_sec': 0.04, 'value_deg': 20.0},
            {'point_id': 'p2', 'time_sec': 0.06, 'value_deg': 30.0},
        ],
    })

    assert [
        frame['time_sec'] for frame in result['frames'] if '1-1' in frame['values']
    ] == [0.04, 0.06]


def test_point_curve_frame_mismatch_is_reported_and_user_choices_resolve_it():
    consistent = edit_layer(layer(), {
        'operation': 'point_curve', 'motion_ids': ['1-1'], 'curve_id': 'curve_user',
        'points': [
            {'point_id': 'p1', 'time_sec': 0.02, 'value_deg': 10.0},
            {'point_id': 'p2', 'time_sec': 0.08, 'value_deg': 40.0},
        ],
    })
    assert point_curve_frame_mismatches(consistent) == []

    inconsistent = {
        **consistent,
        'frames': [dict(frame) for frame in consistent['frames']],
    }
    inconsistent['frames'][1] = {
        **inconsistent['frames'][1],
        'values': {**inconsistent['frames'][1]['values'], '1-1': 99.0},
    }
    issues = point_curve_frame_mismatches(inconsistent)
    assert issues[0]['motion_id'] == '1-1'
    assert issues[0]['first_mismatch']['time_sec'] == 0.04

    point_based = edit_layer(inconsistent, {
        'operation': 'resolve_point_curve_consistency',
        'strategy': 'points',
        'curve_ids': ['curve_user'],
    })
    assert point_curve_frame_mismatches(point_based) == []
    assert values(point_based)[1] != 99.0

    frame_based = edit_layer(inconsistent, {
        'operation': 'resolve_point_curve_consistency',
        'strategy': 'frames',
        'curve_ids': ['curve_user'],
    })
    assert frame_based['point_curves'] == []
    assert values(frame_based)[1] == 99.0


def test_merge_creates_one_layer_and_preserves_sources():
    first = layer()
    first['layer_id'] = 'a'
    first['frames'] = first['frames'][:2]
    second = layer()
    second['layer_id'] = 'b'
    second['frames'] = [
        {'frame': 3, 'time_sec': 0.06, 'values': {'1-2': 2.0}},
        {'frame': 4, 'time_sec': 0.08, 'values': {'1-2': 3.0}},
    ]
    project = {'period_sec': 0.02, 'transition_safety_level': 10, 'layers': [first, second]}

    merged = merge_layers(project, ['a', 'b'], name='하나')

    assert merged['name'] == '하나'
    assert merged['source_layer_ids'] == ['a', 'b']
    assert len(merged['frames']) == 4


def test_merge_preserves_point_curves_from_each_source_layer():
    first = edit_layer({
        'layer_id': 'a',
        'name': '첫 포인트',
        'enabled': True,
        'locked': False,
        'frames': [],
    }, {
        'operation': 'point_curve',
        'motion_ids': ['1-1'],
        'curve_id': 'curve-a',
        'interpolation_order': 1,
        'points': [
            {'point_id': 'a-start', 'time_sec': 0.02, 'value_deg': 1.0},
            {'point_id': 'a-end', 'time_sec': 0.04, 'value_deg': 2.0},
        ],
    })
    second = edit_layer({
        'layer_id': 'b',
        'name': '둘째 포인트',
        'enabled': True,
        'locked': False,
        'frames': [],
    }, {
        'operation': 'point_curve',
        'motion_ids': ['2-1'],
        'curve_id': 'curve-b',
        'interpolation_order': 3,
        'points': [
            {'point_id': 'b-start', 'time_sec': 0.06, 'value_deg': 3.0},
            {'point_id': 'b-end', 'time_sec': 0.08, 'value_deg': 4.0},
        ],
    })

    merged = merge_layers(
        {'period_sec': 0.02, 'layers': [first, second]},
        ['a', 'b'],
    )

    assert {
        curve['curve_id'] for curve in merged['point_curves']
    } == {'curve-a', 'curve-b'}
    assert {
        point['point_id']
        for curve in merged['point_curves']
        for point in curve['points']
    } == {'a-start', 'a-end', 'b-start', 'b-end'}
    assert point_curve_frame_mismatches(merged) == []


def test_merged_point_curves_remain_isolated_between_two_projects():
    def merge_project(project_id, base_value):
        layers = []
        for index, motion_id in enumerate(['1-1', '2-1'], start=1):
            layer_id = f'{project_id}-layer-{index}'
            layers.append(edit_layer({
                'layer_id': layer_id,
                'name': layer_id,
                'enabled': True,
                'locked': False,
                'frames': [],
            }, {
                'operation': 'point_curve',
                'motion_ids': [motion_id],
                'curve_id': f'{project_id}-curve-{index}',
                'interpolation_order': 1,
                'points': [
                    {
                        'point_id': f'{project_id}-point-{index}-start',
                        'time_sec': 0.02,
                        'value_deg': base_value + index,
                    },
                    {
                        'point_id': f'{project_id}-point-{index}-end',
                        'time_sec': 0.04,
                        'value_deg': base_value + index + 1,
                    },
                ],
            }))
        return merge_layers(
            {'period_sec': 0.02, 'layers': layers},
            [layer['layer_id'] for layer in layers],
        )

    first = merge_project('project-a', 0.0)
    second = merge_project('project-b', 100.0)

    assert {
        curve['curve_id'] for curve in first['point_curves']
    } == {'project-a-curve-1', 'project-a-curve-2'}
    assert {
        curve['curve_id'] for curve in second['point_curves']
    } == {'project-b-curve-1', 'project-b-curve-2'}
    assert first['frames'] != second['frames']


def test_merge_reports_exact_overlap_and_transition_stop_reason():
    first = {
        'layer_id': 'a', 'name': '앞 레이어', 'frames': [
            {'frame': 1, 'time_sec': 0.02, 'values': {'1-1': 0.0}},
            {'frame': 2, 'time_sec': 0.04, 'values': {'1-1': 1.0}},
        ],
    }
    overlap = {
        'layer_id': 'b', 'name': '겹친 레이어', 'frames': [
            {'frame': 1, 'time_sec': 0.04, 'values': {'1-1': 1.0}},
            {'frame': 2, 'time_sec': 0.06, 'values': {'1-1': 2.0}},
        ],
    }
    with pytest.raises(ValueError, match=r'합치기 중단 · 시간 충돌.*1-1.*0\.040~0\.040초'):
        merge_layers({'layers': [first, overlap]}, ['a', 'b'])

    jump = {
        'layer_id': 'c', 'name': '뒤 레이어', 'frames': [
            {'frame': 3, 'time_sec': 0.06, 'values': {'1-1': 20.0}},
            {'frame': 4, 'time_sec': 0.08, 'values': {'1-1': 21.0}},
        ],
    }
    with pytest.raises(ValueError, match=r'합치기 중단 · 모션 급변.*1-1.*1\.000° → 20\.000°'):
        merge_layers(
            {'transition_safety_level': 4, 'layers': [first, jump]},
            ['a', 'c'],
        )
