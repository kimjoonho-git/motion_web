/** 상태 목록은 한 곳에서만 적는다 · §6-167
 *
 * **같은 목록을 두 곳에 적으면 언젠가 한쪽만 고친다.**
 *
 * 화면에 이런 것들이 흩어져 있었다.
 *
 *   ['initializing', 'recording', 'playing', 'stopping']   motion_studio.js ×2
 *   ['initializing', 'playing', 'recording', 'stopping']   editor_math.js
 *        ↑ 같은 것인데 **순서가 달라** 한눈에 안 보였다
 *   ['playing', 'recording']                               playback.js ×2
 *   ['failure', 'timeout', 'cancelled']                    main.js ×2
 *        ↑ restart_tracking.js 에 이미 이름이 있었는데도
 *
 * 새 상태가 하나 생기면 전부 고쳐야 하고, 빠뜨린 곳만 조용히 틀린다 ·
 * 터지지 않으니 한참 모른다.
 */

import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
import test from 'node:test';

import {
  MOTION_STUDIO_BUSY_STATES,
  MOTION_STUDIO_MOVING_STATES,
  MOTION_STUDIO_STATES,
} from '../static/js/motion_studio_constants.js';

const JS_DIR = new URL('../static/js/', import.meta.url);

function sources() {
  return readdirSync(JS_DIR)
    .filter((name) => name.endsWith('.js'))
    .map((name) => [name, readFileSync(new URL(name, JS_DIR), 'utf8')]);
}

/** 이 목록들이 다시 손으로 적히면 안 된다 · 순서를 바꿔 적어도 잡는다. */
const OWNED_LISTS = [
  { owner: 'MOTION_STUDIO_BUSY_STATES', members: [...MOTION_STUDIO_BUSY_STATES] },
  { owner: 'MOTION_STUDIO_MOVING_STATES', members: [...MOTION_STUDIO_MOVING_STATES] },
  { owner: 'TERMINAL_FAILURES', members: ['failure', 'timeout', 'cancelled'] },
];

/** 그 목록을 정의한 파일 · 여기 한 번만 적혀 있어야 한다. */
const OWNER_FILES = new Set(['motion_studio_constants.js', 'restart_tracking.js']);

test('스튜디오가 일하는 중 · 목록은 상태 이름에서 나온다', () => {
  // 손으로 또 적지 않고 `MOTION_STUDIO_STATES` 에서 빼서 만든다
  assert.deepEqual(
    [...MOTION_STUDIO_BUSY_STATES].sort(),
    MOTION_STUDIO_STATES.filter((s) => !['error', 'idle'].includes(s)).sort(),
  );
});

test('움직이는 중은 일하는 중보다 좁다', () => {
  // 초기 이동과 정지 처리는 「움직인다」가 아니다 · 서버의 is_moving 과 같은 구분
  for (const state of MOTION_STUDIO_MOVING_STATES) {
    assert.ok(MOTION_STUDIO_BUSY_STATES.includes(state), `${state} 가 빠졌다`);
  }
  assert.ok(!MOTION_STUDIO_MOVING_STATES.includes('initializing'));
  assert.ok(!MOTION_STUDIO_MOVING_STATES.includes('stopping'));
});

for (const { owner, members } of OWNED_LISTS) {
  test(`${owner} 목록을 다시 손으로 적은 곳이 없다`, () => {
    // 순서를 바꿔 적어도 잡는다 · 실제로 한 곳이 그랬다
    const quoted = members.map((m) => `'${m}'`);
    const offenders = [];
    for (const [name, text] of sources()) {
      if (OWNER_FILES.has(name)) continue;
      for (const match of text.matchAll(/[[{]\s*('[a-z_-]+'\s*(?:,\s*'[a-z_-]+'\s*)+)[\]}]/g)) {
        const found = match[1].split(',').map((s) => s.trim());
        if (found.length !== quoted.length) continue;
        if ([...found].sort().join() === [...quoted].sort().join()) {
          offenders.push(`${name}: ${match[0]}`);
        }
      }
    }
    assert.deepEqual(offenders, [], (
      `${owner} 의 목록을 직접 적고 있습니다 · 주인을 가져다 쓰세요:\n  `
      + offenders.join('\n  ')
    ));
  });
}

test('움직이는 중은 실재하는 상태 이름만 쓴다', () => {
  // 오타가 나면 조건이 영원히 거짓이 된다 · 터지지 않아 한참 모른다
  for (const state of MOTION_STUDIO_MOVING_STATES) {
    assert.ok(MOTION_STUDIO_STATES.includes(state), `${state} 는 없는 상태다`);
  }
});
