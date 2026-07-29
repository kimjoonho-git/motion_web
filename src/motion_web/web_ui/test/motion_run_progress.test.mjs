import assert from 'node:assert/strict';
import test from 'node:test';

import { effectiveMotionRunProgress } from '../static/js/motion_data.js';

test('running graph follows runtime cycle progress instead of overall start time', () => {
  const progress = effectiveMotionRunProgress({
    state: 'running',
    run_mode: 'continuous',
    lifecycle: {
      motion_started_at: 90,
    },
    progress: {
      elapsed_sec: 0.4,
      duration_sec: 9,
    },
    updated_at: 100,
  }, {
    nowSec: 100.1,
  });

  assert.ok(Math.abs(progress.elapsed_sec - 0.5) < 1e-9);
});

test('dwell graph stays at the motion file end position', () => {
  const progress = effectiveMotionRunProgress({
    state: 'waiting',
    summary: {
      duration_sec: 9,
    },
    progress: {
      elapsed_sec: 0,
      duration_sec: 0,
    },
  }, {
    nowSec: 200,
  });

  assert.equal(progress.elapsed_sec, 9);
  assert.equal(progress.ratio, 1);
});
