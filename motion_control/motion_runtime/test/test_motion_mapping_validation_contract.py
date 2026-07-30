import pytest
import yaml

from motion_runtime.motion_mapping_manager import MotionMappingManager


def _manager():
    return MotionMappingManager.__new__(MotionMappingManager)


def _row(motion_id='1-1', axis=0, **overrides):
    row = {
        'motion_id': motion_id,
        'enabled': True,
        'motor_ref': '',
        'motor_axis': axis,
        'reference_enabled': True,
        'reference_position_deg': 10.0,
        'motion_lower_deg': -90.0,
        'motion_upper_deg': 90.0,
        'initial_mode': 'manual',
        'initial_motion_position_deg': 0.0,
        'initial_move_time_sec': 5.0,
        'invert': False,
        'offset_deg': 0.0,
        'scale': 1.0,
        'gear_ratio': 1.0,
    }
    row.update(overrides)
    return row


def test_alias_zero_axis_fallback_is_a_valid_unique_mapping_target():
    manager = _manager()
    mapping = manager._normalize_mapping({
        'name': 'axis_zero_alias',
        'mappings': [_row(axis=0), _row('1-2', axis=1)],
    })
    validation = manager._validate_mapping(mapping, include_motion_file=False)
    assert validation['valid'] is True
    assert [row['motor_axis'] for row in mapping['mappings']] == [0, 1]
    assert [row['motor_ref'] for row in mapping['mappings']] == ['', '']


def test_duplicate_axis_and_invalid_numeric_ranges_are_rejected():
    manager = _manager()
    mapping = manager._normalize_mapping({
        'name': 'invalid',
        'mappings': [
            _row(axis=2),
            _row(
                '1-2',
                axis=2,
                motion_lower_deg=30.0,
                motion_upper_deg=-30.0,
                scale=0.0,
                gear_ratio=0.0,
                initial_move_time_sec=0.0,
            ),
        ],
    })
    validation = manager._validate_mapping(mapping, include_motion_file=False)
    assert validation['valid'] is False
    text = '\n'.join(validation['errors'])
    assert 'duplicated motor target' in text
    assert 'motion_lower_deg must be <=' in text
    assert 'scale must be a non-zero number' in text
    assert 'gear_ratio must be > 0' in text
    assert 'initial_move_time_sec must be > 0' in text


def test_reference_offset_scale_invert_and_gear_ratio_calculation_contract():
    manager = _manager()
    row = _row(
        reference_position_deg=10.0,
        offset_deg=5.0,
        scale=2.0,
        invert=True,
        gear_ratio=3.0,
    )
    assert manager._motion_to_output_value(row, 7.0) == -24.0
    assert manager._motion_to_motor_target(row, 7.0) == -62.0
    row['reference_enabled'] = False
    assert manager._motion_to_motor_target(row, 7.0) == -72.0


def test_mapping_save_list_load_round_trip_stays_inside_selected_project(tmp_path):
    manager = _manager()
    manager.mappings_dir = tmp_path / 'selected-project' / 'motion_axis_matching'
    manager.motion_files_dir = tmp_path / 'selected-project' / 'motions'
    manager.mappings_dir.mkdir(parents=True)
    manager.motion_files_dir.mkdir(parents=True)

    saved = manager._save_mapping({
        'mapping': {
            'name': 'face axes',
            'motion_file_id': '',
            'mappings': [_row(axis=0)],
        },
    })
    assert saved['success'] is True
    assert saved['file']['id'] == 'face_axes.yaml'
    assert (manager.mappings_dir / 'face_axes.yaml').is_file()
    assert len(manager._list_mappings()['files']) == 1

    loaded = manager._load_mapping('face_axes.yaml')
    assert loaded['success'] is True
    assert loaded['mapping']['name'] == 'face axes'
    assert loaded['mapping']['mappings'][0]['motor_axis'] == 0
    assert loaded['validation']['valid'] is True


def test_mapping_save_rejects_an_outdated_loaded_file_revision(tmp_path):
    manager = _manager()
    manager.mappings_dir = tmp_path / 'selected-project' / 'motion_axis_matching'
    manager.motion_files_dir = tmp_path / 'selected-project' / 'motions'
    manager.mappings_dir.mkdir(parents=True)
    manager.motion_files_dir.mkdir(parents=True)
    first = manager._save_mapping({
        'mapping': {'name': 'axes', 'mappings': [_row(axis=0)]},
    })
    revision = first['file']['revision']
    path = manager.mappings_dir / first['file']['id']
    path.write_text(path.read_text(encoding='utf-8') + '# external update\n', encoding='utf-8')

    with pytest.raises(ValueError, match='저장을 거부했습니다'):
        manager._save_mapping({
            'file_id': first['file']['id'],
            'base_revision': revision,
            'mapping': first['mapping'],
        })


def test_mapping_save_allows_a_midi_only_file_revision_change(tmp_path):
    manager = _manager()
    manager.mappings_dir = tmp_path / 'selected-project' / 'motion_axis_matching'
    manager.motion_files_dir = tmp_path / 'selected-project' / 'motions'
    manager.mappings_dir.mkdir(parents=True)
    manager.motion_files_dir.mkdir(parents=True)
    first = manager._save_mapping({
        'mapping': {'name': 'axes', 'mappings': [_row(axis=0)]},
    })
    path = manager.mappings_dir / first['file']['id']
    payload = yaml.safe_load(path.read_text(encoding='utf-8'))
    payload['midi_banks'] = {
        'version': 1,
        'active_bank_id': 'bank_1',
        'banks': [],
    }
    path.write_text(
        yaml.safe_dump(payload, sort_keys=False, allow_unicode=True),
        encoding='utf-8',
    )
    edited = dict(first['mapping'])
    edited['mappings'] = [dict(first['mapping']['mappings'][0], invert=True)]

    saved = manager._save_mapping({
        'file_id': first['file']['id'],
        'base_mapping_revision': first['file']['mapping_revision'],
        'mapping': edited,
    })

    assert saved['success'] is True
    assert saved['mapping']['mappings'][0]['invert'] is True
    assert yaml.safe_load(path.read_text(encoding='utf-8'))['midi_banks'] == (
        payload['midi_banks']
    )


def test_mapping_save_rejects_a_stale_mapping_section_revision(tmp_path):
    manager = _manager()
    manager.mappings_dir = tmp_path / 'selected-project' / 'motion_axis_matching'
    manager.motion_files_dir = tmp_path / 'selected-project' / 'motions'
    manager.mappings_dir.mkdir(parents=True)
    manager.motion_files_dir.mkdir(parents=True)
    first = manager._save_mapping({
        'mapping': {'name': 'axes', 'mappings': [_row(axis=0)]},
    })
    path = manager.mappings_dir / first['file']['id']
    payload = yaml.safe_load(path.read_text(encoding='utf-8'))
    payload['mappings'][0]['invert'] = True
    path.write_text(
        yaml.safe_dump(payload, sort_keys=False, allow_unicode=True),
        encoding='utf-8',
    )

    with pytest.raises(ValueError, match='모션축 설정이 화면을 불러온 뒤 변경'):
        manager._save_mapping({
            'file_id': first['file']['id'],
            'base_mapping_revision': first['file']['mapping_revision'],
            'mapping': first['mapping'],
        })
