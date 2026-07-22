import assert from 'node:assert/strict';
import test from 'node:test';

import {
  buildGeneratedMotionAxisRows,
  mergeConfiguredMotionMotors,
  motionMappingTargetKey,
  motionMotorRef,
  motionMotorSelectionValue,
  motionMotorTargetKey,
} from '../static/js/motion_data.js';

test('Alias 0 AC servos use distinct control-axis selections and no duplicated motor_ref', () => {
  const motors = [0, 1, 2, 3, 4].map((controllerIndex) => ({
    controller_index: controllerIndex,
    motor_type: 'ac_servo',
    alias: 0,
  }));

  assert.deepEqual(motors.map(motionMotorRef), ['', '', '', '', '']);
  assert.deepEqual(
    motors.map(motionMotorSelectionValue),
    ['axis:0', 'axis:1', 'axis:2', 'axis:3', 'axis:4'],
  );
});

test('non-zero AC Alias and Dynamixel ID remain stable motor references', () => {
  assert.equal(
    motionMotorRef({ controller_index: 7, motor_type: 'ac_servo', alias: 103 }),
    'ac_servo:alias:103',
  );
  assert.equal(
    motionMotorRef({ controller_index: 8, motor_type: 'dynamixel', bus_id: 42 }),
    'dynamixel:id:42',
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

test('Alias 0 motor usage is compared by control axis instead of empty ref', () => {
  const motor = { controller_index: 3, motor_type: 'ac_servo', alias: 0 };
  const row = { enabled: true, motor_ref: '', motor_axis: 3 };
  assert.equal(motionMotorTargetKey(motor), 'axis:3');
  assert.equal(motionMappingTargetKey(row), 'axis:3');
});

test('automatic generation repairs five duplicated Alias 0 rows', () => {
  const motors = [0, 1, 2, 3, 4].map((controllerIndex) => ({
    controller_index: controllerIndex,
    motor_type: 'ac_servo',
    alias: 0,
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
  assert.deepEqual(generated.map((row) => row.motor_ref), ['', '', '', '', '']);
});

test('automatic generation preserves valid per-axis edits', () => {
  const motors = [0, 1].map((controllerIndex) => ({
    controller_index: controllerIndex,
    motor_type: 'ac_servo',
    alias: 0,
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
