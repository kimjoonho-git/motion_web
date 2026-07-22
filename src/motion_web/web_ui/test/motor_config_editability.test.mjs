import test from 'node:test';
import assert from 'node:assert/strict';

import {
  isEditableMotorConfigPath,
  normalizeProjectLoadToken,
  conciseMotorScanMessage,
  motorControlConfigurationError,
  motorConfigApplyIdentityBlock,
} from '../static/js/motor_config.js';


test('physical slave position is read-only in advanced config table', () => {
  assert.equal(isEditableMotorConfigPath('masters[0].slaves[0].position'), false);
});


test('logical controller index and display name remain editable', () => {
  assert.equal(isEditableMotorConfigPath('masters[0].slaves[0].controller_index'), true);
  assert.equal(isEditableMotorConfigPath('masters[0].slaves[0].name'), true);
});


test('reload button click event cannot be mistaken for a project load token', () => {
  assert.equal(normalizeProjectLoadToken({ type: 'click' }, 7), 7);
  assert.equal(normalizeProjectLoadToken(6, 7), 6);
});


test('running servo scan failure is concise in the UI', () => {
  const detailed = '직접 스캔 실패: ethercat: 모터 런타임 피드백이 수신 중입니다. '
    + '운전 중 버스 재열거는 안전하지 않아 직접 스캔을 중단했습니다 / dynamixel: 오류 상세';
  assert.equal(
    conciseMotorScanMessage(detailed),
    '검색 중단: 서보가 운전 중이어서 EtherCAT 버스 재검색을 안전하게 실행하지 않았습니다.',
  );
  assert.ok(conciseMotorScanMessage('x'.repeat(500)).length <= 180);
});


test('motion control requires applied project config without depending on scan history', () => {
  assert.equal(
    motorControlConfigurationError({
      runtime_matches_selected: true,
      motor_config_applied: false,
    }, 5),
    '현재 프로젝트에 저장한 모터축 설정이 실행 시스템에 아직 적용되지 않았습니다. 설정 적용·재시작을 실행하세요.',
  );
  assert.equal(
    motorControlConfigurationError({
      runtime_matches_selected: true,
      motor_config_applied: true,
    }, 5),
    '',
  );
});


test('missing scan history does not deadlock saved motor config application', () => {
  assert.equal(
    motorConfigApplyIdentityBlock('전체 모터 검색이 필요합니다.', false, false),
    '',
  );
  assert.equal(
    motorConfigApplyIdentityBlock('실제 연결값이 프로젝트와 다릅니다.', true, false),
    '실제 연결값이 프로젝트와 다릅니다.',
  );
});
