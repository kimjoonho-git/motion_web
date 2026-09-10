import assert from 'node:assert/strict';
import { readdirSync, readFileSync } from 'node:fs';
import test from 'node:test';
import { escapeHtml } from '../static/js/format.js';

const JS_DIR = new URL('../static/js/', import.meta.url);

/**
 * `escapeHtml` 이 네 곳에 복사돼 있었고 그중 셋이 작은따옴표를 빠뜨렸다 ·
 * `[&<>"]` · §6-63
 *
 * 당장 뚫리지는 않았다 · 그 파일들이 `x='${…}'` 형태를 쓰지 않았다. 하지만
 * 누군가 그렇게 쓰는 날 조용히 뚫린다. 사본을 못 만들게 막는다.
 */
test('escapeHtml is defined only in format.js', () => {
  const offenders = readdirSync(JS_DIR)
    .filter((name) => name.endsWith('.js') && name !== 'format.js')
    .filter((name) => /function escapeHtml\s*\(/.test(
      readFileSync(new URL(name, JS_DIR), 'utf8'),
    ));

  assert.deepEqual(
    offenders,
    [],
    `escapeHtml 사본: ${offenders.join(', ')}\n`
    + "format.js 의 것을 가져다 쓰세요 · 사본은 이스케이프 문자를 빠뜨리기 쉽습니다.",
  );
});

test('escapeHtml covers every character that can break out of markup', () => {
  assert.equal(escapeHtml(`&<>"'`), '&amp;&lt;&gt;&quot;&#39;');
  // 작은따옴표를 빠뜨린 사본이 통과하지 못하게 따로 못 박는다
  assert.equal(escapeHtml("it's"), 'it&#39;s');
  assert.equal(escapeHtml(null), 'null');
});
