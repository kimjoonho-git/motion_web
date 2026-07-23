import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const html = readFileSync(new URL('../static/index.html', import.meta.url), 'utf8');
const dom = readFileSync(new URL('../static/js/dom.js', import.meta.url), 'utf8');
const controller = readFileSync(new URL('../static/js/motion_data.js', import.meta.url), 'utf8');
const api = readFileSync(new URL('../static/js/api.js', import.meta.url), 'utf8');

const actionIds = [
  'refreshMotionMappingsButton',
  'newMotionMappingButton',
  'addMotionIdButton',
  'generateMotionIdsButton',
  'validateMotionMappingButton',
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

test('save performs browser validation before sending the mapping request', () => {
  const saveStart = controller.indexOf('async function saveCurrentMapping()');
  const saveEnd = controller.indexOf('async function resetCurrentMapping()', saveStart);
  const saveBody = controller.slice(saveStart, saveEnd);
  assert.ok(saveStart >= 0 && saveEnd > saveStart);
  assert.ok(saveBody.indexOf('validateMappingDraft()') < saveBody.indexOf('saveMotionMapping({'));
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

test('MIDI-only file saves can advance the editor revision without discarding its draft', () => {
  assert.match(controller, /function syncMappingFileRevision\(file\)/);
  assert.match(controller, /fileId !== selectedMappingId/);
  assert.match(controller, /mappingRevision = revision/);
  assert.match(controller, /syncMappingFileRevision,/);
});
