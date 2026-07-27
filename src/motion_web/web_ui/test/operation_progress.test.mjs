import assert from 'node:assert/strict';
import test from 'node:test';

import { createOperationProgressManager } from '../static/js/operation_progress.js';

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
    disabled: false,
    children: [],
    handlers: {},
    addEventListener(name, handler) {
      this.handlers[name] = handler;
    },
    appendChild(child) {
      this.children.push(child);
    },
    replaceChildren() {
      this.children = [];
    },
    scrollTop: 0,
    scrollHeight: 0,
  };
}

function fixture() {
  const el = {
    operationProgressModal: fakeElement('hidden'),
    operationProgressSpinner: fakeElement(),
    operationProgressTitle: fakeElement(),
    operationProgressElapsed: fakeElement(),
    operationProgressState: fakeElement(),
    operationProgressMessage: fakeElement(),
    operationProgressDetail: fakeElement(),
    operationProgressLog: fakeElement('hidden'),
    operationProgressClearButton: fakeElement('hidden'),
    operationProgressCloseButton: fakeElement(),
  };
  globalThis.document = {
    body: fakeElement(),
    createElement: () => fakeElement(),
  };
  return el;
}

test('common operation popup blocks closing until the operation finishes', () => {
  const el = fixture();
  let currentTime = 1000;
  const progress = createOperationProgressManager({
    el,
    now: () => currentTime,
    setTimer: () => 1,
    clearTimer: () => {},
  });

  assert.equal(progress.begin({
    id: 'scan:all',
    title: '전체 모터 검색',
    mode: 'log',
  }), true);
  assert.equal(progress.isRunning(), true);
  assert.equal(el.operationProgressModal.classList.contains('hidden'), false);
  assert.equal(el.operationProgressCloseButton.disabled, true);
  assert.equal(progress.close(), false);

  currentTime = 2500;
  progress.update({ phase: 'EtherCAT 검색', detail: 'Slave 확인' });
  progress.appendLog('AC Servo 5축', 'success');
  assert.equal(el.operationProgressLog.children.length, 1);
  assert.equal(el.operationProgressElapsed.textContent, '경과 1.5초');

  progress.finish({
    outcome: 'success',
    title: '전체 모터 검색 완료',
  });
  assert.equal(progress.isRunning(), false);
  assert.equal(el.operationProgressCloseButton.disabled, false);
  assert.equal(progress.close(), true);
  assert.equal(el.operationProgressModal.classList.contains('hidden'), true);
});

test('a second blocking operation cannot replace a running operation', () => {
  const el = fixture();
  const progress = createOperationProgressManager({
    el,
    setTimer: () => 1,
    clearTimer: () => {},
  });
  assert.equal(progress.begin({ id: 'restart:program', title: '프로그램 재시작' }), true);
  assert.equal(progress.begin({ id: 'scan:all', title: '전체 모터 검색' }), false);
  assert.equal(progress.activeId(), 'restart:program');
});

test('manager initialization clears a restored blocking overlay state', () => {
  const el = fixture();
  el.operationProgressModal.classList.names.delete('hidden');
  document.body.classList.names.add('operation-modal-open');
  createOperationProgressManager({
    el,
    setTimer: () => 1,
    clearTimer: () => {},
  });
  assert.equal(el.operationProgressModal.classList.contains('hidden'), true);
  assert.equal(document.body.classList.contains('operation-modal-open'), false);
});
