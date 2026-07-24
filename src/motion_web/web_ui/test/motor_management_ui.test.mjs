import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const html = readFileSync(new URL('../static/index.html', import.meta.url), 'utf8');
const controller = readFileSync(new URL('../static/js/motor_config.js', import.meta.url), 'utf8');
const motionTest = readFileSync(new URL('../static/js/motion_test.js', import.meta.url), 'utf8');
const styles = readFileSync(new URL('../static/styles.css', import.meta.url), 'utf8');

test('motor management exposes one seven-stage preparation flow', () => {
  for (const step of [
    'service',
    'connection',
    'configuration',
    'application',
    'mapping',
    'drive',
    'verification',
  ]) {
    assert.match(html, new RegExp(`data-motor-readiness-step="${step}"`));
    assert.match(controller, new RegExp(`key: '${step}'`));
  }
  assert.match(controller, /renderMotorReadiness\(rows, rowViews, changed\)/);
  assert.match(styles, /\.motor-readiness-steps/);
});

test('axis readiness table keeps runtime facts distinct', () => {
  for (const heading of [
    '실제 장치 식별',
    '설정·실행 적용',
    '모션 매칭',
    '서보·토크',
    '조그·동작',
    '모션 실행',
    '최종 상태',
  ]) {
    assert.match(html, new RegExp(`<th>${heading}</th>`));
  }
  assert.match(controller, /motion_axis_configured === true/);
  assert.match(controller, /runtime\.servo_on === true/);
  assert.match(controller, /축별 실행 이력은 미지원/);
  assert.match(controller, /실물 검증 미확인/);
});

test('unsupported Dynamixel torque controls are not presented as working actions', () => {
  assert.match(html, /현재 명시적 토크 제어 버튼은 지원하지 않으며/);
  assert.doesNotMatch(html, /id="[^"]*Dynamixel[^"]*Torque/);
});

test('existing AC servo API is reachable per axis from motor management', () => {
  assert.match(controller, /data-axis-servo-action="servo_on"/);
  assert.match(controller, /data-axis-servo-action="servo_off"/);
  assert.match(controller, /data-axis-servo-action="fault_reset"/);
  assert.match(controller, /onAcServoControl\(button\.dataset\.axisServoAction/);
  assert.match(motionTest, /controlAcServo: async \(action, axis\)/);
  assert.match(motionTest, /sendAcServoControl\(action, 'selected'\)/);
});
