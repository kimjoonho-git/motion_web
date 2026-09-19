import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { indexHtml } from '../tools/index_html.mjs';

const html = indexHtml;
const dom = readFileSync(new URL('../static/js/dom.js', import.meta.url), 'utf8');
const controller = readFileSync(new URL('../static/js/motor_config.js', import.meta.url), 'utf8');
const main = readFileSync(new URL('../static/js/main.js', import.meta.url), 'utf8');
const api = readFileSync(new URL('../static/js/api.js', import.meta.url), 'utf8');

const actions = {
  addAxisButton: 'addSelectedAxis',
  updateAxisIdentityButton: 'updateSelectedAxisIdentity',
  // 「모델·운전 프로필 설정」과 「고급 장비 설정(EEPROM Alias 변경)」은 지웠다 · §6-205
  deleteAxisButton: 'deleteSelectedAxis',
  toggleAxisButton: 'toggleSelectedAxis',
  sortAxisButton: 'sortAxisNumbers',
  saveAxisConfigButton: 'saveAxisConfig',
  applyAxisConfigButton: 'applyConfigRestart',
  deleteMotorConfigButton: 'deleteCurrentMotorConfig',
  scanAllButton: 'scanAllMotors',
  scanButton: 'scanMotors',
  dynamixelScanButton: 'scanDynamixel',
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

test('position-only legacy axes are merged for explicit batch SII confirmation', () => {
  assert.match(
    controller,
    /resolveRegistryMotorForScanRow\(scanRow, axisMotors\(\)\)/,
  );
  assert.match(
    controller,
    /row\.identityConfirmationRequired = Boolean\(\s*resolved\.confirmationRequired/,
  );
  // **고른 것 중 해당하는 것만 다룬다** · §6-204
  //
  // 전에는 「고른 것이 전부 AC 서보여야」 반영이 켜졌다 · 검색이 나온 것을
  // 전부 고르게 되면서 다이나믹셀이 섞여 늘 회색이 됐다 · 핸들러는 이미
  // 해당하는 행만 골라내므로 막을 이유가 없었다.
  assert.match(
    controller,
    /const canUpdateIdentity = combinedIdentityRows\.length > 0/,
  );
  assert.match(
    controller,
    /model_source: catalogModel\s*\?\s*'verified_catalog'\s*:\s*'physical_sii_user_confirmed'/,
  );
  assert.match(
    html,
    /id="updateAxisIdentityButton"[^>]*>선택 축 검색값 반영</,
  );
});

test('apply and restart completes pending scan confirmation and save first', () => {
  const applyFunction = controller.match(
    /async function applyConfigRestart\(\) \{([\s\S]*?)\n  \}\n\n  function addSelectedAxis/,
  );
  assert.ok(applyFunction, 'applyConfigRestart function missing');
  assert.match(applyFunction[1], /const pendingScanRows = axisRowsData\(\)\.filter/);
  assert.match(applyFunction[1], /await updateSelectedAxisIdentity\(\)/);
  assert.match(applyFunction[1], /await saveAxisConfig\(\)/);
  assert.match(applyFunction[1], /await applyMotorConfig\(\)/);
  assert.ok(
    applyFunction[1].indexOf('await updateSelectedAxisIdentity()')
      < applyFunction[1].indexOf('await saveAxisConfig()'),
  );
  assert.ok(
    applyFunction[1].indexOf('await saveAxisConfig()')
      < applyFunction[1].indexOf('await applyMotorConfig()'),
  );
});

test('program and motor status refresh actions are clearly separated', () => {
  assert.match(
    html,
    /id="programStatusRefreshButton"[^>]*>프로그램 상태 확인</,
  );
  assert.match(
    html,
    /id="motorStatusRefreshButton"[^>]*>모터 상태 확인</,
  );
  assert.match(
    dom,
    /motorStatusRefreshButton: document\.getElementById\('motorStatusRefreshButton'\)/,
  );
  assert.match(
    main,
    /motorStatusRefreshButton\.addEventListener\('click', \(\) => \{\s*fetchStatus\(el\.motorStatusRefreshButton\)/,
  );
  assert.match(html, /id="operationProgressModal"/);
  assert.match(html, /id="operationProgressCloseButton"[^>]*disabled/);
  assert.match(main, /function statusCheckResult\(triggerButton, payload\)/);
});

test('motor type scans and the full scan are directly available without nested controls', () => {
  const scanSection = html.match(
    /<section class="[^"]*\baxis-setup-step\b[^"]*" aria-label="모터 타입별 검색">([\s\S]*?)<\/section>\s*<section class="[^"]*\baxis-settings-panel\b/,
  );
  assert.ok(scanSection, 'primary motor type scan section missing');
  assert.match(scanSection[1], /id="scanButton"[^>]*>AC Servo 검색</);
  assert.match(scanSection[1], /id="dynamixelScanButton"[^>]*>Dynamixel 검색</);
  assert.match(scanSection[1], /id="scanAllButton"[^>]*>전체 모터 검색</);
  assert.match(
    scanSection[1],
    /id="scanAllButton"[\s\S]*id="scanButton"[\s\S]*id="dynamixelScanButton"/,
  );
  assert.doesNotMatch(scanSection[1], /<details class="axis-full-scan-tools"/);
  assert.doesNotMatch(scanSection[1], /class="axis-full-scan-tools"/);
  assert.doesNotMatch(scanSection[1], /전체 모터 순차 검색/);
  assert.doesNotMatch(scanSection[1], /EtherCAT 재검색 후 실제 Slave/);
  assert.doesNotMatch(scanSection[1], /직렬 포트에서 Protocol/);
  assert.match(controller, /let scanRequestRunning = false/);
  assert.match(controller, /\[el\.scanButton, el\.dynamixelScanButton, el\.scanAllButton\]/);
  assert.match(controller, /operationProgress\?\.begin\(\{/);
  assert.match(controller, /operationProgress\?\.finish\(\{/);
  assert.match(
    controller,
    /Master \$\{formatInt\(master\.master_index \?\? 0\)\}/,
  );
  assert.match(
    controller,
    /`\$\{resultState\} · \$\{formatInt\(slaves\.length\)\}축\$\{masterSummary\}`/,
  );
  assert.match(controller, /dynamixelScanResult\.textContent = `\$\{resultState\} · \$\{formatInt\(devices\.length\)\}개`/);
  assert.doesNotMatch(controller, /검색된 축: \$\{slaveText\}/);
  assert.doesNotMatch(controller, /후보 \$\{formatInt\(targetCount\)\}개/);
});

test('project-compatible physical scan gaps are displayed as partial, not failure', () => {
  assert.match(
    controller,
    /const scanPartial = payload\.partial === true\s*\|\| payload\.motor_operation\?\.status === 'partial'/,
  );
  assert.match(controller, /scanPartial\s*\?\s*'직접 검색 부분 완료'/);
  assert.match(
    controller,
    /scanPartial \? 'partial' : ''/,
  );
  assert.match(
    controller,
    /const scanPartial = payload\.partial === true\s*\|\| payload\.scan\?\.scan_outcome === 'partial'/,
  );
});

test('motor configuration file deletion uses the matching DELETE endpoint', () => {
  assert.match(api, /deleteMotorConfig = \(\) => request\('DELETE', '\/api\/motor-config'\)/);
  assert.match(controller, /const payload = await deleteMotorConfig\(\)/);
});

test('저장이 표 편집을 흡수한다 · 중간 단추를 다시 만들지 않는다', () => {
  // 「표 변경값을 초안에 반영」은 브라우저 안에서만 일어나는 중간 단계였다 ·
  // 서버에 아무것도 보내지 않는데 이걸 모르면 저장이 영영 잠겨 있었다 · §6-154
  assert.doesNotMatch(html, /id="updateConfigTableButton"/, '중간 단추가 되살아났다');
  assert.match(
    controller,
    /if \(!applyConfigTableUpdates\(\)\) return false;/,
    '저장이 표 편집을 반영하지 않는다',
  );
  // 표에서 고친 것도 「저장할 것」으로 세야 저장 단추가 켜진다
  assert.match(controller, /hasAnyConfigChanges[\s\S]{0,400}hasConfigTableDrafts\(\)/);
});

test('두 단추가 무엇을 하는지 이름만 보고 알 수 있다', () => {
  assert.match(html, /id="saveAxisConfigButton"[^>]*>설정 저장</);
  assert.match(html, /id="applyAxisConfigButton"[^>]*>장비에 적용 · 모터 재시작</);
});

test('motor configuration file actions are concise', () => {
  assert.match(
    html,
    /id="deleteMotorConfigButton"[^>]*>설정 삭제</,
  );
  assert.match(html, /id="reloadMotorConfigButton"[^>]*>설정 불러오기</);
});
