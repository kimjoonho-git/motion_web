import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
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


// 곡선 위에 포인트를 더할 때는 **그 곡선에 넣는다** · §6-294
//
// 전에는 편집 묶음이 없으면 빈 묶음을 새로 만들고 거기에 넣었다 · 화면에는
// 원래 곡선(96점)이 그려져 있는데 실제로 만진 것은 점 하나짜리 딴 묶음이라,
// 후보만 사라지고 아무 일도 안 나는 것처럼 보였다 · 그 뒤로는 그 유령 묶음
// 때문에 다른 축 선택까지 막혔다.

test('편집 묶음이 없으면 빈 묶음이 생긴다 (고치기 전 모습)', () => {
  const editor = { pointDraft: null, selectedPointId: '' };

  const result = addMotionStudioDraftPoint(
    editor, { motionId: '1-1', timeSec: 0.5, valueDeg: 3 },
    { curveId: 'new', pointId: 'p1', interpolationOrder: 1 },
  );

  assert.equal(result.ok, true);
  assert.equal(editor.pointDraft.points.length, 1, '점 하나짜리 묶음이 된다');
  assert.equal(editor.pointDraft.curve_id, 'new');
});

test('기존 곡선을 먼저 실으면 그 곡선에 더해진다', () => {
  // 편집기가 후보 자리의 곡선을 실어 준 뒤의 모습
  const editor = {
    pointDraft: {
      curve_id: 'c1', motion_id: '1-1', interpolation_order: 1,
      points: [
        { point_id: 'a', time_sec: 0.02, value_deg: 0 },
        { point_id: 'b', time_sec: 1.00, value_deg: 10 },
      ],
    },
    selectedPointId: '',
  };

  const result = addMotionStudioDraftPoint(
    editor, { motionId: '1-1', timeSec: 0.5, valueDeg: 5 },
    { curveId: 'ignored', pointId: 'p2', interpolationOrder: 1 },
  );

  assert.equal(result.ok, true);
  assert.equal(editor.pointDraft.curve_id, 'c1', '원래 곡선에 들어가야 한다');
  assert.deepEqual(
    editor.pointDraft.points.map((point) => point.time_sec),
    [0.02, 0.5, 1.00],
  );
});

test('편집기가 후보 자리의 곡선을 찾아 싣는다', () => {
  const source = readFileSync(
    new URL('../static/js/motion_studio_editor_controller.js', import.meta.url), 'utf8',
  );

  assert.match(source, /const adoptCurveAtCandidate = \(editor, candidate\)/);
  assert.match(source, /if \(found\) loadPointDraft\(found, ''\);/);

  const handler = readFileSync(
    new URL('../static/js/motion_studio_point_editor.js', import.meta.url), 'utf8',
  );
  const addBlock = handler.slice(handler.indexOf('studioEditorPointAddButton'));
  assert.match(addBlock.slice(0, 2200), /adoptCurveAtCandidate\?\.\(editor, candidate\)/);
});
