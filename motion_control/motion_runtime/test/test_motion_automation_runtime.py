import threading
import time

from motion_runtime.motion_automation_store import default_automation_state
from motion_runtime.motion_run_manager import MotionRunManager


class _Logger:
    def error(self, _message):
        pass


def _manager():
    manager = MotionRunManager.__new__(MotionRunManager)
    manager.period_sec = 0.001
    manager._run_lock = threading.RLock()
    manager._stop_event = threading.Event()
    manager._graceful_stop_event = threading.Event()
    manager._status = manager._empty_status()
    manager._execution_context = {}
    manager._execution_context_ready = True
    manager._automation_state = {
        **default_automation_state(),
        'enabled': True,
        'armed': True,
    }
    manager._automation_runtime = {
        'state': 'starting',
        'message': '',
        'resume_pending': False,
        'stop_after_cycle': False,
    }
    manager._automation_project_id = 'project'
    manager._publish_status = lambda: None
    manager._require_playback_command_allowed = lambda: None
    manager._current_motors = lambda: []
    manager._prepare_motion_stream = lambda _motors, _axes: None
    manager._publish_motion_setpoints = lambda *_args, **_kwargs: None
    manager._sleep_until = lambda _deadline: None
    manager._current_servo_alarm_grade = lambda: 0
    manager.get_logger = lambda: _Logger()
    return manager


def _plan(repeat_mode='direct'):
    return {
        'project_id': 'project',
        'request_source': 'motion_run',
        'motion_file_id': 'motion.json',
        'mapping_file_id': 'mapping.yaml',
        'run_mode': 'continuous',
        'automation_run': True,
        'repeat_mode': repeat_mode,
        'dwell_sec': 0.1,
        'axes': [],
        'samples': [{
            'time_sec': 0.0,
            'positions': {},
            'motion_values': {},
        }],
        'warnings': [],
        'capabilities': {},
        'summary': {
            'duration_sec': 0.0,
            'sample_count': 1,
        },
    }


def test_direct_repeat_finishes_current_cycle_after_graceful_stop_request():
    manager = _manager()
    publishes = []

    def publish(*_args, **_kwargs):
        publishes.append('sample')
        manager._graceful_stop_event.set()

    manager._publish_motion_setpoints = publish
    plan = _plan()
    plan['samples'][0]['positions'] = {0: 0.0}

    manager._run_motion(plan)

    assert publishes == ['sample']
    assert manager.status()['state'] == 'stopped'
    assert manager.status()['cycle_count'] == 1
    assert '현재 모션 회차 완료 후' in manager.status()['message']


def test_dwell_repeat_uses_one_transition_handler_between_cycles():
    manager = _manager()
    transitions = []
    manager._wait_between_cycles = lambda _plan, _started, cycle, seconds: (
        transitions.append((cycle, seconds)) or False
    )

    manager._run_motion(_plan('dwell'))

    assert transitions == [(1, 0.1)]

def test_dwell_status_holds_motion_progress_at_file_end():
    manager = _manager()
    plan = _plan('dwell')
    plan['summary']['duration_sec'] = 8.98
    plan['samples'] = [
        {
            'time_sec': 8.98,
            'positions': {3: 10.0},
            'motion_values': {'1-2': 1.0},
        },
    ]
    waiting_status = {}
    manager._finish_cycle_stop = lambda *_args, **_kwargs: waiting_status.update(
        manager.status()
    )
    manager._graceful_stop_event.set()

    completed = manager._wait_between_cycles(
        plan,
        time.time(),
        1,
        4.0,
    )

    assert completed is False
    assert waiting_status['progress']['elapsed_sec'] == 8.98
    assert waiting_status['progress']['ratio'] == 1.0


def test_next_cycle_status_uses_new_phase_start_time(monkeypatch):
    manager = _manager()
    monkeypatch.setattr(time, 'time', lambda: 200.0)

    manager._restore_running_status(_plan('dwell'), 100.0, 1)

    status = manager.status()
    assert status['phase_started_at'] == 200.0
    assert status['current_cycle'] == 2
    assert status['progress']['elapsed_sec'] == 0.0


def test_reinitialize_repeat_moves_to_initial_position_between_cycles():
    manager = _manager()
    calls = []

    def initialize(plan):
        calls.append(plan['name'])
        manager._status = {
            **manager._status,
            'state': 'initialized',
        }
        manager._graceful_stop_event.set()

    manager._run_initialization = initialize
    manager._run_motion(_plan('reinitialize'), {'name': 'all-enabled-axes'})

    assert calls == ['all-enabled-axes']
    assert manager.status()['state'] == 'stopped'
    assert '초기위치 이동 완료 후' in manager.status()['message']


def test_grade_one_alarm_allows_current_cycle_then_blocks_next_cycle():
    manager = _manager()
    failures = []
    manager._current_servo_alarm_grade = lambda: 1
    manager._automation_failure = failures.append

    manager._run_motion(_plan())

    assert failures == ['1등급 서보 에러 · 나머지 축의 현재 회차 완료 후 자동 반복 중단']
    assert manager.status()['state'] == 'error'
    assert manager.status()['cycle_count'] == 1


def test_reinitialize_repeat_does_not_require_direct_loop_seam():
    reason = MotionRunManager._motion_auto_start_guard_error({
        'run_mode': 'continuous',
        'repeat_mode': 'reinitialize',
        'capabilities': {
            'continuous_run': {
                'available': False,
                'reason': '시작·종료값 차이 초과',
            },
        },
    })

    assert reason == ''


def test_disable_during_first_initialization_does_not_start_motion():
    manager = _manager()
    manager._run_initialization = lambda _plan: (
        manager._update_status({'state': 'initialized'})
    )
    manager._graceful_stop_event.set()
    calls = []
    manager._run_motion = lambda *_args: calls.append('motion')

    manager._run_initialization_then_motion(
        {'automation_run': True},
        _plan(),
    )

    assert calls == []
    assert manager.status()['state'] == 'stopped'
    assert '초기위치 이동 완료 후' in manager.status()['message']


def test_confirmed_context_schedules_only_armed_automation_for_restart():
    manager = _manager()
    manager._execution_context = {
        'context_id': 'context-1',
        'project_id': 'project',
    }
    manager._automation_state.update({
        'enabled': True,
        'armed': True,
    })
    manager._automation_resume_pending = False
    manager._automation_resume_started_at = None

    result = manager._confirm_execution_context({'context_id': 'context-1'})

    assert result['success'] is True
    assert manager._execution_context_ready is True
    assert manager._automation_resume_pending is True
    assert manager._automation_runtime['resume_pending'] is True


def test_confirmed_context_does_not_start_enabled_but_unarmed_automation():
    manager = _manager()
    manager._execution_context = {
        'context_id': 'context-1',
        'project_id': 'project',
    }
    manager._automation_state.update({
        'enabled': True,
        'armed': False,
    })
    manager._automation_resume_pending = False

    manager._confirm_execution_context({'context_id': 'context-1'})

    assert manager._automation_resume_pending is False
