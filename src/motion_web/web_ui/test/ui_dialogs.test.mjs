import assert from 'node:assert/strict';
import test from 'node:test';

import { createDialogManager } from '../static/js/ui_dialogs.js';

class FakeClassList {
  constructor(...names) {
    this.names = new Set(names);
  }

  toggle(name, force) {
    if (force) this.names.add(name);
    else this.names.delete(name);
  }

  contains(name) {
    return this.names.has(name);
  }
}

function fakeElement(...classNames) {
  return {
    classList: new FakeClassList(...classNames),
    dataset: {},
    textContent: '',
    value: '',
    handlers: {},
    focusCount: 0,
    addEventListener(name, handler) {
      this.handlers[name] = handler;
    },
    focus() {
      this.focusCount += 1;
    },
  };
}

function fixture() {
  const previousFocus = fakeElement();
  const body = fakeElement();
  const documentHandlers = {};
  globalThis.document = {
    activeElement: previousFocus,
    body,
    addEventListener(name, handler) {
      documentHandlers[name] = handler;
    },
  };
  globalThis.window = {
    setTimeout(callback) {
      callback();
      return 1;
    },
  };
  return {
    previousFocus,
    documentHandlers,
    el: {
      appDialogModal: fakeElement('hidden'),
      appDialogTitle: fakeElement(),
      appDialogMessage: fakeElement(),
      appDialogInputWrap: fakeElement('hidden'),
      appDialogInput: fakeElement(),
      appDialogCancelButton: fakeElement(),
      appDialogConfirmButton: fakeElement(),
    },
  };
}

test('confirm resolves from common dialog buttons and restores focus', async () => {
  const { el, previousFocus } = fixture();
  const dialogs = createDialogManager({ el });
  const result = dialogs.confirm('계속할까요?', {
    title: '위험 작업',
    confirmLabel: '계속',
    tone: 'danger',
  });

  assert.equal(el.appDialogModal.classList.contains('hidden'), false);
  assert.equal(el.appDialogModal.dataset.tone, 'danger');
  assert.equal(el.appDialogTitle.textContent, '위험 작업');
  assert.equal(el.appDialogConfirmButton.textContent, '계속');
  assert.equal(el.appDialogConfirmButton.classList.contains('danger'), true);

  el.appDialogCancelButton.handlers.click();
  assert.equal(await result, false);
  assert.equal(el.appDialogModal.classList.contains('hidden'), true);
  assert.equal(previousFocus.focusCount, 1);
});

test('prompt returns input and queued alert opens after it', async () => {
  const { el } = fixture();
  const dialogs = createDialogManager({ el });
  const promptResult = dialogs.prompt('이름을 입력하세요', {
    defaultValue: '기본값',
  });
  const alertResult = dialogs.alert('저장 완료');

  assert.equal(el.appDialogInputWrap.classList.contains('hidden'), false);
  assert.equal(el.appDialogInput.value, '기본값');
  el.appDialogInput.value = '새 이름';
  el.appDialogInput.handlers.keydown({
    key: 'Enter',
    preventDefault() {},
  });

  assert.equal(await promptResult, '새 이름');
  assert.equal(el.appDialogMessage.textContent, '저장 완료');
  assert.equal(el.appDialogCancelButton.classList.contains('hidden'), true);
  el.appDialogConfirmButton.handlers.click();
  assert.equal(await alertResult, true);
});

test('Escape cancels confirm but does not dismiss alert', async () => {
  const { el, documentHandlers } = fixture();
  const dialogs = createDialogManager({ el });
  const confirmResult = dialogs.confirm('취소할 수 있습니다');
  documentHandlers.keydown({
    key: 'Escape',
    preventDefault() {},
  });
  assert.equal(await confirmResult, false);

  const alertResult = dialogs.alert('반드시 확인해야 합니다');
  documentHandlers.keydown({
    key: 'Escape',
    preventDefault() {
      throw new Error('alert Escape must not be handled');
    },
  });
  assert.equal(el.appDialogModal.classList.contains('hidden'), false);
  el.appDialogConfirmButton.handlers.click();
  assert.equal(await alertResult, true);
});

test('manager initialization clears a restored blocking dialog state', () => {
  const { el } = fixture();
  el.appDialogModal.classList.names.delete('hidden');
  document.body.classList.names.add('app-dialog-open');
  createDialogManager({ el });
  assert.equal(el.appDialogModal.classList.contains('hidden'), true);
  assert.equal(document.body.classList.contains('app-dialog-open'), false);
});
