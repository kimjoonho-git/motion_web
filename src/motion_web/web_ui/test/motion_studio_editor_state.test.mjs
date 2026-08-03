import assert from 'node:assert/strict';
import test from 'node:test';

import {
  createMotionStudioEditorSession,
  motionStudioEditorFailureFingerprint,
  motionStudioEditorLayerIsDirty,
  motionStudioEditorPointCurves,
  motionStudioPointDraftHasUnsavedChanges,
  motionStudioSelectedDraftPoint,
  motionStudioSelectedPointRange,
} from '../static/js/motion_studio_editor_state.js';

function pointCurve() {
  return {
    curve_id: 'curve-a',
    motion_id: '1-1',
    interpolation_order: 1,
    points: [
      { point_id: 'p1', time_sec: 0, value_deg: 1, tangent_mode: 'linear' },
      { point_id: 'p2', time_sec: 1, value_deg: 2, tangent_mode: 'linear' },
    ],
  };
}

test('editor session owns independent saved and working layer copies', () => {
  const layer = {
    layer_id: 'layer-a',
    frames: [{ time_sec: 0, values: { '1-1': 1 } }],
    point_curves: [pointCurve()],
  };
  const session = createMotionStudioEditorSession({
    layer,
    operation: 'value_offset',
    duration: 1,
    pointTimelineEnd: 2,
    rangeWarnings: [{ motion_id: '1-1' }],
  });

  session.working.frames[0].values['1-1'] = 9;
  assert.equal(layer.frames[0].values['1-1'], 1);
  assert.equal(session.original.frames[0].values['1-1'], 1);
  assert.equal(session.viewEnd, 1);
  assert.deepEqual(session.validation.range_warnings, [{ motion_id: '1-1' }]);
});

test('editor point selectors preserve curve range and linear compatibility', () => {
  const stored = pointCurve();
  const editor = {
    working: { point_curves: [stored] },
    pointDraft: structuredClone(stored),
    selectedPointId: 'p2',
    selectionStartSec: 0,
    selectionEndSec: 1,
    selectionMotionId: '1-1',
    selectionCurveId: 'curve-a',
  };
  editor.pointDraft.points.forEach((point) => { point.tangent_mode = 'auto'; });

  assert.equal(motionStudioEditorPointCurves(editor.working).length, 1);
  assert.equal(motionStudioSelectedDraftPoint(editor).point_id, 'p2');
  assert.deepEqual(
    motionStudioSelectedPointRange(editor).points.map((point) => point.point_id),
    ['p1', 'p2'],
  );
  assert.equal(motionStudioPointDraftHasUnsavedChanges(editor), false);

  editor.pointDraft.points[1].value_deg = 3;
  assert.equal(motionStudioPointDraftHasUnsavedChanges(editor), true);
  assert.match(motionStudioEditorFailureFingerprint(editor), /"value_deg":3/);
});

test('editor layer dirty comparison is reused until a layer copy changes', () => {
  const editor = {
    original: { frames: [] },
    working: { frames: [] },
  };
  let comparisonCount = 0;
  const equal = (first, second) => {
    comparisonCount += 1;
    return JSON.stringify(first) === JSON.stringify(second);
  };

  assert.equal(motionStudioEditorLayerIsDirty(editor, equal), false);
  assert.equal(motionStudioEditorLayerIsDirty(editor, equal), false);
  assert.equal(comparisonCount, 1);

  editor.working = { frames: [{ time_sec: 0 }] };
  assert.equal(motionStudioEditorLayerIsDirty(editor, equal), true);
  assert.equal(comparisonCount, 2);
});
