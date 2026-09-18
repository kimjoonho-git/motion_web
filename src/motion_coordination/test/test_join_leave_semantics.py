"""연동 사용 · 참가 · 탈퇴가 각각 무엇을 바꾸는가 · §6-164

**들어오거나 나가거나 둘 뿐이다.**

전에는 「그룹 나가기」와 「지금 빠지기」가 따로 있었다 · 멈춰 있을 때는 둘이
완전히 같은 일이었고 도는 중일 때만 갈렸다 (나가기는 거부, 빠지기는 강제로
세우고 나감) · 사용자는 매번 어느 쪽인지 골라야 했다 · 하나로 합쳤다.

    연동 사용   `enabled` · 설정 파일에 영구히 남는다
    참가/탈퇴   `_joined` · 노드 메모리만 · 재시작하면 `configured` 로 되돌아간다

**도는 중에는 못 나간다** · 먼저 세운다 · 연동 설정을 바꿀 때도 같다 (§6-163).
"""

from types import SimpleNamespace

import pytest

from motion_common.group_config import GroupConfig

from test_coordination_node import _node, _Publisher


def _idle_node(*, joined=True, is_master=True):
    node = _node()
    node._joined = joined
    node._config.is_master = is_master
    node._heartbeat_pub = _Publisher()
    return node


def test_joined_follows_the_enabled_setting_at_boot():
    """참가 초기값은 「연동 사용」이다 · 사용자가 참가를 누를 일이 없다."""
    def config(**overrides):
        return GroupConfig(
            pc_id='pc-a', display_name='PC A', dds_domain_id=21, **overrides,
        )

    used = config(enabled=True, group_id='stage-a')
    unused = config(enabled=False, group_id='stage-a')
    no_group = config(enabled=True, group_id='')

    assert used.configured is True
    assert unused.configured is False
    assert no_group.configured is False, '그룹 ID 가 없으면 연동을 켜도 참가하지 않는다'


def test_join_needs_the_enabled_setting_first():
    node = _idle_node(joined=False)
    node._config.configured = False

    result = node._handle_local_request({'command': 'join'})

    assert result['success'] is False
    assert 'DDS Domain ID' in result['message']
    assert node._joined is False


def test_join_only_flips_the_runtime_flag():
    node = _idle_node(joined=False)

    result = node._handle_local_request({'command': 'join'})

    assert result['success'] is True
    assert node._joined is True
    # 설정 파일은 건드리지 않는다 · 재시작하면 `configured` 로 되돌아간다
    assert node._config.enabled is True


def test_leave_is_refused_while_the_group_is_running():
    node = _idle_node()
    node._execution.execution_id = 'exec-a'

    result = node._handle_local_request({'command': 'leave'})

    assert result['success'] is False
    assert '먼저 정지' in result['message']
    assert node._joined is True, '거부됐으면 참가 상태가 그대로여야 한다'


def test_leave_when_idle_tells_the_peers():
    node = _idle_node()

    result = node._handle_local_request({'command': 'leave'})

    assert result['success'] is True
    assert node._joined is False
    assert len(node._heartbeat_pub.messages) == 1, '빠졌다고 한 번 알린다'
    assert node._heartbeat_pub.messages[0].joined is False


def test_leaving_never_stops_the_group_for_the_user():
    """탈퇴가 남의 모션을 세우면 안 된다 · 세우는 것은 사람이 정한다.

    전에 「지금 빠지기」는 도는 중에 **세 대를 다 세우고** 나갔다 · 한 대만
    빼려던 사람이 공연을 멈췄다 · 이제는 거부하고 먼저 세우라고 말한다.
    """
    node = _idle_node()
    node._execution.execution_id = 'exec-a'
    node._request_group_stop = lambda after_cycle: pytest.fail('탈퇴가 세우면 안 된다')

    result = node._handle_local_request({'command': 'leave'})

    assert result['success'] is False
    assert node._joined is True


def test_leaving_clears_what_is_left_behind():
    """나갈 때는 조건 없이 비운다 · 찌꺼기가 남으면 단독 작업 길이 막힌다."""
    node = _idle_node()
    node._execution.state = 'preparing'
    node._coordination_error = {'code': 'GROUP_ERROR', 'message': '뭔가'}

    result = node._handle_local_request({'command': 'leave'})

    assert result['success'] is True
    assert node._joined is False
    assert node._coordination_error == {}
    assert not node._execution.execution_id


def test_group_start_needs_the_pc_to_be_joined():
    node = _idle_node(joined=False)

    result = node._handle_local_request({'command': 'start_group'})

    assert result['success'] is False
    assert result['message'] == '먼저 DDS 그룹에 참가하세요'


def test_group_start_is_master_only():
    node = _idle_node(is_master=False)

    result = node._handle_local_request({'command': 'start_group'})

    assert result['success'] is False
    assert '슬레이브' in result['message']
