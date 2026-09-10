import test from 'node:test';
import assert from 'node:assert/strict';

import { createCoordinationController } from '../static/js/coordination.js';

function element() {
  const classes = new Set();
  return {
    textContent: '', className: '', innerHTML: '', value: '', disabled: false,
    classList: {
      toggle(name, enabled) {
        if (enabled) classes.add(name);
        else classes.delete(name);
      },
      contains(name) { return classes.has(name); },
    },
  };
}

function fixture() {
  const names = [
    'coordinationPcId', 'coordinationDisplayName', 'coordinationGroupId',
    'coordinationDomainId', 'coordinationEnabled', 'coordinationNodeState',
    'coordinationConfigMessage', 'coordinationUpdatedAt',
    'coordinationMachineId', 'coordinationGroupDomain',
    'coordinationJoinState', 'coordinationPeerCount',
    'coordinationExecutionState', 'coordinationJoinButton',
    'coordinationLeaveButton', 'coordinationRunAvailability',
    'coordinationAcknowledgeErrorButton', 'coordinationErrorSummary',
    'coordinationPeerRows',
  ];
  return Object.fromEntries(names.map((name) => [name, element()]));
}

function snapshot(peer, coordinationError = {}) {
  return {
    node_connected: true,
    status_age_sec: 0.1,
    config: {
      pc_id: 'pc-a', display_name: 'PC A', enabled: true,
      group_id: 'stage-a', dds_domain_id: 21, is_master: true,
    },
    runtime: {
      joined: true,
      local: { pc_id: 'pc-a', display_name: 'PC A' },
      peers: [peer],
      alarms: [],
      execution: { state: 'idle', participants: [] },
      coordination_error: coordinationError,
    },
  };
}

// 실행 버튼은 모션 실행 화면으로 옮겼다 · 여기서 지키는 것은 버튼이 아니라
// "그룹 실행을 지금 시작해도 되는가" 규칙이다 · §6-65
test('online participant allows a group run and a warning participant blocks it', () => {
  const el = fixture();
  const controller = createCoordinationController({ el });
  const peer = {
    pc_id: 'pc-b', display_name: 'PC B', state: 'online',
    motion_state: 'ready', trigger_sync_state: 'ready',
    trigger_sync_uncertainty_ms: 1.0, servo_alarm_grade: 0,
  };

  controller.renderSnapshot(snapshot(peer));
  assert.equal(el.coordinationPeerCount.textContent, '2대');
  assert.deepEqual(controller.groupRun.availability(), {
    ok: true, reason: '', active: false, peerCount: 2, state: 'idle',
  });
  assert.match(el.coordinationRunAvailability.textContent, /그룹 실행 준비됨/);
  assert.match(el.coordinationPeerRows.innerHTML, /PC B/);

  controller.renderSnapshot(snapshot({ ...peer, state: 'warning' }));
  const blocked = controller.groupRun.availability();
  assert.equal(blocked.ok, false);
  assert.match(blocked.reason, /통신 이상이나 알람/);
  assert.match(el.coordinationRunAvailability.textContent, /그룹 실행 불가/);
  assert.match(el.coordinationPeerRows.innerHTML, /지연/);
});

test('coordination error is visible and blocks start until acknowledgement', () => {
  const el = fixture();
  const controller = createCoordinationController({ el });
  const peer = {
    pc_id: 'pc-b', state: 'online', motion_state: 'ready',
    trigger_sync_state: 'ready', servo_alarm_grade: 0,
  };

  controller.renderSnapshot(snapshot(peer, {
    active: true,
    code: 'DUPLICATE_PC_ID',
    message: '같은 PC ID가 있습니다',
  }));

  const availability = controller.groupRun.availability();
  assert.equal(availability.ok, false);
  assert.match(availability.reason, /그룹 오류/);
  assert.equal(el.coordinationAcknowledgeErrorButton.disabled, false);
  assert.match(el.coordinationErrorSummary.textContent, /DUPLICATE_PC_ID/);
  assert.equal(el.coordinationErrorSummary.classList.contains('hidden'), false);
});


test('a slave cannot start a group run but can still stop one', () => {
  const el = fixture();
  const controller = createCoordinationController({ el });
  const peer = {
    pc_id: 'pc-b', display_name: 'PC B', state: 'online',
    motion_state: 'ready', trigger_sync_state: 'ready', servo_alarm_grade: 0,
  };

  // 시작은 마스터만 · 정지는 누구나 · §6-70
  const asSlave = snapshot(peer);
  asSlave.config.is_master = false;
  controller.renderSnapshot(asSlave);

  const availability = controller.groupRun.availability();
  assert.equal(availability.ok, false);
  assert.match(availability.reason, /슬레이브/);
  // 정지 창구는 역할과 무관하게 남아 있어야 한다
  assert.equal(typeof controller.groupRun.stopNow, 'function');
  assert.equal(typeof controller.groupRun.stopAfterCycle, 'function');
});
