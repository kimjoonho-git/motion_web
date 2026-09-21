"""구간 복사는 **그 축을 곡선 하나로** 만든다 · §6-256 §6-280

처음에는 떠 온 곡선에 도로 넣었다 · 앞 곡선의 포인트를 30초에 붙이면 그 곡선이
늘어나 가운데 곡선을 통째로 덮었고, 저장할 때 「포인트 곡선과 20ms 프레임이
다릅니다」로 터졌다(실측 1499개 표본).

그래서 붙인 자리의 곡선에 넣고, 없으면 새로 만들게 했다 · 이번에는 **끝에 이어
붙일 때마다 곡선이 하나씩 늘었다** · 기존 데이터 끝과 붙인 자리 사이는 곡선도
프레임도 없는 빈 구간으로 남아 화면에 흰 줄로 보였고, 조각난 곡선 때문에 구간
지우기까지 어긋났다.

나눌 이유가 없다 · **붙이고 나면 그 축의 곡선은 하나다** · 빈 구간은 이어지면서
메워진다.
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


def test_pasting_leaves_one_curve():
    """붙이면 그 축은 곡선 하나가 된다 · 나뉘어 있던 둘도 합쳐진다."""
    layer = _layer()

    pasted = _paste(layer, 0.0, 0.04, 0.12)

    assert _spans(pasted) == [(0.0, 0.20)]


def test_the_pasted_values_land_at_the_target():
    layer = _layer()

    pasted = _paste(layer, 0.0, 0.04, 0.12)

    curve = pasted['point_curves'][0]
    values = {p['time_sec']: p['value_deg'] for p in curve['points']}
    assert values[0.12] == 0.0
    assert values[0.14] == 5.0
    assert values[0.16] == 10.0


def test_saving_it_would_not_fail():
    layer = _layer()

    pasted = _paste(layer, 0.0, 0.04, 0.12)

    assert point_curve_frame_mismatches(pasted) == []


def test_pasting_across_two_curves_is_no_longer_refused():
    """예전에는 두 곡선에 걸치면 거부했다 · 이제 하나로 합치므로 걸칠 것이 없다."""
    layer = _layer()

    pasted = _paste(layer, 0.10, 0.18, 0.02)

    assert len([c for c in pasted['point_curves'] if c['motion_id'] == '1-1']) == 1
    assert point_curve_frame_mismatches(pasted) == []


def test_pasting_past_the_end_leaves_no_hole():
    """끝에 이어 붙여도 곡선은 하나다 · 그 사이가 비지 않는다."""
    layer = _layer()

    pasted = _paste(layer, 0.0, 0.04, 0.30)

    assert _spans(pasted) == [(0.0, 0.34)]
    assert point_curve_frame_mismatches(pasted) == []
    times = [frame['time_sec'] for frame in pasted['frames']]
    holes = [
        round(b - a, 9) for a, b in zip(times, times[1:])
        if round(b - a, 9) > PERIOD + 1e-9
    ]
    assert holes == [], f'붙인 자리 앞이 비었다: {holes}'


def test_the_pasted_values_are_kept():
    layer = _layer()

    pasted = _paste(layer, 0.0, 0.04, 0.30)

    curve = pasted['point_curves'][0]
    tail = [p['value_deg'] for p in curve['points'] if p['time_sec'] >= 0.30]
    assert tail == [0.0, 5.0, 10.0]


