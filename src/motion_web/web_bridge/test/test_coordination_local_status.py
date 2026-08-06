import threading
import time

from motion_web_bridge.bridge_node import MotionWebBridge


def test_coordination_local_status_contains_only_runtime_and_safety_fields():
    bridge = MotionWebBridge.__new__(MotionWebBridge)
    bridge._motion_run_lock = threading.Lock()
    bridge._safety_status_lock = threading.Lock()
    bridge._coordination_poll_lock = threading.Lock()
    bridge._coordination_poll_received_monotonic = 0.0
    bridge._coordination_watchdog_stop_execution_id = ''
    bridge._motion_run_status = {
        'group_execution': True, 'execution_id': 'exec-a', 'phase': 'running',
    }
    bridge._safety_status = {'servo_alarm_grade': 0}

    result = bridge.coordination_local_status()

    assert result['bridge_state'] == 'ok'
    assert result['motion_run_status']['execution_id'] == 'exec-a'
    assert result['safety_status']['servo_alarm_grade'] == 0
    assert 'motion_state' not in result
    assert 'execution_context' not in result


def test_coordination_watchdog_stops_group_run_after_local_poll_disappears():
    bridge = MotionWebBridge.__new__(MotionWebBridge)
    bridge._motion_run_lock = threading.Lock()
    bridge._coordination_poll_lock = threading.Lock()
    bridge._coordination_poll_received_monotonic = time.monotonic() - 2.0
    bridge._coordination_watchdog_stop_execution_id = ''
    bridge._motion_run_status = {
        'group_execution': True,
        'execution_id': 'exec-a',
        'phase': 'running',
    }
    stopped = threading.Event()
    bridge.coordination_stop_now = lambda: stopped.set() or {'success': True}

    bridge._coordination_watchdog_callback()

    assert stopped.wait(0.5)
    assert bridge._coordination_watchdog_stop_execution_id == 'exec-a'


def test_coordination_watchdog_does_not_affect_standalone_run():
    bridge = MotionWebBridge.__new__(MotionWebBridge)
    bridge._motion_run_lock = threading.Lock()
    bridge._coordination_poll_lock = threading.Lock()
    bridge._coordination_poll_received_monotonic = 0.0
    bridge._coordination_watchdog_stop_execution_id = ''
    bridge._motion_run_status = {
        'group_execution': False,
        'execution_id': '',
        'phase': 'running',
    }
    bridge.coordination_stop_now = lambda: (_ for _ in ()).throw(
        AssertionError('standalone run must not be stopped')
    )

    bridge._coordination_watchdog_callback()


def test_coordination_stop_publishes_safety_before_motion_run_stop():
    bridge = MotionWebBridge.__new__(MotionWebBridge)
    events = []
    bridge.cancel_pending_motion_studio_start = lambda: events.append('cancel')
    bridge.publish_safety_stop = (
        lambda emergency: events.append(('safety', emergency)) or 'safety-a'
    )
    bridge.motion_run_stop = (
        lambda: events.append('motion_run_stop') or {'success': True}
    )

    result = bridge.coordination_stop_now()

    assert events == ['cancel', ('safety', False), 'motion_run_stop']
    assert result['success'] is True
    assert result['safety_stop']['request_id'] == 'safety-a'
    assert result['safety_stop']['acknowledgement_pending'] is True


def test_coordination_stop_still_stops_motion_when_safety_publish_fails():
    bridge = MotionWebBridge.__new__(MotionWebBridge)
    events = []
    bridge.cancel_pending_motion_studio_start = lambda: None
    bridge.publish_safety_stop = lambda _emergency: (_ for _ in ()).throw(
        RuntimeError('publisher unavailable')
    )
    bridge.motion_run_stop = (
        lambda: events.append('motion_run_stop') or {'success': True}
    )

    result = bridge.coordination_stop_now()

    assert events == ['motion_run_stop']
    assert result['success'] is False
    assert result['safety_stop']['success'] is False


def test_coordination_stop_still_stops_when_start_cancel_raises():
    bridge = MotionWebBridge.__new__(MotionWebBridge)
    events = []
    bridge.cancel_pending_motion_studio_start = lambda: (_ for _ in ()).throw(
        RuntimeError('cancel unavailable')
    )
    bridge.publish_safety_stop = (
        lambda emergency: events.append(('safety', emergency)) or 'safety-a'
    )
    bridge.motion_run_stop = (
        lambda: events.append('motion_run_stop') or {'success': True}
    )

    result = bridge.coordination_stop_now()

    assert events == [('safety', False), 'motion_run_stop']
    assert result['success'] is False
    assert '시작 예약 취소 실패' in result['message']
