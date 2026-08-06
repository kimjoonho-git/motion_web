from types import SimpleNamespace

from motion_coordination.alarm_registry import AlarmRegistry


def _alarm(boot_id, sequence=1):
    return SimpleNamespace(pc_id='pc-b', boot_id=boot_id, sequence=sequence)


def test_alarm_from_new_boot_waits_for_matching_heartbeat_then_is_released():
    registry = AlarmRegistry()
    message = _alarm('boot-new')

    assert registry.accept(message, current_boot_id='boot-old') is False
    assert registry.member_boot_changed(
        'pc-b', 'boot-new', previous_boot_id='boot-old',
    ) is message
    assert registry.accept(message, current_boot_id='boot-new') is True


def test_alarm_from_previous_boot_is_discarded_after_restart():
    registry = AlarmRegistry()
    registry.member_boot_changed(
        'pc-b', 'boot-new', previous_boot_id='boot-old',
    )
    assert registry.accept(
        _alarm('boot-old', 99), current_boot_id='boot-new',
    ) is False


def test_duplicate_sequence_is_rejected_within_same_boot():
    registry = AlarmRegistry()
    assert registry.accept(_alarm('boot-a', 2), 'boot-a') is True
    assert registry.accept(_alarm('boot-a', 2), 'boot-a') is False
