import json

import pytest

from motion_studio.project_store import ProjectStore


def write_mapping(root, name='face.yaml'):
    mappings = root / 'motion_axis_matching'
    mappings.mkdir(parents=True)
    path = mappings / name
    path.write_text(
        'mappings:\n'
        '- motion_id: 1-1\n'
        '  enabled: true\n'
        '  motor_axis: 0\n'
        '  motion_lower_deg: -20\n'
        '  motion_upper_deg: 20\n'
        '- motion_id: 1-2\n'
        '  enabled: true\n'
        '  motor_axis: 1\n',
        encoding='utf-8',
    )
    return path


def test_project_references_mapping_without_modifying_it(tmp_path):
    mapping_path = write_mapping(tmp_path)
    original = mapping_path.read_bytes()
    store = ProjectStore(tmp_path)

    project = store.create_project('얼굴 테스트', 'face.yaml')
    saved = store.save_project(project)

    assert saved['mapping_file_id'] == 'face.yaml'
    assert store.mapping_check(saved)['matches_project'] is True
    assert 'armed_motion_ids' not in saved
    assert mapping_path.read_bytes() == original


def test_project_layers_round_trip(tmp_path):
    write_mapping(tmp_path)
    store = ProjectStore(tmp_path)
    project = store.create_project('test', 'face.yaml')
    project['layers'] = [{
        'layer_id': 'take_1',
        'name': '첫 녹화',
        'frames': [
            {'frame': 1, 'time_sec': 0.02, 'values': {'1-1': 0.0}},
            {'frame': 2, 'time_sec': 0.04, 'values': {'1-1': 1.5}},
        ],
    }]
    saved = store.save_project(project)

    loaded = store.load_project(saved['project_id'])
    assert loaded['layers'][0]['frames'][1]['values']['1-1'] == 1.5
    assert json.loads(
        (tmp_path / 'runtime' / 'studio_projects' / f"{saved['project_id']}.json").read_text()
    )


def test_mapping_checksum_detects_external_change(tmp_path):
    mapping_path = write_mapping(tmp_path)
    store = ProjectStore(tmp_path)
    project = store.create_project('test', 'face.yaml')
    mapping_path.write_text(mapping_path.read_text() + '# changed\n', encoding='utf-8')

    assert store.mapping_check(project)['matches_project'] is False


def test_exported_motion_file_imports_as_single_editable_layer(tmp_path):
    mapping_path = write_mapping(tmp_path)
    original_mapping = mapping_path.read_bytes()
    store = ProjectStore(tmp_path)
    motion_path = tmp_path / 'motions' / 'recorded.json'
    motion_path.write_text(
        '{"title":"가져온 모션","type":"motion_header",'
        '"rotation_mode":"relative","rotation_unit":"deg",'
        '"fields":["frame","time_sec","id","value"]}\n'
        '[1,0.02,"1-1",0.0,"1-2",1.5]\n'
        '[2,0.04,"1-1",2.0,"1-2",3.5]\n',
        encoding='utf-8',
    )

    project = store.import_motion_file('recorded.json', 'face.yaml')

    assert project['name'] == '가져온 모션'
    assert project['mapping_file_id'] == 'face.yaml'
    assert len(project['layers']) == 1
    assert project['layers'][0]['source_motion_file_id'] == 'recorded.json'
    assert project['layers'][0]['frames'][1]['values']['1-2'] == 3.5
    assert mapping_path.read_bytes() == original_mapping


def test_motion_file_import_rejects_id_missing_from_read_only_mapping(tmp_path):
    write_mapping(tmp_path)
    store = ProjectStore(tmp_path)
    (tmp_path / 'motions' / 'unknown-axis.json').write_text(
        '{"title":"bad","type":"motion_header","rotation_unit":"deg"}\n'
        '[1,0.02,"9-9",1.0]\n',
        encoding='utf-8',
    )

    with pytest.raises(ValueError, match='모션축 설정에 없는 Motion ID: 9-9'):
        store.import_motion_file('unknown-axis.json', 'face.yaml')

    assert store.list_projects() == []


def test_workspace_identity_and_imported_layer_round_trip(tmp_path):
    write_mapping(tmp_path)
    store = ProjectStore(tmp_path)
    project = store.create_project('통합 프로젝트', 'face.yaml')
    project['workspace_project_id'] = 'robot-face-001'
    project = store.save_project(project)
    (tmp_path / 'motions' / 'base.json').write_text(
        '{"title":"base","type":"motion_header","rotation_unit":"deg"}\n'
        '[1,0.02,"1-1",2.0]\n',
        encoding='utf-8',
    )

    saved = store.append_motion_file(project, 'base.json')
    loaded = store.load_project(saved['project_id'])

    assert loaded['workspace_project_id'] == 'robot-face-001'
    assert store.summary(loaded)['workspace_project_id'] == 'robot-face-001'
    assert loaded['layers'][0]['source_motion_file_id'] == 'base.json'
