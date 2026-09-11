import assert from 'node:assert/strict';
import test from 'node:test';

import { motionStudioGraphTimeSpan } from '../static/js/motion_studio_graph.js';
import {
  motionStudioOverdubHint,
  motionStudioPlaybackView,
} from '../static/js/motion_studio_playback.js';

/**
 * 추가 녹화 중에 사용자가 알아야 하는 것은 **지금이 몇 초인가** 하나다 · §6-79
 *
 * 녹화된 것이 끝난 뒤가 본무대다 · 축 1-1 이 8.92 초에 끝나면 그 뒤부터 얹는다.
 * 그런데 시간축이 데이터 길이에 묶여 있으면 바로 그 순간부터 플레이헤드가
 * 오른쪽 끝에 붙어 버린다 · 정작 필요한 구간에서 시계가 멈춘다.
 */

const DATA_SEC = 8.92;

const recording = (elapsed) => motionStudioPlaybackView({
  status: { state: 'recording', elapsed_sec: elapsed },
  duration: DATA_SEC,
  timeText: (value) => `${value}`,
});

test('the time axis follows the take past the end of the recorded data', () => {
  const early = motionStudioGraphTimeSpan(DATA_SEC, recording(3.0));
  assert.equal(early, DATA_SEC, '데이터 안에서는 축이 데이터 길이 그대로다');

  const late = motionStudioGraphTimeSpan(DATA_SEC, recording(14.0));
  assert.ok(late > 14.0, '녹화가 데이터를 지났는데 축이 안 늘어난다');
  // 한 칸 앞질러 늘린다 · 딱 맞추면 플레이헤드가 오른쪽 끝에 붙는다
  assert.equal(late, DATA_SEC * 2);
  assert.equal(motionStudioGraphTimeSpan(DATA_SEC, recording(20.0)), DATA_SEC * 3);
});

test('the playhead keeps moving after the recorded data ends', () => {
  const ratio = (elapsed) => {
    const playback = recording(elapsed);
    return playback.playheadTime / motionStudioGraphTimeSpan(DATA_SEC, playback);
  };

  const atEnd = ratio(DATA_SEC);
  const after = ratio(DATA_SEC + 4);
  assert.ok(atEnd <= 1 && after <= 1);
  assert.notEqual(
    after, atEnd,
    '녹화된 데이터가 끝나자 플레이헤드가 멈췄다 · 그 뒤가 추가 녹화의 본무대다',
  );
});

test('an idle graph keeps the plain data axis', () => {
  const idle = motionStudioPlaybackView({
    status: { state: 'idle' }, duration: DATA_SEC, timeText: String,
  });
  assert.equal(motionStudioGraphTimeSpan(DATA_SEC, idle), DATA_SEC);
  assert.equal(motionStudioGraphTimeSpan(DATA_SEC, {}), DATA_SEC);
});

test('the playhead is shown while recording', () => {
  assert.equal(recording(3.0).showPlayhead, true);
  assert.equal(recording(3.0).playheadTime, 3.0);
});


/**
 * 잠금 띠 · 재생이 쥔 구간을 축마다 한 줄로 올린다.
 *
 * 캔버스가 없으니 그리기 호출을 받아 적는 최소한의 2D 문맥으로 확인한다 ·
 * 무엇이 어디에 칠해졌는지만 보면 된다.
 */
function fakeCanvas(width = 760) {
  const fills = [];
  const context = {
    fills,
    setTransform() {}, clearRect() {}, strokeRect() {}, beginPath() {},
    moveTo() {}, lineTo() {}, stroke() {}, save() {}, restore() {},
    fillText() {}, globalAlpha: 1, fillStyle: '', strokeStyle: '', font: '',
    lineWidth: 1,
    fillRect(x, y, w, h) { fills.push({ x, y, w, h, fillStyle: this.fillStyle }); },
  };
  return {
    context,
    canvas: {
      width: 0,
      height: 0,
      getBoundingClientRect: () => ({ width }),
      getContext: () => context,
    },
  };
}

const TRACKS = new Map([
  ['1-1', [{ timeSec: 0.02, value: 0 }, { timeSec: 8.92, value: 30 }]],
  ['1-2', [{ timeSec: 0.02, value: 0 }, { timeSec: 4.0, value: 10 }]],
]);

async function draw(ownedSpans, playback) {
  const { drawMotionStudioLayerGraph } = await import(
    '../static/js/motion_studio_graph.js'
  );
  const { canvas, context } = fakeCanvas();
  drawMotionStudioLayerGraph({
    canvas, playhead: null, tracks: TRACKS, playback, ownedSpans,
    devicePixelRatio: 1,
  });
  return context.fills;
}

test('each locked axis gets its own lane, in its own colour', async () => {
  const fills = await draw(
    { '1-1': [[2.36, 8.92]], '1-2': [[0.0, 4.0]] },
    recording(3.0),
  );

  assert.equal(fills.length, 2, '축마다 한 줄씩 나와야 한다');
  assert.notEqual(fills[0].fillStyle, fills[1].fillStyle, '두 축이 같은 색이다');
  assert.notEqual(fills[0].y, fills[1].y, '두 축이 같은 줄에 겹쳤다');
});

test('a locked span is drawn where its time actually is', async () => {
  const [band] = await draw({ '1-1': [[2.36, 8.92]] }, recording(3.0));
  const [full] = await draw({ '1-1': [[0.0, 8.92]] }, recording(3.0));

  assert.ok(band.x > full.x, '2.36초에서 시작하는 띠가 0초와 같은 자리에 있다');
  assert.ok(band.w < full.w, '띠 길이가 구간을 따르지 않는다');
});

test('an axis with no locked span gets no lane', async () => {
  const fills = await draw({ '1-1': [[0.0, 8.92]] }, recording(3.0));
  assert.equal(fills.length, 1, '데이터가 없는 축까지 잠긴 것으로 그렸다');
});

test('no spans means no lanes at all', async () => {
  assert.equal((await draw(null, recording(3.0))).length, 0);
  assert.equal((await draw({}, recording(3.0))).length, 0);
});


/**
 * 안내 한 줄 · 페이더를 잡은 사람은 그래프를 계속 보고 있지 않다.
 *
 * 축이 여럿이면 일부만 잠긴다 · "지금 녹화가 되는가" 가 아니라 **어느 축이**
 * 되는가를 말해야 한다.
 */
const hint = (spans, elapsed) => motionStudioOverdubHint(spans, elapsed);

test('it names the locked axis and when it frees', () => {
  const line = hint({ '1-1': [[2.36, 8.92]] }, 3.0);
  assert.match(line, /1-1/);
  assert.match(line, /8\.9초까지/);
});

test('before the playback starts every axis is recordable', () => {
  const line = hint({ '1-1': [[2.36, 8.92]] }, 1.0);
  assert.match(line, /전 축 녹화 가능/);
  assert.match(line, /2\.4초부터/, '언제부터 잠기는지 말하지 않는다');
});

test('after every span ends it says so plainly', () => {
  assert.match(hint({ '1-1': [[2.36, 8.92]] }, 12), /끝났습니다/);
});

test('with several axes it says the rest still record', () => {
  const line = hint({ '1-1': [[0, 9]], '1-2': [[0, 4]] }, 6);
  assert.match(line, /1-1/);
  assert.match(line, /나머지 축은 녹화됩니다/);
  assert.doesNotMatch(line, /1-2/, '이미 풀린 축을 잠겼다고 말한다');
});

test('a single locked axis does not claim others are recording', () => {
  const line = hint({ '1-1': [[0, 9]] }, 3);
  assert.match(line, /지금은 녹화되지 않습니다/);
});

test('plain recording shows no overdub hint', () => {
  assert.equal(hint(null, 3), '');
  assert.equal(hint({}, 3), '');
  assert.equal(hint({ '1-1': [] }, 3), '');
});
