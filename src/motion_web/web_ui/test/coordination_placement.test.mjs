import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const runPanel = readFileSync(
  new URL('../static/panels/08-panel-motion-data.html', import.meta.url), 'utf8',
);
const systemPanel = readFileSync(
  new URL('../static/panels/03-panel-system.html', import.meta.url), 'utf8',
);

const has = (text, id) => new RegExp(`id="${id}"`).test(text);

/**
 * 매일 쓰는 것과 몇 달에 한 번 쓰는 것이 한 화면에 겹쳐 있었다 · §6-98
 *
 * 모션 실행 탭 안에 그룹 설정(pc_id·domain·마스터·필수 명단)과 세션 관리
 * (참가·나가기·일시 해제)와 스케줄까지 들어 있었다 · 한 번 정하면 몇 달을
 * 안 건드리는 것들이다.
 *
 * 실행 화면에는 **실행 중에 봐야 하는 것만** 남긴다.
 */

const HOUSEKEEPING = [
  'coordinationPcId', 'coordinationDisplayName', 'coordinationGroupId',
  'coordinationDomainId', 'coordinationEnabled', 'coordinationIsMaster',
  'coordinationRequiredPeers', 'coordinationSaveButton', 'coordinationConfigMessage',
  'coordinationJoinButton', 'coordinationLeaveButton',
  'coordinationTemporaryDisableButton', 'coordinationAutoPlayToggle',
  'coordinationJoinState', 'coordinationPeerCount', 'coordinationRunAvailability',
];

const DURING_A_RUN = [
  'coordinationExecutionState', 'coordinationControlSummary',
  'coordinationErrorSummary', 'coordinationAcknowledgeErrorButton',
  'coordinationPeerRows', 'coordinationConfirmRosterButton',
  'coordinationConfirmedRosterBanner',
];

test('설정과 세션 관리는 실행 화면에 없다', () => {
  for (const id of HOUSEKEEPING) {
    assert.equal(has(runPanel, id), false, `${id} 가 아직 실행 화면에 있다`);
    assert.equal(has(systemPanel, id), true, `${id} 가 시스템 화면에 없다`);
  }
});

test('실행 중에 봐야 하는 것은 실행 화면에 남는다', () => {
  for (const id of DURING_A_RUN) {
    assert.equal(has(runPanel, id), true, `${id} 가 실행 화면에서 사라졌다`);
    assert.equal(has(systemPanel, id), false, `${id} 가 두 곳에 있다`);
  }
});

test('그룹 상태는 그룹을 고를 때만 보인다', () => {
  // 이 id 를 화면 코드가 범위에 따라 켜고 끈다 · 이름이 바뀌면 조용히 죽는다
  assert.match(runPanel, /id="motionRunGroupDetails"[^>]*class="[^"]*hidden/);
});


/**
 * 순서가 기능을 따라가야 한다 · §6-98
 *
 * 전에는 그룹을 고르고 나면 **누가 함께 도는지가 맨 아래**에 있었다 · 그 위에
 * 로컬 그래프가 있었다 · 대상을 고른 자리에서 참가 PC 가 보이지 않으면 무엇을
 * 시작하는 것인지 알 수 없다.
 *
 *   1. 실행 대상 (누가 도는가 · 참가 PC 표)
 *   2. 실행할 모션 (무엇을)
 *   3. 실행 (어떻게 · 시작)
 *   4. 진행 (어떻게 되고 있나)
 */
test('실행 화면은 어디서 · 무엇을 · 어떻게 · 어떻게 되고 있나 순서다', () => {
  const order = ['1. 실행 대상', '2. 실행할 모션', '3. 실행', '4. 진행']
    .map((title) => runPanel.indexOf(`<strong>${title}</strong>`));

  assert.ok(order.every((index) => index > 0), `단계 제목이 빠졌다 · ${order}`);
  for (let i = 1; i < order.length; i += 1) {
    assert.ok(order[i] > order[i - 1], `${i + 1}번이 앞선다`);
  }
});

test('참가 PC 는 대상을 고른 자리에서 바로 보인다', () => {
  const target = runPanel.indexOf('<strong>1. 실행 대상</strong>');
  const peers = runPanel.indexOf('id="motionRunGroupDetails"');
  const nextStep = runPanel.indexOf('<strong>2. 실행할 모션</strong>');

  assert.ok(peers > target && peers < nextStep, '참가 PC 표가 대상 구역 밖에 있다');
});

test('진행에 관한 것은 한 자리에 모인다', () => {
  const progress = runPanel.indexOf('<strong>4. 진행</strong>');
  for (const id of ['motionRunStatus', 'motionRunStageStrip', 'motionRunGraphCanvas']) {
    assert.ok(runPanel.indexOf(`id="${id}"`) > progress, `${id} 가 진행 구역 밖에 있다`);
  }
});
