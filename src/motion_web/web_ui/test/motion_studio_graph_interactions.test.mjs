import assert from 'node:assert/strict';
import test from 'node:test';

import {
  motionStudioGraphPointInside,
  motionStudioMoveDraftPoint,
  motionStudioMoveTangentHandle,
  motionStudioPanEditorGraph,
} from '../static/js/motion_studio_graph_interactions.js';

const metrics = {
  padding: { left: 10, top: 20 },
  plotWidth: 100,
  plotHeight: 200,
  timeFor: (x) => x / 100,
  valueFor: (y) => 100 - y,
};

test('graph interaction boundary includes only the plot area', () => {
  assert.equal(motionStudioGraphPointInside(metrics, 10, 20), true);
  assert.equal(motionStudioGraphPointInside(metrics, 110, 220), true);
  assert.equal(motionStudioGraphPointInside(metrics, 9, 20), false);
});

test('point drag snaps to 20 ms and preserves an occupied point time', () => {
  const point = { point_id: 'moving', time_sec: 0, value_deg: 0 };
  const editor = {
    pointDraft: {
      points: [
        point,
        { point_id: 'occupied', time_sec: 0.06, value_deg: 3 },
      ],
    },
  };
  let result = motionStudioMoveDraftPoint(editor, point, 4.9, 90, metrics);
  assert.equal(point.time_sec, 0.04);
  assert.equal(point.value_deg, 10);
  assert.equal(result.collides, false);

  result = motionStudioMoveDraftPoint(editor, point, 6.1, 80, metrics);
  assert.equal(result.collides, true);
  assert.equal(point.time_sec, 0.04);
});

test('graph pan and tangent movement update only editor view state', () => {
  const editor = {
    valueRangeLock: null,
    panningGraph: {
      startX: 10,
      startY: 20,
      startViewStart: 1,
      startViewEnd: 3,
      startMinValue: -10,
      startMaxValue: 10,
      timeSpan: 2,
      valueSpan: 20,
      moved: false,
    },
  };
  const nextView = motionStudioPanEditorGraph(editor, metrics, 20, 40);
  assert.deepEqual(nextView, { viewStart: 0.8, viewEnd: 2.8 });
  assert.deepEqual(editor.valueView, { minValue: -8, maxValue: 12 });

  const point = { time_sec: 0.5, value_deg: 10, tangent_mode: 'auto' };
  motionStudioMoveTangentHandle(point, 'out', 60, 80, metrics);
  assert.equal(point.tangent_mode, 'smooth');
  assert.equal(point.out_handle.dt_sec > 0, true);
  assert.equal(point.in_handle.dt_sec < 0, true);
});
