from pathlib import Path
import json
import threading
from types import SimpleNamespace

from motion_web_bridge.project_service import ProjectService
from motion_web_bridge.motor_runtime_service import MotorRuntimeService
from motion_web_bridge.bridge_node import MotionWebBridge
from motion_web_bridge.scan_orchestrator import ScanOrchestrator


def _project_of(bridge):
    """노드 스텁에 프로젝트 서비스를 붙인다 · §6-23으로 노드에서 떨어져 나왔다."""
    service = getattr(bridge, '_project', None)
    if service is None:
        service = ProjectService(
            bridge,
            repository=getattr(bridge, 'project_repository', None),
            motion_projects_dir=getattr(bridge, 'motion_projects_dir', Path('.')),
        )
        bridge._project = service
    repository = getattr(bridge, 'project_repository', None)
    if repository is not None:
        service.repository = repository
    projects_dir = getattr(bridge, 'motion_projects_dir', None)
    if projects_dir is not None:
        service.motion_projects_dir = projects_dir
    return service


def _runtime_of(bridge):
    """노드 스텁에 모터 런타임 서비스를 붙인다 · §6-22로 노드에서 떨어져 나왔다."""
    service = getattr(bridge, '_motor_runtime', None)
    if service is None:
        service = MotorRuntimeService(
            bridge,
            project=_project_of(bridge),
            repository=getattr(bridge, 'project_repository', None),
            workspace_root=getattr(bridge, 'workspace_root', Path('.')),
        )
        bridge._motor_runtime = service
    repository = getattr(bridge, 'project_repository', None)
    if repository is not None:
        service.repository = repository
    return service


def scan_orchestrator(bridge) -> ScanOrchestrator:
    """노드 없이 스캔 조율만 세운다 · §6-18로 노드에서 떨어져 나왔다."""
    return ScanOrchestrator(
        bridge,
        project=_project_of(bridge),
        runtime=_runtime_of(bridge),
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


def test_progress_records_each_event_once_from_both_channels():
    """토픽과 Action feedback이 같은 이벤트를 보낸다 · 한 번만 센다 (§6-26).

    Action 전환 직후 진행 이벤트가 2배로 쌓였다. 브리지가 토픽 구독과
    feedback 콜백을 모두 갖고 있는데 서버가 양쪽으로 같은 것을 보내기 때문이다.
    """
    bridge = MotionWebBridge.__new__(MotionWebBridge)
    bridge.project_repository = SimpleNamespace(selected_project_id=lambda: 'project-1')
    bridge._current_project_generation = lambda: 3
    bridge.get_logger = lambda: SimpleNamespace(warn=lambda _message: None)
    scan = scan_orchestrator(bridge)

    event = {
        'scan_id': 'scan-9',
        'phase': 'ethercat_rescan',
        'transport': 'ethercat',
        'message': '재열거',
        'timestamp': 12.5,
    }
    scan.record_progress_event(dict(event))          # 토픽
    scan.record_progress_event(dict(event))          # Action feedback · 같은 것
    scan.record_progress_event({**event, 'phase': 'ethercat_topology'})

    phases = [e['phase'] for e in scan.progress()['progress']['events']]
    assert phases == ['ethercat_rescan', 'ethercat_topology']
