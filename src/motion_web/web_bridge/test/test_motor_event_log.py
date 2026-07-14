import threading
from pathlib import Path

from motion_web_bridge.bridge_node import MotionWebBridge


def event_log_bridge(tmp_path: Path) -> MotionWebBridge:
    bridge = MotionWebBridge.__new__(MotionWebBridge)
    bridge.event_log_dir = tmp_path
    bridge._event_log_lock = threading.RLock()
    bridge._active_motor_errors = {}
    bridge._last_motion_run_state = None
    bridge.event_log_retention_days = 30
    bridge.event_log_max_bytes = 100 * 1024 * 1024
    return bridge


def test_records_motor_error_transition_once(tmp_path):
    bridge = event_log_bridge(tmp_path)
    state = {
        'motors': [{
            'controller_index': 2,
            'display_name': 'Axis motor',
            'motor_type_label': 'AC Servo',
            'fault': True,
            'errorcode': 0x2310,
            'errorcode_hex': '0x2310',
            'error_text': 'Over current',
            'statusword': 0x0008,
        }],
    }

    bridge._record_motor_error_transitions(state)
    bridge._record_motor_error_transitions(state)

    events = bridge.motor_events()['events']
    assert len(events) == 1
    assert events[0]['category'] == 'error'
    assert events[0]['details']['axis'] == 2


def test_records_motion_lifecycle_transitions(tmp_path):
    bridge = event_log_bridge(tmp_path)
    base = {
        'motion_file_id': 'sample.json',
        'mapping_file_id': 'sample_mapping.yaml',
        'axes': [{'motor_axis': 0}, {'motor_axis': 2}],
        'run_mode': 'once',
    }

    bridge._record_motion_run_transition({**base, 'state': 'idle'})
    bridge._record_motion_run_transition({**base, 'state': 'initializing'})
    bridge._record_motion_run_transition({**base, 'state': 'initialized'})
    bridge._record_motion_run_transition({**base, 'state': 'running'})
    bridge._record_motion_run_transition({**base, 'state': 'running'})

    events = list(reversed(bridge.motor_events()['events']))
    assert [event['event_type'] for event in events] == [
        'initial_position_started',
        'initial_position_completed',
        'single_motion_started',
    ]
    assert events[-1]['content'].startswith('1회 모션 시작')
    assert events[-1]['details']['run_mode'] == 'once'


def test_records_continuous_motion_start_separately(tmp_path):
    bridge = event_log_bridge(tmp_path)
    base = {
        'motion_file_id': 'sample.json',
        'mapping_file_id': 'sample_mapping.yaml',
        'axes': [{'motor_axis': 0}],
        'run_mode': 'continuous',
    }

    bridge._record_motion_run_transition({**base, 'state': 'initialized'})
    bridge._record_motion_run_transition({**base, 'state': 'running'})

    event = bridge.motor_events()['events'][0]
    assert event['event_type'] == 'continuous_motion_started'
    assert event['content'].startswith('연속 모션 시작')
    assert event['details']['run_mode'] == 'continuous'


def test_clear_motor_events_removes_log_files(tmp_path):
    bridge = event_log_bridge(tmp_path)
    bridge._append_motor_event('motion', 'motion_started', 'sample.json', '모션 시작')

    result = bridge.clear_motor_events()

    assert result['success'] is True
    assert result['deleted_files'] == 1
    assert bridge.motor_events()['events'] == []
