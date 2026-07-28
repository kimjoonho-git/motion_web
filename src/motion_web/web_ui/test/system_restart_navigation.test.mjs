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

test('system information creates only the packaged desktop shortcut', () => {
  assert.match(
    html,
    /id="desktopShortcutButton"[^>]*>바탕화면 바로가기 만들기</,
  );
  assert.match(
    api,
    /createDesktopShortcut[\s\S]*?\/api\/system\/desktop-shortcut/,
  );
  assert.match(
    main,
    /desktopShortcutButton\.addEventListener\('click'[\s\S]*?createDesktopShortcut\(\)/,
  );
  const shortcutStart = main.indexOf("el.desktopShortcutButton.addEventListener('click'");
  const shortcutEnd = main.indexOf("if (el.programRestartButton)", shortcutStart);
  const shortcutHandler = main.slice(shortcutStart, shortcutEnd);
  assert.ok(shortcutStart > 0 && shortcutEnd > shortcutStart);
  assert.doesNotMatch(shortcutHandler, /restartManagedProgram\(\)/);
  assert.doesNotMatch(shortcutHandler, /restartMotorControlSystem\(\)/);
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

test('motor-control restart waits for its backend operation and has a timeout', () => {
  assert.match(
    main,
    /restartCheckMode = 'motor_control'[\s\S]*?restartOperationId = ''[\s\S]*?restartMotorControlSystem\(\)/,
  );
  assert.match(
    main,
    /restartMode === 'motor_control'[\s\S]*?trackedMotorRestartState/,
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
    /motorControlRestartButton\.disabled = \([\s\S]*?motorOperationRunning[\s\S]*?configApplyInProgress/,
  );
  assert.match(main, /현재 프로젝트의 모터축 설정을 먼저 적용하세요/);
  assert.match(main, /모터 설정·검색·재시작 작업이 진행 중입니다/);
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

test('motor apply and restart use the backend motor operation state', () => {
  assert.match(main, /const motorOperation = payload\?\.motor_operation \|\| \{\}/);
  assert.match(main, /trackedMotorRestartState\([\s\S]*?appState\.restartOperationId/);
  assert.match(
    main,
    /payload\?\.motor_operation\?\.operation_id[\s\S]*?appState\.restartOperationId = operationId/,
  );
  assert.match(main, /motorOperation\.type === 'motor_apply'/);
  assert.match(main, /\['failure', 'timeout', 'cancelled'\]\.includes\(operationStatus\)/);
  assert.match(main, /operationStatus === 'running'/);
});

test('motor-control UI projects backend completion without duplicating motor checks', () => {
  const motorBranchStart = main.indexOf("if (restartMode === 'motor_control')");
  const motorBranchEnd = main.indexOf("} else if (", motorBranchStart);
  const motorBranch = main.slice(motorBranchStart, motorBranchEnd);

  assert.match(motorBranch, /tracked\.state === 'success'/);
  assert.match(motorBranch, /ready: true/);
  assert.doesNotMatch(motorBranch, /state\.motors|faultMotors|disconnectedMotors/);
});
