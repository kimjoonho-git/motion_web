import assert from 'node:assert/strict';
import test from 'node:test';

import {
  drawMotionStudioEditorGraph,
  drawMotionStudioLayerGraph,
  motionStudioCompositionTracks,
  motionStudioLayerTracks,
  motionStudioSampleTrack,
} from '../static/js/motion_studio_graph.js';

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

function graphFixture(width = 600) {
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
      getBoundingClientRect: () => ({ width }),
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

test('editor graph renderer preserves graph metrics and point hit targets', () => {
  const fixture = graphFixture(900);
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
  assert.equal(editor.graphMetrics.viewStart, 0);
  assert.equal(editor.graphMetrics.viewEnd, 0.04);
  assert.equal(editor.pointHitTargets.length, 2);
  assert.match(legend.innerHTML, /1-1/);
  assert.match(legend.innerHTML, /현재 작업본/);
});
