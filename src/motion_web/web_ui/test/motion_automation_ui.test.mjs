import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import { indexHtml } from '../tools/index_html.mjs';

const html = indexHtml;
const dom = readFileSync(new URL('../static/js/dom.js', import.meta.url), 'utf8');
const api = readFileSync(new URL('../static/js/api.js', import.meta.url), 'utf8');
const controller = readFileSync(
  new URL('../static/js/motion_data.js', import.meta.url),
  'utf8',
);
const main = readFileSync(new URL('../static/js/main.js', import.meta.url), 'utf8');

test('automatic repeat has explicit enable policy and start controls', () => {
  // 시작·예약 버튼은 걷어냈다 · §6-100
  // 부팅 시 자동 재생도 걷어냈다 · §6-134 · 남은 것은 **반복 방식**뿐이다 ·
  // 한 회차가 끝나면 어떻게 잇는가 · 시작은 사람이나 스케줄이 시킨다
  for (const id of [
    'motionAutomationRepeatMode',
    'motionAutomationDwellSec',
  ]) {
    assert.match(html, new RegExp(`id="${id}"`));
    assert.match(dom, new RegExp(`${id}: document\\.getElementById\\('${id}'\\)`));
  }
  // 화면에서 사라진 요소를 등록부가 계속 가리키면 갱신 코드가 조용히 죽는다 ·
  // motionAutomationStatus 가 그랬다 · §6-59
  assert.doesNotMatch(dom, /motionAutomationStatus/);
  // 눌릴 수 없는 버튼은 남겨 두지 않는다
  for (const gone of [
    'motionAutomationStartButton', 'motionAutomationReserveButton',
    'motionAutomationEnabled', 'motionAutomationToggleWrap',
  ]) {
    assert.doesNotMatch(html, new RegExp(`id="${gone}"`), `${gone} 가 남아 있다`);
    assert.doesNotMatch(dom, new RegExp(`${gone}:`), `${gone} 등록이 남아 있다`);
  }
  assert.match(html, /value="direct">바로 다음 모션/);
  assert.match(html, /value="dwell">대기 후 다음 모션/);
  assert.match(html, /value="reinitialize" selected>초기 위치 이동 후 다음/);
});

test('automatic repeat uses runtime APIs instead of browser timers', () => {
  // 반복 방식을 고치면 그대로 저장한다 · §6-134 · 끄는 길(`/disable`)은
  // 「부팅 시 자동 재생」을 끄기 위한 것이었고 함께 없앴다
  assert.match(api, /'\/api\/motion-run\/automation'/);
  assert.doesNotMatch(api, /automation\/disable/);
  assert.match(controller, /configureMotionAutomation/);
  assert.doesNotMatch(controller, /disableMotionAutomation/);
  assert.doesNotMatch(controller, /setTimeout\(.*automation/i);
});

test('global motor activity banner is driven by server status', () => {
  assert.match(html, /id="motorActivityBanner"/);
  assert.match(main, /motor_activity: payload\.motor_activity \|\| \{\}/);
  assert.match(main, /renderMotorActivity\(appState\.latestState\.motor_activity\)/);
  assert.match(main, /setAttribute\('aria-hidden', active \? 'false' : 'true'\)/);
  assert.doesNotMatch(main, /motorActivityBanner\.classList\.toggle\('hidden'/);
});
