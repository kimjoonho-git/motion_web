import assert from 'node:assert/strict';
import test from 'node:test';

import { formatMoment } from '../static/js/format.js';

/**
 * 시각 표기는 **언제든 같은 모양이어야 한다** · §6-91
 *
 * 처음에는 오늘이면 시각만, 올해면 날짜까지, 그 밖이면 연도까지 줄여 썼다 ·
 * 목록에 섞여 나오니 `09. 10. 09:18` 과 `13:09` 가 나란히 서서 무엇과 무엇을
 * 견주는지 알 수 없었다. 짧은 것보다 같은 것이 낫다.
 */

const SHAPE = /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$/;
const ago = (seconds) => (Date.now() / 1000) - seconds;

test('every moment has the same shape, however old', () => {
  const cases = {
    '방금': ago(5),
    '4분 전': ago(240),
    '1시간 전': ago(3600),
    '어제': ago(90000),
    '지난달': ago(60 * 86400),
    '작년': ago(400 * 86400),
  };
  const shapes = new Set();
  for (const [label, seconds] of Object.entries(cases)) {
    const text = formatMoment(seconds);
    assert.match(text, SHAPE, `${label} · 모양이 다르다 · ${text}`);
    shapes.add(text.replace(/\d/g, '0'));
  }
  assert.equal(shapes.size, 1, `모양이 ${shapes.size}가지다 · ${[...shapes].join(' / ')}`);
});

test('it never adds how long ago it was', () => {
  /** "22시간 전" 같은 꼬리는 목록마다 길이가 달라져 줄이 흔들린다. */
  for (const seconds of [ago(5), ago(3600), ago(90000)]) {
    const text = formatMoment(seconds);
    assert.doesNotMatch(text, /전|방금|ago/, `상대 시간이 섞였다 · ${text}`);
  }
});

test('a missing time is a plain dash', () => {
  for (const empty of [null, undefined, 0, '', NaN, -1]) {
    assert.equal(formatMoment(empty), '-');
  }
});

test('it reads back as the moment it was given', () => {
  const at = new Date(2026, 8, 10, 9, 18, 45);
  assert.equal(formatMoment(at.getTime() / 1000), '2026-09-10 09:18');
});

test('single digits are padded so columns line up', () => {
  const at = new Date(2026, 0, 5, 7, 3, 0);
  assert.equal(formatMoment(at.getTime() / 1000), '2026-01-05 07:03');
});
