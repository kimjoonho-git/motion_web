import yaml

from motion_runtime.midi_bank_store import (
    BLOCK_END,
    BLOCK_START,
    load_midi_banks,
    render_with_midi_banks,
    save_midi_banks,
)


STATE = {
    'version': 1,
    'active_bank_id': 'bank_1',
    'banks': [{'bank_id': 'bank_1', 'name': 'Bank 1', 'mappings': []}],
}


def test_midi_yaml_block_preserves_existing_motion_mapping_text(tmp_path):
    original = (
        'file_id: show_mapping.yaml\n'
        'motion_file_id: show.json\n'
        'mappings: []\n'
    )
    mapping_file = tmp_path / 'show_mapping.yaml'
    mapping_file.write_text(original, encoding='utf-8')

    backup = save_midi_banks(mapping_file, STATE)
    saved = mapping_file.read_text(encoding='utf-8')

    assert backup.read_text(encoding='utf-8') == original
    assert saved.startswith(original.rstrip() + '\n\n')
    assert saved.count(BLOCK_START) == 1
    assert saved.count(BLOCK_END) == 1
    assert load_midi_banks(mapping_file) == STATE


def test_unmarked_midi_section_is_replaced_without_changing_mapping_prefix():
    existing = (
        'file_id: show_mapping.yaml\n'
        'mappings: []\n'
        'midi_banks:\n'
        '  version: 1\n'
        '  active_bank_id: old\n'
        '  banks: []\n'
        'motion_file_id: show.json\n'
    )

    rendered = render_with_midi_banks(existing, STATE)
    parsed = yaml.safe_load(rendered)

    assert rendered.startswith('file_id: show_mapping.yaml\nmappings: []\n')
    assert rendered.endswith('motion_file_id: show.json\n')
    assert parsed['midi_banks'] == STATE
    assert rendered.count('midi_banks:') == 1
