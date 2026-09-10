import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import { indexHtml } from '../tools/index_html.mjs';

const controller = readFileSync(
  new URL('../static/js/motion_data.js', import.meta.url), 'utf8',
);
const coordination = readFileSync(
  new URL('../static/js/coordination.js', import.meta.url), 'utf8',
);
const main = readFileSync(new URL('../static/js/main.js', import.meta.url), 'utf8');
const dom = readFileSync(new URL('../static/js/dom.js', import.meta.url), 'utf8');

/**
 * "이 PC"와 "그룹"은 같은 모터 경로를 두고 다투는 배타 관계다 ·
 * 그룹이 도는 동안 브리지가 로컬 실행을 거절한다.
 *
 * 전에는 같은 이름의 버튼이 두 화면에 두 벌 있었다 · "1회 시작"이 한쪽은 이 PC,
 * 다른 쪽은 전체 PC였다. 이름만 보고는 구별할 수 없어 위험했다 · §6-65
 */
test('the run screen chooses a scope instead of duplicating the buttons', () => {
  for (const id of ['motionRunScopeLocal', 'motionRunScopeGroup']) {
    assert.match(indexHtml, new RegExp(`id="${id}"`));
    assert.match(dom, new RegExp(`${id}: document\\.getElementById\\('${id}'\\)`));
  }
  assert.match(controller, /function motionRunScope\(\)/);
});

test('group scope routes every run command to the coordination facade', () => {
  for (const [button, call] of [
    ['motionRunInitializeButton', 'groupRun.initialize()'],
    ['motionRunStartButton', 'groupRun.start(groupRunOverrides())'],
    ['motionRunContinuousStartButton', 'groupRun.startContinuous(groupRunOverrides())'],
    ['motionRunStopButton', 'groupRun.stopNow()'],
    ['motionRunStopAfterButton', 'groupRun.stopAfterCycle()'],
  ]) {
    const start = controller.indexOf(`el.${button}.addEventListener`);
    assert.ok(start >= 0, `${button} 연결이 없다`);
    const body = controller.slice(start, start + 400);
    assert.match(body, /motionRunScope\(\) === 'group'/, `${button} 이 범위를 보지 않는다`);
    assert.ok(body.includes(call), `${button} 이 ${call} 를 부르지 않는다`);
  }
});

test('the coordination screen no longer owns run buttons', () => {
  for (const id of [
    'coordinationInitializeButton', 'coordinationStartButton',
    'coordinationContinuousStartButton', 'coordinationStopNowButton',
    'coordinationStopAfterButton',
  ]) {
    assert.doesNotMatch(indexHtml, new RegExp(`id="${id}"`), `${id} 가 남아 있다`);
    assert.doesNotMatch(dom, new RegExp(`\\b${id}\\b`), `${id} 등록이 남아 있다`);
  }
  // 규칙은 남아야 한다 · 실행 화면이 같은 판정을 그대로 쓴다
  assert.match(coordination, /function groupRunAvailability\(\)/);
  assert.match(main, /groupRun: coordination\.groupRun/);
});
