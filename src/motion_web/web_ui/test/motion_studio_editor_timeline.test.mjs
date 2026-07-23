import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

import {
  motionStudioLayerDataEqual,
  motionStudioLayerDuration,
  motionStudioEditorNextValueScale,
  motionStudioEditorValueBounds,
  motionStudioLayerMotionIds,
  motionStudioPointCurvePreview,
  motionStudioPointCurveViewEnd,
  motionStudioPointDragStarted,
  motionStudioPointHitTarget,
  motionStudioSelectionKindsMatch,
  motionStudioShouldEditPoint,
  resolveMotionStudioSelectedLayerId,
  synchronizeMotionStudioEditorTimeline,
} from '../static/js/motion_studio.js';

test('a point click edits points only in point mode so general edits can select it', () => {
  const pointTarget = { point: { point_id: 'point_1' } };

  assert.equal(motionStudioShouldEditPoint('point_curve', pointTarget), true);
  assert.equal(motionStudioShouldEditPoint('time_shift', pointTarget), false);
  assert.equal(motionStudioShouldEditPoint('value_offset', pointTarget), false);
  assert.equal(motionStudioShouldEditPoint('interpolate', pointTarget), false);
  assert.equal(motionStudioShouldEditPoint('point_curve', null), false);
});

test('edit ranges allow only point-to-point or motion-to-motion selection', () => {
  assert.equal(motionStudioSelectionKindsMatch('point', 'point'), true);
  assert.equal(motionStudioSelectionKindsMatch('motion', 'motion'), true);
  assert.equal(motionStudioSelectionKindsMatch('point', 'motion'), false);
  assert.equal(motionStudioSelectionKindsMatch('motion', 'point'), false);
  assert.equal(motionStudioSelectionKindsMatch('', 'point'), false);
});

test('primary edit workflow actions stay in the fixed top action area', () => {
  const html = readFileSync(
    new URL('../static/index.html', import.meta.url),
    'utf8',
  );
  const actionArea = html.match(
    /<div class="studio-editor-primary-actions"[\s\S]*?<\/div>\s*<div class="studio-editor-layout">/,
  )?.[0] || '';
  assert.match(actionArea, /id="studioEditorApplyButton"/);
  assert.match(actionArea, /id="studioEditorUpdateButton"/);
  assert.match(actionArea, /id="studioEditorSaveButton"/);
  assert.match(actionArea, /id="studioEditorUndoButton"/);
  assert.match(actionArea, /id="studioEditorRedoButton"/);
  assert.match(actionArea, />1\. 결과 미리보기</);
  assert.match(actionArea, />2\. 편집 반영</);
  assert.match(actionArea, />3\. 저장</);
  for (const id of [
    'studioEditorApplyButton',
    'studioEditorUpdateButton',
    'studioEditorSaveButton',
    'studioEditorUndoButton',
    'studioEditorRedoButton',
  ]) {
    assert.equal((html.match(new RegExp(`id="${id}"`, 'g')) || []).length, 1);
  }
});

test('point curves expose separate detach and destructive delete actions', () => {
  const html = readFileSync(
    new URL('../static/index.html', import.meta.url),
    'utf8',
  );
  const source = readFileSync(
    new URL('../static/js/motion_studio.js', import.meta.url),
    'utf8',
  );
  assert.match(html, /id="studioEditorCurveDetachButton"[^>]*>포인트 연결 해제</);
  assert.match(html, /id="studioEditorCurveDeleteButton"[^>]*>곡선 구간 삭제</);
  assert.match(source, /applyEditorOperation\('detach_point_curve', false\)/);
  assert.match(source, /points \|\| \[\]\)\.length <= 2/);
  assert.match(source, /selection_kind: editor\.selectionKind \|\| 'motion'/);
});

test('saving keeps the layer editor open and refreshes its saved baseline', () => {
  const source = readFileSync(
    new URL('../static/js/motion_studio.js', import.meta.url),
    'utf8',
  );
  const saveFlow = source.match(
    /const acceptSavedEditorLayer =[\s\S]*?const setEditorView =/,
  )?.[0] || '';
  assert.match(saveFlow, /editor\.original = clone\(savedLayer\)/);
  assert.match(saveFlow, /저장 완료 · 창을 닫지 않고 편집을 계속할 수 있습니다/);
  assert.doesNotMatch(saveFlow, /if \(result\) \{\s*closeLayerEditor\(\)/);
});

test('whole-layer range selection is grouped with range inputs, not axis management', () => {
  const html = readFileSync(
    new URL('../static/index.html', import.meta.url),
    'utf8',
  );
  const sidebar = html.match(
    /<aside class="studio-editor-sidebar">[\s\S]*?<\/aside>/,
  )?.[0] || '';
  const scope = html.match(
    /<div id="studioEditorScopeControls"[\s\S]*?<\/div>/,
  )?.[0] || '';
  assert.doesNotMatch(sidebar, /studioEditorSelectWholeRangeButton/);
  assert.match(scope, /studioEditorStart/);
  assert.match(scope, /studioEditorEnd/);
  assert.match(scope, /studioEditorSelectWholeRangeButton/);
});

test('point hit target is forgiving and a click does not become a drag', () => {
  const target = { x: 100, y: 50, point: { point_id: 'point_1' } };
  assert.equal(motionStudioPointHitTarget([target], 113, 50), target);
  assert.equal(motionStudioPointHitTarget([target], 115, 50), null);
  assert.equal(
    motionStudioPointDragStarted({ startX: 100, startY: 50, moved: false }, 102, 51),
    false,
  );
  assert.equal(
    motionStudioPointDragStarted({ startX: 100, startY: 50, moved: false }, 104, 50),
    true,
  );
});

test('point-curve workspace is not limited by the existing axis duration', () => {
  assert.equal(motionStudioPointCurveViewEnd(0.02), 10);
  assert.equal(motionStudioPointCurveViewEnd(7.86, 20), 20);
  assert.equal(motionStudioPointCurveViewEnd(7.86, 20, 35), 35);
});

test('point draft renders a visible curve before server apply', () => {
  const points = [
    { point_id: 'start', time_sec: 0, value_deg: 0, tangent_mode: 'auto' },
    { point_id: 'end', time_sec: 5, value_deg: 10, tangent_mode: 'auto' },
  ];
  for (const order of [1, 3, 5]) {
    const preview = motionStudioPointCurvePreview(points, order);
    assert.equal(preview.length > 2, true);
    assert.deepEqual(preview[0], { timeSec: 0, value: 0 });
    assert.deepEqual(preview.at(-1), { timeSec: 5, value: 10 });
    assert.equal(Math.abs(preview[Math.floor(preview.length / 2)].value - 5) < 1e-9, true);
  }
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
