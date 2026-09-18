"""모터 시스템과 만나는 통로 이름은 고정이다 · §6-176

**우리가 지은 이름이 아니다.**

`motion_system` 은 별도 저장소다 (`src/motion_system`) · 그쪽
`motor_manager_node` 가 이 이름을 **코드에 그대로 박아** 두고 있다.

    motion_system/.../robot_manager_node.py
        create_subscription(MotorStatus, 'motion_control/motor_command', ...)
        create_publisher(MotorStatus,    'motion_control/motor_status', ...)

여기서 이름을 바꾸면 **모터가 통째로 안 돈다** · 오류도 안 난다 · 그냥
아무도 듣지 않는 곳에 말하게 된다.

이 시험이 있는 이유는 실제 위험이 있었기 때문이다 · 2026-09-18 에 통로
이름을 정리하려다, 도는 시스템에 직접 묻지 않았으면
`/motion_control/request` 를 「주인 없는 이름」으로 보고 바꿀 뻔했다.

`motion_control/` 이라는 묶음 이름 자체가 그 경계에서 왔다 · 그 아래 우리
내부 통로 18개가 같이 얹혀 있어서 이름만으로는 남의 집이 어디까지인지
알 수 없다 · 그래서 여기서 지킨다.
"""

import re
from pathlib import Path

import pytest

from motion_common import topics

WORKSPACE = Path(__file__).resolve().parents[3]
MOTION_SYSTEM = WORKSPACE / 'src/motion_system'

#: 여기 적힌 이름은 **양쪽 저장소를 같이 고치기 전에는** 못 바꾼다
FROZEN = {
    'MOTOR_STATUS': '/motion_control/motor_status',
    'MOTOR_COMMAND': '/motion_control/motor_command',
}


@pytest.mark.parametrize('name, path', sorted(FROZEN.items()))
def test_the_boundary_name_has_not_moved(name, path):
    """이름을 바꾸면 모터가 조용히 안 돈다 · 오류도 안 난다."""
    value = getattr(topics, name)
    assert value.endswith(path), (
        f'{name} 이 {value!r} 로 바뀌었습니다 · 이 이름은 모터 시스템'
        f'(별도 저장소)이 코드에 박아 두고 있습니다 · {path}'
    )


def test_the_boundary_list_is_declared_in_one_place():
    """목록이 두 곳에 있으면 한쪽만 고쳐진다."""
    assert topics.MOTOR_SYSTEM_BOUNDARY == FROZEN


@pytest.mark.parametrize('path', sorted(FROZEN.values()))
def test_the_other_side_really_uses_this_name(path):
    """저쪽이 정말 이 이름을 쓰는지 · 안 쓰면 이 시험이 헛것을 지킨다.

    저쪽 저장소가 없을 수도 있다 (하위 모듈을 안 받은 PC) · 그때는 건너뛴다.
    """
    if not MOTION_SYSTEM.is_dir():
        pytest.skip('motion_system 하위 모듈이 없습니다')

    name = path.lstrip('/')
    found = [
        source.name for source in MOTION_SYSTEM.rglob('*.py')
        if f"'{name}'" in source.read_text(encoding='utf-8', errors='replace')
    ]
    assert found, (
        f'모터 시스템이 더 이상 {name} 을 쓰지 않습니다 · '
        '경계가 바뀌었는지 확인하고 이 목록을 고치세요'
    )


def test_we_do_not_reach_into_their_own_channel():
    """`motion_control/request` 는 그쪽 것이다 · 우리는 발행도 구독도 안 한다.

    쓰기 시작하려면 그쪽 저장소를 먼저 읽어야 한다 · 모르고 얹으면
    그쪽이 이름을 바꿀 때 우리가 조용히 끊긴다.
    """
    ours = [
        source for source in (WORKSPACE / 'src').rglob('*.py')
        if 'motion_system' not in str(source)
        and '__pycache__' not in str(source)
        and re.search(r"'/?motion_control/request'", source.read_text(encoding='utf-8', errors='replace'))
    ]
    assert ours == [], (
        '모터 시스템의 내부 통로를 쓰고 있습니다: '
        f'{[str(p.relative_to(WORKSPACE)) for p in ours]}'
    )


def test_our_own_channels_are_not_mistaken_for_the_boundary():
    """같은 문패 아래 있지만 우리 것인 통로들 · 이건 바꿔도 된다."""
    for name in ('MOTION_STATE', 'MOTOR_SCAN_PROGRESS'):
        assert getattr(topics, name) not in FROZEN.values()
