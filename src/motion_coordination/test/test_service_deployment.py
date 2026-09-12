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


# --------------------------------------------------------------------- #
# PC 이름공간 · §6-95
# --------------------------------------------------------------------- #

MOTION_RUNNERS = (
    'src/motion_web/web_bridge/deploy/run_user_service.sh',
    'src/motion_web/web_bridge/deploy/run_motor_user_service.sh',
    'src/motion_web/web_bridge/deploy/run_motor_service.sh',
)


def test_every_motor_side_service_uses_the_same_namespace():
    """제어와 모터가 **같은 이름표**를 써야 서로 찾는다 · 하나만 붙으면
    토픽 이름이 갈려 아무 말 없이 통신이 끊긴다."""
    lines = []
    for relative in MOTION_RUNNERS:
        text = (WORKSPACE / relative).read_text(encoding='utf-8')
        found = [
            line.strip() for line in text.splitlines()
            if 'MOTION_PC_NAMESPACE' in line and line.strip().startswith('export')
        ]
        assert found, f'{relative} 에 이름공간 설정이 없다'
        lines.extend(found)

    assert len(set(lines)) == 1, (
        '서비스마다 이름표를 다르게 정한다:\n  ' + '\n  '.join(sorted(set(lines)))
    )


def test_the_namespace_can_be_turned_off_from_outside():
    """문제가 생기면 환경변수 하나로 되돌릴 수 있어야 한다."""
    text = (WORKSPACE / MOTION_RUNNERS[0]).read_text(encoding='utf-8')
    assert '${MOTION_PC_NAMESPACE:-' in text, '바깥에서 덮어쓸 수 없다'


def test_the_coordination_service_needs_no_namespace():
    """조정 노드는 그룹 토픽만 쓴다 · 그룹 토픽에는 이름표가 안 붙는다."""
    unit = (PACKAGE_ROOT / 'deploy/motion-coordination.service.in').read_text(
        encoding='utf-8'
    )
    assert 'MOTION_PC_NAMESPACE' not in unit


def test_the_motor_node_is_given_the_namespace_from_outside():
    """Motor Manager 는 `motion_system` 안에 있어 수정하지 않는다 · 다행히
    토픽을 **상대 이름**으로 열어(`motion_control/motor_status`) ROS 이름공간이
    그대로 먹는다 · 밖에서 넘겨 준다.

    안 넘기면 모터만 옛 이름에 남아 **브리지가 모터를 못 본다** · 실제로 그랬고,
    화면에 모터가 0대로 나왔다.
    """
    runner = (
        WORKSPACE / 'src/motion_web/web_bridge/deploy/run_motor_service.sh'
    ).read_text(encoding='utf-8')

    assert '__ns:=/${MOTION_PC_NAMESPACE}' in runner, '모터 노드에 이름공간을 안 준다'
    # 이름공간이 비었을 때는 넘기지 않는다 · `__ns:=/` 는 올바른 이름이 아니다
    assert 'if [[ -n "${MOTION_PC_NAMESPACE}" ]]' in runner


def test_the_motor_package_itself_is_not_modified():
    """`motion_system` 은 별도 저장소다 · 이름공간을 위해 그 안을 고치면 안 된다."""
    node = (
        WORKSPACE
        / 'src/motion_system/ros2/motion_system_ros2/motion_control_bridge'
        / 'src/motor_manager_node.cpp'
    )
    if not node.is_file():
        return
    body = node.read_text(encoding='utf-8')
    assert 'MOTION_PC_NAMESPACE' not in body, 'motion_system 안을 고쳤다'
    # 상대 이름이라야 바깥에서 이름공간을 씌울 수 있다
    assert '"motion_control/motor_status"' in body
