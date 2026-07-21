import pytest

from motion_studio.layer_editor import edit_layer, merge_layers
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
    assert [point['point_id'] for point in result['point_curves'][0]['points']] == ['p1', 'p2']
    assert values(result) == pytest.approx([10.0, 20.0, 30.0, 40.0])


def test_frame_edit_cannot_silently_desynchronize_a_point_curve():
    source = edit_layer(layer(), {
        'operation': 'point_curve', 'motion_ids': ['1-1'], 'curve_id': 'curve_user',
        'points': [
            {'point_id': 'p1', 'time_sec': 0.02, 'value_deg': 10.0},
            {'point_id': 'p2', 'time_sec': 0.08, 'value_deg': 40.0},
        ],
    })
    with pytest.raises(ValueError, match='포인트 곡선'):
        edit_layer(source, request('value_offset', offset_deg=2.0))


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
