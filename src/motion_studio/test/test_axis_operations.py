import copy

from motion_studio.axis_operations import apply_axis_operation


def test_axis_command_dispatch_adds_one_axis_without_touching_existing_track():
    working = {
        'frames': [
            {'time_sec': 0.02, 'values': {'1-1': 1.0}},
            {'time_sec': 0.04, 'values': {'1-1': 2.0}},
        ],
        'point_curves': [],
    }
    tracks = {'1-1': [(0.02, 1.0), (0.04, 2.0)]}
    original = copy.deepcopy(tracks['1-1'])

    handled = apply_axis_operation('add_axis', working, tracks, {
        'motion_ids': ['2-1'],
        'initial_value_deg': 3.0,
    })

    assert handled is True
    assert tracks['1-1'] == original
    assert tracks['2-1'] == [(0.02, 3.0), (0.04, 3.0)]


def test_axis_command_dispatch_ignores_non_axis_operations():
    working = {'frames': [], 'point_curves': []}
    tracks = {}

    assert apply_axis_operation('value_offset', working, tracks, {}) is False
    assert tracks == {}
