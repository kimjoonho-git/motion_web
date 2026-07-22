import json
import threading
from types import SimpleNamespace

from motion_web_bridge.bridge_node import MotionWebBridge


def test_scan_progress_groups_events_and_marks_completion():
    bridge = MotionWebBridge.__new__(MotionWebBridge)
    bridge.scan_progress_topic = '/motion_control/motor_scan_progress'
    bridge._scan_progress_lock = threading.RLock()
    bridge._scan_progress = {'scan_id': '', 'events': [], 'running': False}
    bridge.project_repository = SimpleNamespace(selected_project_id=lambda: 'project-1')
    bridge._current_project_generation = lambda: 7
    bridge.get_logger = lambda: SimpleNamespace(warn=lambda _message: None)

    for phase in ('started', 'ethercat_rescan', 'partial'):
        bridge._scan_progress_callback(SimpleNamespace(data=json.dumps({
            'scan_id': 'scan-1',
            'phase': phase,
            'transport': 'ethercat',
            'message': phase,
            'timestamp': 1.0,
        })))

    payload = bridge.motor_scan_progress()
    assert payload['project_generation'] == 7
    assert payload['progress']['scan_id'] == 'scan-1'
    assert [event['phase'] for event in payload['progress']['events']] == [
        'started', 'ethercat_rescan', 'partial'
    ]
    assert payload['progress']['running'] is False
