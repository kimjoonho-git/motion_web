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
