import yaml

from motion_common.group_config import (
    GroupConfig,
    load_group_config,
    migrate_legacy_group_config,
    save_group_config,
)


def test_version_one_config_migrates_disabled_without_project_data(tmp_path):
    path = tmp_path / 'motion_coordination.yaml'
    path.write_text('version: 1\nmachine_id: pc-a\ndisplay_name: A\n', encoding='utf-8')
    config = load_group_config(path)
    assert config.pc_id == 'pc-a'
    assert config.enabled is False
    assert config.group_id == ''


def test_version_one_file_is_replaced_with_only_disabled_dds_fields(tmp_path):
    path = tmp_path / 'motion_coordination.yaml'
    path.write_text(
        'version: 1\nmachine_id: pc-a\ndisplay_name: A\n'
        'heartbeat_sec: 1.0\npeer_timeout_sec: 4.0\n'
        'role: peer\ncoordinator_machine_id: pc-b\n'
        'credential_file: old.credentials.yaml\n'
        'access:\n  coordination:\n    port: 8010\n',
        encoding='utf-8',
    )

    config, migrated = migrate_legacy_group_config(path)
    stored = yaml.safe_load(path.read_text(encoding='utf-8'))

    assert migrated is True
    assert config.pc_id == 'pc-a'
    assert config.display_name == 'A'
    assert config.enabled is False
    assert stored['version'] == 2
    assert stored['pc_id'] == 'pc-a'
    assert stored['display_name'] == 'A'
    assert stored['enabled'] is False
    assert stored['group_id'] == ''
    assert stored['dds_domain_id'] == 21
    assert stored['heartbeat_sec'] == 0.5
    assert stored['warning_timeout_sec'] == 1.5
    assert stored['peer_timeout_sec'] == 3.0
    assert stored['start_lead_sec'] == 0.5
    assert stored['max_trigger_sync_uncertainty_ms'] == 20.0
    assert stored['trigger_sync_samples'] == 5
    assert stored['trigger_report_timeout_sec'] == 1.0
    assert 'role' not in stored
    assert 'coordinator_machine_id' not in stored
    assert 'credential_file' not in stored
    assert 'access' not in stored


def test_version_two_config_round_trip(tmp_path):
    path = tmp_path / 'motion_coordination.yaml'
    expected = GroupConfig('pc-a', 'A', True, 'stage-a', 21)
    save_group_config(path, expected)
    assert load_group_config(path) == expected
    assert path.stat().st_mode & 0o777 == 0o600


def test_old_clock_field_is_rewritten_as_relative_trigger_sync_setting(tmp_path):
    path = tmp_path / 'motion_coordination.yaml'
    path.write_text(
        'version: 2\npc_id: pc-a\ndisplay_name: A\nenabled: false\n'
        "group_id: ''\ndds_domain_id: 21\nstart_lead_sec: 0.3\n"
        'max_clock_offset_ms: 5.0\n',
        encoding='utf-8',
    )

    config, migrated = migrate_legacy_group_config(path)
    stored = yaml.safe_load(path.read_text(encoding='utf-8'))

    assert migrated is True
    assert config.start_lead_sec == 0.5
    assert config.max_trigger_sync_uncertainty_ms == 20.0
    assert 'max_clock_offset_ms' not in stored
    assert stored['max_trigger_sync_uncertainty_ms'] == 20.0


def test_invalid_timeout_order_is_rejected(tmp_path):
    path = tmp_path / 'motion_coordination.yaml'
    path.write_text(
        'version: 2\npc_id: pc-a\nenabled: true\ngroup_id: stage-a\n'
        'warning_timeout_sec: 4\npeer_timeout_sec: 3\n', encoding='utf-8'
    )
    try:
        load_group_config(path)
    except ValueError as exc:
        assert '순서' in str(exc)
    else:
        raise AssertionError('invalid timeout order accepted')


def test_enabled_config_requires_group_id_before_file_is_replaced(tmp_path):
    path = tmp_path / 'motion_coordination.yaml'
    save_group_config(path, GroupConfig('pc-a', 'A', True, 'stage-a', 21))
    original = path.read_text(encoding='utf-8')
    try:
        save_group_config(path, GroupConfig('pc-a', 'A', True, '', 21))
    except ValueError as exc:
        assert '그룹 ID' in str(exc)
    else:
        raise AssertionError('enabled config without group ID accepted')
    assert path.read_text(encoding='utf-8') == original
