"""「작업본 반영」이 **프레임을 안 보내도 된다** · §6-292

곡선이 있으면 프레임은 파생물이다 · 곡선에서 언제든 같은 값이 다시 나온다 ·
그런데 화면은 반영할 때마다 곡선과 프레임을 **둘 다** 올렸다.

    10분 레이어 = 곡선 800점 + 프레임 30,000개 = 3.7 MB
                  곡선만 보내면            약 60 KB

실측으로 그 3.7 MB 를 만드는 데 90 ms, 서버가 읽는 데 87 ms 가 들었고,
그 위에 검사·저장·합성이 얹혔다.

판정(다시 그릴 수 있나)은 **서버가 한 번** 한다 · 화면은 그 답을 그대로 쓴다 ·
규칙을 두 벌로 두면 언젠가 갈린다.
"""

import math

import pytest

from motion_studio.layer_editor import (
    edit_layer,
    frames_are_curve_derived,
    frames_from_point_curves,
)
from motion_studio.motion_model import normalize_layer


def _recorded(seconds=12.0, axes=('1-1',)):
    n = int(seconds / 0.02)
    return normalize_layer({
        'layer_id': 'L', 'name': 'L',
        'frames': [{
            'frame': i + 1, 'time_sec': round((i + 1) * 0.02, 9),
            'values': {a: 20 * math.sin((i + k) * 0.05) for k, a in enumerate(axes)},
        } for i in range(n)],
    })


def _with_points(layer, motion_id='1-1'):
    return edit_layer(layer, {
        'operation': 'create_axis_point_curve', 'motion_ids': [motion_id],
        'approximation_tolerance_deg': 0.1,
        'approximation_maximum_points': 200,
        'approximation_interpolation_order': 1,
    })


def test_a_recorded_layer_still_needs_its_frames():
    """녹화만 한 축은 곡선이 없다 · 프레임이 원본이라 뺄 수 없다."""
    assert frames_are_curve_derived(_recorded()) is False


def test_a_pointed_layer_can_drop_its_frames():
    assert frames_are_curve_derived(_with_points(_recorded())) is True


def test_one_axis_without_points_still_needs_frames():
    """한 축만 포인트를 만들었으면 아직 통째로 보내야 한다."""
    two = _recorded(axes=('1-1', '1-2'))

    assert frames_are_curve_derived(_with_points(two)) is False


def test_rebuilt_frames_are_exactly_the_same():
    """다시 그린 프레임이 **한 값도 다르지 않아야** 한다.

    화면이 미리보기에서 본 값이 그대로 저장돼야 한다 · 원래 그 프레임도 서버가
    같은 코드로 그려 보낸 것이라 같은 값이 나온다.
    """
    pointed = _with_points(_recorded())

    rebuilt = frames_from_point_curves(pointed)

    assert [(f['time_sec'], f['values']) for f in rebuilt] == [
        (f['time_sec'], f['values']) for f in pointed['frames']
    ]


def test_rebuilding_without_curves_is_refused():
    """곡선이 없으면 다시 그릴 수 없다 · 조용히 빈 레이어를 만들지 않는다."""
    with pytest.raises(ValueError, match='다시 그릴 수 없습니다'):
        frames_from_point_curves(_recorded())


def test_the_check_is_cheap_on_long_data():
    """판정은 곡선을 다시 그리지 않고 구간만 본다 · 10분에서도 빨라야 한다."""
    import time

    long_layer = edit_layer(_recorded(seconds=600.0), {
        'operation': 'create_axis_point_curve', 'motion_ids': ['1-1'],
        'approximation_tolerance_deg': 0.5,
        'approximation_maximum_points': 5000,
        'approximation_interpolation_order': 1,
    })

    started = time.perf_counter()
    frames_are_curve_derived(long_layer)
    elapsed_ms = (time.perf_counter() - started) * 1000

    assert elapsed_ms < 200, f'판정에 {elapsed_ms:.0f} ms 걸렸다'
