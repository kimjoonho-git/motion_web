"""추가 녹화 중 축·시간별 소유권 · §6-72

오버더빙은 녹화된 대로 모터를 돌리면서 그 위에 얹는다 · 같은 순간에도 축마다
주인이 다르다. 여기서 그 판정만 따로 시험한다 · 모터가 없어도 확인할 수 있다.
"""

from motion_studio.timeline import playback_ownership

PERIOD = 0.02


def _frames(values_by_time):
    return [
        {'frame': i + 1, 'time_sec': round(t, 9), 'values': dict(v)}
        for i, (t, v) in enumerate(sorted(values_by_time))
    ]


def _layer(values_by_time, enabled=True):
    return {'enabled': enabled, 'frames': _frames(values_by_time)}


def _project(*layers):
    return {'period_sec': PERIOD, 'layers': list(layers)}


def test_axis_with_data_is_owned_for_its_span():
    """축 1-1 이 0.02~0.10 초에 녹화돼 있으면 그 구간만 재생이 소유한다."""
    times = [(round(0.02 * i, 9), {'1-1': float(i)}) for i in range(1, 6)]
    owned = playback_ownership(_project(_layer(times)))
    assert owned == {'1-1': [(0.02, 0.10)]}


def test_axis_without_data_is_not_owned():
    """레이어에 없는 축은 전 구간 MIDI 로 녹화할 수 있다."""
    times = [(0.02, {'1-1': 0.0}), (0.04, {'1-1': 1.0})]
    owned = playback_ownership(_project(_layer(times)))
    assert '1-2' not in owned


def test_a_single_missing_frame_does_not_split_ownership():
    """축이 한 프레임 쉬는 것은 같은 구간이다 · 그 사이에 MIDI 가 끼면 안 된다."""
    times = [
        (0.02, {'1-1': 0.0}),
        (0.04, {}),            # 이 프레임에는 값이 없다
        (0.06, {'1-1': 1.0}),
    ]
    owned = playback_ownership(_project(_layer(times)))
    assert owned == {'1-1': [(0.02, 0.06)]}


def test_a_long_gap_inside_one_layer_stays_owned():
    """한 레이어 안이면 한참 비어도 계속 재생 소유다.

    값이 비는 순간마다 주인이 바뀌면 모터가 재생과 MIDI 사이에서 떤다 ·
    "축이 잠깐 쉬는 순간에도 MIDI 는 동작하면 안 된다" 가 이 규칙이다.
    """
    times = [
        (0.02, {'1-1': 0.0}),
        (0.04, {'1-1': 1.0}),
        (1.00, {'1-1': 2.0}),
        (1.02, {'1-1': 3.0}),
    ]
    owned = playback_ownership(_project(_layer(times)))
    assert owned == {'1-1': [(0.02, 1.02)]}


def test_axes_are_independent():
    """축 1-1 이 재생 중이어도 1-2 는 MIDI 가 쓸 수 있다 · 오버더빙의 핵심."""
    times = [
        (0.02, {'1-1': 0.0, '1-2': 5.0}),
        (0.04, {'1-1': 1.0}),            # 1-2 는 여기서 끝
    ]
    owned = playback_ownership(_project(_layer(times)))
    assert owned['1-1'] == [(0.02, 0.04)]
    assert owned['1-2'] == [(0.02, 0.02)]


def test_multiple_layers_on_one_axis_are_joined():
    """레이어 둘이 한 축을 이어 받으면 하나의 소유 구간이 된다."""
    first = _layer([(0.02, {'1-1': 0.0}), (0.04, {'1-1': 1.0})])
    second = _layer([(0.06, {'1-1': 2.0}), (0.08, {'1-1': 3.0})])
    owned = playback_ownership(_project(first, second))
    assert owned == {'1-1': [(0.02, 0.08)]}


def test_disabled_layers_do_not_own_anything():
    """레이어를 끄면 그 축은 MIDI 가 쓸 수 있다."""
    times = [(0.02, {'1-1': 0.0}), (0.04, {'1-1': 1.0})]
    owned = playback_ownership(_project(_layer(times, enabled=False)))
    assert owned == {}


def test_selected_motion_ids_narrow_the_result():
    times = [(0.02, {'1-1': 0.0, '1-2': 5.0})]
    owned = playback_ownership(_project(_layer(times)), motion_ids=['1-2'])
    assert set(owned) == {'1-2'}
