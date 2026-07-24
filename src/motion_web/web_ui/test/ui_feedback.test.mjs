import assert from 'node:assert/strict';
import test from 'node:test';

import {
  applyFeedbackState,
  feedbackState,
} from '../static/js/ui_feedback.js';

test('feedback messages distinguish busy, success, warning, error, and neutral states', () => {
  assert.equal(feedbackState('설정 파일 저장 중'), 'busy');
  assert.equal(feedbackState('설정 파일 저장 완료'), 'success');
  assert.equal(feedbackState('저장되지 않은 변경 있음'), 'warning');
  assert.equal(feedbackState('설정 파일 저장 실패'), 'error');
  assert.equal(feedbackState('파일을 선택하세요'), 'neutral');
});

test('feedback presentation exposes state and polite live-region semantics', () => {
  const attributes = new Map();
  const element = {
    textContent: '프로젝트 갱신 완료',
    dataset: {},
    classList: { contains: () => false },
    hasAttribute: (name) => attributes.has(name),
    setAttribute: (name, value) => attributes.set(name, value),
  };

  assert.equal(applyFeedbackState(element), 'success');
  assert.equal(element.dataset.feedbackState, 'success');
  assert.equal(attributes.get('aria-live'), 'polite');
  assert.equal(attributes.get('role'), 'status');
});

test('explicit error styling overrides otherwise successful text', () => {
  const element = {
    textContent: '저장 완료',
    dataset: {},
    classList: { contains: (name) => name === 'error-text' },
    hasAttribute: () => true,
    setAttribute: () => {},
  };
  assert.equal(applyFeedbackState(element), 'error');
});
