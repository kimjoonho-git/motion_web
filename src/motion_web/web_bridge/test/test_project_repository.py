import json
import hashlib
import os
import shutil
import threading
import time
from pathlib import Path

import pytest
import yaml

import motion_web_bridge.project_repository as project_repository_module
from motion_web_bridge import service_entrypoint
from motion_web_bridge.motor_restart_coordinator import MotorRestartCoordinator
from motion_web_bridge.project_repository import (
    MAX_MOTION_TEXT_BYTES,
    MAX_TEXT_BYTES,
    ProjectRepository,
    _text_limit,
)
from motion_web_bridge.bridge_node import (
    MotionWebBridge,
    _project_tree_category_signature,
)
from motion_web_bridge.service_entrypoint import (
    resolve_applied_motor_config,
    resolve_project_generation,
)


MOTION_TEXT = '\n'.join([
    json.dumps({'type': 'motion_header', 'rotation_unit': 'deg'}),
    json.dumps([1, 0.0, '1-1', 0.0]),
])


def test_motion_files_have_a_separate_large_file_limit():
    assert _text_limit('motions') == (MAX_MOTION_TEXT_BYTES, '256MB')
    assert MAX_MOTION_TEXT_BYTES == 256 * 1024 * 1024
    assert _text_limit('motor_axes') == (MAX_TEXT_BYTES, '10MB')


def test_project_generation_is_monotonic_and_survives_repository_restart(tmp_path):
    root = tmp_path / 'projects'
    repository = ProjectRepository(root)
    project_id = repository.create_project('generation')['project']['project_id']

    assert repository.project_generation() == 1
    repository.set_project_generation(4)
    repository.select_project(project_id)

    restarted = ProjectRepository(root)
    assert restarted.project_generation() == 4
    assert restarted.selected_project_id() == project_id
    with pytest.raises(ValueError, match='감소'):
        restarted.set_project_generation(3)


def test_new_project_is_ready_for_first_run_without_legacy_files(tmp_path):
    repository = ProjectRepository(tmp_path / 'projects')
    created = repository.create_project('처음 시작')
    project_id = created['project']['project_id']
    project_dir = tmp_path / 'projects' / project_id

    assert not (project_dir / 'motor_axes' / 'motor_axes.yaml').exists()
    assert list((project_dir / 'motion_axis_matching').iterdir()) == []
    assert created['project']['active_files']['motor_axes'] == ''
    assert created['project']['active_files']['motion_axis_matching'] == ''

    with pytest.raises(ValueError, match='모터축 설정 파일'):
        repository.prepare_runtime_motor_config(project_id)
    repository.save_file(
        project_id,
        'motor_axes',
        'motor_axes.yaml',
        'period: 1000000\nmasters:\n- id: 0\n  type: ethercat\n  slaves:\n'
        '  - controller_index: 0\n    driver_id: 0\n    alias: 403\n    position: 1\n'
        'web_axis_identities:\n- controller_index: 0\n  eeprom_alias: 403\n'
        '  rotary_alias: 3\n  slave_position: 1\n  vendor_id: 1647\n'
        '  product_id: 1614282756\n  revision_number: 65536\n'
        '  serial_number: 123456\n  identity_source: physical_sii\n'
        'drivers:\n- id: 0\n  type: minas\n'
        '  profile_velocity: 10\n  profile_acceleration: 20\n  profile_deceleration: 20\n',
    )
    runtime = repository.prepare_runtime_motor_config(project_id)
    runtime_path = project_dir / 'runtime' / 'applied_motor_config.yaml'
    assert runtime_path.is_file()
    assert 'web_axis_identities' not in yaml.safe_load(runtime_path.read_text())
    assert 'web_axis_profiles' not in yaml.safe_load(runtime_path.read_text())
    assert not repository.get_project(project_id)['project']['setup_status']['motor_applied']
    repository.mark_runtime_motor_config_applied(project_id)
    assert repository.get_project(project_id)['project']['setup_status']['motor_applied']


def test_runtime_accepts_web_confirmed_physical_sii_identity_source(tmp_path):
    repository = ProjectRepository(tmp_path / 'projects')
    project_id = repository.create_project('confirmed physical scan')['project']['project_id']
    repository.save_file(
        project_id,
        'motor_axes',
        'motor_axes.yaml',
        'period: 1000000\nmasters:\n- id: 0\n  type: ethercat\n  slaves:\n'
        '  - controller_index: 0\n    driver_id: 0\n    alias: 0\n    position: 0\n'
        'web_axis_identities:\n- controller_index: 0\n  eeprom_alias: 0\n'
        '  slave_position: 0\n  vendor_id: 1647\n  product_id: 1614282756\n'
        '  revision_number: 65536\n  serial_number: 123456\n'
        '  identity_source: physical_sii_user_confirmed\n'
        'web_axis_profiles:\n- controller_index: 0\n  driver_model: MADLN05BE\n'
        '  model_confirmed: true\n  model_source: physical_sii_user_confirmed\n'
        'drivers:\n- id: 0\n  type: minas\n  driver_model: MADLN05BE\n'
        '  profile_velocity: 10\n  profile_acceleration: 20\n'
        '  profile_deceleration: 20\n',
    )

    runtime = repository.prepare_runtime_motor_config(project_id)

    assert runtime['success'] is True


def test_runtime_keeps_two_ethercat_masters_with_duplicate_slave_positions(tmp_path):
    repository = ProjectRepository(tmp_path / 'projects')
    project_id = repository.create_project('dual ethercat')['project']['project_id']
    payload = {
        'period': 1000000,
        'masters': [
            {
                'id': master_index,
                'type': 'ethercat',
                'ethercat_master_index': master_index,
                'number_of_slaves': 1,
                'slaves': [{
                    'controller_index': master_index,
                    'driver_id': master_index,
                    'alias': 0,
                    'position': 0,
                    'vendor_id': 1647,
                    'product_id': 1614282756,
                    'profile_mode': 0,
                }],
            }
            for master_index in (0, 1)
        ],
        'web_axis_identities': [
            {
                'controller_index': master_index,
                'ethercat_master_index': master_index,
                'eeprom_alias': 0,
                'slave_position': 0,
                'vendor_id': 1647,
                'product_id': 1614282756,
                'revision_number': 65536,
                'serial_number': 123456 + master_index,
                'identity_source': 'physical_sii',
            }
            for master_index in (0, 1)
        ],
        'web_axis_profiles': [
            {
                'controller_index': master_index,
                'driver_model': 'MADLN05BE',
                'model_confirmed': True,
                'model_source': 'physical_sii_user_confirmed',
            }
            for master_index in (0, 1)
        ],
        'drivers': [
            {
                'id': master_index,
                'type': 'minas',
                'driver_model': 'MADLN05BE',
                'profile_velocity': 10,
                'profile_acceleration': 20,
                'profile_deceleration': 20,
            }
            for master_index in (0, 1)
        ],
    }
    repository.save_file(
        project_id,
        'motor_axes',
        'motor_axes.yaml',
        yaml.safe_dump(payload, sort_keys=False, allow_unicode=True),
    )

    runtime = repository.prepare_runtime_motor_config(project_id)
    applied = yaml.safe_load(Path(runtime['runtime_file']).read_text())

    assert [
        master['ethercat_master_index'] for master in applied['masters']
    ] == [0, 1]
    assert [
        master['slaves'][0]['position'] for master in applied['masters']
    ] == [0, 0]


def test_runtime_allows_same_dynamixel_id_on_different_serial_ports():
    payload = {
        'masters': [
            {
                'id': index,
                'type': 'serial',
                'serial_port': f'/dev/ttyUSB{index}',
                'slaves': [{
                    'controller_index': index,
                    'driver_id': 0,
                    'bus_id': 3,
                }],
            }
            for index in (0, 1)
        ],
        'web_axis_identities': [
            {
                'controller_index': index,
                'serial_port': f'/dev/ttyUSB{index}',
                'bus_id': 3,
            }
            for index in (0, 1)
        ],
        'drivers': [{
            'id': 0,
            'type': 'dynamixel',
            'profile_velocity': 10,
            'profile_acceleration': 20,
            'profile_deceleration': 20,
        }],
    }

    ProjectRepository._validate_runtime_motor_profiles(payload)


def test_runtime_rejects_duplicate_dynamixel_id_on_same_serial_port():
    payload = {
        'masters': [{
            'id': 0,
            'type': 'serial',
            'serial_port': '/dev/ttyUSB0',
            'slaves': [
                {'controller_index': 0, 'driver_id': 0, 'bus_id': 3},
                {'controller_index': 1, 'driver_id': 0, 'bus_id': 3},
            ],
        }],
        'web_axis_identities': [
            {'controller_index': 0, 'serial_port': '/dev/ttyUSB0', 'bus_id': 3},
            {'controller_index': 1, 'serial_port': '/dev/ttyUSB0', 'bus_id': 3},
        ],
        'drivers': [{
            'id': 0,
            'type': 'dynamixel',
            'profile_velocity': 10,
            'profile_acceleration': 20,
            'profile_deceleration': 20,
        }],
    }

    with pytest.raises(ValueError, match='ID 3.*중복'):
        ProjectRepository._validate_runtime_motor_profiles(payload)


def test_runtime_rejects_duplicate_ethercat_master_index(tmp_path):
    repository = ProjectRepository(tmp_path / 'projects')
    payload = {
        'period': 1000000,
        'masters': [
            {
                'id': master_id,
                'type': 'ethercat',
                'ethercat_master_index': 0,
                'slaves': [],
            }
            for master_id in (0, 1)
        ],
        'drivers': [],
    }

    with pytest.raises(ValueError, match='EtherCAT Master 0 설정이 중복'):
        repository._validate_runtime_motor_profiles(payload)


def test_runtime_rejects_unverified_ac_servo_profile(tmp_path):
    repository = ProjectRepository(tmp_path / 'projects')
    project_id = repository.create_project('unverified driver')['project']['project_id']
    repository.save_file(
        project_id,
        'motor_axes',
        'motor_axes.yaml',
        'period: 1000000\nmasters:\n- id: 0\n  type: ethercat\n  slaves:\n'
        '  - controller_index: 0\n    driver_id: 0\n    alias: 0\n    position: 0\n'
        'drivers:\n- id: 0\n  type: minas\n  driver_model: UNVERIFIED_MINAS\n'
        '  profile_velocity: 10\n  profile_acceleration: 20\n'
        '  profile_deceleration: 20\n',
    )

    with pytest.raises(ValueError, match='실제 서보 드라이버 모델'):
        repository.prepare_runtime_motor_config(project_id)


def test_runtime_rejects_unconfirmed_legacy_nameplate_model(tmp_path):
    repository = ProjectRepository(tmp_path / 'projects')
    project_id = repository.create_project('unconfirmed nameplate')['project']['project_id']
    repository.save_file(
        project_id,
        'motor_axes',
        'motor_axes.yaml',
        'period: 1000000\nmasters:\n- id: 0\n  type: ethercat\n  slaves:\n'
        '  - controller_index: 0\n    driver_id: 0\n    alias: 0\n    position: 0\n'
        '    vendor_id: 1647\n    product_id: 1614282756\n'
        'web_axis_identities:\n- controller_index: 0\n  eeprom_alias: 0\n'
        '  slave_position: 0\n  vendor_id: 1647\n  product_id: 1614282756\n'
        '  revision_number: 65536\n  serial_number: 123456\n'
        '  identity_source: physical_sii\n  nameplate_confirmed: false\n'
        'drivers:\n- id: 0\n  type: minas\n  driver_model: MADLN05BE\n'
        '  profile_velocity: 10\n  profile_acceleration: 20\n'
        '  profile_deceleration: 20\n',
    )

    with pytest.raises(ValueError, match='명판 확인되지 않았습니다'):
        repository.prepare_runtime_motor_config(project_id)


def test_unconfirmed_ac_servo_can_be_saved_but_not_applied(tmp_path):
    repository = ProjectRepository(tmp_path / 'projects')
    project_id = repository.create_project('save before model confirmation')['project']['project_id']
    bridge = MotionWebBridge.__new__(MotionWebBridge)
    bridge.project_repository = repository
    bridge.workspace_root = tmp_path
    bridge.motor_config_file = (
        tmp_path / 'projects' / project_id / 'motor_axes' / 'motor_axes.yaml'
    )
    config = yaml.safe_load(
        'period: 1000000\nmasters:\n- id: 0\n  type: ethercat\n  slaves:\n'
        '  - controller_index: 0\n    driver_id: 0\n    alias: 0\n    position: 0\n'
        '    vendor_id: 1647\n    product_id: 1614282756\n'
        'web_axis_identities:\n- controller_index: 0\n  eeprom_alias: 0\n'
        '  slave_position: 0\n  vendor_id: 1647\n  product_id: 1614282756\n'
        '  revision_number: 65536\n  serial_number: 123456\n'
        '  identity_source: physical_sii\n'
        'web_axis_profiles:\n- controller_index: 0\n  driver_model: ""\n'
        '  model_confirmed: false\n  model_source: ""\n'
        'drivers:\n- id: 0\n  type: minas\n  driver_model: UNVERIFIED_MINAS\n'
        '  profile_velocity: 18000\n  profile_acceleration: 180000\n'
        '  profile_deceleration: 180000\n'
    )

    result = bridge.save_motor_config({
        'registry': bridge._registry_from_motor_config(config),
        'file_name': 'motor_axes.yaml',
        'base_revision': '',
    })

    assert result['success'] is True
    assert repository.get_project(project_id)['project']['active_files']['motor_axes'] == (
        'motor_axes.yaml'
    )
    with pytest.raises(ValueError, match='실제 서보 드라이버 모델'):
        repository.prepare_runtime_motor_config(project_id)


def test_first_motor_config_save_returns_persisted_axes_before_apply(tmp_path):
    """New projects have no active motor_axes file until the first save.

    The save response must register that file and return the persisted axes so
    the UI can satisfy hasConfiguredAxes && !changed without a manual reload.
    """
    repository = ProjectRepository(tmp_path / 'projects')
    project_id = repository.create_project('first save apply ready')['project']['project_id']
    assert repository.get_project(project_id)['project']['active_files']['motor_axes'] == ''

    bridge = MotionWebBridge.__new__(MotionWebBridge)
    bridge.project_repository = repository
    bridge.workspace_root = tmp_path
    bridge.motor_config_file = (
        tmp_path / 'projects' / project_id / 'motor_axes' / 'motor_axes.yaml'
    )
    config = yaml.safe_load(
        'period: 1000000\nmasters:\n- id: 0\n  type: ethercat\n  slaves:\n'
        '  - controller_index: 0\n    driver_id: 0\n    alias: 101\n    position: 0\n'
        '    vendor_id: 1647\n    product_id: 1614282756\n'
        'web_axis_identities:\n- controller_index: 0\n  eeprom_alias: 101\n'
        '  slave_position: 0\n  vendor_id: 1647\n  product_id: 1614282756\n'
        '  revision_number: 65536\n  serial_number: 123456\n'
        '  identity_source: physical_sii\n'
        'web_axis_profiles:\n- controller_index: 0\n  driver_model: MADLN05BE\n'
        '  model_confirmed: true\n  model_source: user_nameplate\n'
        'drivers:\n- id: 0\n  type: minas\n  driver_model: MADLN05BE\n'
        '  profile_velocity: 18000\n  profile_acceleration: 180000\n'
        '  profile_deceleration: 180000\n'
    )
    registry = bridge._registry_from_motor_config(config)

    result = bridge.save_motor_config({
        'registry': registry,
        'file_name': 'motor_axes.yaml',
        'base_revision': '',
    })

    assert result['success'] is True
    assert result.get('project_sync', {}).get('synced') is True
    assert repository.get_project(project_id)['project']['active_files']['motor_axes'] == (
        'motor_axes.yaml'
    )
    assert result['config_file'].endswith('motor_axes.yaml')
    assert result['config_revision']
    assert len(result['registry'].get('motors') or []) == 1
    assert int(result['registry']['motors'][0]['axis']) == 0
    assert int(result['registry']['motors'][0]['identity']['ethercat_alias']) == 101
    assert result['content']

    renamed = bridge._registry_from_motor_config(config)
    renamed['motors'][0]['name'] = '왼쪽 서보'
    renamed['motors'][0]['identity']['ethercat_alias'] = 101
    second = bridge.save_motor_config({
        'registry': renamed,
        'file_name': 'motor_axes.yaml',
        'base_revision': result['config_revision'],
    })
    assert second['success'] is True
    assert second['registry']['motors'][0]['name'] == '왼쪽 서보'
    assert int(second['registry']['motors'][0]['identity']['ethercat_alias']) == 101
    assert second['config_revision'] != result['config_revision']

    reloaded = bridge.load_motor_config()
    assert reloaded['success'] is True
    assert len(reloaded['registry'].get('motors') or []) == 1
    assert reloaded['registry']['motors'][0]['name'] == '왼쪽 서보'
    assert reloaded['config_revision'] == second['config_revision']


def test_runtime_uses_separate_axis_model_profile_confirmation(tmp_path):
    repository = ProjectRepository(tmp_path / 'projects')
    project_id = repository.create_project('separate model profile')['project']['project_id']
    content = (
        'period: 1000000\nmasters:\n- id: 0\n  type: ethercat\n  slaves:\n'
        '  - controller_index: 0\n    driver_id: 0\n    alias: 0\n    position: 0\n'
        '    vendor_id: 1647\n    product_id: 1614282756\n'
        'web_axis_identities:\n- controller_index: 0\n  eeprom_alias: 0\n'
        '  slave_position: 0\n  vendor_id: 1647\n  product_id: 1614282756\n'
        '  revision_number: 65536\n  serial_number: 123456\n'
        '  identity_source: physical_sii\n'
        'web_axis_profiles:\n- controller_index: 0\n  driver_model: MADLN05BE\n'
        '  model_confirmed: false\n  model_source: ""\n'
        'drivers:\n- id: 0\n  type: minas\n  driver_model: MADLN05BE\n'
        '  profile_velocity: 10\n  profile_acceleration: 20\n'
        '  profile_deceleration: 20\n'
    )
    repository.save_file(project_id, 'motor_axes', 'motor_axes.yaml', content)

    with pytest.raises(ValueError, match='명판 확인되지 않았습니다'):
        repository.prepare_runtime_motor_config(project_id)

    repository.save_file(
        project_id,
        'motor_axes',
        'motor_axes.yaml',
        content.replace('model_confirmed: false', 'model_confirmed: true').replace(
            'model_source: ""',
            'model_source: user_nameplate',
        ),
    )
    runtime = repository.prepare_runtime_motor_config(project_id)
    runtime_payload = yaml.safe_load(Path(runtime['runtime_file']).read_text())
    assert 'web_axis_identities' not in runtime_payload
    assert 'web_axis_profiles' not in runtime_payload


def test_legacy_untouched_motor_placeholder_is_removed(tmp_path):
    root = tmp_path / 'projects'
    repository = ProjectRepository(root)
    project = repository.create_project('legacy placeholder')['project']
    project_id = project['project_id']
    project_dir = root / project_id
    placeholder = project_dir / 'motor_axes' / 'motor_axes.yaml'
    placeholder.write_text(
        'period: 1000000\nmasters: []\ndrivers: []\n', encoding='utf-8'
    )
    manifest_path = project_dir / 'project.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    manifest['active_files']['motor_axes'] = 'motor_axes.yaml'
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding='utf-8')
    os.utime(placeholder, (manifest['created_at'], manifest['created_at']))

    migrated = ProjectRepository(root).get_project(project_id)

    assert not placeholder.exists()
    assert migrated['project']['active_files']['motor_axes'] == ''
    assert migrated['project']['counts']['motor_axes'] == 0


def test_execution_context_changes_with_active_file_content(tmp_path):
    repository = ProjectRepository(tmp_path / 'projects')
    project_id = repository.create_project('context')['project']['project_id']
    repository.save_file(
        project_id,
        'motor_axes',
        'motor_axes.yaml',
        'period: 1000000\nmasters: []\ndrivers: []\n',
    )
    repository.import_text(
        project_id,
        'motion_axis_matching',
        'mapping.yaml',
        'version: 1\nname: context\nmappings: []\n',
    )

    before = repository.execution_context(project_id)
    repository.save_file(
        project_id,
        'motion_axis_matching',
        'mapping.yaml',
        'version: 1\nname: changed\nmappings: []\n',
    )
    after = repository.execution_context(project_id)

    assert before['configuration_complete'] is True
    assert before['context_id'] != after['context_id']
    assert (
        before['files']['motion_axis_matching']['sha256']
        != after['files']['motion_axis_matching']['sha256']
    )


def test_web_only_motor_identity_change_does_not_require_motor_runtime_restart(tmp_path):
    repository = ProjectRepository(tmp_path / 'projects')
    project_id = repository.create_project('identity metadata')['project']['project_id']
    source = (
        'period: 1000000\nmasters:\n- id: 0\n  type: ethercat\n  slaves:\n'
        '  - controller_index: 0\n    driver_id: 0\n    alias: 403\n    position: 1\n'
        'web_axis_identities:\n- controller_index: 0\n  eeprom_alias: 403\n'
        '  rotary_alias: 3\n  slave_position: 1\n  vendor_id: 1647\n'
        '  product_id: 1614282756\n  revision_number: 65536\n'
        '  serial_number: 123456\n  identity_source: physical_sii\n'
        'drivers:\n- id: 0\n  type: minas\n'
        '  profile_velocity: 10\n  profile_acceleration: 20\n  profile_deceleration: 20\n'
    )
    repository.save_file(project_id, 'motor_axes', 'motor_axes.yaml', source)
    repository.prepare_runtime_motor_config(project_id)
    repository.mark_runtime_motor_config_applied(project_id)
    assert repository.execution_context(project_id)['motor_applied'] is True

    repository.save_file(
        project_id,
        'motor_axes',
        'motor_axes.yaml',
        source.replace('rotary_alias: 3', 'rotary_alias: 4'),
    )

    assert repository.execution_context(project_id)['motor_applied'] is True

    repository.save_file(
        project_id,
        'motor_axes',
        'motor_axes.yaml',
        source.replace('profile_velocity: 10', 'profile_velocity: 11'),
    )

    assert repository.execution_context(project_id)['motor_applied'] is False


def test_runtime_motor_config_rejects_identity_position_mismatch(tmp_path):
    repository = ProjectRepository(tmp_path / 'projects')
    project_id = repository.create_project('불일치 검사')['project']['project_id']
    repository.save_file(
        project_id,
        'motor_axes',
        'motor_axes.yaml',
        'period: 1000000\nmasters:\n- id: 0\n  type: ethercat\n  slaves:\n'
        '  - controller_index: 0\n    driver_id: 0\n    alias: 403\n    position: 0\n'
        'web_axis_identities:\n- controller_index: 0\n  eeprom_alias: 403\n'
        '  rotary_alias: 3\n  slave_position: 1\n  vendor_id: 1647\n'
        '  product_id: 1614282756\n  revision_number: 65536\n'
        '  serial_number: 123456\n  identity_source: physical_sii\n'
        'drivers:\n- id: 0\n  type: minas\n'
        '  profile_velocity: 10\n  profile_acceleration: 20\n  profile_deceleration: 20\n',
    )

    with pytest.raises(ValueError, match='Slave Position'):
        repository.prepare_runtime_motor_config(project_id)


def test_same_named_files_remain_isolated_between_projects(tmp_path):
    repository = ProjectRepository(tmp_path / 'projects')
    first_id = repository.create_project('first')['project']['project_id']
    second_id = repository.create_project('second')['project']['project_id']
    first_text = 'version: 1\nname: first\nmappings: []\n'
    second_text = 'version: 1\nname: second\nmappings: []\n'

    repository.import_text(
        first_id, 'motion_axis_matching', 'motion_axes.yaml', first_text
    )
    repository.import_text(
        second_id, 'motion_axis_matching', 'motion_axes.yaml', second_text
    )

    first_path = repository.export_path(
        first_id, 'motion_axis_matching', 'motion_axes.yaml'
    )
    second_path = repository.export_path(
        second_id, 'motion_axis_matching', 'motion_axes.yaml'
    )
    assert first_path != second_path
    assert first_path.read_text(encoding='utf-8') == first_text
    assert second_path.read_text(encoding='utf-8') == second_text

    repository.delete_file(first_id, 'motion_axis_matching', 'motion_axes.yaml')
    assert not first_path.exists()
    assert second_path.read_text(encoding='utf-8') == second_text
    assert repository.get_project(first_id)['project']['active_files']['motion_axis_matching'] == ''
    assert repository.get_project(second_id)['project']['active_files']['motion_axis_matching'] == 'motion_axes.yaml'


def test_project_tree_shows_embedded_midi_banks_and_missing_state(tmp_path):
    repository = ProjectRepository(tmp_path / 'projects')
    project_id = repository.create_project('MIDI tree')['project']['project_id']
    repository.import_text(
        project_id,
        'motion_axis_matching',
        'without_banks.yaml',
        'name: no banks\nmappings: []\n',
    )
    repository.import_text(
        project_id,
        'motion_axis_matching',
        'with_banks.yaml',
        'name: banks\nmappings: []\nmidi_banks:\n'
        '  version: 1\n  active_bank_id: bank_2\n  banks:\n'
        '  - bank_id: bank_1\n    name: Basic\n    mappings: []\n'
        '  - bank_id: bank_2\n    name: Face\n    mappings: [{channel: 0}]\n',
    )

    tree = repository.get_project(project_id)['tree']
    folder = next(item for item in tree if item['category'] == 'motion_axis_matching')
    files = {item['name']: item for item in folder['children']}

    assert files['without_banks.yaml']['midi_banks']['stored'] is False
    saved = files['with_banks.yaml']['midi_banks']
    assert saved['stored'] is True
    assert saved['count'] == 2
    assert saved['active_bank_id'] == 'bank_2'
    assert saved['banks'][1] == {
        'bank_id': 'bank_2', 'name': 'Face', 'mapping_count': 1,
    }


def test_old_generated_empty_mapping_is_moved_to_its_own_project_trash(tmp_path):
    root = tmp_path / 'projects'
    project_dir = root / 'legacy-project'
    for name in ('motor_axes', 'motion_axis_matching', 'motions', 'layers', 'runtime', 'trash'):
        (project_dir / name).mkdir(parents=True, exist_ok=True)
    (project_dir / 'project.json').write_text(json.dumps({
        'version': 1,
        'project_id': 'legacy-project',
        'name': 'legacy',
        'active_files': {
            'motor_axes': '',
            'motion_axis_matching': 'motion_axes.yaml',
            'motions': '',
            'layers': '',
        },
    }), encoding='utf-8')
    generated = project_dir / 'motion_axis_matching' / 'motion_axes.yaml'
    generated.write_text(
        'file_id: motion_axes.yaml\nname: legacy-project_motion_axes\n'
        "motion_file_id: ''\nmappings: []\n",
        encoding='utf-8',
    )

    repository = ProjectRepository(root)

    assert not generated.exists()
    assert repository.get_project('legacy-project')['project']['active_files']['motion_axis_matching'] == ''
    assert list((project_dir / 'trash' / 'motion_axis_matching').glob('*motion_axes.yaml'))


def test_motor_config_load_ignores_stale_path_from_another_project(tmp_path):
    repository = ProjectRepository(tmp_path / 'projects')
    first_id = repository.create_project('first')['project']['project_id']
    second_id = repository.create_project('second')['project']['project_id']
    repository.save_file(
        first_id, 'motor_axes', 'motor_axes.yaml',
        'period: 1000000\nmasters: []\ndrivers: []\n',
    )
    repository.save_file(
        second_id, 'motor_axes', 'motor_axes.yaml',
        'period: 2000000\nmasters: []\ndrivers: []\n',
    )
    repository.select_project(second_id)

    bridge = MotionWebBridge.__new__(MotionWebBridge)
    bridge.project_repository = repository
    bridge.motor_config_file = repository.export_path(
        first_id, 'motor_axes', 'motor_axes.yaml'
    )

    loaded = bridge.load_motor_config()

    assert loaded['success'] is True
    assert Path(loaded['config_file']).parent.parent.name == second_id
    assert yaml.safe_load(loaded['content'])['period'] == 2000000


def test_project_without_saved_motor_file_clears_stale_editor_path(tmp_path):
    repository = ProjectRepository(tmp_path / 'projects')
    first_id = repository.create_project('first')['project']['project_id']
    repository.save_file(
        first_id, 'motor_axes', 'motor_axes.yaml',
        'period: 1000000\nmasters: []\ndrivers: []\n',
    )
    stale_path = repository.export_path(first_id, 'motor_axes', 'motor_axes.yaml')
    second_id = repository.create_project('second')['project']['project_id']
    repository.select_project(second_id)
    bridge = MotionWebBridge.__new__(MotionWebBridge)
    bridge.project_repository = repository
    bridge.motor_config_file = stale_path

    bridge._bind_selected_project_sources()
    loaded = bridge.load_motor_config()

    assert bridge.motor_config_file == Path()
    assert loaded['success'] is True
    assert loaded['saved'] is False
    assert loaded['config_file'] == ''
    target = bridge._motor_config_file_from_payload({})
    assert target == tmp_path / 'projects' / second_id / 'motor_axes' / 'motor_axes.yaml'


def test_no_selected_project_clears_stale_editor_path(tmp_path):
    repository = ProjectRepository(tmp_path / 'projects')
    bridge = MotionWebBridge.__new__(MotionWebBridge)
    bridge.project_repository = repository
    bridge.motor_config_file = tmp_path / 'old-project' / 'motor_axes.yaml'

    bridge._bind_selected_project_sources()

    assert bridge.motor_config_file == Path()


def test_delete_motor_config_only_moves_selected_project_file_to_its_trash(tmp_path):
    repository = ProjectRepository(tmp_path / 'projects')
    selected_id = repository.create_project('selected')['project']['project_id']
    other_id = repository.create_project('other')['project']['project_id']
    content = 'period: 1000000\nmasters: []\ndrivers: []\n'
    repository.save_file(selected_id, 'motor_axes', 'selected.yaml', content)
    repository.save_file(other_id, 'motor_axes', 'other.yaml', content)
    repository.select_project(selected_id)
    selected_path = repository.export_path(selected_id, 'motor_axes', 'selected.yaml')
    other_path = repository.export_path(other_id, 'motor_axes', 'other.yaml')

    bridge = MotionWebBridge.__new__(MotionWebBridge)
    bridge.project_repository = repository
    bridge.motor_config_file = selected_path
    bridge._write_motor_config_selection(selected_path)

    result = bridge.delete_motor_config()

    assert result['success'] is True
    assert result['deleted_file'] == 'selected.yaml'
    assert result['replacement_active_file'] == ''
    assert result['config_file'] == ''
    assert bridge.motor_config_file == Path()
    assert not selected_path.exists()
    assert other_path.is_file()
    assert repository.selected_project_id() == selected_id
    assert repository.get_project(selected_id)['project']['active_files']['motor_axes'] == ''
    assert not (
        tmp_path / 'projects' / selected_id / 'runtime' / 'selected_motor_config_path.txt'
    ).exists()
    assert list(
        (tmp_path / 'projects' / selected_id / 'trash' / 'motor_axes').glob(
            '*-selected.yaml'
        )
    )


def test_motor_config_save_rejects_stale_browser_revision(tmp_path):
    repository = ProjectRepository(tmp_path / 'projects')
    project_id = repository.create_project('revision')['project']['project_id']
    content = (
        'period: 1000000\nmasters:\n- id: 0\n  type: ethercat\n  slaves:\n'
        '  - controller_index: 0\n    driver_id: 0\ndrivers:\n- id: 0\n  type: minas\n'
    )
    repository.save_file(project_id, 'motor_axes', 'motor_axes.yaml', content)
    bridge = MotionWebBridge.__new__(MotionWebBridge)
    bridge.project_repository = repository
    bridge.motor_config_file = repository.export_path(
        project_id, 'motor_axes', 'motor_axes.yaml'
    )
    loaded = bridge.load_motor_config()
    bridge.motor_config_file.write_text(content.replace('1000000', '2000000'), encoding='utf-8')

    result = bridge.save_motor_config({
        'registry': loaded['registry'],
        'file_name': 'motor_axes.yaml',
        'base_revision': loaded['config_revision'],
    })

    assert result['success'] is False
    assert '현재 파일 보호를 위해 저장을 거부' in result['message']
    assert 'period: 2000000' in bridge.motor_config_file.read_text(encoding='utf-8')


def test_motor_config_save_rejects_zero_axis_overwrite(tmp_path):
    repository = ProjectRepository(tmp_path / 'projects')
    project_id = repository.create_project('zero guard')['project']['project_id']
    content = (
        'period: 1000000\nmasters:\n- id: 0\n  type: ethercat\n  slaves:\n'
        '  - controller_index: 0\n    driver_id: 0\ndrivers:\n- id: 0\n  type: minas\n'
    )
    repository.save_file(project_id, 'motor_axes', 'motor_axes.yaml', content)
    bridge = MotionWebBridge.__new__(MotionWebBridge)
    bridge.project_repository = repository
    bridge.motor_config_file = repository.export_path(
        project_id, 'motor_axes', 'motor_axes.yaml'
    )
    loaded = bridge.load_motor_config()

    result = bridge.save_motor_config({
        'registry': {'version': 1, 'motors': []},
        'file_name': 'motor_axes.yaml',
        'base_revision': loaded['config_revision'],
    })

    assert result['success'] is False
    assert '0축 모터 설정은 저장할 수 없습니다' in result['message']
    assert bridge.load_motor_config()['registry']['motors']


def test_runtime_motor_config_rejects_unusable_ac_profile(tmp_path):
    repository = ProjectRepository(tmp_path / 'projects')
    project_id = repository.create_project('slow profile')['project']['project_id']
    repository.save_file(
        project_id,
        'motor_axes',
        'motor_axes.yaml',
        'period: 1000000\nmasters:\n- id: 0\n  type: ethercat\n  slaves:\n'
        '  - controller_index: 0\n    driver_id: 0\ndrivers:\n- id: 0\n  type: minas\n'
        '  profile_velocity: 0.0374507\n  profile_acceleration: 1\n'
        '  profile_deceleration: 1\n',
    )

    with pytest.raises(ValueError, match='지나치게 낮습니다'):
        repository.prepare_runtime_motor_config(project_id)


def test_runtime_motor_config_removes_legacy_empty_ethercat_master(tmp_path):
    repository = ProjectRepository(tmp_path / 'projects')
    project_id = repository.create_project('serial only')['project']['project_id']
    repository.save_file(
        project_id,
        'motor_axes',
        'motor_axes.yaml',
        'period: 1000000\nmasters:\n'
        '- id: 0\n  type: ethercat\n  number_of_slaves: 0\n  slaves: []\n'
        '- id: 1\n  type: serial\n  port: /dev/ttyUSB0\n  number_of_slaves: 1\n'
        '  slaves:\n  - controller_index: 0\n    driver_id: 1\n    id: 1\n'
        'drivers:\n- id: 1\n  type: dynamixel\n'
        '  profile_velocity: 10\n  profile_acceleration: 20\n'
        '  profile_deceleration: 20\n',
    )

    prepared = repository.prepare_runtime_motor_config(project_id)
    runtime = yaml.safe_load(Path(prepared['runtime_file']).read_text(encoding='utf-8'))
    source = yaml.safe_load(
        repository.export_path(
            project_id, 'motor_axes', 'motor_axes.yaml'
        ).read_text(encoding='utf-8')
    )

    assert [master['type'] for master in runtime['masters']] == ['serial']
    assert [master['type'] for master in source['masters']] == ['ethercat', 'serial']


def test_project_asset_lifecycle_is_project_local_and_soft_deleted(tmp_path):
    repository = ProjectRepository(tmp_path / 'projects')
    created = repository.create_project('얼굴 모션')
    project_id = created['project']['project_id']

    repository.import_text(project_id, 'motions', 'hello.json', MOTION_TEXT)
    loaded = repository.read_file(project_id, 'motions', 'hello.json')
    assert loaded['content'].startswith('{')

    repository.rename_file(project_id, 'motions', 'hello.json', 'greeting.json')
    repository.set_active(project_id, 'motions', 'greeting.json')
    deleted = repository.delete_file(project_id, 'motions', 'greeting.json')

    manifest = json.loads(
        (tmp_path / 'projects' / project_id / 'project.json').read_text(encoding='utf-8')
    )
    assert manifest['active_files']['motions'] == ''
    assert deleted['replacement_active_file'] == ''
    assert not (tmp_path / 'projects' / project_id / 'motions' / 'greeting.json').exists()
    assert list((tmp_path / 'projects' / project_id / 'trash' / 'motions').glob('*-greeting.json'))


def test_project_memo_is_stored_in_project_manifest(tmp_path):
    repository = ProjectRepository(tmp_path / 'projects')
    project_id = repository.create_project('메모 테스트')['project']['project_id']

    updated = repository.update_project_memo(project_id, '장비 구성과 작업 내용을 기록')

    assert updated['project']['memo'] == '장비 구성과 작업 내용을 기록'
    manifest = json.loads(
        (tmp_path / 'projects' / project_id / 'project.json').read_text(encoding='utf-8')
    )
    assert manifest['memo'] == '장비 구성과 작업 내용을 기록'

    with pytest.raises(ValueError, match='4000자'):
        repository.update_project_memo(project_id, '가' * 4001)


def test_project_tree_includes_project_log_files(tmp_path):
    repository = ProjectRepository(tmp_path / 'projects')
    project_id = repository.create_project('로그 트리')['project']['project_id']
    log_file = repository.project_logs_dir(project_id) / '2026-07-18.jsonl'
    log_file.write_text('{"content":"첫 로그"}\n{"content":"둘째 로그"}\n', encoding='utf-8')

    tree = repository.get_project(project_id)['tree']
    logs = next(folder for folder in tree if folder['category'] == 'logs')

    assert logs['name'] == '로그'
    assert logs['children'][0]['name'] == '2026-07-18.jsonl'
    assert logs['children'][0]['record_count'] == 2


def test_project_tree_includes_root_runtime_and_trash_files(tmp_path):
    repository = ProjectRepository(tmp_path / 'projects')
    project_id = repository.create_project('전체 트리')['project']['project_id']
    project_dir = tmp_path / 'projects' / project_id
    runtime_file = project_dir / 'runtime' / 'history' / 'motor_axes' / 'previous.yaml'
    runtime_file.parent.mkdir(parents=True, exist_ok=True)
    runtime_file.write_text('period: 1000000\n', encoding='utf-8')
    trash_file = project_dir / 'trash' / 'motions' / 'deleted.json'
    trash_file.parent.mkdir(parents=True, exist_ok=True)
    trash_file.write_text('{}\n', encoding='utf-8')

    tree = repository.get_project(project_id)['tree']
    root = next(folder for folder in tree if folder['category'] == 'project_root')
    runtime = next(folder for folder in tree if folder['category'] == 'runtime')
    trash = next(folder for folder in tree if folder['category'] == 'trash')

    assert root['children'][0]['name'] == 'project.json'
    assert root['children'][0]['read_only'] is True
    history = next(node for node in runtime['children'] if node['name'] == 'history')
    motor_axes = next(node for node in history['children'] if node['name'] == 'motor_axes')
    assert motor_axes['children'][0]['relative_path'] == (
        'runtime/history/motor_axes/previous.yaml'
    )
    motions = next(node for node in trash['children'] if node['name'] == 'motions')
    assert motions['children'][0]['relative_path'] == 'trash/motions/deleted.json'


def test_active_required_file_delete_leaves_no_unsaved_replacement(tmp_path):
    repository = ProjectRepository(tmp_path / 'projects')
    project_id = repository.create_project('reset')['project']['project_id']
    repository.save_file(
        project_id, 'motor_axes', 'motor_axes.yaml',
        'period: 1000000\nmasters: []\ndrivers: []\n',
    )

    result = repository.delete_file(project_id, 'motor_axes', 'motor_axes.yaml')

    assert result['replacement_active_file'] == ''
    assert not (tmp_path / 'projects' / project_id / 'motor_axes' / 'motor_axes.yaml').exists()


def test_internal_motor_config_backups_move_to_project_runtime_history(tmp_path):
    repository = ProjectRepository(tmp_path / 'projects')
    created = repository.create_project('backup filtering')
    project_id = created['project']['project_id']
    motor_dir = tmp_path / 'projects' / project_id / 'motor_axes'
    (motor_dir / 'motor_axes.yaml.bak-20260716-171705').write_text(
        'period: 1000000\nmasters: []\ndrivers: []\n',
        encoding='utf-8',
    )

    repository = ProjectRepository(tmp_path / 'projects')
    project = repository.get_project(project_id)
    motor_folder = next(
        folder for folder in project['tree'] if folder['category'] == 'motor_axes'
    )

    assert [item['name'] for item in motor_folder['children']] == []
    assert project['project']['counts']['motor_axes'] == 0
    assert list(
        (tmp_path / 'projects' / project_id / 'runtime' / 'history' / 'motor_axes').glob(
            'motor_axes.yaml.bak-*'
        )
    )


def test_runtime_owner_remains_visible_when_another_project_is_selected(tmp_path):
    repository = ProjectRepository(tmp_path / 'projects')
    runtime_id = repository.create_project('runtime')['project']['project_id']
    selected_id = repository.create_project('editor')['project']['project_id']
    repository.select_project(selected_id)

    bridge = MotionWebBridge.__new__(MotionWebBridge)
    bridge.project_repository = repository
    bridge.motion_projects_dir = tmp_path / 'projects'
    bridge.applied_motor_config_file = (
        tmp_path / 'projects' / runtime_id / 'runtime' / 'applied_motor_config.yaml'
    )

    listed = bridge.list_motion_projects()

    assert listed['selected_project_id'] == selected_id
    assert listed['runtime_project_id'] == runtime_id
    assert next(
        item for item in listed['projects'] if item['project_id'] == runtime_id
    )['runtime_active'] is True


def test_project_switch_preserves_the_independent_applied_runtime(tmp_path):
    workspace = tmp_path / 'workspace'
    repository = ProjectRepository(workspace / 'motion_projects')
    runtime_id = repository.create_project('runtime')['project']['project_id']
    repository.save_file(
        runtime_id,
        'motor_axes',
        'motor_axes.yaml',
        'period: 1000000\nmasters:\n- id: 0\n  type: ethercat\n  slaves:\n'
        '  - controller_index: 0\n    driver_id: 0\ndrivers:\n- id: 0\n  type: minas\n'
        '  profile_velocity: 18000\n  profile_acceleration: 180000\n'
        '  profile_deceleration: 180000\n',
    )
    repository.prepare_runtime_motor_config(runtime_id)
    repository.mark_runtime_motor_config_applied(runtime_id)

    editor_id = repository.create_project('editor')['project']['project_id']
    repository.select_project(editor_id)

    selection = json.loads(
        (workspace / 'motion_projects' / '.selected_project.json').read_text(
            encoding='utf-8'
        )
    )
    assert selection['project_id'] == editor_id
    assert 'applied_project_id' not in selection
    runtime = repository.applied_runtime_motor_config()
    assert runtime is not None
    assert runtime.parents[2].name == runtime_id
    assert repository.selected_runtime_motor_config() is None
    assert resolve_applied_motor_config(workspace) == runtime
    runtime_state = json.loads(
        (workspace / 'motion_projects' / '.motor_runtime.json').read_text(
            encoding='utf-8'
        )
    )
    assert runtime_state['target_project_id'] == runtime_id
    previous = next(
        item for item in repository.list_projects()['projects']
        if item['project_id'] == runtime_id
    )
    assert previous['setup_status']['motor_applied'] is True


def test_repository_migrates_legacy_applied_runtime_without_changing_selection(
    tmp_path,
):
    root = tmp_path / 'projects'
    repository = ProjectRepository(root)
    runtime_id = repository.create_project('runtime')['project']['project_id']
    repository.save_file(
        runtime_id,
        'motor_axes',
        'motor_axes.yaml',
        'period: 1000000\nmasters:\n- id: 0\n  type: ethercat\n  slaves:\n'
        '  - controller_index: 0\n    driver_id: 0\ndrivers:\n- id: 0\n  type: minas\n'
        '  profile_velocity: 18000\n  profile_acceleration: 180000\n'
        '  profile_deceleration: 180000\n',
    )
    repository.prepare_runtime_motor_config(runtime_id)
    editor_id = repository.create_project('editor')['project']['project_id']
    selection_file = root / '.selected_project.json'
    selection = json.loads(selection_file.read_text(encoding='utf-8'))
    selection['project_id'] = editor_id
    selection['applied_project_id'] = runtime_id
    selection_file.write_text(json.dumps(selection), encoding='utf-8')

    migrated = ProjectRepository(root)

    assert migrated.selected_project_id() == editor_id
    assert migrated.applied_runtime_motor_config() is not None
    assert migrated.motor_runtime_state()['target_project_id'] == runtime_id
    assert 'applied_project_id' not in json.loads(
        selection_file.read_text(encoding='utf-8')
    )


def test_repository_repairs_corrupt_runtime_state_before_removing_legacy_target(
    tmp_path,
):
    root = tmp_path / 'projects'
    repository = ProjectRepository(root)
    runtime_id = repository.create_project('runtime')['project']['project_id']
    repository.save_file(
        runtime_id,
        'motor_axes',
        'motor_axes.yaml',
        'period: 1000000\nmasters:\n- id: 0\n  type: ethercat\n  slaves:\n'
        '  - controller_index: 0\n    driver_id: 0\ndrivers:\n- id: 0\n'
        '  type: minas\n  profile_velocity: 18000\n'
        '  profile_acceleration: 180000\n  profile_deceleration: 180000\n',
    )
    repository.prepare_runtime_motor_config(runtime_id)
    selection_file = root / '.selected_project.json'
    selection = json.loads(selection_file.read_text(encoding='utf-8'))
    selection['applied_project_id'] = runtime_id
    selection_file.write_text(json.dumps(selection), encoding='utf-8')
    (root / '.motor_runtime.json').write_text('{broken', encoding='utf-8')

    migrated = ProjectRepository(root)

    assert migrated.motor_runtime_state()['valid'] is True
    assert migrated.motor_runtime_state()['target_project_id'] == runtime_id
    assert 'applied_project_id' not in json.loads(
        selection_file.read_text(encoding='utf-8')
    )


def test_applied_runtime_rejects_modified_runtime_content(tmp_path):
    workspace = tmp_path / 'workspace'
    repository = ProjectRepository(workspace / 'motion_projects')
    project_id = repository.create_project('runtime integrity')['project']['project_id']
    repository.save_file(
        project_id,
        'motor_axes',
        'motor_axes.yaml',
        'period: 1000000\nmasters:\n- id: 0\n  type: ethercat\n  slaves:\n'
        '  - controller_index: 0\n    driver_id: 0\ndrivers:\n- id: 0\n  type: minas\n'
        '  profile_velocity: 18000\n  profile_acceleration: 180000\n'
        '  profile_deceleration: 180000\n',
    )
    prepared = repository.prepare_runtime_motor_config(project_id)
    repository.mark_runtime_motor_config_applied(project_id)
    runtime = Path(repository.motor_runtime_state()['config_file'])
    runtime.write_text(runtime.read_text(encoding='utf-8') + '# changed\n', encoding='utf-8')

    state = repository.motor_runtime_state()

    assert state['valid'] is False
    assert 'sha256 mismatch' in state['validation_error']
    assert repository.applied_runtime_motor_config() is None
    assert resolve_applied_motor_config(workspace) is None


def test_motor_operation_is_persistent_and_rejects_concurrent_work(tmp_path):
    root = tmp_path / 'projects'
    repository = ProjectRepository(root)

    started = repository.begin_motor_operation(
        'motor_scan',
        'preparing',
        timeout_sec=30.0,
        details={'project_id': 'project-a'},
    )
    with pytest.raises(ValueError, match='다른 모터'):
        repository.begin_motor_operation(
            'motor_restart',
            'preparing',
            timeout_sec=30.0,
        )

    restarted = ProjectRepository(root)
    restored = restarted.motor_operation_status()
    assert restored['operation_id'] == started['operation_id']
    assert restored['status'] == 'running'

    updated = restarted.update_motor_operation(
        started['operation_id'],
        'scanning',
        message='검색 중',
    )
    assert updated['phase'] == 'scanning'
    completed = restarted.finish_motor_operation(
        started['operation_id'],
        'success',
        phase='completed',
        message='검색 완료',
    )
    assert completed['status'] == 'success'
    assert ProjectRepository(root).motor_operation_status()['status'] == 'success'


def test_motor_operation_supports_terminal_partial_status(tmp_path):
    repository = ProjectRepository(tmp_path / 'projects')
    operation = repository.begin_motor_operation(
        'full_scan',
        'scanning',
        timeout_sec=30.0,
    )

    completed = repository.finish_motor_operation(
        operation['operation_id'],
        'partial',
        phase='partial',
        message='EtherCAT 성공 · Dynamixel 실패',
    )

    assert completed['status'] == 'partial'
    assert completed['phase'] == 'partial'
    assert repository.motor_operation_status()['status'] == 'partial'


def test_motor_operation_mutations_share_repository_runtime_lock(tmp_path):
    repository = ProjectRepository(tmp_path / 'projects')
    started = threading.Event()
    finished = threading.Event()

    def begin():
        started.set()
        repository.begin_motor_operation(
            'motor_restart',
            'preparing',
            timeout_sec=30.0,
        )
        finished.set()

    repository._motor_runtime_lock.acquire()
    worker = threading.Thread(target=begin)
    worker.start()
    assert started.wait(timeout=1.0)
    assert finished.wait(timeout=0.05) is False
    repository._motor_runtime_lock.release()
    worker.join(timeout=1.0)

    assert finished.is_set()
    assert repository.motor_operation_status()['type'] == 'motor_restart'


def test_marking_runtime_preserves_the_active_motor_operation(tmp_path):
    repository = ProjectRepository(tmp_path / 'projects')
    project_id = repository.create_project('operation apply')['project']['project_id']
    repository.save_file(
        project_id,
        'motor_axes',
        'motor_axes.yaml',
        'period: 1000000\nmasters:\n- id: 0\n  type: ethercat\n  slaves:\n'
        '  - controller_index: 0\n    driver_id: 0\ndrivers:\n- id: 0\n  type: minas\n'
        '  profile_velocity: 18000\n  profile_acceleration: 180000\n'
        '  profile_deceleration: 180000\n',
    )
    operation = repository.begin_motor_operation(
        'motor_apply',
        'preparing',
        timeout_sec=45.0,
    )
    repository.prepare_runtime_motor_config(project_id)
    repository.mark_runtime_motor_config_applied(project_id)

    restored = repository.motor_operation_status()

    assert restored['operation_id'] == operation['operation_id']
    assert restored['status'] == 'running'
    assert repository.motor_runtime_state()['target_project_id'] == project_id


def test_runtime_target_rollback_preserves_the_failed_operation(tmp_path):
    repository = ProjectRepository(tmp_path / 'projects')

    def prepare_project(name, axis):
        project_id = repository.create_project(name)['project']['project_id']
        repository.save_file(
            project_id,
            'motor_axes',
            'motor_axes.yaml',
            'period: 1000000\nmasters:\n- id: 0\n  type: ethercat\n  slaves:\n'
            f'  - controller_index: {axis}\n    driver_id: {axis}\ndrivers:\n'
            f'- id: {axis}\n  type: minas\n  profile_velocity: 18000\n'
            '  profile_acceleration: 180000\n  profile_deceleration: 180000\n',
        )
        repository.prepare_runtime_motor_config(project_id)
        return project_id

    previous_id = prepare_project('previous runtime', 0)
    repository.mark_runtime_motor_config_applied(previous_id)
    previous = repository.motor_runtime_target_snapshot()
    next_id = prepare_project('next runtime', 1)
    operation = repository.begin_motor_operation(
        'motor_apply',
        'preparing',
        timeout_sec=45.0,
        details={'previous_runtime': previous},
    )
    repository.mark_runtime_motor_config_applied(next_id)
    repository.finish_motor_operation(
        operation['operation_id'],
        'failure',
        phase='failed',
        error='restart failed',
    )

    repository.restore_motor_runtime_target(previous)

    state = repository.motor_runtime_state()
    assert state['target_project_id'] == previous_id
    assert state['valid'] is True
    assert repository.motor_operation_status()['status'] == 'failure'


def test_same_project_reapply_keeps_previous_runtime_session_for_rollback(tmp_path):
    repository = ProjectRepository(tmp_path / 'projects')
    project_id = repository.create_project('same project rollback')['project']['project_id']
    repository.save_file(
        project_id,
        'motor_axes',
        'motor_axes.yaml',
        'period: 1000000\nmasters:\n- id: 0\n  type: ethercat\n  slaves:\n'
        '  - controller_index: 0\n    driver_id: 0\ndrivers:\n- id: 0\n'
        '  type: minas\n  profile_velocity: 10\n'
        '  profile_acceleration: 20\n  profile_deceleration: 20\n',
    )
    config_file = repository.export_path(
        project_id, 'motor_axes', 'motor_axes.yaml'
    )
    repository.prepare_runtime_motor_config(project_id)
    repository.mark_runtime_motor_config_applied(project_id)
    previous = repository.motor_runtime_target_snapshot()
    previous_file = Path(repository.motor_runtime_state()['config_file'])
    previous_content = previous_file.read_bytes()

    config_file.write_text(
        config_file.read_text(encoding='utf-8').replace(
            'profile_velocity: 10', 'profile_velocity: 30'
        ),
        encoding='utf-8',
    )
    repository.prepare_runtime_motor_config(project_id)
    repository.mark_runtime_motor_config_applied(project_id)
    next_file = Path(repository.motor_runtime_state()['config_file'])

    assert next_file != previous_file
    assert previous_file.read_bytes() == previous_content

    repository.restore_motor_runtime_target(previous)
    restored = repository.motor_runtime_state()
    assert restored['valid'] is True
    assert Path(restored['config_file']) == previous_file
    assert Path(restored['config_file']).read_bytes() == previous_content


def test_timed_out_motor_apply_restores_previous_target_and_requests_restart(
    tmp_path, monkeypatch
):
    root = tmp_path / 'projects'
    repository = ProjectRepository(root)

    def prepare_project(name, axis):
        project_id = repository.create_project(name)['project']['project_id']
        repository.save_file(
            project_id,
            'motor_axes',
            'motor_axes.yaml',
            'period: 1000000\nmasters:\n- id: 0\n  type: ethercat\n  slaves:\n'
            f'  - controller_index: {axis}\n    driver_id: {axis}\ndrivers:\n'
            f'- id: {axis}\n  type: minas\n  profile_velocity: 18000\n'
            '  profile_acceleration: 180000\n  profile_deceleration: 180000\n',
        )
        repository.prepare_runtime_motor_config(project_id)
        return project_id

    previous_id = prepare_project('previous', 0)
    repository.mark_runtime_motor_config_applied(previous_id)
    previous = repository.motor_runtime_target_snapshot()
    next_id = prepare_project('next', 1)
    operation = repository.begin_motor_operation(
        'motor_apply',
        'restart_requested',
        timeout_sec=1.0,
        details={'previous_runtime': previous},
    )
    repository.mark_runtime_motor_config_applied(next_id)
    runtime_file = root / '.motor_runtime.json'
    runtime_payload = json.loads(runtime_file.read_text(encoding='utf-8'))
    runtime_payload['operation']['deadline_at'] = time.time() - 1.0
    runtime_file.write_text(json.dumps(runtime_payload), encoding='utf-8')

    bridge = MotionWebBridge.__new__(MotionWebBridge)
    bridge.project_repository = repository
    bridge._bridge_started_at = operation['started_at'] + 1.0
    scheduled = []
    bridge._schedule_managed_service_restart = (
        lambda *services: scheduled.append(services)
    )
    monkeypatch.setenv('MOTION_CONTROL_SERVICE_UNIT', 'motion-control.service')
    monkeypatch.setenv('MOTION_MOTOR_SERVICE_UNIT', 'motion-motor.service')

    result = bridge._reconcile_motor_operation_status({}, {}, {})
    repeated = bridge._reconcile_motor_operation_status({}, {}, {})

    assert result['status'] == 'timeout'
    assert result['phase'] == 'rollback_requested'
    assert repeated['phase'] == 'rollback_requested'
    assert repository.motor_runtime_state()['target_project_id'] == previous_id
    assert scheduled == [
        ('motion-motor.service', 'motion-control.service'),
    ]


def test_service_entrypoint_never_loads_runtime_from_another_project(tmp_path):
    workspace = tmp_path / 'workspace'
    projects = workspace / 'motion_projects'
    selected_id = 'selected-project'
    previous_id = 'previous-project'
    runtime = projects / previous_id / 'runtime' / 'applied_motor_config.yaml'
    runtime.parent.mkdir(parents=True)
    runtime.write_text('masters: []\n', encoding='utf-8')
    (projects / '.selected_project.json').write_text(
        json.dumps({
            'project_id': selected_id,
            'applied_project_id': previous_id,
        }),
        encoding='utf-8',
    )

    assert resolve_applied_motor_config(workspace) is None


def test_service_entrypoint_loads_independent_motor_runtime_after_project_switch(
    tmp_path,
):
    workspace = tmp_path / 'workspace'
    projects = workspace / 'motion_projects'
    runtime = projects / 'runtime-project' / 'runtime' / 'applied_motor_config.yaml'
    runtime.parent.mkdir(parents=True)
    content = b'masters: []\n'
    runtime.write_bytes(content)
    (projects / '.selected_project.json').write_text(
        json.dumps({'project_id': 'editor-project'}),
        encoding='utf-8',
    )
    (projects / '.motor_runtime.json').write_text(
        json.dumps({
            'version': 1,
            'target_project_id': 'runtime-project',
            'config_sha256': hashlib.sha256(content).hexdigest(),
        }),
        encoding='utf-8',
    )

    assert resolve_applied_motor_config(workspace) == runtime


def test_service_entrypoint_rejects_motor_runtime_sha_mismatch(tmp_path):
    workspace = tmp_path / 'workspace'
    projects = workspace / 'motion_projects'
    runtime = projects / 'runtime-project' / 'runtime' / 'applied_motor_config.yaml'
    runtime.parent.mkdir(parents=True)
    runtime.write_text('masters: []\n', encoding='utf-8')
    (projects / '.motor_runtime.json').write_text(
        json.dumps({
            'version': 1,
            'target_project_id': 'runtime-project',
            'config_sha256': '0' * 64,
        }),
        encoding='utf-8',
    )

    assert resolve_applied_motor_config(workspace) is None


def test_service_entrypoint_loads_immutable_runtime_session(tmp_path):
    workspace = tmp_path / 'workspace'
    projects = workspace / 'motion_projects'
    session = (
        projects / 'runtime-project' / 'runtime' / 'sessions'
        / 'motor-abc123.yaml'
    )
    session.parent.mkdir(parents=True)
    content = b'masters: []\n'
    session.write_bytes(content)
    (projects / '.motor_runtime.json').write_text(
        json.dumps({
            'version': 1,
            'target_project_id': 'runtime-project',
            'config_relpath': 'runtime/sessions/motor-abc123.yaml',
            'config_sha256': hashlib.sha256(content).hexdigest(),
        }),
        encoding='utf-8',
    )

    assert resolve_applied_motor_config(workspace) == session


def test_service_entrypoint_rejects_runtime_session_outside_project(tmp_path):
    workspace = tmp_path / 'workspace'
    projects = workspace / 'motion_projects'
    project = projects / 'runtime-project'
    project.mkdir(parents=True)
    outside = projects / 'outside.yaml'
    content = b'masters: []\n'
    outside.write_bytes(content)
    (projects / '.motor_runtime.json').write_text(
        json.dumps({
            'version': 1,
            'target_project_id': 'runtime-project',
            'config_relpath': '../outside.yaml',
            'config_sha256': hashlib.sha256(content).hexdigest(),
        }),
        encoding='utf-8',
    )

    assert resolve_applied_motor_config(workspace) is None


def test_service_entrypoint_restores_persisted_project_generation(tmp_path):
    workspace = tmp_path / 'workspace'
    projects = workspace / 'motion_projects'
    projects.mkdir(parents=True)
    (projects / '.selected_project.json').write_text(
        json.dumps({'project_id': 'current-project', 'project_generation': 17}),
        encoding='utf-8',
    )

    assert resolve_project_generation(workspace) == 17


def test_service_entrypoint_rejects_invalid_project_generation(tmp_path):
    workspace = tmp_path / 'workspace'
    projects = workspace / 'motion_projects'
    projects.mkdir(parents=True)
    (projects / '.selected_project.json').write_text(
        json.dumps({'project_id': 'current-project', 'project_generation': -1}),
        encoding='utf-8',
    )

    assert resolve_project_generation(workspace) == 0


def test_upper_service_entrypoint_never_starts_embedded_motor_manager(
    tmp_path, monkeypatch
):
    workspace = tmp_path / 'workspace'
    restart_script = workspace / 'scripts' / 'restart_motion_monitor.sh'
    restart_script.parent.mkdir(parents=True)
    restart_script.write_text('#!/bin/bash\n', encoding='utf-8')
    projects = workspace / 'motion_projects'
    runtime = projects / 'current' / 'runtime' / 'applied_motor_config.yaml'
    runtime.parent.mkdir(parents=True)
    runtime.write_text('masters: []\n', encoding='utf-8')
    (projects / '.selected_project.json').write_text(
        json.dumps({
            'project_id': 'current',
            'applied_project_id': 'current',
            'project_generation': 7,
        }),
        encoding='utf-8',
    )
    captured = {}
    monkeypatch.setenv('MOTION_WORKSPACE', str(workspace))
    monkeypatch.setattr(
        service_entrypoint.os,
        'execvpe',
        lambda executable, arguments, environment: captured.update({
            'executable': executable,
            'arguments': arguments,
            'environment': environment,
        }),
    )

    service_entrypoint.main()

    assert captured['environment']['START_MOTOR_MANAGER'] == 'false'
    assert captured['environment']['MOTOR_CONFIG_FILE'] == str(runtime)
    assert captured['environment']['MOTION_PROJECT_GENERATION'] == '7'


def test_motor_service_entrypoint_starts_only_applied_motor_runtime(
    tmp_path, monkeypatch
):
    workspace = tmp_path / 'workspace'
    runner = (
        workspace / 'src' / 'motion_web' / 'web_bridge'
        / 'deploy' / 'run_motor_service.sh'
    )
    runner.parent.mkdir(parents=True)
    runner.write_text('#!/bin/bash\n', encoding='utf-8')
    projects = workspace / 'motion_projects'
    runtime = projects / 'current' / 'runtime' / 'applied_motor_config.yaml'
    runtime.parent.mkdir(parents=True)
    runtime.write_text('masters: []\n', encoding='utf-8')
    (projects / '.selected_project.json').write_text(
        json.dumps({
            'project_id': 'current',
            'applied_project_id': 'current',
        }),
        encoding='utf-8',
    )
    captured = {}
    monkeypatch.setenv('MOTION_WORKSPACE', str(workspace))
    monkeypatch.setattr(
        service_entrypoint.os,
        'execvpe',
        lambda executable, arguments, environment: captured.update({
            'executable': executable,
            'arguments': arguments,
            'environment': environment,
        }),
    )

    service_entrypoint.motor_main()

    assert captured['arguments'] == ['/bin/bash', str(runner)]
    assert captured['environment']['MOTOR_CONFIG_FILE'] == str(runtime)


def test_motor_service_entrypoint_fails_configuration_without_runtime(
    tmp_path, monkeypatch
):
    workspace = tmp_path / 'workspace'
    (workspace / 'motion_projects').mkdir(parents=True)
    monkeypatch.setenv('MOTION_WORKSPACE', str(workspace))

    with pytest.raises(SystemExit) as failure:
        service_entrypoint.motor_main()

    assert failure.value.code == service_entrypoint.MOTOR_CONFIG_ERROR_EXIT


def test_motor_service_print_config_is_safe_without_runtime(
    tmp_path, monkeypatch, capsys
):
    workspace = tmp_path / 'workspace'
    (workspace / 'motion_projects').mkdir(parents=True)
    monkeypatch.setenv('MOTION_WORKSPACE', str(workspace))
    monkeypatch.setattr(service_entrypoint.sys, 'argv', ['motion_motor_service', '--print-config'])

    service_entrypoint.motor_main()

    assert capsys.readouterr().out == '\n'


def test_service_installer_validates_motor_runtime_before_stopping_services():
    installer = (
        Path(__file__).resolve().parents[1]
        / 'deploy'
        / 'install_user_service.sh'
    ).read_text(encoding='utf-8')

    preflight = 'MOTOR_CONFIG="$("${MOTOR_SERVICE_EXECUTABLE}" --print-config)"'
    render_control = (
        '"${CONTROL_TEMPLATE}" > "${INSTALL_TMP}/motion-control.service"'
    )
    stop_motor = 'systemctl --user stop motion-motor.service'
    assert installer.index(preflight) < installer.index(stop_motor)
    assert installer.index(render_control) < installer.index(stop_motor)
    assert installer.index('SERVICES_STOPPED=true') < installer.index(stop_motor)
    assert (
        'systemctl --user is-active --quiet motion-motor.service'
        in installer
    )
    assert 'restore_previous_install_on_error' in installer
    assert 'motion-control.service.previous' in installer
    assert 'motion-motor.service.previous' in installer
    assert 'exit 78' in installer


def test_managed_service_restores_last_applied_config_after_project_edit(tmp_path):
    workspace = tmp_path / 'workspace'
    repository = ProjectRepository(workspace / 'motion_projects')
    project_id = repository.create_project('runtime')['project']['project_id']
    repository.save_file(
        project_id, 'motor_axes', 'motor_axes.yaml',
        'period: 1000000\nmasters:\n- id: 0\n  type: ethercat\n  slaves:\n'
        '  - controller_index: 0\n    driver_id: 0\ndrivers:\n- id: 0\n'
        '  type: minas\n  profile_velocity: 18000\n'
        '  profile_acceleration: 180000\n  profile_deceleration: 180000\n',
    )
    source = repository.export_path(project_id, 'motor_axes', 'motor_axes.yaml')
    repository.prepare_runtime_motor_config(project_id)
    repository.mark_runtime_motor_config_applied(project_id)
    assert resolve_applied_motor_config(workspace) is not None

    source.write_text(source.read_text(encoding='utf-8') + '# pending edit\n', encoding='utf-8')

    assert resolve_applied_motor_config(workspace) is not None


def test_runtime_status_reports_disabled_motor_manager_without_runtime_config(
    tmp_path, monkeypatch
):
    bridge = MotionWebBridge.__new__(MotionWebBridge)
    bridge.workspace_root = tmp_path
    monkeypatch.delenv('MOTOR_CONFIG_FILE', raising=False)

    status = bridge._runtime_service_status({'generated_at': 100.0, 'motors': []})

    assert status['phase'] == 'motor_manager_disabled'
    assert status['motor_manager_expected'] is False
    assert status['runtime_config_file'].endswith('config/bootstrap_motor_config.yaml')


def test_runtime_status_reports_ready_motor_feedback(tmp_path, monkeypatch):
    bridge = MotionWebBridge.__new__(MotionWebBridge)
    bridge.workspace_root = tmp_path
    runtime = tmp_path / 'runtime.yaml'
    runtime.write_text('masters: []\n', encoding='utf-8')
    bridge.applied_motor_config_file = runtime
    bridge.project_repository = type('Repository', (), {
        'motor_runtime_state': lambda _self: {
            'valid': True,
            'config_file': str(runtime),
        },
    })()

    status = bridge._runtime_service_status({
        'generated_at': 100.0,
        'last_motor_status_at': 99.8,
        'motors': [{'controller_index': 0}],
    })

    assert status['phase'] == 'ready'
    assert status['motor_manager_expected'] is True
    assert status['motor_count'] == 1


def test_runtime_status_rejects_process_and_target_config_mismatch(tmp_path):
    bridge = MotionWebBridge.__new__(MotionWebBridge)
    bridge.workspace_root = tmp_path
    running = tmp_path / 'project-a' / 'runtime' / 'applied_motor_config.yaml'
    target = tmp_path / 'project-b' / 'runtime' / 'applied_motor_config.yaml'
    running.parent.mkdir(parents=True)
    target.parent.mkdir(parents=True)
    running.write_text('masters: []\n', encoding='utf-8')
    target.write_text('masters: []\n', encoding='utf-8')
    bridge.applied_motor_config_file = running
    bridge.project_repository = type('Repository', (), {
        'motor_runtime_state': lambda _self: {
            'valid': True,
            'config_file': str(target),
        },
    })()

    status = bridge._runtime_service_status({
        'generated_at': 100.0,
        'last_motor_status_at': 99.9,
        'motors': [{'controller_index': 0}],
    })

    assert status['phase'] == 'runtime_config_mismatch'
    assert status['motor_manager_expected'] is False
    assert status['runtime_target_matches_process'] is False


def test_restarted_bridge_completes_persisted_motor_apply_operation(tmp_path):
    repository = ProjectRepository(tmp_path / 'projects')
    runtime = tmp_path / 'runtime.yaml'
    runtime.write_text('masters: []\n', encoding='utf-8')
    operation = repository.begin_motor_operation(
        'motor_apply',
        'restart_requested',
        timeout_sec=45.0,
        details={
            'runtime_file': str(runtime),
            'expected_axes': [0],
        },
    )
    bridge = MotionWebBridge.__new__(MotionWebBridge)
    bridge.project_repository = repository
    bridge._bridge_started_at = operation['started_at'] + 1.0
    result = bridge._reconcile_motor_operation_status(
        {
            'phase': 'ready',
            'runtime_config_file': str(runtime),
            'runtime_target_matches_process': True,
        },
        {
            'last_motor_status_at': operation['started_at'] + 2.0,
            'motors': [{
                'controller_index': 0,
                'connection_state': 'online',
                'connection_connected': True,
                'fault': False,
            }],
        },
        {'ready': True},
    )

    assert result['status'] == 'success'
    assert result['phase'] == 'completed'
    assert ProjectRepository(tmp_path / 'projects').motor_operation_status()['status'] == 'success'


def test_motor_apply_completes_without_motion_axis_execution_context(tmp_path):
    repository = ProjectRepository(tmp_path / 'projects')
    runtime = tmp_path / 'runtime.yaml'
    runtime.write_text('masters: []\n', encoding='utf-8')
    operation = repository.begin_motor_operation(
        'motor_apply',
        'restart_requested',
        timeout_sec=45.0,
        details={
            'runtime_file': str(runtime),
            'expected_axes': [0],
        },
    )
    bridge = MotionWebBridge.__new__(MotionWebBridge)
    bridge.project_repository = repository
    bridge._bridge_started_at = operation['started_at'] + 1.0

    result = bridge._reconcile_motor_operation_status(
        {
            'phase': 'ready',
            'runtime_config_file': str(runtime),
            'runtime_target_matches_process': True,
        },
        {
            'last_motor_status_at': operation['started_at'] + 2.0,
            'motors': [{
                'controller_index': 0,
                'connection_state': 'online',
                'connection_connected': True,
                'fault': False,
            }],
        },
        {
            'ready': False,
            'state': 'configuration_required',
            'missing': ['motion_axis_matching'],
        },
    )

    assert result['status'] == 'success'
    assert result['phase'] == 'completed'


def test_motor_restart_success_uses_terminal_completed_phase(tmp_path):
    repository = ProjectRepository(tmp_path / 'projects')
    runtime = tmp_path / 'runtime.yaml'
    runtime.write_text('masters: []\n', encoding='utf-8')
    operation = repository.begin_motor_operation(
        'motor_restart',
        'restart_requested',
        timeout_sec=45.0,
        details={
            'runtime_file': str(runtime),
            'expected_axes': [0],
        },
    )
    operation = repository.update_motor_operation(
        operation['operation_id'],
        'verifying',
        details={'restart_observed_at': operation['started_at'] + 1.0},
    )
    bridge = MotionWebBridge.__new__(MotionWebBridge)
    bridge.project_repository = repository
    bridge._bridge_started_at = operation['started_at'] - 1.0
    result = bridge._reconcile_motor_operation_status(
        {
            'phase': 'ready',
            'runtime_config_file': str(runtime),
            'runtime_target_matches_process': True,
        },
        {
            'last_motor_status_at': operation['started_at'] + 2.0,
            'motors': [{
                'controller_index': 0,
                'connection_state': 'online',
                'connection_connected': True,
                'fault': False,
            }],
        },
        {'ready': True},
    )

    assert result['status'] == 'success'
    assert result['phase'] == 'completed'


def test_motor_restart_does_not_complete_before_service_restart_is_observed(
    tmp_path,
):
    repository = ProjectRepository(tmp_path / 'projects')
    runtime = tmp_path / 'runtime.yaml'
    runtime.write_text('masters: []\n', encoding='utf-8')
    operation = repository.begin_motor_operation(
        'motor_restart',
        'restart_requested',
        timeout_sec=45.0,
        details={
            'runtime_file': str(runtime),
            'expected_axes': [0],
            'service_main_pid_before': 100,
            'service_invocation_id_before': 'before',
        },
    )
    bridge = MotionWebBridge.__new__(MotionWebBridge)
    bridge.project_repository = repository
    bridge._bridge_started_at = operation['started_at'] - 1.0

    result = bridge._reconcile_motor_operation_status(
        {
            'phase': 'ready',
            'runtime_config_file': str(runtime),
            'runtime_target_matches_process': True,
        },
        {
            'last_motor_status_at': operation['started_at'] + 10.0,
            'motors': [{
                'controller_index': 0,
                'connection_state': 'online',
                'connection_connected': True,
                'fault': False,
            }],
        },
        {'ready': True},
    )

    assert result['status'] == 'running'
    assert result['phase'] == 'restart_requested'


def test_motor_restart_waits_for_every_configured_axis_to_be_online(tmp_path):
    repository = ProjectRepository(tmp_path / 'projects')
    runtime = tmp_path / 'runtime.yaml'
    runtime.write_text('masters: []\n', encoding='utf-8')
    operation = repository.begin_motor_operation(
        'motor_restart',
        'restart_requested',
        timeout_sec=45.0,
        details={
            'runtime_file': str(runtime),
            'expected_axes': [0, 1],
        },
    )
    operation = repository.update_motor_operation(
        operation['operation_id'],
        'verifying',
        details={'restart_observed_at': operation['started_at'] + 1.0},
    )
    bridge = MotionWebBridge.__new__(MotionWebBridge)
    bridge.project_repository = repository
    bridge._bridge_started_at = operation['started_at'] - 1.0
    result = bridge._reconcile_motor_operation_status(
        {
            'phase': 'ready',
            'runtime_config_file': str(runtime),
            'runtime_target_matches_process': True,
        },
        {
            'last_motor_status_at': operation['started_at'] + 2.0,
            'motors': [
                {
                    'controller_index': 0,
                    'connection_state': 'online',
                    'connection_connected': True,
                    'fault': False,
                },
                {
                    'controller_index': 1,
                    'connection_state': 'offline',
                    'connection_connected': False,
                    'fault': False,
                },
            ],
        },
        {'ready': True},
    )

    assert result['status'] == 'running'
    assert repository.motor_operation_status()['status'] == 'running'


def test_motor_restart_fails_when_motor_manager_uses_another_config(tmp_path):
    repository = ProjectRepository(tmp_path / 'projects')
    expected = tmp_path / 'expected.yaml'
    actual = tmp_path / 'actual.yaml'
    expected.write_text('masters: []\n', encoding='utf-8')
    actual.write_text('masters: []\n', encoding='utf-8')
    operation = repository.begin_motor_operation(
        'motor_restart',
        'restart_requested',
        timeout_sec=45.0,
        details={
            'runtime_file': str(expected),
            'expected_axes': [0],
        },
    )
    operation = repository.update_motor_operation(
        operation['operation_id'],
        'verifying',
        details={'restart_observed_at': operation['started_at'] + 1.0},
    )
    bridge = MotionWebBridge.__new__(MotionWebBridge)
    bridge.project_repository = repository
    bridge._bridge_started_at = operation['started_at'] - 1.0
    result = bridge._reconcile_motor_operation_status(
        {
            'phase': 'ready',
            'runtime_config_file': str(actual),
            'runtime_target_matches_process': False,
        },
        {
            'last_motor_status_at': operation['started_at'] + 2.0,
            'motors': [{
                'controller_index': 0,
                'connection_state': 'online',
                'connection_connected': True,
                'fault': False,
            }],
        },
        {'ready': True},
    )

    assert result['status'] == 'failure'
    assert result['phase'] == 'failed'
    assert '실행 설정 불일치' in result['error']


def test_restarted_bridge_schedules_interrupted_ac_servo_scan_recovery(tmp_path):
    repository = ProjectRepository(tmp_path / 'projects')
    operation = repository.begin_motor_operation(
        'ac_servo_scan',
        'preparing',
        timeout_sec=30.0,
    )
    repository.update_motor_operation(
        operation['operation_id'],
        'scanning',
        details={
            'motor_service_was_active': True,
            'expected_axes': [0, 1],
        },
    )
    bridge = MotionWebBridge.__new__(MotionWebBridge)
    bridge.project_repository = repository
    bridge._bridge_started_at = operation['started_at'] + 1.0
    scheduled = []

    def schedule(payload):
        scheduled.append(dict(payload))
        return dict(payload)

    bridge._schedule_interrupted_scan_recovery = schedule

    result = bridge._reconcile_motor_operation_status(
        {'phase': 'waiting_motor_feedback'},
        {},
        {'ready': False},
    )

    assert result['status'] == 'running'
    assert scheduled[0]['operation_id'] == operation['operation_id']


def test_active_ac_servo_scan_is_not_reconciled_as_motor_restart(tmp_path):
    repository = ProjectRepository(tmp_path / 'projects')
    operation = repository.begin_motor_operation(
        'ac_servo_scan',
        'stopping_runtime',
        timeout_sec=30.0,
        details={
            'motor_service_was_active': True,
            'expected_axes': [0],
        },
    )
    bridge = MotionWebBridge.__new__(MotionWebBridge)
    bridge.project_repository = repository
    bridge._bridge_started_at = operation['started_at'] - 1.0
    result = bridge._reconcile_motor_operation_status(
        {'phase': 'ready'},
        {
            'last_motor_status_at': operation['started_at'] + 1.0,
            'motors': [],
        },
        {'ready': True},
    )

    assert result['status'] == 'running'
    assert result['phase'] == 'stopping_runtime'


def test_interrupted_ac_servo_scan_restores_motor_service_and_records_failure(
    tmp_path
):
    repository = ProjectRepository(tmp_path / 'projects')
    operation = repository.begin_motor_operation(
        'ac_servo_scan',
        'scanning',
        timeout_sec=30.0,
        details={
            'motor_service_was_active': True,
            'expected_axes': [0],
        },
    )
    bridge = MotionWebBridge.__new__(MotionWebBridge)
    bridge.project_repository = repository
    actions = []
    bridge._run_managed_user_service = (
        lambda action, service: actions.append((action, service))
    )
    bridge._wait_for_motor_runtime_recovery = lambda *_args, **_kwargs: {
        'required': True,
        'expected_axes': [0],
        'online_axes': [0],
        'recovered': True,
        'service_active': True,
    }

    bridge._recover_interrupted_scan(operation)

    completed = repository.motor_operation_status()
    assert actions == [('start', 'motion-motor.service')]
    assert completed['status'] == 'failure'
    assert completed['phase'] == 'interrupted_recovered'
    assert '검색 결과를 확인할 수 없습니다' in completed['error']


def test_runtime_status_reports_ethercat_start_block_instead_of_waiting_forever(
    tmp_path, monkeypatch
):
    bridge = MotionWebBridge.__new__(MotionWebBridge)
    bridge.workspace_root = tmp_path
    monkeypatch.setenv('MOTOR_CONFIG_FILE', str(tmp_path / 'runtime.yaml'))
    monkeypatch.setenv(
        'MOTOR_START_BLOCK_REASON',
        'EtherCAT 오류 플래그를 해제하지 못해 모터 관리 노드 시작을 차단했습니다',
    )
    monkeypatch.setenv('ROS_LOCALHOST_ONLY', '1')

    status = bridge._runtime_service_status({'generated_at': 100.0, 'motors': []})

    assert status['phase'] == 'motor_manager_start_blocked'
    assert status['motor_manager_expected'] is False
    assert status['ros_localhost_only'] is True
    assert 'EtherCAT 오류' in status['message']


def test_web_apply_requests_managed_service_restart_without_second_launch(
    tmp_path, monkeypatch
):
    workspace = tmp_path / 'workspace'
    repository = ProjectRepository(workspace / 'motion_projects')
    project_id = repository.create_project('managed')['project']['project_id']
    repository.save_file(
        project_id,
        'motor_axes',
        'motor_axes.yaml',
        'period: 1000000\nmasters:\n- id: 0\n  type: ethercat\n  slaves:\n'
        '  - controller_index: 0\n    driver_id: 0\ndrivers:\n- id: 0\n  type: minas\n'
        '  profile_velocity: 18000\n  profile_acceleration: 180000\n'
        '  profile_deceleration: 180000\n',
    )
    restart_script = workspace / 'scripts' / 'restart_motion_monitor.sh'
    restart_script.parent.mkdir(parents=True)
    restart_script.write_text('#!/bin/bash\n', encoding='utf-8')

    bridge = MotionWebBridge.__new__(MotionWebBridge)
    bridge.project_repository = repository
    bridge.restart_script = restart_script
    bridge.workspace_root = workspace
    bridge.snapshot = lambda: {}
    commands = []
    monkeypatch.setenv('MOTION_CONTROL_SERVICE_UNIT', 'motion-control.service')
    monkeypatch.setenv('MOTION_MOTOR_SERVICE_UNIT', 'motion-motor.service')
    monkeypatch.setattr(
        'motion_web_bridge.bridge_node.subprocess.Popen',
        lambda command, **kwargs: commands.append((command, kwargs)),
    )

    result = bridge.apply_motor_config()

    assert result['success'] is True
    assert result['restart_mode'] == 'split_managed_services'
    assert commands[0][0][:4] == [
        '/bin/bash', '-c', 'sleep 0.5; exec "$@"',
        'motion-control-delayed-restart',
    ]
    assert commands[0][0][4:] == [
        '/usr/bin/systemctl', '--user', 'restart', '--no-block',
        'motion-motor.service', 'motion-control.service',
    ]
    assert commands[0][1]['start_new_session'] is True
    assert repository.applied_runtime_motor_config().is_file()


def test_web_apply_schedule_failure_restores_previous_runtime(
    tmp_path, monkeypatch
):
    workspace = tmp_path / 'workspace'
    repository = ProjectRepository(workspace / 'motion_projects')

    def prepare_project(name, axis):
        project_id = repository.create_project(name)['project']['project_id']
        repository.save_file(
            project_id,
            'motor_axes',
            'motor_axes.yaml',
            'period: 1000000\nmasters:\n- id: 0\n  type: ethercat\n  slaves:\n'
            f'  - controller_index: {axis}\n    driver_id: {axis}\ndrivers:\n'
            f'- id: {axis}\n  type: minas\n  profile_velocity: 18000\n'
            '  profile_acceleration: 180000\n  profile_deceleration: 180000\n',
        )
        repository.prepare_runtime_motor_config(project_id)
        return project_id

    previous_id = prepare_project('previous', 0)
    repository.mark_runtime_motor_config_applied(previous_id)
    next_id = prepare_project('next', 1)
    restart_script = workspace / 'scripts' / 'restart_motion_monitor.sh'
    restart_script.parent.mkdir(parents=True)
    restart_script.write_text('#!/bin/bash\n', encoding='utf-8')
    bridge = MotionWebBridge.__new__(MotionWebBridge)
    bridge.project_repository = repository
    bridge.restart_script = restart_script
    bridge.workspace_root = workspace
    bridge.snapshot = lambda: {}
    monkeypatch.setenv('MOTION_CONTROL_SERVICE_UNIT', 'motion-control.service')
    monkeypatch.setenv('MOTION_MOTOR_SERVICE_UNIT', 'motion-motor.service')
    monkeypatch.setattr(
        'motion_web_bridge.bridge_node.subprocess.Popen',
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError('schedule failed')),
    )

    result = bridge.apply_motor_config()

    assert result['success'] is False
    assert repository.motor_runtime_state()['target_project_id'] == previous_id
    assert repository.motor_runtime_state()['target_project_id'] != next_id
    assert repository.motor_operation_status()['status'] == 'failure'


def test_user_can_request_managed_program_restart_from_web(monkeypatch):
    bridge = MotionWebBridge.__new__(MotionWebBridge)
    bridge._motion_run_lock = threading.Lock()
    bridge._motion_studio_lock = threading.Lock()
    bridge._motion_run_status = {'state': 'stopping'}
    bridge._motion_studio_status = {'state': 'stopping'}
    bridge.snapshot = lambda: {}
    commands = []
    monkeypatch.setenv('MOTION_CONTROL_SERVICE_UNIT', 'motion-control.service')
    monkeypatch.setenv('MOTION_MOTOR_SERVICE_UNIT', 'motion-motor.service')
    monkeypatch.setenv('MOTION_COORDINATION_SERVICE_UNIT', 'motion-coordination.service')
    monkeypatch.setattr(
        'motion_web_bridge.bridge_node.subprocess.Popen',
        lambda command, **kwargs: commands.append((command, kwargs)),
    )

    result = bridge.restart_managed_program()

    assert result['success'] is True
    assert commands[0][0][-2:] == [
        'motion-control.service',
        'motion-coordination.service',
    ]
    assert bridge._motion_run_status['state'] == 'stopped'
    assert bridge._motion_studio_status['state'] == 'idle'


def test_program_restart_button_requires_installed_service(monkeypatch):
    bridge = MotionWebBridge.__new__(MotionWebBridge)
    bridge.snapshot = lambda: {}
    monkeypatch.delenv('MOTION_CONTROL_SERVICE_UNIT', raising=False)

    result = bridge.restart_managed_program()

    assert result['success'] is False
    assert '최초 설치' in result['message']


def test_user_can_restart_only_motor_control_service_from_web(monkeypatch):
    bridge = MotionWebBridge.__new__(MotionWebBridge)
    bridge._motion_run_lock = threading.Lock()
    bridge._motion_studio_lock = threading.Lock()
    bridge._motion_run_status = {'state': 'idle'}
    bridge._motion_studio_status = {'state': 'idle'}
    bridge.snapshot = lambda: {}
    operation = {}

    class Repository:
        def selected_runtime_motor_config(self):
            return Path('/runtime/applied.yaml')

        def selected_project_id(self):
            return 'project-a'

        def begin_motor_operation(self, operation_type, phase, **kwargs):
            operation.update({
                'operation_id': 'restart-1',
                'type': operation_type,
                'phase': phase,
                'status': 'running',
                'details': dict(kwargs.get('details') or {}),
            })
            return dict(operation)

        def motor_operation_status(self):
            return dict(operation)

        def finish_motor_operation(self, *_args, **_kwargs):
            raise AssertionError('successful scheduling must remain pending verification')

    bridge.project_repository = Repository()
    bridge._configured_axes_from_runtime_file = lambda _runtime: [0]
    calls = []

    class Coordinator:
        def begin(self, *, project_id, runtime_file, expected_axes):
            calls.append((project_id, runtime_file, expected_axes))
            return bridge.project_repository.begin_motor_operation(
                'motor_restart',
                'restart_requested',
                timeout_sec=45.0,
                details={
                    'project_id': project_id,
                    'runtime_file': str(runtime_file),
                    'expected_axes': expected_axes,
                },
            )

    bridge.motor_restart_coordinator = Coordinator()
    monkeypatch.setenv('MOTION_MOTOR_SERVICE_UNIT', 'motion-motor.service')

    result = bridge.restart_motor_control_system()

    assert result['success'] is True
    assert result['restart_mode'] == 'motor_service'
    assert calls == [('project-a', Path('/runtime/applied.yaml'), [0])]


def test_motor_restart_worker_records_new_service_generation_before_verifying(
    tmp_path,
    monkeypatch,
):
    repository = ProjectRepository(tmp_path / 'projects')
    operation = repository.begin_motor_operation(
        'motor_restart',
        'restart_requested',
        timeout_sec=45.0,
        details={
            'runtime_file': '/runtime/applied.yaml',
            'expected_axes': [0],
            'service_main_pid_before': 100,
            'service_invocation_id_before': 'before',
        },
    )
    actions = []
    coordinator = MotorRestartCoordinator(
        repository,
        lambda *_args: {'ready': False, 'failed': False},
        restart_service=lambda service: actions.append(('restart', service)),
        service_identity=lambda _service: {
            'active_state': 'active',
            'sub_state': 'running',
            'main_pid': 200,
            'invocation_id': 'after',
            'started_monotonic': 2000,
        },
        sleep=lambda _seconds: None,
    )

    coordinator._restart_worker(
        operation['operation_id'],
        {
            'active_state': 'active',
            'main_pid': 100,
            'invocation_id': 'before',
            'started_monotonic': 1000,
        },
    )

    status = repository.motor_operation_status()
    assert actions == [('restart', 'motion-motor.service')]
    assert status['status'] == 'running'
    assert status['phase'] == 'verifying'
    assert status['details']['service_main_pid_after'] == 200
    assert status['details']['service_invocation_id_after'] == 'after'
    assert status['details']['restart_observed_at'] > 0


def test_motor_control_restart_rejects_project_without_applied_motor_config(monkeypatch):
    bridge = MotionWebBridge.__new__(MotionWebBridge)
    bridge._motion_run_lock = threading.Lock()
    bridge._motion_studio_lock = threading.Lock()
    bridge._motion_run_status = {'state': 'stopping'}
    bridge._motion_studio_status = {'state': 'stopping'}
    bridge.snapshot = lambda: {}
    bridge.project_repository = type(
        'Repository',
        (),
        {'selected_runtime_motor_config': lambda _self: None},
    )()
    commands = []
    monkeypatch.setenv('MOTION_MOTOR_SERVICE_UNIT', 'motion-motor.service')
    monkeypatch.setattr(
        'motion_web_bridge.bridge_node.subprocess.Popen',
        lambda command, **kwargs: commands.append((command, kwargs)),
    )

    result = bridge.restart_motor_control_system()

    assert result['success'] is False
    assert '설정 적용·재시작' in result['message']
    assert commands == []
    assert bridge._motion_run_status['state'] == 'stopped'
    assert bridge._motion_studio_status['state'] == 'idle'


@pytest.mark.parametrize(
    ('run_state', 'studio_state'),
    [('running', 'idle'), ('waiting', 'idle'), ('idle', 'recording')],
)
def test_project_change_is_blocked_during_motion_operations(run_state, studio_state):
    bridge = MotionWebBridge.__new__(MotionWebBridge)
    bridge._motion_run_lock = threading.Lock()
    bridge._motion_studio_lock = threading.Lock()
    bridge._motion_run_status = {'state': run_state}
    bridge._motion_studio_status = {'state': studio_state}

    with pytest.raises(ValueError, match='프로젝트를 변경할 수 없습니다'):
        bridge._ensure_project_change_allowed()


def test_project_change_is_blocked_during_motor_lifecycle_operation():
    bridge = MotionWebBridge.__new__(MotionWebBridge)
    bridge._motion_run_lock = threading.Lock()
    bridge._motion_studio_lock = threading.Lock()
    bridge._motion_run_status = {'state': 'idle'}
    bridge._motion_studio_status = {'state': 'idle'}
    bridge._motor_lifecycle_lock = threading.Lock()
    bridge._motor_lifecycle_lock.acquire()

    with pytest.raises(ValueError, match='모터 설정·검색·재시작 작업'):
        bridge._ensure_project_change_allowed()


def test_project_change_is_blocked_by_persisted_motor_operation():
    bridge = MotionWebBridge.__new__(MotionWebBridge)
    bridge._motion_run_lock = threading.Lock()
    bridge._motion_studio_lock = threading.Lock()
    bridge._motion_run_status = {'state': 'idle'}
    bridge._motion_studio_status = {'state': 'idle'}
    bridge._motor_lifecycle_lock = threading.Lock()
    bridge.project_repository = type('Repository', (), {
        'motor_operation_status': lambda _self: {
            'operation_id': 'apply-1',
            'status': 'running',
            'type': 'motor_apply',
        },
    })()

    with pytest.raises(ValueError, match='모터 설정·검색·재시작 작업'):
        bridge._ensure_project_change_allowed()


def test_copy_file_between_projects_is_a_physical_independent_copy(tmp_path):
    repository = ProjectRepository(tmp_path / 'projects')
    source_id = repository.create_project('source')['project']['project_id']
    target_id = repository.create_project('target')['project']['project_id']
    repository.import_text(source_id, 'motions', 'wave.json', MOTION_TEXT)

    result = repository.copy_file_from_project(
        target_id, source_id, 'motions', 'wave.json'
    )
    copied = tmp_path / 'projects' / target_id / 'motions' / 'wave.json'
    assert copied.is_file()
    assert result['copied_file']['path'] == str(copied)

    changed = MOTION_TEXT.replace('[1, 0.0, "1-1", 0.0]', '[1, 0.0, "1-1", 10.0]')
    repository.save_file(source_id, 'motions', 'wave.json', changed)
    assert copied.read_text(encoding='utf-8') == MOTION_TEXT + '\n'


def test_delete_project_rejects_the_active_motor_runtime_owner(tmp_path):
    repository = ProjectRepository(tmp_path / 'projects')
    project_id = repository.create_project('running')['project']['project_id']
    repository.save_file(
        project_id,
        'motor_axes',
        'motor_axes.yaml',
        'period: 1000000\nmasters:\n- id: 0\n  type: ethercat\n  slaves:\n'
        '  - controller_index: 0\n    driver_id: 0\ndrivers:\n- id: 0\n  type: minas\n'
        '  profile_velocity: 18000\n  profile_acceleration: 180000\n'
        '  profile_deceleration: 180000\n',
    )
    repository.prepare_runtime_motor_config(project_id)
    repository.mark_runtime_motor_config_applied(project_id)

    with pytest.raises(ValueError, match='모터 실행 설정이 사용하는 프로젝트'):
        repository.delete_project(project_id)

    assert (tmp_path / 'projects' / project_id).is_dir()
    assert repository.applied_runtime_motor_config() is not None


def test_clear_motor_runtime_target_allows_project_delete(tmp_path):
    repository = ProjectRepository(tmp_path / 'projects')
    project_id = repository.create_project('running')['project']['project_id']
    repository.save_file(
        project_id,
        'motor_axes',
        'motor_axes.yaml',
        'period: 1000000\nmasters:\n- id: 0\n  type: ethercat\n  slaves:\n'
        '  - controller_index: 0\n    driver_id: 0\ndrivers:\n- id: 0\n  type: minas\n'
        '  profile_velocity: 18000\n  profile_acceleration: 180000\n'
        '  profile_deceleration: 180000\n',
    )
    repository.prepare_runtime_motor_config(project_id)
    repository.mark_runtime_motor_config_applied(project_id)

    cleared = repository.clear_motor_runtime_target()

    assert cleared['cleared'] is True
    assert cleared['previous_project_id'] == project_id
    assert repository.motor_runtime_state().get('target_project_id') in ('', None)
    result = repository.delete_project(project_id)
    assert result['permanently_deleted'] is True
    assert not (tmp_path / 'projects' / project_id).exists()


def test_clear_motor_runtime_application_stops_and_allows_delete(
    tmp_path, monkeypatch
):
    workspace = tmp_path / 'workspace'
    repository = ProjectRepository(workspace / 'motion_projects')
    project_id = repository.create_project('running')['project']['project_id']
    repository.save_file(
        project_id,
        'motor_axes',
        'motor_axes.yaml',
        'period: 1000000\nmasters:\n- id: 0\n  type: ethercat\n  slaves:\n'
        '  - controller_index: 0\n    driver_id: 0\ndrivers:\n- id: 0\n  type: minas\n'
        '  profile_velocity: 18000\n  profile_acceleration: 180000\n'
        '  profile_deceleration: 180000\n',
    )
    repository.prepare_runtime_motor_config(project_id)
    runtime_file = repository.mark_runtime_motor_config_applied(project_id)

    bridge = MotionWebBridge.__new__(MotionWebBridge)
    bridge.project_repository = repository
    bridge.workspace_root = workspace
    bridge.motion_projects_dir = workspace / 'motion_projects'
    bridge.applied_motor_config_file = Path(runtime_file).resolve()
    bridge.motor_config_file = Path(runtime_file).resolve()
    bridge.snapshot = lambda: {}
    bridge.list_motion_projects = lambda: {
        'projects': [],
        'runtime_project_id': '',
        'selected_project_id': project_id,
    }
    bridge._motion_run_status = {'state': 'stopping'}
    bridge._motion_studio_status = {'state': 'stopping'}
    bridge._motion_run_lock = threading.Lock()
    bridge._motion_studio_lock = threading.Lock()
    bridge._coordination_execution_blocker = lambda: ''
    bridge._ethercat_scan_safety_blocker = lambda **_kwargs: ''
    bridge._managed_user_service_active = lambda _unit: True
    bridge._run_managed_user_service = lambda *_args, **_kwargs: None
    bridge._wait_for_ethercat_release = lambda *_args, **_kwargs: None
    bridge.motion_run_stop = lambda: None
    bridge.publish_safety_stop = lambda *_args, **_kwargs: None
    bridge._clear_motor_config_selection = lambda: None
    bridge._motor_lifecycle_lock = threading.Lock()
    bridge._execution_context_apply_lock = threading.Lock()
    bridge._project_generation = 1
    bridge._project_generation_lock = threading.Lock()
    bridge._invalidate_execution_nodes = lambda *_args, **_kwargs: None
    monkeypatch.setenv('MOTION_MOTOR_SERVICE_UNIT', 'motion-motor.service')

    assert bridge._runtime_project_id() == project_id
    result = bridge.clear_motor_runtime_application()

    assert result['success'] is True
    assert result['cleared'] is True
    assert result['previous_project_id'] == project_id
    assert result['runtime_project_id'] == ''
    assert bridge.applied_motor_config_file == Path()
    assert bridge._runtime_project_id() == ''
    assert bridge._motion_run_status['state'] == 'stopped'
    assert bridge._motion_studio_status['state'] == 'idle'
    assert repository.motor_runtime_state().get('target_project_id') in ('', None)
    deleted = bridge.delete_motion_project(project_id)
    assert deleted['permanently_deleted'] is True


def test_project_change_blocker_allows_stopping_only_when_requested(tmp_path):
    repository = ProjectRepository(tmp_path / 'projects')
    bridge = MotionWebBridge.__new__(MotionWebBridge)
    bridge.project_repository = repository
    bridge._motion_run_status = {'state': 'idle'}
    bridge._motion_studio_status = {'state': 'stopping'}
    bridge._motion_run_lock = threading.Lock()
    bridge._motion_studio_lock = threading.Lock()

    assert 'stopping' in bridge._project_change_blocker()
    assert bridge._project_change_blocker(allow_studio_stopping=True) == ''

    bridge._motion_studio_status = {'state': 'playing'}

    assert 'playing' in bridge._project_change_blocker(
        allow_studio_stopping=True
    )


def test_delete_project_permanently_removes_folder_and_older_archives(tmp_path):
    repository = ProjectRepository(tmp_path / 'projects')
    other_id = repository.create_project('keep me')['project']['project_id']
    project_id = repository.create_project('delete me')['project']['project_id']
    project_dir = tmp_path / 'projects' / project_id
    archive = tmp_path / 'projects' / '.trash' / 'projects' / f'legacy-{project_id}'
    archive.parent.mkdir(parents=True)
    shutil.copytree(project_dir, archive)
    other_archive = archive.parent / f'legacy-{other_id}'
    shutil.copytree(tmp_path / 'projects' / other_id, other_archive)

    result = repository.delete_project(project_id)

    assert not project_dir.exists()
    assert not archive.exists()
    assert (tmp_path / 'projects' / other_id).is_dir()
    assert other_archive.is_dir()
    assert result['selected_project_id'] == ''
    assert result['permanently_deleted'] is True
    assert 'trash_path' not in result


def test_import_rejects_path_escape_and_invalid_file(tmp_path):
    repository = ProjectRepository(tmp_path / 'projects')
    project_id = repository.create_project('safe')['project']['project_id']

    with pytest.raises(ValueError):
        repository.import_text(project_id, 'motions', '../escape.json', MOTION_TEXT)
    with pytest.raises(ValueError, match='지원하지 않는 모션 파일 헤더'):
        repository.import_text(project_id, 'motions', 'bad.json', '{}\n[]')


def test_project_editor_save_and_studio_layers_stay_in_selected_project(tmp_path):
    repository = ProjectRepository(tmp_path / 'projects')
    project_id = repository.create_project('sync')['project']['project_id']
    project_motion = tmp_path / 'projects' / project_id / 'motions' / 'motion.json'
    project_motion.write_text(MOTION_TEXT + '\n', encoding='utf-8')

    sync = repository.sync_project_file('motions', project_motion)
    assert sync['project_id'] == project_id
    assert (tmp_path / 'projects' / project_id / 'motions' / 'motion.json').is_file()

    layers = repository.sync_studio_layers({
        'project_id': 'studio-one',
        'layers': [{'layer_id': 'base', 'frames': []}],
    })
    assert layers['files'] == ['studio-one__base.json']
    assert (tmp_path / 'projects' / project_id / 'layers' / 'studio-one__base.json').is_file()


def test_studio_layer_partial_sync_writes_only_changed_layer(tmp_path, monkeypatch):
    repository = ProjectRepository(tmp_path / 'projects')
    project_id = repository.create_project('partial sync')['project']['project_id']
    project_dir = tmp_path / 'projects' / project_id
    studio_project = {
        'project_id': 'studio-one',
        'layers': [
            {'layer_id': 'first', 'frames': []},
            {'layer_id': 'second', 'frames': []},
        ],
    }
    repository.sync_studio_layers(studio_project)
    second_path = project_dir / 'layers' / 'studio-one__second.json'
    second_before = second_path.read_bytes()
    writes = []
    file_hashes = []
    original_write = repository._atomic_write
    original_file_hash = project_repository_module._sha256_file

    def record_write(path, content):
        writes.append(Path(path))
        return original_write(path, content)

    monkeypatch.setattr(repository, '_atomic_write', record_write)
    monkeypatch.setattr(
        project_repository_module,
        '_sha256_file',
        lambda path: file_hashes.append(Path(path)) or original_file_hash(path),
    )
    studio_project['layers'][0]['name'] = '변경됨'
    result = repository.sync_studio_layers(
        studio_project,
        upsert_layer_ids=['first'],
        replace_all=False,
    )

    assert result['files'] == ['studio-one__first.json']
    assert second_path.read_bytes() == second_before
    assert project_dir / 'layers' / 'studio-one__first.json' in writes
    assert second_path not in writes
    assert file_hashes == []
    assert result['hashed_file_count'] == 0
    assert result['reused_hash_count'] == 1
    assert result['elapsed_ms'] >= 0
    assert set(result['managed_files']) == {
        'studio-one__first.json',
        'studio-one__second.json',
    }
    assert result['layer_signature'] == _project_tree_category_signature(
        repository.get_project(project_id)['tree'], 'layers'
    )
    file_hashes.clear()

    second_path.write_text('{"layer_id":"second","frames":[]}\n', encoding='utf-8')
    external_result = repository.sync_studio_layers(
        studio_project,
        upsert_layer_ids=[],
        replace_all=False,
    )
    assert file_hashes == [second_path]
    assert external_result['hashed_file_count'] == 1


def test_studio_layer_partial_sync_deletes_only_requested_layer(tmp_path):
    repository = ProjectRepository(tmp_path / 'projects')
    project_id = repository.create_project('partial delete')['project']['project_id']
    project_dir = tmp_path / 'projects' / project_id
    repository.sync_studio_layers({
        'project_id': 'studio-one',
        'layers': [
            {'layer_id': 'first', 'frames': []},
            {'layer_id': 'second', 'frames': []},
        ],
    })

    result = repository.sync_studio_layers(
        {'project_id': 'studio-one', 'layers': []},
        delete_layer_ids=['first'],
        replace_all=False,
    )

    assert result['deleted_files'] == ['studio-one__first.json']
    assert not (project_dir / 'layers' / 'studio-one__first.json').exists()
    assert (project_dir / 'layers' / 'studio-one__second.json').is_file()


def test_project_editor_rejects_automatic_external_file_sync(tmp_path):
    repository = ProjectRepository(tmp_path / 'projects')
    repository.create_project('isolated')
    external = tmp_path / 'motion.json'
    external.write_text(MOTION_TEXT + '\n', encoding='utf-8')

    with pytest.raises(ValueError, match='프로젝트 외부'):
        repository.sync_project_file('motions', external)


def test_project_repository_rejects_linked_project_directory(tmp_path):
    root = tmp_path / 'projects'
    repository = ProjectRepository(root)
    external = tmp_path / 'external-project'
    external.mkdir()
    (external / 'project.json').write_text(
        json.dumps({'version': 1, 'project_id': 'linked'}), encoding='utf-8'
    )
    (root / 'linked').symlink_to(external, target_is_directory=True)

    with pytest.raises(ValueError, match='링크'):
        repository.get_project('linked')


def test_project_repository_rejects_linked_internal_directory(tmp_path):
    repository = ProjectRepository(tmp_path / 'projects')
    project_id = repository.create_project('isolated')['project']['project_id']
    project_dir = tmp_path / 'projects' / project_id
    (project_dir / 'motions').rmdir()
    (project_dir / 'motions').symlink_to(tmp_path, target_is_directory=True)

    with pytest.raises(ValueError, match='링크'):
        repository.get_project(project_id)


def test_project_repository_rejects_linked_asset_file(tmp_path):
    repository = ProjectRepository(tmp_path / 'projects')
    project_id = repository.create_project('isolated')['project']['project_id']
    external = tmp_path / 'outside.json'
    external.write_text(MOTION_TEXT + '\n', encoding='utf-8')
    linked = tmp_path / 'projects' / project_id / 'motions' / 'linked.json'
    linked.symlink_to(external)

    with pytest.raises(ValueError, match='프로젝트 파일'):
        repository.read_file(project_id, 'motions', 'linked.json')


def test_web_file_read_rejects_non_selected_project(tmp_path):
    repository = ProjectRepository(tmp_path / 'projects')
    other_id = repository.create_project('other')['project']['project_id']
    selected_id = repository.create_project('selected')['project']['project_id']
    repository.import_text(other_id, 'motions', 'other.json', MOTION_TEXT)
    repository.select_project(selected_id)
    bridge = MotionWebBridge.__new__(MotionWebBridge)
    bridge.project_repository = repository

    with pytest.raises(ValueError, match='현재 선택한 프로젝트'):
        bridge.load_motion_project_file(other_id, 'motions', 'other.json')
    with pytest.raises(ValueError, match='현재 선택한 프로젝트'):
        bridge.download_motion_project_file(other_id, 'motions', 'other.json')


def test_read_only_project_files_can_be_viewed_but_managed_paths_are_rejected(tmp_path):
    repository = ProjectRepository(tmp_path / 'projects')
    project = repository.create_project('viewer')['project']
    project_id = project['project_id']
    project_dir = tmp_path / 'projects' / project_id
    runtime_file = project_dir / 'runtime' / 'applied_motor_config.yaml'
    runtime_file.write_text('masters: []\n', encoding='utf-8')

    manifest = repository.read_read_only_file(project_id, 'project.json')
    runtime = repository.read_read_only_file(
        project_id, 'runtime/applied_motor_config.yaml'
    )

    assert manifest['read_only'] is True
    assert manifest['relative_path'] == 'project.json'
    assert '"project_id"' in manifest['content']
    assert runtime['content'] == 'masters: []\n'
    assert runtime['relative_path'] == 'runtime/applied_motor_config.yaml'

    with pytest.raises(ValueError, match='읽기 전용 프로젝트 파일'):
        repository.read_read_only_file(project_id, 'motor_axes/motor_axes.yaml')
    with pytest.raises(ValueError, match='올바르지 않은'):
        repository.read_read_only_file(project_id, '../outside.txt')


def test_web_read_only_file_rejects_non_selected_project(tmp_path):
    repository = ProjectRepository(tmp_path / 'projects')
    other_id = repository.create_project('other')['project']['project_id']
    selected_id = repository.create_project('selected')['project']['project_id']
    repository.select_project(selected_id)
    bridge = MotionWebBridge.__new__(MotionWebBridge)
    bridge.project_repository = repository

    with pytest.raises(ValueError, match='현재 선택한 프로젝트'):
        bridge.load_read_only_project_file(other_id, 'project.json')
