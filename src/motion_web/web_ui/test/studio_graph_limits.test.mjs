import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

import { drawMotionStudioAxisLimits } from '../static/js/motion_studio_graph.js';

// 모션축 한계를 점선으로 · §6-120

function recorder() {
  const calls = [];
  return {
    calls,
    save() { calls.push(['save']); },
    restore() { calls.push(['restore']); },
    beginPath() { calls.push(['beginPath']); },
    moveTo(x, y) { calls.push(['moveTo', x, y]); },
    lineTo(x, y) { calls.push(['lineTo', x, y]); },
    stroke() { calls.push(['stroke']); },
    fillText(text, x, y) { calls.push(['fillText', text, x, y]); },
    setLineDash(pattern) { calls.push(['setLineDash', pattern.join(',')]); },
  };
}

const padding = { left: 10, top: 0 };
const yFor = (value) => 100 - value;   // -180..180 이 280..-80 로

test('both limits are drawn with a dashed line', () => {
  const context = recorder();
  const drawn = drawMotionStudioAxisLimits(
    context, padding, 200, -200, 200, yFor,
    [{ motionId: '1-1', minValue: -180, maxValue: 180, color: '#123456' }],
  );
  assert.equal(drawn, 2);
  assert.ok(context.calls.some(([name, pattern]) => name === 'setLineDash' && pattern === '6,4'));
  const labels = context.calls.filter(([name]) => name === 'fillText').map(([, text]) => text);
  assert.deepEqual(labels, ['1-1 최소 -180.0°', '1-1 최대 180.0°']);
});

test('a limit outside the visible value range is not drawn', () => {
  const context = recorder();
  // 보이는 값 범위가 -50~50 인데 한계는 -180~180 · 그리면 테두리에 붙어 눈속임이 된다
  const drawn = drawMotionStudioAxisLimits(
    context, padding, 200, -50, 50, yFor,
    [{ motionId: '1-1', minValue: -180, maxValue: 180 }],
  );
  assert.equal(drawn, 0);
});

test('only the limit that fits is drawn', () => {
  const context = recorder();
  const drawn = drawMotionStudioAxisLimits(
    context, padding, 200, -200, 100, yFor,
    [{ motionId: '1-2', minValue: -180, maxValue: 180 }],
  );
  assert.equal(drawn, 1);
  const labels = context.calls.filter(([name]) => name === 'fillText').map(([, text]) => text);
  assert.deepEqual(labels, ['1-2 최소 -180.0°']);
});

test('nothing to draw is handled', () => {
  const context = recorder();
  assert.equal(drawMotionStudioAxisLimits(context, padding, 200, -1, 1, yFor, []), 0);
  assert.equal(drawMotionStudioAxisLimits(context, padding, 200, -1, 1, yFor, null), 0);
});

// 도구 줄은 그래프 안 오른쪽 위 · 한 줄을 비워 그래프를 키운다 · §6-120

const PANEL = readFileSync(
  fileURLToPath(new URL('../static/panels/09-panel-studio.html', import.meta.url)),
  'utf8',
);
const CSS = readFileSync(
  fileURLToPath(new URL('../static/css/05-studio-editor.css', import.meta.url)),
  'utf8',
);

test('도구 줄은 그래프 위에 따로 선다 · 그림을 가리지 않는다 · §6-247', () => {
  // 전에는 그래프 안에 띄워(position: absolute) 오른쪽 위에 얹었다 ·
  // 높이를 아끼려던 것인데 그림 위에 상자가 겹쳐 앉아 그래프를 가렸다.
  const toolbarAt = PANEL.indexOf('studio-editor-toolbar');
  const canvasAt = PANEL.indexOf('studio-editor-canvas-wrap');
  assert.ok(toolbarAt > 0 && canvasAt > 0, '도구 줄 또는 그래프를 못 찾았다');
  assert.ok(toolbarAt < canvasAt, '도구 줄이 아직 그래프 아래에 있다');

  const wrap = PANEL.slice(canvasAt, PANEL.indexOf('studioEditorLegend'));
  assert.ok(!wrap.includes('studio-editor-toolbar'), '도구 줄이 아직 그래프 상자 안에 있다');
});

test('도구 줄은 가로 한 줄로 눕는다', () => {
  const block = CSS.slice(CSS.indexOf('.studio-editor-toolbar {'));
  assert.doesNotMatch(block.slice(0, 400), /position: absolute;/, '아직 그래프 위에 떠 있다');
  assert.match(block, /flex-direction: row;/);
  assert.match(block, /flex-wrap: wrap;/);
});

test('the graph keeps a taller minimum height', () => {
  const match = CSS.match(/--studio-editor-graph-min-height:\s*(\d+)px/);
  assert.ok(match, '그래프 최소 높이를 찾지 못했다');
  assert.ok(Number(match[1]) >= 400, `아직 ${match[1]}px 이다`);
});

test('남는 높이는 그래프가 가져간다', () => {
  // 첫 줄은 도구 줄(auto) · 그 다음이 그래프(1fr) 다 · 줄을 안 늘리면
  // 도구가 「그래프 자리」를 차지해 실측 414px 로 부풀었다
  const block = CSS.slice(CSS.indexOf('.studio-editor-main {'));
  assert.match(
    block,
    /grid-template-rows: auto minmax\(var\(--studio-editor-graph-min-height\), 1fr\)/,
  );
});

test('짧은 화면에서도 아래 편집 구역에 닿을 수 있다 · §6-250', () => {
  // 그래프 최소 높이가 420px 로 못 박혀 있고 바깥이 overflow: hidden 이라,
  // 화면이 짧으면 편집 단추가 대화상자 밖으로 밀리고 스크롤도 막혔다.
  const block = CSS.slice(CSS.indexOf('.studio-editor-main {'));
  assert.match(block.slice(0, 900), /overflow-y: auto;/, '넘쳐도 내려갈 수 없다');
  // 화면이 낮으면 그래프를 줄여 한 화면에 담는다
  assert.match(CSS, /@media \(max-height: 900px\)/);
  assert.match(CSS, /@media \(max-height: 820px\)/);
  assert.match(CSS, /@media \(max-height: 720px\)/);
  // 오류 목록이 길어도 아래를 밀어내지 않는다
  const details = CSS.slice(CSS.indexOf('.studio-editor-validation-details {'));
  assert.match(details.slice(0, 300), /max-height: \d+px;/);
  assert.match(details.slice(0, 300), /overflow-y: auto;/);
});

// 그리기가 끝까지 돌아야 한다 · §6-120
//
// 한계 점선을 `colors` 선언보다 **위**에서 불렀다가, 아직 만들어지지 않은
// 이름을 건드려 예외가 났다 · 테두리와 0° 선만 그려지고 곡선도 눈금도 안
// 나왔다 · 화면에는 오류가 보이지 않아 원인을 찾기 어려웠다.

import { drawMotionStudioEditorGraph } from '../static/js/motion_studio_graph.js';

function fakeCanvas() {
  const calls = [];
  const context = new Proxy({}, {
    get(_target, name) {
      if (name === 'canvas') return {};
      if (name === 'measureText') return () => ({ width: 10 });
      return (...args) => { calls.push([String(name), ...args]); };
    },
    set() { return true; },
  });
  return {
    calls,
    getBoundingClientRect: () => ({ width: 900, height: 400 }),
    getContext: () => context,
    width: 900,
    height: 400,
  };
}

function layerWithOneAxis() {
  const frames = Array.from({ length: 40 }, (_unused, index) => ({
    time_sec: Number((index * 0.02).toFixed(3)),
    values: { '1-1': Math.sin(index * 0.3) * 40 },
  }));
  return { layer_id: 'L', name: 'L', frames, point_curves: [] };
}

test('the whole graph is drawn, not just the frame', () => {
  const canvas = fakeCanvas();
  const layer = layerWithOneAxis();
  const drawn = drawMotionStudioEditorGraph({
    editor: { original: layer, working: layer, view: null },
    canvas,
    legend: null,
    selectedMotionIds: ['1-1'],
    axisLimits: [{ motionId: '1-1', minValue: -180, maxValue: 180 }],
    devicePixelRatio: 1,
  });
  assert.equal(drawn, true);
  // 눈금 글자는 한계 점선 **뒤에** 그려진다 · 예외가 나면 여기까지 오지 못한다
  const texts = canvas.calls.filter(([name]) => name === 'fillText').map(([, text]) => String(text));
  assert.ok(texts.some((text) => text.endsWith('°')), '값 눈금이 안 그려졌다');
  assert.ok(texts.some((text) => text.endsWith('초')), '시간 눈금이 안 그려졌다');
});
