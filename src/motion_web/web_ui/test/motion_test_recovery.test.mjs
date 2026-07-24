import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import { recoveryTargetForMotor } from '../static/js/motion_test.js';

const html = readFileSync(new URL('../static/index.html', import.meta.url), 'utf8');
const dom = readFileSync(new URL('../static/js/dom.js', import.meta.url), 'utf8');
const controller = readFileSync(new URL('../static/js/motion_test.js', import.meta.url), 'utf8');

test('range recovery selects only the violated boundary', () => {
  assert.deepEqual(
    recoveryTargetForMotor({ position_deg: -1200, lower: -1000, upper: 1000 }),
    { targetDeg: -1000, boundary: 'lower', message: '하한 경계로 복귀합니다' },
  );
  assert.deepEqual(
    recoveryTargetForMotor({ position_deg: 1200, lower: -1000, upper: 1000 }),
    { targetDeg: 1000, boundary: 'upper', message: '상한 경계로 복귀합니다' },
  );
  assert.deepEqual(
    recoveryTargetForMotor({ position_deg: 0, lower: -1000, upper: 1000 }),
    { targetDeg: null, boundary: 'inside', message: '현재 위치가 정상 범위 안입니다' },
  );
});

test('range recovery requires valid current position and limits', () => {
  assert.equal(recoveryTargetForMotor({ lower: -1000, upper: 1000 }).targetDeg, null);
  assert.equal(
    recoveryTargetForMotor({ position_deg: -1200, lower: 1000, upper: -1000 }).targetDeg,
    null,
  );
});

test('range recovery UI is wired to the existing absolute action path', () => {
  assert.match(html, /data-motion-test-mode="recovery">범위 복귀</);
  assert.match(html, /id="motionTestRecoveryButton"[^>]*>경계 위치로 복귀</);
  assert.match(dom, /motionTestRecoveryButton: document\.getElementById\('motionTestRecoveryButton'\)/);
  assert.match(controller, /motionTestRecoveryButton\.addEventListener\('click', sendAction\)/);
  assert.match(controller, /target_deg: plan\.targetMotorDeg/);
  assert.match(controller, /range_recovery: isRecovery/);
});
