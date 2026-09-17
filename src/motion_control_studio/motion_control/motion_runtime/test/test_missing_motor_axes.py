"""모터가 빠진 축이 있어도 남은 축은 돈다 · §6-139

로보티즈 2축을 떼자 3축 모션이 통째로 거부됐다.

    모션 실행 준비 실패:
      Motion ID 1-2: Motor dynamixel:...id:3 not found
      Motion ID 1-3: Motor dynamixel:...id:5 not found
      requested Motion ID is unavailable: 1-2, 1-3

1-1 은 멀쩡한 AC 서보인데 같이 죽었다 · 축 하나를 떼면 그 축만 빠져야지
모션 전체가 못 돌 이유가 없다.

같은 생각이 이미 위에 있다 · 「이 PC 의 모션축 설정에 없는 축은 읽자마자
버린다」 (§6-106 연동) · 연동은 원래 한 모션 파일을 여러 PC 가 나눠 갖는다 ·
모터가 빠진 것도 같은 모양이다.

다만 **조용히 넘어가지는 않는다** · 3축짜리 모션이 1축만 도는 걸 모르면 안 된다.
"""

import threading
from unittest import mock

import pytest

from motion_runtime import motion_run_rules
from motion_runtime.motion_player import MotionPlayer
from motion_runtime.motion_run_manager import MotionRunManager
from motion_runtime.plan_builder import PlanBuilder


def _patch_rule(name, value):
    mock.patch.object(motion_run_rules, name, value).start()


@pytest.fixture(autouse=True)
def _restore_patched_rules():
    yield
    mock.patch.stopall()


def _row(motion_id, motor_ref, **overrides):
    row = {
        'motion_id': motion_id,
        'enabled': True,
        'motor_ref': motor_ref,
        'reference_enabled': True,
        'reference_position_deg': 0.0,
        'motion_lower_deg': -180.0,
        'motion_upper_deg': 180.0,
        'initial_mode': 'first_frame',
        'initial_move_time_sec': 5.0,
        'invert': False,
        'offset_deg': 0.0,
        'scale': 1.0,
        'gear_ratio': 1.0,
    }
    row.update(overrides)
    return row


def _manager(mapping, present_motors):
    """`present_motors` 에 있는 `motor_ref` 만 이 PC 에 달려 있다."""
    manager = MotionRunManager.__new__(MotionRunManager)
    manager._player = MotionPlayer(manager)
    manager._plan_builder = PlanBuilder(manager)
    manager._run_lock = threading.RLock()
    manager.period_sec = 0.02
    manager._mapping_file_path = lambda _file_id: None
    manager._load_mapping = lambda _path: mapping
    manager._motion_file_path = lambda _file_id: 'motion.json'
    manager._current_motors = lambda: [
        {'axis': index, 'controller_index': index, 'motor_ref': ref}
        for index, ref in enumerate(present_motors)
    ]
    manager._motor_for_axis = lambda axis, motors: next(
        (motor for motor in motors if motor['controller_index'] == axis), None
    )
    _patch_rule('_motor_ready_error', lambda _motor: '')
    _patch_rule('_target_range_limit_error', lambda _motor, _low, _high: '')
    _patch_rule('_motor_type', lambda _motor: 'ac_servo')
    _patch_rule(
        '_motors_for_ref',
        lambda ref, motors: [m for m in motors if m.get('motor_ref') == ref],
    )
    manager._load_motion_records = lambda _path: [
        {'motion_id': motion_id, 'time_sec': time_sec, 'value': 0.0}
        for time_sec in (0.02, 0.04)
        for motion_id in ('1-1', '1-2', '1-3')
    ]
    return manager


ALL_THREE = {
    'motion_file_id': 'motion.json',
    'mappings': [
        _row('1-1', 'ac_servo:master:0:alias:103'),
        _row('1-2', 'dynamixel:id:3'),
        _row('1-3', 'dynamixel:id:5'),
    ],
}


def _build(manager, **payload):
    return manager._plan_builder.build({
        'motion_file_id': 'motion.json',
        'mapping_file_id': 'mapping.yaml',
        **payload,
    })


def test_every_motor_present_plans_every_axis():
    manager = _manager(ALL_THREE, [
        'ac_servo:master:0:alias:103', 'dynamixel:id:3', 'dynamixel:id:5',
    ])

    plan = _build(manager)

    assert sorted(axis['motion_id'] for axis in plan['axes']) == ['1-1', '1-2', '1-3']
    assert plan['warnings'] == []


def test_a_missing_motor_does_not_block_the_rest():
    """로보티즈 2축을 뗀 상황 · 1-1 은 그대로 돌아야 한다."""
    manager = _manager(ALL_THREE, ['ac_servo:master:0:alias:103'])

    plan = _build(manager)

    assert [axis['motion_id'] for axis in plan['axes']] == ['1-1']


def test_the_skipped_axes_are_reported():
    """조용히 넘어가면 3축 모션이 1축만 도는 걸 모른다."""
    manager = _manager(ALL_THREE, ['ac_servo:master:0:alias:103'])

    plan = _build(manager)

    skipped = ' '.join(plan['warnings'])
    assert '1-2' in skipped and '1-3' in skipped
    assert '모터' in skipped


def test_no_motor_at_all_is_still_an_error():
    """전부 없으면 돌릴 것이 없다 · 이건 알려야 한다."""
    manager = _manager(ALL_THREE, ['ac_servo:master:0:alias:999'])

    with pytest.raises(ValueError, match='mapping'):
        _build(manager)


def test_initialization_also_skips_missing_motors():
    """초기 위치 이동도 있는 축만 움직인다."""
    manager = _manager(ALL_THREE, ['ac_servo:master:0:alias:103'])

    plan = manager._plan_builder.build(
        {'motion_file_id': 'motion.json', 'mapping_file_id': 'mapping.yaml'},
        initialization_only=True,
    )

    assert [axis['motion_id'] for axis in plan['axes']] == ['1-1']


MOTION_ONLY_TWO = {
    'motion_file_id': 'motion.json',
    'mappings': [
        _row('1-1', 'ac_servo:master:0:alias:103'),
        _row('1-2', 'dynamixel:id:3'),
        _row('1-4', 'dynamixel:id:9'),
    ],
}


def test_an_axis_missing_from_the_motion_file_is_skipped_too():
    """모터는 달렸는데 모션 파일에 그 축이 없는 경우 · §6-142

    모터가 없을 때와 같은 일이다 · 축 하나가 비었다고 나머지가 못 돌 이유가
    없다 · 전에는 `motion file data not found` 로 전체를 막았다.

    레이어 재생(`motion_studio`)이 이 길로 온다 · 일반 모션 실행은 모션
    파일에 있는 축만 「요구한 축」으로 삼아 애초에 여기까지 오지 않는다.
    """
    manager = _manager(MOTION_ONLY_TWO, [
        'ac_servo:master:0:alias:103', 'dynamixel:id:3', 'dynamixel:id:9',
    ])

    plan = _build(manager, request_source='motion_studio')

    assert sorted(axis['motion_id'] for axis in plan['axes']) == ['1-1', '1-2']
    assert any('1-4' in text for text in plan['warnings'])


def test_a_loop_value_gap_does_not_block_continuous_play():
    """이음매에서 값이 튀어도 막지 않는다 · 알리기만 한다 · §6-142"""
    manager = _manager(ALL_THREE, [
        'ac_servo:master:0:alias:103', 'dynamixel:id:3', 'dynamixel:id:5',
    ])
    manager._load_motion_records = lambda _path: [
        {'motion_id': motion_id, 'time_sec': time_sec, 'value': value}
        for time_sec, value in ((0.02, 0.0), (0.04, 30.0))
        for motion_id in ('1-1', '1-2', '1-3')
    ]

    plan = _build(manager, run_mode='continuous', repeat_mode='direct')

    assert len(plan['axes']) == 3, '값이 튄다고 실행을 막으면 안 된다'
    assert any('튑니다' in text for text in plan['warnings'])
