import json
import threading
from pathlib import Path

import pytest

from motion_runtime.motion_mapping_manager import MotionMappingManager
from motion_runtime.motion_run_manager import (
    CONTINUOUS_LOOP_TOLERANCE_DEG,
    MotionRunManager,
)


def test_motion_run_confirmation_returns_standard_context_acknowledgement():
    manager = MotionRunManager.__new__(MotionRunManager)
    manager._run_lock = threading.RLock()
    manager._execution_context_ready = False
    manager._execution_context = {
        'context_id': 'context-1',
        'project_id': 'project-1',
        'mapping_file_id': 'mapping.yaml',
        'mapping_sha256': 'mapping-sha',
    }

    result = manager._confirm_execution_context({'context_id': 'context-1'})

    assert result['success'] is True
    assert result['context_id'] == 'context-1'
    assert result['project_id'] == 'project-1'
    assert manager._execution_context_ready is True


def test_continuous_capability_accepts_values_inside_axis_tolerances():
    capability = MotionRunManager._continuous_capability([
        {
            'motor_axis': 0,
            'loop_delta_deg': 4.9,
            'loop_tolerance_deg': 5.0,
        },
        {
            'motor_axis': 1,
            'loop_delta_deg': 5.0,
            'loop_tolerance_deg': 5.0,
        },
    ])

    assert capability['available'] is True
    assert '5° 이내' in capability['reason']


def test_four_degree_motion_seam_is_allowed_even_if_motor_delta_is_large():
    capability = MotionRunManager._continuous_capability([{
        'motor_axis': 0,
        'loop_delta_deg': 4.0,
        'loop_motor_delta_deg': 400.0,
        'loop_tolerance_deg': 5.0,
    }])

    assert capability['available'] is True


def test_continuous_loop_tolerance_is_five_degrees():
    assert CONTINUOUS_LOOP_TOLERANCE_DEG == 5.0


def test_motion_value_clamps_to_mapping_min_and_max():
    assert MotionRunManager._clamp_motion_value(-35.0, -30.0, 30.0) == -30.0
    assert MotionRunManager._clamp_motion_value(12.0, -30.0, 30.0) == 12.0
    assert MotionRunManager._clamp_motion_value(35.0, -30.0, 30.0) == 30.0


def test_legacy_initial_disabled_setting_is_ignored():
    manager = MotionRunManager.__new__(MotionRunManager)

    initial = manager._initial_motion_value({
        'initial_enabled': False,
        'initial_mode': 'manual',
        'initial_motion_position_deg': 12.5,
    }, [{'value': -3.0}])

    assert initial == 12.5


def test_legacy_initial_disabled_mapping_keeps_initial_settings_and_drops_option():
    manager = MotionMappingManager.__new__(MotionMappingManager)

    normalized = manager._normalize_mapping({
        'name': 'legacy',
        'mappings': [{
            'motion_id': '1-1',
            'initial_enabled': False,
            'initial_mode': 'manual',
            'initial_motion_position_deg': 12.5,
            'initial_move_time_sec': 5.0,
        }],
    })

    row = normalized['mappings'][0]
    assert 'initial_enabled' not in row
    assert row['initial_motion_position_deg'] == 12.5
    assert row['initial_move_time_sec'] == 5.0


def test_continuous_capability_rejects_only_continuous_mode_on_seam_mismatch():
    capability = MotionRunManager._continuous_capability([
        {
            'motor_axis': 2,
            'loop_delta_deg': 5.001,
            'loop_tolerance_deg': 5.0,
        },
    ])

    assert capability['available'] is False
    assert 'Axis 2' in capability['reason']
    assert '5.001°' in capability['reason']


def test_failed_readiness_marks_all_actions_unavailable():
    capabilities = MotionRunManager._unavailable_capabilities('모터 연결 끊김')

    assert all(item['available'] is False for item in capabilities.values())
    assert all(item['reason'] == '모터 연결 끊김' for item in capabilities.values())


def test_motor_alarm_is_reported_even_when_fault_flag_is_missing():
    manager = MotionRunManager.__new__(MotionRunManager)
    error = manager._motor_ready_error({
        'controller_index': 0,
        'state': 'detected',
        'motor_type': 'AC Servo',
        'servo_on': True,
        'fault': False,
        'errorcode': 21,
        'errorcode_hex': '0xFF15',
        'error_text': 'Error 21.0',
    })

    assert error == 'Axis 0 motor alarm 0xFF15 (Error 21.0)'


def test_motor_target_applies_reference_scale_direction_and_gear_ratio():
    manager = MotionRunManager.__new__(MotionRunManager)
    row = {
        'reference_position_deg': 10.0,
        'offset_deg': 2.0,
        'scale': 1.5,
        'invert': True,
        'gear_ratio': 2.0,
    }

    assert manager._motor_target(row, 3.0) == -5.0


def _initialization_only_manager(mapping):
    manager = MotionRunManager.__new__(MotionRunManager)
    manager.period_sec = 0.02
    manager._mapping_file_path = lambda _file_id: None
    manager._load_mapping = lambda _path: mapping
    manager._current_motors = lambda: [{'axis': 0}]
    manager._motor_for_axis = lambda _axis, motors: motors[0]
    manager._motor_ready_error = lambda _motor: ''
    manager._target_range_limit_error = lambda _motor, _low, _high: ''
    manager._motor_type = lambda _motor: 'ac_servo'
    return manager


def test_first_frame_initialization_without_motion_file_uses_motion_zero():
    manager = _initialization_only_manager({
        'motion_file_id': '',
        'mappings': [{
            'motion_id': '1-1',
            'motor_axis': 0,
            'reference_position_deg': 100.0,
            'motion_lower_deg': -30.0,
            'motion_upper_deg': 30.0,
            'initial_mode': 'first_frame',
            'initial_move_time_sec': 5.0,
            'gear_ratio': 50.0,
        }],
    })

    plan = manager._build_plan(
        {'motion_file_id': '', 'mapping_file_id': 'mapping.yaml'},
        initialization_only=True,
    )

    axis = plan['axes'][0]
    assert axis['initial_motion_source_position_deg'] == 0.0
    assert axis['initial_motion_position_deg'] == 0.0
    assert axis['initial_motor_target_deg'] == 100.0
    assert plan['capabilities']['single_run']['available'] is False
    assert '첫 프레임 데이터가 없어 모션 0°' in plan['warnings'][0]


def test_motion_playback_without_motion_file_remains_blocked():
    manager = _initialization_only_manager({'motion_file_id': '', 'mappings': []})

    with pytest.raises(ValueError, match='motion file_id is required'):
        manager._build_plan({
            'motion_file_id': '',
            'mapping_file_id': 'mapping.yaml',
        })


def test_zero_fallback_outside_motion_range_blocks_initialization():
    manager = _initialization_only_manager({
        'motion_file_id': '',
        'mappings': [{
            'motion_id': '1-1',
            'motor_axis': 0,
            'reference_position_deg': 100.0,
            'motion_lower_deg': 10.0,
            'motion_upper_deg': 30.0,
            'initial_mode': 'first_frame',
            'initial_move_time_sec': 5.0,
        }],
    })

    with pytest.raises(ValueError, match='초기 모션값 0.000°가 모션 설정 범위 밖'):
        manager._build_plan(
            {'motion_file_id': '', 'mapping_file_id': 'mapping.yaml'},
            initialization_only=True,
        )


def test_plan_keeps_single_run_available_when_continuous_seam_fails():
    manager = MotionRunManager.__new__(MotionRunManager)
    manager.period_sec = 0.02
    manager._motion_file_path = lambda _file_id: None
    manager._mapping_file_path = lambda _file_id: None
    manager._load_motion_records = lambda _path: [
        {'time_sec': 0.0, 'motion_id': 'joint', 'value': 0.0},
        {'time_sec': 1.0, 'motion_id': 'joint', 'value': 6.0},
    ]
    manager._load_mapping = lambda _path: {
        'motion_file_id': 'motion.json',
        'mappings': [{
            'motion_id': 'joint',
            'motor_axis': 0,
            'reference_position_deg': 10.0,
            # A legacy false value must not bypass mandatory initialization.
            'initial_enabled': False,
        }],
    }
    manager._current_motors = lambda: [{'axis': 0}]
    manager._motor_for_axis = lambda _axis, motors: motors[0]
    manager._motor_ready_error = lambda _motor: ''
    manager._target_range_limit_error = lambda _motor, _low, _high: ''
    manager._motor_type = lambda _motor: 'ac_servo'
    plan = manager._build_plan({
        'motion_file_id': 'motion.json',
        'mapping_file_id': 'mapping.yaml',
    })

    assert plan['capabilities']['initial_position']['available'] is True
    assert plan['capabilities']['single_run']['available'] is True
    assert plan['capabilities']['continuous_run']['available'] is False
    assert plan['axes'][0]['loop_start_motion_deg'] == 0.0
    assert plan['axes'][0]['loop_end_motion_deg'] == 6.0
    assert plan['axes'][0]['loop_start_target_deg'] == 10.0
    assert plan['axes'][0]['loop_end_target_deg'] == 16.0
    assert plan['axes'][0]['loop_delta_deg'] == 6.0
    assert plan['axes'][0]['loop_motor_delta_deg'] == 6.0
    assert plan['axes'][0]['loop_tolerance_deg'] == 5.0


def test_plan_resolves_current_axis_from_stable_alias_instead_of_saved_axis():
    manager = MotionRunManager.__new__(MotionRunManager)
    manager.period_sec = 0.02
    manager._motion_file_path = lambda _file_id: None
    manager._mapping_file_path = lambda _file_id: None
    manager._load_motion_records = lambda _path: [
        {'time_sec': 0.0, 'motion_id': 'joint', 'value': 0.0},
        {'time_sec': 0.02, 'motion_id': 'joint', 'value': 1.0},
    ]
    manager._load_mapping = lambda _path: {
        'motion_file_id': 'motion.json',
        'mappings': [{
            'motion_id': 'joint',
            'motor_ref': 'ac_servo:alias:101',
            'motor_axis': 0,
            'reference_position_deg': 0.0,
        }],
    }
    manager._current_motors = lambda: [{
        'controller_index': 5,
        'motor_type': 'ac_servo',
        'alias': 101,
    }]
    manager._motor_ready_error = lambda _motor: ''
    manager._target_range_limit_error = lambda _motor, _low, _high: ''

    plan = manager._build_plan({
        'motion_file_id': 'motion.json',
        'mapping_file_id': 'mapping.yaml',
    })

    assert plan['axes'][0]['motor_axis'] == 5
    assert 5 in plan['samples'][0]['positions']
    assert 0 not in plan['samples'][0]['positions']


def test_plan_runs_with_out_of_range_data_and_clamps_every_command():
    manager = MotionRunManager.__new__(MotionRunManager)
    manager.period_sec = 0.5
    manager._motion_file_path = lambda _file_id: None
    manager._mapping_file_path = lambda _file_id: None
    manager._load_motion_records = lambda _path: [
        {'time_sec': 0.0, 'motion_id': 'joint', 'value': -35.0},
        {'time_sec': 0.5, 'motion_id': 'joint', 'value': 0.0},
        {'time_sec': 1.0, 'motion_id': 'joint', 'value': 35.0},
    ]
    manager._load_mapping = lambda _path: {
        'motion_file_id': 'motion.json',
        'mappings': [{
            'motion_id': 'joint',
            'motor_axis': 0,
            'reference_position_deg': 0.0,
            'motion_lower_deg': -30.0,
            'motion_upper_deg': 30.0,
            'initial_enabled': True,
            'initial_mode': 'first_frame',
        }],
    }
    manager._current_motors = lambda: [{'axis': 0}]
    manager._motor_for_axis = lambda _axis, motors: motors[0]
    manager._motor_ready_error = lambda _motor: ''
    manager._target_range_limit_error = lambda _motor, _low, _high: ''
    manager._motor_type = lambda _motor: 'ac_servo'

    plan = manager._build_plan({
        'motion_file_id': 'motion.json',
        'mapping_file_id': 'mapping.yaml',
    })
    axis = plan['axes'][0]
    commanded = [sample['positions'][0] for sample in plan['samples']]

    assert plan['capabilities']['initial_position']['available'] is True
    assert plan['capabilities']['single_run']['available'] is True
    assert len(plan['warnings']) == 2
    assert axis['motion_clamped'] is True
    assert axis['initial_motion_source_position_deg'] == -35.0
    assert axis['initial_motion_position_deg'] == -30.0
    assert axis['initial_motor_target_deg'] == -30.0
    assert min(commanded) == -30.0
    assert max(commanded) == 30.0


def test_motion_studio_can_use_read_only_mapping_with_generated_preview_file():
    manager = MotionRunManager.__new__(MotionRunManager)
    manager.period_sec = 0.02
    manager._motion_file_path = lambda _file_id: None
    manager._mapping_file_path = lambda _file_id: None
    manager._load_motion_records = lambda _path: [
        {'time_sec': 0.02, 'motion_id': '1-2', 'value': 0.0},
        {'time_sec': 0.04, 'motion_id': '1-2', 'value': 2.0},
    ]
    manager._load_mapping = lambda _path: {
        'motion_file_id': 'original.json',
        'mappings': [
            {'motion_id': '1-1', 'motor_axis': 0, 'reference_position_deg': 0.0},
            {'motion_id': '1-2', 'motor_axis': 1, 'reference_position_deg': 10.0},
        ],
    }
    motors = [{'axis': 0}, {'axis': 1}]
    manager._current_motors = lambda: motors
    manager._motor_for_axis = lambda axis, _motors: motors[axis]
    manager._motor_ready_error = lambda _motor: ''
    manager._target_range_limit_error = lambda _motor, _low, _high: ''
    manager._motor_type = lambda _motor: 'dynamixel'

    plan = manager._build_plan({
        'request_source': 'motion_studio',
        'motion_file_id': '__studio_preview.json',
        'mapping_file_id': 'mapping.yaml',
        'active_motion_ids': ['1-2'],
    })

    assert plan['request_source'] == 'motion_studio'
    assert [axis['motion_id'] for axis in plan['axes']] == ['1-2']
    assert plan['samples'][-1]['positions'] == {1: 12.0}


def test_normal_motion_run_still_rejects_mapping_file_mismatch():
    manager = MotionRunManager.__new__(MotionRunManager)
    manager.period_sec = 0.02
    manager._motion_file_path = lambda _file_id: None
    manager._mapping_file_path = lambda _file_id: None
    manager._load_motion_records = lambda _path: [
        {'time_sec': 0.02, 'motion_id': '1-1', 'value': 0.0},
    ]
    manager._load_mapping = lambda _path: {
        'motion_file_id': 'original.json',
        'mappings': [{'motion_id': '1-1', 'motor_axis': 0}],
    }

    try:
        manager._build_plan({
            'motion_file_id': 'different.json',
            'mapping_file_id': 'mapping.yaml',
        })
    except ValueError as exc:
        assert 'expects motion file original.json' in str(exc)
    else:
        raise AssertionError('normal motion run must preserve strict mapping-file binding')


def test_run_manager_resolves_assets_only_inside_requested_project(tmp_path):
    root = (tmp_path / 'motion_projects').resolve()
    for project_id, marker in (('first', 'one'), ('second', 'two')):
        project_dir = root / project_id
        (project_dir / 'motions').mkdir(parents=True)
        (project_dir / 'motion_axis_matching').mkdir()
        (project_dir / 'project.json').write_text(
            json.dumps({'project_id': project_id}), encoding='utf-8'
        )
        (project_dir / 'motions' / 'same.json').write_text(marker, encoding='utf-8')
        (project_dir / 'motion_axis_matching' / 'same.yaml').write_text(
            marker, encoding='utf-8'
        )

    manager = MotionRunManager.__new__(MotionRunManager)
    manager.motion_projects_dir = root
    first = manager._project_asset_dirs({'project_id': 'first'})
    second = manager._project_asset_dirs({'project_id': 'second'})

    assert first[0] == 'first'
    assert second[0] == 'second'
    assert manager._motion_file_path('same.json', first[1]).read_text() == 'one'
    assert manager._motion_file_path('same.json', second[1]).read_text() == 'two'
    assert manager._mapping_file_path('same.yaml', first[2]).read_text() == 'one'
    assert manager._mapping_file_path('same.yaml', second[2]).read_text() == 'two'
