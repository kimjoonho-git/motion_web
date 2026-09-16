import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';

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


test('a saved motor keeps its selection after the server lowercases the reference', () => {
  // 저장을 누르면 모터 칸이 「선택 안함」으로 되돌아갔다 · §6-104
  //
  // 화면은 `encodeURIComponent` 로 값을 만드는데 그것은 **대문자**를 낸다 ·
  // 서버는 저장할 때 **소문자**로 바꿔 돌려준다 · 그리는 쪽이 `===` 로 그대로
  // 비교해서 제 선택을 못 찾았다.
  //
  // AC 서보 값은 대문자가 없어 안 걸렸다 · **다이나믹셀에서만**, 그것도
  // **저장 직후에만** 났다. 짝을 찾는 `motorForRef` 는 이미 소문자로 낮춰
  // 비교하고 있었고 여기만 빠져 있었다.
  const motor = {
    controller_index: 0,
    motor_type: 'dynamixel',
    bus_id: 3,
    serial_port: '/dev/serial/by-id/usb-FTDI_USB__-__Serial_Converter_FTAAMMJV-if00-port0',
  };
  const optionValue = motionMotorSelectionValue(motor);
  const savedByServer = optionValue.toLowerCase();

  assert.notEqual(optionValue, savedByServer, '대문자가 없으면 이 시험은 의미가 없다');
  assert.equal(
    optionValue.trim().toLowerCase(),
    savedByServer.trim().toLowerCase(),
    '대소문자를 무시하면 같은 모터로 맞아야 한다',
  );
});

test('the motor select compares references without case', () => {
  const controller = readFileSync(
    new URL('../static/js/motion_data.js', import.meta.url),
    'utf8',
  );
  const start = controller.indexOf('function motorSelectHtml(');
  const body = controller.slice(start, controller.indexOf('\n  }', start));

  assert.match(body, /\.trim\(\)\.toLowerCase\(\)/, '대소문자를 그대로 비교한다');
  assert.doesNotMatch(
    body,
    /const selected = selectionValue === value/,
    '저장 뒤 선택이 풀린다',
  );
});
