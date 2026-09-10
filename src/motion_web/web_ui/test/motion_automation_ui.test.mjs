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
  for (const id of [
    'motionAutomationEnabled',
    'motionAutomationRepeatMode',
    'motionAutomationDwellSec',
    'motionAutomationStartButton',
  ]) {
    assert.match(html, new RegExp(`id="${id}"`));
    assert.match(dom, new RegExp(`${id}: document\\.getElementById\\('${id}'\\)`));
  }
  // 화면에서 사라진 요소를 등록부가 계속 가리키면 갱신 코드가 조용히 죽는다 ·
  // motionAutomationStatus 가 그랬다 · §6-59
  assert.doesNotMatch(dom, /motionAutomationStatus/);
  assert.match(html, /value="direct">바로 다음 모션/);
  assert.match(html, /value="dwell">대기 후 다음 모션/);
  assert.match(html, /value="reinitialize" selected>초기 위치 이동 후 다음/);
});

test('automatic repeat uses runtime APIs instead of browser timers', () => {
  assert.match(api, /'\/api\/motion-run\/automation'/);
  assert.match(api, /'\/api\/motion-run\/automation\/start'/);
  assert.match(api, /'\/api\/motion-run\/automation\/disable'/);
  assert.match(controller, /configureMotionAutomation/);
  assert.match(controller, /startMotionAutomation/);
  assert.match(controller, /disableMotionAutomation/);
  assert.doesNotMatch(
    controller.slice(
      controller.indexOf('async function startCurrentMotionAutomation()'),
      controller.indexOf('function bindEvents()'),
    ),
    /setTimeout|setInterval/,
  );
});

test('global motor activity banner is driven by server status', () => {
  assert.match(html, /id="motorActivityBanner"/);
  assert.match(main, /motor_activity: payload\.motor_activity \|\| \{\}/);
  assert.match(main, /renderMotorActivity\(appState\.latestState\.motor_activity\)/);
  assert.match(main, /setAttribute\('aria-hidden', active \? 'false' : 'true'\)/);
  assert.doesNotMatch(main, /motorActivityBanner\.classList\.toggle\('hidden'/);
});
