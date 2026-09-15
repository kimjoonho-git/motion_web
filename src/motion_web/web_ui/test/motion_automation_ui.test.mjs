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
  // 시작·예약 버튼은 걷어냈다 · §6-100 · `이 PC 부팅 시 자동 재생` 체크박스가
  // 같은 일을 하고, 두 버튼은 `display:none` 인 채로만 남아 있었다
  for (const id of [
    'motionAutomationEnabled',
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
  for (const gone of ['motionAutomationStartButton', 'motionAutomationReserveButton']) {
    assert.doesNotMatch(html, new RegExp(`id="${gone}"`), `${gone} 가 남아 있다`);
    assert.doesNotMatch(dom, new RegExp(`${gone}:`), `${gone} 등록이 남아 있다`);
  }
  assert.match(html, /value="direct">바로 다음 모션/);
  assert.match(html, /value="dwell">대기 후 다음 모션/);
  assert.match(html, /value="reinitialize" selected>초기 위치 이동 후 다음/);
});

test('automatic repeat uses runtime APIs instead of browser timers', () => {
  // 켜고 끄는 것만 남았다 · §6-100 · 손으로 시작·예약하던 버튼은 화면에서
  // 숨겨진 채였고, `이 PC 부팅 시 자동 재생` 체크박스가 같은 일을 한다
  assert.match(api, /'\/api\/motion-run\/automation'/);
  assert.match(api, /'\/api\/motion-run\/automation\/disable'/);
  assert.match(controller, /configureMotionAutomation/);
  assert.match(controller, /disableMotionAutomation/);
  assert.doesNotMatch(controller, /setTimeout\(.*automation/i);
});

test('global motor activity banner is driven by server status', () => {
  assert.match(html, /id="motorActivityBanner"/);
  assert.match(main, /motor_activity: payload\.motor_activity \|\| \{\}/);
  assert.match(main, /renderMotorActivity\(appState\.latestState\.motor_activity\)/);
  assert.match(main, /setAttribute\('aria-hidden', active \? 'false' : 'true'\)/);
  assert.doesNotMatch(main, /motorActivityBanner\.classList\.toggle\('hidden'/);
});
