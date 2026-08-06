import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const html = readFileSync(new URL('../static/index.html', import.meta.url), 'utf8');
const dom = readFileSync(new URL('../static/js/dom.js', import.meta.url), 'utf8');
const controller = readFileSync(new URL('../static/js/motion_data.js', import.meta.url), 'utf8');
const api = readFileSync(new URL('../static/js/api.js', import.meta.url), 'utf8');
const main = readFileSync(new URL('../static/js/main.js', import.meta.url), 'utf8');

const actionIds = [
  'refreshMotionMappingsButton',
  'newMotionMappingButton',
  'addMotionIdButton',
  'generateMotionIdsButton',
  'saveMotionMappingButton',
  'resetMotionMappingButton',
  'deleteMotionMappingButton',
  'importMotionIdsButton',
];

test('every motion-axis setting action exists in HTML, DOM bindings, and controller events', () => {
  for (const id of actionIds) {
    assert.match(html, new RegExp(`id=["']${id}["']`), `${id} missing from HTML`);
    assert.match(dom, new RegExp(`${id}: document\\.getElementById\\(["']${id}["']\\)`));
    assert.match(controller, new RegExp(`${id}(?:\\?\\.|\\.)addEventListener\\(["']click["']`));
  }
});

test('one action validates, previews, and then saves the mapping', () => {
  const saveStart = controller.indexOf('async function saveCurrentMapping()');
  const saveEnd = controller.indexOf('async function resetCurrentMapping()', saveStart);
  const saveBody = controller.slice(saveStart, saveEnd);
  assert.ok(saveStart >= 0 && saveEnd > saveStart);
  assert.ok(saveBody.indexOf('validateMappingDraft()') < saveBody.indexOf('validateMotionMapping({'));
  assert.ok(saveBody.indexOf('validateMotionMapping({') < saveBody.indexOf('saveMotionMapping({'));
  assert.match(saveBody, /base_mapping_revision: mappingRevision/);
  assert.match(html, />검증·미리보기·저장<\/button>/);
  assert.doesNotMatch(html, /id="validateMotionMappingButton"/);
});

test('revert does not save and deletion uses the recoverable project DELETE path', () => {
  const resetStart = controller.indexOf('async function resetCurrentMapping()');
  const resetEnd = controller.indexOf('async function deleteCurrentMapping()', resetStart);
  const resetBody = controller.slice(resetStart, resetEnd);
  assert.doesNotMatch(resetBody, /saveCurrentMapping\(/);
  assert.match(resetBody, /selectMapping\(selectedMappingId\)/);
  assert.match(api, /projectFetch\(`\/api\/motion-mappings\/\$\{encodeURIComponent\(fileId\)\}`/);
  assert.match(api, /method: 'DELETE'/);
});

test('reference use is editable and project file explorer refresh is wired after changes', () => {
  assert.match(controller, /data-motion-mapping-field="reference_enabled"/);
  assert.match(controller, /await onProjectFilesChange\?\.\(\)/);
  assert.match(html, /기준 사용·캡처/);
});

test('MIDI-only saves use the mapping-section revision without discarding the draft', () => {
  assert.match(controller, /file\?\.mapping_revision \|\| file\?\.revision/);
  assert.match(controller, /function syncMappingFileRevision\(file\)/);
  assert.match(controller, /fileId !== selectedMappingId/);
  assert.match(controller, /mappingRevision = revision/);
  assert.match(controller, /syncMappingFileRevision,/);
});

test('mapping revision conflicts explain recovery and block blind retries', () => {
  assert.match(controller, /function isMappingRevisionConflict\(message\)/);
  assert.match(controller, /저장된 모션축 설정과 이 화면이 기준으로 삼은 설정이 다릅니다/);
  assert.match(controller, /confirmLabel: '저장된 내용 불러오기'/);
  assert.match(controller, /if \(mappingRevisionConflict\)/);
  assert.match(controller, /await selectMapping\(selectedMappingId\)/);
});

test('program reconnect refreshes clean mappings and preserves dirty drafts', () => {
  const start = controller.indexOf('async function refreshMappingAfterReconnect()');
  const end = controller.indexOf('\n  function ', start);
  const body = controller.slice(start, end > start ? end : undefined);
  assert.match(body, /if \(!mappingDirty\)/);
  assert.match(body, /await selectMapping\(selectedMappingId\)/);
  assert.match(body, /if \(currentRevision && currentRevision !== mappingRevision\)/);
  assert.match(body, /편집 내용은 유지 중/);
  assert.match(controller, /refreshMappingAfterReconnect,/);
  assert.match(main, /motionData\?\.refreshMappingAfterReconnect\?\.\(\)/);
});
