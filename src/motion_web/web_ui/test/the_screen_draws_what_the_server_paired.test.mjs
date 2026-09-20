// 화면은 서버가 짝지어 준 것을 그리기만 한다 · §6-216 · §6-219
//
// 두 가지를 없앴다.
//
//   ① 화면이 스스로 짝을 맞추던 것      → 서버가 `scan.axis_rows` 로 보낸다
//   ② 축 목록이 둘이던 것                → 검색 결과가 곧 목록이다
//
// ② 때문에 화면에 네 줄이 보이는데 저장은 「0축 모터 설정은 저장할 수
// 없습니다」가 떴다 · 검색 결과가 `proposedMotor` 라는 **제안**일 뿐이고
// 저장은 `axisConfig` 만 봤기 때문이다 · 「선택 축 추가」로 사람이 손수
// 옮겨야 한다는 것을 알아야만 했다.
//
// 그리고 이 자리에서 검색이 통째로 죽은 적이 있다 (§6-217) ·
// `options.nextAvailableAxis is not a function` · 시험 535개가 전부
// 통과했는데도 그랬다 · 행 만들기가 화면 closure 안에 있어 시험이 글자만
// 훑었기 때문이다 · 그래서 꺼내 두고 **실제로 돌려 본다.**
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import { buildAxisRows, axisRowDriverModel } from '../static/js/motor_config.js';

const PORT = '/dev/serial/by-id/usb-FTDI_USB__-__Serial_Converter_X-if00-port0';

const RAW_SLAVE = {
  master_index: 0, slave_position: 0, ethercat_alias: 103, rotary_alias: 52,
  serial_number: 402982152, vendor_id: 1647, product_code: 1614282756,
  order_number: 'MADLN05BE', device_name: 'MADLN05BE', device_state: 'PREOP',
};
const RAW_DEVICE = { id: 3, port: PORT, model_number: 1130, model_name: 'XM540-W150' };

const SERVED_SLAVE = {
  model: 'MADLN05BE', alias: 103, rotary_alias: 52, slave_position: 0,
  serial_number: 402982152, vendor_id: 1647, product_code: 1614282756,
  master_index: 0, serial_port: '', bus_id: null,
};
const SERVED_DEVICE = {
  model: 'XM540-W150', alias: null, rotary_alias: null, slave_position: null,
  serial_number: null, vendor_id: null, product_code: null, master_index: null,
  serial_port: PORT, bus_id: 3,
};

const wiring = {
  findAcServoScanRow: (scanned) => (scanned?.master_index === 0 ? RAW_SLAVE : null),
  findDynamixelDevice: (scanned) => (scanned?.bus_id === 3 ? RAW_DEVICE : null),
};

const servo = { id: 'ac_servo_ethercat_master_0_alias_103', axis: 0, name: 'alias 103',
                transport: 'ethercat', config: { controller_index: 0 } };
const dynamixel = { id: 'dxl0', axis: 1, name: 'ID 3', transport: 'serial',
                    config: { controller_index: 1 } };

test('a matched axis gets its raw scan row attached', () => {
  const rows = buildAxisRows({
    motors: [servo],
    served: { axes: [{ id: servo.id, transport: 'ethercat', state: 'matched',
                       model: 'MADLN05BE', changed: [], confirmation_required: false,
                       scanned: SERVED_SLAVE }] },
    ...wiring,
  });

  assert.equal(rows.length, 1);
  assert.equal(rows[0].scanRow, RAW_SLAVE);
  assert.equal(axisRowDriverModel(rows[0]), 'MADLN05BE');
});

test('a matched Dynamixel gets its raw device attached', () => {
  const rows = buildAxisRows({
    motors: [dynamixel],
    served: { axes: [{ id: dynamixel.id, transport: 'serial', state: 'matched',
                       model: 'XM540-W150', scanned: SERVED_DEVICE }] },
    ...wiring,
  });

  assert.equal(rows[0].scanDevice, RAW_DEVICE);
  assert.equal(rows[0].scanRow, null);
});

test('an axis the server marked for confirmation says so', () => {
  const rows = buildAxisRows({
    motors: [servo],
    served: { axes: [{ id: servo.id, transport: 'ethercat', state: 'matched',
                       confirmation_required: true, scanned: SERVED_SLAVE }] },
    ...wiring,
  });

  assert.equal(rows[0].servedRow.confirmation_required, true);
});

test('an axis the scan never covered keeps no scan row', () => {
  const rows = buildAxisRows({
    motors: [dynamixel],
    served: { axes: [{ id: dynamixel.id, transport: 'serial', state: 'not_scanned', scanned: null }] },
    ...wiring,
  });

  assert.equal(rows[0].scanDevice, null);
  assert.equal(rows[0].scanRow, null);
});

test('before any scan the project axes still draw', () => {
  const rows = buildAxisRows({ motors: [servo], served: null, ...wiring });

  assert.equal(rows.length, 1);
  assert.equal(rows[0].servedRow, null);
});

test('rows are ordered by axis number', () => {
  const motors = [
    { id: 'b', axis: 2, transport: 'ethercat', config: { controller_index: 2 } },
    { id: 'a', axis: 0, transport: 'ethercat', config: { controller_index: 0 } },
  ];

  assert.deepEqual(buildAxisRows({ motors, served: null }).map((row) => row.id), ['a', 'b']);
});

// --------------------------------------------------------------------------- //
// 목록은 하나다 · 제안 목록이 없다
// --------------------------------------------------------------------------- //

test('the row builder never invents a row the project does not have', () => {
  // **이것이 그 버그다** · 전에는 검색 결과로 행을 하나 더 만들었고,
  // 그 행은 저장 대상이 아니었다.
  const rows = buildAxisRows({
    motors: [],
    served: {
      axes: [],
      new_devices: [{ id: 'ac', transport: 'ethercat', proposed_axis: 0, scanned: SERVED_SLAVE }],
    },
    ...wiring,
  });

  assert.deepEqual(rows, []);
});

test('the screen keeps no second list of axes', () => {
  const code = readFileSync(new URL('../static/js/motor_config.js', import.meta.url), 'utf8')
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/^\s*\/\/.*$/gm, '');

  assert.doesNotMatch(code, /proposedMotor/, '제안 목록이 남아 있습니다');
  assert.doesNotMatch(code, /selectedAxisIds/, '고르는 단계가 남아 있습니다');
  assert.match(code, /function adoptScanIntoDraft\(\)/);
});

test('every scan kind puts what it found into the list', () => {
  const code = readFileSync(new URL('../static/js/motor_config.js', import.meta.url), 'utf8');

  // 전체 검색 · AC 서보 검색 · 다이나믹셀 검색
  assert.equal((code.match(/adoptScanIntoDraft\(\);/g) || []).length, 3);
});

// --------------------------------------------------------------------------- //
// 검색한 것으로 갈아 끼운다 · §6-219
//
// 검색한 통로의 축을 전부 지우고 이번 검색이 찾은 것으로 채운다 ·
// 검색하지 않은 통로는 건드리지 않는다.
// --------------------------------------------------------------------------- //

test('any scan replaces the whole list', () => {
  const code = readFileSync(new URL('../static/js/motor_config.js', import.meta.url), 'utf8');
  const adopt = code.slice(code.indexOf('function adoptScanIntoDraft()'));
  const body = adopt.slice(0, adopt.indexOf('\n  }\n'));

  // 기존 목록을 지우고 찾은 것으로 채운다
  assert.match(body, /axisConfig\.motors = \[\];/);
  // 사람이 붙인 이름은 남긴다
  assert.match(body, /namesById/);
  // 통로별로 반쪽만 바꾸지 않는다
  assert.doesNotMatch(body, /scanned_transports/);
  assert.doesNotMatch(body, /unreachable/);
  assert.doesNotMatch(body, /ethercat_scan/);
});

test('only pressing 설정 저장 writes the file', () => {
  const code = readFileSync(new URL('../static/js/motor_config.js', import.meta.url), 'utf8');
  const apply = code.slice(code.indexOf('async function applyConfigRestart()'));
  const body = apply.slice(0, apply.indexOf('\n  }\n'));

  assert.doesNotMatch(body, /saveAxisConfig/);
});
