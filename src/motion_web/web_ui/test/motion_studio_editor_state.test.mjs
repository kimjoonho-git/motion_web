import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
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


// 「작업본 반영」이 프레임을 안 보내도 되는가 · §6-292
//
// 곡선이 있으면 프레임은 파생물이다 · 그런데 화면은 반영할 때마다 곡선과
// 프레임을 둘 다 올렸다 (10분 레이어 3.7 MB) · 판정은 서버가 한 번 하고
// 화면은 그 답을 나른다 · 규칙을 두 벌로 두면 언젠가 갈린다.

test('처음에는 통째로 보낸다', () => {
  // 모르면 보낸다 · 잘못 빼면 값이 사라진다
  const editor = createMotionStudioEditorSession({
    layer: { layer_id: 'L', frames: [], point_curves: [] },
    operation: 'time_scale', duration: 1, pointTimelineEnd: 1,
  });

  assert.equal(editor.workingFramesDerived, false);
  assert.equal(editor.previewFramesDerived, false);
});

test('되돌리기 기록에 판정이 함께 실린다', () => {
  // 되돌리면 그때의 작업본으로 가므로, 그때의 판정도 함께 돌아와야 한다
  const source = readFileSync(
    new URL('../static/js/motion_studio_editor_controller.js', import.meta.url), 'utf8',
  );
  const entry = source.slice(
    source.indexOf('export function motionStudioEditorHistoryEntry'),
    source.indexOf('export function createMotionStudioEditorController'),
  );

  assert.match(entry, /framesDerived: Boolean\(editor\.workingFramesDerived\)/);
});

test('반영은 판정이 참일 때만 프레임을 뺀다', () => {
  const source = readFileSync(
    new URL('../static/js/motion_studio_editor_controller.js', import.meta.url), 'utf8',
  );

  assert.match(source, /editor\.workingFramesDerived\s*\n?\s*\?\s*\{ \.\.\.editor\.working, frames: \[\] \}/);
  assert.match(source, /: editor\.working;/);
});

test('저장된 것을 새로 받으면 다시 통째로 보낸다', () => {
  const source = readFileSync(
    new URL('../static/js/motion_studio_editor_controller.js', import.meta.url), 'utf8',
  );
  const accept = source.slice(source.indexOf('const acceptSavedEditorLayer'));

  assert.match(accept.slice(0, 900), /editor\.workingFramesDerived = false/);
});
