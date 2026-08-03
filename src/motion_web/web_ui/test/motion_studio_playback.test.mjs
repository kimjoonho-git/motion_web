import assert from 'node:assert/strict';
import test from 'node:test';

import {
  createMotionStudioPlaybackController,
  motionStudioPlaybackView,
  syncMotionStudioPlaybackClock,
} from '../static/js/motion_studio_playback.js';

const timeText = (value) => `${Number(value).toFixed(1)}s`;

test('playback view reports initialization, playback, recording, and errors', () => {
  assert.equal(motionStudioPlaybackView({
    status: { state: 'initializing', phase: 'countdown' },
    timeText,
  }).displayState, 'countdown');
  const playing = motionStudioPlaybackView({
    status: {
      state: 'playing',
      playback_duration_sec: 4,
      runtime_progress: { elapsed_sec: 1 },
    },
    duration: 3,
    now: () => 1000,
    timeText,
  });
  assert.equal(playing.label, '모션 재생');
  assert.equal(playing.ratio, 0.25);
  assert.equal(playing.showPlayhead, true);
  assert.equal(motionStudioPlaybackView({
    status: { state: 'error' },
    timeText,
  }).chip, 'danger');
});

test('playback clock advances monotonically and clears outside active states', () => {
  const state = {
    status: { state: 'playing', runtime_progress: { elapsed_sec: 1 } },
    playbackClock: null,
  };
  syncMotionStudioPlaybackClock(state, 1000);
  assert.deepEqual(state.playbackClock, {
    runtimeState: 'playing', sourceElapsed: 1, receivedAt: 1000,
  });
  state.status.runtime_progress.elapsed_sec = 1.5;
  syncMotionStudioPlaybackClock(state, 2000);
  assert.equal(state.playbackClock.sourceElapsed, 2);
  state.status.state = 'idle';
  assert.equal(syncMotionStudioPlaybackClock(state, 3000), null);
});

test('playback controller renders monitor text and cancels animation', () => {
  const classList = { add() {}, remove() {}, toggle() {} };
  const label = { textContent: '' };
  const el = {
    studioLayerPlayhead: {
      classList,
      style: {},
      querySelector: () => label,
    },
    studioLayerGraph: { getBoundingClientRect: () => ({ width: 500 }) },
    studioPlaybackMonitor: { dataset: {} },
    studioPlaybackPhase: {},
    studioPlaybackTime: {},
    studioPlaybackLayerCount: {},
    studioPlaybackProgressBar: { style: {} },
    studioPlaybackMessage: {},
  };
  const state = {
    status: {
      state: 'playing', playback_duration_sec: 2,
      runtime_progress: { elapsed_sec: 1 },
    },
    playbackClock: null,
    playbackAnimationFrame: 0,
    detailGraph: { duration: 2, enabledLayerCount: 2 },
  };
  let frameCallback = null;
  let cancelled = 0;
  const controller = createMotionStudioPlaybackController({
    state,
    el,
    timeText,
    now: () => 0,
    requestFrame: (callback) => { frameCallback = callback; return 7; },
    cancelFrame: (frameId) => { cancelled = frameId; },
  });

  const view = controller.renderMonitor();
  assert.equal(el.studioPlaybackMonitor.dataset.state, 'playing');
  assert.equal(el.studioPlaybackPhase.textContent, '모션 재생');
  assert.equal(el.studioPlaybackProgressBar.style.width, '50.00%');
  controller.updatePlayhead(view);
  assert.equal(label.textContent, '1.0s');
  controller.animate();
  assert.equal(typeof frameCallback, 'function');
  controller.cancel();
  assert.equal(cancelled, 7);
});
