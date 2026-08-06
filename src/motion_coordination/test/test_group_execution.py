import pytest

from motion_coordination.group_execution import GroupExecution, Member, MemberRegistry


@pytest.mark.parametrize('count', range(1, 9))
def test_group_barrier_supports_one_to_eight_pcs(count):
    pcs = [f'pc-{index}' for index in range(count)]
    execution = GroupExecution(start_lead_sec=0.3)
    execution.begin(pcs[0], pcs)
    for pc in pcs:
        execution.mark_ready(pc)
    initialize = execution.initialize_action(now=100.0)
    assert initialize.scheduled_at == pytest.approx(100.3)
    for pc in pcs:
        execution.mark_armed(pc)
    first = execution.start_action(now=200.0)
    assert first.cycle_number == 1
    for pc in pcs:
        execution.mark_scheduled(pc, 1)
        execution.mark_triggered(pc, 1, 200.3 + (pcs.index(pc) * 0.001))
    for pc in pcs:
        execution.mark_cycle_ready(pc, 1)
    assert execution.state == 'cycle_ready'
    second = execution.start_action(now=210.0)
    assert second.cycle_number == 2


def test_trigger_spread_uses_software_trigger_timestamps():
    execution = GroupExecution(max_start_spread_ms=20.0)
    execution.begin('a', ['a', 'b', 'c'])
    for pc in execution.participants:
        execution.mark_ready(pc)
    execution.initialize_action(now=1.0)
    for pc in execution.participants:
        execution.mark_armed(pc)
    execution.start_action(now=2.0)
    execution.mark_triggered('a', 1, 2.300)
    execution.mark_triggered('b', 1, 2.309)
    execution.mark_triggered('c', 1, 2.318)
    assert execution.last_start_spread_ms == pytest.approx(18.0)
    assert execution.trigger_within_tolerance() is True


def test_initialize_trigger_spread_uses_software_trigger_timestamps():
    execution = GroupExecution(max_start_spread_ms=20.0)
    execution.begin('a', ['a', 'b'])
    execution.mark_ready('a')
    execution.mark_ready('b')
    execution.initialize_action(now=1.0)
    execution.mark_armed('a', 1.300)
    execution.mark_armed('b', 1.321)
    assert execution.last_initialize_spread_ms == pytest.approx(21.0)
    assert execution.initialize_within_tolerance() is False


def test_member_registry_warns_then_expires():
    registry = MemberRegistry(warning_timeout_sec=1.5, timeout_sec=3.0)
    registry.update(Member('a', 'boot', True, 'ready', 'ready', 1.0, 0, 10.0))
    assert registry.status('a', now=11.0) == 'online'
    assert registry.status('a', now=12.0) == 'warning'
    assert registry.status('a', now=13.0) == 'offline'


def test_member_registry_ignores_duplicate_or_older_heartbeat_sequence():
    registry = MemberRegistry()
    registry.update(Member('a', 'boot', True, 'ready', 'ready', 1.0, 0, 10.0, 5))
    registry.update(Member('a', 'boot', True, 'error', 'ready', 1.0, 2, 11.0, 4))
    assert registry.member('a').state == 'ready'
    registry.update(Member('a', 'boot', True, 'running', 'ready', 1.0, 0, 12.0, 6))
    assert registry.member('a').state == 'running'


def test_stop_after_cycle_before_running_stops_without_next_start():
    execution = GroupExecution()
    execution.begin('a', ['a', 'b'])
    execution.request_stop_after_cycle()
    assert execution.state == 'stopped'


def test_next_start_is_blocked_until_every_pc_reports_cycle_ready():
    execution = GroupExecution()
    execution.begin('a', ['a', 'b', 'c'])
    for pc in execution.participants:
        execution.mark_ready(pc)
    execution.initialize_action(now=1.0)
    for pc in execution.participants:
        execution.mark_armed(pc)
    execution.start_action(now=2.0)
    for pc in execution.participants:
        execution.mark_triggered(pc, 1, 2.3)
    execution.mark_cycle_ready('a', 1)
    execution.mark_cycle_ready('b', 1)
    with pytest.raises(ValueError, match='전체 PC'):
        execution.start_action(now=3.0)
    execution.mark_cycle_ready('c', 1)
    assert execution.start_action(now=3.0).cycle_number == 2
