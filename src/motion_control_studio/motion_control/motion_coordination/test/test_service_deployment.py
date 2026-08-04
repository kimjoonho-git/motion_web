from pathlib import Path

from motion_coordination.configuration import CoordinationConfig
from motion_coordination.coordination_node import create_app
from motion_coordination.runtime import (
    CONTROL_PATH,
    CoordinationRuntime,
    READINESS_PATH,
    STATUS_PATH,
)


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = PACKAGE_ROOT.parents[3]


def test_off_mode_application_exposes_only_status_transport_route(tmp_path):
    runtime = CoordinationRuntime(CoordinationConfig.disabled(tmp_path), {})
    app = create_app(runtime)
    application_routes = {
        route.path for route in app.routes
        if getattr(route, 'methods', None) and route.path.startswith('/coordination/')
    }

    assert application_routes == {STATUS_PATH, READINESS_PATH, CONTROL_PATH}


def test_coordination_service_is_independent_from_local_control_services():
    unit = (PACKAGE_ROOT / 'deploy/motion-coordination.service.in').read_text(
        encoding='utf-8'
    )

    assert 'After=network-online.target' in unit
    assert 'motion-control.service' not in unit
    assert 'motion-motor.service' not in unit
    assert 'ROS_LOCALHOST_ONLY=1' in unit


def test_user_service_installer_registers_coordination_service():
    installer = (
        WORKSPACE / 'src/motion_web/web_bridge/deploy/install_user_service.sh'
    ).read_text(encoding='utf-8')

    assert 'COORDINATION_SERVICE_EXECUTABLE=' in installer
    enabled_services = (
        'enable motion-motor.service motion-control.service '
        'motion-coordination.service'
    )
    assert enabled_services in installer
    assert 'start motion-coordination.service' in installer
