// 10분짜리 모션에서 그래프가 통째로 안 보이던 것 · §6-261
//
// `Math.min(0, ...values)` 는 값을 전부 **인자로** 넘긴다 · 인자 수에는
// 한계가 있다 · 27,473프레임 × 3축 = 8만 값에서 이렇게 터졌다.
//
//     RangeError: Maximum call stack size exceeded
//       at drawMotionStudioEditorGraph (motion_studio_graph.js:332)
//
// 그리는 도중에 터지므로 **편집 창이 비어 보였다** · 사람에게는 「용량이 커서
// 안 나오나」로 보였다.

import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import { maxOf, minOf } from '../static/js/format.js';

test('값이 20만 개여도 터지지 않는다', () => {
  const many = Array.from({ length: 200000 }, (_, index) => Math.sin(index) * 100);

  assert.ok(minOf(many, 0) < -99);
  assert.ok(maxOf(many, 0) > 99);
});

test('펼치기는 실제로 터진다 · 고친 이유가 남아 있어야 한다', () => {
  const many = Array.from({ length: 200000 }, () => 1);

  assert.throws(() => Math.min(0, ...many), RangeError);
});

test('씨앗값을 함께 본다 · 0을 넣으면 0보다 작아지지 않는다', () => {
  assert.equal(minOf([5, 9], 0), 0);
  assert.equal(maxOf([-5, -9], 0), 0);
  assert.equal(minOf([5, 9]), 5);
  assert.equal(maxOf([-5, -9]), -5);
});

test('빈 배열이면 씨앗값 그대로다 · 그래프가 NaN 을 그리지 않는다', () => {
  assert.equal(minOf([], 0), 0);
  assert.equal(maxOf([], 0), 0);
});

test('숫자가 아닌 값은 건너뛴다', () => {
  assert.equal(minOf([null, 'x', 3, undefined, NaN], 10), 3);
  assert.equal(maxOf([null, 'x', 3, undefined, NaN], -10), 3);
});

// --------------------------------------------------------------------------- //
// 프레임만큼 큰 배열을 다루는 자리에는 펼치기가 남아 있으면 안 된다
// --------------------------------------------------------------------------- //

const FRAME_SCALE_FILES = [
  'motion_studio_graph.js',
  'motion_studio_tracks.js',
  'motion_studio_editor_controller.js',
  'motion_studio.js',
  'motion_data.js',
  'motion_studio_project_model.js',
];

test('프레임을 다루는 파일에 Math.min/max 펼치기가 없다', () => {
  for (const name of FRAME_SCALE_FILES) {
    const source = readFileSync(new URL(`../static/js/${name}`, import.meta.url), 'utf8')
      .replace(/\/\*[\s\S]*?\*\//g, '')
      .replace(/^\s*\/\/.*$/gm, '');
    assert.doesNotMatch(
      source,
      /Math\.(min|max)\([^)]*\.\.\./,
      `${name} 에 펼치기가 남아 있다`,
    );
  }
});
