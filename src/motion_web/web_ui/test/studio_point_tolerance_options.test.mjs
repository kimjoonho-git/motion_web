import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

// 정밀도 선택지 · §6-110
//
// 포인트는 최대 200개까지만 만든다 · 18초짜리 굴곡 많은 녹화는 0.5° 안에
// 들어오지 못해 「전체 포인트 생성」이 통째로 실패했다 · 거친 쪽 선택지가
// 없어서 사용자가 물러설 자리가 없었다.

const panel = readFileSync(
  fileURLToPath(new URL('../static/panels/09-panel-studio.html', import.meta.url)),
  'utf8',
);

const toleranceOptions = () => {
  const select = panel.match(
    /<select id="studioEditorApproximationTolerance">([\s\S]*?)<\/select>/,
  );
  assert.ok(select, '정밀도 선택 상자를 찾지 못했다');
  return [...select[1].matchAll(/<option value="([^"]+)"/g)].map((m) => Number(m[1]));
};

test('coarse tolerances are offered so a dense recording can still be converted', () => {
  const values = toleranceOptions();
  for (const coarse of [1, 2, 3, 4, 5]) {
    assert.ok(values.includes(coarse), `${coarse}° 선택지가 없다`);
  }
});

test('the working fine tolerances are kept', () => {
  const values = toleranceOptions();
  for (const fine of [0.5, 0.1]) {
    assert.ok(values.includes(fine), `${fine}° 선택지가 사라졌다`);
  }
});

test('the 0.02 degree option is gone', () => {
  // 실제 녹화에서 0.02°는 936표본에 포인트 689개 · 그래프보다 포인트가 많아
  // 편집할 수 있는 물건이 아니었다 · 사용자가 빼 달라고 했다.
  assert.equal(toleranceOptions().includes(0.02), false);
});

test('labels are the angle and nothing else', () => {
  const select = panel.match(
    /<select id="studioEditorApproximationTolerance">([\s\S]*?)<\/select>/,
  )[1];
  const labels = [...select.matchAll(/<option[^>]*>([^<]+)<\/option>/g)].map((m) => m[1]);
  for (const label of labels) {
    assert.match(label, /^[0-9]+(\.[0-9]+)?°$/, `설명이 붙어 있다: ${label}`);
  }
});

test('one degree is the preselected default', () => {
  // 0.1° 는 18.7초 녹화 한 축에 520개가 나온다 · 편집할 수 있는 물건이 아니다 ·
  // 1° 는 154개 · 사용자가 기본값으로 정했다.
  const select = panel.match(
    /<select id="studioEditorApproximationTolerance">([\s\S]*?)<\/select>/,
  )[1];
  const preselected = select.match(/<option value="([^"]+)"[^>]*selected/);
  assert.ok(preselected, '기본 선택이 없다');
  assert.equal(Number(preselected[1]), 1);
});

test('options run from coarse to fine and exactly one is preselected', () => {
  const values = toleranceOptions();
  assert.deepEqual(values, [...values].sort((a, b) => b - a), '목록이 거친 순서가 아니다');
  const selected = panel.match(
    /<select id="studioEditorApproximationTolerance">([\s\S]*?)<\/select>/,
  )[1].match(/selected/g) || [];
  assert.equal(selected.length, 1);
});
