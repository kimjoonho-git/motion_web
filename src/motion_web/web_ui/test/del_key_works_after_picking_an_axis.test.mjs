// 축 체크칸을 누른 뒤에도 Del 이 먹어야 한다 · §6-264
//
// 입력칸에서는 Del 이 글자 지우기여야 하므로 단축키를 받지 않는다 · 그런데
// 「모션축 선택」의 체크칸도 `<input>` 이라 같이 걸렸다.
//
//     전체 해제 → 축 하나 체크 → 초점이 체크칸에 남는다
//     → 그래프에서 포인트를 골라도 Del 이 먹지 않는다
//     → 그래프를 한 번 누르면 초점이 빠져 그때부터 먹는다
//
// 사람에게는 「포인트 삭제가 안 되다가 다른 걸 만지면 되더라」로 보였다.

import assert from 'node:assert/strict';
import test from 'node:test';
import {
  motionStudioShortcutAllowed,
  motionStudioTypingTarget,
} from '../static/js/motion_studio_editor_shortcuts.js';

const fake = (tagName, type) => ({ tagName, type, isContentEditable: false });

test('축 체크칸은 글자를 치는 곳이 아니다', () => {
  assert.equal(motionStudioTypingTarget(fake('INPUT', 'checkbox')), false);
});

test('단추도 아니다', () => {
  for (const type of ['button', 'submit', 'reset']) {
    assert.equal(motionStudioTypingTarget(fake('INPUT', type)), false, type);
  }
});

test('글자·숫자 칸은 그대로 막는다 · Del 은 글자 지우기여야 한다', () => {
  for (const type of ['text', 'number', 'search', 'password', 'email']) {
    assert.equal(motionStudioTypingTarget(fake('INPUT', type)), true, type);
  }
  assert.equal(motionStudioTypingTarget(fake('TEXTAREA')), true);
});

test('선택칸과 라디오는 그대로 둔다 · 화살표로 값이 바뀐다', () => {
  assert.equal(motionStudioTypingTarget(fake('SELECT')), true);
  assert.equal(motionStudioTypingTarget(fake('INPUT', 'radio')), true);
});

test('type 이 없는 input 은 글자 칸으로 본다', () => {
  assert.equal(motionStudioTypingTarget({ tagName: 'INPUT', isContentEditable: false }), true);
});

test('직접 고쳐 쓰는 곳은 그대로 막는다', () => {
  assert.equal(motionStudioTypingTarget({ tagName: 'DIV', isContentEditable: true }), true);
});

test('축을 체크한 직후에도 단축키가 허용된다', () => {
  const allowed = motionStudioShortcutAllowed({
    editorOpen: true,
    saveConfirmOpen: false,
    typing: motionStudioTypingTarget(fake('INPUT', 'checkbox')),
  });

  assert.equal(allowed, true);
});

test('시간 칸에 값을 치는 중에는 여전히 안 받는다', () => {
  const allowed = motionStudioShortcutAllowed({
    editorOpen: true,
    saveConfirmOpen: false,
    typing: motionStudioTypingTarget(fake('INPUT', 'number')),
  });

  assert.equal(allowed, false);
});
