import assert from 'node:assert/strict';
import test from 'node:test';

import { createMotionStudioRecordingPreview }
  from '../static/js/motion_studio_recording_preview.js';
import { motionStudioLayerTracks }
  from '../static/js/motion_studio_tracks.js';

/**
 * 그래프 머리말 · 지금 화면에 무엇이 보이는지를 말해야 한다 · §6-85
 *
 * "화면 표시 간격 80ms · 원본은 20ms로 기록" 은 만드는 사람에게나 뜻이 있는
 * 말이고, 정작 점선·실선·띠가 무엇인지는 아무 데도 없었다.
 *
 * "시간" 은 **녹화한 길이**여야 한다 · 그래프 축 길이를 적었더니 추가 녹화
 * 시작부터 기존 레이어 길이(8.92초)가 떠 있고 그 시간이 지나도록 꿈쩍하지
 * 않아, 멈춘 것처럼 보였다.
 */

const BASE_SEC = 8.92;

function el() {
  const make = () => ({ textContent: '', innerHTML: '' });
  return {
    studioLayerDetailName: make(),
    studioLayerDetailStatus: make(),
    studioLayerDetailFrames: make(),
    studioLayerDetailDuration: make(),
    studioLayerDetailAxisCount: make(),
    studioLayerGraphLegend: make(),
    studioLayerDetailTabs: { querySelectorAll: () => [] },
    studioLayerDetail: { querySelectorAll: () => [] },
  };
}

function render({ elapsed, recordedIds = [], overdub = true, frames = [] }) {
  const target = el();
  const state = {
    status: {
      state: 'recording',
      record_mode: overdub ? 'overdub' : 'record',
      elapsed_sec: elapsed,
      recorded_frames: Math.round(elapsed / 0.02),
      recording_preview_frames: frames,
      recording_preview_stride: 4,
      recording_motion_ids: recordedIds,
    },
    project: { layers: [] },
  };
  const base = {
    tracks: new Map([['1-1', [
      { timeSec: 0.02, value: 0 }, { timeSec: BASE_SEC, value: 30 },
    ]]]),
    duration: BASE_SEC,
    enabledLayers: [{}],
  };
  const drawn = [];
  const render = createMotionStudioRecordingPreview({
    state,
    el: target,
    escapeHtml: (value) => String(value),
    layerTracks: motionStudioLayerTracks,
    compositionTracks: () => base,
    activeMapping: () => ({ rows: [] }),
    renderPlaybackMonitor: () => ({ displayState: 'recording' }),
    drawLayerGraph: (...args) => drawn.push(args),
    periodSec: 0.02,
  });
  assert.equal(render(), true);
  return { target, drawn, state };
}

test('the time shown is what has been recorded, not the graph width', () => {
  const early = render({ elapsed: 2.0 });
  assert.equal(
    early.target.studioLayerDetailDuration.textContent, '2.000초',
    '그래프 축 길이를 적어 시간이 멈춘 것처럼 보인다',
  );

  const later = render({ elapsed: 12.0 });
  assert.equal(later.target.studioLayerDetailDuration.textContent, '12.000초');
});

test('the time keeps moving through the locked span', () => {
  /** 추가 녹화의 앞부분은 기존 레이어가 도는 구간이다 · 그동안 시간이 안 움직이면
   *  사용자는 녹화가 안 되고 있다고 생각한다. */
  const seen = [1.0, 3.0, 5.0, 7.0].map(
    (sec) => render({ elapsed: sec }).target.studioLayerDetailDuration.textContent,
  );
  assert.equal(new Set(seen).size, 4, `시간이 멈춰 있다 · ${seen.join(', ')}`);
});

test('the header says what the dashed and solid lines are', () => {
  const { target } = render({ elapsed: 3.0 });
  assert.equal(target.studioLayerDetailName.textContent, '추가 녹화 중');
  assert.match(target.studioLayerDetailStatus.textContent, /점선/);
  assert.match(target.studioLayerDetailStatus.textContent, /실선/);
  assert.match(target.studioLayerDetailStatus.textContent, /잠김/);
  assert.doesNotMatch(
    target.studioLayerDetailStatus.textContent, /원본은 20ms/,
    '만드는 사람에게나 뜻이 있는 말이 남아 있다',
  );
});

test('plain recording does not mention the dashed base line', () => {
  const { target } = render({ elapsed: 3.0, overdub: false });
  assert.equal(target.studioLayerDetailName.textContent, '녹화 중');
  assert.doesNotMatch(target.studioLayerDetailStatus.textContent, /점선/);
});

test('the axis count is what has actually been recorded', () => {
  assert.equal(
    render({ elapsed: 3.0, recordedIds: [] }).target.studioLayerDetailAxisCount.textContent,
    '0개',
  );
  assert.equal(
    render({ elapsed: 12.0, recordedIds: ['1-1', '1-2'] })
      .target.studioLayerDetailAxisCount.textContent,
    '2개',
  );
});

test('the legend names the dashed base line too', () => {
  const { target } = render({
    elapsed: 12.0,
    frames: [{ time_sec: 11.0, values: { '1-1': 5 } }],
  });
  assert.match(target.studioLayerGraphLegend.innerHTML, /기존\(점선\)/);
  assert.match(target.studioLayerGraphLegend.innerHTML, /녹화/);
});

test('the graph still gets the base tracks and the preview interval', () => {
  const { drawn } = render({
    elapsed: 12.0,
    frames: [{ time_sec: 11.0, values: { '1-1': 5 } }],
  });
  const [, , baseTracks, sampleIntervalSec] = drawn[0];
  assert.ok(baseTracks instanceof Map, '기존 레이어가 그려지지 않는다');
  assert.equal(sampleIntervalSec, 0.08, '솎아낸 간격이 전달되지 않는다');
});
