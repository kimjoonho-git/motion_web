import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const html = readFileSync(new URL('../static/index.html', import.meta.url), 'utf8');
const dom = readFileSync(new URL('../static/js/dom.js', import.meta.url), 'utf8');
const controller = readFileSync(new URL('../static/js/motor_config.js', import.meta.url), 'utf8');
const api = readFileSync(new URL('../static/js/api.js', import.meta.url), 'utf8');

const actions = {
  addAxisButton: 'addSelectedAxis',
  updateAxisIdentityButton: 'updateSelectedAxisIdentity',
  writeEthercatAliasButton: 'writeSelectedEthercatAlias',
  deleteAxisButton: 'deleteSelectedAxis',
  toggleAxisButton: 'toggleSelectedAxis',
  sortAxisButton: 'sortAxisNumbers',
  saveAxisConfigButton: 'saveAxisConfig',
  saveConfigTableButton: 'saveAxisConfig',
  applyAxisConfigButton: 'applyConfigRestart',
  updateConfigTableButton: 'applyConfigTableUpdates',
  deleteMotorConfigButton: 'deleteCurrentMotorConfig',
  scanAllButton: 'scanAllMotors',
  scanButton: 'scanMotors',
  dynamixelScanButton: 'scanDynamixel',
  motorScanProgressClearButton: 'clearScanProgressPopup',
  motorScanProgressCloseButton: 'closeScanProgressPopup',
};

test('every motor configuration action button exists and has a controller handler', () => {
  for (const [id, handler] of Object.entries(actions)) {
    assert.match(html, new RegExp(`id=["']${id}["']`), `${id} missing from HTML`);
    assert.match(dom, new RegExp(`${id}: document\\.getElementById\\(["']${id}["']\\)`));
    assert.match(
      controller,
      new RegExp(`${id}\\.addEventListener\\(["']click["'], ${handler}\\)`),
      `${id} is not wired to ${handler}`,
    );
  }
  assert.match(controller, /reloadMotorConfigButton\.addEventListener\('click', \(\) => fetchRegistry\(\)\)/);
});

test('motor type scans are primary and the full scan remains a sequential option', () => {
  const scanSection = html.match(
    /<section class="axis-setup-step" aria-label="모터 타입별 검색">([\s\S]*?)<\/section>\s*<section class="axis-settings-panel"/,
  );
  assert.ok(scanSection, 'primary motor type scan section missing');
  assert.match(scanSection[1], /id="scanButton"[^>]*>AC Servo 검색</);
  assert.match(scanSection[1], /id="dynamixelScanButton"[^>]*>Dynamixel 검색</);
  assert.match(scanSection[1], /전체 모터 순차 검색/);
  assert.match(scanSection[1], /id="scanAllButton"[^>]*>전체 모터 검색</);
  assert.match(controller, /let scanRequestRunning = false/);
  assert.match(controller, /\[el\.scanButton, el\.dynamixelScanButton, el\.scanAllButton\]/);
  assert.match(controller, /scanResult\.textContent = `\$\{resultState\} · \$\{formatInt\(slaves\.length\)\}축`/);
  assert.match(controller, /dynamixelScanResult\.textContent = `\$\{resultState\} · \$\{formatInt\(devices\.length\)\}개`/);
  assert.doesNotMatch(controller, /검색된 축: \$\{slaveText\}/);
  assert.doesNotMatch(controller, /후보 \$\{formatInt\(targetCount\)\}개/);
});

test('motor configuration file deletion uses the matching DELETE endpoint', () => {
  assert.match(api, /function deleteMotorConfig\(\)/);
  assert.match(api, /projectFetch\('\/api\/motor-config', \{ method: 'DELETE' \}\)/);
  assert.match(controller, /const payload = await deleteMotorConfig\(\)/);
});

test('advanced draft actions are named as drafts and file deletion says trash', () => {
  for (const id of [
    'updateConfigTableButton',
  ]) {
    assert.match(
      html,
      new RegExp(`id=["']${id}["'][^>]*>[^<]*초안`),
      `${id} must disclose that it only changes a draft`,
    );
  }
  assert.match(
    html,
    /id="deleteMotorConfigButton"[^>]*>현재 설정 파일 휴지통으로 이동</,
  );
});
