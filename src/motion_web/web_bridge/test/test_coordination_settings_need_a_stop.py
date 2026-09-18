"""연동 설정은 모션이 선 뒤에 바꾼다 · §6-163

**명단에서 PC 하나 빼려던 일이 공연을 멈출 수 있었다.**

연동 설정 저장은 끝에서 **연동 서비스를 재시작한다** · 그룹이 도는 중에
재시작하면 이 PC 가 그룹에서 사라지고, 남은 PC 들은 참가 PC 가 죽은 것으로
보아 `GROUP_PARTICIPANT_FAILURE` 로 세 대가 통째로 선다.

연동 탈퇴도 같은 규칙이다 · 도는 중에는 못 나간다 (§6-164) ·
설정 저장만 아무것도 안 보고 있었다 · 그룹 이름·도메인·마스터·명단이 전부
이 길을 지난다 · 명단 버튼 한 번이 재시작 한 번이다.
"""

import pytest

from motion_web_bridge.coordination_bridge import CoordinationWebBridge


def _bridge(tmp_path, execution):
    bridge = CoordinationWebBridge.__new__(CoordinationWebBridge)
    bridge._config_path = tmp_path / 'motion_coordination.yaml'
    bridge._status_received_at = 9_999_999_999.0  # 방금 받은 것으로 친다
    bridge._status = {}
    bridge.snapshot = lambda: {'runtime': {'execution': execution}}
    bridge._restarted = False

    def _restart():
        bridge._restarted = True
        return {'service_installed': False, 'restart_pending': False, 'message': ''}

    bridge._restart_coordination_service = staticmethod(_restart)
    return bridge


RUNNING = {'state': 'running', 'execution_id': 'exec-a', 'participants': ['a', 'b']}
STOPPED = {'state': 'stopped', 'execution_id': '', 'participants': []}


def test_a_running_group_refuses_the_change(tmp_path):
    """도는 중에 재시작하면 세 대가 통째로 선다."""
    bridge = _bridge(tmp_path, RUNNING)

    with pytest.raises(ValueError, match='먼저 정지'):
        bridge.update_settings({'required_peers': ['a']})


def test_nothing_is_written_or_restarted_when_refused(tmp_path):
    """거부하면 설정 파일도 안 바뀌고 서비스도 안 흔들려야 한다."""
    bridge = _bridge(tmp_path, RUNNING)

    with pytest.raises(ValueError):
        bridge.update_settings({'group_id': '다른방'})

    assert not bridge._config_path.exists()
    assert bridge._restarted is False


@pytest.mark.parametrize('field, value', [
    ('required_peers', ['a']),
    ('group_id', '다른방'),
    ('dds_domain_id', 30),
    ('is_master', True),
    ('enabled', False),
])
def test_every_setting_goes_through_the_same_door(tmp_path, field, value):
    """명단만이 아니다 · 그룹 이름·도메인·마스터 전부 재시작을 부른다."""
    bridge = _bridge(tmp_path, RUNNING)

    with pytest.raises(ValueError, match='먼저 정지'):
        bridge.update_settings({field: value})


def test_a_stopped_group_lets_the_change_through(tmp_path):
    """막을 것만 막아야 한다 · 정지 상태에서 못 바꾸면 설정 자체가 불가능하다."""
    bridge = _bridge(tmp_path, STOPPED)

    result = bridge.update_settings({'group_id': 'test1', 'dds_domain_id': 21})

    assert result['saved'] is True
    assert bridge._config_path.is_file()


def test_a_silent_coordination_node_does_not_block_forever(tmp_path):
    """연동 노드가 죽어 상태를 못 받을 때까지 막으면 고칠 길이 사라진다.

    `local_execution_blocker` 는 3초 넘게 못 받은 상태를 「모른다」 로 친다 ·
    모르면 막지 않는다 · 안 그러면 연동이 깨졌을 때 설정을 못 고친다.
    """
    bridge = _bridge(tmp_path, RUNNING)
    bridge._status_received_at = 0.0  # 한 번도 못 받았다

    result = bridge.update_settings({'group_id': 'test1'})

    assert result['saved'] is True
