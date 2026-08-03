import pytest

from motion_studio.constants import DEFAULT_PERIOD_MS, DEFAULT_PERIOD_SEC
from motion_studio.curve_engine import render_point_curve


POINTS = [
    {'time_sec': 0, 'value_deg': 0, 'tangent_mode': 'auto'},
    {'time_sec': 0.04, 'value_deg': 8, 'tangent_mode': 'auto'},
    {'time_sec': 0.08, 'value_deg': 0, 'tangent_mode': 'auto'},
]
GOLDEN_VALUES = {
    1: [0, 4, 8, 4, 0],
    3: [0, 4, 8, 4, 0],
    5: [0, 3.75, 8, 3.75, 0],
}


@pytest.mark.parametrize('order', [1, 3, 5])
def test_backend_curve_matches_browser_20_ms_golden_vectors(order):
    assert DEFAULT_PERIOD_SEC == 0.02
    assert DEFAULT_PERIOD_MS == 20
    _points, samples = render_point_curve(POINTS, order)
    assert [time_sec for time_sec, _value in samples] == pytest.approx(
        [index * DEFAULT_PERIOD_SEC for index in range(5)]
    )
    assert [value for _time_sec, value in samples] == pytest.approx(
        GOLDEN_VALUES[order]
    )
