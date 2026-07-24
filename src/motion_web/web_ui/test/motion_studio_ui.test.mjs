import assert from 'node:assert/strict';
import test from 'node:test';

import {
  createMotionStudioState,
  resetMotionStudioProjectState,
  setMotionStudioMessage,
} from '../static/js/motion_studio_ui.js';

test('studio UI state is isolated per controller and reset preserves runtime fields', () => {
  const first = createMotionStudioState();
  const second = createMotionStudioState();
  first.mergeLayerIds.add('layer-a');
  first.project = { project_id: 'project-a' };
  first.editor = { dirty: true };

  resetMotionStudioProjectState(first);

  assert.equal(first.project, null);
  assert.deepEqual(first.editor, { dirty: true });
  assert.equal(second.mergeLayerIds.size, 0);
});

test('studio message rendering preserves text and error state', () => {
  const classes = new Map();
  const element = {
    textContent: '',
    classList: { toggle: (name, enabled) => classes.set(name, enabled) },
  };

  setMotionStudioMessage(element, '저장 실패', true);

  assert.equal(element.textContent, '저장 실패');
  assert.equal(classes.get('error-text'), true);
});
