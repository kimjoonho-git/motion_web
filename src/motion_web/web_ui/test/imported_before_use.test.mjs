import assert from 'node:assert/strict';
import { readdirSync, readFileSync } from 'node:fs';
import test from 'node:test';

const JS = new URL('../static/js/', import.meta.url);
const jsFiles = readdirSync(JS).filter((name) => name.endsWith('.js'));
const sources = new Map(jsFiles.map(
  (name) => [name, readFileSync(new URL(name, JS), 'utf8')],
));

/** 다른 파일이 `export` 한 이름들 · 이름 → 그 이름을 내보낸 파일 */
const exported = new Map();
for (const [file, source] of sources) {
  for (const match of source.matchAll(/^export (?:async )?(?:function|const|class) (\w+)/gm)) {
    exported.set(match[1], file);
  }
}

const declaredHere = (source, name) => (
  new RegExp(`(?:function|class)\\s+${name}\\b`).test(source)
  || new RegExp(`(?:const|let|var)\\s+${name}\\s*[=;]`).test(source)
  // 클래스 안의 메서드 정의 · `this.이름(` 으로만 불린다
  || new RegExp(`^\\s*(?:async\\s+)?${name}\\s*\\([^)]*\\)\\s*\\{`, 'm').test(source)
);

/**
 * 부르기만 하고 들여오지 않은 이름을 잡는다 · §6-130
 *
 * 「고른 것」의 주인을 `motion_studio_editor_selection.js` 로 옮기면서
 * `motion_studio_editor_controller.js` 가 `selectPoint` · `releaseRange` 를
 * **import 없이** 부르게 됐다 · 축 확인란을 누를 때마다 처리기가 통째로
 * `ReferenceError` 로 죽어, 그래프가 다시 그려지지 않았다.
 *
 * 문법 검사도 단위 시험도 이것을 못 잡았다 · 그 파일을 실제로 불러 눌러 보는
 * 시험이 없었기 때문이다. 그래서 여기서 **정적으로** 막는다 · 어떤 이름이
 * 그 파일 안에서 오직 `이름(` 꼴로만 나타난다면, 선언도 import 도 없는 것이다.
 */
test('every sibling export that a module calls is imported there', () => {
  const missing = [];
  for (const [file, source] of sources) {
    for (const [name, owner] of exported) {
      if (owner === file) continue;
      const occurrences = [...source.matchAll(new RegExp(`(.?)\\b${name}\\b`, 'g'))]
        .filter((match) => match[1] !== '.');
      if (!occurrences.length) continue;
      if (declaredHere(source, name)) continue;
      const callSites = occurrences.filter(
        (match) => /^\s*\(/.test(source.slice(match.index + match[0].length)),
      );
      if (!callSites.length || callSites.length !== occurrences.length) continue;
      missing.push(`${file}: ${name} (${owner} 가 내보냄)`);
    }
  }
  assert.deepEqual(
    missing,
    [],
    `import 없이 부르는 이름:\n  ${missing.join('\n  ')}\n`
    + '해당 모듈에서 import 하세요.',
  );
});
