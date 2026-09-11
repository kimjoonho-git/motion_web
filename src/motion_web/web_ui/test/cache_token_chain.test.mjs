import assert from 'node:assert/strict';
import { readdirSync, readFileSync } from 'node:fs';
import test from 'node:test';

const STATIC = new URL('../static/', import.meta.url);
const read = (name) => readFileSync(new URL(name, STATIC), 'utf8');

const TOKEN = /\?v=([\w.-]+)/g;

/**
 * 캐시 토큰은 사슬이다 · `index.html → app.js → main.js → 나머지`.
 *
 * 첫 고리를 안 바꾸면 브라우저가 `app.js` 를 다시 받지 않고, 그 안의 새 토큰을
 * 영영 못 본다 · 아래 모듈을 아무리 바꿔도 화면은 옛 코드로 돈다.
 *
 * 실제로 그렇게 막혔다 · 추가 녹화 버튼이 새 브라우저에서는 활성인데 쓰던
 * 브라우저에서는 잠긴 채였다 · §6-75
 *
 * 토큰을 손으로 고치지 말고 한 번에 맞춘다.
 *
 *     python3 scripts/update_cache.py static
 */
test('every cache-bust token is the same value', () => {
  const files = ['index.html', 'app.js', ...readdirSync(new URL('js/', STATIC))
    .filter((name) => name.endsWith('.js'))
    .map((name) => `js/${name}`)];

  const seen = new Map();
  for (const file of files) {
    for (const [, token] of read(file).matchAll(TOKEN)) {
      if (!seen.has(token)) seen.set(token, []);
      seen.get(token).push(file);
    }
  }

  assert.ok(seen.size > 0, '토큰을 하나도 찾지 못했다');
  assert.equal(
    seen.size,
    1,
    '토큰이 갈렸다 · 브라우저가 옛 코드를 계속 쓴다\n'
    + [...seen].map(([token, where]) =>
      `  ${token} · ${[...new Set(where)].slice(0, 4).join(', ')}`).join('\n')
    + '\n  python3 scripts/update_cache.py static 로 한 번에 맞추세요.',
  );
});

test('the entry chain carries the shared token', () => {
  const [entry] = [...read('index.html').matchAll(/app\.js\?v=([\w.-]+)/g)];
  const [main] = [...read('app.js').matchAll(/main\.js\?v=([\w.-]+)/g)];
  assert.ok(entry, 'index.html 이 app.js 를 토큰과 함께 부르지 않는다');
  assert.ok(main, 'app.js 가 main.js 를 토큰과 함께 부르지 않는다');
  assert.equal(entry[1], main[1], '첫 고리가 끊겼다 · app.js 를 다시 받지 않는다');
});
