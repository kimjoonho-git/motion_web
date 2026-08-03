import assert from 'node:assert/strict';
import test from 'node:test';

import {
  motionStudioPointCurvePreview,
} from '../static/js/motion_studio_calculations.js';
import {
  MOTION_STUDIO_PERIOD_MS,
  MOTION_STUDIO_PERIOD_SEC,
} from '../static/js/motion_studio_constants.js';

const POINTS = [
  { time_sec: 0, value_deg: 0, tangent_mode: 'auto' },
  { time_sec: 0.04, value_deg: 8, tangent_mode: 'auto' },
  { time_sec: 0.08, value_deg: 0, tangent_mode: 'auto' },
];
const GOLDEN_VALUES = {
  1: [0, 4, 8, 4, 0],
  3: [0, 4, 8, 4, 0],
  5: [0, 3.75, 8, 3.75, 0],
};

test('browser curve preview matches backend 20 ms golden vectors', () => {
  assert.equal(MOTION_STUDIO_PERIOD_SEC, 0.02);
  assert.equal(MOTION_STUDIO_PERIOD_MS, 20);
  for (const order of [1, 3, 5]) {
    const preview = motionStudioPointCurvePreview(POINTS, order);
    const samples = Array.from({ length: 5 }, (_, index) => {
      const timeSec = index * MOTION_STUDIO_PERIOD_SEC;
      return preview.find((point) => Math.abs(point.timeSec - timeSec) < 1e-9)?.value;
    });
    samples.forEach((value, index) => {
      assert.ok(Math.abs(value - GOLDEN_VALUES[order][index]) < 1e-9);
    });
  }
});
