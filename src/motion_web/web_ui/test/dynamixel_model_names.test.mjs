// 같은 모터를 두 이름으로 부르던 것 · §6-212
//
// 검색기는 모델 번호 1120 을 `XM540-W270` 으로 읽는다 · 서버는 드라이버를
// 만들 때 `XM540-W270-R` 로 적는다 · 화면은 검색한 이름을 그대로 보여 줬다.
//
//   화면    XM540-W270      ← 검색 직후
//   파일    XM540-W270-R    ← 저장된 값
//
// 검색할 때마다 모델 칸의 글자가 왔다 갔다 했다 · 어느 쪽이 맞는지 알 수 없다.
//
// 표가 파이썬과 자바스크립트로 나뉘어 있으므로 여기서 두 쪽을 맞춰 둔다 ·
// 한쪽만 고치면 이 시험이 막는다.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import { canonicalDynamixelModel, modelTextFromDevice } from '../static/js/motor_type_dynamixel.js';

const SERVER = readFileSync(
  new URL(
    '../../web_bridge/motion_web_bridge/motor_config_build.py',
    import.meta.url,
  ),
  'utf8',
);

test('the browser spells a scanned model the way the file does', () => {
  // **이것이 그 버그다** · 검색 이름과 저장 이름이 갈렸다.
  assert.equal(modelTextFromDevice({ id: 5, model_number: 1120, model_name: 'XM540-W270' }), 'XM540-W270-R');
  assert.equal(modelTextFromDevice({ id: 3, model_number: 1130, model_name: 'XM540-W150' }), 'XM540-W150');
});

test('a model nobody has a name for is left alone', () => {
  assert.equal(modelTextFromDevice({ id: 9, model_number: 9999 }), 'Model 9,999');
  assert.equal(canonicalDynamixelModel(''), '');
  assert.equal(canonicalDynamixelModel(null), '');
});

test('spelling differences do not make a new model', () => {
  assert.equal(canonicalDynamixelModel('xm540_w270'), 'XM540-W270-R');
  assert.equal(canonicalDynamixelModel(' XM540-W270-R '), 'XM540-W270-R');
});

test('both sides carry the same table', () => {
  const server = [...SERVER.matchAll(/canonical_model = '([^']+)'/g)].map((m) => m[1]);

  for (const name of ['XM540-W150', 'XM540-W270-R']) {
    assert.ok(
      server.includes(name),
      `서버가 ${name} 를 더 이상 쓰지 않습니다 · 화면 표도 같이 고치세요`,
    );
  }
});
