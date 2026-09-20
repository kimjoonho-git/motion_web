import test from 'node:test';
import assert from 'node:assert/strict';

// 짝 맞추기 규칙의 시험은 서버로 갔다 · §6-216
// `web_bridge/test/test_the_server_pairs_the_scan.py` 가 지킨다.
import {
  duplicateEthercatAddress,
  runtimeMotorConfirmsRegistryMotor,
  scanRowToMotor,
  siiReportedAcServoModel,
  verifiedAcServoModel,
} from '../static/js/motor_type_ac_servo.js';
import { normalizeMotor } from '../static/js/motor_registry.js';


function configuredMotor({ alias = 0, position = 0, masterIndex = 0 } = {}) {
  return {
    axis: position,
    transport: 'ethercat',
    identity: {
      ethercat_master_index: masterIndex,
      ethercat_alias: alias,
      rotary_alias: alias,
      slave_position: position,
    },
    config: {
      ethercat_master_index: masterIndex,
      alias,
      position,
      controller_index: position,
    },
  };
}


test('SII order number is exposed as the physical scan model reference', () => {
  assert.equal(siiReportedAcServoModel({
    sii_order_number: 'MCDLN35BE',
    sii_device_name: 'Panasonic Servo',
  }), 'MCDLN35BE');
  assert.equal(siiReportedAcServoModel({
    sii_device_name: 'MADLN05BE',
  }), 'MADLN05BE');
});

test('duplicate EtherCAT addresses are rejected only within the same master', () => {
  assert.deepEqual(duplicateEthercatAddress([
    configuredMotor({ alias: 0, position: 2, masterIndex: 1 }),
    configuredMotor({ alias: 0, position: 2, masterIndex: 1 }),
  ]), {
    masterIndex: 1,
    addressType: 'position',
    value: 2,
  });

  assert.equal(duplicateEthercatAddress([
    configuredMotor({ alias: 103, position: 0, masterIndex: 0 }),
    configuredMotor({ alias: 103, position: 1, masterIndex: 1 }),
  ]), null);

  assert.deepEqual(duplicateEthercatAddress([
    configuredMotor({ alias: 103, position: 0, masterIndex: 0 }),
    configuredMotor({ alias: 103, position: 1, masterIndex: 0 }),
  ]), {
    masterIndex: 0,
    addressType: 'alias',
    value: 103,
  });
});


test('fresh online runtime feedback confirms an alias-zero motor by axis and position', () => {
  const motor = configuredMotor({ alias: 0, position: 0 });
  const runtime = {
    controller_index: 0,
    ethercat_master_index: 0,
    slave_position: 0,
    alias: 0,
    driver_model: '',
    connection_state: 'online',
    connection_confirmed: true,
  };

  assert.equal(runtimeMotorConfirmsRegistryMotor(motor, runtime), true);
});

test('runtime feedback from another EtherCAT master cannot confirm the axis', () => {
  const motor = configuredMotor({ alias: 0, position: 0, masterIndex: 1 });
  const runtime = {
    controller_index: 0,
    ethercat_master_index: 0,
    slave_position: 0,
    alias: 0,
    connection_state: 'online',
    connection_confirmed: true,
  };

  assert.equal(runtimeMotorConfirmsRegistryMotor(motor, runtime), false);
});


test('runtime feedback cannot confirm a different or stale slave', () => {
  const motor = configuredMotor({ alias: 0, position: 0 });
  const runtime = {
    controller_index: 0,
    slave_position: 1,
    alias: 0,
    connection_state: 'online',
    connection_confirmed: true,
  };
  assert.equal(runtimeMotorConfirmsRegistryMotor(motor, runtime), false);

  runtime.slave_position = 0;
  runtime.connection_state = 'stale';
  assert.equal(runtimeMotorConfirmsRegistryMotor(motor, runtime), false);
});


test('SII 가 읽어 온 모델을 쓰되 출처는 구분한다 · §6-208', () => {
  // 전에는 SII 이름을 모델로 쓰지 않았다 · 사람이 명판을 보고 다시
  // 입력해야 「확인됨」이 됐다 · 그런데 「확인된 모델 목록」이 비어 있어
  // 새 서보는 **언제나** 「모델 미확인」이었고, 그것을 푸는 길이 하나뿐이라
  // 그 순서를 모르면 진도가 안 나갔다.
  //
  // 이제 검색이 읽은 값을 쓴다 · 다만 카탈로그로 확인한 것과 장치가 말한
  // 것은 `model_source` 로 구분해 남긴다.
  const motor = scanRowToMotor({
    master_index: 0,
    slave_position: 2,
    ethercat_alias: 0,
    vendor_id: 1647,
    product_code: 1614282756,
    revision_number: 65536,
    serial_number: 123456,
    identity_source: 'physical_sii',
    order_number: 'SII-ORDER',
    device_name: 'SII-DEVICE',
  }, () => 7);

  assert.equal(motor.identity.driver_model, undefined);
  assert.equal(motor.identity.nameplate_confirmed, undefined);
  assert.equal(motor.profile.driver_model, 'SII-ORDER');
  assert.equal(motor.profile.model_confirmed, true);
  assert.equal(motor.profile.model_source, 'physical_sii', '카탈로그 확인은 아니다');
  assert.equal(motor.identity.sii_order_number, 'SII-ORDER');
  assert.equal(motor.identity.sii_device_name, 'SII-DEVICE');
  assert.equal(motor.identity.serial_number, 123456);
  assert.equal(motor.identity.vendor_id, 1647);
  assert.equal(motor.identity.product_code, 1614282756);
  assert.equal(motor.identity.ethercat_master_index, 0);
  assert.equal(motor.config.controller_index, 7);
  assert.equal(motor.config.ethercat_master_index, 0);
  assert.equal(verifiedAcServoModel({
    vendor_id: 1647,
    product_code: 1614282756,
    revision_number: 65536,
    sii_device_name: 'MADLN05BE',
  }), '');
});


test('legacy model metadata migrates out of physical identity', () => {
  const motor = normalizeMotor({
    transport: 'ethercat',
    motor_type: 'ac_servo',
    identity: {
      serial_number: 123456,
      driver_model: 'MADLN05BE',
      nameplate_confirmed: true,
    },
  });

  assert.deepEqual(motor.identity, { serial_number: 123456 });
  assert.deepEqual(motor.profile, {
    driver_model: 'MADLN05BE',
    model_confirmed: true,
    model_source: 'user_nameplate',
  });
});
