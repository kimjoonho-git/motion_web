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


// --------------------------------------------------------------------- //
// 대상은 연동 상태가 정한다 · §6-98
// --------------------------------------------------------------------- //

test('참가하지 않았으면 그룹 칸이 아예 없다', () => {
  /** 고를 수 없는 것을 고르게 해놓고 막는 것이 헷갈림의 뿌리였다 ·
   * 참가하지 않은 채 "그룹 전체"를 고르면 버튼이 전부 회색이 되는데 이유는
   * 작은 힌트 글씨 한 줄에만 나왔다. */
  const view = motionRunTargetView({ role: { joined: false }, chosen: 'group' });

  assert.equal(view.scope, 'local', '고를 수 없는 것이 골라졌다');
  assert.equal(view.groupSelectable, false);
  assert.equal(view.needsCoordinationSetup, true, '참가할 길이 없다');
  assert.match(view.coordinationLinkLabel, /참가하기/);
});

test('그룹에서 나가면 고른 값이 남아 있어도 이 PC 로 돌아온다', () => {
  const view = motionRunTargetView({ role: { joined: false, peerCount: 3 }, chosen: 'group' });

  assert.equal(view.scope, 'local');
});

test('참가했으면 그룹을 고를 수 있고 대수가 이름에 박힌다', () => {
  const view = motionRunTargetView({
    role: { joined: true, peerCount: 3 }, chosen: 'group',
  });

  assert.equal(view.scope, 'group');
  assert.equal(view.groupLabel, '그룹 3대');
  assert.equal(view.needsCoordinationSetup, false);
});

test('대상 한 줄 요약도 같은 판정에서 나온다', () => {
  const alone = motionRunTargetView({ role: { joined: true, peerCount: 3 }, chosen: 'local' });
  const group = motionRunTargetView({ role: { joined: true, peerCount: 3 }, chosen: 'group' });

  assert.match(alone.summary, /이 PC 에 연결된 모터만/);
  assert.match(group.summary, /3대가 같은 시각에/);
});

test('버튼 이름이 몇 대를 움직이는지 말한다', () => {
  /** 전에는 같은 [1회 시작] 이 1대일 수도 3대일 수도 있었고 구분은 툴팁뿐이었다 ·
   * 모터가 실제로 움직이는 명령에서 이건 위험하다. */
  const alone = motionRunTargetView({ role: { joined: true, peerCount: 3 }, chosen: 'local' });
  const group = motionRunTargetView({ role: { joined: true, peerCount: 3 }, chosen: 'group' });

  assert.equal(alone.buttons.start, '1회 시작');
  assert.equal(group.buttons.start, '그룹 1회 시작 · 3대');
  assert.notEqual(alone.buttons.stop, group.buttons.stop);
  for (const key of Object.keys(alone.buttons)) {
    assert.notEqual(alone.buttons[key], group.buttons[key], `${key} 가 같은 이름이다`);
  }
});

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

test('그룹 칸과 연동 화면 길과 사유 자리가 화면에 있다', () => {
  for (const id of [
    'motionRunScopeGroupOption', 'motionRunOpenCoordinationButton',
    'motionRunBlockReason', 'motionRunPeerSummary',
  ]) {
    assert.match(indexHtml, new RegExp(`id="${id}"`), `${id} 가 없다`);
    assert.match(dom, new RegExp(`${id}: document\\.getElementById\\('${id}'\\)`));
  }
  // 참가 PC 이름은 연동 화면이 넘겨 준다 · 실행 화면이 따로 읽지 않는다
  assert.match(coordination, /peers: peers\.map/);
});

test('참가 PC 를 한 줄로 요약한다', () => {
  const view = motionRunTargetView({
    role: {
      joined: true, peerCount: 3, isMaster: true, master: 'joonhoTest',
      peers: [
        { display_name: 'pc-a', state: 'online' },
        { display_name: 'pc-b', state: 'warning' },
      ],
    },
    chosen: 'group',
  });

  assert.match(view.peerSummary, /joonhoTest\(마스터\)/);
  assert.match(view.peerSummary, /pc-a 정상/);
  assert.match(view.peerSummary, /pc-b 경고/);
});
