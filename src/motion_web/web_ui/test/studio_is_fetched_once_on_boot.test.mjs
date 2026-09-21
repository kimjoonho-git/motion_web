// 스튜디오 레이어를 **두 번 받지 않는다** · §6-260
//
// 조회 하나가 레이어를 전부 실어 온다 · 10분짜리 모션이 있으면 5.2MB 다 ·
// 시작할 때 한 번, 탭을 누를 때 또 한 번 받아서 탭을 여는 데 실측 11.2초가
// 걸렸다 · 시작할 때는 **보고 있을 때만** 받는다.

import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';

const main = readFileSync(new URL('../static/js/main.js', import.meta.url), 'utf8');

function withoutComments(text) {
  return text.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');
}

test('시작할 때 부르는 자리는 스튜디오를 보고 있는지 먼저 본다', () => {
  const body = withoutComments(main);
  const index = body.lastIndexOf('motionStudio.refresh(false)');
  assert.ok(index > 0, '시작할 때 부르는 자리를 못 찾았다');
  const before = body.slice(Math.max(0, index - 200), index);
  assert.match(
    before,
    /workspaceRouteState\.current\(\)\)?\s*===\s*'studio'/,
    '조건 없이 받고 있다',
  );
});

test('탭을 누를 때는 그대로 받는다 · 최신 상태를 보여 줘야 한다', () => {
  const body = withoutComments(main);
  const onTab = [...body.matchAll(/if \(target === 'studio'\) (await )?motionStudio\.refresh\(false\)/g)];
  assert.ok(onTab.length >= 2, `탭에서 받는 자리가 ${onTab.length}개뿐이다`);
});

test('시작할 때 받는 자리는 하나뿐이다', () => {
  const body = withoutComments(main);
  const unconditional = [...body.matchAll(/^motionStudio\.refresh\(/gm)];
  assert.equal(unconditional.length, 0, '조건 없이 시작하며 받는 자리가 남아 있다');
});
