import assert from 'node:assert/strict';
import test from 'node:test';

import {
  buildGeneratedMotionAxisRows,
  mergeConfiguredMotionMotors,
  motionMappingTargetKey,
  motionMotorIdentityLabel,
  motionMotorRef,
  motionMotorRefs,
  motionMotorSelectionValue,
  motionMotorTargetKey,
} from '../static/js/motion_data.js';

test('Alias 0 AC servos use distinct Master and Slave references', () => {
  const motors = [0, 1, 2, 3, 4].map((controllerIndex) => ({
    controller_index: controllerIndex,
    motor_type: 'ac_servo',
    ethercat_master_index: 0,
    alias: 0,
    slave_position: controllerIndex,
  }));

  assert.deepEqual(motors.map(motionMotorRef), [
    'ac_servo:master:0:slave:0',
    'ac_servo:master:0:slave:1',
    'ac_servo:master:0:slave:2',
    'ac_servo:master:0:slave:3',
    'ac_servo:master:0:slave:4',
  ]);
  assert.deepEqual(
    motors.map(motionMotorSelectionValue),
    [
      'ac_servo:master:0:slave:0',
      'ac_servo:master:0:slave:1',
      'ac_servo:master:0:slave:2',
      'ac_servo:master:0:slave:3',
      'ac_servo:master:0:slave:4',
    ],
  );
});

test('non-zero AC Alias and Dynamixel ID remain stable motor references', () => {
  assert.equal(
    motionMotorRef({
      controller_index: 7,
      motor_type: 'ac_servo',
      ethercat_master_index: 1,
      alias: 103,
    }),
    'ac_servo:master:1:alias:103',
  );
  assert.equal(
    motionMotorRef({
      controller_index: 8,
      motor_type: 'dynamixel',
      serial_port: '/dev/ttyUSB1',
      bus_id: 42,
    }),
    'dynamixel:port:%2Fdev%2FttyUSB1:id:42',
  );
});

test('same Alias and Dynamixel ID remain distinct on different buses', () => {
  assert.notEqual(
    motionMotorRef({
      motor_type: 'ac_servo', ethercat_master_index: 0, alias: 103,
    }),
    motionMotorRef({
      motor_type: 'ac_servo', ethercat_master_index: 1, alias: 103,
    }),
  );
  assert.notEqual(
    motionMotorRef({
      motor_type: 'dynamixel', serial_port: '/dev/ttyUSB0', bus_id: 7,
    }),
    motionMotorRef({
      motor_type: 'dynamixel', serial_port: '/dev/ttyUSB1', bus_id: 7,
    }),
  );
});

test('motion-axis selector shows the EtherCAT Master or Dynamixel serial port', () => {
  assert.equal(
    motionMotorIdentityLabel({
      motor_type: 'ac_servo',
      ethercat_master_index: 1,
      alias: 0,
      slave_position: 3,
    }),
    'AC Master 1 · EEPROM Alias 미설정 · Slave Position 3',
  );
  assert.equal(
    motionMotorIdentityLabel({
      motor_type: 'dynamixel',
      serial_port: '/dev/ttyUSB1',
      bus_id: 7,
    }),
    'Dynamixel /dev/ttyUSB1 · ID 7',
  );
});

test('legacy refs remain available only as compatibility candidates', () => {
  assert.deepEqual(
    motionMotorRefs({
      motor_type: 'ac_servo', ethercat_master_index: 1, alias: 103,
    }),
    ['ac_servo:master:1:alias:103', 'ac_servo:alias:103'],
  );
  assert.deepEqual(
    motionMotorRefs({
      motor_type: 'dynamixel', serial_port: '/dev/ttyUSB1', bus_id: 7,
    }),
    ['dynamixel:port:%2Fdev%2FttyUSB1:id:7', 'dynamixel:id:7'],
  );
});

test('selected-project motor names and identities override stale runtime labels', () => {
  const runtime = [{
    controller_index: 4,
    motor_type: 'minas',
    alias: 0,
    slave_position: 4,
    display_name: 'alias 0',
    connection_state: 'online',
  }];
  const configured = [{
    axis: 4,
    name: '목 상하',
    motor_type: 'ac_servo',
    identity: { ethercat_alias: 0, slave_position: 4 },
    config: { controller_index: 4, alias: 0, position: 4 },
  }];

  const [merged] = mergeConfiguredMotionMotors(runtime, configured);
  assert.equal(merged.display_name, '목 상하');
  assert.equal(merged.controller_index, 4);
  assert.equal(merged.slave_position, 4);
  assert.equal(merged.connection_state, 'online');
});

test('Alias 0 motor usage is compared by Master and Slave reference', () => {
  const motor = {
    controller_index: 3,
    motor_type: 'ac_servo',
    ethercat_master_index: 1,
    alias: 0,
    slave_position: 3,
  };
  const row = {
    enabled: true,
    motor_ref: 'ac_servo:master:1:slave:3',
    motor_axis: 3,
  };
  assert.equal(motionMotorTargetKey(motor), 'ref:ac_servo:master:1:slave:3');
  assert.equal(motionMappingTargetKey(row), 'ref:ac_servo:master:1:slave:3');
});

test('automatic generation repairs five duplicated Alias 0 rows', () => {
  const motors = [0, 1, 2, 3, 4].map((controllerIndex) => ({
    controller_index: controllerIndex,
    motor_type: 'ac_servo',
    ethercat_master_index: 0,
    alias: 0,
    slave_position: controllerIndex,
  }));
  const corruptedRows = [1, 2, 3, 4, 5].map((number) => ({
    motion_id: `1-${number}`,
    enabled: true,
    motor_ref: 'ac_servo:alias:0',
    motor_axis: 4,
  }));

  const generated = buildGeneratedMotionAxisRows(motors, corruptedRows);
  assert.deepEqual(generated.map((row) => row.motion_id), ['1-1', '1-2', '1-3', '1-4', '1-5']);
  assert.deepEqual(generated.map((row) => row.motor_axis), [0, 1, 2, 3, 4]);
  assert.deepEqual(generated.map((row) => row.motor_ref), [
    'ac_servo:master:0:slave:0',
    'ac_servo:master:0:slave:1',
    'ac_servo:master:0:slave:2',
    'ac_servo:master:0:slave:3',
    'ac_servo:master:0:slave:4',
  ]);
});

test('automatic generation preserves valid per-axis edits', () => {
  const motors = [0, 1].map((controllerIndex) => ({
    controller_index: controllerIndex,
    motor_type: 'ac_servo',
    ethercat_master_index: 0,
    alias: 0,
    slave_position: controllerIndex,
  }));
  const previous = [{
    motion_id: '2-3',
    enabled: true,
    motor_ref: '',
    motor_axis: 1,
    offset_deg: 12.5,
  }];

  const generated = buildGeneratedMotionAxisRows(motors, previous);
  assert.equal(generated[1].motion_id, '2-3');
  assert.equal(generated[1].offset_deg, 12.5);
  assert.equal(generated[0].motion_id, '1-1');
});

test('automatic generation upgrades one unambiguous legacy ref', () => {
  const motors = [{
    controller_index: 7,
    motor_type: 'ac_servo',
    ethercat_master_index: 1,
    alias: 103,
  }];
  const generated = buildGeneratedMotionAxisRows(motors, [{
    motion_id: '4-1',
    enabled: true,
    motor_ref: 'ac_servo:alias:103',
    motor_axis: 0,
    offset_deg: 5,
  }]);

  assert.equal(generated[0].motor_ref, 'ac_servo:master:1:alias:103');
  assert.equal(generated[0].motor_axis, 7);
  assert.equal(generated[0].offset_deg, 5);
});
