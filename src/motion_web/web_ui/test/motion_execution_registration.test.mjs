import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

import {
  motionFileOriginalText,
  registeredMotionFileId,
} from '../static/js/motion_data.js';

const controller = readFileSync(
  new URL('../static/js/motion_data.js', import.meta.url),
  'utf8',
);
const html = readFileSync(new URL('../static/index.html', import.meta.url), 'utf8');
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
  const loadStart = controller.indexOf('async function loadFiles(');
  const loadEnd = controller.indexOf('async function selectFile(', loadStart);
  const loadBody = controller.slice(loadStart, loadEnd);
  const selectEnd = controller.indexOf('async function exportSelectedFileToStudio()', loadEnd);
  const selectBody = controller.slice(loadEnd, selectEnd);

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

test('original motion file view prefers the complete file content without truncation', () => {
  const complete = `header\n${'x'.repeat(15000)}\nlast-frame`;
  assert.equal(
    motionFileOriginalText(
      { content: complete, content_preview: complete.slice(0, 12000) },
      {},
    ),
    complete,
  );
});

test('registered motion file deletion is blocked with an alert before delete request', () => {
  const deleteStart = controller.indexOf('async function deleteSelectedFile()');
  const deleteEnd = controller.indexOf('async function refreshMotionRunStatus()', deleteStart);
  const deleteBody = controller.slice(deleteStart, deleteEnd);
  const registrationGuard = deleteBody.indexOf('selectedFileId === registeredMotionFileIdValue');
  const alertCall = deleteBody.indexOf('showAlert(', registrationGuard);
  const confirmCall = deleteBody.indexOf('showConfirm(', registrationGuard);
  const deleteCall = deleteBody.indexOf('deleteMotionFile(selectedFileId)', registrationGuard);

  assert.ok(registrationGuard >= 0);
  assert.ok(alertCall > registrationGuard);
  assert.ok(confirmCall > alertCall);
  assert.ok(deleteCall > confirmCall);
  assert.match(deleteBody, /재생 등록된 모션 파일은 삭제할 수 없습니다/);
  assert.match(deleteBody, /if \(payload\.success === false\)/);
});

test('motion file screen exports the selected file to Studio without project-tree transfer', () => {
  assert.match(html, /id="exportMotionFileToStudioButton"/);
  assert.match(dom, /exportMotionFileToStudioButton: document\.getElementById\('exportMotionFileToStudioButton'\)/);
  assert.match(controller, /exportMotionFileToStudioButton\?\.addEventListener\('click', exportSelectedFileToStudio\)/);
  assert.match(controller, /onExportMotionFileToStudio\(file\.id\)/);
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
  assert.match(studio, /if \(!result\) return null;\s*await onMotionFilesChange\(result\)/);
  assert.match(main, /onMotionFilesChange: async \(\) => \{\s*await motionData\.refreshMotionFiles\(\);\s*await projectExplorer\.refresh\(true\)/);
});

test('external JSON upload is not exposed by the motion file UI or API client', () => {
  assert.doesNotMatch(html, /uploadMotionFileButton|motionFileInput/);
  assert.doesNotMatch(dom, /uploadMotionFileButton|motionFileInput/);
  assert.doesNotMatch(controller, /uploadSelectedFile|uploadMotionFile/);
  assert.doesNotMatch(api, /motion-files\/upload|uploadMotionFile/);
  assert.doesNotMatch(html, /option value="motions">모션 파일/);
  assert.doesNotMatch(projectExplorer, /'motor_axes', 'motion_axis_matching', 'motions', 'layers'/);
});
