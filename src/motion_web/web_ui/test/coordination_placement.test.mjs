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

test('같은 조작이 두 곳에 있지 않다', () => {
  // 실행 화면에도 [그룹 참가] 가 있었다 · 조작은 연동 화면 하나다
  assert.equal(/id="motionRunJoinGroupButton"/.test(runPanel), false);
  assert.match(runPanel, /id="motionRunOpenCoordinationButton"/);
});

test('실행 화면에는 시작 판단에 필요한 것만 남는다', () => {
  for (const id of [
    'motionRunScopeLocal', 'motionRunScopeGroup', 'motionRunScopeSummary',
    'motionRunPeerSummary', 'motionRunBlockReason', 'motionRunGroupRole',
  ]) {
    assert.match(runPanel, new RegExp(`id="${id}"`), `${id} 가 없다`);
  }
});

/**
 * 순서가 기능을 따라간다 · 어디서 → 무엇을 → 어떻게 → 어떻게 되고 있나
 */
test('실행 화면은 네 단계 순서다', () => {
  const order = ['1. 실행 대상', '2. 실행할 모션', '3. 실행', '4. 진행']
    .map((title) => runPanel.indexOf(`<strong>${title}</strong>`));

  assert.ok(order.every((index) => index > 0), `단계 제목이 빠졌다 · ${order}`);
  for (let i = 1; i < order.length; i += 1) {
    assert.ok(order[i] > order[i - 1], `${i + 1}번이 앞선다`);
  }
});

test('참가 PC 요약은 대상을 고른 자리에 있다', () => {
  const target = runPanel.indexOf('<strong>1. 실행 대상</strong>');
  const summary = runPanel.indexOf('id="motionRunPeerSummary"');
  const nextStep = runPanel.indexOf('<strong>2. 실행할 모션</strong>');

  assert.ok(summary > target && summary < nextStep, '요약이 대상 구역 밖에 있다');
});

test('진행에 관한 것은 한 자리에 모인다', () => {
  const progress = runPanel.indexOf('<strong>4. 진행</strong>');
  for (const id of ['motionRunStatus', 'motionRunStageStrip', 'motionRunGraphCanvas']) {
    assert.ok(runPanel.indexOf(`id="${id}"`) > progress, `${id} 가 진행 구역 밖에 있다`);
  }
});
