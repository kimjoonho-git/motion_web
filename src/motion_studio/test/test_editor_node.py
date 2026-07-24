from types import SimpleNamespace
from unittest.mock import patch

from rclpy.node import Node

from motion_studio.editor_node import MotionStudioEditorNode


def base_layer():
    return {
        'layer_id': 'layer',
        'name': '레이어',
        'enabled': True,
        'locked': False,
        'frames': [
            {'frame': 1, 'time_sec': 0.02, 'values': {'1-1': 10.0}},
            {'frame': 2, 'time_sec': 0.04, 'values': {'1-1': 20.0}},
        ],
    }


def test_editor_node_keeps_global_ros_arguments_enabled_for_parameter_overrides():
    declared = {
        'request_topic': '/isolated/editor/request',
        'response_topic': '/isolated/editor/response',
    }
    editor = MotionStudioEditorNode.__new__(MotionStudioEditorNode)

    with (
        patch.object(Node, '__init__', return_value=None) as node_init,
        patch.object(
            MotionStudioEditorNode,
            'declare_parameter',
            side_effect=lambda name, _default: SimpleNamespace(value=declared[name]),
        ),
        patch.object(MotionStudioEditorNode, 'create_publisher', return_value=object()),
        patch.object(MotionStudioEditorNode, 'create_subscription'),
        patch.object(
            MotionStudioEditorNode,
            'get_logger',
            return_value=SimpleNamespace(info=lambda _message: None),
        ),
    ):
        MotionStudioEditorNode.__init__(editor)

    node_init.assert_called_once_with('motion_studio_editor_node')
    assert editor.request_topic == '/isolated/editor/request'
    assert editor.response_topic == '/isolated/editor/response'


def test_editor_allows_unregistered_motion_id_for_axis_addition():
    editor = MotionStudioEditorNode.__new__(MotionStudioEditorNode)

    result = editor._handle('edit', {
        'layer': base_layer(),
        'project': {},
        'operation': 'add_axis',
        'motion_ids': ['9-9'],
        'initial_value_deg': 3.0,
        'mapping_rows': [],
    })

    assert result['success'] is True
    assert [frame['values']['9-9'] for frame in result['layer']['frames']] == [3.0, 3.0]


def test_editor_allows_unregistered_target_for_axis_copy():
    editor = MotionStudioEditorNode.__new__(MotionStudioEditorNode)

    result = editor._handle('edit', {
        'layer': base_layer(),
        'project': {},
        'operation': 'copy_axis',
        'source_motion_id': '1-1',
        'motion_ids': ['9-9'],
        'mapping_rows': [],
    })

    assert result['success'] is True
    assert [frame['values']['9-9'] for frame in result['layer']['frames']] == [10.0, 20.0]


def test_editor_returns_spike_correction_preview_report():
    editor = MotionStudioEditorNode.__new__(MotionStudioEditorNode)
    layer = base_layer()
    layer['frames'] = [
        {'frame': index + 1, 'time_sec': index * 0.02, 'values': {'1-1': value}}
        for index, value in enumerate([0.0, 1.0, 2.0, 4.0, 4.0, 5.0, 6.0])
    ]

    result = editor._handle('edit', {
        'layer': layer,
        'project': {},
        'operation': 'repair_spikes',
        'motion_ids': ['1-1'],
        'start_sec': 0.0,
        'end_sec': 0.12,
        'spike_detection_threshold_deg': 0.1,
        'spike_maximum_correction_deg': 2.0,
        'mapping_rows': [],
    })

    assert result['success'] is True
    assert result['operation_report']['changed_count'] == 1
    assert result['operation_report']['changed'][0]['time_sec'] == 0.06


def test_editor_returns_manual_point_conversion_approximation_report():
    editor = MotionStudioEditorNode.__new__(MotionStudioEditorNode)
    layer = base_layer()
    layer['frames'] = [
        {'frame': index + 1, 'time_sec': index * 0.02, 'values': {'1-1': value}}
        for index, value in enumerate([0.0, 0.0, 5.0, 10.0, 10.0])
    ]

    result = editor._handle('edit', {
        'layer': layer,
        'project': {},
        'operation': 'convert_motion_to_point_curve',
        'motion_ids': ['1-1'],
        'selection_kind': 'motion',
        'start_sec': 0.0,
        'end_sec': 0.08,
        'approximation_tolerance_deg': 0.01,
        'approximation_maximum_points': 20,
        'approximation_interpolation_order': 3,
        'curve_id': 'curve_fitted',
        'mapping_rows': [],
    })

    assert result['success'] is True
    assert result['operation_report']['operation'] == 'convert_motion_to_point_curve'
    assert result['operation_report']['interpolation_order'] == 3
    assert result['operation_report']['source_sample_count'] == 5
    assert result['operation_report']['point_count'] >= 3
    assert result['validation']['point_curve_mismatches'] == []


def test_editor_warns_but_does_not_block_values_outside_axis_range():
    editor = MotionStudioEditorNode.__new__(MotionStudioEditorNode)

    result = editor._handle('edit', {
        'layer': base_layer(),
        'project': {},
        'operation': 'convert_motion_to_point_curve',
        'motion_ids': ['1-1'],
        'selection_kind': 'motion',
        'start_sec': 0.02,
        'end_sec': 0.04,
        'approximation_tolerance_deg': 0.1,
        'approximation_interpolation_order': 3,
        'curve_id': 'outside-range',
        'mapping_rows': [{
            'motion_id': '1-1',
            'motion_lower_deg': -5.0,
            'motion_upper_deg': 5.0,
        }],
    })

    assert result['success'] is True
    assert result['validation']['playable'] is True
    assert [
        (warning['time_sec'], warning['value_deg'])
        for warning in result['validation']['range_warnings']
    ] == [(0.02, 10.0), (0.04, 20.0)]


def test_point_approximation_results_are_isolated_between_projects():
    editor = MotionStudioEditorNode.__new__(MotionStudioEditorNode)

    def convert(project_id, layer_id, track_values):
        layer = {
            'layer_id': layer_id,
            'name': project_id,
            'enabled': True,
            'locked': False,
            'frames': [
                {
                    'frame': index + 1,
                    'time_sec': index * 0.02,
                    'values': {'1-1': value},
                }
                for index, value in enumerate(track_values)
            ],
        }
        return editor._handle('edit', {
            'layer': layer,
            'project': {'project_id': project_id, 'layers': [layer]},
            'operation': 'convert_motion_to_point_curve',
            'motion_ids': ['1-1'],
            'selection_kind': 'motion',
            'start_sec': 0.0,
            'end_sec': (len(track_values) - 1) * 0.02,
            'approximation_tolerance_deg': 0.01,
            'approximation_maximum_points': 50,
            'approximation_interpolation_order': 3,
            'curve_id': f'curve-{project_id}',
            'mapping_rows': [],
        })

    first = convert('project-a', 'layer-a', [0.0, 1.0, 2.0, 3.0, 4.0])
    second = convert('project-b', 'layer-b', [10.0, 0.0, 10.0, 0.0, 10.0])

    assert first['layer']['layer_id'] == 'layer-a'
    assert second['layer']['layer_id'] == 'layer-b'
    assert first['layer']['point_curves'][0]['curve_id'] == 'curve-project-a'
    assert second['layer']['point_curves'][0]['curve_id'] == 'curve-project-b'
    assert first['layer']['frames'] != second['layer']['frames']


def test_editor_merge_response_preserves_source_point_curves():
    editor = MotionStudioEditorNode.__new__(MotionStudioEditorNode)
    first = {
        'layer_id': 'a',
        'name': '첫 곡선',
        'enabled': True,
        'locked': False,
        'frames': [
            {'frame': 1, 'time_sec': 0.02, 'values': {'1-1': 1.0}},
            {'frame': 2, 'time_sec': 0.04, 'values': {'1-1': 2.0}},
        ],
        'point_curves': [{
            'curve_id': 'curve-a',
            'motion_id': '1-1',
            'interpolation_order': 1,
            'points': [
                {'point_id': 'a-start', 'time_sec': 0.02, 'value_deg': 1.0},
                {'point_id': 'a-end', 'time_sec': 0.04, 'value_deg': 2.0},
            ],
        }],
    }
    second = {
        'layer_id': 'b',
        'name': '둘째 곡선',
        'enabled': True,
        'locked': False,
        'frames': [
            {'frame': 3, 'time_sec': 0.06, 'values': {'2-1': 3.0}},
            {'frame': 4, 'time_sec': 0.08, 'values': {'2-1': 4.0}},
        ],
        'point_curves': [{
            'curve_id': 'curve-b',
            'motion_id': '2-1',
            'interpolation_order': 1,
            'points': [
                {'point_id': 'b-start', 'time_sec': 0.06, 'value_deg': 3.0},
                {'point_id': 'b-end', 'time_sec': 0.08, 'value_deg': 4.0},
            ],
        }],
    }

    result = editor._handle('merge', {
        'project': {'period_sec': 0.02, 'layers': [first, second]},
        'layer_ids': ['a', 'b'],
        'mapping_rows': [],
    })

    assert result['success'] is True
    assert {
        curve['curve_id'] for curve in result['layer']['point_curves']
    } == {'curve-a', 'curve-b'}
