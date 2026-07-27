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
  const programHandler = main.match(
    /if \(el\.programRestartButton\) \{([\s\S]*?)\n\}\n\nif \(el\.motorControlRestartButton\)/,
  )?.[1] || '';
  const motorHandler = main.match(
    /if \(el\.motorControlRestartButton\) \{([\s\S]*?)\n\}\n\nif \(el\.headerProgramRestartButton\)/,
  )?.[1] || '';

  assert.match(programHandler, /window\.confirm/);
  assert.match(programHandler, /restartManagedProgram\(\)/);
  assert.doesNotMatch(programHandler, /restartMotorControlSystem\(\)/);
  assert.match(motorHandler, /window\.confirm/);
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
