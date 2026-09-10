import assert from 'node:assert/strict';
import { readdirSync, readFileSync } from 'node:fs';
import test from 'node:test';

const JS = new URL('../static/js/', import.meta.url);
const TESTS = new URL('./', import.meta.url);

const read = (base, name) => readFileSync(new URL(name, base), 'utf8');
const jsFiles = readdirSync(JS).filter((n) => n.endsWith('.js'));
const sources = new Map(jsFiles.map((n) => [n, read(JS, n)]));
const testText = readdirSync(TESTS)
  .filter((n) => n.endsWith('.mjs'))
  .map((n) => read(TESTS, n))
  .join('\n');

const uses = (text, name) => new RegExp(`\\b${name}\\b`).test(text);

/**
 * 아무 데서도 쓰지 않는 `export` 는 두 가지 중 하나다 · 지울 죽은 코드이거나,
 * 내부 전용인데 `export` 만 붙은 것. 어느 쪽이든 남겨두면 "누군가 쓰겠지" 하고
 * 계속 늘어난다 · 죽은 함수 14개와 불필요한 export 14개가 그렇게 쌓였다 · §6-64
 */
test('every exported symbol is used somewhere', () => {
  const unused = [];
  for (const [file, source] of sources) {
    const names = [...source.matchAll(/^export (?:async )?(?:function|const|class) (\w+)/gm)]
      .map((m) => m[1]);
    for (const name of names) {
      const elsewhere = [...sources]
        .some(([other, text]) => other !== file && uses(text, name));
      if (elsewhere || uses(testText, name)) continue;
      unused.push(`${file}: ${name}`);
    }
  }
  assert.deepEqual(
    unused,
    [],
    `쓰이지 않는 export:\n  ${unused.join('\n  ')}\n`
    + '내부 전용이면 export 를 떼고, 아무도 안 쓰면 지우세요.',
  );
});
