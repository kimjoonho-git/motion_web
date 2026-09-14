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
