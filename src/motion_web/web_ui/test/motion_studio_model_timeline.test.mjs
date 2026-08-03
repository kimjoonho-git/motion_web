import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

import {
  motionStudioEditorNextValueScale,
  motionStudioEditorValueBounds,
  motionStudioLayerDataEqual,
  motionStudioLayerDuration,
  motionStudioLayerMotionIds,
  motionStudioMotionAxisRange,
  resolveMotionStudioSelectedLayerId,
  synchronizeMotionStudioEditorTimeline,
} from '../static/js/motion_studio.js';
import {
  motionStudioEditorHistoryEntry,
} from '../static/js/motion_studio_editor_controller.js';

const motionStudioRuntimeSource = () => [
  'motion_studio.js',
  'motion_studio_editor_controller.js',
  'motion_studio_graph_interactions.js',
  'motion_studio_point_editor.js',
].map((name) => readFileSync(
  new URL(`../static/js/${name}`, import.meta.url),
  'utf8',
)).join('\n');


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

test('editor vertical view supports unlimited offsets and exact motion-axis locking', () => {
  assert.deepEqual(
    motionStudioEditorValueBounds(-10, 10, 1, 123456789),
    { minValue: 123456779, maxValue: 123456799 },
  );
  assert.deepEqual(
    motionStudioEditorValueBounds(-10, 10, 100, 999, {
      minValue: -35,
      maxValue: 45,
    }),
    { minValue: -35, maxValue: 45 },
  );
  assert.deepEqual(motionStudioMotionAxisRange([{
    motion_id: '1-2',
    motion_lower_deg: -35,
    motion_upper_deg: 45,
  }], '1-2'), {
    motionId: '1-2',
    minValue: -35,
    maxValue: 45,
  });
  assert.equal(motionStudioMotionAxisRange([{
    motion_id: '1-2',
    motion_lower_deg: 45,
    motion_upper_deg: -35,
  }], '1-2'), null);
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
    rangeSelection: {
      phase: 'complete', start: { timeSec: 5 }, end: { timeSec: 11.12 },
    },
  };

  const changed = synchronizeMotionStudioEditorTimeline(
    editor,
    layer(2.66, 11.12),
    layer(2.66, 11.12, 25.26),
  );

  assert.equal(changed, true);
  assert.equal(editor.viewStart, 0);
  assert.equal(editor.viewEnd, 11.12);
  assert.deepEqual(editor.rangeSelection, {
    phase: 'inactive', start: null, end: null,
  });
});

test('editor timeline expands when an edit creates later data', () => {
  const editor = {
    viewStart: 0,
    viewEnd: 11.12,
    rangeSelection: { phase: 'inactive', start: null, end: null },
  };

  synchronizeMotionStudioEditorTimeline(
    editor,
    layer(2.66, 18.5),
    layer(2.66, 11.12),
  );

  assert.equal(editor.viewEnd, 18.5);
});

test('value-only edits preserve the current zoom', () => {
  const editor = {
    viewStart: 4,
    viewEnd: 8,
    rangeSelection: { phase: 'awaiting_end', start: { timeSec: 5 }, end: null },
  };

  const changed = synchronizeMotionStudioEditorTimeline(
    editor,
    layer(2.66, 11.12),
    layer(2.66, 11.12),
  );

  assert.equal(changed, false);
  assert.deepEqual(editor, {
    viewStart: 4,
    viewEnd: 8,
    rangeSelection: { phase: 'awaiting_end', start: { timeSec: 5 }, end: null },
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

test('editor history restores point-curve selection with each applied edit', () => {
  const editor = {
    working: { point_curves: [{ curve_id: 'curve-a', points: [] }] },
    validation: { playable: true },
    pointDraft: { curve_id: 'curve-a' },
    selectedPointId: 'point-a',
  };
  const history = motionStudioEditorHistoryEntry(editor);
  editor.working.point_curves[0].curve_id = 'changed';
  editor.selectedPointId = 'point-b';
  assert.equal(history.layer.point_curves[0].curve_id, 'curve-a');
  assert.equal(history.curveId, 'curve-a');
  assert.equal(history.selectedPointId, 'point-a');
});

test('studio mutations render their response without an automatic full refresh', () => {
  const source = motionStudioRuntimeSource();

  assert.match(source, /async function run\([\s\S]*?isCurrent = \(\) => true/);
  assert.match(source, /if \(refreshAfter\) await refresh\(false\);/);
  assert.match(
    source,
    /async function runMotorStart\(action, pendingMessage\)/,
  );
  assert.match(
    source,
    /pendingMotorStartAt > 0[\s\S]*?\['initializing', 'recording', 'playing'\]/,
  );
  assert.match(
    source,
    /studioStopButton\.disabled = pendingMotorStartAt > 0[\s\S]*?\? false/,
  );
  assert.match(
    source,
    /onStop: \(\) => \{[\s\S]*?state: 'stopping'[\s\S]*?run\(stopMotionStudio, \{ refreshAfter: true \}\)/,
  );
});

test('editor operations and axis management keep a fixed compact layout', () => {
  const html = readFileSync(
    new URL('../static/index.html', import.meta.url),
    'utf8',
  );
  const styles = readFileSync(
    new URL('../static/styles.css', import.meta.url),
    'utf8',
  );
  const source = motionStudioRuntimeSource();

  assert.match(
    html,
    /<section class="studio-editor-axis-management" aria-label="축 관리">/,
  );
  assert.doesNotMatch(html, /<details class="studio-editor-axis-management"/);
  assert.match(html, /<div class="studio-editor-operations">/);
  assert.doesNotMatch(html, /class="studio-editor-operations hidden"/);
  assert.match(
    html,
    /id="studioEditorOperationHelp" class="studio-editor-operation-help"/,
  );
  assert.match(
    styles,
    /\.studio-editor-operation-choices \{[\s\S]*?grid-template-columns: repeat\(5, minmax\(92px, 1fr\)\);/,
  );
  assert.match(
    styles,
    /\.studio-editor-operation-choices button \{[\s\S]*?white-space: nowrap;/,
  );
  assert.doesNotMatch(
    source,
    /querySelector\('\.studio-editor-operations'\)\?\.classList\.toggle/,
  );
  assert.match(
    source,
    /button\.disabled = Boolean\(editor\?\.preview\) \|\| !workingPointCurve;/,
  );
});

test('layer merge lets the user choose one whole layer to append', () => {
  const html = readFileSync(
    new URL('../static/index.html', import.meta.url),
    'utf8',
  );
  const source = motionStudioRuntimeSource();

  assert.match(
    html,
    /id="studioMergeMode">[\s\S]*?value="preserve">시간 위치 유지<[\s\S]*?value="append">뒤에 이어 붙이기</,
  );
  assert.match(html, /id="studioMergeAppendLayer" disabled/);
  assert.match(html, /지정한 레이어의 전체 축을 나머지 레이어 뒤로 함께 이동/);
  assert.match(
    source,
    /previewMotionStudioMerge\(\{[\s\S]*?append_layer_id: appendLayerId/,
  );
  assert.match(
    source,
    /commitMotionStudioMerge\(\{[\s\S]*?append_layer_id: appendLayerId/,
  );
  assert.match(
    source,
    /state\.mergeMode === 'append'[\s\S]*?state\.mergeAppendLayerId/,
  );
});
