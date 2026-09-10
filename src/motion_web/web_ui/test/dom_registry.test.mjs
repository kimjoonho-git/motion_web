import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import { indexHtml } from '../tools/index_html.mjs';

const dom = readFileSync(new URL('../static/js/dom.js', import.meta.url), 'utf8');
const html = indexHtml;

/**
 * 화면에서 사라진 요소를 등록부가 계속 가리키면, 그 요소를 갱신하는 코드가
 * `if (el.X)` 안에서 조용히 아무 일도 하지 않는다. 실제로 26개가 그렇게 남아
 * 있었고 자동 반복 상태 문구는 계산만 되고 버려졌다 · §6-61
 *
 * 요소를 화면에서 뺄 때는 등록부와 그것을 쓰는 코드도 함께 정리해야 한다.
 */
test('dom registry only points at elements that exist in the page', () => {
  const registered = [...dom.matchAll(/getElementById\('([^']+)'\)/g)].map((m) => m[1]);
  assert.ok(registered.length > 100, '등록부를 읽지 못했다');

  const present = new Set([...html.matchAll(/\bid="([^"]+)"/g)].map((m) => m[1]));
  const dangling = registered.filter((id) => !present.has(id));

  assert.deepEqual(
    dangling,
    [],
    `화면에 없는 요소를 등록부가 가리킨다: ${dangling.join(', ')}\n`
    + '요소를 되살리거나, 등록과 그것을 쓰는 코드를 함께 지우세요.',
  );
});

test('dom registry has no duplicate keys', () => {
  const keys = [...dom.matchAll(/^\s{4}(\w+):\s*document\./gm)].map((m) => m[1]);
  const seen = new Set();
  const duplicated = keys.filter((k) => (seen.has(k) ? true : (seen.add(k), false)));
  assert.deepEqual(duplicated, [], `중복 등록: ${duplicated.join(', ')}`);
});
