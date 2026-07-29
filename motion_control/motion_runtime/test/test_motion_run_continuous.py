import json
import threading
from pathlib import Path

import pytest

from motion_runtime.motion_mapping_manager import MotionMappingManager
from motion_runtime.motion_run_manager import (
    CONTINUOUS_LOOP_TOLERANCE_DEG,
    MotionRunManager,
)


def test_runtime_ignores_optional_studio_editor_metadata_in_motion_header():
    manager = MotionRunManager.__new__(MotionRunManager)
    content = (
        '{"title":"편집 가능 모션","type":"motion_header","rotation_unit":"deg",'
        '"fields":["frame","time_sec","id","value"],'
        '"editor":{"schema_version":1,"layer":{"point_curves":['
        '{"curve_id":"curve_1","motion_id":"1-1","points":[]}]}}}\n'
        '[1,0.02,"1-1",3.5]\n'
    )

    rows, headers = manager._extract_motion_rows(content)

    assert headers == ['frame', 'time_sec', 'id', 'value']
    assert rows == [[1, 0.02, '1-1', 3.5]]


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


def test_motion_run_publishes_final_control_motion_values():
    class CapturePublisher:
        def __init__(self):
            self.messages = []

        def publish(self, message):
            self.messages.append(message)

    manager = MotionRunManager.__new__(MotionRunManager)
    manager._execution_context = {
        'project_id': 'project-1',
        'project_generation': 9,
    }
    manager._motion_value_pub = CapturePublisher()

    manager._publish_motion_values({'2-1': 3.5, 'bad': float('nan')})

    payload = json.loads(manager._motion_value_pub.messages[-1].data)
    assert payload['source'] == 'motion_run'
    assert payload['project_id'] == 'project-1'
    assert payload['project_generation'] == 9
    assert payload['values'] == {'2-1': 3.5}


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


def test_motion_run_initialization_uses_every_enabled_mapping_axis():
    manager = MotionRunManager.__new__(MotionRunManager)
    manager.period_sec = 0.02
    manager._motion_file_path = lambda _file_id: None
    manager._mapping_file_path = lambda _file_id: None
    manager._load_motion_records = lambda _path: [
        {'time_sec': 0.02, 'motion_id': '1-2', 'value': 3.0},
    ]
    manager._load_mapping = lambda _path: {
        'motion_file_id': 'motion.json',
        'mappings': [
            {
                'motion_id': '1-1',
                'motor_axis': 0,
                'initial_mode': 'manual',
                'initial_motion_position_deg': -2.0,
            },
            {
                'motion_id': '1-2',
                'motor_axis': 1,
                'initial_mode': 'manual',
                'initial_motion_position_deg': 4.0,
            },
        ],
    }
    motors = [{'axis': 0}, {'axis': 1}]
    manager._current_motors = lambda: motors
    manager._motor_for_axis = lambda axis, _motors: motors[axis]
    manager._motor_ready_error = lambda _motor: ''
    manager._target_range_limit_error = lambda _motor, _low, _high: ''
    manager._motor_type = lambda _motor: 'ac_servo'

    plan = manager._build_plan({
        'request_source': 'motion_run',
        'motion_file_id': 'motion.json',
        'mapping_file_id': 'mapping.yaml',
        # A client-supplied subset must not weaken whole-mapping initialization.
        'active_motion_ids': ['1-2'],
    }, initialization_only=True)

    assert [axis['motion_id'] for axis in plan['axes']] == ['1-1', '1-2']
    assert [axis['initial_motion_position_deg'] for axis in plan['axes']] == [-2.0, 4.0]
    assert 'Motion ID 1-1: 모션 데이터가 없어 수동 초기위치 -2.000°를 사용' in plan['warnings']


def test_motion_run_initialization_fails_when_any_mapping_axis_is_not_ready():
    manager = MotionRunManager.__new__(MotionRunManager)
    manager.period_sec = 0.02
    manager._motion_file_path = lambda _file_id: None
    manager._mapping_file_path = lambda _file_id: None
    manager._load_motion_records = lambda _path: [
        {'time_sec': 0.02, 'motion_id': '1-1', 'value': 0.0},
        {'time_sec': 0.02, 'motion_id': '1-2', 'value': 0.0},
    ]
    manager._load_mapping = lambda _path: {
        'motion_file_id': 'motion.json',
        'mappings': [
            {'motion_id': '1-1', 'motor_axis': 0},
            {'motion_id': '1-2', 'motor_axis': 1},
        ],
    }
    motors = [{'axis': 0}, {'axis': 1}]
    manager._current_motors = lambda: motors
    manager._motor_for_axis = lambda axis, _motors: motors[axis]
    manager._motor_ready_error = lambda motor: (
        'Axis 1 servo is off' if motor['axis'] == 1 else ''
    )
    manager._target_range_limit_error = lambda _motor, _low, _high: ''
    manager._motor_type = lambda _motor: 'ac_servo'

    with pytest.raises(ValueError, match='Motion ID 1-2: Axis 1 servo is off'):
        manager._build_plan({
            'request_source': 'motion_run',
            'motion_file_id': 'motion.json',
            'mapping_file_id': 'mapping.yaml',
        }, initialization_only=True)


def test_motion_run_playback_uses_only_motion_ids_present_in_file():
    manager = MotionRunManager.__new__(MotionRunManager)
    manager.period_sec = 0.02
    manager._motion_file_path = lambda _file_id: None
    manager._mapping_file_path = lambda _file_id: None
    manager._load_motion_records = lambda _path: [
        {'time_sec': 0.02, 'motion_id': '1-2', 'value': 0.0},
        {'time_sec': 0.04, 'motion_id': '1-2', 'value': 2.0},
    ]
    manager._load_mapping = lambda _path: {
        'motion_file_id': 'motion.json',
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
    manager._motor_type = lambda _motor: 'ac_servo'

    plan = manager._build_plan({
        'request_source': 'motion_run',
        'motion_file_id': 'motion.json',
        'mapping_file_id': 'mapping.yaml',
    })

    assert [axis['motion_id'] for axis in plan['axes']] == ['1-2']
    assert plan['samples'][0]['positions'] == {1: 10.0}
    assert plan['samples'][-1]['positions'] == {1: 12.0}


def test_auto_start_runs_motion_only_after_initialization_completes():
    manager = MotionRunManager.__new__(MotionRunManager)
    manager._stop_event = threading.Event()
    current = {'state': 'idle'}
    calls = []
    manager.status = lambda: dict(current)

    def initialize(plan):
        calls.append(('initialize', plan['name']))
        current['state'] = 'initialized'

    manager._run_initialization = initialize
    manager._run_countdown = lambda _plan: True
    manager._run_motion = lambda plan: calls.append(('motion', plan['name']))

    manager._run_initialization_then_motion(
        {'name': 'all-mapping-axes'},
        {'name': 'file-motion-axes'},
    )

    assert calls == [
        ('initialize', 'all-mapping-axes'),
        ('motion', 'file-motion-axes'),
    ]


def test_auto_start_does_not_run_motion_when_initialization_fails():
    manager = MotionRunManager.__new__(MotionRunManager)
    manager._stop_event = threading.Event()
    current = {'state': 'idle'}
    calls = []
    manager.status = lambda: dict(current)

    def initialize(_plan):
        calls.append('initialize')
        current['state'] = 'error'

    manager._run_initialization = initialize
    manager._run_countdown = lambda _plan: True
    manager._run_motion = lambda _plan: calls.append('motion')

    manager._run_initialization_then_motion({}, {})

    assert calls == ['initialize']


def test_start_routes_one_owned_initialization_and_motion_sequence(monkeypatch):
    manager = MotionRunManager.__new__(MotionRunManager)
    manager._run_lock = threading.RLock()
    manager._run_thread = None
    manager._stop_event = threading.Event()
    manager._graceful_stop_event = threading.Event()
    manager._playback_ownership_error = lambda: ''
    manager.status = lambda: {'state': 'initialized'}
    manager._build_plan = lambda _payload, initialization_only=False: {
        'name': 'initialization' if initialization_only else 'motion',
        'run_mode': 'once',
        'summary': {},
    }
    manager._motion_auto_start_guard_error = lambda _plan: ''
    calls = []
    manager._run_initialization_then_motion = lambda initialization, motion: calls.append(
        ('initialize_then_motion', initialization['name'], motion['name'])
    )

    class ImmediateThread:
        def __init__(self, *, target, args, daemon):
            self._target = target
            self._args = args
            self.daemon = daemon

        def is_alive(self):
            return False

        def start(self):
            self._target(*self._args)

    monkeypatch.setattr(threading, 'Thread', ImmediateThread)

    result = manager._start_thread('run', {})

    assert result['success'] is True
    assert calls == [('initialize_then_motion', 'initialization', 'motion')]


def test_owned_sequence_runs_countdown_between_initialization_and_motion():
    manager = MotionRunManager.__new__(MotionRunManager)
    manager._stop_event = threading.Event()
    current = {'state': 'idle'}
    calls = []
    manager.status = lambda: dict(current)

    def initialize(_plan):
        calls.append('initialize')
        current['state'] = 'initialized'

    manager._run_initialization = initialize
    manager._run_countdown = lambda _plan: calls.append('countdown') or True
    manager._run_motion = lambda _plan: calls.append('motion')

    manager._run_initialization_then_motion({}, {})

    assert calls == ['initialize', 'countdown', 'motion']


def test_countdown_stop_prevents_motion_start():
    manager = MotionRunManager.__new__(MotionRunManager)
    manager._run_lock = threading.RLock()
    manager._status = manager._empty_status()
    manager._publish_status = lambda: None
    manager._stop_event = threading.Event()
    manager._stop_event.set()

    result = manager._run_countdown({
        'countdown_sec': 3.0,
        'axes': [],
    })

    assert result is False
    assert manager._status['state'] == 'stopped'


def test_auto_start_rejects_unsafe_continuous_motion_before_initialization():
    reason = MotionRunManager._motion_auto_start_guard_error({
        'run_mode': 'continuous',
        'capabilities': {
            'continuous_run': {
                'available': False,
                'reason': '시작·종료값 차이 초과',
            },
        },
    })

    assert reason == '시작·종료값 차이 초과'
    assert MotionRunManager._motion_auto_start_guard_error({
        'run_mode': 'once',
        'capabilities': {},
    }) == ''


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
