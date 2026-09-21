"""서버의 최종 출력 계산은 화면과 **같은 답**을 내야 한다 · §6-245

같은 식이 두 곳에 적혀 있다.

    서버   midi_control_node.py  _filtered_output_14bit()
    화면   midi_monitor.js       mappedOutput14bit()

화면이 굳이 또 계산하는 이유는 **저장 전 편집을 미리 보여주려고** 다 ·
서버는 아직 저장되지 않은 최소값·최대값·반전을 모른다 · 그래서 없앨 수 없고,
대신 갈라지면 잡히게 한다.

아래 표본과 기대값은 화면 쪽 `midi_output_matches_the_node.test.mjs` 와
**글자 그대로 같다** · 한쪽 식만 고치면 그쪽 시험이 깨진다.

모델값 때 겪은 것과 같은 모양이다 — 같은 사실을 두 곳에 적어 두면 한 곳만
고치게 되고, 화면 숫자와 실제 나가는 값이 말없이 어긋난다.
"""

import pytest

from midi_control.midi_control_node import MidiControlNode

MIDI_MAX = 16383

# (필터 출력, 최소값%, 최대값%, 반전, 기대 최종 출력)
SAMPLES = [
    (0, 0, 100, False, 0),
    (16383, 0, 100, False, 16383),
    (8191.5, 0, 100, False, 8191.5),
    (0, 0, 100, True, 16383),
    (16383, 0, 100, True, 0),
    (8191.5, 20, 80, False, 8191.5),
    (0, 20, 80, False, 3276.6),
    (16383, 20, 80, False, 13106.4),
    (16383, 0, 200, False, 16383),
    (8191.5, 0, 200, False, 16383),
    (4095.75, 0, 200, False, 8191.5),
    (0, 50, 50, False, 8191.5),
    (16383, 50, 50, False, 8191.5),
    (-100, 0, 100, False, 0),
    (99999, 0, 100, False, 16383),
]


@pytest.mark.parametrize('filtered,min_percent,max_percent,reversed_,expected', SAMPLES)
def test_final_output_matches_the_screen(
    filtered, min_percent, max_percent, reversed_, expected
):
    got = MidiControlNode._filtered_output_14bit(
        filtered,
        {
            'min_percent': min_percent,
            'max_percent': max_percent,
            'reversed': reversed_,
        },
    )

    assert got == pytest.approx(expected, abs=1e-6), (
        f'필터출력 {filtered} · 최소 {min_percent}% · 최대 {max_percent}% '
        f'· 반전 {reversed_} → {got} (화면 기대값 {expected})'
    )


@pytest.mark.parametrize('filtered,min_percent,max_percent,reversed_,_expected', SAMPLES)
def test_final_output_stays_inside_the_range(
    filtered, min_percent, max_percent, reversed_, _expected
):
    got = MidiControlNode._filtered_output_14bit(
        filtered,
        {
            'min_percent': min_percent,
            'max_percent': max_percent,
            'reversed': reversed_,
        },
    )

    assert 0 <= got <= MIDI_MAX, f'범위를 벗어남: {got}'
