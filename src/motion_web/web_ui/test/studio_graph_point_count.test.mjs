import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

// 그래프에 찍힌 포인트가 몇 개인지 보여 준다 · §6-112
//
// 포인트를 만들어 놓고도 몇 개인지 알 길이 없어, 정밀도를 어느 쪽으로 옮겨야
// 하는지 판단할 수 없었다.

const source = readFileSync(
  fileURLToPath(new URL('../static/js/motion_studio_graph.js', import.meta.url)),
  'utf8',
);

const legendBlock = () => {
  const start = source.indexOf('if (legend) {');
  assert.ok(start > 0, '범례를 그리는 자리를 찾지 못했다');
  return source.slice(start, start + 1200);
};

test('the legend reports how many points are drawn', () => {
  assert.match(legendBlock(), /포인트 \$\{pointTotal\}개/);
});

test('the count comes from the curves actually drawn', () => {
  const block = legendBlock();
  assert.match(block, /displayedCurves\.filter/);
  assert.match(block, /selected\.has\(curve\.motion_id\)/);
});

test('several axes are listed one by one with a total', () => {
  assert.match(legendBlock(), /합계 \$\{pointTotal\}개/);
});


// 「최대 포인트 수」는 상한이지 그릴 개수가 아니다 · §6-113

const panel = readFileSync(
  fileURLToPath(new URL('../static/panels/09-panel-studio.html', import.meta.url)),
  'utf8',
);

test('the point budget field says it is a maximum', () => {
  const label = panel.match(
    /<span>([^<]*)<\/span>\s*<input id="studioEditorApproximationMaximumPoints"/,
  );
  assert.ok(label, '포인트 수 칸의 이름을 찾지 못했다');
  assert.match(label[1], /최대/, '상한이라는 것이 이름에 없다');
});


// 용어 · 「합성 미리보기」 → 「레이어 재생」 · §6-115

test('the studio calls it layer playback, not composition preview', () => {
  assert.equal(panel.includes('합성 미리보기'), false, '옛 이름이 남아 있다');
  assert.match(panel, /레이어 재생/);
});
