"""그룹 설정에서 실행 환경을 뽑는다 · §6-96

"이 PC 는 누구인가"와 "어느 DDS 망에 있는가"는 그룹 설정이 주인이다 ·
호스트 이름을 따로 읽으면 주인이 둘이 되고, 설정에서 `pc_id` 를 바꿨는데
토픽 이름은 호스트 이름을 따라가는 일이 생긴다.

ROS 를 켜기 전에 도는 코드라 표준 라이브러리만 쓴다.
"""

import socket

from motion_common.group_env import FALLBACK_DOMAIN_ID, resolve

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
