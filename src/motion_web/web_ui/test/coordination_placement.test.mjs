import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const panel = (name) => readFileSync(
  new URL(`../static/panels/${name}`, import.meta.url), 'utf8',
);

const runPanel = panel('08-panel-motion-data.html');
const systemPanel = panel('03-panel-system.html');
const coordinationPanel = panel('08b-panel-coordination.html');
const topbar = panel('01-topbar.html');
const navigation = readFileSync(
  new URL('../static/js/workspace_navigation.js', import.meta.url), 'utf8',
);

/**
 * 연동이 두 화면에 반씩 나뉘어 있었다 · §6-98
 *
 * 시스템 정보에는 설정과 세션이, 모션 실행에는 상태와 명단이 있었고 [그룹 참가]
 * 버튼은 **양쪽에 하나씩** 있었다 · 어느 쪽을 열어야 할지 매번 생각해야 했다.
 *
 * 이제 연동은 한 탭이다 · 실행 화면에는 "지금 시작해도 되는가" 에 답하는 것만
 * 남는다 · 대상 · 참가 PC 한 줄 · 막힘 사유.
 */

test('연동 탭이 있다', () => {
  assert.match(coordinationPanel, /data-workspace-panel="coordination"/);
  assert.match(topbar, /data-workspace-tab="coordination"/);
  assert.match(navigation, /'motion-run', 'coordination'/);
});

test('연동 요소는 연동 탭에만 있다', () => {
  const ids = [...coordinationPanel.matchAll(/id="(coordination[A-Za-z]*)"/g)]
    .map((match) => match[1]);
  assert.ok(ids.length > 15, `연동 요소를 못 찾았다 · ${ids.length}개`);
  for (const id of ids) {
    assert.equal(
      new RegExp(`id="${id}"`).test(runPanel), false, `${id} 가 실행 화면에 남았다`,
    );
    assert.equal(
      new RegExp(`id="${id}"`).test(systemPanel), false, `${id} 가 시스템 화면에 남았다`,
    );
  }
});

test('실행 화면에 연동 이야기가 남아 있지 않다', () => {
  // **실행과 연동을 가른다** · §6-100 · 실행 화면은 이 PC 의 모션만 다룬다
  for (const id of [
    'motionRunJoinGroupButton', 'motionRunScopeLocal', 'motionRunScopeGroup',
    'motionRunScopeSummary', 'motionRunPeerSummary', 'motionRunGroupRole',
    'motionRunOpenCoordinationButton', 'motionRunPeerRows',
  ]) {
    assert.equal(
      new RegExp(`id="${id}"`).test(runPanel), false, `${id} 가 아직 실행 화면에 있다`,
    );
  }
});

test('실행 화면에는 시작 판단에 필요한 것만 남는다', () => {
  // 왜 못 누르는지는 로컬 실행에도 필요하다
  assert.match(runPanel, /id="motionRunBlockReason"/);
});

/**
 * 순서가 기능을 따라간다 · 무엇을 → 어떻게 → 어떻게 되고 있나
 */
test('실행 화면은 세 단계 순서다', () => {
  const order = ['1. 실행할 모션', '2. 실행', '3. 진행']
    .map((title) => runPanel.indexOf(`<strong>${title}</strong>`));

  assert.ok(order.every((index) => index > 0), `단계 제목이 빠졌다 · ${order}`);
  for (let i = 1; i < order.length; i += 1) {
    assert.ok(order[i] > order[i - 1], `${i + 1}번이 앞선다`);
  }
});

test('그룹 실행과 각 PC 진행은 연동 화면에 있다', () => {
  for (const id of [
    'coordinationStartOnceButton', 'coordinationStopNowButton',
    'motionRunPeerRows', 'coordinationExecutionState',
  ]) {
    assert.match(coordinationPanel, new RegExp(`id="${id}"`), `${id} 가 없다`);
  }
});

test('진행에 관한 것은 한 자리에 모인다', () => {
  const progress = runPanel.indexOf('<strong>3. 진행</strong>');
  for (const id of ['motionRunStatus', 'motionRunStageStrip', 'motionRunGraphCanvas']) {
    assert.ok(runPanel.indexOf(`id="${id}"`) > progress, `${id} 가 진행 구역 밖에 있다`);
  }
});

test('실행 상태표는 가로를 채워 다섯 줄이다', () => {
  // 한 쌍만 쓰고 가로를 비워 두면 상태표가 그래프보다 높이를 더 먹는다 · §6-100
  const controller = readFileSync(
    new URL('../static/js/motion_data.js', import.meta.url), 'utf8');
  const table = controller.slice(
    controller.indexOf('motion-run-status-table'),
    controller.indexOf('</table>', controller.indexOf('motion-run-status-table')),
  );
  const rows = table.match(/<tr>/g) || [];
  assert.equal(rows.length, 5, `줄 수가 ${rows.length} 이다`);
  // 한 칸짜리로 늘어놓던 자리가 남아 있지 않다
  assert.equal(/colspan="3"/.test(table), false, '가로를 비워 둔 칸이 있다');
});
