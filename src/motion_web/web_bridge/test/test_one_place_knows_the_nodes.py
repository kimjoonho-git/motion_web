"""모션 상태와 노드 목록은 각각 한 곳이 안다 · §6-184 · §6-185

**두 가지가 여러 벌로 적혀 있었다.**

1. 「지금 모션 상태가 뭔가」 — 일곱 자리에서 각자 자물쇠를 잡았다

       with self.bridge._lock:
           motion_state = copy.deepcopy(self.bridge._motion_state)
           received_at = self.bridge._motion_state_received_at

   `scan_orchestrator` 는 「1초 지났나」 검사까지 **글자 하나까지 똑같이 두
   번** 적어 놨다 · 한쪽만 고치면 어느 길로 왔느냐에 따라 축이 보였다 안
   보였다 한다.

   `manual_motor_commands` 는 자물쇠 안에서 **원본 참조만** 받아 밖에서
   읽었다 · 그 사이 ROS 콜백이 통째로 갈아끼우면 옛 것을 붙든다 · 자물쇠를
   잡은 뜻이 없어진다.

2. 「어느 노드들에게 말하나」 — 세 자리에 같은 목록

       무효화   네 노드   invalidate_context   0.5초
       적용     네 노드   apply_context        2초
       확인     세 노드   confirm_context      2초

   노드가 늘거나 통로 이름이 바뀌면 세 곳을 모두 찾아야 하고, 한 곳을
   놓치면 **그 노드만 옛 컨텍스트에 남는다.**
"""

import re
from pathlib import Path

import pytest

SOURCES = Path(__file__).resolve().parents[1] / 'motion_web_bridge'


def _read(name: str) -> str:
    return (SOURCES / name).read_text(encoding='utf-8')


# --------------------------------------------------------------------------- #
# 1. 모션 상태 · 자물쇠는 주인만 잡는다
# --------------------------------------------------------------------------- #

READERS = [
    'scan_orchestrator.py',
    'execution_context_service.py',
    'manual_motor_commands.py',
    'motor_runtime_service.py',
]


@pytest.mark.parametrize('name', READERS)
def test_nobody_grabs_the_bridge_lock_to_read_the_state(name):
    source = _read(name)

    assert 'self.bridge._lock' not in source, (
        f'{name} 이 브리지 자물쇠를 직접 잡습니다 · '
        'motion_state() · motion_state_with_time() · fresh_motion_state() 를 쓰세요'
    )
    assert 'self.bridge._motion_state' not in source


def test_the_freshness_rule_is_written_once():
    """`scan_orchestrator` 가 같은 검사를 두 번 적어 놨었다."""
    source = _read('scan_orchestrator.py')

    assert 'received_at' not in source, '아직 직접 시각을 따집니다'
    assert source.count('fresh_motion_state()') == 2


def test_the_age_limit_lives_with_its_owner():
    """운영 수치다 · 흩어져 있으면 사람이 바꿀 수가 없다."""
    bridge = _read('bridge_node.py')

    assert 'MOTION_STATE_MAX_AGE_SEC = 1.0' in bridge


def test_reading_the_state_hands_out_a_copy():
    """참조를 내주면 자물쇠를 잡은 뜻이 없어진다."""
    bridge = _read('bridge_node.py')
    start = bridge.index('    def motion_state(self)')
    body = bridge[start:bridge.index('\n    def ', start)]

    assert 'copy.deepcopy' in body


def test_callers_that_need_the_time_still_get_it():
    """기준이 저마다 다르다 · 판정까지 대신하면 거짓말이 된다.

        1초가 넘었나                    축을 셀 때
        정지 명령을 넣은 뒤에 온 것인가  정지를 확인할 때
    """
    runtime = _read('motor_runtime_service.py')

    assert runtime.count('motion_state_with_time()') == 2
    assert 'started_at' in runtime, '정지 확인 기준이 사라졌습니다'


# --------------------------------------------------------------------------- #
# 2. 노드 목록 · 한 곳만 안다
# --------------------------------------------------------------------------- #

def test_the_node_list_is_written_once():
    source = _read('execution_context_service.py')

    for transport in (
        '_request_motion_mapping', '_request_midi_monitor',
        '_request_motion_run', '_motion_studio_transport',
    ):
        assert f'self.bridge.{transport}' not in source, (
            f'{transport} 을 직접 부릅니다 · managed_context_nodes() 를 쓰세요'
        )

    assert source.count('managed_context_nodes()') == 3


def test_every_managed_node_is_in_the_table():
    """목록에서 빠진 노드는 프로젝트가 바뀌어도 옛 것을 붙든다."""
    bridge = _read('bridge_node.py')
    start = bridge.index('    def managed_context_nodes(self)')
    body = bridge[start:bridge.index('\n    def ', start)]
    names = set(re.findall(r"'(\w+)':", body))

    assert names == {'motion_mapping', 'midi_control', 'motion_run', 'motion_studio'}


def test_the_real_differences_keep_their_names():
    """합쳐서 없앨 수 없는 차이다 · 이름을 붙여 남긴다."""
    source = _read('execution_context_service.py')

    # MIDI 만 적용할 때 다른 말을 알아듣는다
    assert "APPLY_COMMANDS = {'midi_control': 'select_project'}" in source
    # 매핑 노드는 되묻지 않는다
    assert "CONFIRM_NODES = ('midi_control', 'motion_run', 'motion_studio')" in source


def test_confirmation_asks_fewer_nodes_than_apply():
    """적용은 넷, 확인은 셋 · 이 차이가 사라지면 매핑 노드가 두 번 답한다."""
    bridge = _read('bridge_node.py')
    source = _read('execution_context_service.py')

    start = bridge.index('    def managed_context_nodes(self)')
    body = bridge[start:bridge.index('\n    def ', start)]
    all_nodes = set(re.findall(r"'(\w+)':", body))
    confirm = set(re.findall(r"CONFIRM_NODES = \(([^)]*)\)", source)[0].replace("'", '').split(', '))

    assert confirm < all_nodes
    assert all_nodes - confirm == {'motion_mapping'}
