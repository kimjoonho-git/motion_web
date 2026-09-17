import pytest

from motion_studio.curve_engine import interpolation_ratio, render_point_curve
from motion_studio.layer_editor import (
    MAX_APPROXIMATION_POINTS,
    approximate_motion_points,
    edit_layer,
    layer_point_coverage_issues,
    merge_layers,
)
from motion_studio.layer_validation import point_curve_frame_mismatches
from motion_studio.motion_model import normalize_layer


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


def create_all_axis_points(source):
    result = source
    motion_ids = sorted({
        motion_id
        for frame in source.get('frames') or []
        for motion_id in (frame.get('values') or {})
    })
    for motion_id in motion_ids:
        result = edit_layer(result, {
            'operation': 'create_axis_point_curve',
            'motion_ids': [motion_id],
            'curve_id': f'curve-{source["layer_id"]}-{motion_id}',
            'approximation_tolerance_deg': 0.000001,
            'approximation_maximum_points': 200,
            'approximation_interpolation_order': 1,
        })
    assert layer_point_coverage_issues(result) == []
    return result


def test_general_motion_cannot_be_edited_before_points_are_created():
    with pytest.raises(ValueError, match='포인트가 없는 모션은 편집할 수 없습니다'):
        edit_layer(layer(), request('value_offset', offset_deg=-10))


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


def test_delete_axis_removes_selected_values_and_preserves_other_axes():
    source = edit_layer(layer(), {
        'operation': 'point_curve',
        'motion_ids': ['1-1'],
        'curve_id': 'curve-delete',
        'points': [
            {'point_id': 'p1', 'time_sec': 0.02, 'value_deg': 10.0},
            {'point_id': 'p2', 'time_sec': 0.08, 'value_deg': 40.0},
        ],
    })

    result = edit_layer(source, {
        'operation': 'delete_axis',
        'motion_ids': ['1-1'],
    })

    assert values(result, '1-2') == [0.0, 2.0, 4.0, 6.0]
    assert all('1-1' not in frame['values'] for frame in result['frames'])
    assert result['point_curves'] == []


def test_delete_all_axes_leaves_an_empty_layer():
    result = edit_layer(layer(), {
        'operation': 'delete_axis',
        'motion_ids': ['1-1', '1-2'],
    })

    assert result['frames'] == []
    assert result['point_curves'] == []


def test_delete_axis_rejects_unknown_motion_id():
    with pytest.raises(ValueError, match='레이어에 없는 Motion ID'):
        edit_layer(layer(), {
            'operation': 'delete_axis',
            'motion_ids': ['9-9'],
        })


def test_edit_range_without_selected_axis_data_is_rejected():
    with pytest.raises(ValueError, match='편집 구간에 모션 데이터가 없습니다'):
        edit_layer(layer(), request('value_offset', start_sec=1.0, end_sec=2.0, offset_deg=1.0))


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


def multi_axis_point_curve_layer():
    source = {
        'layer_id': 'multi-linked', 'name': '다축 연동 곡선',
        'enabled': True, 'locked': False,
        'frames': [
            {'frame': 1, 'time_sec': 1.0,
             'values': {'1-1': 10.0, '2-1': 100.0}},
            {'frame': 2, 'time_sec': 2.0,
             'values': {'1-1': 20.0, '2-1': 110.0}},
        ],
    }
    result = edit_layer(source, {
        'operation': 'point_curve', 'motion_ids': ['1-1'],
        'curve_id': 'curve-first', 'interpolation_order': 3,
        'points': [
            {'point_id': 'a1', 'time_sec': 1.0, 'value_deg': 10.0},
            {'point_id': 'a2', 'time_sec': 1.5, 'value_deg': 30.0},
            {'point_id': 'a3', 'time_sec': 2.0, 'value_deg': 20.0},
        ],
    })
    return edit_layer(result, {
        'operation': 'point_curve', 'motion_ids': ['2-1'],
        'curve_id': 'curve-second', 'interpolation_order': 3,
        'points': [
            {'point_id': 'b1', 'time_sec': 1.0, 'value_deg': 100.0},
            {'point_id': 'b2', 'time_sec': 1.6, 'value_deg': 120.0},
            {'point_id': 'b3', 'time_sec': 2.0, 'value_deg': 110.0},
        ],
    })


def test_common_time_range_edits_points_from_multiple_axes_atomically():
    source = multi_axis_point_curve_layer()
    result = edit_layer(source, {
        'operation': 'value_offset', 'motion_ids': ['1-1', '2-1'],
        'start_sec': 1.0, 'end_sec': 2.0, 'offset_deg': 5.0,
    })

    curves = {
        curve['motion_id']: curve for curve in result['point_curves']
    }
    assert [point['value_deg'] for point in curves['1-1']['points']] == [
        15.0, 35.0, 25.0,
    ]
    assert [point['value_deg'] for point in curves['2-1']['points']] == [
        105.0, 125.0, 115.0,
    ]
    assert point_curve_frame_mismatches(result) == []
    assert source != result


def test_common_time_range_uses_one_time_pivot_for_different_axis_point_times():
    result = edit_layer(multi_axis_point_curve_layer(), {
        'operation': 'time_scale', 'motion_ids': ['1-1', '2-1'],
        'start_sec': 1.0, 'end_sec': 2.0, 'factor': 0.5,
    })

    curves = {
        curve['motion_id']: curve for curve in result['point_curves']
    }
    assert [point['time_sec'] for point in curves['1-1']['points']] == [
        1.0, 1.24, 1.5,
    ]
    assert [point['time_sec'] for point in curves['2-1']['points']] == [
        1.0, 1.3, 1.5,
    ]
    assert point_curve_frame_mismatches(result) == []


def test_common_time_range_shifts_point_times_on_every_selected_axis():
    result = edit_layer(multi_axis_point_curve_layer(), {
        'operation': 'time_shift', 'motion_ids': ['1-1', '2-1'],
        'start_sec': 1.0, 'end_sec': 2.0, 'delta_sec': 0.2,
    })

    curves = {
        curve['motion_id']: curve for curve in result['point_curves']
    }
    assert [point['time_sec'] for point in curves['1-1']['points']] == [
        1.2, 1.7, 2.2,
    ]
    assert [point['time_sec'] for point in curves['2-1']['points']] == [
        1.2, 1.8, 2.2,
    ]
    assert point_curve_frame_mismatches(result) == []


def test_common_time_range_scales_values_from_each_axis_start_value():
    result = edit_layer(multi_axis_point_curve_layer(), {
        'operation': 'value_scale', 'motion_ids': ['1-1', '2-1'],
        'start_sec': 1.0, 'end_sec': 2.0, 'factor': 0.5,
    })

    curves = {
        curve['motion_id']: curve for curve in result['point_curves']
    }
    assert [point['value_deg'] for point in curves['1-1']['points']] == [
        10.0, 20.0, 15.0,
    ]
    assert [point['value_deg'] for point in curves['2-1']['points']] == [
        100.0, 110.0, 105.0,
    ]
    assert point_curve_frame_mismatches(result) == []


def test_common_time_range_rejects_an_axis_without_points_in_the_area():
    with pytest.raises(
        ValueError,
        match='선택영역에 편집할 포인트가 없는 Motion ID: 2-1',
    ):
        edit_layer(multi_axis_point_curve_layer(), {
            'operation': 'value_offset', 'motion_ids': ['1-1', '2-1'],
            'start_sec': 1.4, 'end_sec': 1.5, 'offset_deg': 5.0,
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

    inverted = edit_layer(linked_point_curve_layer(), {
        'operation': 'value_scale', 'motion_ids': ['1-1'],
        'start_sec': 1.0, 'end_sec': 2.0, 'factor': -1.0,
    })
    points = inverted['point_curves'][0]['points']
    assert [point['value_deg'] for point in points] == [10.0, -10.0, 0.0]
    assert points[1]['out_handle']['dv_deg'] == pytest.approx(-2.0)
    assert [point['time_sec'] for point in points] == [1.0, 1.5, 2.0]
    assert point_curve_frame_mismatches(inverted) == []

    with pytest.raises(ValueError, match='0을 제외'):
        edit_layer(linked_point_curve_layer(), {
            'operation': 'value_scale', 'motion_ids': ['1-1'],
            'start_sec': 1.0, 'end_sec': 2.0, 'factor': 0.0,
        })


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


def test_single_point_selection_uses_zero_as_time_and_value_scale_pivot():
    time_scaled = edit_layer(linked_point_curve_layer(), {
        'operation': 'time_scale', 'motion_ids': ['1-1'],
        'start_sec': 1.5, 'end_sec': 1.5, 'factor': 0.8,
        'selection_kind': 'point',
    })
    assert [
        point['time_sec'] for point in time_scaled['point_curves'][0]['points']
    ] == [1.0, 1.2, 2.0]
    assert point_curve_frame_mismatches(time_scaled) == []

    value_scaled = edit_layer(linked_point_curve_layer(), {
        'operation': 'value_scale', 'motion_ids': ['1-1'],
        'start_sec': 1.5, 'end_sec': 1.5, 'factor': 0.5,
        'selection_kind': 'point',
    })
    assert [
        point['value_deg'] for point in value_scaled['point_curves'][0]['points']
    ] == [10.0, 15.0, 20.0]
    assert point_curve_frame_mismatches(value_scaled) == []


def test_single_point_selection_supports_time_and_value_translation():
    shifted = edit_layer(linked_point_curve_layer(), {
        'operation': 'time_shift', 'motion_ids': ['1-1'],
        'start_sec': 1.5, 'end_sec': 1.5, 'delta_sec': 0.2,
        'selection_kind': 'point',
    })
    assert [
        point['time_sec'] for point in shifted['point_curves'][0]['points']
    ] == [1.0, 1.7, 2.0]

    offset = edit_layer(linked_point_curve_layer(), {
        'operation': 'value_offset', 'motion_ids': ['1-1'],
        'start_sec': 1.5, 'end_sec': 1.5, 'offset_deg': -5.0,
        'selection_kind': 'point',
    })
    assert [
        point['value_deg'] for point in offset['point_curves'][0]['points']
    ] == [10.0, 25.0, 20.0]
    assert point_curve_frame_mismatches(shifted) == []
    assert point_curve_frame_mismatches(offset) == []


def test_recorded_motion_creates_points_for_the_entire_axis():
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
        'operation': 'create_axis_point_curve',
        'motion_ids': ['1-1'],
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

    # 곧은 경사는 양 끝 두 개면 오차가 0 이다 · 가운데 포인트는 제 몫이
    # 없으므로 솎여 나간다 · 「정밀도 안에서 최소」 · §6-113
    assert len(simple) == 2
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


def test_point_creation_stores_the_selected_approximation_curve_order():
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
        'operation': 'create_axis_point_curve',
        'motion_ids': ['1-1'],
        'approximation_tolerance_deg': 0.1,
        'approximation_maximum_points': 50,
        'approximation_interpolation_order': 3,
        'curve_id': 'curve_cubic',
    })

    assert converted['point_curves'][0]['interpolation_order'] == 3
    assert point_curve_frame_mismatches(converted) == []


@pytest.mark.parametrize('operation', [
    'convert_motion_to_point_curve',
    'convert_point_curve_to_motion',
    'detach_point_curve',
    'delete_point_curve',
])
def test_removed_motion_section_operations_are_rejected(operation):
    with pytest.raises(ValueError, match='지원하지 않는 레이어 편집 기능'):
        edit_layer(linked_point_curve_layer(), {
            'operation': operation,
            'motion_ids': ['1-1'],
            'curve_id': 'curve_linked',
        })


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

    with pytest.raises(ValueError, match='포인트 기준 재계산만 지원'):
        edit_layer(inconsistent, {
            'operation': 'resolve_point_curve_consistency',
            'strategy': 'frames',
            'curve_ids': ['curve_user'],
        })


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
    first = create_all_axis_points(first)
    second = create_all_axis_points(second)
    project = {'period_sec': 0.02, 'layers': [first, second]}

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
            append_layer_id=layers[1]['layer_id'],
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


def test_merge_append_moves_the_user_selected_whole_layer_after_the_other_layer():
    first = create_all_axis_points({
        'layer_id': 'a', 'name': 'A', 'frames': [
            {'frame': 1, 'time_sec': 0.02, 'values': {'1-1': 0.0}},
            {'frame': 2, 'time_sec': 0.04, 'values': {'1-1': 1.0}},
        ],
    })
    second = create_all_axis_points({
        'layer_id': 'b', 'name': 'B', 'frames': [
            {'frame': 1, 'time_sec': 0.02, 'values': {'1-1': 20.0, '2-1': 5.0}},
            {'frame': 2, 'time_sec': 0.04, 'values': {'1-1': 21.0, '2-1': 6.0}},
        ],
    })
    project = {'period_sec': 0.02, 'layers': [first, second]}

    append_second = merge_layers(
        project, ['a', 'b'], append_layer_id='b', name='A 뒤 B'
    )
    append_first = merge_layers(
        project, ['a', 'b'], append_layer_id='a', name='B 뒤 A'
    )

    assert values(append_second, '1-1') == [0.0, 1.0, 20.0, 21.0]
    assert {
        point['time_sec']
        for curve in append_second['point_curves']
        if curve['motion_id'] in {'1-1', '2-1'} and curve['curve_id'].startswith('curve-b-')
        for point in curve['points']
    } == {0.06, 0.08}
    report = append_second['merge_report']
    assert {
        key: report[key]
        for key in ('mode', 'append_layer_id', 'append_offset_sec')
    } == {'mode': 'append', 'append_layer_id': 'b', 'append_offset_sec': 0.04}
    # 이음매에서 얼마나 튀는지도 함께 알린다 · 막지는 않는다 · §6-116
    assert [item['motion_id'] for item in report['append_seam']] == ['1-1', '2-1']
    assert values(append_first, '1-1') == [20.0, 21.0, 0.0, 1.0]
    assert append_first['merge_report']['append_layer_id'] == 'a'
    assert point_curve_frame_mismatches(append_second) == []
    assert point_curve_frame_mismatches(append_first) == []
    assert [frame['time_sec'] for frame in first['frames']] == [0.02, 0.04]
    assert [frame['time_sec'] for frame in second['frames']] == [0.02, 0.04]


def test_merge_append_requires_the_moved_layer_to_be_selected():
    first = create_all_axis_points({
        'layer_id': 'a', 'name': 'A', 'frames': [
            {'frame': 1, 'time_sec': 0.02, 'values': {'1-1': 0.0}},
            {'frame': 2, 'time_sec': 0.04, 'values': {'1-1': 1.0}},
        ],
    })
    second = create_all_axis_points({
        'layer_id': 'b', 'name': 'B', 'frames': [
            {'frame': 1, 'time_sec': 0.02, 'values': {'1-1': 2.0}},
            {'frame': 2, 'time_sec': 0.04, 'values': {'1-1': 3.0}},
        ],
    })

    with pytest.raises(ValueError, match='합치기 대상에 포함되지 않았습니다'):
        merge_layers(
            {'period_sec': 0.02, 'layers': [first, second]},
            ['a', 'b'], append_layer_id='missing',
        )


def _recorded(layer_id, name, samples, motion_id='1-1'):
    """녹화가 내놓는 레이어 · 20ms 프레임만 있고 포인트 곡선은 없다."""
    return {
        'layer_id': layer_id, 'name': name,
        'frames': [
            {'frame': i + 1, 'time_sec': t, 'values': {motion_id: v}}
            for i, (t, v) in enumerate(samples)
        ],
    }


def test_recorded_layers_can_be_merged():
    """녹화한 레이어는 포인트 곡선이 없다 · 스튜디오가 만들어 내는 것이 바로
    그것인데, 합치기만 "모든 축이 포인트로 덮여야 한다" 고 요구해서 **영영 합칠
    수 없었다** · §6-90

    재생·내보내기·초기 위치가 지키는 불변식은 "곡선이 **있으면** 프레임과 맞아야
    한다" 이다 · 합치기도 같은 규칙을 쓴다.
    """
    first = _recorded('a', '녹화 1', [(0.02, 0.0), (0.04, 1.0)])
    second = _recorded('c', '추가 녹화 1', [(0.06, 20.0), (0.08, 21.0)])

    merged = merge_layers({'layers': [first, second]}, ['a', 'c'])

    assert values(merged) == [0.0, 1.0, 20.0, 21.0]
    assert merged['point_curves'] == [], '없던 곡선이 생겼다'


def test_merging_a_recorded_layer_with_a_point_backed_one_keeps_both():
    """한쪽만 포인트 곡선이 있어도 된다 · **축이 다를 때** 이야기다 · §6-116

    같은 축을 한쪽만 포인트로 덮은 채 합치면 그 축이 반쪽이 되어 막는다 ·
    축이 다르면 축마다 상태가 한결같으므로 그대로 합친다 · 합친 레이어는
    곡선이 있는 축만 곡선을 물려받고 나머지는 프레임 그대로 남는다.
    """
    recorded = _recorded('a', '녹화 1', [(0.02, 0.0), (0.04, 1.0)])
    edited = create_all_axis_points(
        _recorded('c', '편집한 레이어', [(0.06, 20.0), (0.08, 21.0)], motion_id='1-2')
    )

    merged = merge_layers({'layers': [recorded, edited]}, ['a', 'c'])

    assert len(merged['point_curves']) == 1
    assert merged['point_curves'][0]['motion_id'] == '1-2'
    # 합친 결과도 시스템의 불변식을 지킨다 · 다시 합칠 수 있다
    assert point_curve_frame_mismatches(merged) == []


def test_merge_still_refuses_curves_that_disagree_with_their_frames():
    """곡선이 **있는데** 프레임과 다르면 막는다 · 그게 진짜 위험한 경우다."""
    edited = create_all_axis_points(
        _recorded('a', '편집한 레이어', [(0.02, 0.0), (0.04, 1.0)])
    )
    # 프레임만 몰래 바꾼다 · 곡선은 그대로다
    edited['frames'][1]['values']['1-1'] = 99.0
    other = _recorded('c', '녹화 1', [(0.06, 20.0), (0.08, 21.0)])

    with pytest.raises(ValueError, match='포인트 곡선이 20ms 프레임과 어긋납니다'):
        merge_layers({'layers': [edited, other]}, ['a', 'c'])


def test_merge_refuses_layers_with_no_motion_data():
    with pytest.raises(ValueError, match='모션 데이터 없음'):
        merge_layers({'layers': [
            {'layer_id': 'empty-a', 'name': '빈 레이어 A', 'frames': []},
            {'layer_id': 'empty-b', 'name': '빈 레이어 B', 'frames': []},
        ]}, ['empty-a', 'empty-b'])


def test_merge_rejects_exact_time_overlap():
    first = _recorded('a', '앞 레이어', [(0.02, 0.0), (0.04, 1.0)])
    overlap = _recorded('b', '겹친 레이어', [(0.04, 1.0), (0.06, 2.0)])

    with pytest.raises(ValueError, match=r'합치기 중단 · 시간 충돌.*1-1.*0\.040~0\.040초'):
        merge_layers({'layers': [first, overlap]}, ['a', 'b'])


# 거친 허용 오차가 있어야 빽빽한 녹화도 포인트로 바뀐다 · §6-110
#
# 포인트는 최대 200개까지만 만든다 · 굴곡이 많은 긴 녹화는 0.5° 안에 들어오지
# 못해 「전체 포인트 생성」이 통째로 실패했다 · 실제 18.7초 녹화에서 0.5°는
# 200개로도 못 맞췄고 1°는 186개로 들어왔다.


def _wiggly_samples():
    """빽빽한 녹화의 모양 · 20ms 간격 · 잔 굴곡이 많다."""
    import math
    return [
        (round(index * 0.02, 3),
         60.0 * math.sin(index * 0.05) + 5.0 * math.sin(index * 0.2))
        for index in range(900)
    ]


def test_a_fine_tolerance_can_run_out_of_points():
    """사용자가 본 실패 · 이 상태가 존재하기 때문에 거친 선택지가 필요하다."""
    for tolerance in (0.02, 0.1):
        _points, report = approximate_motion_points(
            _wiggly_samples(), tolerance, 200, 3
        )
        assert report['point_limit_reached'] is True, (
            f'{tolerance}° 가 들어와 버리면 이 시험이 아무것도 못 지킨다'
        )


def test_a_coarse_tolerance_fits_inside_the_point_budget():
    for tolerance in (1.0, 2.0, 3.0):
        points, report = approximate_motion_points(
            _wiggly_samples(), tolerance, 200, 3
        )
        assert report['point_limit_reached'] is False, f'{tolerance}° 도 못 맞춘다'
        assert 3 <= len(points) <= 200


def test_a_coarser_tolerance_never_needs_more_points():
    counts = [
        len(approximate_motion_points(_wiggly_samples(), tolerance, 200, 3)[0])
        for tolerance in (1.0, 2.0, 3.0)
    ]
    assert counts == sorted(counts, reverse=True), '거칠게 잡았는데 포인트가 늘었다'


# 긴 모션도 포인트로 바뀐다 · §6-111
#
# 포인트 하나를 고를 때마다 표본 전체를 다시 훑었다 · 5분짜리(15,000표본)에서
# 2,000개를 고르면 54초가 걸려, 상한을 200 에 묶어 둘 수밖에 없었고 긴 모션은
# 「전체 포인트 생성」 자체가 되지 않았다.
#
# 구간의 오차는 그 구간을 쪼개기 전까지 변하지 않는다 · 쪼갠 구간만 다시 재고,
# 아직 어긋난 구간은 한 번에 채운다.


def _five_minute_samples():
    """5분 · 20ms · 사람이 슬라이더로 만든 모양(느린 흔들림 + 잔떨림)."""
    import math
    return [
        (round(index * 0.02, 3),
         45.0 * math.sin(index * 0.004)
         + 10.0 * math.sin(index * 0.017)
         + 1.5 * math.sin(index * 0.09))
        for index in range(15_000)
    ]


def test_a_five_minute_motion_converts_within_the_budget():
    points, report = approximate_motion_points(
        _five_minute_samples(), 0.5, MAX_APPROXIMATION_POINTS, 3
    )
    assert report['point_limit_reached'] is False, '5분짜리가 아직도 안 된다'
    assert report['maximum_error_deg'] <= 0.5
    assert 3 <= len(points) <= MAX_APPROXIMATION_POINTS


def test_the_budget_is_large_enough_for_a_long_motion():
    """200 으로는 5분짜리가 절대 들어오지 않는다 · 상한이 벽이었다."""
    assert MAX_APPROXIMATION_POINTS >= 2000
    _points, report = approximate_motion_points(
        _five_minute_samples(), 0.5, 200, 3
    )
    assert report['point_limit_reached'] is True


def test_a_long_motion_does_not_take_minutes():
    """속도가 곧 기능이다 · 느리면 상한을 다시 못 올린다 · 넉넉하게 10초로 잡는다."""
    import time
    started = time.monotonic()
    approximate_motion_points(
        _five_minute_samples(), 0.02, MAX_APPROXIMATION_POINTS, 5
    )
    assert time.monotonic() - started < 10.0


def test_the_choice_still_follows_the_largest_error():
    """고르는 규칙은 그대로 · 직선에서 가장 벗어난 자리가 먼저 뽑힌다."""
    samples = [(round(index * 0.02, 3), 0.0) for index in range(11)]
    samples[3] = (samples[3][0], 10.0)
    points, _report = approximate_motion_points(samples, 0.5, 4, 1)
    assert 0.06 in [point['time_sec'] for point in points]


# 직선 기준으로 재면 곡선에 필요 없는 포인트까지 잡는다 · §6-112


def test_relaxing_the_linear_pass_costs_no_accuracy():
    """느슨하게 잡아도 최종 결과는 허용 오차 안에 있어야 한다."""
    for tolerance in (0.1, 0.5, 1.0):
        for order in (3, 5):
            _points, report = approximate_motion_points(
                _wiggly_samples(), tolerance, MAX_APPROXIMATION_POINTS, order
            )
            assert report['point_limit_reached'] is False
            assert report['maximum_error_deg'] <= tolerance + 1e-9


def test_a_straight_line_curve_is_not_relaxed():
    """1차(직선)는 1차 통과가 곧 최종 곡선이다 · 느슨하게 잡으면 오차를 넘긴다."""
    points, report = approximate_motion_points(
        _wiggly_samples(), 0.5, MAX_APPROXIMATION_POINTS, 1
    )
    assert report['maximum_error_deg'] <= 0.5 + 1e-9
    assert len(points) <= report['initial_point_count']


def test_the_relaxed_pass_really_reduces_points():
    """긴 모션에서 값이 드러난다 · 솎아내기 예산이 한정되어 있기 때문이다.

    짧은 모션은 솎아내기가 끝까지 가므로 1차 통과를 어떻게 잡든 결과가 같다 ·
    긴 모션은 예산이 먼저 떨어지므로, 1차 통과가 덜 잡아 놓을수록 최종 포인트가
    적다.
    """
    import motion_studio.layer_editor as editor_module
    assert editor_module.LINEAR_PASS_RELAXATION > 1.0, (
        '1차 통과를 직선 기준 그대로 재고 있다'
    )
    samples = _five_minute_samples()
    shipped = editor_module.LINEAR_PASS_RELAXATION
    try:
        editor_module.LINEAR_PASS_RELAXATION = 1.0
        tight, _ = approximate_motion_points(samples, 0.1, 5000, 3)
    finally:
        editor_module.LINEAR_PASS_RELAXATION = shipped
    relaxed, report = approximate_motion_points(samples, 0.1, 5000, 3)
    assert len(relaxed) < len(tight), '느슨하게 잡았는데 줄지 않았다'
    assert report['maximum_error_deg'] <= 0.1 + 1e-9, '줄이면서 허용 오차를 넘겼다'


# 정밀도 안에서 최소 포인트 · §6-113
#
# 앞에서부터 욕심내어 넣기 때문에, 나중에 넣은 포인트가 앞서 넣은 것을 필요
# 없게 만든다 · 그대로 두면 「정밀도 안에서 최소」가 아니다.


def test_no_point_can_be_removed_without_breaking_the_tolerance():
    """남은 포인트는 전부 제 몫이 있어야 한다 · 하나라도 빼면 오차를 넘긴다."""
    from motion_studio.curve_engine import render_point_curve
    samples = _wiggly_samples()
    tolerance = 1.0
    points, report = approximate_motion_points(samples, tolerance, 5000, 3)
    assert report['point_limit_reached'] is False

    def worst_error(candidate_points):
        _normalized, rendered = render_point_curve(candidate_points, 3)
        by_time = {round(t, 9): v for t, v in rendered}
        return max(
            abs(value - by_time[round(time_sec, 9)])
            for time_sec, value in samples
        )

    removable = [
        slot for slot in range(1, len(points) - 1)
        if worst_error(points[:slot] + points[slot + 1:]) <= tolerance
    ]
    assert removable == [], f'뺄 수 있는 포인트가 {len(removable)}개 남았다'


def test_pruning_keeps_the_result_inside_the_tolerance():
    for tolerance in (0.5, 1.0, 3.0):
        for order in (1, 3, 5):
            _points, report = approximate_motion_points(
                _wiggly_samples(), tolerance, 5000, order
            )
            assert report['maximum_error_deg'] <= tolerance + 1e-9


def test_a_long_motion_still_finishes_quickly_with_pruning():
    import time
    started = time.monotonic()
    _points, report = approximate_motion_points(
        _five_minute_samples(), 0.5, MAX_APPROXIMATION_POINTS, 3
    )
    assert report['maximum_error_deg'] <= 0.5 + 1e-9
    assert time.monotonic() - started < 10.0


# 한 축이 한쪽에만 포인트로 덮여 있으면 합치지 않는다 · §6-116
#
# 합치면 그 축은 반쪽이 된다 · 덮인 구간은 포인트로 편집되고 나머지는 막힌다 ·
# 거기서 「전체 포인트 생성」을 누르면 축 전체를 새 곡선으로 덮어써, 앞쪽에서
# 손으로 다듬어 둔 포인트가 사라진다 · 합치고 나서는 되돌릴 방법이 없다.


def _recorded_sine(layer_id, name, motion_id, start_sec, count, phase=0.0):
    import math
    return normalize_layer({
        'layer_id': layer_id, 'name': name, 'enabled': True, 'locked': False,
        'point_curves': [],
        'frames': [
            {'time_sec': round(start_sec + index * 0.02, 3),
             'values': {motion_id: 30.0 * math.sin(index * 0.05 + phase)}}
            for index in range(count)
        ],
    })


def _pointed_axis(layer, motion_id):
    return edit_layer(layer, {
        'operation': 'create_axis_point_curve', 'motion_ids': [motion_id],
        'approximation_tolerance_deg': 1.0, 'approximation_maximum_points': 5000,
        'approximation_interpolation_order': 3,
    })


def test_one_axis_pointed_on_only_one_side_is_refused():
    pointed = _pointed_axis(_recorded_sine('A', '포인트', '1-1', 0.02, 200), '1-1')
    raw = _recorded_sine('B', '생녹화', '1-1', 0.02, 200, phase=1.0)

    with pytest.raises(ValueError) as caught:
        merge_layers({'layers': [pointed, raw]}, ['A', 'B'], append_layer_id='B')

    message = str(caught.value)
    assert '1-1' in message, '어느 축인지 말하지 않는다'
    assert '포인트' in message
    assert '포인트 있음: 포인트' in message and '포인트 없음: 생녹화' in message, (
        '어느 레이어가 어느 쪽인지 말하지 않는다'
    )


def test_both_sides_pointed_still_merges():
    first = _pointed_axis(_recorded_sine('A', '앞', '1-1', 0.02, 200), '1-1')
    second = _pointed_axis(_recorded_sine('B', '뒤', '1-1', 0.02, 200, phase=1.0), '1-1')

    merged = merge_layers({'layers': [first, second]}, ['A', 'B'], append_layer_id='B')

    assert len(merged['point_curves']) == 2
    assert layer_point_coverage_issues(merged) == [], '축이 반쪽으로 남았다'


def test_neither_side_pointed_still_merges():
    """녹화한 레이어끼리 합치는 것은 막지 않는다 · §6-90 으로 이미 풀어 둔 길이다."""
    first = _recorded_sine('A', '앞', '1-1', 0.02, 200)
    second = _recorded_sine('B', '뒤', '1-1', 0.02, 200, phase=1.0)

    merged = merge_layers({'layers': [first, second]}, ['A', 'B'], append_layer_id='B')

    assert merged['point_curves'] == []


def test_different_axes_are_not_affected():
    """축이 다르면 축마다 상태가 한결같다 · 반쪽이 되지 않으므로 막지 않는다."""
    pointed = _pointed_axis(_recorded_sine('A', '포인트', '1-1', 0.02, 200), '1-1')
    raw = _recorded_sine('B', '생녹화', '1-2', 0.02, 200, phase=2.0)

    merged = merge_layers({'layers': [pointed, raw]}, ['A', 'B'])

    assert layer_point_coverage_issues(merged) == ['1-2']


# 이음매에서 값이 튀는 것은 막지 않는다 · 다만 말해 준다 · §6-116


def test_the_append_seam_step_is_reported():
    first = _recorded_sine('A', '앞', '1-1', 0.02, 200)
    second = _recorded_sine('B', '뒤', '1-1', 0.02, 200, phase=1.0)

    merged = merge_layers({'layers': [first, second]}, ['A', 'B'], append_layer_id='B')

    seam = merged['merge_report']['append_seam']
    assert seam, '이음매를 알려 주지 않는다'
    frames = {round(f['time_sec'], 3): f['values']['1-1'] for f in merged['frames']}
    seam_time = round(float(seam[0]['time_sec']), 3)
    measured = abs(frames[seam_time] - frames[round(seam_time - 0.02, 3)])
    assert abs(seam[0]['step_deg'] - measured) < 1e-3, (
        '알려 준 값이 실제 프레임 차이와 다르다'
    )
    assert seam[0]['step_deg'] > 1.0, '이 시험이 튀지 않는 자료를 쓰고 있다'


def test_a_plain_merge_reports_no_seam():
    first = _pointed_axis(_recorded_sine('A', '앞', '1-1', 0.02, 200), '1-1')
    second = _pointed_axis(_recorded_sine('B', '다른축', '1-2', 0.02, 200, phase=2.0), '1-2')

    merged = merge_layers({'layers': [first, second]}, ['A', 'B'])

    assert merged['merge_report']['append_seam'] == []


# 포인트 값을 허용 오차 안에서 푼다 · §6-117
#
# 포인트를 녹화 표본 값에 묶어 두면 곡선이 그 점을 반드시 지나야 해서 주변에
# 포인트가 더 필요하다 · 값을 조금 옮길 수 있게 하면 같은 모양을 더 적은
# 포인트로 낸다 · 실제 녹화에서 0.5° 218→186, 1° 154→133, 3° 90→63.


def test_free_values_still_respect_the_tolerance():
    """값을 풀어 주더라도 곡선은 허용 오차 안에 있어야 한다 · 전체로 다시 잰다."""
    from motion_studio.curve_engine import render_point_curve
    samples = _wiggly_samples()
    for tolerance in (0.5, 1.0, 3.0):
        for order in (1, 3, 5):
            points, report = approximate_motion_points(
                samples, tolerance, MAX_APPROXIMATION_POINTS, order
            )
            _normalized, rendered = render_point_curve(points, order)
            by_time = {round(t, 9): v for t, v in rendered}
            worst = max(
                abs(value - by_time[round(time_sec, 9)])
                for time_sec, value in samples
                if round(time_sec, 9) in by_time
            )
            assert worst <= tolerance + 1e-9, f'{tolerance}° {order}차 에서 넘겼다'
            assert abs(report['maximum_error_deg'] - worst) < 1e-6, (
                '보고한 오차가 실제와 다르다'
            )


def test_point_times_stay_on_the_recorded_grid():
    """값은 풀어 주지만 **시간은** 녹화 격자 위에 그대로 둔다."""
    samples = _wiggly_samples()
    grid = {round(time_sec, 9) for time_sec, _value in samples}
    points, _report = approximate_motion_points(samples, 1.0, 5000, 3)
    assert all(round(p['time_sec'], 9) in grid for p in points)


def test_free_values_actually_leave_the_recorded_values():
    """값이 하나도 안 움직였다면 이 기능이 꺼진 것이다."""
    samples = _wiggly_samples()
    recorded = {round(time_sec, 9): value for time_sec, value in samples}
    points, _report = approximate_motion_points(samples, 1.0, 5000, 3)
    moved = [
        p for p in points
        if abs(p['value_deg'] - recorded[round(p['time_sec'], 9)]) > 1e-9
    ]
    assert moved, '값이 전혀 안 움직였다 · 값 풀기가 동작하지 않는다'
    # 옮긴 폭은 허용 오차를 크게 벗어나지 않는다
    assert max(
        abs(p['value_deg'] - recorded[round(p['time_sec'], 9)]) for p in moved
    ) <= 1.0 + 1e-9


def test_free_values_reduce_the_point_count():
    samples = _wiggly_samples()
    points, report = approximate_motion_points(samples, 1.0, 5000, 3)
    assert len(points) < report['initial_point_count'], (
        '1차 통과가 잡은 것보다 줄어야 한다'
    )


def test_a_long_motion_still_finishes_with_free_values():
    import time
    started = time.monotonic()
    _points, report = approximate_motion_points(
        _five_minute_samples(), 0.5, MAX_APPROXIMATION_POINTS, 3
    )
    assert report['maximum_error_deg'] <= 0.5 + 1e-9
    assert time.monotonic() - started < 20.0


# 구간 복사·삭제는 고른 축 전부를 한 번에 · §6-122
#
# 전에는 곡선 하나만 봤다 · 시작·종료 포인트가 같은 축·같은 곡선이어야 했고,
# 축을 셋 골라도 한 축만 바뀌었다 · 시간 이동·배율은 이미 고른 축 전부를
# 처리하고 있었는데 여기만 달랐다.


def _two_axis_layer():
    import math
    frames = [
        {'time_sec': round(index * 0.02, 3),
         'values': {'1-1': 30.0 * math.sin(index * 0.15),
                    '1-2': 20.0 * math.sin(index * 0.19 + 1.0)}}
        for index in range(1, 301)
    ]
    layer = normalize_layer({
        'layer_id': 'L', 'name': 'L', 'enabled': True, 'locked': False,
        'point_curves': [], 'frames': frames,
    })
    for motion_id in ('1-1', '1-2'):
        layer = edit_layer(layer, {
            'operation': 'create_axis_point_curve', 'motion_ids': [motion_id],
            'approximation_tolerance_deg': 1.0,
            'approximation_maximum_points': 5000,
            'approximation_interpolation_order': 3,
        })
    return layer


def _counts(layer):
    return {
        str(curve['motion_id']): len(curve['points'])
        for curve in layer['point_curves']
    }


def _times_in(layer, motion_id, start_sec, end_sec):
    curve = next(
        curve for curve in layer['point_curves']
        if str(curve['motion_id']) == motion_id
    )
    return [
        round(float(point['time_sec']), 9)
        for point in curve['points']
        if start_sec - 1e-9 <= float(point['time_sec']) <= end_sec + 1e-9
    ]


def _values_in(layer, motion_id, start_sec, end_sec):
    curve = next(
        curve for curve in layer['point_curves']
        if str(curve['motion_id']) == motion_id
    )
    return [
        round(float(point['value_deg']), 6)
        for point in curve['points']
        if start_sec - 1e-9 <= float(point['time_sec']) <= end_sec + 1e-9
    ]


def test_range_copy_touches_every_selected_axis():
    layer = _two_axis_layer()
    before = _counts(layer)

    after = edit_layer(layer, {
        'operation': 'copy_point_range', 'motion_ids': ['1-1', '1-2'],
        'start_sec': 1.0, 'end_sec': 2.0, 'target_start_sec': 4.0,
    })

    assert set(_counts(after)) == {'1-1', '1-2'}
    # 개수가 아니라 **내용**을 본다 · 대체 때문에 개수는 그대로일 수 있다
    for motion_id in ('1-1', '1-2'):
        source_times = _times_in(layer, motion_id, 1.0, 2.0)
        assert source_times, f'{motion_id} 시험 자료에 복사할 포인트가 없다'
        span = source_times[-1] - source_times[0]
        source = _values_in(layer, motion_id, source_times[0], source_times[-1])
        pasted = _values_in(after, motion_id, 4.0, round(4.0 + span, 9))
        assert pasted == source, f'{motion_id} 축이 안 붙었다'
    assert point_curve_frame_mismatches(after) == []


def test_range_delete_touches_every_selected_axis():
    layer = _two_axis_layer()
    before = _counts(layer)

    after = edit_layer(layer, {
        'operation': 'delete_point_range', 'motion_ids': ['1-1', '1-2'],
        'start_sec': 1.0, 'end_sec': 2.0,
    })

    counts = _counts(after)
    assert counts['1-1'] < before['1-1'] and counts['1-2'] < before['1-2']
    assert point_curve_frame_mismatches(after) == []


def test_an_axis_without_points_in_the_range_is_skipped():
    """그 시간대에 포인트가 없는 축은 조용히 건너뛴다 · 나머지 축은 바뀐다."""
    layer = _two_axis_layer()
    # 1-2 의 곡선을 앞쪽으로만 남긴다
    trimmed = []
    for curve in layer['point_curves']:
        if str(curve['motion_id']) == '1-2':
            curve = dict(curve, points=[
                point for point in curve['points'] if point['time_sec'] <= 1.0
            ])
        trimmed.append(curve)
    layer = dict(layer, point_curves=trimmed)
    before = _counts(layer)

    after = edit_layer(layer, {
        'operation': 'delete_point_range', 'motion_ids': ['1-1', '1-2'],
        'start_sec': 3.0, 'end_sec': 4.0,
    })

    counts = _counts(after)
    assert counts['1-1'] < before['1-1'], '1-1 은 바뀌어야 한다'
    assert counts['1-2'] == before['1-2'], '1-2 는 그대로여야 한다'


def test_a_range_with_nothing_to_do_is_refused():
    layer = _two_axis_layer()
    with pytest.raises(ValueError, match='다룰 포인트가 없습니다'):
        edit_layer(layer, {
            'operation': 'delete_point_range', 'motion_ids': ['1-1', '1-2'],
            'start_sec': 90.0, 'end_sec': 95.0,
        })


# 포인트 고르기 단계를 따로 시험한다 · §6-128
#
# 전에는 560줄짜리 함수 안에 중첩 함수 열다섯 개가 있어, 단계 하나만 떼어
# 시험할 수 없었다 · 이제 1단계는 밖에서 부를 수 있다.


def _fit_context(samples, tolerance=1.0, order=3, limit=5000):
    from motion_studio.layer_editor import _FitContext
    ordered = sorted((round(float(t), 9), float(v)) for t, v in samples)
    return _FitContext(
        ordered=ordered, tolerance=tolerance, curve_order=order,
        point_limit=limit, prune_attempts=[10_000],
    )


def test_the_chord_stage_starts_from_three_points():
    from motion_studio.layer_editor import _select_by_chords
    flat = [(round(index * 0.02, 3), 5.0) for index in range(50)]
    selected = _select_by_chords(_fit_context(flat))
    # 곧은 자료는 더 넣을 이유가 없다 · 처음 셋 그대로
    assert selected == {0, 25, 49}


def test_the_chord_stage_respects_the_point_limit():
    from motion_studio.layer_editor import _select_by_chords
    context = _fit_context(_wiggly_samples(), tolerance=0.01, limit=20)
    assert len(_select_by_chords(context)) <= 20


def test_the_chord_stage_is_looser_for_curves():
    """직선(1차)은 허용 오차 그대로, 곡선은 느슨하게 · 그래서 덜 잡는다."""
    from motion_studio.layer_editor import _select_by_chords
    samples = _wiggly_samples()
    straight = _select_by_chords(_fit_context(samples, tolerance=1.0, order=1))
    curved = _select_by_chords(_fit_context(samples, tolerance=1.0, order=3))
    assert len(curved) < len(straight)


def test_the_context_draws_points_on_the_recorded_samples():
    from motion_studio.layer_editor import _select_by_chords
    samples = _wiggly_samples()
    context = _fit_context(samples)
    indices = sorted(_select_by_chords(context))
    points = context.points_at(indices)
    recorded = {round(t, 9): v for t, v in samples}
    assert all(round(p['time_sec'], 9) in recorded for p in points)
    assert all(
        abs(p['value_deg'] - recorded[round(p['time_sec'], 9)]) < 1e-9
        for p in points
    )
    assert max(context.errors_of(points)) >= 0.0
