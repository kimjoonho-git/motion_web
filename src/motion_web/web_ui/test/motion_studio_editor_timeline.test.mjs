import assert from 'node:assert/strict';
import test from 'node:test';

import {
  motionStudioLayerDataEqual,
  motionStudioLayerDuration,
  motionStudioEditorNextValueScale,
  motionStudioEditorValueBounds,
  motionStudioLayerMotionIds,
  motionStudioShouldEditPoint,
  resolveMotionStudioSelectedLayerId,
  synchronizeMotionStudioEditorTimeline,
} from '../static/js/motion_studio.js';

test('saved points are selectable only during point-curve editing', () => {
  const pointTarget = { point: { point_id: 'point_1' } };

  assert.equal(motionStudioShouldEditPoint('point_curve', pointTarget), true);
  assert.equal(motionStudioShouldEditPoint('time_shift', pointTarget), false);
  assert.equal(motionStudioShouldEditPoint('value_offset', pointTarget), false);
  assert.equal(motionStudioShouldEditPoint('interpolate', pointTarget), false);
  assert.equal(motionStudioShouldEditPoint('point_curve', null), false);
});

function layer(...times) {
  return {
    frames: times.map((time_sec, index) => ({
      frame: index + 1,
      time_sec,
      values: { '1-1': index },
    })),
  };
}

test('layer duration follows the actual last frame', () => {
  assert.equal(motionStudioLayerDuration(layer(2.66, 11.12)), 11.12);
});

test('editor vertical zoom-out has no fixed scale ceiling', () => {
  const first = motionStudioEditorValueBounds(-10, 10, 1);
  const veryWide = motionStudioEditorValueBounds(-10, 10, 1e12);

  assert.deepEqual(first, { minValue: -10, maxValue: 10 });
  assert.equal(veryWide.minValue, -1e13);
  assert.equal(veryWide.maxValue, 1e13);
});

test('500 consecutive vertical zoom-outs keep expanding monotonically', () => {
  let scale = 1;
  let previousSpan = 20;
  for (let index = 0; index < 500; index += 1) {
    scale = motionStudioEditorNextValueScale(scale, 1.7);
    const bounds = motionStudioEditorValueBounds(-10, 10, scale);
    const span = bounds.maxValue - bounds.minValue;
    assert.equal(Number.isFinite(scale), true);
    assert.equal(Number.isFinite(span), true);
    assert.equal(span > previousSpan, true);
    previousSpan = span;
  }
  assert.equal(scale > 1e100, true);
});

test('zoom-in and invalid factors preserve a positive finite scale', () => {
  assert.equal(motionStudioEditorNextValueScale(10, 0.8), 8);
  assert.equal(motionStudioEditorNextValueScale(10, 0), 10);
  assert.equal(motionStudioEditorNextValueScale(10, Number.POSITIVE_INFINITY), 10);
});

test('editor timeline shrinks when an edit removes trailing data', () => {
  const editor = {
    viewStart: 0,
    viewEnd: 25.26,
    selectionStage: 2,
    selectionAnchor: 11.12,
  };

  const changed = synchronizeMotionStudioEditorTimeline(
    editor,
    layer(2.66, 11.12),
    layer(2.66, 11.12, 25.26),
  );

  assert.equal(changed, true);
  assert.equal(editor.viewStart, 0);
  assert.equal(editor.viewEnd, 11.12);
  assert.equal(editor.selectionStage, 0);
  assert.equal(editor.selectionAnchor, null);
});

test('editor timeline expands when an edit creates later data', () => {
  const editor = { viewStart: 0, viewEnd: 11.12, selectionStage: 0, selectionAnchor: null };

  synchronizeMotionStudioEditorTimeline(
    editor,
    layer(2.66, 18.5),
    layer(2.66, 11.12),
  );

  assert.equal(editor.viewEnd, 18.5);
});

test('value-only edits preserve the current zoom', () => {
  const editor = { viewStart: 4, viewEnd: 8, selectionStage: 2, selectionAnchor: 5 };

  const changed = synchronizeMotionStudioEditorTimeline(
    editor,
    layer(2.66, 11.12),
    layer(2.66, 11.12),
  );

  assert.equal(changed, false);
  assert.deepEqual(editor, {
    viewStart: 4,
    viewEnd: 8,
    selectionStage: 2,
    selectionAnchor: 5,
  });
});

test('layer data comparison ignores revision but detects unsaved frame changes', () => {
  const saved = { ...layer(2.66, 11.12), edit_revision: 3 };
  const staleEditor = { ...layer(2.66, 11.12), edit_revision: 2 };
  const changedEditor = { ...layer(2.66, 10.5), edit_revision: 2 };

  assert.equal(motionStudioLayerDataEqual(saved, staleEditor), true);
  assert.equal(motionStudioLayerDataEqual(saved, changedEditor), false);
});

test('opening another layer derives its own graph axis selection', () => {
  const first = {
    frames: [{ time_sec: 0.02, values: { '1-1': 1, '1-2': 2 } }],
  };
  const second = {
    frames: [
      { time_sec: 0.02, values: {} },
      { time_sec: 2.18, values: { '2-1': 3 } },
    ],
  };

  assert.deepEqual(motionStudioLayerMotionIds(first), ['1-1', '1-2']);
  assert.deepEqual(motionStudioLayerMotionIds(second), ['2-1']);
});

test('layer selection stays valid and falls back after deletion', () => {
  const layers = [{ layer_id: 'first' }, { layer_id: 'second' }];

  assert.equal(resolveMotionStudioSelectedLayerId(layers, 'second'), 'second');
  assert.equal(resolveMotionStudioSelectedLayerId(layers, 'deleted'), 'first');
  assert.equal(resolveMotionStudioSelectedLayerId([], 'deleted'), '');
});
