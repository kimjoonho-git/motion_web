import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

// 이음매에서 값이 튀는 것은 막지 않는다 · 다만 말해 준다 · §6-116
//
// 앞 레이어의 마지막 값과 뒤 레이어의 첫 값 사이에는 아무것도 없다 · 20ms 한
// 칸에 그 차이만큼 건너뛰고 모터에 그대로 나간다 · 실제로 40° 가 튄 적이 있다
// (다른 칸은 최대 1.5°).

const source = readFileSync(
  fileURLToPath(new URL('../static/js/motion_studio.js', import.meta.url)),
  'utf8',
);
const panel = readFileSync(
  fileURLToPath(new URL('../static/panels/09-panel-studio.html', import.meta.url)),
  'utf8',
);

test('the merge result tells how far the seam jumps', () => {
  assert.match(source, /append_seam/);
  assert.match(source, /이음매/);
  assert.match(source, /재생 전에 확인하세요/);
});

test('a small seam step is not worth a warning', () => {
  assert.match(source, /step_deg\) >= 1/);
});

test('the merge guide no longer claims every axis must have points', () => {
  assert.equal(
    panel.includes('모든 모션축에 포인트가 생성된 레이어만 선택할 수 있습니다'),
    false,
    '사실과 다른 옛 설명이 남아 있다',
  );
  assert.match(panel, /한쪽 레이어에만 포인트로 덮여 있으면 합칠 수 없습니다/);
  assert.match(panel, /이음매에서 값이 튈 수 있으니/);
});
