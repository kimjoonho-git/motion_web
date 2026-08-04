from motion_runtime.motion_mapping_manager import MotionMappingManager


def test_reads_midi_banks_for_preservation_during_motion_mapping_save(tmp_path):
    path = tmp_path / 'show_mapping.yaml'
    path.write_text(
        'file_id: show_mapping.yaml\n'
        'mappings: []\n'
        'midi_banks:\n'
        '  version: 1\n'
        '  active_bank_id: bank_1\n'
        '  banks: []\n',
        encoding='utf-8',
    )

    assert MotionMappingManager._midi_banks_from_file(path) == {
        'version': 1,
        'active_bank_id': 'bank_1',
        'banks': [],
    }


def test_missing_midi_banks_returns_none(tmp_path):
    path = tmp_path / 'show_mapping.yaml'
    path.write_text('file_id: show_mapping.yaml\nmappings: []\n', encoding='utf-8')

    assert MotionMappingManager._midi_banks_from_file(path) is None


def test_loading_missing_midi_banks_is_a_first_save_state(tmp_path):
    manager = MotionMappingManager.__new__(MotionMappingManager)
    manager.mappings_dir = tmp_path
    path = tmp_path / 'show_mapping.yaml'
    path.write_text('file_id: show_mapping.yaml\nmappings: []\n', encoding='utf-8')

    result = manager._load_midi_banks('show_mapping.yaml')

    assert result['success'] is False
    assert result['missing'] is True
    assert result['midi_banks'] is None


def test_first_motion_axis_mapping_does_not_require_existing_motion_file():
    manager = MotionMappingManager.__new__(MotionMappingManager)
    mapping = manager._normalize_mapping({
        'name': 'first axes',
        'motion_file_id': '',
        'mappings': [{
            'motion_id': '1-1',
            'enabled': True,
            'motor_axis': 0,
            'reference_position_deg': 0.0,
            'motion_lower_deg': -10.0,
            'motion_upper_deg': 10.0,
            'initial_mode': 'manual',
            'initial_motion_position_deg': 0.0,
            'initial_move_time_sec': 5.0,
            'scale': 1.0,
            'gear_ratio': 1.0,
        }],
    })
    validation = manager._validate_mapping(mapping)
    assert validation['valid'], validation


def test_motor_ref_is_the_primary_mapping_target():
    manager = MotionMappingManager.__new__(MotionMappingManager)
    mapping = manager._normalize_mapping({
        'name': 'stable ids',
        'mappings': [{
            'motion_id': '1-1',
            'enabled': True,
            'motor_ref': 'ac_servo:alias:101',
            'motor_axis': 7,
            'initial_mode': 'manual',
        }],
    })

    validation = manager._validate_mapping(mapping, include_motion_file=False)

    assert mapping['mappings'][0]['motor_ref'] == 'ac_servo:alias:101'
    assert validation['valid'], validation


def test_bus_scoped_motor_refs_are_valid_mapping_targets():
    manager = MotionMappingManager.__new__(MotionMappingManager)
    mapping = manager._normalize_mapping({
        'name': 'bus scoped ids',
        'mappings': [
            {
                'motion_id': '1-1',
                'enabled': True,
                'motor_ref': 'ac_servo:master:1:alias:101',
                'motor_axis': 7,
                'initial_mode': 'manual',
            },
            {
                'motion_id': '1-2',
                'enabled': True,
                'motor_ref': 'dynamixel:port:%2Fdev%2FttyUSB1:id:3',
                'motor_axis': 8,
                'initial_mode': 'manual',
            },
            {
                'motion_id': '1-3',
                'enabled': True,
                'motor_ref': 'ac_servo:master:0:slave:4',
                'motor_axis': 4,
                'initial_mode': 'manual',
            },
        ],
    })

    validation = manager._validate_mapping(mapping, include_motion_file=False)

    assert validation['valid'], validation
