import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import { indexHtml } from '../tools/index_html.mjs';
import {
  motionRunBlockView,
  motionRunTargetView,
} from '../static/js/motion_data.js';

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




// --------------------------------------------------------------------- //
// 대상은 연동 상태가 정한다 · §6-98
// --------------------------------------------------------------------- //






// --------------------------------------------------------------------- //
// 왜 못 누르는지는 늘 한 자리에 · §6-98
// --------------------------------------------------------------------- //

test('이 PC 실행은 실행 설정과 파일이 정한다', () => {
  const ready = motionRunBlockView({ scope: 'local', localReady: true });
  const notReady = motionRunBlockView({
    scope: 'local', localReady: false, localReason: '모션 파일을 선택하세요',
  });

  assert.equal(ready.blocked, false);
  assert.equal(notReady.blocked, true);
  assert.equal(notReady.reason, '모션 파일을 선택하세요');
});

test('슬레이브 PC 는 왜 시작할 수 없는지 글로 말한다', () => {
  /** 버튼을 회색으로만 두면 고장처럼 보인다 · 슬레이브는 시작이 영원히 불가라
   * 더 그렇다. */
  const view = motionRunBlockView({
    scope: 'group',
    availability: { ok: false, active: false, reason: '이 PC 는 슬레이브입니다 · 마스터 PC 에서 시작하세요' },
  });

  assert.equal(view.blocked, true);
  assert.match(view.reason, /마스터 PC 에서 시작하세요/);
});

test('그룹이 도는 중은 막힌 것이 아니다', () => {
  /** 시작은 마스터만이지만 정지는 누구나 · 그룹이 도는 동안이면 슬레이브에서도
   * 세울 수 있어야 한다 · §6-70 */
  const view = motionRunBlockView({
    scope: 'group',
    availability: { ok: false, active: true, reason: '그룹 실행이 진행 중입니다' },
  });

  assert.equal(view.blocked, false);
  assert.equal(view.reason, '');
});







// --------------------------------------------------------------------- //
// 실행과 연동을 가른다 · §6-100
// --------------------------------------------------------------------- //

test('실행 화면에는 대상 선택이 아예 없다', () => {
  // 전에는 `이 PC 만 / 그룹 전체` 를 여기서 골랐다 · 같은 버튼이 대상에 따라
  // 이 PC 를 돌리기도, 참가한 PC 전부를 돌리기도 했다
  for (const id of ['motionRunScopeLocal', 'motionRunScopeGroup', 'motionRunScopeGroupOption']) {
    assert.doesNotMatch(indexHtml, new RegExp(`id="${id}"`), `${id} 가 아직 있다`);
    assert.doesNotMatch(dom, new RegExp(`${id}:`), `${id} 등록이 남아 있다`);
  }
});

test('실행 버튼은 언제나 이 PC 것이다', () => {
  // 모터가 실제로 움직이는 버튼이다 · 같은 이름이 때에 따라 다른 대수를
  // 움직이면 위험하다
  assert.match(
    controller,
    /motionRunStartButton\.addEventListener\(\s*'click', \(\) => startCurrentMotionRun\('once'\)/,
  );
  assert.match(
    controller,
    /motionRunStopButton\.addEventListener\(\s*'click', \(\) => stopCurrentMotionRun\(\)/,
  );
  assert.doesNotMatch(controller, /motionRunScope\(\) === 'group'/);
  const view = motionRunTargetView({
    role: { joined: true, isMaster: true, peerCount: 3 }, chosen: 'group',
  });
  assert.equal(view.scope, 'local', '실행 화면이 그룹으로 돈다');
  assert.equal(view.buttons.start, '1회 시작');
});

test('그룹 실행은 연동 화면이 주인이다', () => {
  for (const id of [
    'coordinationInitializeButton', 'coordinationStartOnceButton',
    'coordinationStartContinuousButton', 'coordinationStopNowButton',
    'coordinationStopAfterButton',
  ]) {
    assert.match(indexHtml, new RegExp(`id="${id}"`), `${id} 가 없다`);
    assert.match(coordination, new RegExp(`el\\.${id}\\?\\.addEventListener`), `${id} 배선 없음`);
  }
});

test('그룹 실행 칸은 마스터에서만 보인다', () => {
  // 조정 노드가 이미 거부한다 — "이 PC 는 연동 슬레이브라 그룹 실행을 시작할
  // 수 없습니다" · 눌러 본 뒤에 알게 하지 않는다
  assert.match(
    coordination,
    /coordinationGroupRunSection\?\.classList\.toggle\(\s*'hidden', !\(joined && config\.is_master === true\)/,
  );
});
