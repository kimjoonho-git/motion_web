import assert from 'node:assert/strict';
import { readdirSync, readFileSync, statSync } from 'node:fs';
import test from 'node:test';

/**
 * 화면 자산에 `?v=` 토큰을 붙이지 않는다 · §6-92
 *
 * 서버가 이미 `Cache-Control: no-cache` 와 `ETag` 를 보낸다 · 브라우저는 쓰기
 * 전에 반드시 서버에 물어보므로 바뀐 파일은 언제나 새로 받아 간다 · 토큰은
 * 아무 일도 하지 않았다.
 *
 * 하는 일 없이 값은 비쌌다 ·
 *
 *   · 한 줄만 고쳐도 26개 파일이 함께 바뀌어 진짜 변경이 묻혔다
 *   · 토큰 사슬(`index.html → app.js → main.js`)이 끊기면 브라우저가 옛 코드를
 *     계속 썼고, 그걸 찾는 데 한참 걸렸다 · §6-75
 *   · 배포할 때마다 잊지 말아야 할 단계가 하나 더 있었다
 *
 * 다시 넣고 싶어지면 먼저 서버 헤더를 보라 · `no-cache` 가 살아 있는 한 토큰은
 * 필요 없다.
 */

const STATIC = new URL('../static/', import.meta.url);

function assetFiles(base = STATIC, prefix = '') {
  const found = [];
  for (const name of readdirSync(base)) {
    const url = new URL(name, base);
    if (statSync(url).isDirectory()) {
      found.push(...assetFiles(new URL(`${name}/`, base), `${prefix}${name}/`));
      continue;
    }
    if (/\.(html|js|css)$/.test(name)) found.push([`${prefix}${name}`, url]);
  }
  return found;
}

test('no asset carries a cache-bust token', () => {
  const offenders = [];
  for (const [name, url] of assetFiles()) {
    const matches = readFileSync(url, 'utf8').match(/\?v=[0-9A-Za-z.-]+/g);
    if (matches) offenders.push(`${name}: ${[...new Set(matches)].join(', ')}`);
  }
  assert.deepEqual(
    offenders, [],
    '`?v=` 토큰이 돌아왔다 · 서버가 no-cache 를 보내므로 필요 없다:\n  '
    + offenders.join('\n  '),
  );
});

test('the entry chain still loads without them', () => {
  const read = (name) => readFileSync(new URL(name, STATIC), 'utf8');
  assert.match(read('index.html'), /src="\/static\/app\.js"/);
  assert.match(read('app.js'), /import '\.\/js\/main\.js'/);
});

test('the scan actually looks at the files', () => {
  /** 목록이 비면 위 검사는 아무것도 안 보면서 통과한다. */
  const names = assetFiles().map(([name]) => name);
  assert.ok(names.length > 20, `자산을 ${names.length}개밖에 못 찾았다`);
  assert.ok(names.includes('index.html'));
  assert.ok(names.some((name) => name.startsWith('js/')));
  assert.ok(names.some((name) => name.startsWith('panels/')));
});
