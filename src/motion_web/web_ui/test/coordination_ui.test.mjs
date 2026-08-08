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
    'coordinationLeaveButton', 'coordinationInitializeButton', 'coordinationStartButton',
    'coordinationStopAfterButton', 'coordinationStopNowButton',
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
      group_id: 'stage-a', dds_domain_id: 21,
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

test('online participant enables start and warning participant blocks it', () => {
  const el = fixture();
  const controller = createCoordinationController({ el });
  const peer = {
    pc_id: 'pc-b', display_name: 'PC B', state: 'online',
    motion_state: 'ready', trigger_sync_state: 'ready',
    trigger_sync_uncertainty_ms: 1.0, servo_alarm_grade: 0,
  };

  controller.renderSnapshot(snapshot(peer));
  assert.equal(el.coordinationPeerCount.textContent, '2대');
  assert.equal(el.coordinationStartButton.disabled, false);
  assert.equal(el.coordinationInitializeButton.disabled, false);
  assert.match(el.coordinationPeerRows.innerHTML, /PC B/);

  controller.renderSnapshot(snapshot({ ...peer, state: 'warning' }));
  assert.equal(el.coordinationStartButton.disabled, true);
  assert.equal(el.coordinationInitializeButton.disabled, true);
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

  assert.equal(el.coordinationStartButton.disabled, true);
  assert.equal(el.coordinationAcknowledgeErrorButton.disabled, false);
  assert.match(el.coordinationErrorSummary.textContent, /DUPLICATE_PC_ID/);
  assert.equal(el.coordinationErrorSummary.classList.contains('hidden'), false);
});
