import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import { indexHtml } from '../tools/index_html.mjs';

const html = indexHtml;
const dom = readFileSync(new URL('../static/js/dom.js', import.meta.url), 'utf8');
const controller = readFileSync(new URL('../static/js/motion_data.js', import.meta.url), 'utf8');
const api = readFileSync(new URL('../static/js/api.js', import.meta.url), 'utf8');
const main = readFileSync(new URL('../static/js/main.js', import.meta.url), 'utf8');

const actionIds = [
  'addMotionIdButton',
  'generateMotionIdsButton',
  'saveMotionMappingButton',
  'resetMotionMappingButton',
];

// 파일은 프로젝트마다 하나다 · 고르는 손잡이는 없어졌다 · §6-239
const removedIds = [
  'motionMappingSelect',
  'refreshMotionMappingsButton',
  'newMotionMappingButton',
  'deleteMotionMappingButton',
  'motionMappingName',
  'motionMappingFileSelect',
  'importMotionIdsButton',
];

test('every motion-axis setting action exists in HTML, DOM bindings, and controller events', () => {
  for (const id of actionIds) {
    assert.match(html, new RegExp(`id=["']${id}["']`), `${id} missing from HTML`);
    assert.match(dom, new RegExp(`${id}: document\\.getElementById\\(["']${id}["']\\)`));
    assert.match(controller, new RegExp(`${id}(?:\\?\\.|\\.)addEventListener\\(["']click["']`));
  }
});

test('the mapping file is the one the project registered · no picking, no deleting', () => {
  for (const id of removedIds) {
    assert.doesNotMatch(html, new RegExp(`id=["']${id}["']`), `${id} still in HTML`);
    assert.doesNotMatch(dom, new RegExp(`${id}:`), `${id} still bound in dom.js`);
    assert.doesNotMatch(controller, new RegExp(`el\\.${id}`), `${id} still used by controller`);
  }
  // 등록된 파일을 연다 · 목록의 첫 번째가 아니다
  assert.match(controller, /payload\.active_file_id/);
  assert.doesNotMatch(controller, /selectMapping\(mappingFiles\[0\]\.id\)/);
  // 이름은 고정이다 · 사람이 지을 일이 없다
  assert.match(controller, /const DEFAULT_MAPPING_NAME = 'motion_axis'/);
  // 파일 이름은 보여주기만 한다
  assert.match(html, /id="motionMappingFileName"/);
  assert.match(controller, /function renderMappingFileName\(\)/);
  // 지우는 길 자체가 없다
  assert.doesNotMatch(api, /deleteMotionMapping/);
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

test('revert does not save', () => {
  const resetStart = controller.indexOf('async function resetCurrentMapping()');
  const resetEnd = controller.indexOf('async function refreshMappingAfterReconnect()', resetStart);
  const resetBody = controller.slice(resetStart, resetEnd);
  assert.ok(resetStart >= 0 && resetEnd > resetStart);
  assert.doesNotMatch(resetBody, /saveCurrentMapping\(/);
  assert.match(resetBody, /selectMapping\(selectedMappingId\)/);
});

test('the edit table keeps only the columns that are actually set here', () => {
  // 지운 칸은 흔적도 없어야 한다 · §6-241
  for (const gone of ['모션 보정값', '배율', '초기이동', '기준 사용·캡처']) {
    assert.doesNotMatch(html, new RegExp(`mapping-head-label">${gone}<`), `${gone} 머리글이 남아 있다`);
  }
  for (const field of ['offset_deg', 'scale', 'reference_enabled', 'initial_move_time_sec']) {
    assert.doesNotMatch(
      controller,
      new RegExp(`data-motion-mapping-field="${field}"`),
      `${field} 입력칸이 남아 있다`,
    );
  }
  // 기준점은 캡처로만 채운다 · 켜고 끄는 체크는 없다
  assert.match(html, /기준점 캡처/);
  assert.match(controller, /data-motion-mapping-action="capture_reference"/);
  // 머리글 수와 행의 칸 수가 같아야 한다
  const heads = [...html.matchAll(/mapping-head-label">([^<]+)</g)].length;
  const rowStart = controller.indexOf('<tr data-mapping-index=');
  const rowEnd = controller.indexOf('</tr>', rowStart);
  const cells = [...controller.slice(rowStart, rowEnd).matchAll(/<td[ >]/g)].length;
  assert.equal(cells, heads, `머리글 ${heads} · 행 ${cells}`);
  assert.match(controller, /await onProjectFilesChange\?\.\(\)/);
});

test('the motion-axis page carries nothing but the file, the table, and save', () => {
  // 읽을거리·곁다리 표는 없앴다 · 편집에 쓰는 것만 남는다 · §6-241
  for (const gone of ['미사용 모터축', '고급: 매핑 YAML 원본', '계산 흐름', '값 기준']) {
    assert.ok(!html.includes(gone), `${gone} 이 아직 화면에 있다`);
  }
  for (const id of ['motionMappingUnusedMotorRows', 'motionMappingRawText']) {
    assert.doesNotMatch(html, new RegExp(`id=["']${id}["']`), `${id} 가 남아 있다`);
    assert.doesNotMatch(dom, new RegExp(`${id}:`), `${id} 가 dom.js 에 남아 있다`);
    assert.doesNotMatch(controller, new RegExp(`el\\.${id}`), `${id} 를 아직 쓴다`);
  }
  assert.doesNotMatch(controller, /function renderUnusedMotors\(/);
  assert.doesNotMatch(controller, /mappingRawText/);
});

test('MIDI-only saves use the mapping-section revision without discarding the draft', () => {
  assert.match(controller, /file\?\.mapping_revision \|\| file\?\.revision/);
  assert.match(controller, /function syncMappingFileRevision\(file\)/);
  assert.match(controller, /fileId !== selectedMappingId/);
  assert.match(controller, /mappingRevision = revision/);
  assert.match(controller, /syncMappingFileRevision,/);
});

test('a save conflict says what is lost and never locks the draft away', () => {
  assert.match(controller, /function isMappingRevisionConflict\(message\)/);
  // 무엇을 잃는지 말한다
  assert.match(controller, /저장된 내용을 다시 불러오면 지금 고친 것은 사라집니다/);
  assert.match(controller, /confirmLabel: '고친 것을 버리고 다시 불러오기'/);
  assert.match(controller, /cancelLabel: '그대로 두기'/);
  assert.match(controller, /await selectMapping\(selectedMappingId\)/);
  // 「그대로 두기」 뒤에도 다시 저장할 수 있어야 한다 · §6-243
  const start = controller.indexOf('async function resolveMappingRevisionConflict(');
  const end = controller.indexOf('\n  function ', start);
  const body = controller.slice(start, end);
  assert.match(body, /mappingRevisionConflict = false;/);
  // 저장 첫머리에서 막던 잠금은 없앴다
  const saveStart = controller.indexOf('async function saveCurrentMapping()');
  const saveBody = controller.slice(saveStart, controller.indexOf('async function resetCurrentMapping()', saveStart));
  assert.doesNotMatch(saveBody, /if \(mappingRevisionConflict\)/);
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
