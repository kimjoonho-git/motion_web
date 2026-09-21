"""구간 복사는 **붙인 자리의 곡선**에 넣는다 · §6-256

한 축에 곡선이 여럿일 수 있다(겹치지만 않으면 된다) · 합친 레이어의 빈 구간을
따로 채우면서 1-2 축은 곡선이 셋이 됐다.

전에는 포인트를 떠 온 곡선에 도로 넣었다 · 그래서 앞 곡선(0.02~12.98)의
포인트를 30초에 붙이면 그 곡선이 0.02~42.96 으로 늘어나 가운데
곡선(13.00~53.38)을 통째로 덮었다 · 덮인 곡선은 그대로 남고 프레임만
사라져서 미리보기·반영은 멀쩡했고 **저장할 때** 터졌다.

    저장 실패 · 1-2 포인트 곡선과 20ms 프레임이 다릅니다

실측으로 어긋난 표본이 1499개였다.
"""

import copy

import pytest

from motion_studio.layer_editor import edit_layer
from motion_studio.layer_validation import point_curve_frame_mismatches

PERIOD = 0.02


def _points(start_sec, values):
    return [
        {
            'point_id': f'p{index}',
            'time_sec': round(start_sec + index * PERIOD, 9),
            'value_deg': float(value),
            'tangent_mode': 'linear',
        }
        for index, value in enumerate(values)
    ]


def _layer():
    """한 축에 곡선 둘 · 앞 0.00~0.04, 뒤 0.10~0.20"""
    head = _points(0.0, [0.0, 5.0, 10.0])
    tail = _points(0.10, [0.0, 0.0, 0.0, 0.0, 0.0, 0.0])
    frames = {}
    for point in head + tail:
        frames[point['time_sec']] = {'1-1': point['value_deg']}
    for index in range(3, 5):  # 두 곡선 사이 0.06, 0.08 은 프레임만 있다
        frames[round(index * PERIOD, 9)] = {'1-1': 10.0}
    return {
        'layer_id': 'layer_test',
        'name': '시험',
        'frames': [
            {'time_sec': time_sec, 'values': values}
            for time_sec, values in sorted(frames.items())
        ],
        'point_curves': [
            {'curve_id': 'head', 'motion_id': '1-1',
             'interpolation_order': 1, 'points': head},
            {'curve_id': 'tail', 'motion_id': '1-1',
             'interpolation_order': 1, 'points': tail},
        ],
    }


def _spans(layer, motion_id='1-1'):
    return sorted(
        (curve['points'][0]['time_sec'], curve['points'][-1]['time_sec'])
        for curve in layer['point_curves']
        if curve['motion_id'] == motion_id
    )


def _paste(layer, start_sec, end_sec, target_start_sec):
    return edit_layer(copy.deepcopy(layer), {
        'operation': 'copy_point_range',
        'motion_ids': ['1-1'],
        'start_sec': start_sec,
        'end_sec': end_sec,
        'target_start_sec': target_start_sec,
    })


def test_the_curve_we_copied_from_does_not_grow():
    layer = _layer()

    pasted = _paste(layer, 0.0, 0.04, 0.12)

    assert _spans(pasted) == [(0.0, 0.04), (0.10, 0.20)]


def test_the_curve_at_that_time_takes_the_points():
    layer = _layer()

    pasted = _paste(layer, 0.0, 0.04, 0.12)

    tail = next(c for c in pasted['point_curves'] if c['curve_id'] == 'tail')
    values = {p['time_sec']: p['value_deg'] for p in tail['points']}
    assert values[0.12] == 0.0
    assert values[0.14] == 5.0
    assert values[0.16] == 10.0


def test_saving_it_would_not_fail():
    layer = _layer()

    pasted = _paste(layer, 0.0, 0.04, 0.12)

    assert point_curve_frame_mismatches(pasted) == []


def test_pasting_across_two_curves_is_refused():
    layer = _layer()

    # 0.08 초 길이를 0.02 초에 붙이면 0.02~0.10 · 두 곡선에 걸친다
    with pytest.raises(ValueError, match='곡선 여럿에 걸칩니다'):
        _paste(layer, 0.10, 0.18, 0.02)


def test_pasting_where_there_is_no_curve_makes_a_new_one():
    # 가장 흔한 쓰임이 **끝에 이어 붙이기**다 · 옆 곡선을 늘려 그 사이를
    # 평평하게 덮지 않고, 붙인 자리에 곡선을 따로 만든다
    layer = _layer()

    pasted = _paste(layer, 0.0, 0.04, 0.30)

    assert _spans(pasted) == [(0.0, 0.04), (0.10, 0.20), (0.30, 0.34)]
    assert point_curve_frame_mismatches(pasted) == []


def test_the_new_curve_keeps_the_values_we_copied():
    layer = _layer()

    pasted = _paste(layer, 0.0, 0.04, 0.30)

    fresh = next(c for c in pasted['point_curves']
                 if c['points'][0]['time_sec'] == 0.30)
    assert [p['value_deg'] for p in fresh['points']] == [0.0, 5.0, 10.0]


def test_one_curve_per_axis_works_as_before():
    layer = _layer()
    layer['point_curves'] = [layer['point_curves'][1]]

    pasted = _paste(layer, 0.10, 0.14, 0.16)

    assert _spans(pasted) == [(0.10, 0.20)]
    assert point_curve_frame_mismatches(pasted) == []
