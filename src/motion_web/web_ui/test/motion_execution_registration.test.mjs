import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import { indexHtml } from '../tools/index_html.mjs';

import {
  registeredMotionFileId,
} from '../static/js/motion_data.js';

const controller = readFileSync(
  new URL('../static/js/motion_data.js', import.meta.url),
  'utf8',
);
// 목록 적재·선택·삭제·스튜디오 내보내기는 `motion_file_manager.js` 가 소유한다.
// 소스를 대조하는 단언은 실제 소유자를 봐야 코드가 옮겨갈 때 함께 따라간다.
const fileManager = readFileSync(
  new URL('../static/js/motion_file_manager.js', import.meta.url),
  'utf8',
);
const html = indexHtml;
const dom = readFileSync(new URL('../static/js/dom.js', import.meta.url), 'utf8');
const main = readFileSync(new URL('../static/js/main.js', import.meta.url), 'utf8');
const api = readFileSync(new URL('../static/js/api.js', import.meta.url), 'utf8');
const projectExplorer = readFileSync(
  new URL('../static/js/project_explorer.js', import.meta.url),
  'utf8',
);

test('motion execution only uses the explicitly registered mapping file', () => {
  assert.equal(registeredMotionFileId({ motion_file_id: 'wave.json' }), 'wave.json');
  assert.equal(registeredMotionFileId({ motion_file_id: '' }), '');
  assert.equal(registeredMotionFileId({}), '');
});

test('one-shot and continuous start include whole-axis initialization automatically', () => {
  const renderStart = controller.indexOf('function renderMotionRunPanel()');
  const renderEnd = controller.indexOf('function renderMotionFileGraph', renderStart);
  const renderBody = controller.slice(renderStart, renderEnd);
  const startStart = controller.indexOf('async function startCurrentMotionRun(');
  const startEnd = controller.indexOf('async function stopCurrentMotionRun()', startStart);
  const startBody = controller.slice(startStart, startEnd);

  assert.doesNotMatch(renderBody, /startReady/);
  assert.match(
    startBody,
    /전체 활성 축을 초기 위치로 이동한 뒤 연속 모션을 시작/,
  );
  assert.match(
    startBody,
    /전체 활성 축을 초기 위치로 이동한 뒤 현재 모션 파일을 1회 실행/,
  );
  assert.match(startBody, /startMotionRun\(\{ \.\.\.motionRunPayload\(\), run_mode: runMode \}\)/);
});

test('motion file list does not arbitrarily select the first file', () => {
  const loadStart = controller.indexOf('async function loadFiles(');
  const loadEnd = controller.indexOf('async function selectFile(', loadStart);
  const loadBody = controller.slice(loadStart, loadEnd);
  const payloadStart = controller.indexOf('function motionRunPayload()');
  const payloadEnd = controller.indexOf('function motionRunInitialMoveTimeSec()', payloadStart);
  const payloadBody = controller.slice(payloadStart, payloadEnd);

  assert.doesNotMatch(loadBody, /selectFile\(files\[0\]\.id\)/);
  assert.doesNotMatch(payloadBody, /selectedFileId/);
  assert.doesNotMatch(payloadBody, /mappingDraft/);
  assert.match(payloadBody, /registeredMotionFileIdValue/);
});

test('late motion file list responses are discarded by request token', () => {
  const loadStart = fileManager.indexOf('async function loadFiles(');
  const loadEnd = fileManager.indexOf('async function selectFile(', loadStart);
  const loadBody = fileManager.slice(loadStart, loadEnd);
  const selectEnd = fileManager.indexOf('async function exportSelectedFileToStudio()', loadEnd);
  const selectBody = fileManager.slice(loadEnd, selectEnd);

  assert.match(loadBody, /const loadToken = \+\+fileLoadToken/);
  assert.match(loadBody, /if \(loadToken !== fileLoadToken\) return/);
  assert.match(selectBody, /requestToken \?\? \+\+fileLoadToken/);
  assert.match(selectBody, /if \(loadToken !== fileLoadToken\) return/);
});

test('loading a motion-axis mapping cannot replace the motion file list', () => {
  const mappingStart = controller.indexOf('async function selectMapping(');
  const mappingEnd = controller.indexOf('async function newMappingDraft()', mappingStart);
  const mappingBody = controller.slice(mappingStart, mappingEnd);

  assert.match(mappingBody, /let loadedMotionFiles = files/);
  assert.match(
    mappingBody,
    /loadedMotionFiles = Array\.isArray\(motionPayload\.files\)[\s\S]*?motionPayload\.files/,
  );
  assert.match(mappingBody, /files = loadedMotionFiles/);
  assert.doesNotMatch(
    mappingBody,
    /loadedMotionFiles = Array\.isArray\(payload\.files\)|files = payload\.files/,
  );
});

test('file list registration is explicit and persists through mapping save', () => {
  assert.match(html, /id="registerMotionFileButton"/);
  assert.match(dom, /registerMotionFileButton: document\.getElementById\('registerMotionFileButton'\)/);
  assert.match(controller, /registerMotionFileButton\?\.addEventListener\('click', registerSelectedMotionFile\)/);

  const registerStart = controller.indexOf('async function registerSelectedMotionFile()');
  const registerEnd = controller.indexOf('async function saveCurrentMapping()', registerStart);
  const registerBody = controller.slice(registerStart, registerEnd);
  assert.match(registerBody, /mappingDraft\.motion_file_id = selectedFile\.id/);
  assert.match(registerBody, /await saveCurrentMapping\(\)/);
});

test('registered motion file can be explicitly unregistered without deleting the file', () => {
  assert.match(html, /id="unregisterMotionFileButton"/);
  assert.match(dom, /unregisterMotionFileButton: document\.getElementById\('unregisterMotionFileButton'\)/);
  assert.match(controller, /unregisterMotionFileButton\?\.addEventListener\('click', unregisterSelectedMotionFile\)/);

  const unregisterStart = controller.indexOf('async function unregisterSelectedMotionFile()');
  const unregisterEnd = controller.indexOf('async function saveCurrentMapping()', unregisterStart);
  const unregisterBody = controller.slice(unregisterStart, unregisterEnd);
  assert.match(unregisterBody, /mappingDraft\.motion_file_id = ''/);
  assert.match(unregisterBody, /await saveCurrentMapping\(\)/);
  assert.doesNotMatch(unregisterBody, /deleteMotionFile/);
});

test('registered motion file deletion is blocked with an alert before delete request', () => {
  const helperStart = fileManager.indexOf('async function showMotionFileDeleteFailure(');
  const deleteStart = fileManager.indexOf('async function deleteSelectedFile()');
  const deleteEnd = fileManager.length;
  const helperBody = fileManager.slice(helperStart, deleteStart);
  const deleteBody = fileManager.slice(deleteStart, deleteEnd);
  // 등록 여부 판정은 주입받는다 · 소유자가 갈라져도 순서 보증은 그대로여야 한다
  const registrationGuard = deleteBody.indexOf('checkIsFileRegistered(selectedFileId)');
  const alertCall = deleteBody.indexOf('showMotionFileDeleteFailure(', registrationGuard);
  const confirmCall = deleteBody.indexOf('showConfirm(', registrationGuard);
  const deleteCall = deleteBody.indexOf('deleteMotionFile(selectedFileId)', registrationGuard);
  const serverGuard = deleteBody.indexOf('payload.success === false', deleteCall);
  const clearSelection = deleteBody.indexOf('selectedFileId = null', serverGuard);
  const projectRefresh = deleteBody.indexOf('await onProjectFilesChange?.()', clearSelection);

  assert.ok(helperStart >= 0, '삭제 불가 알림 헬퍼가 있어야 한다');
  assert.match(helperBody, /showAlert\(/);
  assert.match(helperBody, /title: '모션 파일 삭제 불가'/);
  assert.ok(registrationGuard >= 0, '등록 파일 선검사가 있어야 한다');
  assert.ok(alertCall > registrationGuard, '막았으면 알린다');
  assert.ok(confirmCall > alertCall, '확인은 선검사를 통과한 뒤');
  assert.ok(deleteCall > confirmCall, '삭제 요청은 확인 뒤');
  // 서버도 등록 여부를 검사한다 · 200 + success:false 로 오므로 예외가 아니다.
  // 이 검사를 빼면 삭제되지 않았는데 선택이 풀린다.
  assert.ok(serverGuard > deleteCall, '서버 거절을 검사해야 한다');
  assert.ok(clearSelection > serverGuard, '선택 해제는 성공을 확인한 뒤');
  assert.ok(projectRefresh > clearSelection, '삭제 후 프로젝트 트리를 갱신한다');
  assert.match(deleteBody, /재생 등록된 모션 파일은 삭제할 수 없습니다/);
  assert.match(
    deleteBody,
    /catch \(error\) \{[\s\S]*?await showMotionFileDeleteFailure\(message\)/,
  );
});

test('motion file screen exports the selected file to Studio without project-tree transfer', () => {
  assert.match(html, /id="exportMotionFileToStudioButton"/);
  assert.match(dom, /exportMotionFileToStudioButton: document\.getElementById\('exportMotionFileToStudioButton'\)/);
  assert.match(controller, /exportMotionFileToStudioButton\?\.addEventListener\('click', exportSelectedFileToStudio\)/);
  assert.match(fileManager, /onExportToStudio\(file\.id\)/);
  assert.match(controller, /onExportMotionFileToStudio\(id\)/);
  assert.match(main, /onExportMotionFileToStudio: \(fileName\) => motionStudio\.addMotionFile\(fileName\)/);
  assert.doesNotMatch(projectExplorer, /data-project-add-layer/);
  assert.doesNotMatch(projectExplorer, /onAddMotionLayer/);
});

test('motion file list refreshes from successful Studio exports without manual polling controls', () => {
  const studio = readFileSync(
    new URL('../static/js/motion_studio.js', import.meta.url),
    'utf8',
  );
  assert.doesNotMatch(html, /refreshMotionFilesButton/);
  assert.doesNotMatch(dom, /refreshMotionFilesButton/);
  assert.doesNotMatch(controller, /refreshMotionFilesButton/);
  assert.match(controller, /refreshMotionFiles: \(\) => loadFiles\(\)/);
  // 성공하면 목록을 새로 읽는다
  assert.match(studio, /await onMotionFilesChange\(result\)/);
  // 실패해도 새로 읽는다 · 파일을 쓴 뒤 실패하면 화면만 옛 목록으로 남는다 · §6-53
  assert.match(studio, /if \(failed\) await onMotionFilesChange\(null\)/);
  assert.match(main, /onMotionFilesChange: async \(\) => \{\s*await motionData\.refreshMotionFiles\(\);\s*await projectExplorer\.refresh\(true\)/);
});

test('motion files leave and arrive as plain files · one door each', () => {
  // 나가는 문 · 모션 실행 화면의 「파일로 저장」 · 이미 있는 프로젝트 파일
  // 경로를 그대로 쓴다 · 전용 업로드/다운로드 API 를 새로 내지 않는다.
  assert.match(html, /id="downloadMotionFileButton"[^>]*>파일로 저장</);
  assert.match(dom, /downloadMotionFileButton/);
  assert.match(controller, /function downloadSelectedMotionFile/);
  assert.match(controller, /projectFileDownloadUrl\(motionProjectId, 'motions', file\.id\)/);
  assert.doesNotMatch(api, /motion-files\/upload|uploadMotionFile/);

  // 주소가 프로젝트 번호를 요구한다 · 목록 응답이 실어 오는 값만 쓴다
  assert.match(controller, /motionProjectId = String\(projectId \|\| ''\)/);

  // 들어오는 문 · 프로젝트 관리의 「모션 파일 불러오기」 하나뿐 ·
  // 모션 실행 화면에는 불러오기 입력을 두지 않는다
  assert.match(html, /id="projectImportFileButton"[^>]*>모션 파일 불러오기</);
  assert.doesNotMatch(html, /uploadMotionFileButton|motionFileInput/);
  assert.doesNotMatch(dom, /uploadMotionFileButton|motionFileInput/);
  assert.doesNotMatch(controller, /uploadSelectedFile|uploadMotionFile/);
});

test('only motion files can be brought into a project', () => {
  // 모터축·모션축 설정은 그 PC 의 하드웨어 배선에 매인 값이라 옮기면 꼬인다 ·
  // 종류를 고르는 칸이 있으면 언젠가 다시 새어 든다 · 칸 자체를 없앴다.
  assert.doesNotMatch(html, /projectImportCategory/);
  assert.doesNotMatch(dom, /projectImportCategory/);
  assert.doesNotMatch(projectExplorer, /projectImportCategory/);
  assert.match(projectExplorer, /category: 'motions'/);
  assert.match(html, /id="projectImportFileInput"[^>]*accept="\.json"/);
});

test('an imported motion file shows up in the run screen without a reload', () => {
  // 모션 실행 목록은 프로젝트가 바뀔 때와 스튜디오가 저장할 때만 다시
  // 읽었다 · 가져온 파일이 탭을 옮겨도 안 보였다.
  assert.match(projectExplorer, /if \(imported\) await onMotionFilesChange\(\)/);
  assert.match(main, /onMotionFilesChange: async \(\) => \{\s*await motionData\.refreshMotionFiles\(\);\s*\},/);
});

test('DDS execution blocks show a recovery popup and expose local temporary disable', () => {
  const coordination = readFileSync(
    new URL('../static/js/coordination.js', import.meta.url),
    'utf8',
  );
  assert.match(controller, /async function showMotionRunFailure/);
  assert.match(controller, /DDS 그룹 실행이 로컬 모션 실행을 사용 중입니다/);
  assert.match(controller, /「연동 일시 해제」를 실행한 뒤 다시 시도하세요/);
  assert.match(html, /id="coordinationTemporaryDisableButton"[^>]*>연동 일시 해제</);
  assert.match(dom, /coordinationTemporaryDisableButton/);
  assert.match(coordination, /control\('temporarily_disable'\)/);
  assert.match(coordination, /다른 PC의 확인 없이 이 PC가 그룹에서 나갑니다/);
});
