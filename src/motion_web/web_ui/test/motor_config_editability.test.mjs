import test from 'node:test';
import assert from 'node:assert/strict';

import {
  isEditableMotorConfigPath,
  normalizeProjectLoadToken,
  conciseMotorScanMessage,
  motorControlConfigurationError,
  motorConfigApplyIdentityBlock,
  motorModelProfileApplyBlock,
  motorRuntimeReadyForAppliedConfig,
} from '../static/js/motor_config.js';


test('physical slave position is read-only in advanced config table', () => {
  assert.equal(isEditableMotorConfigPath('masters[0].slaves[0].position'), false);
});


test('logical controller index and display name remain editable', () => {
  assert.equal(isEditableMotorConfigPath('masters[0].slaves[0].controller_index'), true);
  assert.equal(isEditableMotorConfigPath('masters[0].slaves[0].name'), true);
});


test('model profile remains editable only outside physical SII identity', () => {
  assert.equal(isEditableMotorConfigPath('drivers[0].driver_model'), false);
  assert.equal(
    isEditableMotorConfigPath('web_axis_identities[0].sii_device_name'),
    false,
  );
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


test('applied config is complete only after runtime feedback is ready', () => {
  assert.equal(
    motorRuntimeReadyForAppliedConfig({
      project_scope: {
        runtime_matches_selected: true,
        motor_config_applied: true,
      },
      service_management: {
        runtime: { phase: 'waiting_motor_feedback' },
      },
    }),
    false,
  );
  assert.equal(
    motorRuntimeReadyForAppliedConfig({
      project_scope: {
        runtime_matches_selected: true,
        motor_config_applied: true,
      },
      service_management: {
        runtime: { phase: 'ready' },
      },
    }),
    true,
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


test('unconfirmed model profile blocks runtime application without blocking project storage', () => {
  const message = motorModelProfileApplyBlock([
    {
      enabled: true,
      deleted: false,
      transport: 'ethercat',
      axis: 0,
      profile: {
        driver_model: '',
        model_confirmed: false,
      },
      config: { controller_index: 0 },
    },
    {
      enabled: true,
      deleted: false,
      transport: 'ethercat',
      axis: 1,
      profile: {
        driver_model: 'MCDLN35BE',
        model_confirmed: true,
      },
      config: { controller_index: 1 },
    },
  ]);

  assert.match(message, /실행 적용 불가/);
  assert.match(message, /미확인 축: 0/);
  assert.match(message, /프로젝트 저장은 가능/);
  assert.doesNotMatch(message, /미확인 축: 0, 1/);
});
