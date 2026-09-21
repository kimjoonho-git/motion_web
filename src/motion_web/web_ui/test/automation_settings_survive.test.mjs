import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import { indexHtml } from '../tools/index_html.mjs';

const html = indexHtml;
const motionData = readFileSync(
  new URL('../static/js/motion_data.js', import.meta.url), 'utf8',
);

/**
 * 부팅 자동 재생을 뺀 뒤에도 **자동 반복 설정은 남아야 한다** · §6-134
 *
 * 둘은 한 파일(`motion_automation.json`)에 같이 살았다.
 *
 *   뺀 것   `enabled` · `armed` — 부팅 때 스스로 시작하는가
 *   남는 것 `repeat_mode` · `dwell_sec` — 한 회차가 끝나면 어떻게 잇는가
 *
 * 스케줄이 발화할 때 `repeat_mode` · `dwell_sec` 를 이 파일에서 읽어 그대로
 * 실어 보낸다 · 같이 지우면 스케줄이 반복 방식을 잃는다.
 */

test('반복 방식과 대기 시간 칸이 화면에 남아 있다', () => {
  assert.match(html, /id="motionAutomationRepeatMode"/);
  assert.match(html, /id="motionAutomationDwellSec"/);
});

test('연속 시작이 반복 방식을 그대로 실어 보낸다', () => {
  assert.match(motionData, /el\.motionAutomationRepeatMode\?\.value/);
  assert.match(motionData, /repeat_mode: repeatMode/);
  assert.match(motionData, /dwell_sec:/);
});

test('부팅 자동 재생은 화면에서 사라졌다', () => {
  // 켜는 곳이 둘이었고 (이 PC · 그룹) 서로 배타적이었다 · 연동을 켜면 로컬이
  // 스스로 꺼지고, 그룹은 필수 PC 가 2대 미만이면 안 떴다 · 그래서 혼자
  // 쓰는 PC 가 연동을 켜 두면 아무것도 안 됐다.
  assert.doesNotMatch(html, /motionAutomationEnabled/);
  assert.doesNotMatch(html, /coordinationAutoPlayToggle/);
  assert.doesNotMatch(html, /automationResumeModal/);
  assert.doesNotMatch(html, /부팅 시 자동 재생/);
  assert.doesNotMatch(html, /전원 재부팅 감지/);
});

test('복구 기계장치가 코드에 남아 조용히 돌지 않는다', () => {
  assert.doesNotMatch(motionData, /resume_pending/);
  assert.doesNotMatch(motionData, /automationResume/);
  assert.doesNotMatch(motionData, /automation\.armed/);
});

test('반복 방식 기본값이 화면 어디서나 같다', () => {
  // 기본값이 여덟 곳에 적혀 있었고 답이 두 가지였다 · 화면은
  // 「초기 위치 이동 후 다음」인데 스케줄만 `direct` 로 쐈다 · §6-135
  const coordination = readFileSync(
    new URL('../static/js/coordination.js', import.meta.url), 'utf8',
  );
  assert.match(html, /value="reinitialize" selected/);
  for (const source of [motionData, coordination]) {
    const fallbacks = [...source.matchAll(/repeat_?[Mm]ode[^\n]*\|\|\s*'(\w+)'/g)]
      .map((match) => match[1]);
    assert.ok(fallbacks.length, '반복 방식 기본값을 읽지 못했다');
    assert.deepEqual(
      [...new Set(fallbacks)],
      ['reinitialize'],
      `화면 기본값이 갈렸다: ${[...new Set(fallbacks)].join(', ')}`,
    );
  }
});


// --------------------------------------------------------------------------- //
// 저장된 값을 **화면에 넣어야** 한다 · §6-268
//
// 전에는 `automation.repeat_mode` 를 읽어 놓고 선택칸에 넣지 않았다 · 화면은
// 언제나 HTML 의 기본값을 보여 줬고, 파일에 다른 값이 적혀 있어도 그대로였다 ·
// 실측으로 파일과 서버는 `direct` 인데 화면은 20초를 기다려도 `reinitialize`
// 였다.
//
// 더 나쁜 것은 그다음이다 · 사람이 다른 값을 고르면 화면에 보이던 잘못된 값이
// 그대로 파일에 덮어써졌다 · 저장해 둔 설정이 화면을 한 번 열었다는 이유로
// 바뀌었다.
// --------------------------------------------------------------------------- //

function renderBody() {
  const start = motionData.indexOf('const repeatMode = String(automation.repeat_mode');
  const end = motionData.indexOf('function renderMotionRunPanel(', start);
  return motionData
    .slice(start, end > start ? end : start + 3000)
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/^\s*\/\/.*$/gm, '');
}

test('저장된 반복 방식을 선택칸에 넣는다', () => {
  assert.match(
    renderBody(),
    /motionAutomationRepeatMode\.value\s*=\s*repeatMode/,
    '읽기만 하고 화면에 넣지 않는다',
  );
});

test('저장된 대기 시간도 칸에 넣는다', () => {
  assert.match(
    renderBody(),
    /motionAutomationDwellSec\.value\s*=/,
    '대기 시간을 화면에 넣지 않는다',
  );
});

test('사람이 그 칸을 만지는 중에는 건드리지 않는다', () => {
  // 고르는 도중에 값이 바뀌면 손이 미끄러진 것처럼 보인다
  const body = renderBody();
  const guards = [...body.matchAll(/document\.activeElement !== el\.motionAutomation\w+/g)];
  assert.ok(guards.length >= 2, `보호가 모자라다: ${guards.length}곳`);
});
