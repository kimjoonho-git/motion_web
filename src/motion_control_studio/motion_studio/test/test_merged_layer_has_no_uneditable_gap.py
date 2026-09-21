"""합친 레이어에 **편집할 수 없는 구간**을 남기지 않는다 · §6-252

합치기는 둘을 따로 만들었다.

    frames = render_project(...)            전체 길이를 꽉 채운다
    point_curves = 원본에서 그대로 복사      원본이 덮던 구간만

그래서 합친 뒤에는 곡선 밖에 프레임만 있는 구간이 남았다 · 그래프에는 앞뒤로
평평한 선이 그어지는데 **집을 포인트가 없어** 그 구간은 편집할 수 없었다 ·
녹화만 한 레이어는 둘이 같은 구간이라 이런 일이 없어서, 사용자에게는
「어떨 땐 있고 없을 때도 있다」로 보였다.

**원래 곡선에 포인트를 이어 붙이지는 않는다** · 붙이면 이웃이 생겨 원래
곡선의 접선이 달라지고 그린 결과가 프레임과 어긋난다 · 실측으로 83개 표본이
최대 3.65° 벗어났다 · 대신 빈 구간에 **곡선을 따로** 만든다.
"""

from motion_studio.layer_editor import extend_point_curves_to_frames
from motion_studio.layer_validation import point_curve_frame_mismatches

PERIOD = 0.02


def _frames(values_by_time):
    return [
        {'time_sec': round(time_sec, 9), 'values': dict(values)}
        for time_sec, values in sorted(values_by_time.items())
    ]


def _flat_layer():
    """0.0~1.0 초 프레임 · 곡선은 0.4~0.6 초만 덮는다."""
    values = {}
    for index in range(51):
        time_sec = round(index * PERIOD, 9)
        values[time_sec] = {'1-1': 0.0}
    return {
        'frames': _frames(values),
        'point_curves': [{
            'curve_id': 'curve_a',
            'motion_id': '1-1',
            'interpolation_order': 1,
            'points': [
                {'point_id': 'p1', 'time_sec': 0.4, 'value_deg': 0.0, 'tangent_mode': 'linear'},
                {'point_id': 'p2', 'time_sec': 0.6, 'value_deg': 0.0, 'tangent_mode': 'linear'},
            ],
        }],
    }


def _covered_spans(curves, motion_id):
    spans = []
    for curve in curves:
        if str(curve.get('motion_id')) != motion_id:
            continue
        points = curve['points']
        spans.append((points[0]['time_sec'], points[-1]['time_sec']))
    return sorted(spans)


def test_the_gap_before_and_after_gets_its_own_curve():
    layer = _flat_layer()

    extended = extend_point_curves_to_frames(layer)

    spans = _covered_spans(extended, '1-1')
    assert len(spans) == 3, spans
    assert spans[0][0] == 0.0
    assert spans[-1][1] == 1.0


def test_the_original_curve_is_left_alone():
    layer = _flat_layer()
    original = layer['point_curves'][0]

    extended = extend_point_curves_to_frames(layer)

    kept = [c for c in extended if c['curve_id'] == 'curve_a']
    assert len(kept) == 1
    assert kept[0]['points'] == original['points']


def test_what_it_draws_still_matches_the_frames():
    layer = _flat_layer()

    extended = extend_point_curves_to_frames(layer)

    assert point_curve_frame_mismatches(
        {'frames': layer['frames'], 'point_curves': extended}
    ) == []


def test_curves_never_overlap_each_other():
    layer = _flat_layer()

    spans = _covered_spans(extend_point_curves_to_frames(layer), '1-1')

    for previous, following in zip(spans, spans[1:]):
        assert following[0] > previous[1], (previous, following)


def test_a_layer_already_covered_is_left_as_it_is():
    layer = _flat_layer()
    layer['point_curves'][0]['points'] = [
        {'point_id': 'p1', 'time_sec': 0.0, 'value_deg': 0.0, 'tangent_mode': 'linear'},
        {'point_id': 'p2', 'time_sec': 1.0, 'value_deg': 0.0, 'tangent_mode': 'linear'},
    ]

    extended = extend_point_curves_to_frames(layer)

    assert len(extended) == 1


def test_a_layer_without_curves_is_left_as_it_is():
    layer = _flat_layer()
    layer['point_curves'] = []

    assert extend_point_curves_to_frames(layer) == []
