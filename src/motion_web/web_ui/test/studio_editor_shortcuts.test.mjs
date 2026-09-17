import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

import {
  MOTION_STUDIO_SHORTCUT_BUTTONS,
  MOTION_STUDIO_SHORTCUT_LABELS,
  motionStudioEditorShortcut,
  motionStudioNeighbourPointId,
  motionStudioShortcutAllowed,
  motionStudioTypingTarget,
} from '../static/js/motion_studio_editor_shortcuts.js';

// 레이어 편집기 단축키 · §6-118
//
// 편집 동작만 키로 받는다 · 모터가 도는 것과 저장은 마우스로만.

test('the editing keys map to their actions', () => {
  const cases = [
    [{ key: 'Delete' }, 'deletePoint'],
    [{ key: 'Insert' }, 'addPoint'],
    [{ key: 'a' }, 'addPoint'],
    [{ key: 'Enter' }, 'preview'],
    [{ key: '+' }, 'zoomIn'],
    [{ key: '-' }, 'zoomOut'],
    [{ key: 'f' }, 'fitAll'],
    [{ key: 'Escape' }, 'close'],
    [{ key: 'ArrowLeft' }, 'previousPoint'],
    [{ key: 'ArrowRight' }, 'nextPoint'],
    [{ key: 'z', ctrlKey: true }, 'undo'],
    [{ key: 'Z', ctrlKey: true, shiftKey: true }, 'redo'],
  ];
  for (const [event, action] of cases) {
    assert.equal(motionStudioEditorShortcut(event), action, JSON.stringify(event));
  }
});

test('the emergency stop key is left alone', () => {
  // Ctrl+Shift+E 는 긴급정지다 · 편집기가 가로채면 안 된다
  assert.equal(motionStudioEditorShortcut({ key: 'E', ctrlKey: true, shiftKey: true }), '');
});

test('keys with alt or the windows key are ignored', () => {
  assert.equal(motionStudioEditorShortcut({ key: 'Delete', altKey: true }), '');
  assert.equal(motionStudioEditorShortcut({ key: 'Delete', metaKey: true }), '');
});

test('typing in a field never triggers a shortcut', () => {
  for (const tag of ['INPUT', 'SELECT', 'TEXTAREA']) {
    assert.equal(motionStudioTypingTarget({ tagName: tag }), true, tag);
  }
  assert.equal(motionStudioTypingTarget({ isContentEditable: true }), true);
  assert.equal(motionStudioTypingTarget({ tagName: 'BUTTON' }), false);
  assert.equal(motionStudioTypingTarget(null), false);
});

test('shortcuts need the editor open and no save dialog', () => {
  const open = { editorOpen: true, saveConfirmOpen: false, typing: false };
  assert.equal(motionStudioShortcutAllowed(open), true);
  assert.equal(motionStudioShortcutAllowed({ ...open, editorOpen: false }), false);
  assert.equal(motionStudioShortcutAllowed({ ...open, saveConfirmOpen: true }), false);
  assert.equal(motionStudioShortcutAllowed({ ...open, typing: true }), false);
});

test('arrow keys walk the points and stop at both ends', () => {
  const points = [{ point_id: 'a' }, { point_id: 'b' }, { point_id: 'c' }];
  assert.equal(motionStudioNeighbourPointId(points, 'b', 1), 'c');
  assert.equal(motionStudioNeighbourPointId(points, 'b', -1), 'a');
  assert.equal(motionStudioNeighbourPointId(points, 'c', 1), '');
  assert.equal(motionStudioNeighbourPointId(points, 'a', -1), '');
  assert.equal(motionStudioNeighbourPointId(points, '', 1), 'a');
  assert.equal(motionStudioNeighbourPointId([], 'a', 1), '');
});

// 모터가 도는 것과 저장은 키로 하지 않는다

const FORBIDDEN = [
  'studioRecordButton', 'studioOverdubButton', 'studioPlayButton',
  'studioInitializeButton', 'studioStopButton', 'studioExportButton',
  'studioEditorSaveButton', 'studioEditorSaveConfirmButton',
  'studioEditorUpdateButton', 'studioSelectedLayerDeleteButton',
];

test('no shortcut reaches a motor action or a save', () => {
  const wired = new Set(Object.values(MOTION_STUDIO_SHORTCUT_BUTTONS));
  for (const name of FORBIDDEN) {
    assert.equal(wired.has(name), false, `${name} 이 단축키에 걸려 있다`);
  }
});

test('every wired action has a label to show on the button', () => {
  for (const name of Object.values(MOTION_STUDIO_SHORTCUT_BUTTONS)) {
    assert.ok(MOTION_STUDIO_SHORTCUT_LABELS[name], `${name} 에 키 표기가 없다`);
  }
});

// 실제로 편집기에 물려 있는가 · 검사가 통과해도 안 붙였으면 소용없다

const CONTROLLER = readFileSync(
  fileURLToPath(new URL('../static/js/motion_studio_editor_controller.js', import.meta.url)),
  'utf8',
);

test('the editor binds the shortcut handler', () => {
  assert.match(CONTROLLER, /bindEditorShortcuts\(\);/);
  assert.match(CONTROLLER, /addEventListener\('keydown'/);
});

test('a disabled button is not clickable by key', () => {
  assert.match(CONTROLLER, /if \(!button \|\| button\.disabled\) return;/);
});

test('the button titles get the key written on them', () => {
  assert.match(CONTROLLER, /labelEditorShortcuts\(\)/);
  assert.match(CONTROLLER, /setAttribute\('title'/);
});
