import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const api = readFileSync(new URL('../static/js/api.js', import.meta.url), 'utf8');
const html = readFileSync(new URL('../static/index.html', import.meta.url), 'utf8');
const main = readFileSync(new URL('../static/js/main.js', import.meta.url), 'utf8');

test('program and motor-control restart actions keep distinct API routes', () => {
  assert.match(
    api,
    /restartManagedProgram[\s\S]*?\/api\/system\/program\/restart/,
  );
  assert.match(
    api,
    /restartMotorControlSystem[\s\S]*?\/api\/system\/motor-control\/restart/,
  );
});

test('program restart stays in the header and motor restart stays in motor management', () => {
  assert.doesNotMatch(html, /id="headerMotorControlRestartButton"/);
  assert.match(html, /id="headerProgramRestartButton"/);
  assert.match(html, /id="programRestartButton"[^>]*>프로그램 재시작</);
  assert.match(
    html,
    /class="motor-readiness-overview"[\s\S]*?id="motorControlRestartButton"[^>]*>모터 제어 재시작</,
  );
  assert.match(
    main,
    /headerProgramRestartButton\.addEventListener\('click'[\s\S]*?programRestartButton\.click\(\)/,
  );
  assert.doesNotMatch(main, /headerMotorControlRestartButton/);
});

test('restart controls require confirmation and invoke only their matching operation', () => {
  const programStart = main.indexOf("el.programRestartButton.addEventListener('click'");
  const motorStart = main.indexOf("el.motorControlRestartButton.addEventListener('click'");
  const headerStart = main.indexOf("el.headerProgramRestartButton.addEventListener('click'");
  assert.ok(programStart > 0 && motorStart > programStart && headerStart > motorStart);
  const programHandler = main.slice(programStart, motorStart);
  const motorHandler = main.slice(motorStart, headerStart);

  assert.match(programHandler, /await appDialogs\.confirm/);
  assert.match(programHandler, /restartManagedProgram\(\)/);
  assert.doesNotMatch(programHandler, /restartMotorControlSystem\(\)/);
  assert.match(motorHandler, /await appDialogs\.confirm/);
  assert.match(motorHandler, /restartMotorControlSystem\(\)/);
  assert.doesNotMatch(motorHandler, /restartManagedProgram\(\)/);
});

test('program restart readiness does not require motor runtime state', () => {
  assert.match(main, /restartCheckMode: ''/);
  assert.match(
    main,
    /if \(restartMode === 'program'\)[\s\S]*?title: '프로그램 재시작 완료'[\s\S]*?const runtime = payload\?\.service_management\?\.runtime/,
  );
  assert.match(
    main,
    /programRestartButton\.addEventListener\('click'[\s\S]*?restartCheckMode = 'program'/,
  );
  assert.match(
    main,
    /onConfigApplyStart:[\s\S]*?restartCheckMode = 'motor_apply'/,
  );
});

test('motor-control restart waits for a new motor status and has a timeout', () => {
  assert.match(
    main,
    /restartCheckMode = 'motor_control'[\s\S]*?restartPreviousMotorStatusAt[\s\S]*?restartMotorControlSystem\(\)/,
  );
  assert.match(
    main,
    /restartMode === 'motor_control'[\s\S]*?새로운 motor_status 수신 대기/,
  );
  assert.match(main, /RESTART_TIMEOUT_MS = 45000/);
  assert.match(main, /startRestartProgressPolling\(\)/);
});

test('motor-control restart requires the selected project motor config to be applied', () => {
  assert.match(
    main,
    /motorConfigApplied = Boolean\(payload\?\.project_scope\?\.motor_config_applied\)/,
  );
  assert.match(
    main,
    /motorControlRestartButton\.disabled = !\(motorManaged && motorConfigApplied\)/,
  );
  assert.match(main, /현재 프로젝트의 모터축 설정을 먼저 적용하세요/);
});

test('restart status polling has an HTTP deadline and can stop completion monitoring', () => {
  assert.match(api, /fetchStatusSnapshot\(timeoutMs = 5000\)/);
  assert.match(api, /controller\.abort\(\)/);
  assert.match(main, /cancelable: true/);
  assert.match(main, /onCancel: cancelRestartCompletionCheck/);
  assert.match(
    main,
    /완료 확인만 중단했습니다\. 이미 요청된 서비스 재시작은 취소되지 않습니다\./,
  );
});
