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


def test_every_motor_side_service_resolves_the_namespace_the_same_way():
    """제어와 모터가 **같은 이름표**를 써야 서로 찾는다 · 하나만 달라지면
    토픽 이름이 갈려 아무 말 없이 통신이 끊긴다 · 실제로 그랬다 · §6-95

    이제 셋 다 그룹 설정에서 가져온다 · 읽는 곳이 하나라 갈릴 수가 없다.
    """
    for relative in MOTION_RUNNERS:
        text = (WORKSPACE / relative).read_text(encoding='utf-8')
        assert 'group_env.py' in text, f'{relative} 가 설정을 안 읽는다'


def test_the_namespace_and_network_can_be_set_from_outside():
    """문제가 생기면 환경변수로 되돌릴 수 있어야 한다."""
    from motion_common.group_env import exports

    given = {'MOTION_PC_NAMESPACE': 'stage_left', 'ROS_DOMAIN_ID': '42'}
    lines = exports({'namespace': 'joonhoTest', 'domain_id': 21}, given)
    assert 'export MOTION_PC_NAMESPACE="stage_left"' in lines, (
        '이름표를 바깥에서 못 덮는다'
    )
    assert 'export ROS_DOMAIN_ID="42"' in lines, '도메인을 바깥에서 못 덮는다'

    runner = (WORKSPACE / MOTION_RUNNERS[0]).read_text(encoding='utf-8')
    assert '${MOTION_GROUP_NETWORK:-' in runner, '네트워크를 바깥에서 못 덮는다'


def test_a_missing_helper_never_stops_the_service():
    """도우미가 없거나 실패해도 서비스는 떠야 한다 · 실제로 그 때문에 모터
    서비스가 `unbound variable` 로 죽었다."""
    for relative in MOTION_RUNNERS:
        text = (WORKSPACE / relative).read_text(encoding='utf-8')
        assert 'if [[ -f "${GROUP_ENV_HELPER}" ]]' in text, f'{relative} 가 확인 없이 부른다'
        assert 'export MOTION_PC_NAMESPACE="${MOTION_PC_NAMESPACE:-}"' in text


def test_the_coordination_service_gets_the_same_namespace():
    """조정 노드도 이름표를 받는다 · §6-94

    그룹 토픽만 쓰던 동안에는 필요 없었다 · 이제 원시 MIDI 중계를 맡아 **이 PC
    의** `/xtouch/midi` 를 연다 · 이름표가 없으면 옛 이름을 열어 아무 말 없이
    아무것도 안 흐른다.

    다른 서비스와 **같은 곳에서** 가져와야 한다 · 따로 읽으면 갈린다.
    """
    runner = (PACKAGE_ROOT / 'deploy/run_coordination_user_service.sh').read_text(
        encoding='utf-8'
    )
    assert 'group_env.py' in runner, '조정 서비스가 설정을 안 읽는다'
    assert 'hostname' not in runner, '호스트 이름을 따로 읽는다'
    assert 'export MOTION_PC_NAMESPACE="${MOTION_PC_NAMESPACE:-}"' in runner


def test_the_coordination_service_stays_open_to_the_network():
    """조정 노드는 늘 열려 있다 · 여기가 PC 끼리 만나는 자리다."""
    unit = (PACKAGE_ROOT / 'deploy/motion-coordination.service.in').read_text(
        encoding='utf-8'
    )
    assert 'ROS_LOCALHOST_ONLY=0' in unit


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


# --------------------------------------------------------------------- #
# 네트워크 개방 · §6-96
# --------------------------------------------------------------------- #

def test_the_network_can_be_locked_back_down():
    """모터 쪽을 네트워크에 여는 일이다 · 문제가 생기면 한 값으로 도로 잠글 수
    있어야 한다 · `MOTION_GROUP_NETWORK=0`."""
    for relative in MOTION_RUNNERS:
        text = (WORKSPACE / relative).read_text(encoding='utf-8')
        assert 'MOTION_GROUP_NETWORK' in text, f'{relative} 에 스위치가 없다'
        assert 'export ROS_LOCALHOST_ONLY=1' in text, f'{relative} 에 잠그는 가지가 없다'
        assert 'export ROS_LOCALHOST_ONLY=0' in text, f'{relative} 에 여는 가지가 없다'


def test_the_domain_and_namespace_come_from_the_group_config():
    """이름표와 도메인의 주인은 그룹 설정이다 · 호스트 이름을 따로 읽으면
    주인이 둘이 된다."""
    for relative in MOTION_RUNNERS:
        text = (WORKSPACE / relative).read_text(encoding='utf-8')
        assert 'group_env.py' in text, f'{relative} 가 설정을 안 읽는다'
        assert 'hostname' not in text, f'{relative} 가 호스트 이름을 따로 읽는다'


def test_opening_the_network_never_lands_on_the_default_domain():
    """도메인 0 은 ROS2 의 기본값이라 아무 장비나 거기 있다 · 네트워크를 연 채
    도메인 0 이면 남의 장비가 우리 모터를 본다."""
    from motion_common.group_env import FALLBACK_DOMAIN_ID

    assert FALLBACK_DOMAIN_ID != 0, '설정을 못 읽었을 때 도메인 0 으로 떨어진다'
    text = (
        WORKSPACE / 'src/motion_common/motion_common/group_env.py'
    ).read_text(encoding='utf-8')
    assert 'ROS_DOMAIN_ID' in text, '도메인을 정해 주지 않는다'
