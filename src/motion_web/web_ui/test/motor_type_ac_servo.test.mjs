import test from 'node:test';
import assert from 'node:assert/strict';

import {
  runtimeMotorConfirmsRegistryMotor,
  scanRowMatchesRegistryMotor,
} from '../static/js/motor_type_ac_servo.js';


function configuredMotor({ alias = 0, position = 0 } = {}) {
  return {
    axis: position,
    transport: 'ethercat',
    identity: {
      ethercat_alias: alias,
      rotary_alias: alias,
      slave_position: position,
    },
    config: {
      alias,
      position,
      controller_index: position,
    },
  };
}


test('unconfigured alias zero matches a scanned drive by slave position', () => {
  const motor = configuredMotor({ alias: 0, position: 0 });
  const scanRow = {
    slave_position: 0,
    ethercat_alias: 103,
    rotary_alias: 403,
  };

  assert.equal(scanRowMatchesRegistryMotor(scanRow, motor), true);
});


test('unconfigured alias zero never matches a different slave position', () => {
  const motor = configuredMotor({ alias: 0, position: 0 });
  const scanRow = {
    slave_position: 1,
    ethercat_alias: 103,
    rotary_alias: 403,
  };

  assert.equal(scanRowMatchesRegistryMotor(scanRow, motor), false);
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
    slave_position: 0,
    alias: 0,
    driver_model: '',
    connection_state: 'online',
    connection_confirmed: true,
  };

  assert.equal(runtimeMotorConfirmsRegistryMotor(motor, runtime), true);
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
