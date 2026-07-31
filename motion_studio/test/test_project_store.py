import json
from pathlib import Path

import pytest

from motion_studio.layer_editor import edit_layer
from motion_studio.project_store import ProjectStore
from motion_studio.project_store import MOTION_FILE_SIZE_LIMIT_BYTES
from motion_studio.timeline import motion_file_text


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
    assert saved['transition_safety_level'] == 4
    assert store.mapping_check(saved)['matches_project'] is True
    assert 'armed_motion_ids' not in saved
    assert mapping_path.read_bytes() == original


def test_motion_file_limit_matches_large_editor_output_and_reader_streams(
    tmp_path,
    monkeypatch,
):
    write_mapping(tmp_path)
    store = ProjectStore(tmp_path)
    project = store.create_project('large motion', 'face.yaml')
    file_id = store.write_motion_file(
        'streamed.json',
        motion_file_text(project, [
            {'frame': 1, 'time_sec': 0.02, 'values': {'1-1': 1.0}},
            {'frame': 2, 'time_sec': 0.04, 'values': {'1-1': 2.0}},
        ]),
    )
    path = store.files_dir / file_id
    original_read_text = Path.read_text

    def reject_whole_file_read(candidate, *args, **kwargs):
        if candidate == path:
            raise AssertionError('motion file must be streamed')
        return original_read_text(candidate, *args, **kwargs)

    monkeypatch.setattr(Path, 'read_text', reject_whole_file_read)

    loaded = store.read_motion_file(file_id)

    assert MOTION_FILE_SIZE_LIMIT_BYTES == 256 * 1024 * 1024
    assert len(loaded['frames']) == 2


def test_limited_motion_write_preserves_previous_file_on_overflow(tmp_path):
    target = tmp_path / 'motion.json'
    target.write_text('previous', encoding='utf-8')

    with pytest.raises(ValueError, match='limit'):
        ProjectStore._atomic_write_limited(
            target,
            'too-large',
            4,
            'limit exceeded',
        )

    assert target.read_text(encoding='utf-8') == 'previous'
    assert not target.with_suffix('.json.tmp').exists()


def test_project_layers_round_trip(tmp_path):
    write_mapping(tmp_path)
    store = ProjectStore(tmp_path)
    project = store.create_project('test', 'face.yaml')
    project['transition_safety_level'] = 7
    project['layers'] = [{
        'layer_id': 'take_1',
        'name': '첫 녹화',
        'source_layer_ids': ['source_a', 'source_b'],
        'edit_revision': 3,
        'point_curves': [{
            'curve_id': 'curve_1', 'motion_id': '1-1',
            'interpolation_order': 5,
            'points': [
                {'point_id': 'point_1', 'time_sec': 0.02, 'value_deg': 0.0,
                 'tangent_mode': 'auto'},
                {'point_id': 'point_2', 'time_sec': 0.04, 'value_deg': 1.5,
                 'tangent_mode': 'broken',
                 'in_handle': {'dt_sec': -0.01, 'dv_deg': -0.5},
                 'out_handle': {'dt_sec': 0.0, 'dv_deg': 0.0}},
            ],
        }],
        'frames': [
            {'frame': 1, 'time_sec': 0.02, 'values': {'1-1': 0.0}},
            {'frame': 2, 'time_sec': 0.04, 'values': {'1-1': 1.5}},
        ],
    }]
    saved = store.save_project(project)

    loaded = store.load_project(saved['project_id'])
    assert loaded['transition_safety_level'] == 7
    assert loaded['layers'][0]['source_layer_ids'] == ['source_a', 'source_b']
    assert loaded['layers'][0]['edit_revision'] == 3
    assert loaded['layers'][0]['point_curves'][0]['points'][1]['tangent_mode'] == 'broken'
    assert loaded['layers'][0]['point_curves'][0]['interpolation_order'] == 5
    assert loaded['layers'][0]['point_curves'][0]['points'][1]['in_handle']['dv_deg'] == -0.5
    assert loaded['layers'][0]['frames'][1]['values']['1-1'] == 1.5
    assert json.loads(
        (tmp_path / 'runtime' / 'studio_projects' / f"{saved['project_id']}.json").read_text()
    )


def test_linked_point_edits_remain_isolated_between_two_projects(tmp_path):
    write_mapping(tmp_path)
    store = ProjectStore(tmp_path)
    projects = [
        store.create_project('프로젝트 A', 'face.yaml'),
        store.create_project('프로젝트 B', 'face.yaml'),
    ]
    for index, project in enumerate(projects):
        project['layers'] = [{
            'layer_id': 'same_layer_id',
            'name': f'레이어 {index}',
            'frames': [
                {'frame': 1, 'time_sec': 0.0, 'values': {'1-1': float(index)}},
                {'frame': 2, 'time_sec': 1.0, 'values': {'1-1': 10.0 + index}},
            ],
        }]
        projects[index] = store.save_project(project)

    first = store.load_project(projects[0]['project_id'])
    first['layers'][0] = edit_layer(first['layers'][0], {
        'operation': 'point_curve',
        'motion_ids': ['1-1'],
        'curve_id': 'curve_a',
        'points': [
            {'point_id': 'a1', 'time_sec': 0.0, 'value_deg': 0.0},
            {'point_id': 'a2', 'time_sec': 1.0, 'value_deg': 10.0},
        ],
    })
    first['layers'][0] = edit_layer(first['layers'][0], {
        'operation': 'time_shift',
        'motion_ids': ['1-1'],
        'start_sec': 0.0,
        'end_sec': 1.0,
        'delta_sec': 0.2,
    })
    store.save_project(first)

    saved_first = store.load_project(projects[0]['project_id'])
    untouched_second = store.load_project(projects[1]['project_id'])
    assert saved_first['layers'][0]['frames'][0]['time_sec'] == 0.2
    assert saved_first['layers'][0]['point_curves'][0]['curve_id'] == 'curve_a'
    assert untouched_second['layers'][0]['frames'][0]['time_sec'] == 0.0
    assert untouched_second['layers'][0].get('point_curves') == []


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


def test_motion_file_round_trip_restores_optional_points_and_tangents(tmp_path):
    write_mapping(tmp_path)
    store = ProjectStore(tmp_path)
    source = store.create_project('포인트 원본', 'face.yaml')
    source['layers'] = [{
        'layer_id': 'merged_layer',
        'name': '합친 레이어',
        'enabled': True,
        'edit_revision': 7,
        'point_curves': [{
            'curve_id': 'curve_1',
            'motion_id': '1-1',
            'interpolation_order': 5,
            'points': [
                {
                    'point_id': 'p1',
                    'time_sec': 0.02,
                    'value_deg': 0.0,
                    'tangent_mode': 'broken',
                    'out_handle': {'dt_sec': 0.01, 'dv_deg': 0.5},
                },
                {
                    'point_id': 'p2',
                    'time_sec': 0.04,
                    'value_deg': 2.0,
                    'tangent_mode': 'broken',
                    'in_handle': {'dt_sec': -0.01, 'dv_deg': -0.5},
                },
            ],
        }],
        'frames': [
            {'frame': 1, 'time_sec': 0.02, 'values': {'1-1': 0.0}},
            {'frame': 2, 'time_sec': 0.04, 'values': {'1-1': 2.0}},
        ],
    }]
    source = store.save_project(source)
    layer = source['layers'][0]
    store.write_motion_file(
        'published',
        motion_file_text(
            source,
            layer['frames'],
            editor_layer=layer,
            file_title='모션 테스트 1',
        ),
    )

    imported = store.import_motion_file('published.json', 'face.yaml', '다시 편집')
    restored = imported['layers'][0]

    assert restored['name'] == '모션 테스트 1'
    assert restored['source_motion_file_id'] == 'published.json'
    assert restored['edit_revision'] == 7
    assert restored['point_curves'][0]['interpolation_order'] == 5
    assert restored['point_curves'][0]['points'][0]['out_handle']['dv_deg'] == 0.5
    assert restored['point_curves'][0]['points'][1]['in_handle']['dv_deg'] == -0.5
    assert restored['frames'] == layer['frames']


def test_legacy_editor_layer_name_does_not_override_motion_file_name(tmp_path):
    write_mapping(tmp_path)
    store = ProjectStore(tmp_path)
    store.write_motion_file(
        '모션_테스트_1',
        '{"title":"프로젝트 이름","type":"motion_header","rotation_unit":"deg",'
        '"editor":{"schema_version":1,"layer":{'
        '"name":"녹화 1 복사본 복사본","point_curves":[]}}}\n'
        '[1,0.02,"1-1",3.0]\n',
    )

    imported = store.import_motion_file('모션_테스트_1.json', 'face.yaml')

    assert imported['layers'][0]['name'] == '모션_테스트_1'
    assert imported['layers'][0]['source_motion_file_id'] == '모션_테스트_1.json'


def test_motion_file_without_editor_metadata_still_imports_normally(tmp_path):
    write_mapping(tmp_path)
    store = ProjectStore(tmp_path)
    store.write_motion_file(
        'runtime-only',
        '{"title":"실행 전용","type":"motion_header","rotation_unit":"deg"}\n'
        '[1,0.02,"1-1",1.0]\n',
    )

    imported = store.import_motion_file('runtime-only.json', 'face.yaml')

    assert imported['layers'][0]['point_curves'] == []
    assert imported['layers'][0]['frames'][0]['values']['1-1'] == 1.0


def test_invalid_editor_metadata_does_not_invalidate_runtime_frames(tmp_path):
    write_mapping(tmp_path)
    store = ProjectStore(tmp_path)
    store.write_motion_file(
        'damaged-editor',
        '{"title":"실행 정상","type":"motion_header","rotation_unit":"deg",'
        '"editor":{"schema_version":1,"layer":{"point_curves":['
        '{"curve_id":"bad","motion_id":"1-1","points":[]}]}}}\n'
        '[1,0.02,"1-1",3.0]\n',
    )

    motion = store.read_motion_file('damaged-editor.json')
    imported = store.import_motion_file('damaged-editor.json', 'face.yaml')

    assert motion['editor_layer'] is None
    assert motion['editor_message'] == '포인트 편집 정보 사용 불가 · 실행 데이터는 정상'
    assert imported['layers'][0]['point_curves'] == []
    assert imported['layers'][0]['frames'][0]['values']['1-1'] == 3.0


def test_editor_metadata_does_not_hide_invalid_runtime_frames(tmp_path):
    write_mapping(tmp_path)
    store = ProjectStore(tmp_path)
    store.write_motion_file(
        'invalid-runtime',
        '{"title":"실행값 오류","type":"motion_header","rotation_unit":"deg",'
        '"editor":{"schema_version":1,"layer":{"point_curves":[]}}}\n'
        '[1,0.02,"1-1"]\n',
    )

    with pytest.raises(ValueError, match='모션 프레임 형식'):
        store.import_motion_file('invalid-runtime.json', 'face.yaml')


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
