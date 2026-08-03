import assert from 'node:assert/strict';
import test from 'node:test';

import {
  drawMotionStudioEditorGraph,
  drawMotionStudioLayerGraph,
  motionStudioCompositionTracks,
  motionStudioDisplaySegments,
  motionStudioEditorIssueTimes,
  motionStudioLayerTracks,
  motionStudioSampleTrack,
  motionStudioVisiblePoints,
  motionStudioZeroAxisY,
} from '../static/js/motion_studio_graph.js';

test('graph display data is bounded while preserving extrema and time gaps', () => {
  const first = Array.from({ length: 5000 }, (_, index) => ({
    timeSec: index * 0.02,
    value: index === 2500 ? 100 : Math.sin(index / 20),
  }));
  const second = Array.from({ length: 100 }, (_, index) => ({
    timeSec: 200 + (index * 0.02),
    value: -index,
  }));
  const segments = motionStudioDisplaySegments([...first, ...second], 600);

  assert.equal(segments.length, 2);
  assert.ok(segments.flat().length < 700);
  assert.ok(segments[0].some((point) => point.value === 100));

  const visible = motionStudioVisiblePoints(first, 40, 42);
  assert.ok(visible.length < 110);
  assert.ok(visible[0].timeSec <= 40);
  assert.ok(visible.at(-1).timeSec >= 42);
});

test('graph helpers preserve tracks, interpolation, and manual initial values', () => {
  const layer = {
    enabled: true,
    frames: [
      { time_sec: 0.04, values: { '1-1': 4 } },
      { time_sec: 0.02, values: { '1-1': 2 } },
    ],
  };
  const source = motionStudioLayerTracks(layer);
  assert.deepEqual(source.get('1-1'), [
    { timeSec: 0.02, value: 2 },
    { timeSec: 0.04, value: 4 },
  ]);
  assert.equal(motionStudioSampleTrack(source.get('1-1'), 0.03), 3);

  const composed = motionStudioCompositionTracks([layer], [{
    motion_id: '1-1',
    initial_mode: 'manual',
    initial_motion_position_deg: -1,
  }]);
  assert.equal(composed.sampleCount, 2);
  assert.deepEqual(composed.tracks.get('1-1').map((point) => point.value), [2, 4]);
});

function graphFixture(width = 600, height = 320) {
  const calls = [];
  const context = new Proxy({}, {
    get(target, property) {
      if (!(property in target)) {
        target[property] = (...args) => calls.push([property, ...args]);
      }
      return target[property];
    },
    set(target, property, value) {
      calls.push([property, value]);
      target[property] = value;
      return true;
    },
  });
  const hidden = [];
  return {
    calls,
    hidden,
    canvas: {
      width: 0,
      height: 0,
      getBoundingClientRect: () => ({ width, height }),
      getContext: () => context,
    },
    playhead: { classList: { add: (name) => hidden.push(name) } },
  };
}

test('layer graph renderer handles empty data and pixel ratio', () => {
  const fixture = graphFixture();
  const rendered = drawMotionStudioLayerGraph({
    ...fixture,
    tracks: new Map(),
    devicePixelRatio: 2,
  });

  assert.equal(rendered, true);
  assert.equal(fixture.canvas.width, 1200);
  assert.equal(fixture.canvas.height, 640);
  assert.deepEqual(fixture.hidden, ['hidden']);
  assert.ok(fixture.calls.some((call) => call[0] === 'fillText'
    && call[1] === '그래프 데이터 없음'));
});

test('layer graph renderer draws warnings and updates playback', () => {
  const fixture = graphFixture();
  const playbackCalls = [];
  const playback = { state: 'playing', elapsed_sec: 0.02 };

  drawMotionStudioLayerGraph({
    ...fixture,
    tracks: new Map([['1-1', [
      { timeSec: 0.02, value: 1 },
      { timeSec: 0.04, value: 2 },
    ]]]),
    warnings: [{ second_time_sec: 0.03 }],
    playback,
    updatePlayhead: (value) => playbackCalls.push(value),
  });

  assert.ok(fixture.calls.some((call) => call[0] === 'setLineDash'));
  assert.deepEqual(playbackCalls, [playback]);
});

test('zero-degree time axis follows the visible value range', () => {
  assert.equal(motionStudioZeroAxisY(-10, 10, 20, 200), 120);
  assert.equal(motionStudioZeroAxisY(0, 10, 20, 200), 220);
  assert.equal(motionStudioZeroAxisY(-10, 0, 20, 200), 20);
  assert.equal(motionStudioZeroAxisY(1, 10, 20, 200), null);
  assert.equal(motionStudioZeroAxisY(-10, -1, 20, 200), null);
});

test('editor warning lines include range warnings only for displayed axes', () => {
  const validation = {
    conflicts: [{ start_sec: 0.5 }],
    transition_warnings: [{ second_time_sec: 0.7 }],
    range_warnings: [
      { motion_id: '1-2', time_sec: 1.2 },
      { motion_id: '1-3', time_sec: 1.3 },
    ],
  };
  assert.deepEqual(
    motionStudioEditorIssueTimes(validation, ['1-2']),
    [0.5, 0.7, 1.2],
  );
  assert.deepEqual(
    motionStudioEditorIssueTimes(validation, ['1-3']),
    [0.5, 0.7, 1.3],
  );
});

test('editor graph renderer preserves graph metrics and point hit targets', () => {
  const fixture = graphFixture(478, 368);
  const layer = {
    layer_id: 'layer-a',
    frames: [
      { time_sec: 0.02, values: { '1-1': 1 } },
      { time_sec: 0.04, values: { '1-1': 2 } },
    ],
    point_curves: [{
      curve_id: 'curve-a',
      motion_id: '1-1',
      points: [
        { point_id: 'p1', time_sec: 0.02, value_deg: 1 },
        { point_id: 'p2', time_sec: 0.04, value_deg: 2 },
      ],
    }],
  };
  const editor = {
    original: structuredClone(layer),
    working: structuredClone(layer),
    preview: null,
    viewStart: 0,
    viewEnd: 0.04,
    valueScale: 1,
    selectionStage: 0,
    validation: {},
  };
  const legend = { innerHTML: '' };

  const rendered = drawMotionStudioEditorGraph({
    editor,
    canvas: fixture.canvas,
    legend,
    selectedMotionIds: ['1-1'],
    selectionStartText: '0.02',
    selectionEndText: '0.04',
  });

  assert.equal(rendered, true);
  assert.equal(fixture.canvas.width, 478);
  assert.equal(fixture.canvas.height, 368);
  assert.equal(editor.graphMetrics.width, 478);
  assert.equal(editor.graphMetrics.height, 368);
  assert.equal(editor.graphMetrics.viewStart, 0);
  assert.equal(editor.graphMetrics.viewEnd, 0.04);
  assert.equal(editor.graphMetrics.minValue, 0);
  assert.equal(editor.pointHitTargets.length, 2);
  assert.match(legend.innerHTML, /1-1/);
  assert.match(legend.innerHTML, /현재 작업본/);
  assert.ok(fixture.calls.some(
    (call) => call[0] === 'fillText' && call[1] === '0°',
  ));

  editor.valueView = { minValue: -100, maxValue: -50 };
  drawMotionStudioEditorGraph({
    editor,
    canvas: fixture.canvas,
    selectedMotionIds: ['1-1'],
  });
  assert.equal(editor.graphMetrics.minValue, -100);
  assert.equal(editor.graphMetrics.maxValue, -50);

  editor.valueRangeLock = { motionId: '1-1', minValue: -35, maxValue: 45 };
  drawMotionStudioEditorGraph({
    editor,
    canvas: fixture.canvas,
    selectedMotionIds: ['1-1'],
  });
  assert.equal(editor.graphMetrics.minValue, -35);
  assert.equal(editor.graphMetrics.maxValue, 45);

  editor.valueRangeLock = null;
  editor.valueView = null;
  editor.pendingPointCandidate = {
    motionId: '1-1',
    timeSec: 0.03,
    valueDeg: 1.5,
  };
  drawMotionStudioEditorGraph({
    editor,
    canvas: fixture.canvas,
    selectedMotionIds: ['1-1'],
  });
  assert.ok(fixture.calls.some(
    (call) => call[0] === 'fillText' && call[1] === '추가 후보',
  ));
});

test('editor graph renderer accepts cached track maps from the controller', () => {
  const fixture = graphFixture();
  const editor = {
    original: { frames: [] },
    working: { frames: [] },
    preview: null,
    viewStart: 0,
    viewEnd: 0.04,
    valueScale: 1,
    selectionStage: 0,
    validation: {},
  };
  const legend = { innerHTML: '' };
  const cachedTracks = new Map([['7-1', [
    { timeSec: 0, value: 1 },
    { timeSec: 0.04, value: 2 },
  ]]]);

  drawMotionStudioEditorGraph({
    editor,
    canvas: fixture.canvas,
    legend,
    selectedMotionIds: ['7-1'],
    originalTrackMap: cachedTracks,
    workingTrackMap: cachedTracks,
  });

  assert.match(legend.innerHTML, /7-1/);
  assert.equal(editor.graphMetrics.maxValue > 1, true);
});
