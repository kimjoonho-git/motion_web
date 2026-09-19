// 새로 검색했는데 모델만 옛 값이 나오던 것 · §6-211
//
// 한 행 안에서 칸마다 보는 순서가 달랐다.
//
//   Slave · EEPROM · Station   `row.scanRow ? 검색값 : 저장값`   ← 검색이 먼저
//   모델                        저장값만                          ← 검색을 안 봄
//
// 그래서 방금 검색을 눌러도 이렇게 보였다.
//
//   축 0   Slave 1 · EEPROM 403     ← 방금 읽은 값
//          모델 미확인               ← 프로젝트에 저장된 옛 값
//
// 모델을 못 읽던 시절에 저장된 프로젝트는 「검색값 반영」을 누르기 전까지
// 영영 옛 값을 보여 줬다 · 다이나믹셀도 같다 (검색은 XM540-W150 을 읽어
// 왔는데도 「모델 미확인」이었다).
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import { axisRowDriverModel, axisRowScannedModel } from '../static/js/motor_config.js';

const MARKER = 'UNVERIFIED_MINAS';

const stored = (driver_model) => ({
  motor: { id: 'ac0', transport: 'ethercat', profile: { driver_model } },
});

test('a freshly scanned servo model shows now, not the stored one', () => {
  // **이것이 그 버그다** · 저장된 값은 모델을 못 읽던 때의 것이다.
  const row = { ...stored(MARKER), scanRow: { sii_order_number: 'MADLN05BE' } };

  assert.equal(axisRowDriverModel(row), 'MADLN05BE');
});

test('a freshly scanned Dynamixel model shows now, not the stored one', () => {
  const row = { ...stored(''), scanDevice: { model_name: 'XM540-W150' } };

  assert.equal(axisRowDriverModel(row), 'XM540-W150');
});

test('a re-scan that found a different drive shows the drive that is there', () => {
  const row = { ...stored('MADLN05BE'), scanRow: { sii_order_number: 'MBDLN25SE' } };

  assert.equal(axisRowDriverModel(row), 'MBDLN25SE');
});

test('without a scan the stored model still shows', () => {
  assert.equal(axisRowDriverModel(stored('MADLN05BE')), 'MADLN05BE');
});

test('the marker is not a model, so the row says it knows nothing', () => {
  assert.equal(axisRowDriverModel(stored(MARKER)), '');
  assert.equal(axisRowDriverModel(stored('')), '');
});

test('a scan that could not read the model falls back to the stored one', () => {
  const row = { ...stored('MADLN05BE'), scanRow: { sii_order_number: '' } };

  assert.equal(axisRowScannedModel(row), '');
  assert.equal(axisRowDriverModel(row), 'MADLN05BE');
});

test('a scanned device with no project axis yet still names its model', () => {
  const row = { motor: null, proposedMotor: null, scanRow: { sii_device_name: 'MADLN05BE' } };

  assert.equal(axisRowDriverModel(row), 'MADLN05BE');
});

test('every column in a row reads the scan first', () => {
  // 칸마다 순서가 갈리면 같은 행이 서로 다른 시점을 보여 준다.
  const source = readSource();
  const model = source.slice(source.indexOf('export function axisRowScannedModel'));

  assert.match(model.slice(0, 400), /row\.scanRow/);
  assert.match(model.slice(0, 400), /row\.scanDevice/);
});

function readSource() {
  return readFileSync(new URL('../static/js/motor_config.js', import.meta.url), 'utf8');
}
