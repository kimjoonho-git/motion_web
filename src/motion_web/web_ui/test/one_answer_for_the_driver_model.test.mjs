// 화면만 「모델 미확인」이라고 하던 것 · §6-210
//
// 서버는 기본 minas 드라이버에 `UNVERIFIED_MINAS` 를 달아 「모델을 모른다」고
// 적는다 · 화면은 그것을 **모델 이름으로** 받아 들고 다녔다.
//
//   drivers:            driver 4  minas  MADLN05BE   ← 실제로 이걸로 돈다
//   web_axis_profiles:  0번 축    「모름」 표식        ← 화면이 읽던 값
//
// 그래서 적용은 통과하는데 화면에는 영영
// 「실행 적용 불가 · 모델·운전 프로필 미확인 축: 0, 1」이 떴다 · 어느 쪽이
// 맞는지 사람이 알 길이 없었다.
import test from 'node:test';
import assert from 'node:assert/strict';

import { normalizeMotor, modelIsUnknown } from '../static/js/motor_registry.js';
import { motorModelProfileWarning } from '../static/js/motor_config.js';

const MARKER = 'UNVERIFIED_MINAS';

function servo(profile, identity = {}) {
  return normalizeMotor({
    id: 'ac_servo_ethercat_master_0_alias_403',
    enabled: true,
    deleted: false,
    axis: 0,
    transport: 'ethercat',
    motor_type: 'ac_servo',
    driver_family: 'minas',
    identity: { sii_order_number: 'MADLN05BE', ...identity },
    profile,
    config: { controller_index: 0 },
  });
}

test('the marker is not a model name', () => {
  assert.equal(modelIsUnknown(MARKER), true);
  assert.equal(modelIsUnknown(''), true);
  assert.equal(modelIsUnknown('MADLN05BE'), false);
});

test('a scanned model shows instead of the marker', () => {
  // **이것이 그 버그다** · 저장된 프로젝트에는 표식이 남아 있다.
  const motor = servo({ driver_model: MARKER, model_confirmed: false, model_source: '' });

  assert.equal(motor.profile.driver_model, 'MADLN05BE');
  assert.equal(motor.profile.model_confirmed, true);
  assert.equal(motor.profile.model_source, 'physical_sii');
});

test('a scanned model leaves nothing to warn about', () => {
  assert.equal(motorModelProfileWarning([servo({ driver_model: MARKER })]), '');
  assert.equal(motorModelProfileWarning([servo({ driver_model: 'MADLN05BE' })]), '');
});

test('a model nobody knows is named but does not stop the apply', () => {
  // 막지 않는다 · 어느 축을 못 읽었는지만 말한다 · §6-213
  const warning = motorModelProfileWarning([
    servo({ driver_model: '' }, { sii_order_number: '', sii_device_name: '' }),
  ]);

  assert.match(warning, /0/);
  assert.match(warning, /적용은 진행됩니다/);
  assert.doesNotMatch(warning, /적용 불가/);
});

test('a model the user typed wins over the scan', () => {
  const motor = servo({ driver_model: 'MBDLN25SE', model_source: 'user_nameplate' });

  assert.equal(motor.profile.driver_model, 'MBDLN25SE');
  assert.equal(motor.profile.model_source, 'user_nameplate');
});
