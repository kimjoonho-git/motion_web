import assert from 'node:assert/strict';
import test from 'node:test';

import {
  bindMotionStudioProjectTransportEvents,
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

function eventTarget(value = '') {
  const listeners = new Map();
  return {
    value,
    disabled: false,
    addEventListener(type, handler) {
      listeners.set(type, handler);
    },
    removeEventListener(type, handler) {
      if (listeners.get(type) === handler) listeners.delete(type);
    },
    emit(type) {
      listeners.get(type)?.({ currentTarget: this });
    },
    listenerCount() {
      return listeners.size;
    },
  };
}

test('project and transport events pass stable UI values and can be unbound', () => {
  const el = {
    studioTransitionSafetyLevel: eventTarget('3'),
    studioImportFileSelect: eventTarget('motion-a'),
    studioImportButton: eventTarget(),
    studioRecordButton: eventTarget(),
    studioRecordMode: eventTarget('append'),
    studioInitialMoveTime: eventTarget('2.5'),
    studioInitializeButton: eventTarget(),
    studioPlayButton: eventTarget(),
    studioStopButton: eventTarget(),
    studioCreateLayerButton: eventTarget(),
    studioExportButton: eventTarget(),
    studioExportName: eventTarget(''),
  };
  const calls = [];
  const unbind = bindMotionStudioProjectTransportEvents(el, {
    onTransitionSafetyChange: (value) => calls.push(['transition', value]),
    onImport: (value) => calls.push(['import', value]),
    onRecord: (value) => calls.push(['record', value]),
    onStop: () => calls.push(['stop']),
    defaultExportName: () => 'project-name',
    onExport: (value) => calls.push(['export', value]),
  });

  el.studioTransitionSafetyLevel.emit('change');
  el.studioImportButton.emit('click');
  el.studioRecordButton.emit('click');
  el.studioStopButton.emit('click');
  el.studioExportButton.emit('click');

  assert.deepEqual(calls, [
    ['transition', 3],
    ['import', 'motion-a'],
    ['record', { mode: 'append', initialMoveTimeSec: 2.5 }],
    ['stop'],
    ['export', 'project-name'],
  ]);
  assert.equal(el.studioStopButton.disabled, true);
  unbind();
  assert.equal(el.studioRecordButton.listenerCount(), 0);
});
