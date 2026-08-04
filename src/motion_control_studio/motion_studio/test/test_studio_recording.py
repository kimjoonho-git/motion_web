import threading

import pytest

from motion_studio.studio_node import (
    MotionStudioNode,
    next_numbered_layer_name,
    project_initial_motion_values,
)


def test_studio_node_rejects_previous_project_generation():
    node = MotionStudioNode.__new__(MotionStudioNode)
    node._project_generation = 3

    with pytest.raises(ValueError, match='이전 프로젝트 세대'):
        node._validate_request_generation(
            'invalidate_context', 2, {'project_generation': 2}
        )

    with pytest.raises(ValueError, match='현재 프로젝트 세대'):
        node._validate_request_generation('save', 4, {'project_generation': 4})


def test_recording_layer_name_does_not_repeat_after_delete_or_duplicate():
    layers = [
        {'name': '녹화 2'},
        {'name': '녹화 2'},
        {'name': '사용자 이름'},
    ]

    assert next_numbered_layer_name(layers, '녹화') == '녹화 3'
    assert next_numbered_layer_name([{'name': '녹화 1'}, {'name': '녹화 3'}], '녹화') == '녹화 4'


def test_incremental_composition_rechecks_affected_axis_and_keeps_other_axis():
    node = MotionStudioNode.__new__(MotionStudioNode)
    node._composition_cache_project_id = ''
    node._composition_cache = {}
    mapping = {'rows': [], 'motion_ids': ['1-1', '2-2']}
    project = {
        'project_id': 'project',
        'period_sec': 0.02,
        'layers': [
            {
                'layer_id': 'first',
                'enabled': True,
                'frames': [{
                    'frame': 1,
                    'time_sec': 0.02,
                    'values': {'1-1': 1.0, '2-2': 1.0},
                }],
            },
            {
                'layer_id': 'second',
                'enabled': True,
                'frames': [{
                    'frame': 1,
                    'time_sec': 0.02,
                    'values': {'1-1': 2.0, '2-2': 2.0},
                }],
            },
        ],
    }

    initial = node._project_composition(project, mapping)
    assert {item['motion_id'] for item in initial['conflicts']} == {'1-1', '2-2'}

    project['layers'][1]['frames'][0]['values'].pop('1-1')
    updated = node._project_composition(
        project,
        mapping,
        affected_motion_ids={'1-1'},
        affected_layer_ids={'second'},
    )

    assert {item['motion_id'] for item in updated['conflicts']} == {'2-2'}


def test_terminal_status_clears_playback_and_progress_metadata():
    node = MotionStudioNode.__new__(MotionStudioNode)
    node._status = {
        'state': 'playing',
        'runtime_progress': {'ratio': 0.5},
        'initialization_progress': {'ratio': 1.0},
        'playback_duration_sec': 12.0,
        'playback_layer_count': 3,
    }
    node._current_project = None
    node._store = type('Store', (), {'summary': staticmethod(lambda project: project)})()
    node._selected_motion_values_locked = lambda: {}
    node._recorded_motion_ids = set()
    node._record_mode = 'record'

    node._set_status_locked('error', '재생 실패')

    assert node._status['message'] == '재생 실패'
    assert node._status['runtime_progress'] == {}
    assert node._status['initialization_progress'] == {}
    assert node._status['playback_duration_sec'] == 0.0
    assert node._status['playback_layer_count'] == 0


def test_layer_duplicate_is_independent_unlocked_and_disabled():
    node = MotionStudioNode.__new__(MotionStudioNode)
    node._lock = threading.RLock()
    project = {'layers': [{
        'layer_id': 'source', 'name': '원본', 'enabled': True, 'locked': True,
        'created_at': 1.0, 'edit_revision': 4,
        'point_curves': [{
            'curve_id': 'curve_old', 'motion_id': '1-1',
            'points': [
                {'point_id': 'point_a', 'time_sec': 0.02, 'value_deg': 0.0},
                {'point_id': 'point_b', 'time_sec': 0.04, 'value_deg': 1.0},
            ],
        }],
        'frames': [
            {'frame': 1, 'time_sec': 0.02, 'values': {'1-1': 0.0}},
            {'frame': 2, 'time_sec': 0.04, 'values': {'1-1': 1.0}},
        ],
    }]}

    class Store:
        @staticmethod
        def save_project(value, **_kwargs):
            return value

    node._store = Store()
    node._require_idle_locked = lambda: None
    node._require_project_locked = lambda: project
    node._project_result = lambda value, message, **_kwargs: {
        'project': value, 'message': message,
    }

    result = node._duplicate_layer({'layer_id': 'source'})
    copied = result['project']['layers'][1]

    assert copied['enabled'] is False
    assert copied['locked'] is False
    assert copied['copied_from_layer_id'] == 'source'
    assert copied['layer_id'] != 'source'
    assert copied['point_curves'][0]['curve_id'] != 'curve_old'
    assert copied['point_curves'][0]['points'][0]['point_id'] != 'point_a'
    copied['frames'][0]['values']['1-1'] = 99.0
    assert project['layers'][0]['frames'][0]['values']['1-1'] == 0.0


def test_merge_commit_rebuilds_all_source_points_when_editor_preview_omits_them():
    node = MotionStudioNode.__new__(MotionStudioNode)
    node._lock = threading.RLock()
    point_curve = {
        'curve_id': 'curve-source',
        'motion_id': '1-1',
        'interpolation_order': 1,
        'points': [
            {'point_id': 'point-start', 'time_sec': 0.02, 'value_deg': 0.0},
            {'point_id': 'point-end', 'time_sec': 0.04, 'value_deg': 1.0},
        ],
    }
    project = {'layers': [
        {
            'layer_id': 'source-a', 'name': '포인트 원본',
            'enabled': True, 'locked': False, 'edit_revision': 3,
            'point_curves': [point_curve],
            'frames': [
                {'frame': 1, 'time_sec': 0.02, 'values': {'1-1': 0.0}},
                {'frame': 2, 'time_sec': 0.04, 'values': {'1-1': 1.0}},
            ],
        },
        {
            'layer_id': 'source-b', 'name': '둘째 포인트 원본',
            'enabled': True, 'locked': False, 'edit_revision': 1,
            'point_curves': [{
                'curve_id': 'curve-source-b',
                'motion_id': '2-1',
                'interpolation_order': 1,
                'points': [
                    {'point_id': 'point-b-start', 'time_sec': 0.02, 'value_deg': 2.0},
                    {'point_id': 'point-b-end', 'time_sec': 0.04, 'value_deg': 3.0},
                ],
            }],
            'frames': [
                {'frame': 1, 'time_sec': 0.02, 'values': {'2-1': 2.0}},
                {'frame': 2, 'time_sec': 0.04, 'values': {'2-1': 3.0}},
            ],
        },
    ]}

    class Store:
        @staticmethod
        def mapping_check(_project):
            return {'rows': [
                {
                    'motion_id': motion_id,
                    'motion_lower_deg': -180.0,
                    'motion_upper_deg': 180.0,
                }
                for motion_id in ('1-1', '2-1')
            ]}

        @staticmethod
        def save_project(value, **_kwargs):
            return value

    node._store = Store()
    node._require_idle_locked = lambda: None
    node._require_project_locked = lambda: project
    node._project_result = lambda value, message, **_kwargs: {
        'success': True, 'project': value, 'message': message,
    }

    result = node._commit_merged_layer({
        'source_layer_ids': ['source-a', 'source-b'],
        'append_layer_id': 'source-b',
        'source_revisions': {'source-a': 3, 'source-b': 1},
        'name': '방어 병합',
        # Simulate a preview produced by the previously running editor build.
        'layer': {
            'layer_id': 'preview',
            'name': '구버전 미리보기',
            'source_layer_ids': ['source-a', 'source-b'],
            'point_curves': [],
            'frames': [
                {'frame': 1, 'time_sec': 0.02, 'values': {'1-1': 0.0}},
                {'frame': 2, 'time_sec': 0.04, 'values': {'1-1': 1.0}},
                {'frame': 3, 'time_sec': 0.06, 'values': {'2-1': 2.0}},
                {'frame': 4, 'time_sec': 0.08, 'values': {'2-1': 3.0}},
            ],
        },
    })

    merged = result['project']['layers'][-1]
    assert merged['name'] == '방어 병합'
    assert {
        curve['curve_id'] for curve in merged['point_curves']
    } == {'curve-source', 'curve-source-b'}
    appended_curve = next(
        curve for curve in merged['point_curves']
        if curve['curve_id'] == 'curve-source-b'
    )
    assert [point['time_sec'] for point in appended_curve['points']] == [0.06, 0.08]
    assert result['merge_report'] == {
        'mode': 'append',
        'append_layer_id': 'source-b',
        'append_offset_sec': 0.04,
    }
    assert project['layers'][0]['point_curves'] == [point_curve]


def test_layer_save_warns_but_keeps_values_outside_axis_range():
    node = MotionStudioNode.__new__(MotionStudioNode)
    node._lock = threading.RLock()
    project = {'layers': [{
        'layer_id': 'layer', 'name': '범위 경고',
        'enabled': True, 'locked': False, 'created_at': 1.0,
        'edit_revision': 0,
        'frames': [
            {'frame': 1, 'time_sec': 0.0, 'values': {'1-1': 0.0}},
            {'frame': 2, 'time_sec': 0.02, 'values': {'1-1': 1.0}},
        ],
    }]}

    class Store:
        @staticmethod
        def mapping_check(_project):
            return {'motion_ids': ['1-1']}

        @staticmethod
        def save_project(value, **_kwargs):
            return value

    node._store = Store()
    node._require_idle_locked = lambda: None
    node._require_project_locked = lambda: project
    node._motion_ranges = lambda _mapping: {'1-1': (-5.0, 5.0)}
    node._project_result = lambda value, message, **_kwargs: {
        'success': True, 'project': value, 'message': message,
    }

    result = node._replace_layer_data({
        'layer_id': 'layer',
        'original_revision': 0,
        'layer': {
            **project['layers'][0],
            'edit_revision': 1,
            'frames': [
                {'frame': 1, 'time_sec': 0.0, 'values': {'1-1': 10.0}},
                {'frame': 2, 'time_sec': 0.02, 'values': {'1-1': 20.0}},
            ],
        },
    })

    assert result['success'] is True
    assert len(result['range_warnings']) == 2
    assert result['project']['layers'][0]['frames'][1]['values']['1-1'] == 20.0


def test_create_layer_adds_empty_disabled_editable_layer():
    node = MotionStudioNode.__new__(MotionStudioNode)
    node._lock = threading.RLock()
    project = {'layers': [{'layer_id': 'existing', 'name': '새 레이어 1'}]}

    class Store:
        @staticmethod
        def save_project(value, **_kwargs):
            return value

    node._store = Store()
    node._require_idle_locked = lambda: None
    node._require_project_locked = lambda: project
    node._project_result = lambda value, message, **_kwargs: {
        'project': value, 'message': message,
    }

    result = node._create_layer({})
    created = result['project']['layers'][-1]

    assert result['layer_id'] == created['layer_id']
    assert created['name'] == '새 레이어 2'
    assert created['enabled'] is False
    assert created['locked'] is False
    assert created['frames'] == []
    assert created['point_curves'] == []


def test_motion_actions_are_blocked_until_point_curve_mismatch_is_resolved():
    project = {'layers': [{
        'layer_id': 'bad', 'name': '불일치 레이어',
        'point_curves': [{
            'curve_id': 'curve', 'motion_id': '1-1',
            'points': [
                {'point_id': 'a', 'time_sec': 0.02, 'value_deg': 0.0},
                {'point_id': 'b', 'time_sec': 0.04, 'value_deg': 10.0},
            ],
        }],
        'frames': [
            {'frame': 1, 'time_sec': 0.02, 'values': {'1-1': 0.0}},
            {'frame': 2, 'time_sec': 0.04, 'values': {'1-1': 5.0}},
        ],
    }]}

    with pytest.raises(ValueError, match='포인트 곡선과 20ms 프레임이 다릅니다'):
        MotionStudioNode._require_point_curve_consistency(project, '합성 미리보기')


def test_initial_position_uses_first_recorded_value_even_when_track_starts_late():
    project = {'layers': [{
        'enabled': True,
        'frames': [
            {'time_sec': 3.0, 'values': {'1-1': 30.0}},
            {'time_sec': 3.02, 'values': {'1-1': 31.0}},
        ],
    }]}

    assert project_initial_motion_values(project, ['1-1']) == {'1-1': 30.0}


def test_recording_expands_one_selected_midi_channel_to_all_linked_motion_ids():
    node = MotionStudioNode.__new__(MotionStudioNode)
    node._midi_state = {
        'channels': [{
            'control_enabled': True,
            'motion_id': '1-1',
            'motion_value_deg': 12.5,
            'motion_values_deg': {
                '1-1': 12.5,
                '1-2': 12.5,
                '3-1': 12.5,
            },
        }],
    }

    assert node._selected_motion_values_locked() == {
        '1-1': 12.5,
        '1-2': 12.5,
        '3-1': 12.5,
    }


def test_studio_confirmation_returns_standard_context_acknowledgement():
    node = MotionStudioNode.__new__(MotionStudioNode)
    node._lock = threading.RLock()
    node._execution_context_ready = False
    node._execution_context = {
        'context_id': 'context-1',
        'project_id': 'project-1',
        'mapping_file_id': 'mapping.yaml',
        'mapping_sha256': 'mapping-sha',
    }
    node._select_workspace = lambda _payload: None
    node.snapshot = lambda: {
        'success': True,
        'execution_context': {
            **node._execution_context,
            'ready': node._execution_context_ready,
        },
    }

    result = node._handle('confirm_context', {'context_id': 'context-1'})

    assert result['success'] is True
    assert result['context_id'] == 'context-1'
    assert result['project_id'] == 'project-1'
    assert result['status']['execution_context']['ready'] is True


def test_recording_ignores_a_motion_command_rejected_by_limit_validation():
    node = MotionStudioNode.__new__(MotionStudioNode)
    node._midi_state = {
        'channels': [{
            'control_enabled': True,
            'motion_group_valid': True,
            'motion_command_valid': False,
            'motion_id': '3-1',
            'motion_value_deg': 179.934,
            'motion_values_deg': {'3-1': 179.934},
        }],
    }

    assert node._selected_motion_values_locked() == {}


def test_recording_waits_until_all_physical_midi_faders_reach_zero():
    node = MotionStudioNode.__new__(MotionStudioNode)
    node._lock = threading.RLock()
    node._status = {'state': 'initializing'}
    node._publish_status = lambda: None
    replies = iter([
        {
            'success': True,
            'ready': False,
            'device_connected': True,
            'message': 'MIDI 페이더 0 복귀 대기: 채널 2',
        },
        {'success': True, 'ready': True, 'device_connected': True},
    ])
    node._request_midi = lambda *_args: next(replies)

    node._wait_for_midi_faders_zero(1.0)

    assert node._status['phase'] == 'midi_zero_wait'


def test_recording_blocks_motor_initialization_when_midi_is_disconnected():
    node = MotionStudioNode.__new__(MotionStudioNode)
    node._lock = threading.RLock()
    node._status = {'state': 'initializing'}
    node._publish_status = lambda: None
    node._request_midi = lambda *_args: {
        'success': True,
        'ready': False,
        'device_connected': False,
    }

    with pytest.raises(ValueError, match='MIDI 장치 연결이 끊겨'):
        node._wait_for_midi_faders_zero(1.0)
