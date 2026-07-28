import assert from 'node:assert/strict';
import test from 'node:test';

import { trackedMotorRestartState } from '../static/js/restart_tracking.js';

test('waits for the exact restart operation id', () => {
  const state = trackedMotorRestartState({
    operation_id: 'previous',
    type: 'motor_restart',
    status: 'success',
  }, 'current');

  assert.equal(state.state, 'waiting');
  assert.match(state.detail, /current/);
});

test('keeps a matching restart operation pending until backend success', () => {
  const state = trackedMotorRestartState({
    operation_id: 'current',
    type: 'motor_restart',
    phase: 'restarting',
    status: 'running',
  }, 'current');

  assert.equal(state.state, 'running');
  assert.match(state.detail, /restarting/);
});

test('accepts only matching backend success', () => {
  const state = trackedMotorRestartState({
    operation_id: 'current',
    type: 'motor_restart',
    phase: 'completed',
    status: 'success',
    message: '모터 제어 재시작 완료',
  }, 'current');

  assert.deepEqual(state, {
    state: 'success',
    detail: '모터 제어 재시작 완료',
  });
});

test('preserves matching backend terminal failure', () => {
  const state = trackedMotorRestartState({
    operation_id: 'current',
    type: 'motor_restart',
    phase: 'failed',
    status: 'failure',
    error: '서비스 재시작 실패',
  }, 'current');

  assert.deepEqual(state, {
    state: 'failure',
    detail: '서비스 재시작 실패',
  });
});
