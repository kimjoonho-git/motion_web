import threading
import time

from motion_runtime.motion_run_manager import MotionRunManager


def _wait_until(predicate, timeout=1.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.005)
    raise AssertionError('condition timeout')


def _group_manager():
    manager = MotionRunManager.__new__(MotionRunManager)
    manager._run_lock = threading.RLock()
    manager._group_condition = threading.Condition(manager._run_lock)
    manager._group_session = {
        'active': True, 'execution_id': 'exec-a', 'state': 'preparing',
        'cycle_number': 0, 'next_cycle_number': 0, 'next_start_at': 0.0,
        'stop_after_cycle': False,
    }
    manager._stop_event = threading.Event()
    manager._graceful_stop_event = threading.Event()
    manager.period_sec = 0.02
    manager._status = {}
    manager.status = lambda: dict(manager._status)
    manager._set_status = lambda value: setattr(manager, '_status', dict(value))
    manager._update_status = lambda value: manager._status.update(dict(value))
    manager.get_logger = lambda: type('Logger', (), {
        'error': lambda self, _message: None,
    })()
    return manager


def test_one_start_at_runs_exactly_one_motion_then_waits_for_next_cycle():
    manager = _group_manager()
    manager._build_plan = lambda payload, **kwargs: {
        'run_mode': payload.get('run_mode', 'once'),
        'repeat_mode': 'direct', 'dwell_sec': 0.0,
        'group_execution': True,
    }
    manager._wait_group_deadline = lambda *_args, **_kwargs: None
    manager._run_initialization = lambda _plan: manager._status.update({
        'state': 'initialized', 'phase': 'initialized',
    })
    calls = []

    def run_motion(plan):
        calls.append(int(plan['group_cycle_number']))
        manager._status.update({
            'state': 'completed', 'phase': 'completed',
            'lifecycle': {
                'motion_started_at': time.time(),
                'motion_started_monotonic': time.monotonic(),
            },
        })

    manager._run_motion = run_motion
    worker = threading.Thread(target=manager._prepare_and_run_group, args=({
        'execution_id': 'exec-a',
        'initialize_monotonic': time.monotonic() + 1.0,
    }, [{}]))
    worker.start()
    _wait_until(lambda: manager._group_session.get('state') == 'armed')

    first = manager._schedule_group_cycle({
        'execution_id': 'exec-a', 'cycle_number': 1,
        'start_monotonic': time.monotonic() + 1.0,
    })
    assert first['success'] is True
    _wait_until(lambda: manager._group_session.get('state') == 'cycle_ready')
    assert calls == [1]
    time.sleep(0.03)
    assert calls == [1]

    second = manager._schedule_group_cycle({
        'execution_id': 'exec-a', 'cycle_number': 2,
        'start_monotonic': time.monotonic() + 1.0,
    })
    assert second['success'] is True
    _wait_until(lambda: calls == [1, 2])
    manager._cancel_group_session({'execution_id': 'exec-a'})
    worker.join(timeout=1.0)
    assert not worker.is_alive()


def test_duplicate_start_at_does_not_schedule_a_second_local_cycle():
    manager = _group_manager()
    manager._group_session['state'] = 'armed'
    scheduled_at = time.monotonic() + 1.0
    first = manager._schedule_group_cycle({
        'execution_id': 'exec-a', 'cycle_number': 1,
        'start_monotonic': scheduled_at,
    })
    duplicate = manager._schedule_group_cycle({
        'execution_id': 'exec-a', 'cycle_number': 1,
        'start_monotonic': scheduled_at,
    })
    assert first['success'] is True
    assert duplicate['duplicate'] is True
    assert manager._group_session['next_cycle_number'] == 1


def test_group_stop_after_cycle_does_not_interrupt_running_cycle():
    manager = _group_manager()
    manager._status = {'state': 'running', 'group_execution': True}
    result = manager._handle_stop_after_cycle()
    assert result['success'] is True
    assert manager._group_session['stop_after_cycle'] is True
    assert manager._graceful_stop_event.is_set()
    assert not manager._stop_event.is_set()


def test_group_stop_after_cycle_before_motion_prevents_next_start():
    manager = _group_manager()
    manager._status = {'state': 'armed', 'group_execution': True}
    result = manager._handle_stop_after_cycle()
    assert result['success'] is True
    assert manager._stop_event.is_set()
