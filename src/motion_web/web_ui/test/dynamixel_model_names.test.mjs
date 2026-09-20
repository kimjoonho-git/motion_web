// 모델 이름은 **서버가 정한다** · §6-216
//
// 검색기는 모델 번호 1120 을 `XM540-W270` 으로 읽는다 · 서버는 드라이버를
// 만들 때 `XM540-W270-R` 로 적는다 · 화면은 검색한 이름을 그대로 보여 줬다.
//
//   화면    XM540-W270      ← 검색 직후
//   파일    XM540-W270-R    ← 저장된 값
//
// 검색할 때마다 모델 칸의 글자가 왔다 갔다 했다.
//
// 처음에는 화면에도 같은 표를 두고 두 언어를 시험으로 묶었다 (§6-212) ·
// 그건 누더기였다 · 이제 이름표는 서버 한 곳에만 있고, 검색 응답이 정해진
// 이름을 실어 온다 · 화면에는 표가 없다.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

// 주석에는 사연을 적어 두므로 코드만 본다
const BROWSER = readFileSync(
  new URL('../static/js/motor_type_dynamixel.js', import.meta.url),
  'utf8',
).replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');
const SERVER_NAMES = readFileSync(
  new URL('../../web_bridge/motion_web_bridge/motor_identity.py', import.meta.url),
  'utf8',
);
const SERVER_DRIVER = readFileSync(
  new URL('../../web_bridge/motion_web_bridge/motor_config_build.py', import.meta.url),
  'utf8',
);

test('the browser no longer keeps a model name table', () => {
  // **이것이 그 누더기였다** · 같은 표가 두 언어에 있었다.
  assert.doesNotMatch(BROWSER, /XM540-W270-R/);
  assert.doesNotMatch(BROWSER, /canonicalDynamixelModel/);
});

test('the browser passes through what the device said, and no more', () => {
  const body = BROWSER.slice(BROWSER.indexOf('export function modelTextFromDevice'));
  const fn = body.slice(0, body.indexOf('\n}'));

  assert.match(fn, /device\.model_name/);
  assert.doesNotMatch(fn, /XM540/);
});

test('the server names the model in one place', () => {
  assert.match(SERVER_NAMES, /DYNAMIXEL_MODEL_NAMES/);
  assert.match(SERVER_NAMES, /'XM540-W270', 'XM540-W270-R'/);
  assert.match(SERVER_NAMES, /def canonical_dynamixel_model\(/);
});

test('the driver builder looks up drive values by that one name', () => {
  // 이름과 운전 값은 다른 사실이다 · 이름으로 찾되 표는 따로 둔다.
  assert.match(SERVER_DRIVER, /canonical_dynamixel_model\(driver_model\)/);
  assert.match(SERVER_DRIVER, /DYNAMIXEL_DRIVE_VALUES/);
  assert.doesNotMatch(SERVER_DRIVER, /canonical_model = 'XM540/);
});
