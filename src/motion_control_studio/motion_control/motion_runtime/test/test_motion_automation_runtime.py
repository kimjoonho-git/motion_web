from unittest import mock

import pytest
import threading
import time

from motion_runtime.motion_automation_store import default_automation_state
from motion_runtime.motion_player import MotionPlayer
from motion_runtime import motion_run_rules
from motion_runtime.motion_run_manager import MotionRunManager


def _patch_rule(name, value):
    """규칙 함수를 시험용으로 갈아끼운다 · §6-25로 노드에서 떨어져 나왔다.

    이전에는 `manager.<이름> = ...`로 인스턴스에 꽂았다. 규칙이 모듈 함수가
    되면서 이음매도 모듈로 옮겼다 · autouse 픽스처가 테스트마다 되돌린다.
    """
    mock.patch.object(motion_run_rules, name, value).start()


@pytest.fixture(autouse=True)
def _restore_patched_rules():
    yield
    mock.patch.stopall()


class _Logger:
    def error(self, _message):
        pass


def _manager():
    manager = MotionRunManager.__new__(MotionRunManager)
    manager._player = MotionPlayer(manager)
    manager.period_sec = 0.001
    manager._run_lock = threading.RLock()
    manager._stop_event = threading.Event()
    manager._graceful_stop_event = threading.Event()
    manager._status = motion_run_rules._empty_status()
    manager._execution_context = {}
    manager._execution_context_ready = True
    manager._automation_state = dict(default_automation_state())
    manager._automation_runtime = {
        'state': 'starting',
        'message': '',
        'stop_after_cycle': False,
    }
    manager._automation_project_id = 'project'
    manager._publish_status = lambda: None
    manager._player._require_playback_command_allowed = lambda axes=None: None
    manager._current_motors = lambda: []
    manager._player._prepare_motion_stream = lambda _motors, _axes: None
    manager._player._publish_motion_setpoints = lambda *_args, **_kwargs: None
    _patch_rule('_sleep_until', lambda _deadline: None)
    manager._player._current_servo_alarm_grade = lambda: 0
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

    manager._player._publish_motion_setpoints = publish
    plan = _plan()
    plan['samples'][0]['positions'] = {0: 0.0}

    manager._player._run_motion(plan)

    assert publishes == ['sample']
    assert manager.status()['state'] == 'stopped'
    assert manager.status()['cycle_count'] == 1
    assert '현재 모션 회차 완료 후' in manager.status()['message']


def test_dwell_repeat_uses_one_transition_handler_between_cycles():
    manager = _manager()
    transitions = []
    manager._player._wait_between_cycles = lambda _plan, _started, cycle, seconds: (
        transitions.append((cycle, seconds)) or False
    )

    manager._player._run_motion(_plan('dwell'))

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
    manager._player._finish_cycle_stop = lambda *_args, **_kwargs: waiting_status.update(
        manager.status()
    )
    manager._graceful_stop_event.set()

    completed = manager._player._wait_between_cycles(
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

    manager._player._restore_running_status(_plan('dwell'), 100.0, 1)

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

    manager._player._run_initialization = initialize
    manager._player._run_motion(_plan('reinitialize'), {'name': 'all-enabled-axes'})

    assert calls == ['all-enabled-axes']
    assert manager.status()['state'] == 'stopped'
    assert '초기위치 이동 완료 후' in manager.status()['message']


def test_grade_one_alarm_allows_current_cycle_then_blocks_next_cycle():
    manager = _manager()
    failures = []
    manager._player._current_servo_alarm_grade = lambda: 1
    manager._automation_failure = failures.append

    manager._player._run_motion(_plan())

    assert failures == ['1등급 서보 에러 · 나머지 축의 현재 회차 완료 후 자동 반복 중단']
    assert manager.status()['state'] == 'error'
    assert manager.status()['cycle_count'] == 1


def test_reinitialize_repeat_does_not_require_direct_loop_seam():
    reason = motion_run_rules._motion_auto_start_guard_error({
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
    manager._player._run_initialization = lambda _plan: (
        manager._update_status({'state': 'initialized'})
    )
    manager._graceful_stop_event.set()
    calls = []
    manager._player._run_motion = lambda *_args: calls.append('motion')

    manager._player._run_initialization_then_motion(
        {'automation_run': True},
        _plan(),
    )

    assert calls == []
    assert manager.status()['state'] == 'stopped'
    assert '초기위치 이동 완료 후' in manager.status()['message']


def test_confirmed_context_no_longer_revives_playback():
    """부팅 때 스스로 시작하는 기능은 없앴다 · §6-134

    켜는 곳이 둘이었고(이 PC · 그룹) 서로 배타적이었다 · 연동을 켜면 로컬이
    스스로 꺼지고, 그룹은 필수 PC 가 2대 미만이면 안 떴다 · 그래서 혼자 쓰는
    PC 가 연동을 켜 두면 아무것도 안 됐다 · 시작은 사람이나 스케줄이 시킨다.
    """
    manager = _manager()
    manager._execution_context = {
        'context_id': 'context-1',
        'project_id': 'project',
    }

    result = manager._confirm_execution_context({'context_id': 'context-1'})

    assert result['success'] is True
    assert manager._execution_context_ready is True
    assert not hasattr(manager, '_automation_resume_pending')
    assert 'resume_pending' not in manager._automation_runtime
    assert not hasattr(manager, '_coordination_enabled')


# --------------------------------------------------------------------------- #
# 실행 허용을 거둘 때 **사람이 저장한 설정**까지 지우지 않는다 · §6-267
#
# 브릿지는 실행 컨텍스트가 준비되지 않으면 1초마다 `invalidate_context` 를
# 보낸다 · 실측으로 12초에 11번 왔다 · 전에는 그때마다 자동 반복 설정과 그것이
# 어느 프로젝트 것인지까지 지웠다 · `_automation_project_id` 가 비면 저장이
# 「자동 반복을 저장할 현재 프로젝트가 없습니다」로 거절된다.
# --------------------------------------------------------------------------- #


def _invalidated(manager):
    manager._run_thread = None      # 동작 중이 아니다
    return manager._invalidate_execution_context({})


def test_invalidate_keeps_the_saved_automation():
    manager = _manager()
    manager._automation_state = {**manager._automation_state, 'enabled': True}
    before = dict(manager._automation_state)

    _invalidated(manager)

    assert manager._automation_state == before
    assert manager._automation_project_id == 'project'


def test_invalidate_still_takes_the_permission():
    manager = _manager()
    manager._execution_context = {'context_id': 'abc'}
    manager._execution_context_ready = True

    _invalidated(manager)

    assert manager._execution_context == {}
    assert manager._execution_context_ready is False


def test_invalidate_answers_with_the_project_it_kept():
    response = _invalidated(_manager())

    assert response['project_id'] == 'project'
    assert '유지' in response['message']
