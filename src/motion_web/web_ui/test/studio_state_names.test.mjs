import assert from 'node:assert/strict';
import { readdirSync, readFileSync } from 'node:fs';
import test from 'node:test';

import {
  MOTION_STUDIO_STATES,
  MOTION_STUDIO_STATUS_PHASES,
} from '../static/js/motion_studio_constants.js';

/**
 * 스튜디오 상태 이름은 한 곳에서만 정해진다 · §6-88
 *
 * 같은 이름을 파이썬 50곳과 JS 54곳이 맨 문자열로 들고 있었다 · 한쪽에 새
 * 이름이 생기면 다른 쪽은 모른 채로 돌고, 알 방법도 없었다.
 *
 * 서버와 같은지는 파이썬 쪽 검사가 본다 · 여기서는 **화면의 가지가 아는
 * 이름만 쓰는지** 본다. 오타 하나면 그 가지는 영영 안 탄다 · 조용히 안 도는
 * 것이 제일 나쁘다.
 */

const JS = new URL('../static/js/', import.meta.url);
const files = readdirSync(JS).filter((name) => name.startsWith('motion_studio'));

/** 상태를 견주는 자리만 고른다 · 편집기의 `rangeSelection.phase` 같은 것은 뺀다. */
const COMPARISON = new RegExp(
  String.raw`(?:runtimeState|displayState|status\??\.(?:state|phase))`
  + String.raw`[^'\n]*?(?:===|!==)\s*'([a-z]+)'`,
  'g',
);
const INCLUDES = /\[([^\]]*)\]\.includes\((?:runtimeState|playback\.displayState)\)/g;
/** `status.state || 'idle'` 같은 기본값도 상태 이름이다. */
const FALLBACK = /status\??\.(?:state|phase)[^'\n]*?\|\|\s*'([a-z]+)'/g;

function literalsIn(source) {
  const found = new Set();
  for (const [, name] of source.matchAll(COMPARISON)) found.add(name);
  for (const [, list] of source.matchAll(INCLUDES)) {
    for (const [, name] of list.matchAll(/'([a-z]+)'/g)) found.add(name);
  }
  for (const [, name] of source.matchAll(FALLBACK)) found.add(name);
  return found;
}

test('every state the studio screen branches on is a known name', () => {
  const known = new Set(MOTION_STUDIO_STATUS_PHASES);
  const unknown = [];
  for (const file of files) {
    const source = readFileSync(new URL(file, JS), 'utf8');
    for (const name of literalsIn(source)) {
      if (!known.has(name)) unknown.push(`${file}: ${name}`);
    }
  }
  assert.deepEqual(
    unknown, [],
    `모르는 상태 이름 · 그 가지는 영영 안 탄다:\n  ${unknown.join('\n  ')}`,
  );
});

test('the vocabulary is not quietly empty', () => {
  /** 목록이 비면 위 검사는 아무것도 안 잡으면서 통과한다. */
  assert.ok(MOTION_STUDIO_STATES.length >= 6);
  assert.ok(MOTION_STUDIO_STATUS_PHASES.includes('countdown'));
  for (const name of MOTION_STUDIO_STATES) {
    assert.ok(MOTION_STUDIO_STATUS_PHASES.includes(name));
  }
});

test('the screen really does branch on these names', () => {
  /** 실제로 쓰이는 것을 확인한다 · 안 그러면 위 검사는 빈 집합만 훑는다. */
  const all = new Set();
  for (const file of files) {
    for (const name of literalsIn(readFileSync(new URL(file, JS), 'utf8'))) {
      all.add(name);
    }
  }
  for (const name of ['idle', 'initializing', 'recording', 'playing']) {
    assert.ok(all.has(name), `${name} 을 견주는 자리를 못 찾았다 · 정규식이 헛돈다`);
  }
});
