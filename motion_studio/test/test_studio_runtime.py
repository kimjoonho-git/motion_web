import json
import threading

import pytest

import motion_studio.studio_node as studio_module
from motion_studio.studio_node import MotionStudioNode
from std_msgs.msg import String


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


def test_playback_graph_waits_for_actual_runtime_running_state():
    node = MotionStudioNode.__new__(MotionStudioNode)
    node._lock = threading.RLock()
    node._workspace_project_id = 'project-1'
    node._execution_context = {'project_generation': 1}
    node._status = {
        'state': 'initializing',
        'phase': 'countdown',
        'elapsed_sec': 0.0,
        'playback_duration_sec': 7.84,
    }
    node._motion_run_status = {}
    node._set_status_locked = lambda state, message: node._status.update({
        'state': state,
        'phase': state,
        'message': message,
    })

    node._run_status_callback(String(data=json.dumps({
        'project_id': 'project-1',
        'execution_context': {'project_generation': 1},
        'request_source': 'motion_studio',
        'state': 'initialized',
        'progress': {
            'elapsed_sec': 5.0,
            'duration_sec': 5.0,
            'ratio': 1.0,
        },
    })))
    assert node._status['state'] == 'initializing'
    assert node._status['elapsed_sec'] == 0.0

    node._run_status_callback(String(data=json.dumps({
        'project_id': 'project-1',
        'execution_context': {'project_generation': 1},
        'request_source': 'motion_studio',
        'state': 'countdown',
        'message': '모션 시작 3초 전',
        'progress': {
            'elapsed_sec': 0.1,
            'duration_sec': 3.0,
            'ratio': 0.1 / 3.0,
        },
    })))
    assert node._status['state'] == 'initializing'
    assert node._status['phase'] == 'countdown'
    assert node._status['message'] == '모션 시작 3초 전'
    assert node._status['elapsed_sec'] == 0.0

    node._run_status_callback(String(data=json.dumps({
        'project_id': 'project-1',
        'execution_context': {'project_generation': 1},
        'request_source': 'motion_studio',
        'state': 'running',
        'progress': {
            'elapsed_sec': 0.02,
            'duration_sec': 7.84,
            'ratio': 0.02 / 7.84,
        },
    })))
    assert node._status['state'] == 'playing'
    assert node._status['elapsed_sec'] == 0.02
    assert node._status['playback_duration_sec'] == 7.84


def test_studio_ignores_status_from_cancelled_operation_generation():
    node = MotionStudioNode.__new__(MotionStudioNode)
    node._lock = threading.RLock()
    node._workspace_project_id = 'project-1'
    node._execution_context = {'project_generation': 1}
    node._operation_generation = 4
    node._status = {'state': 'initializing'}
    node._motion_run_status = {'state': 'idle'}

    node._run_status_callback(String(data=json.dumps({
        'project_id': 'project-1',
        'execution_context': {'project_generation': 1},
        'request_source': 'motion_studio',
        'operation_generation': 3,
        'state': 'running',
    })))

    assert node._motion_run_status == {'state': 'idle'}
    assert node._status == {'state': 'initializing'}


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


def test_recording_stop_reports_only_the_new_layer_for_sync(monkeypatch):
    node = MotionStudioNode.__new__(MotionStudioNode)
    node._lock = threading.RLock()
    node._status = {'state': 'recording', 'message': '녹화 중'}
    node._operation_generation = 2
    node._current_project = {'project_id': 'studio', 'layers': []}
    node._finish_record_locked = lambda: (
        node._status.update({'message': '녹화 완료'})
        or 'layer-new'
    )
    node._set_status_locked = lambda state, message: node._status.update({
        'state': state,
        'message': message,
    })
    node.snapshot = lambda: dict(node._status)

    class DeferredThread:
        def __init__(self, *, target, args, daemon):
            pass

        def start(self):
            return None

    monkeypatch.setattr(studio_module.threading, 'Thread', DeferredThread)

    result = node._stop()

    assert result['layer_sync'] == {
        'upsert_layer_ids': ['layer-new'],
        'delete_layer_ids': [],
    }


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


def test_playback_sends_one_runtime_owned_sequence_request():
    node = MotionStudioNode.__new__(MotionStudioNode)
    node._lock = threading.RLock()
    node._status = {'state': 'initializing'}
    node._operation_generation = 8
    node._run_payload = lambda *_args: {
        'request_source': 'motion_studio',
        'motion_file_id': 'preview.json',
    }
    commands = []

    def request(command, payload, *_args):
        commands.append((command, dict(payload)))
        return {'success': True}

    node._request_run_for_operation = request
    node._set_status_locked = lambda state, message: node._status.update({
        'state': state,
        'message': message,
    })

    node._prepare_playback(
        {'mapping_file_id': 'mapping.yaml'},
        'preview.json',
        ['1-1'],
        5.0,
        8,
    )

    assert [command for command, _payload in commands] == ['start']
    assert commands[0][1]['countdown_sec'] == 3.0
    assert node._status['state'] == 'initializing'


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
