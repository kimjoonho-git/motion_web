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
