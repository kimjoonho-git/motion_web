import json
import threading
from types import SimpleNamespace

from motion_web_bridge.bridge_node import MotionWebBridge
from motion_web_bridge.scan_orchestrator import ScanOrchestrator


def scan_orchestrator(bridge) -> ScanOrchestrator:
    """노드 없이 스캔 조율만 세운다 · §6-18로 노드에서 떨어져 나왔다."""
    return ScanOrchestrator(
        bridge,
        lifecycle_lock=threading.Lock(),
        repository=getattr(bridge, 'project_repository', None),
        scan_client=None,
        scan_ac_servo_client=None,
        scan_dynamixel_client=None,
        scan_service='/scan_motors',
        scan_ac_servo_service='/scan_ac_servo_motors',
        scan_dynamixel_service='/scan_dynamixel_motors',
        load_motor_config=lambda: {'success': False},
    )


def test_scan_progress_groups_events_and_marks_completion():
    bridge = MotionWebBridge.__new__(MotionWebBridge)
    bridge.project_repository = SimpleNamespace(selected_project_id=lambda: 'project-1')
    bridge._current_project_generation = lambda: 7
    bridge.get_logger = lambda: SimpleNamespace(warn=lambda _message: None)
    scan = scan_orchestrator(bridge)

    for phase in ('started', 'ethercat_rescan', 'partial'):
        scan.progress_callback(SimpleNamespace(data=json.dumps({
            'scan_id': 'scan-1',
            'phase': phase,
            'transport': 'ethercat',
            'message': phase,
            'timestamp': 1.0,
        })))

    payload = scan.progress()
    assert payload['project_generation'] == 7
    assert payload['progress']['scan_id'] == 'scan-1'
    assert [event['phase'] for event in payload['progress']['events']] == [
        'started', 'ethercat_rescan', 'partial'
    ]
    assert payload['progress']['running'] is False
