import assert from 'node:assert/strict';
import test from 'node:test';

import {
  createMotionStudioGraphScheduler,
} from '../static/js/motion_studio_graph_scheduler.js';

test('graph scheduler coalesces repeated pointer updates into one frame', () => {
  const frames = new Map();
  let nextFrameId = 1;
  let drawCount = 0;
  const scheduler = createMotionStudioGraphScheduler({
    draw: () => { drawCount += 1; },
    requestFrame: (callback) => {
      const frameId = nextFrameId;
      nextFrameId += 1;
      frames.set(frameId, callback);
      return frameId;
    },
    cancelFrame: (frameId) => frames.delete(frameId),
  });

  scheduler.schedule();
  scheduler.schedule();
  scheduler.schedule();
  assert.equal(frames.size, 1);
  assert.equal(drawCount, 0);

  const [[frameId, callback]] = frames.entries();
  frames.delete(frameId);
  callback();
  assert.equal(drawCount, 1);

  scheduler.schedule();
  scheduler.flush();
  assert.equal(frames.size, 0);
  assert.equal(drawCount, 2);
});

test('graph scheduler cancellation leaves the graph unchanged', () => {
  const frames = new Map();
  let drawCount = 0;
  const scheduler = createMotionStudioGraphScheduler({
    draw: () => { drawCount += 1; },
    requestFrame: (callback) => {
      frames.set(7, callback);
      return 7;
    },
    cancelFrame: (frameId) => frames.delete(frameId),
  });

  scheduler.schedule();
  scheduler.cancel();
  assert.equal(frames.size, 0);
  assert.equal(drawCount, 0);
});
