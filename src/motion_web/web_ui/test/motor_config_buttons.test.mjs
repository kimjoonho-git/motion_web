import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { indexHtml } from '../tools/index_html.mjs';

const html = indexHtml;
const dom = readFileSync(new URL('../static/js/dom.js', import.meta.url), 'utf8');
const controller = readFileSync(new URL('../static/js/motor_config.js', import.meta.url), 'utf8');
const main = readFileSync(new URL('../static/js/main.js', import.meta.url), 'utf8');
const api = readFileSync(new URL('../static/js/api.js', import.meta.url), 'utf8');

// 축 편집 버튼을 전부 지웠다 · §6-219
// 검색이 찾은 것이 그대로 목록이고, 사람이 고치는 것은 **이름 하나**다 ·
// 「선택 축 추가 · 검색값 반영 · 사용상태 변경 · 축 번호 정렬 · 선택 축 삭제」
// 는 두 목록 사이를 손으로 옮기던 단계였다.
const actions = {
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

test('the scan result is the list · nothing to add by hand', () => {
  // 짝은 서버가 맞추고(§6-216), 찾은 축은 바로 목록에 들어간다(§6-219)
  assert.doesNotMatch(controller, /resolveRegistryMotorForScanRow/);
  assert.doesNotMatch(controller, /addSelectedAxis/);
  assert.match(controller, /function adoptScanIntoDraft\(\)/);
});

// 「설정 적용 · 모터 재시작」은 파일을 바꾸지 않는다 · §6-221
// 파일은 「설정 저장」을 눌렀을 때만 바뀐다.
test('apply and restart never writes the project file', () => {
  const start = controller.indexOf('async function applyConfigRestart()');
  assert.ok(start >= 0, 'applyConfigRestart function missing');
  const body = controller.slice(start, controller.indexOf('\n  }\n', start));

  assert.doesNotMatch(body, /saveAxisConfig\(\)/, '적용이 저장까지 합니다');
  assert.doesNotMatch(body, /updateSelectedAxisIdentity/);
  assert.match(body, /await applyMotorConfig\(\)/);
  // 막지 않고 말로 알린다
  assert.match(body, /저장된 파일\*\*이 적용됩니다/);
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
  // 전체 검색도 **서버 판정**을 따른다 · §6-206
  //
  // `scan.scan_outcome` / `scan.scan_complete` 는 「등록된 Master 가 전부
  // 응답했나」다 · 프로젝트가 쓰지 않는 Master 가 비어 있으면 늘 partial 이라
  // 다 찾았는데도 「일부 검색만 완료됐습니다」가 떴다 · 서버가 프로젝트
  // 기준으로 다시 판정해 success/partial 로 보낸다 (§6-198).
  assert.match(controller, /const scanComplete = payload\.success === true;/);
  assert.match(controller, /const scanPartial = payload\.partial === true;/);
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
  assert.match(html, /id="applyAxisConfigButton"[^>]*>설정 적용 · 모터 재시작</);
});

test('motor configuration file actions are concise', () => {
  assert.match(
    html,
    /id="deleteMotorConfigButton"[^>]*>설정 삭제</,
  );
  assert.match(html, /id="reloadMotorConfigButton"[^>]*>설정 불러오기</);
});
