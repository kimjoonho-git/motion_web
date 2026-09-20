import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import { indexHtml, stylesCss } from '../tools/index_html.mjs';

const html = indexHtml;
const controller = readFileSync(new URL('../static/js/motor_config.js', import.meta.url), 'utf8');
const motionTest = readFileSync(new URL('../static/js/motion_test.js', import.meta.url), 'utf8');
const styles = stylesCss;

test('motor readiness summary keeps each title and value on one compact row', () => {
  assert.match(
    styles,
    /\.motor-readiness-summary > div\s*\{[\s\S]*?display: flex;[\s\S]*?min-height: 40px;/,
  );
  assert.match(
    styles,
    /\.motor-readiness-summary strong\s*\{[\s\S]*?text-overflow: ellipsis;[\s\S]*?white-space: nowrap;/,
  );
});

test('axis readiness table keeps runtime facts distinct', () => {
  for (const heading of [
    '실제 장치 식별',
    '모델·운전 프로필',
    '설정·실행 적용',
    '모션 매칭',
    '서보·토크',
    '조그·동작',
    '모션 실행',
    '최종 상태',
  ]) {
    assert.match(html, new RegExp(`<th>${heading}</th>`));
  }
  assert.match(controller, /motion_axis_configured === true/);
  assert.match(controller, /runtime\.servo_on === true/);
  assert.match(controller, /축별 실행 이력은 미지원/);
  assert.match(controller, /실물 검증 미확인/);
  assert.doesNotMatch(controller, /aria-label="실제 서보 드라이버 모델"/);
  assert.match(controller, /model_confirmed/);
  assert.match(controller, /모델 미확인/);
  assert.match(controller, /Vendor \$\{displayText\(vendor\)\}/);
  assert.match(controller, /EEPROM Alias \$\{displayText\(eepromAlias\)\}/);
  assert.match(controller, /Slave Position \$\{displayText\(position\)\}/);
  assert.match(controller, /기존 축 연결 확인/);
  // 확인 필요 여부도 서버가 말한다 · §6-216
  assert.match(controller, /servedRow\?\.confirmation_required/);
  assert.match(controller, /SII 참고값/);
});

// 모터 관리 윗부분의 상태 표시는 전부 지웠다 · §6-229
// 준비 단계 7칸 · 모터 종류 요약표 · 안내 문구 · 「모터 상태 확인」
test('모터 관리에 상태 표시 덩이가 없다', () => {
  assert.doesNotMatch(html, /motor-readiness-overview/);
  assert.doesNotMatch(html, /motorReadinessSteps/);
  assert.doesNotMatch(html, /motorTypeRows/);
  assert.doesNotMatch(controller, /renderMotorReadiness|renderMotorTypeStatus/);
  assert.doesNotMatch(html, /id="[^"]*Dynamixel[^"]*Torque/);
});

test('existing AC servo API is reachable per axis from motor management', () => {
  assert.match(controller, /data-axis-servo-action="servo_on"/);
  assert.match(controller, /data-axis-servo-action="servo_off"/);
  assert.match(controller, /data-axis-servo-action="fault_reset"/);
  assert.match(controller, /onAcServoControl\(button\.dataset\.axisServoAction/);
  assert.match(motionTest, /controlAcServo: async \(action, axis\)/);
  assert.match(motionTest, /sendAcServoControl\(action, 'selected'\)/);
});

test('current project name is visually emphasized without emphasizing an empty state', () => {
  assert.match(styles, /\.header-project-name\s*\{[\s\S]*?background: #1f5fca;[\s\S]*?color: #fff;/);
  assert.match(styles, /\.header-project-name\.empty\s*\{[\s\S]*?background: #f5f9fd;/);
  assert.match(styles, /\.project-current-display\s*\{[\s\S]*?background: #1f5fca;[\s\S]*?color: #fff;/);
  assert.match(styles, /\.project-current-display\.empty\s*\{[\s\S]*?background: #f5f9fd;/);
});

test('motor setting tabs use concise names', () => {
  assert.match(html, /data-axis-settings-tab="table">상세 설정 표</);
  assert.match(html, /data-axis-settings-tab="raw">원본 보기</);
  assert.doesNotMatch(html, />설정 표</);
  assert.doesNotMatch(html, />고급 원본 보기</);
});

test('motor management actions follow control, edit, save and apply groups', () => {
  assert.match(
    html,
    /장비 제어[\s\S]*id="allAcServoOnButton"[\s\S]*id="allAcServoOffButton"/,
  );
  // 「모터 제어 재시작」은 지웠다 · §6-228
  // 「설정 적용 · 모터 재시작」이 같은 일을 하고 설정까지 새로 반영한다.
  assert.doesNotMatch(html, /motorControlRestartButton/);
  // 축 편집 버튼은 전부 지웠다 · §6-219
  // 검색이 찾은 것이 그대로 목록이고 사람이 고치는 것은 이름 하나다.
  for (const gone of [
    'addAxisButton', 'updateAxisIdentityButton', 'toggleAxisButton',
    'sortAxisButton', 'deleteAxisButton', 'axis-edit-toolbar',
  ]) {
    assert.doesNotMatch(html, new RegExp(gone), `${gone} 가 남아 있습니다`);
  }
  // 고칠 수 있는 칸은 이름뿐이다
  assert.match(controller, /aria-label="축 이름"/);
  assert.doesNotMatch(controller, /aria-label="축 번호"/);
  assert.doesNotMatch(html, /<th>선택<\/th>/);
  assert.match(
    html,
    /3\. 저장하고 설정 적용[\s\S]*id="saveAxisConfigButton"[\s\S]*id="applyAxisConfigButton"/,
  );
  assert.doesNotMatch(html, /id="saveConfigTableButton"/);
  assert.match(styles, /\.settings-final-actions\s*\{[\s\S]*?grid-template-columns: repeat\(2,/);
});

// 모델을 몰라도 저장도 적용도 된다 · §6-213
// 못 읽은 축이 있으면 확인창에서 말로 알린다 · 막지는 않는다.
test('an unreadable model stops neither the save nor the apply', () => {
  const saveFlow = controller.match(
    /async function saveAxisConfig\(\)[\s\S]*?async function applyConfigRestart\(\)/,
  )?.[0] || '';
  const applyFlow = controller.match(
    /async function applyConfigRestart\(\)[\s\S]*?const confirmed = await showConfirm/,
  )?.[0] || '';

  assert.doesNotMatch(saveFlow, /unverifiedAcModels/);
  assert.match(saveFlow, /await saveMotorConfig/);
  assert.match(applyFlow, /modelProfileWarningMessage\(\)/);
  assert.match(applyFlow, /const modelWarning = /);
  assert.doesNotMatch(
    applyFlow.match(/const applyBlockMessage[\s\S]*?;/)?.[0] || '',
    /modelWarning/,
  );
});

test('project selection lives in the left project sidebar only', () => {
  const sidebar = html.match(/<aside class="project-sidebar"[\s\S]*?<\/aside>\s*<section id="projectSetupProgress"/)?.[0] || '';
  assert.match(sidebar, /id="projectExplorerCurrentName"/);
  assert.match(sidebar, /id="projectExplorerSelect"/);
  assert.doesNotMatch(html, /class="project-system-manager system-project-card"/);
});

test('system status cards use compact exact desktop columns', () => {
  assert.match(
    styles,
    /\.system-runtime-grid\s*\{[\s\S]*?grid-template-columns: minmax\(0, 3fr\) minmax\(400px, 2fr\)/,
  );
  // 프로젝트 관리 도구와 메모는 반반이다 · 가져오기 종류 칸이 빠지면서
  // 왼쪽이 비었는데 3:2 가 그대로라 한쪽만 휑했다
  assert.match(
    styles,
    /\.system-project-grid\s*\{[\s\S]*?grid-template-columns: minmax\(0, 1fr\) minmax\(0, 1fr\)/,
  );
  assert.match(
    styles,
    /@media \(max-width: 1500px\)[\s\S]*?\.system-runtime-grid,[\s\S]*?\.system-project-grid\s*\{[\s\S]*?grid-template-columns: 1fr/,
  );
  assert.match(
    html,
    /class="system-runtime-grid"[\s\S]*class="system-overview-card"[\s\S]*class="project-system-manager system-program-card"/,
  );
  assert.match(
    styles,
    /\.system-status-primary\s*\{[\s\S]*?grid-template-columns: minmax\(0, 1\.35fr\) repeat\(4,/,
  );
  assert.match(styles, /\.system-status-primary > div\s*\{[\s\S]*?min-height: 56px;/);
  assert.match(styles, /\.system-status-secondary\s*\{[\s\S]*?grid-template-columns: repeat\(2,/);
  assert.match(styles, /\.system-status-secondary > div\s*\{[\s\S]*?min-height: 46px;/);
});

test('system project tools and memo use a fixed two-column layout', () => {
  assert.match(
    styles,
    /\.system-project-grid\s*\{[\s\S]*?grid-template-columns: minmax\(0, 1fr\) minmax\(0, 1fr\)/,
  );
  assert.match(
    html,
    /class="system-project-grid"[\s\S]*class="project-system-manager system-project-tools"[\s\S]*class="project-system-manager system-project-memo"/,
  );
  assert.doesNotMatch(html, /<details class="project-system-manager system-project-tools"/);
});

test('project information is read-only and file actions live in the popup menu', () => {
  const manager = html.match(
    /<section id="projectFileManager"[\s\S]*?<textarea id="projectFileEditor"[\s\S]*?<\/section>/,
  )?.[0] || '';
  assert.match(
    manager,
    /class="section-head compact system-project-info-head"[\s\S]*id="projectFileEditorTitle"[\s\S]*id="projectFileInfo"/,
  );
  assert.equal((manager.match(/class="section-head/g) || []).length, 1);
  assert.match(manager, /id="projectFileEditor"[\s\S]*readonly disabled/);
  assert.doesNotMatch(manager, /id="projectFile(?:OpenEditor|Rename|Activate|Export|Delete)Button"/);
  assert.match(html, /id="projectFileActionMenu"[\s\S]*id="projectFileOpenEditorButton"[\s\S]*id="projectFileDeleteButton"/);
});

