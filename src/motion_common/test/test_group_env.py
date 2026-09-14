"""그룹 설정에서 실행 환경을 뽑는다 · §6-96

"이 PC 는 누구인가"와 "어느 DDS 망에 있는가"는 그룹 설정이 주인이다 ·
호스트 이름을 따로 읽으면 주인이 둘이 되고, 설정에서 `pc_id` 를 바꿨는데
토픽 이름은 호스트 이름을 따라가는 일이 생긴다.

ROS 를 켜기 전에 도는 코드라 표준 라이브러리만 쓴다.
"""

import re
import socket

from motion_common import topics
from motion_common.group_env import FALLBACK_DOMAIN_ID, exports, resolve

SAMPLE = """version: 2
pc_id: joonhoTest
display_name: joonhoTest
group_id: test1
dds_domain_id: 21
heartbeat_sec: 1.0
"""


def _config(tmp_path, text):
    path = tmp_path / 'motion_coordination.yaml'
    path.write_text(text, encoding='utf-8')
    return path


def test_it_takes_both_values_from_the_group_config(tmp_path):
    resolved = resolve(_config(tmp_path, SAMPLE))
    assert resolved['namespace'] == 'joonhoTest'
    assert resolved['domain_id'] == 21


def test_the_pc_id_wins_over_the_hostname(tmp_path):
    """설정에서 이름을 바꾸면 토픽 이름도 따라가야 한다 · 주인은 하나다."""
    resolved = resolve(_config(tmp_path, SAMPLE.replace('joonhoTest', 'stage_left')))
    assert resolved['namespace'] == 'stage_left'
    assert resolved['namespace'] != socket.gethostname()


# --------------------------------------------------------------------- #
# 설정이 없거나 깨져도 시스템은 떠야 한다
# --------------------------------------------------------------------- #

def test_a_missing_config_falls_back_to_the_hostname(tmp_path):
    resolved = resolve(tmp_path / 'nowhere.yaml')
    assert resolved['namespace'] == socket.gethostname()
    assert resolved['domain_id'] == FALLBACK_DOMAIN_ID


def test_an_empty_config_still_works(tmp_path):
    resolved = resolve(_config(tmp_path, ''))
    assert resolved['namespace'] == socket.gethostname()
    assert resolved['domain_id'] == FALLBACK_DOMAIN_ID


def test_a_missing_domain_uses_the_group_default(tmp_path):
    text = '\n'.join(
        line for line in SAMPLE.splitlines() if not line.startswith('dds_domain_id')
    )
    assert resolve(_config(tmp_path, text))['domain_id'] == FALLBACK_DOMAIN_ID


def test_a_broken_domain_does_not_crash_the_boot(tmp_path):
    for broken in ('abc', '', '-1', '999', '21.5'):
        text = SAMPLE.replace('dds_domain_id: 21', f'dds_domain_id: {broken}')
        resolved = resolve(_config(tmp_path, text))
        assert resolved['domain_id'] == FALLBACK_DOMAIN_ID, broken


def test_a_domain_at_the_edges_is_kept(tmp_path):
    for good in (0, 21, 101):
        text = SAMPLE.replace('dds_domain_id: 21', f'dds_domain_id: {good}')
        assert resolve(_config(tmp_path, text))['domain_id'] == good


def test_quoted_values_are_read(tmp_path):
    text = SAMPLE.replace('pc_id: joonhoTest', 'pc_id: "stage_left"')
    assert resolve(_config(tmp_path, text))['namespace'] == 'stage_left'


def test_a_similar_key_is_not_mistaken_for_the_real_one(tmp_path):
    """`display_name` 이나 주석이 `pc_id` 로 읽히면 안 된다."""
    text = '# pc_id: wrong\ndisplay_name: wrong\npc_id: right\ndds_domain_id: 21\n'
    assert resolve(_config(tmp_path, text))['namespace'] == 'right'


# --------------------------------------------------------------------- #
# 이름은 토픽에 쓸 수 있는 모양이어야 한다
# --------------------------------------------------------------------- #

#: 실제로 이 그룹에 있는 이름들이다 · 셋 다 하이픈이나 점이 들어간다
AWKWARD = ('pc-a', 'floating3-Ecolite-Series', 'stage.left', '3rd-pc')


def _resolve_pc_id(tmp_path, pc_id):
    text = SAMPLE.replace('pc_id: joonhoTest', f'pc_id: {pc_id}')
    return resolve(_config(tmp_path, text))


def test_the_name_can_be_used_as_a_ros_namespace(tmp_path):
    """`pc_id` 에는 하이픈이 들어간다(`pc-a`) · 그대로 넘기면 모터 노드가
    `__ns:=/pc-a` 를 거부해 **아예 뜨지 않는다** · 화면에 모터가 0대로 나온다.
    """
    for pc_id in AWKWARD:
        namespace = _resolve_pc_id(tmp_path, pc_id)['namespace']
        assert re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', namespace), (
            f'{pc_id} → {namespace} · ROS 이름으로 쓸 수 없다'
        )


def test_the_script_and_the_nodes_end_up_with_the_same_name(
    tmp_path, monkeypatch
):
    """실행 스크립트가 넘기는 값과 노드가 여는 토픽 이름이 갈리면 아무 말 없이
    통신이 끊긴다 · 규칙의 주인은 `topics.py` 하나이고, 여기서 그것을 지난다 ·
    갈리는 순간 이 검사가 잡는다.
    """
    for pc_id in AWKWARD + ('joonhoTest',):
        namespace = _resolve_pc_id(tmp_path, pc_id)['namespace']
        monkeypatch.setenv('MOTION_PC_NAMESPACE', namespace)
        assert topics.pc_namespace() == namespace, f'{pc_id} 에서 둘이 갈렸다'


# --------------------------------------------------------------------- #
# 바깥에서 덮어쓰기 · 되돌릴 수 있어야 한다
# --------------------------------------------------------------------- #

RESOLVED = {'namespace': 'joonhoTest', 'domain_id': 21}


def test_nothing_given_uses_the_config():
    assert exports(RESOLVED, {}) == [
        'export MOTION_PC_NAMESPACE="joonhoTest"',
        'export ROS_DOMAIN_ID="21"',
    ]


def test_what_is_given_from_outside_wins():
    lines = exports(RESOLVED, {'MOTION_PC_NAMESPACE': 'stage_left',
                               'ROS_DOMAIN_ID': '42'})
    assert lines == [
        'export MOTION_PC_NAMESPACE="stage_left"',
        'export ROS_DOMAIN_ID="42"',
    ]


def test_an_empty_name_is_a_choice_not_a_missing_value():
    """`MOTION_PC_NAMESPACE=` 는 이름표 끄기다 · 예전 이름으로 돌아간다 ·
    설정 값으로 덮어 버리면 되돌릴 길이 없어진다."""
    lines = exports(RESOLVED, {'MOTION_PC_NAMESPACE': ''})
    assert 'export MOTION_PC_NAMESPACE=""' in lines


def test_even_a_hand_written_name_goes_through_the_same_rule():
    """손으로 `pc-a` 를 넣어도 모터 노드가 받는 이름과 노드가 여는 토픽이
    갈리지 않아야 한다."""
    lines = exports(RESOLVED, {'MOTION_PC_NAMESPACE': 'pc-a'})
    assert 'export MOTION_PC_NAMESPACE="pc_a"' in lines
