import test from 'node:test';
import assert from 'node:assert/strict';

import {
  duplicateEthercatAddress,
  resolveRegistryMotorForScanRow,
  runtimeMotorConfirmsRegistryMotor,
  scanRowMatchesRegistryMotor,
  scanRowSharesConfiguredPosition,
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


test('unconfigured alias zero never auto-matches only by slave position', () => {
  const motor = configuredMotor({ alias: 0, position: 0 });
  const scanRow = {
    slave_position: 0,
    ethercat_alias: 103,
    rotary_alias: 403,
  };

  assert.equal(scanRowMatchesRegistryMotor(scanRow, motor), false);
  assert.equal(scanRowSharesConfiguredPosition(scanRow, motor), true);
});

test('SII order number is exposed as the physical scan model reference', () => {
  assert.equal(siiReportedAcServoModel({
    sii_order_number: 'MCDLN35BE',
    sii_device_name: 'Panasonic Servo',
  }), 'MCDLN35BE');
  assert.equal(siiReportedAcServoModel({
    sii_device_name: 'MADLN05BE',
  }), 'MADLN05BE');
});

test('one position match is merged as an explicit confirmation candidate', () => {
  const motor = configuredMotor({ alias: 0, position: 3 });
  const resolved = resolveRegistryMotorForScanRow({
    slave_position: 3,
    ethercat_alias: 0,
    serial_number: 605164099,
  }, [motor]);

  assert.equal(resolved.motor, motor);
  assert.equal(resolved.confirmationRequired, true);
});

test('ambiguous position matches are never merged', () => {
  const first = configuredMotor({ alias: 0, position: 3 });
  const second = configuredMotor({ alias: 0, position: 3 });

  assert.equal(resolveRegistryMotorForScanRow({
    slave_position: 3,
    ethercat_alias: 0,
    serial_number: 605164099,
  }, [first, second]), null);
});


test('unconfigured alias zero never matches a different slave position', () => {
  const motor = configuredMotor({ alias: 0, position: 0 });
  const scanRow = {
    slave_position: 1,
    ethercat_alias: 103,
    rotary_alias: 403,
  };

  assert.equal(scanRowMatchesRegistryMotor(scanRow, motor), false);
  assert.equal(scanRowSharesConfiguredPosition(scanRow, motor), false);
});


test('stored physical serial matches after EtherCAT chain position changes', () => {
  const motor = configuredMotor({ alias: 0, position: 2 });
  motor.identity.serial_number = 571478791;

  assert.equal(scanRowMatchesRegistryMotor({
    slave_position: 4,
    ethercat_alias: 0,
    serial_number: 571478791,
  }, motor), true);
  assert.equal(scanRowMatchesRegistryMotor({
    slave_position: 2,
    ethercat_alias: 0,
    serial_number: 571484229,
  }, motor), false);
});

test('the same slave position and alias on different masters never auto-match', () => {
  const motor = configuredMotor({ alias: 103, position: 0, masterIndex: 0 });

  assert.equal(scanRowMatchesRegistryMotor({
    master_index: 1,
    slave_position: 0,
    ethercat_alias: 103,
  }, motor), false);
  assert.equal(scanRowSharesConfiguredPosition({
    master_index: 1,
    slave_position: 0,
    ethercat_alias: 0,
  }, motor), false);
});

test('the same alias-zero slave position is valid on different EtherCAT masters', () => {
  const motors = [
    configuredMotor({ alias: 0, position: 0, masterIndex: 0 }),
    configuredMotor({ alias: 0, position: 0, masterIndex: 1 }),
  ];

  assert.equal(duplicateEthercatAddress(motors), null);
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


test('configured non-zero alias remains an identity requirement', () => {
  const motor = configuredMotor({ alias: 103, position: 0 });

  assert.equal(scanRowMatchesRegistryMotor({
    slave_position: 0,
    ethercat_alias: 103,
  }, motor), true);
  assert.equal(scanRowMatchesRegistryMotor({
    slave_position: 0,
    ethercat_alias: 104,
  }, motor), false);
  assert.equal(scanRowMatchesRegistryMotor({
    slave_position: 0,
    ethercat_alias: 0,
  }, motor), false);
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


test('physical SII name is not promoted to verified driver model', () => {
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
  assert.equal(motor.profile.driver_model, '');
  assert.equal(motor.profile.model_confirmed, false);
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
