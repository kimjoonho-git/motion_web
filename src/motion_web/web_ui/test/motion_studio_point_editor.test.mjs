import assert from 'node:assert/strict';
import test from 'node:test';

import {
  addMotionStudioDraftPoint,
  applyMotionStudioCopiedPointRange,
  applyMotionStudioDeletedPointRange,
  deleteMotionStudioDraftPoint,
  updateMotionStudioDraftPoint,
} from '../static/js/motion_studio_point_editor.js';

const point = (id, time, value = 0) => ({
  point_id: id,
  time_sec: time,
  value_deg: value,
  tangent_mode: 'auto',
});

test('point creation snaps to 20 ms and rejects occupied time', () => {
  const editor = { pointDraft: null, selectedPointId: '' };
  const added = addMotionStudioDraftPoint(
    editor,
    { motionId: '1-1', timeSec: 0.031, valueDeg: 2.5 },
    { curveId: 'curve-1', pointId: 'point-1', interpolationOrder: 5 },
  );
  assert.equal(added.ok, true);
  assert.equal(added.point.time_sec, 0.04);
  assert.equal(editor.pointDraft.interpolation_order, 5);
  assert.equal(addMotionStudioDraftPoint(
    editor,
    { motionId: '1-1', timeSec: 0.04, valueDeg: 8 },
    { curveId: 'curve-1', pointId: 'point-2', interpolationOrder: 5 },
  ).reason, 'time_conflict');
});

test('point update and delete preserve curve constraints', () => {
  const editor = {
    pointDraft: { points: [point('a', 0), point('b', 0.1), point('c', 0.2)] },
    selectedPointId: 'b',
  };
  assert.equal(updateMotionStudioDraftPoint(editor, editor.pointDraft.points[1], {
    timeSec: 0.14,
    valueDeg: 12,
    tangentMode: 'smooth',
  }).ok, true);
  assert.equal(editor.pointDraft.points[1].time_sec, 0.14);
  assert.equal(deleteMotionStudioDraftPoint(editor, 'b').ok, true);
  assert.equal(deleteMotionStudioDraftPoint(editor, 'a').reason, 'minimum_points');
});

test('range copy and deletion replace one draft without changing the source curve', () => {
  const curve = { curve_id: 'curve', points: [point('a', 0), point('b', 0.1)] };
  const editor = {};
  let sequence = 0;
  const copied = applyMotionStudioCopiedPointRange(editor, curve, {
    ok: true,
    points: [point('copy', 0.2)],
  }, () => `new-${sequence += 1}`);
  assert.equal(copied[0].point_id, 'new-1');
  assert.equal(curve.points.length, 2);
  assert.equal(editor.pointDraft.points.length, 3);

  assert.equal(applyMotionStudioDeletedPointRange(editor, curve, {
    ok: true,
    points: [point('a', 0), point('copy', 0.2)],
  }), true);
  assert.deepEqual(editor.pointDraft.points.map((item) => item.point_id), ['a', 'copy']);
});
