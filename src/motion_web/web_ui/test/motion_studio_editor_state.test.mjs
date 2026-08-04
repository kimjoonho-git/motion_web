import assert from 'node:assert/strict';
import test from 'node:test';

import {
  createMotionStudioEditorSession,
  motionStudioBeginPointDrag,
  motionStudioBeginTangentDrag,
  motionStudioEditorFailureFingerprint,
  motionStudioEditorLayerIsDirty,
  motionStudioEditorPointCurves,
  motionStudioPointDraftHasUnsavedChanges,
  motionStudioSelectedDraftPoint,
  motionStudioSelectedPointRange,
  motionStudioSelectedTimeRange,
  motionStudioResetRangeSelection,
  motionStudioSelectRangePoint,
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
  assert.deepEqual(session.rangeSelection, {
    phase: 'inactive', start: null, end: null,
  });
  assert.deepEqual(session.validation.range_warnings, [{ motion_id: '1-1' }]);
});

test('editor point selectors preserve curve range and linear compatibility', () => {
  const stored = pointCurve();
  const editor = {
    working: { point_curves: [stored] },
    pointDraft: structuredClone(stored),
    selectedPointId: 'p2',
    rangeSelection: {
      phase: 'complete',
      start: { pointId: 'p1', timeSec: 0, motionId: '1-1', curveId: 'curve-a' },
      end: { pointId: 'p2', timeSec: 1, motionId: '1-1', curveId: 'curve-a' },
    },
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

test('range selection accepts points from different selected axes and orders global time', () => {
  const editor = { rangeSelection: { phase: 'awaiting_start', start: null, end: null } };
  const target = (pointId, timeSec, curveId = 'curve-a', motionId = '1-1') => ({
    curve: { curve_id: curveId, motion_id: motionId },
    point: { point_id: pointId, time_sec: timeSec, value_deg: timeSec * 10 },
  });

  assert.deepEqual(
    motionStudioSelectRangePoint(editor, target('p2', 2)),
    {
      ok: true,
      phase: 'awaiting_end',
      target: {
        pointId: 'p2', motionId: '1-1', curveId: 'curve-a', timeSec: 2,
        valueDeg: 20,
      },
    },
  );
  assert.equal(motionStudioSelectRangePoint(editor, target('p2', 2)).reason, 'same_point');
  assert.equal(
    motionStudioSelectRangePoint(editor, target('other', 2, 'curve-b', '2-1')).reason,
    'same_time',
  );

  const complete = motionStudioSelectRangePoint(
    editor,
    target('p1', 1, 'curve-b', '2-1'),
  );
  assert.equal(complete.ok, true);
  assert.equal(editor.rangeSelection.phase, 'complete');
  assert.deepEqual(
    [editor.rangeSelection.start.motionId, editor.rangeSelection.end.motionId],
    ['2-1', '1-1'],
  );
  assert.deepEqual(motionStudioSelectedTimeRange(editor), {
    startSec: 1,
    endSec: 2,
    start: editor.rangeSelection.start,
    end: editor.rangeSelection.end,
  });
  assert.equal(motionStudioSelectedPointRange(editor), null);
});

test('single point and tangent drags clear range state and competing gestures', () => {
  const editor = {
    rangeSelection: { phase: 'complete', start: {}, end: {} },
    draggingHandle: { side: 'in' },
    panningGraph: { moved: false },
  };
  const pointTarget = {
    curve: { curve_id: 'curve-a', motion_id: '1-1' },
    point: { point_id: 'p1', time_sec: 0 },
  };

  assert.equal(motionStudioBeginPointDrag(editor, pointTarget, 10, 20, false), true);
  assert.equal(editor.draggingPoint.pointId, 'p1');
  assert.equal(editor.draggingPoint.activated, false);
  assert.equal(editor.draggingHandle, null);
  assert.equal(editor.panningGraph, null);
  assert.equal(editor.rangeSelection.phase, 'inactive');

  motionStudioResetRangeSelection(editor, true);
  assert.equal(motionStudioBeginTangentDrag(editor, 'out'), true);
  assert.deepEqual(editor.draggingHandle, { side: 'out' });
  assert.equal(editor.draggingPoint, null);
  assert.equal(editor.rangeSelection.phase, 'inactive');
});
