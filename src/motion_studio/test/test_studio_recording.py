import json
import threading

import pytest

import motion_studio.studio_node as studio_module
from motion_studio.studio_node import (
    MotionStudioNode,
    next_numbered_layer_name,
    project_initial_motion_values,
)
from std_msgs.msg import String


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
        def save_project(value):
            return value

    node._store = Store()
    node._require_idle_locked = lambda: None
    node._require_project_locked = lambda: project
    node._project_result = lambda value, message: {'project': value, 'message': message}

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


def test_merge_commit_restores_source_points_when_editor_preview_omits_them():
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
            'layer_id': 'source-b', 'name': '일반 원본',
            'enabled': True, 'locked': False, 'edit_revision': 1,
            'point_curves': [],
            'frames': [
                {'frame': 3, 'time_sec': 0.06, 'values': {'2-1': 2.0}},
                {'frame': 4, 'time_sec': 0.08, 'values': {'2-1': 3.0}},
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
        def save_project(value):
            return value

    node._store = Store()
    node._require_idle_locked = lambda: None
    node._require_project_locked = lambda: project
    node._project_result = lambda value, message: {
        'success': True, 'project': value, 'message': message,
    }

    result = node._commit_merged_layer({
        'source_layer_ids': ['source-a', 'source-b'],
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
    assert len(merged['point_curves']) == 1
    assert merged['point_curves'][0]['curve_id'] == 'curve-source'
    assert [
        point['point_id'] for point in merged['point_curves'][0]['points']
    ] == ['point-start', 'point-end']
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
        def save_project(value):
            return value

    node._store = Store()
    node._require_idle_locked = lambda: None
    node._require_project_locked = lambda: project
    node._motion_ranges = lambda _mapping: {'1-1': (-5.0, 5.0)}
    node._project_result = lambda value, message: {
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
        def save_project(value):
            return value

    node._store = Store()
    node._require_idle_locked = lambda: None
    node._require_project_locked = lambda: project
    node._project_result = lambda value, message: {'project': value, 'message': message}

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


def test_playback_status_mirrors_motion_run_progress_for_web_graph():
    node = MotionStudioNode.__new__(MotionStudioNode)
    node._lock = threading.RLock()
    node._workspace_project_id = 'project-1'
    node._status = {
        'state': 'playing',
        'phase': 'playing',
        'elapsed_sec': 0.0,
        'playback_duration_sec': 12.0,
    }
    node._motion_run_status = {}
    node._execution_context = {'project_generation': 1}

    node._run_status_callback(String(data=json.dumps({
        'project_id': 'project-1',
        'execution_context': {'project_generation': 1},
        'request_source': 'motion_studio',
        'state': 'running',
        'progress': {
            'elapsed_sec': 3.2,
            'duration_sec': 12.0,
            'ratio': 3.2 / 12.0,
        },
    })))

    assert node._status['state'] == 'playing'
    assert node._status['elapsed_sec'] == 3.2
    assert node._status['playback_duration_sec'] == 12.0
    assert node._status['runtime_progress']['ratio'] == pytest.approx(3.2 / 12.0)

    node._run_status_callback(String(data=json.dumps({
        'project_id': 'other-project',
        'execution_context': {'project_generation': 1},
        'request_source': 'motion_studio',
        'state': 'running',
        'progress': {'elapsed_sec': 9.0, 'duration_sec': 12.0, 'ratio': 0.75},
    })))
    assert node._status['elapsed_sec'] == 3.2


def test_recording_snapshot_contains_bounded_live_graph_preview():
    node = MotionStudioNode.__new__(MotionStudioNode)
    node._lock = threading.RLock()
    node._status = {'state': 'recording'}
    node._current_project = None
    node._midi_state = {'channels': []}
    node._recorded_motion_ids = {'1-1'}
    node._record_mode = 'record'
    node._record_frames = [
        {'time_sec': index * 0.02, 'values': {'1-1': float(index)}}
        for index in range(1000)
    ]

    snapshot = node.snapshot()

    assert snapshot['recorded_frames'] == 1000
    assert snapshot['recording_preview_stride'] == 5
    assert len(snapshot['recording_preview_frames']) <= 241
    assert snapshot['recording_preview_frames'][-1]['time_sec'] == pytest.approx(19.98)


def test_stop_returns_immediately_and_defers_acknowledgement_waits(monkeypatch):
    node = MotionStudioNode.__new__(MotionStudioNode)
    node._lock = threading.RLock()
    node._status = {'state': 'playing', 'message': '재생 중'}
    node._operation_generation = 7
    node._current_project = None
    node._set_status_locked = lambda state, message: node._status.update({
        'state': state,
        'message': message,
    })
    node.snapshot = lambda: dict(node._status)
    started = []

    class DeferredThread:
        def __init__(self, *, target, args, daemon):
            started.append((target, args, daemon))

        def start(self):
            return None

    monkeypatch.setattr(studio_module.threading, 'Thread', DeferredThread)

    result = node._stop()

    assert result['success'] is True
    assert result['status']['state'] == 'stopping'
    assert node._operation_generation == 8
    assert len(started) == 1
    assert started[0][1] == (8, '모션 스튜디오 정지 완료')


def test_stop_sends_motion_stop_before_midi_cleanup():
    node = MotionStudioNode.__new__(MotionStudioNode)
    node._lock = threading.RLock()
    node._status = {'state': 'stopping'}
    node._operation_generation = 3
    calls = []
    node._request_run = lambda command, payload, timeout: (
        calls.append(('run', command)) or {'success': True}
    )
    node._request_midi = lambda command, payload, timeout: (
        calls.append(('midi', command)) or {'success': True}
    )
    node._set_status_locked = lambda state, message: node._status.update({
        'state': state,
        'message': message,
    })

    node._finish_stop(3, '정지 완료')

    assert calls == [
        ('run', 'stop'),
        ('midi', 'studio_recording_ready'),
    ]
    assert node._status == {'state': 'idle', 'message': '정지 완료'}


def test_stop_generation_invalidates_pending_playback_start():
    node = MotionStudioNode.__new__(MotionStudioNode)
    node._lock = threading.RLock()
    node._status = {'state': 'stopping'}
    node._operation_generation = 5

    with pytest.raises(RuntimeError, match='정지'):
        node._require_active_operation(4, 'initializing')


def test_standalone_initial_position_finishes_without_starting_playback():
    node = MotionStudioNode.__new__(MotionStudioNode)
    node._lock = threading.RLock()
    node._status = {'state': 'initializing'}
    node._operation_generation = 7
    node._motion_run_status = {'state': 'initialized', 'message': '초기 위치 이동 완료'}
    commands = []
    node._run_payload = lambda *_args: {'request_source': 'motion_studio'}
    node._request_run_for_operation = lambda command, *_args: (
        commands.append(command) or {'success': True}
    )
    node._set_status_locked = lambda state, message: node._status.update({
        'state': state,
        'message': message,
    })

    node._prepare_initial_position(
        {'mapping_file_id': 'mapping.yaml'}, 'initial.json', ['1-1'], 5.0, 7
    )

    assert commands == ['initialize']
    assert node._status == {'state': 'idle', 'message': '초기 위치 이동 완료'}


def test_standalone_initial_position_uses_zero_when_project_has_no_layers(monkeypatch):
    node = MotionStudioNode.__new__(MotionStudioNode)
    node._lock = threading.RLock()
    node._status = {'state': 'idle'}
    node._operation_generation = 0
    project = {
        'project_id': 'studio-project',
        'name': '빈 스튜디오',
        'mapping_file_id': 'mapping.yaml',
        'layers': [],
    }
    written = {}

    class Store:
        @staticmethod
        def mapping_check(_project):
            return {'motion_ids': ['1-1', '1-2'], 'rows': []}

        @staticmethod
        def write_motion_file(file_id, content, hidden=False):
            written.update(file_id=file_id, content=content, hidden=hidden)
            return 'initial.json'

    class DeferredThread:
        def __init__(self, target, args, daemon):
            self.target = target
            self.args = args
            self.daemon = daemon

        def start(self):
            return None

    node._store = Store()
    node._require_idle_locked = lambda: None
    node._require_project_locked = lambda: project
    node._validate_mapping_locked = lambda _project: None
    node._set_status_locked = lambda state, message: node._status.update({
        'state': state,
        'message': message,
    })
    node.snapshot = lambda: dict(node._status)
    monkeypatch.setattr(studio_module.threading, 'Thread', DeferredThread)

    result = node._start_initial_position({'initial_move_time_sec': 5})

    assert result['success'] is True
    assert written['hidden'] is True
    assert '[1,0.0,"1-1",0.0,"1-2",0.0]' in written['content']


def test_cancelled_operation_cannot_publish_a_late_start_command():
    node = MotionStudioNode.__new__(MotionStudioNode)
    node._lock = threading.RLock()
    node._status = {'state': 'stopping'}
    node._operation_generation = 9

    class RejectUnexpectedPublish:
        def publish(self, _message):
            raise AssertionError('취소된 재생 시작 명령이 발행되었습니다')

    node._request_pub = RejectUnexpectedPublish()

    result = node._request_run_for_operation(
        'start', {}, 1.0, operation_generation=8, expected_state='initializing'
    )

    assert result['success'] is False
    assert '정지' in result['message']
