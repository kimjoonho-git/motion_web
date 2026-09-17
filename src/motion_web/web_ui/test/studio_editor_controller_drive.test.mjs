import assert from 'node:assert/strict';
import test from 'node:test';

/**
 * 편집기 컨트롤러를 **실제로 띄워서 눌러본다** · §6-130
 *
 * 나머지 시험들은 순수 함수를 부르거나 소스 문자열을 정규식으로 본다 · 둘 다
 * `motion_studio_editor_controller.js` 를 **불러오지 않는다**. 그래서 1718줄짜리
 * 그 파일에서 import 한 줄이 빠져도 426개가 전부 통과했고, 축 확인란을 누를
 * 때마다 처리기가 `ReferenceError` 로 죽는 것을 아무도 못 잡았다.
 *
 * 여기서는 가짜 DOM 위에 진짜 컨트롤러를 올리고 사람이 하는 순서대로 누른다 ·
 * 파일을 쪼개든 옮기든, 이 시험이 그 파일을 실제로 실행한다.
 */

const JS = new URL('../static/js/', import.meta.url);
const listeners = new WeakMap();

function fakeClassList() {
  const set = new Set();
  return {
    add: (...c) => c.forEach((x) => set.add(x)),
    remove: (...c) => c.forEach((x) => set.delete(x)),
    toggle: (c, on) => (on === undefined
      ? (set.has(c) ? set.delete(c) : set.add(c))
      : (on ? set.add(c) : set.delete(c))),
    contains: (c) => set.has(c),
  };
}

const makeInput = (value, checked) => ({
  tagName: 'INPUT', type: 'checkbox', value, checked,
  dataset: {}, style: {}, classList: fakeClassList(), closest: () => null,
});

function makeEl(id) {
  return {
    id, tagName: 'DIV', value: '', textContent: '', title: '',
    disabled: false, hidden: false,
    dataset: {}, style: {}, classList: fakeClassList(),
    children: [], _inputs: [], _html: '',
    get innerHTML() { return this._html; },
    set innerHTML(html) {
      this._html = html;
      this._inputs = [...html.matchAll(/<input[^>]*>/g)].map((match) => makeInput(
        /value="([^"]*)"/.exec(match[0])?.[1] || '',
        / checked/.test(match[0]),
      ));
    },
    querySelectorAll(selector) {
      if (!this._inputs.length) return [];
      return selector.includes(':checked')
        ? this._inputs.filter((input) => input.checked)
        : this._inputs;
    },
    querySelector: () => null,
    appendChild() {}, replaceChildren() {}, setAttribute() {}, removeAttribute() {},
    getAttribute: () => '',
    closest: () => null,
    getBoundingClientRect: () => ({ width: 900, height: 360, left: 0, top: 0 }),
    getContext: () => new Proxy({}, { get: () => () => ({}) }),
    addEventListener(type, handler) {
      if (!listeners.has(this)) listeners.set(this, {});
      const byType = listeners.get(this);
      (byType[type] = byType[type] || []).push(handler);
    },
    removeEventListener() {},
    dispatchEvent(event) {
      (listeners.get(this)?.[event.type] || []).forEach((handler) => handler(event));
      return true;
    },
    focus() {}, blur() {}, click() {},
    forEach() {}, map: () => [], filter: () => [], length: 0,
  };
}

function installFakeDom() {
  const cache = new Map();
  const el = new Proxy({}, {
    get(_target, key) {
      if (typeof key !== 'string') return undefined;
      if (!cache.has(key)) cache.set(key, makeEl(key));
      return cache.get(key);
    },
    has: () => true,
  });
  globalThis.document = {
    body: { classList: fakeClassList() },
    getElementById: () => null,
    querySelectorAll: () => [],
    createElement: () => makeEl('tmp'),
    addEventListener() {},
  };
  globalThis.window = {
    devicePixelRatio: 1,
    requestAnimationFrame: (callback) => { callback(); return 1; },
    cancelAnimationFrame() {},
    addEventListener() {},
  };
  globalThis.requestAnimationFrame = globalThis.window.requestAnimationFrame;
  globalThis.cancelAnimationFrame = globalThis.window.cancelAnimationFrame;
  globalThis.Event = class {
    constructor(type, options = {}) { this.type = type; this.bubbles = options.bubbles; }
  };
  return el;
}

const el = installFakeDom();
const { createMotionStudioEditorController } = await import(
  new URL('motion_studio_editor_controller.js', JS)
);
const { motionStudioLayerTracks } = await import(new URL('motion_studio_tracks.js', JS));
const selection = await import(new URL('motion_studio_editor_selection.js', JS));

/** 「전체 포인트 생성」까지 끝난 레이어 · 축마다 반영된 포인트 곡선이 있다 */
function makeLayer() {
  const frames = [];
  for (let i = 0; i <= 100; i += 1) {
    frames.push({ time_sec: i * 0.02, values: { '1-1': i, '1-2': i * 2, '1-3': i * 3 } });
  }
  const curveFor = (motionId) => ({
    curve_id: `c_${motionId}`,
    motion_id: motionId,
    interpolation_order: 3,
    points: [0, 25, 50, 75, 100].map((i) => ({
      point_id: `p_${motionId}_${i}`, time_sec: i * 0.02, value: i, tangent_mode: 'auto',
    })),
  });
  return {
    layer_id: 'L1',
    name: '시험 레이어',
    locked: false,
    frames,
    point_curves: ['1-1', '1-2', '1-3'].map(curveFor),
  };
}

function openEditor() {
  const state = { editor: null, composition: null };
  const controller = createMotionStudioEditorController({
    state,
    el,
    clone: structuredClone,
    escapeHtml: (value) => String(value),
    activeMapping: () => ({ rows: [] }),
    configuredMotors: () => [],
    editorAxisLabel: (motionId) => motionId,
    layerPointCoverageIssues: () => [],
    editorValidationProject: () => ({}),
    cachedLayerTracks: (layer) => motionStudioLayerTracks(layer),
    run: async (task) => task(),
  });
  controller.bind();
  controller.openLayerEditor(makeLayer());
  return { state, controller };
}

const axisList = () => el.studioEditorAxisList;
const shownAxes = () => [...el.studioEditorLegend.innerHTML.matchAll(/<\/i>([^<]+)</g)]
  .map((match) => match[1]);

const uncheck = (motionId) => {
  const input = axisList().querySelectorAll('input').find((one) => one.value === motionId);
  input.checked = false;
  axisList().dispatchEvent(new Event('change', { bubbles: true }));
};

const pickRange = (state, startSec, endSec) => {
  selection.setSelectionMode(state.editor, 'range');
  selection.restartRange(state.editor);
  state.editor.rangeSelection.start = { timeSec: startSec, motionId: '1-1' };
  state.editor.rangeSelection.end = { timeSec: endSec, motionId: '1-1' };
  state.editor.rangeSelection.phase = 'complete';
};

const chooseOperation = (operation) => {
  el.studioEditorOperation.value = operation;
  el.studioEditorOperation.dispatchEvent(new Event('change', { bubbles: true }));
};

const RANGE_OPERATIONS = ['time_shift', 'time_scale', 'value_offset', 'value_scale'];

test('축 확인란을 누르면 그래프가 고른 축만 보인다', () => {
  openEditor();
  assert.deepEqual(shownAxes(), ['1-1', '1-2', '1-3'], '편집기를 열면 전부 보인다');

  uncheck('1-2');
  assert.deepEqual(shownAxes(), ['1-1', '1-3'], '끈 축은 그래프에서 빠진다');

  uncheck('1-3');
  assert.deepEqual(shownAxes(), ['1-1'], '하나만 남기면 그 축만 보인다');

  uncheck('1-1');
  assert.deepEqual(shownAxes(), [], '전부 끄면 아무것도 안 그린다');
});

test('축 선택 처리기는 예외 없이 돈다', () => {
  openEditor();
  // import 를 빠뜨리면 여기서 ReferenceError 로 터진다 · 실제로 터졌었다
  assert.doesNotThrow(() => uncheck('1-2'));
});

test('구간을 잡으면 구간 편집 네 가지 모두 미리보기가 눌린다', () => {
  const { state } = openEditor();
  pickRange(state, 0.2, 1.6);
  for (const operation of RANGE_OPERATIONS) {
    chooseOperation(operation);
    assert.equal(
      el.studioEditorApplyButton.disabled,
      false,
      `${operation} · 전체 포인트를 생성하고 구간을 잡았는데 미리보기가 안 눌린다`,
    );
  }
});

test('구간을 안 잡으면 구간 편집 미리보기는 안 눌린다', () => {
  openEditor();
  for (const operation of RANGE_OPERATIONS) {
    chooseOperation(operation);
    assert.equal(el.studioEditorApplyButton.disabled, true, `${operation} · 구간이 없다`);
  }
});

test('구간에 포인트가 없으면 미리보기는 안 눌린다', () => {
  const { state } = openEditor();
  // 포인트는 0 · 0.5 · 1.0 · 1.5 · 2.0 초에 있다 · 그 사이 빈 구간을 잡는다
  pickRange(state, 1.02, 1.48);
  for (const operation of RANGE_OPERATIONS) {
    chooseOperation(operation);
    assert.equal(el.studioEditorApplyButton.disabled, true, `${operation} · 구간에 포인트가 없다`);
  }
});

test('구간 편집은 축을 여러 개 골라도 미리보기가 눌린다', () => {
  const { state } = openEditor();
  pickRange(state, 0.2, 1.6);
  chooseOperation('value_scale');
  assert.equal(el.studioEditorApplyButton.disabled, false);
  assert.deepEqual(
    axisList().querySelectorAll('input:checked').map((input) => input.value),
    ['1-1', '1-2', '1-3'],
    '세 축이 그대로 골라져 있어야 한다',
  );
});
