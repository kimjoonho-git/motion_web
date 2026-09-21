// 축 하나의 시작과 끝을 숫자로 말한다 · §6-249
//
// 그래프를 눈으로 훑어 「어디서 시작해 어디서 끝나나」를 읽던 것을 표로
// 적어 준다 · 계산을 화면 안에 묻어 두면 시험이 글자만 훑게 되므로 밖으로
// 빼서 **직접 돌려** 본다.

import assert from 'node:assert/strict';
import test from 'node:test';
import { motionStudioAxisEndpoints } from '../static/js/motion_studio_tracks.js';

const TRACK = [
  { timeSec: 0.5, value: -10 },
  { timeSec: 1.0, value: 0 },
  { timeSec: 2.25, value: 35.5 },
];

test('첫 점과 마지막 점을 그대로 읽는다', () => {
  const ends = motionStudioAxisEndpoints(TRACK);

  assert.equal(ends.startTimeSec, 0.5);
  assert.equal(ends.startValueDeg, -10);
  assert.equal(ends.endTimeSec, 2.25);
  assert.equal(ends.endValueDeg, 35.5);
  assert.equal(ends.frameCount, 3);
});

test('길이와 변화량을 같이 준다', () => {
  const ends = motionStudioAxisEndpoints(TRACK);

  assert.equal(ends.durationSec, 1.75);
  assert.equal(ends.deltaDeg, 45.5);
});

test('점이 하나뿐이면 시작과 끝이 같다', () => {
  const ends = motionStudioAxisEndpoints([{ timeSec: 3, value: 7 }]);

  assert.equal(ends.startTimeSec, 3);
  assert.equal(ends.endTimeSec, 3);
  assert.equal(ends.durationSec, 0);
  assert.equal(ends.deltaDeg, 0);
  assert.equal(ends.frameCount, 1);
});

test('값이 없으면 null 이다 · 빈 표를 그리지 않기 위해서다', () => {
  assert.equal(motionStudioAxisEndpoints([]), null);
  assert.equal(motionStudioAxisEndpoints(null), null);
  assert.equal(motionStudioAxisEndpoints(undefined), null);
  assert.equal(motionStudioAxisEndpoints('아님'), null);
});

test('숫자가 아닌 값이 끼면 null 이다 · 「NaN°」 를 적지 않는다', () => {
  assert.equal(motionStudioAxisEndpoints([{ timeSec: 0, value: null }]), null);
  assert.equal(
    motionStudioAxisEndpoints([{ timeSec: 0, value: 1 }, { timeSec: 'x', value: 2 }]),
    null,
  );
});

test('거꾸로 내려가는 동작은 변화량이 음수다', () => {
  const ends = motionStudioAxisEndpoints([
    { timeSec: 0, value: 90 },
    { timeSec: 1, value: -90 },
  ]);

  assert.equal(ends.deltaDeg, -180);
  assert.equal(ends.durationSec, 1);
});
