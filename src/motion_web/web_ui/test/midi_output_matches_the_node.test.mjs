// 화면의 최종 출력 계산은 서버와 **같은 답**을 내야 한다 · §6-245
//
// 같은 식이 두 곳에 적혀 있다.
//
//     화면   midi_monitor.js  mappedOutput14bit()
//     서버   midi_control_node.py  _filtered_output_14bit()
//
// 화면이 굳이 또 계산하는 이유는 **저장 전 편집을 미리 보여주려고** 다 ·
// 서버는 아직 저장되지 않은 최소값·최대값·반전을 모른다.
//
// 그래서 없앨 수 없고, 대신 갈라지면 잡히게 한다 · 아래 표본과 기대값은
// 서버 쪽 `test_midi_output_matches_the_screen.py` 와 **글자 그대로 같다** ·
// 한쪽 식만 고치면 그쪽 시험이 깨진다.

import assert from 'node:assert/strict';
import test from 'node:test';
import { mappedOutput14bit } from '../static/js/midi_monitor.js';

const MIDI_MAX = 16383;

// [필터 출력, 최소값%, 최대값%, 반전, 기대 최종 출력]
const SAMPLES = [
  [0, 0, 100, false, 0],
  [16383, 0, 100, false, 16383],
  [8191.5, 0, 100, false, 8191.5],
  [0, 0, 100, true, 16383],
  [16383, 0, 100, true, 0],
  [8191.5, 20, 80, false, 8191.5],
  [0, 20, 80, false, 3276.6],
  [16383, 20, 80, false, 13106.4],
  [16383, 0, 200, false, 16383],
  [8191.5, 0, 200, false, 16383],
  [4095.75, 0, 200, false, 8191.5],
  [0, 50, 50, false, 8191.5],
  [16383, 50, 50, false, 8191.5],
  [-100, 0, 100, false, 0],
  [99999, 0, 100, false, 16383],
];

test('최종 출력은 서버와 같은 값을 낸다', () => {
  for (const [filtered, min, max, reversed, expected] of SAMPLES) {
    const got = mappedOutput14bit(filtered, {
      min_percent: min,
      max_percent: max,
      reversed,
    });
    assert.ok(
      Math.abs(got - expected) < 1e-6,
      `필터출력 ${filtered} · 최소 ${min}% · 최대 ${max}% · 반전 ${reversed}`
      + ` → ${got} (서버 기대값 ${expected})`,
    );
  }
});

test('최종 출력은 0 ~ 16383 을 벗어나지 않는다', () => {
  for (const [filtered, min, max, reversed] of SAMPLES) {
    const got = mappedOutput14bit(filtered, {
      min_percent: min,
      max_percent: max,
      reversed,
    });
    assert.ok(got >= 0 && got <= MIDI_MAX, `범위를 벗어남: ${got}`);
  }
});

// --------------------------------------------------------------------------- //
// 그리면서 설정을 고치지 않는다 · 사람에게 하는 말은 지워지지 않는다 · §6-245
// --------------------------------------------------------------------------- //

import { readFileSync } from 'node:fs';

const controller = readFileSync(new URL('../static/js/midi_monitor.js', import.meta.url), 'utf8');

test('화면을 그리는 도중에 설정을 고치지 않는다', () => {
  const start = controller.indexOf('function renderRows(');
  const end = controller.indexOf('\n  function ', start);
  assert.ok(start >= 0 && end > start);
  // 주석에 옛 코드를 적어 둘 수 있다 · 주석을 빼고 본다
  const body = controller.slice(start, end)
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/^\s*\/\/.*$/gm, '');
  // 전에는 여기서 mapping.min_percent = 0 을 했다 · 사람이 적은 값이 말없이 사라졌다
  assert.doesNotMatch(body, /mapping\.\w+\s*=[^=]/, '그리는 자리에서 설정을 고치고 있다');
});

test('최대값이 100을 넘으면 최소값이 0이 된 것을 알린다', () => {
  assert.match(controller, /최대값이 100%를 넘어 최소값을 0%로 맞췄습니다/);
});

test('편집 안내는 노드 상태 문구에 덮이지 않는다', () => {
  // 상태 문구는 초당 열 번 서버 값으로 갈아끼워진다 · 거기 적으면 안 보인다
  assert.match(controller, /let editNotice = '';/);
  assert.match(controller, /editNotice \|\| status\?\.message/);
  // 저장·뱅크 전환·다시 읽기에서 지운다 (시간으로 지우지 않는다)
  assert.ok(controller.split("editNotice = '';").length - 1 >= 3, '안내를 지우는 자리가 모자라다');
});
