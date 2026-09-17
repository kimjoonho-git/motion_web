"""연동 사용 · 그룹 참가 · 일시 해제가 각각 무엇을 바꾸는가 · §6-132.

화면에서 이 셋을 정리하기 전에 **지금 되는 것을 잠근다** · 세 가지가 서로
무엇이 다른지는 코드에만 있고 화면에는 없다. 정리하다 뜻이 바뀌면 여기서 걸린다.

    연동 사용      `enabled` · 설정 파일에 영구히 남는다
    그룹 참가/나가기  `_joined` · 노드 메모리만 · 재시작하면 `configured` 로 되돌아간다
    연동 일시 해제  `_joined` 를 끄되, 돌고 있는 그룹 실행을 **먼저 세운다**
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
    assert result['message'] == '그룹 실행 중에는 그룹에서 나갈 수 없습니다'
    assert node._joined is True, '거부됐으면 참가 상태가 그대로여야 한다'


def test_leave_when_idle_tells_the_peers():
    node = _idle_node()

    result = node._handle_local_request({'command': 'leave'})

    assert result['success'] is True
    assert node._joined is False
    assert len(node._heartbeat_pub.messages) == 1, '빠졌다고 한 번 알린다'
    assert node._heartbeat_pub.messages[0].joined is False


def test_temporary_disable_stops_the_group_first():
    """「일시 해제」가 「나가기」와 다른 점은 이것 하나뿐이다."""
    node = _idle_node()
    node._execution.execution_id = 'exec-a'
    stopped = []
    node._request_group_stop = lambda after_cycle: (
        stopped.append(after_cycle) or {'success': True, 'message': '그룹 정지'}
    )

    result = node._handle_local_request({'command': 'temporarily_disable'})

    assert result['success'] is True
    assert stopped == [False], '회차를 기다리지 않고 즉시 세운다'
    assert node._joined is False


def test_temporary_disable_without_a_run_is_the_same_as_leaving():
    node = _idle_node()
    node._request_group_stop = lambda after_cycle: pytest.fail('세울 것이 없다')

    result = node._handle_local_request({'command': 'temporarily_disable'})

    assert result['success'] is True
    assert node._joined is False


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
