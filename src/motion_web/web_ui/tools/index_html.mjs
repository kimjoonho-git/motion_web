// `index.html` 셸에 조각을 끼워 넣는다 · 시험용 · §6-44
//
// 서버는 `motion_web_bridge/routes/system_routes.py`의 `IndexComposer`가 같은
// 일을 한다. **두 곳의 규칙은 같아야 한다** · 자리표시자 형태와 출처 주석 제거 ·
// 어긋나면 시험이 보는 화면과 실제 화면이 갈라진다.
// `test_index_compose.py::test_javascript_helper_uses_the_same_rules`가 지킨다.
import { readFileSync } from 'node:fs';

const INCLUDE = /^[ \t]*<!--#include ([A-Za-z0-9_./-]+) -->[ \t]*\r?\n/gm;
const PROVENANCE = /^<!--[\s\S]*?-->\r?\n/;

export function composeIndexHtml() {
  const shellUrl = new URL('../static/index.html', import.meta.url);
  const shell = readFileSync(shellUrl, 'utf8');
  return shell.replace(INCLUDE, (_line, name) => {
    const part = readFileSync(new URL(`../static/${name}`, import.meta.url), 'utf8');
    return part.replace(PROVENANCE, '');
  });
}

export const indexHtml = composeIndexHtml();

// 조각난 CSS를 링크 순서대로 이어 하나로 돌려준다 · §6-43
//
// 겹치기(cascade) 순서가 곧 링크 순서다 · 시험도 같은 순서로 봐야 한다.
export function composeStylesCss() {
  const shellUrl = new URL('../static/index.html', import.meta.url);
  const shell = readFileSync(shellUrl, 'utf8');
  const links = [...shell.matchAll(/href="\/static\/(css\/[0-9a-z-]+\.css)/g)]
    .map((match) => match[1]);
  return links
    .map((name) => readFileSync(new URL(`../static/${name}`, import.meta.url), 'utf8')
      .replace(PROVENANCE, ''))
    .join('');
}

export const stylesCss = composeStylesCss();
