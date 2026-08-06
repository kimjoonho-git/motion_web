from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = PACKAGE_ROOT.parents[1]


def test_coordination_service_opens_ros_dds_only():
    unit = (PACKAGE_ROOT / 'deploy/motion-coordination.service.in').read_text(
        encoding='utf-8'
    )

    assert 'After=network-online.target' in unit
    assert 'ROS_LOCALHOST_ONLY=0' in unit
    assert '8010' not in unit
    assert 'motion-control.service' not in unit
    assert 'motion-motor.service' not in unit


def test_user_service_installer_registers_coordination_service():
    installer = (
        WORKSPACE / 'src/motion_web/web_bridge/deploy/install_user_service.sh'
    ).read_text(encoding='utf-8')

    assert 'COORDINATION_SERVICE_EXECUTABLE=' in installer
    assert 'src/motion_coordination/deploy/motion-coordination.service.in' in installer
    assert 'src/motion_coordination/deploy/run_coordination_user_service.sh' in installer
    expected = (
        'enable motion-motor.service motion-control.service '
        'motion-coordination.service'
    )
    assert expected in installer
    assert 'start motion-coordination.service' in installer


def test_example_configuration_contains_only_dds_v2_fields():
    example = (WORKSPACE / 'config/motion_coordination.example.yaml').read_text(
        encoding='utf-8'
    )

    assert 'version: 2' in example
    assert 'dds_domain_id:' in example
    assert 'group_id:' in example
    assert 'enabled: false' in example
    assert '8010' not in example
    assert 'HMAC' not in example
    assert 'credential' not in example
    assert 'pairing' not in example
