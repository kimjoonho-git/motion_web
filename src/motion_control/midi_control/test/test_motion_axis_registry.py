import os
from pathlib import Path

from midi_control.motion_axis_registry import MotionAxisRegistry


def write_mapping(path: Path, rows: str) -> None:
    path.write_text(f'mappings:\n{rows}', encoding='utf-8')


def test_registry_uses_preferred_mapping_and_only_enabled_assigned_axes(tmp_path):
    write_mapping(
        tmp_path / 'older.yaml',
        '- motion_id: 1-1\n  enabled: true\n  motor_axis: 1\n',
    )
    write_mapping(
        tmp_path / 'selected.yaml',
        '- motion_id: 4-3\n  enabled: true\n  motor_axis: 2\n'
        '- motion_id: 4-4\n  enabled: false\n  motor_axis: 3\n'
        '- motion_id: 4-5\n  enabled: true\n  motor_axis: null\n',
    )

    registry = MotionAxisRegistry(tmp_path)
    registry.refresh('selected.yaml')

    assert registry.file_id == 'selected.yaml'
    assert registry.motor_axis('4-3') == 2
    assert registry.mapping('4-3')['motor_axis'] == 2
    assert registry.motor_axis('1-1') is None
    assert registry.motor_axis('4-4') is None
    assert registry.motor_axis('4-5') is None


def test_registry_falls_back_to_newest_mapping(tmp_path):
    older = tmp_path / 'older.yaml'
    newer = tmp_path / 'newer.yaml'
    write_mapping(older, '- motion_id: 1-1\n  enabled: true\n  motor_axis: 1\n')
    write_mapping(newer, '- motion_id: 2-1\n  enabled: true\n  motor_axis: 4\n')
    os.utime(older, (100, 100))
    os.utime(newer, (200, 200))

    registry = MotionAxisRegistry(tmp_path)
    registry.refresh('missing.yaml')

    assert registry.file_id == 'newer.yaml'
    assert registry.motor_axis('2-1') == 4


def test_registry_resolves_stable_motor_ref_after_controller_index_changes(tmp_path):
    write_mapping(
        tmp_path / 'stable.yaml',
        '- motion_id: 1-1\n'
        '  enabled: true\n'
        '  motor_ref: ac_servo:alias:101\n'
        '  motor_axis: 0\n'
        '- motion_id: 1-2\n'
        '  enabled: true\n'
        '  motor_ref: dynamixel:id:3\n'
        '  motor_axis: 2\n',
    )
    state = {
        'motors': [
            {
                'controller_index': 4,
                'motor_type': 'ac_servo',
                'alias': 101,
            },
            {
                'controller_index': 1,
                'motor_type': 'dynamixel',
                'bus_id': 3,
            },
        ],
    }

    registry = MotionAxisRegistry(tmp_path)
    registry.refresh('stable.yaml', state)

    assert registry.motor_axis('1-1') == 4
    assert registry.motor_axis('1-2') == 1


def test_registry_refresh_reloads_changed_gear_ratio(tmp_path):
    mapping = tmp_path / 'selected.yaml'
    write_mapping(
        mapping,
        '- motion_id: 1-1\n'
        '  enabled: true\n'
        '  motor_axis: 0\n'
        '  gear_ratio: 1.0\n',
    )
    registry = MotionAxisRegistry(tmp_path)
    registry.refresh('selected.yaml')
    assert registry.mapping('1-1')['gear_ratio'] == 1.0

    write_mapping(
        mapping,
        '- motion_id: 1-1\n'
        '  enabled: true\n'
        '  motor_axis: 0\n'
        '  gear_ratio: 50.0\n',
    )
    registry.refresh('selected.yaml')

    assert registry.mapping('1-1')['gear_ratio'] == 50.0
