"""축별 재생 종료 · 오버더빙 (a) · §6-73

축이 끝났는데도 재생이 계속 명령하면 `CommandArbiter` 가 그 축을 계속 쥐고 있어
MIDI 가 영영 못 들어온다 · 오버더빙이 성립하지 않는다.

`plan_builder` 의 샘플 계산은 그대로 두고 **발행 직전에만** 거른다 ·
로컬·그룹 실행은 `axis_release_sec` 를 주지 않으므로 아무 영향이 없다.
"""

from motion_runtime.motion_player import MotionPlayer
from motion_runtime.plan_builder import _axis_release_sec


_filter = MotionPlayer._positions_after_release


def test_without_a_release_map_nothing_is_filtered():
    """로컬·그룹 실행은 이 값을 주지 않는다 · 지금과 똑같이 돌아야 한다."""
    positions = {0: 1.0, 1: 2.0}
    assert _filter({}, positions, 5.0) == positions
    assert _filter({'axis_release_sec': {}}, positions, 5.0) == positions


def test_an_axis_is_dropped_after_its_release_time():
    plan = {'axis_release_sec': {0: 10.0}}
    assert _filter(plan, {0: 1.0, 1: 2.0}, 9.99) == {0: 1.0, 1: 2.0}
    assert _filter(plan, {0: 1.0, 1: 2.0}, 10.0) == {0: 1.0, 1: 2.0}
    assert _filter(plan, {0: 1.0, 1: 2.0}, 10.02) == {1: 2.0}


def test_axes_are_released_independently():
    """축마다 끝나는 시각이 다르다 · 먼저 끝난 축부터 MIDI 가 가져간다."""
    plan = {'axis_release_sec': {0: 5.0, 1: 20.0}}
    assert _filter(plan, {0: 1.0, 1: 2.0, 2: 3.0}, 10.0) == {1: 2.0, 2: 3.0}


def test_an_axis_without_an_entry_is_never_released():
    """종료 시각이 없는 축은 재생이 끝까지 몬다."""
    plan = {'axis_release_sec': {0: 1.0}}
    assert _filter(plan, {0: 1.0, 7: 9.0}, 100.0) == {7: 9.0}


def test_motion_ids_are_translated_to_motor_axes():
    """부르는 쪽은 모션 ID 로 말하고 발행부는 모터축으로 움직인다."""
    axes = [
        {'motion_id': '1-1', 'motor_axis': 0},
        {'motion_id': '1-2', 'motor_axis': 3},
    ]
    payload = {'axis_release_sec': {'1-1': 10.0, '9-9': 5.0}}
    assert _axis_release_sec(payload, axes) == {0: 10.0}


def test_a_missing_or_bad_release_request_is_ignored():
    axes = [{'motion_id': '1-1', 'motor_axis': 0}]
    assert _axis_release_sec({}, axes) == {}
    assert _axis_release_sec({'axis_release_sec': None}, axes) == {}
    assert _axis_release_sec({'axis_release_sec': {'1-1': 'x'}}, axes) == {}
    assert _axis_release_sec({'axis_release_sec': {'1-1': -1}}, axes) == {}
