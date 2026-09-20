import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import { indexHtml } from '../tools/index_html.mjs';

const api = readFileSync(new URL('../static/js/api.js', import.meta.url), 'utf8');
const html = indexHtml;
const main = readFileSync(new URL('../static/js/main.js', import.meta.url), 'utf8');

// 「모터 제어 재시작」은 지웠다 · §6-228
// 「설정 적용 · 모터 재시작」이 같은 일을 하고 설정까지 새로 반영한다.
test('program restart is the only system restart route', () => {
  assert.match(api, /restartManagedProgram[\s\S]*?\/api\/system\/program\/restart/);
  assert.doesNotMatch(api, /motor-control\/restart/);
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

test('system information displays host and workspace paths', () => {
  assert.match(html, /id="systemHostname"/);
  assert.match(html, /id="systemWorkspacePath"/);
  assert.match(html, /id="systemProjectsPath"/);
  assert.match(html, /id="systemGitRemote"/);
  assert.match(html, /id="systemGitBranch"/);
  assert.match(html, /id="systemGitHead"/);
  assert.match(html, /id="globalGitVersion"[\s\S]*?href="#"/);
  assert.match(main, /const systemInfo = payload\?\.system_info \|\| \{\}/);
  assert.match(main, /systemInfo\.hostname/);
  assert.match(main, /systemInfo\.workspace_root/);
  assert.match(main, /systemInfo\.motion_projects_dir/);
  assert.match(main, /function renderGitVersion/);
  assert.match(main, /branch === 'main' \? 'main' : `\$\{branch\} \(main 아님\)`/);
  assert.match(main, /systemGitRemote\.href = remoteWebUrl/);
  assert.match(main, /systemGitHead\.title = fullHash/);
});

test('program restart stays in the header and motor restart stays in motor management', () => {
  assert.doesNotMatch(html, /id="headerMotorControlRestartButton"/);
  assert.match(html, /id="headerProgramRestartButton"/);
  assert.match(html, /id="programRestartButton"[^>]*>프로그램 재시작</);
  assert.doesNotMatch(html, /motorControlRestartButton/);
  assert.match(
    main,
    /headerProgramRestartButton\.addEventListener\('click'[\s\S]*?programRestartButton\.click\(\)/,
  );
  assert.doesNotMatch(main, /headerMotorControlRestartButton/);
});

test('program restart requires confirmation', () => {
  const programStart = main.indexOf("el.programRestartButton.addEventListener('click'");
  const headerStart = main.indexOf("el.headerProgramRestartButton.addEventListener('click'");
  assert.ok(programStart > 0 && headerStart > programStart);
  const programHandler = main.slice(programStart, headerStart);

  assert.match(programHandler, /await appDialogs\.confirm/);
  assert.match(programHandler, /restartManagedProgram\(\)/);
  assert.doesNotMatch(main, /restartMotorControlSystem/);
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

test('restart status polling has an HTTP deadline and can stop completion monitoring', () => {
  assert.match(api, /fetchStatusSnapshot = \(timeoutMs = 5000\)/);
  // 표로 옮기면서 축약 옵션 `{ timeoutMs }` 를 놓쳐 시간 제한이 사라진 적이 있다 ·
  // 기본값만 보지 말고 실제로 전달되는지 확인한다 · §6-60
  assert.match(api, /request\('GET', '\/api\/status', \{ timeoutMs \}\)/);
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
  assert.match(main, /motorOperation\.type === 'motor_apply'/);
  assert.doesNotMatch(main, /'motor_control'/);
  assert.match(main, /TERMINAL_FAILURES\.has\(operationStatus\)/);
  assert.match(main, /operationStatus === 'running'/);
});

