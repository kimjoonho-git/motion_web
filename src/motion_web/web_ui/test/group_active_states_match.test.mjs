import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

/**
 * 「그룹 실행 중」 상태 목록이 화면과 파이썬에서 같아야 한다 · §6-145
 *
 * 이 목록이 두 곳에 따로 있었다 · 스케줄 점검이 세 번째로 쓸 뻔했다 ·
 * 파이썬 쪽 주인은 `motion_common/run_state.py` 다.
 *
 * 화면은 파이썬을 import 할 수 없다 · 그래서 여기서 **두 목록이 같은지**를
 * 본다 · 한쪽에 단계를 더하고 다른 쪽을 빠뜨리면 걸린다.
 */
test('그룹 실행 중 상태 목록이 화면과 파이썬에서 같다', () => {
  const python = readFileSync(
    new URL('../../../motion_common/motion_common/run_state.py', import.meta.url),
    'utf8',
  );
  const js = readFileSync(
    new URL('../static/js/coordination.js', import.meta.url), 'utf8',
  );

  const pythonBlock = python.match(
    /GROUP_ACTIVE_STATES = frozenset\(\{([\s\S]*?)\}\)/,
  )?.[1];
  const jsBlock = js.match(/const activeStates = new Set\(\[([\s\S]*?)\]\)/)?.[1];
  assert.ok(pythonBlock, '파이썬 목록을 읽지 못했다');
  assert.ok(jsBlock, '화면 목록을 읽지 못했다');

  const names = (text) => [...text.matchAll(/'([a-z_]+)'/g)].map((m) => m[1]).sort();

  assert.deepEqual(
    names(jsBlock),
    names(pythonBlock),
    '두 목록이 갈렸다 · 한쪽에만 단계를 더했는지 보세요',
  );
});
