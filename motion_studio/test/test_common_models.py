import pytest

from motion_studio.constants import DEFAULT_PERIOD_SEC
from motion_studio.mapping_model import manual_initial_values, motion_ranges
from motion_studio.motion_model import (
    layer_motion_ids,
    normalize_layer,
    point_curve_bounds,
    unique_motion_ids,
)
from motion_studio.project_store import (
    normalize_layer as store_normalize_layer,
    unique_motion_ids as store_unique_motion_ids,
)


def test_project_store_keeps_compatibility_exports_for_common_motion_model():
    assert store_normalize_layer is normalize_layer
    assert store_unique_motion_ids is unique_motion_ids


def test_motion_ids_and_layer_axes_include_frames_and_point_curves_once():
    assert unique_motion_ids(['1-1', '1-1', '2-3']) == ['1-1', '2-3']
    with pytest.raises(ValueError, match='invalid Motion ID'):
        unique_motion_ids(['0-1'])

    layer = {
        'frames': [{'values': {'1-1': 1.0, '2-3': 2.0}}],
        'point_curves': [
            {'motion_id': '2-3'},
            {'motion_id': '4-5'},
        ],
    }
    assert layer_motion_ids(layer) == {'1-1', '2-3', '4-5'}


def test_layer_normalization_uses_shared_period_and_sorts_curve_points():
    normalized = normalize_layer({
        'layer_id': 'layer-a',
        'frames': [{'values': {'1-1': 2}}],
        'point_curves': [{
            'curve_id': 'curve-a',
            'motion_id': '1-1',
            'interpolation_order': 3,
            'points': [
                {'point_id': 'second', 'time_sec': 0.04, 'value_deg': 2},
                {'point_id': 'first', 'time_sec': 0.02, 'value_deg': 1},
            ],
        }],
    })

    assert normalized['frames'][0]['time_sec'] == DEFAULT_PERIOD_SEC
    assert [
        point['point_id'] for point in normalized['point_curves'][0]['points']
    ] == ['first', 'second']
    assert point_curve_bounds(normalized['point_curves'][0]) == (0.02, 0.04)


def test_curve_bounds_reject_empty_or_non_finite_times():
    with pytest.raises(ValueError, match='at least one point'):
        point_curve_bounds({'points': []})
    with pytest.raises(ValueError, match='non-finite time'):
        point_curve_bounds({'points': [{'time_sec': 'not-a-number'}]})


@pytest.mark.parametrize('source', [
    {'rows': [
        {
            'motion_id': '1-1',
            'motion_lower_deg': -45,
            'motion_upper_deg': 90,
            'initial_mode': 'manual',
            'initial_motion_position_deg': 12,
        },
        {'motion_id': '2-1', 'initial_mode': 'first_frame'},
    ]},
    {'mapping_rows': [
        {
            'motion_id': '1-1',
            'motion_lower_deg': -45,
            'motion_upper_deg': 90,
            'initial_mode': 'manual',
            'initial_motion_position_deg': 12,
        },
        {'motion_id': '2-1', 'initial_mode': 'first_frame'},
    ]},
])
def test_mapping_models_accept_store_and_editor_payload_shapes(source):
    assert motion_ranges(source) == {
        '1-1': (-45.0, 90.0),
        '2-1': (-180.0, 180.0),
    }
    assert manual_initial_values(source) == {'1-1': 12.0}


def test_mapping_models_ignore_non_row_values():
    source = {'mapping_rows': [None, 'invalid', {}, {'motion_id': '3-2'}]}
    assert motion_ranges(source) == {'3-2': (-180.0, 180.0)}
    assert manual_initial_values(source) == {}
