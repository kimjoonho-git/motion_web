/** 긴급정지는 알리기만 한다 · 버튼을 끄지 않는다 · §6-189
 *
 * **막다른 길이 있었다.**
 *
 * 긴급정지가 걸리면 화면의 `<button>` 을 **전부** 껐다 · 197개 중 128개.
 *
 *     document.querySelectorAll('button').forEach((button) => {
 *       if (button === el.programRestartButton || ...) return;
 *       button.disabled = true;
 *     });
 *
 * 그 안에 **대화상자의 확인·취소**가 있었다.
 *
 *     알림이 뜬다            → 「확인」이 회색 → 닫을 수가 없다
 *     프로그램 재시작을 누른다 → 「재시작」도 「취소」도 회색 → 갇힌다
 *
 * 허락된 단 하나의 복구 동작이 그 동작의 **확인창 때문에** 막혔다 ·
 * `window.alert` 도 이 대화상자로 이어져 있어서 경고를 닫을 수조차 없었다 ·
 * 탭도 `<button>` 이라 함께 꺼져 상태를 보러 갈 수도, 기록을 읽을 수도 없었다 ·
 * 상태를 받을 때마다 다시 도니까 한 번 열린 버튼도 곧 다시 꺼졌다.
 *
 * **막는 일은 서버가 한다** · 슈퍼바이저가 여덟 자리에서 모터 명령을 거절한다
 * (`EMERGENCY_LATCHED_MESSAGE`) · 버튼마다의 판단도 따로 있다 ·
 * `studioMotorActionBlockReason()` 이 서버가 내려준 `motor_action_blocker` 를
 * 읽는다 · **같은 판단이 세 벌**이었고, 갈리는 날 사람이 갇혔다.
 */

import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
import test from 'node:test';

const JS_DIR = new URL('../static/js/', import.meta.url);
const PANELS = new URL('../static/panels/', import.meta.url);
const MAIN = readFileSync(new URL('main.js', JS_DIR), 'utf8');

function allPanels() {
  return readdirSync(PANELS)
    .filter((name) => name.endsWith('.html'))
    .map((name) => readFileSync(new URL(name, PANELS), 'utf8'))
    .join('\n');
}

/** 주석을 지운 코드 · 설명문에 적어둔 **옛 코드 예시**가 진짜로 보인다 */
function codeOnly(text) {
  return text
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .split('\n')
    .filter((line) => !line.trimStart().startsWith('//'))
    .join('\n');
}

test('화면 전체 버튼을 훑어 끄는 코드가 없다', () => {
  const offenders = [];
  for (const name of readdirSync(JS_DIR).filter((f) => f.endsWith('.js'))) {
    const text = codeOnly(readFileSync(new URL(name, JS_DIR), 'utf8'));
    for (const match of text.matchAll(/querySelectorAll\(\s*['"]button['"]\s*\)/g)) {
      offenders.push(`${name}:${text.slice(0, match.index).split('\n').length}`);
    }
  }

  assert.deepEqual(offenders, [], (
    '버튼을 통째로 훑습니다 · 대화상자와 탭까지 꺼져 사람이 갇힙니다:\n  '
    + offenders.join('\n  ')
  ));
});

test('긴급정지는 알리기만 한다', () => {
  const start = MAIN.indexOf('function showEmergencyLatched() {');
  assert.ok(start > 0, 'showEmergencyLatched() 가 없습니다');
  const body = MAIN.slice(start, MAIN.indexOf('\n}', start));

  // 띠를 띄우고 몸통에 표시를 남긴다 · 그 이상은 하지 않는다
  assert.match(body, /emergencyStopBanner/);
  assert.match(body, /classList\.toggle\('emergency-latched'/);
  assert.doesNotMatch(body, /disabled/);
});

test('끈 버튼을 되살리던 장치도 남지 않았다', () => {
  // 되살리기가 남아 있으면 「전에 꺼져 있었는지」를 기억하는 칸도 남는다
  for (const name of ['emergencyForcedDisabled', 'emergencyPreviousDisabled',
    'data-emergency-keep', 'staysUsableWhileLatched', 'enforceEmergencyUi']) {
    assert.ok(!MAIN.includes(name), `main.js 에 ${name} 이 남아 있습니다`);
  }
  assert.ok(!allPanels().includes('data-emergency-keep'));
});

test('모터 버튼은 서버가 내려준 답으로 끈다', () => {
  // 화면이 스스로 판단하면 서버와 갈린다 · 주인은 하나다
  const start = MAIN.indexOf('function studioMotorActionBlockReason() {');
  assert.ok(start > 0);
  const body = MAIN.slice(start, MAIN.indexOf('\n}', start));

  assert.match(body, /appState\.motorActionBlocker/);
  assert.match(body, /appState\.emergencyLatched/);
});

test('긴급정지를 알리는 띠가 화면에 있다', () => {
  // 버튼을 안 끄므로, 지금 어떤 상태인지는 이 띠가 유일하게 말해준다
  const html = allPanels();

  assert.match(html, /id="emergencyStopBanner"/);
  assert.match(html, /긴급정지 잠김/);
});

test('프로그램 재시작은 제 사정으로만 꺼진다', () => {
  // 서비스가 설치되지 않았을 때만 · 긴급정지와는 무관하다
  const start = MAIN.indexOf('const programRestartBlockedReason =');
  const body = MAIN.slice(start, start + 600);

  assert.match(body, /자동 실행 서비스가 설치되지 않았습니다/);
  assert.doesNotMatch(body, /emergencyLatched/);
});

// --------------------------------------------------------------------------- //
// 저장·적용은 막지 않는다 · §6-203
// --------------------------------------------------------------------------- //

const MOTOR_CONFIG = readFileSync(new URL('../static/js/motor_config.js', import.meta.url), 'utf8');

test('저장 버튼은 끄지 않는다', () => {
  // 전에는 「바뀐 게 없으면」 껐다 · 다시 저장해서 나쁠 일이 없다
  assert.doesNotMatch(
    codeOnly(MOTOR_CONFIG),
    /saveAxisConfigButton\.disabled = !/,
    '저장을 조건부로 막고 있습니다',
  );
  assert.match(MOTOR_CONFIG, /saveAxisConfigButton\.disabled = false/);
});

test('적용(모터 재시작)은 언제든 누를 수 있다', () => {
  // 적용은 곧 모터 재시작이다 · 장비가 이상할 때 가장 먼저 하고 싶은 일이다
  assert.match(MOTOR_CONFIG, /el\.applyAxisConfigButton\.disabled = false;/);
  assert.doesNotMatch(
    codeOnly(MOTOR_CONFIG),
    /applyAxisConfigButton\.disabled = !/,
    '적용을 조건부로 막고 있습니다',
  );
});

test('버튼 이름은 바뀌지 않는다', () => {
  // 「장비에 적용됨」은 상태다 · 누를 수 있는 버튼에는 할 일을 쓴다 · §6-203
  // 그리고 이름이 상황에 따라 바뀌면 같은 버튼을 부를 말이 둘이 된다 · §6-226
  assert.match(codeOnly(MOTOR_CONFIG), /textContent = '설정 적용 · 모터 재시작'/);
  assert.doesNotMatch(codeOnly(MOTOR_CONFIG), /다시 적용/);
});

test('보기만 하는 버튼은 막지 않는다', () => {
  // 다른 일이 도는 중이라고 목록 갱신까지 막았다 · 정작 그 일이 멎었나
  // 보려고 누르는 버튼이다
  const explorer = readFileSync(new URL('../static/js/project_explorer.js', import.meta.url), 'utf8');
  for (const button of ['projectExplorerRefreshButton', 'projectUsbRescanButton']) {
    assert.match(
      codeOnly(explorer),
      new RegExp(`${button}\\.disabled = false`),
      `${button} 을 조건부로 막고 있습니다`,
    );
  }
});

test('안 되는 이유를 보는 버튼을 막지 않는다', () => {
  // 실행 준비 검사는 무엇이 모자란지 보려고 누른다 · 준비가 안 됐다고
  // 막으면 이유를 알 길이 없다
  const data = readFileSync(new URL('../static/js/motion_data.js', import.meta.url), 'utf8');
  const start = data.indexOf('el.motionRunCheckButton.disabled');
  const line = data.slice(start, data.indexOf(';', start));

  assert.doesNotMatch(line, /contextReady/, '준비 안 됐다고 검사를 막습니다');
});

test('다시 저장은 언제나 된다', () => {
  // 「바뀐 게 없으면」으로 막던 곳들 · 다시 저장해서 나쁠 일이 없다
  const alarm = readFileSync(new URL('../static/js/servo_alarm.js', import.meta.url), 'utf8');
  const start = alarm.indexOf('el.servoAlarmSaveButton.disabled');
  const line = alarm.slice(start, alarm.indexOf(';', start));

  assert.doesNotMatch(line, /!dirty/, '바뀐 게 없다고 저장을 막습니다');
});

test('저장하면 팝업으로 알린다', () => {
  // 바뀐 내용이 없으면 화면이 그대로라 「눌렀는데 아무 일도 없다」로 보였다
  const config = readFileSync(new URL('../static/js/motor_config.js', import.meta.url), 'utf8');

  assert.match(config, /title: '설정 저장 완료'/);
  assert.match(config, /title: '설정 저장 실패'/);
});

test('저장이 끝났다고 버튼을 도로 잠그지 않는다', () => {
  // finally 에서 다시 껐다 · 한 번 저장하면 회색이 됐다
  const config = readFileSync(new URL('../static/js/motor_config.js', import.meta.url), 'utf8');
  const start = config.indexOf('saveButton.textContent = originalText;');
  const body = config.slice(start, start + 400);

  assert.doesNotMatch(body, /saveButton\.disabled = !/);
  assert.match(body, /saveButton\.disabled = false/);
});

test('검색하면 나온 것이 그대로 목록이 된다', () => {
  // 전에는 검색 결과가 **제안**이라 「선택 축 추가」로 사람이 옮겨야 했다 ·
  // 고르는 단계 자체를 없앴다 · §6-219
  const config = readFileSync(new URL('../static/js/motor_config.js', import.meta.url), 'utf8');
  const code = codeOnly(config);

  assert.match(code, /function adoptScanIntoDraft\(\)/);
  assert.doesNotMatch(code, /selectedAxisIds/, '고르는 단계가 남아 있습니다');
  assert.doesNotMatch(code, /proposedMotor/, '제안 목록이 남아 있습니다');
  // 세 가지 검색 모두 목록에 넣는다
  assert.equal((code.match(/adoptScanIntoDraft\(\);/g) || []).length, 3);
});

test('전체 검색도 서버 판정을 따른다', () => {
  // scan.scan_complete 는 「등록된 Master 가 전부 응답했나」다 · 프로젝트가
  // 쓰지 않는 Master 가 비면 늘 false 라, 다 찾았는데 「일부만 완료」가 떴다
  const config = readFileSync(new URL('../static/js/motor_config.js', import.meta.url), 'utf8');

  assert.doesNotMatch(codeOnly(config), /scan_complete/, '옛 칸을 봅니다');
  assert.match(config, /const scanComplete = payload\.success === true;/);
});

// --------------------------------------------------------------------------- //
// 축 id 는 화면과 서버가 같아야 한다 · §6-209
// --------------------------------------------------------------------------- //

test('다이나믹셀 축 id 가 서버 규칙과 같다', async () => {
  // 저장하면 서버가 설정 파일을 읽어 id 를 다시 만든다 · 규칙이 다르면
  // 저장 직후 id 가 바뀌고 그 id 로 기억하던 선택이 통째로 풀린다
  const { dynamixelMotorIdFromDevice } = await import('../static/js/motor_type_dynamixel.js');
  const port = '/dev/serial/by-id/usb-FTDI_USB__-__Serial_Converter_X-if00-port0';

  // 서버: f'{motor_type}_{transport}_port_{quote(port, safe="")}_id_{bus_id}'
  assert.equal(
    dynamixelMotorIdFromDevice({ port, id: 3 }),
    `dynamixel_serial_port_${encodeURIComponent(port)}_id_3`,
  );
});

test('서버는 축 id 를 한 곳에서만 만든다', () => {
  // 전에는 서버 안에서도 한 군데에 박혀 있었고 화면이 그것을 옮겨 적었다 ·
  // 이제 `motor_id_for` 하나가 만든다 · 화면은 검색 응답이 실어 온 id 를 쓴다.
  const rules = readFileSync(
    new URL('../../web_bridge/motion_web_bridge/motor_config_rules.py', import.meta.url),
    'utf8',
  );

  assert.match(rules, /def motor_id_for\(/);
  assert.match(rules, /\{motor_type\}_\{transport\}_port_\{port\}_id_/);
  assert.equal(
    (rules.match(/_id_\{/g) || []).length,
    1,
    'id 규칙이 서버 안에서 두 번 적혔습니다',
  );
});

test('AC 서보 축 id 도 서버 규칙과 같다', async () => {
  const { scanRowToMotor } = await import('../static/js/motor_type_ac_servo.js');
  const motor = scanRowToMotor({ master_index: 0, ethercat_alias: 103, slave_position: 0 }, () => 0);

  // 서버: f'{motor_type}_{transport}_master_{index}_alias_{alias}'
  assert.equal(motor.id, 'ac_servo_ethercat_master_0_alias_103');
});
