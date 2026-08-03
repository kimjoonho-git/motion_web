import assert from 'node:assert/strict';
import test from 'node:test';

import {
  createMotionStudioEditorViewportController,
} from '../static/js/motion_studio_editor_viewport.js';

function button() {
  let click = () => {};
  return {
    addEventListener: (eventName, handler) => {
      if (eventName === 'click') click = handler;
    },
    click: () => click(),
  };
}

function fixture() {
  const el = {
    studioEditorTimeZoomInButton: button(),
    studioEditorTimeZoomOutButton: button(),
    studioEditorValueZoomInButton: button(),
    studioEditorValueZoomOutButton: button(),
    studioEditorValueRangeLockButton: button(),
    studioEditorFitAllButton: button(),
    studioEditorFitSelectionButton: button(),
  };
  const editor = {
    working: { frames: [{ time_sec: 2, values: {} }] },
    viewStart: 0,
    viewEnd: 2,
    valueScale: 2,
    valueOffset: 3,
    valueView: null,
    valueRangeLock: null,
    graphMetrics: { minValue: -10, maxValue: 10 },
    selectionStartSec: 0.5,
    selectionEndSec: 1.5,
  };
  let drawCount = 0;
  let scheduledCount = 0;
  let resetOptions = null;
  const messages = [];
  const controller = createMotionStudioEditorViewportController({
    el,
    getEditor: () => editor,
    drawGraph: () => { drawCount += 1; },
    scheduleGraph: () => { scheduledCount += 1; },
    renderEditor: () => { drawCount += 1; },
    setMessage: (...args) => messages.push(args),
    resetValueView: (options = {}) => {
      resetOptions = options;
      editor.valueView = options.preserveLockedRange && editor.valueRangeLock
        ? {
          minValue: editor.valueRangeLock.minValue,
          maxValue: editor.valueRangeLock.maxValue,
        }
        : null;
      editor.valueRangeLock = null;
    },
    selectedMotionAxisRange: () => ({
      motionId: '1-1', minValue: -20, maxValue: 20,
    }),
    selectedPointRange: () => ({ points: [{}, {}] }),
    editorDuration: () => 2,
  });
  return {
    controller,
    drawCount: () => drawCount,
    editor,
    el,
    messages,
    resetOptions: () => resetOptions,
    scheduledCount: () => scheduledCount,
  };
}

test('editor viewport owns zoom, fit, and value range interactions', () => {
  const view = fixture();
  view.controller.bind();

  view.el.studioEditorTimeZoomInButton.click();
  assert.equal(view.editor.viewStart, 0.4);
  assert.equal(view.editor.viewEnd, 1.6);

  view.el.studioEditorValueZoomOutButton.click();
  assert.deepEqual(view.editor.valueView, { minValue: -17, maxValue: 17 });
  assert.equal(view.editor.valueScale, 1);
  assert.equal(view.editor.valueOffset, 0);

  view.el.studioEditorValueRangeLockButton.click();
  assert.deepEqual(view.editor.valueRangeLock, {
    motionId: '1-1', minValue: -20, maxValue: 20,
  });
  assert.match(view.messages.at(-1)[0], /세로축 고정/);

  view.el.studioEditorValueRangeLockButton.click();
  assert.deepEqual(view.resetOptions(), { unlock: true, preserveLockedRange: true });
  assert.deepEqual(view.editor.valueView, { minValue: -20, maxValue: 20 });

  view.el.studioEditorFitSelectionButton.click();
  assert.equal(view.editor.viewStart, 0.5);
  assert.equal(view.editor.viewEnd, 1.5);
  assert.ok(view.drawCount() >= 4);
});

test('editor viewport schedules drag redraws without drawing immediately', () => {
  const view = fixture();
  assert.equal(view.controller.setView(1, 2, true), true);
  assert.equal(view.editor.viewStart, 1);
  assert.equal(view.editor.viewEnd, 2);
  assert.equal(view.scheduledCount(), 1);
  assert.equal(view.drawCount(), 0);
});
