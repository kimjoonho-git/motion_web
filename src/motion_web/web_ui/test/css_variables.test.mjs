import assert from 'node:assert/strict';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import path from 'node:path';
import test from 'node:test';

/**
 * 없는 색 이름을 쓰면 **조용히 아무 색도 안 나온다** · §6-147
 *
 * CSS 변수는 정의가 없어도 오류가 나지 않는다 · 그냥 그 속성이 없는 것처럼
 * 처리된다 · 그래서 다음 같은 일이 실제로 있었다.
 *
 *   - 스케줄 배지 · 상태를 네 가지로 갈라 놓고도 **늘 회색**이었다
 *   - `⭐ 필수` 딱지 · 배경이 비고 글씨가 흰색이라 **「필수」가 안 보였다**
 *   - 그룹 명단 배너 · 테두리와 글씨 색이 안 나왔다
 *   - 랜 경고 띠 · 만들자마자 색이 없었다
 *
 * 전부 `--color-primary` · `--color-danger` 처럼 **이 프로젝트에 없는 이름**을
 * 썼기 때문이다 · 화면을 열어 보기 전에는 알 수 없고, 열어 봐도 "원래 저런가
 * 보다" 하고 넘어간다 · 그래서 검사로 잡는다.
 *
 * 되돌릴 구멍은 남겨 둔다 · `var(--이름, 대체값)` 처럼 대체값을 적었으면
 * 정의가 없어도 색이 나오므로 통과시킨다.
 */

const STATIC_DIR = path.resolve(import.meta.dirname, '../static');

/** 어디서든 정의되면 정의된 것이다 · `:root` 만 보면 안 된다.
 *
 * 요소마다 붙이는 변수도 있다 (`--folder-color` 는 폴더 칸에서, 그래프 높이는
 * 편집기 안에서 정한다) · `:root` 만 훑으면 그것들을 없는 이름이라고 우긴다.
 */
function definedNames() {
  const names = new Set();
  for (const file of filesUnder(STATIC_DIR)) {
    const text = readFileSync(file, 'utf8');
    for (const [, name] of text.matchAll(/(--[\w-]+)\s*:/g)) names.add(name);
    for (const [, name] of text.matchAll(/setProperty\(\s*['"`](--[\w-]+)/g)) names.add(name);
  }
  return names;
}

function filesUnder(dir) {
  const found = [];
  for (const entry of readdirSync(dir)) {
    const full = path.join(dir, entry);
    if (statSync(full).isDirectory()) {
      found.push(...filesUnder(full));
    } else if (/\.(css|js|html)$/.test(entry)) {
      found.push(full);
    }
  }
  return found;
}

test('화면이 쓰는 색 이름은 모두 정의되어 있다', () => {
  const known = definedNames();
  assert.ok(known.size > 0, ':root 에서 변수를 하나도 못 읽었다');

  const missing = [];
  for (const file of filesUnder(STATIC_DIR)) {
    const text = readFileSync(file, 'utf8');
    for (const [, name, rest] of text.matchAll(/var\(\s*(--[\w-]+)\s*([^)]*)\)/g)) {
      if (known.has(name)) continue;
      if (rest.trim().startsWith(',')) continue;   // 대체값이 있으면 색은 나온다
      missing.push(`${path.relative(STATIC_DIR, file)} · ${name}`);
    }
  }

  assert.deepEqual(
    missing, [],
    `정의되지 않은 색 이름을 씁니다 · 아무 색도 안 나옵니다:\n  ${missing.join('\n  ')}`,
  );
});

test('대체값을 적어 두면 정의가 없어도 통과한다', () => {
  // 이 검사가 지나치게 빡빡해서 사람을 막지는 않는지 · 규칙 자체를 확인한다
  const sample = 'color: var(--nowhere-defined, #2b6cb0);';
  const [, name, rest] = [...sample.matchAll(/var\(\s*(--[\w-]+)\s*([^)]*)\)/g)][0];
  assert.equal(name, '--nowhere-defined');
  assert.ok(rest.trim().startsWith(','), '대체값을 못 알아본다');
});
