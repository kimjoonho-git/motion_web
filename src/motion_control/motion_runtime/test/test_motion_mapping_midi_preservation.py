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
