import json
import os
import shutil
import threading
from pathlib import Path

import pytest
import yaml

from motion_web_bridge.project_repository import ProjectRepository
from motion_web_bridge.bridge_node import MotionWebBridge
from motion_web_bridge.service_entrypoint import (
    resolve_applied_motor_config,
    resolve_project_generation,
)


MOTION_TEXT = '\n'.join([
    json.dumps({'type': 'motion_header', 'rotation_unit': 'deg'}),
    json.dumps([1, 0.0, '1-1', 0.0]),
])


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
        '  rotary_alias: 3\n  slave_position: 1\ndrivers:\n- id: 0\n  type: minas\n'
        '  profile_velocity: 10\n  profile_acceleration: 20\n  profile_deceleration: 20\n',
    )
    runtime = repository.prepare_runtime_motor_config(project_id)
    runtime_path = project_dir / 'runtime' / 'applied_motor_config.yaml'
    assert runtime_path.is_file()
    assert 'web_axis_identities' not in yaml.safe_load(runtime_path.read_text())
    assert not repository.get_project(project_id)['project']['setup_status']['motor_applied']
    repository.mark_runtime_motor_config_applied(project_id)
    assert repository.get_project(project_id)['project']['setup_status']['motor_applied']


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
        '  rotary_alias: 3\n  slave_position: 1\ndrivers:\n- id: 0\n  type: minas\n'
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
        '  rotary_alias: 3\n  slave_position: 1\ndrivers:\n- id: 0\n  type: minas\n'
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


def test_runtime_from_another_project_is_hidden_at_the_project_boundary(tmp_path):
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
    assert listed['runtime_project_id'] == ''
    assert next(
        item for item in listed['projects'] if item['project_id'] == runtime_id
    )['runtime_active'] is False


def test_project_switch_discards_the_previous_applied_runtime(tmp_path):
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
    prepared = repository.prepare_runtime_motor_config(runtime_id)
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
    assert repository.applied_runtime_motor_config() is None
    assert resolve_applied_motor_config(workspace) is None
    previous = next(
        item for item in repository.list_projects()['projects']
        if item['project_id'] == runtime_id
    )
    assert previous['setup_status']['motor_applied'] is False


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
    monkeypatch.setenv('MOTOR_CONFIG_FILE', str(runtime))

    status = bridge._runtime_service_status({
        'generated_at': 100.0,
        'last_motor_status_at': 99.8,
        'motors': [{'controller_index': 0}],
    })

    assert status['phase'] == 'ready'
    assert status['motor_manager_expected'] is True
    assert status['motor_count'] == 1


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
    monkeypatch.setattr(
        'motion_web_bridge.bridge_node.subprocess.Popen',
        lambda command, **kwargs: commands.append((command, kwargs)),
    )

    result = bridge.apply_motor_config()

    assert result['success'] is True
    assert result['restart_mode'] == 'managed_service'
    assert commands[0][0][:4] == [
        '/bin/bash', '-c', 'sleep 0.5; exec "$@"',
        'motion-control-delayed-restart',
    ]
    assert commands[0][0][4:] == [
        '/usr/bin/systemctl', '--user', 'restart', '--no-block',
        'motion-control.service',
    ]
    assert commands[0][1]['start_new_session'] is True
    assert repository.applied_runtime_motor_config().is_file()


def test_user_can_request_managed_program_restart_from_web(monkeypatch):
    bridge = MotionWebBridge.__new__(MotionWebBridge)
    bridge._motion_run_lock = threading.Lock()
    bridge._motion_studio_lock = threading.Lock()
    bridge._motion_run_status = {'state': 'idle'}
    bridge._motion_studio_status = {'state': 'idle'}
    bridge.snapshot = lambda: {}
    commands = []
    monkeypatch.setenv('MOTION_CONTROL_SERVICE_UNIT', 'motion-control.service')
    monkeypatch.setattr(
        'motion_web_bridge.bridge_node.subprocess.Popen',
        lambda command, **kwargs: commands.append((command, kwargs)),
    )

    result = bridge.restart_managed_program()

    assert result['success'] is True
    assert commands[0][0][-1] == 'motion-control.service'


def test_program_restart_button_requires_installed_service(monkeypatch):
    bridge = MotionWebBridge.__new__(MotionWebBridge)
    bridge.snapshot = lambda: {}
    monkeypatch.delenv('MOTION_CONTROL_SERVICE_UNIT', raising=False)

    result = bridge.restart_managed_program()

    assert result['success'] is False
    assert '최초 설치' in result['message']


@pytest.mark.parametrize(
    ('run_state', 'studio_state'),
    [('running', 'idle'), ('idle', 'recording')],
)
def test_project_change_is_blocked_during_motion_operations(run_state, studio_state):
    bridge = MotionWebBridge.__new__(MotionWebBridge)
    bridge._motion_run_lock = threading.Lock()
    bridge._motion_studio_lock = threading.Lock()
    bridge._motion_run_status = {'state': run_state}
    bridge._motion_studio_status = {'state': studio_state}

    with pytest.raises(ValueError, match='프로젝트를 변경할 수 없습니다'):
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


def test_motion_studio_refresh_does_not_reopen_workspace_while_recording(tmp_path):
    repository = ProjectRepository(tmp_path / 'projects')
    project = repository.create_project('recording')['project']
    project_id = project['project_id']
    repository.import_text(
        project_id,
        'motion_axis_matching',
        'mapping.yaml',
        'version: 1\nname: recording\nmappings: []\n',
    )

    bridge = MotionWebBridge.__new__(MotionWebBridge)
    bridge.project_repository = repository
    bridge._motion_studio_lock = threading.Lock()
    bridge._motion_studio_status = {'state': 'recording'}
    commands = []

    def request(command, payload=None, timeout_sec=4.0):
        commands.append((command, payload, timeout_sec))
        return {
            'success': True,
            'project': {'project_id': 'studio-project', 'layers': []},
            'mappings': [{'file_id': 'mapping.yaml'}],
            'motion_files': [],
            'status': {'state': 'recording'},
        }

    bridge.request_motion_studio = request

    result = bridge.prepare_unified_motion_studio()

    assert result['success'] is True
    assert commands[0][0] == 'list'
    assert result['status']['state'] == 'recording'
