import assert from 'node:assert/strict';
import test from 'node:test';

import {
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
