import assert from 'node:assert/strict';
import test from 'node:test';

import {
  bindMotionStudioProjectTransportEvents,
  createMotionStudioState,
  motionStudioExportSelection,
  motionStudioExportResultMessage,
  resetMotionStudioProjectState,
  setMotionStudioMessage,
} from '../static/js/motion_studio_ui.js';

test('motion export popup messages distinguish saved and failed results', () => {
  assert.equal(
    motionStudioExportResultMessage({ file_id: 'wave.json', frame_count: 120 }),
    '모션 실행 파일 저장 완료\n파일 · wave.json\n프레임 · 120개',
  );
  assert.equal(
    motionStudioExportResultMessage(null, new Error('레이어를 1개 선택하세요')),
    '모션 실행 파일이 저장되지 않았습니다.\n원인 · 레이어를 1개 선택하세요',
  );
});

test('motion export selection uses only playback-enabled layers', () => {
  const blueDetailLayerId = 'layer-blue';
  const layers = [
    { layer_id: blueDetailLayerId, name: '연한 파란색 상세 행', enabled: false },
    { layer_id: 'layer-playback', name: '재생 선택 레이어', enabled: true },
  ];

  const selection = motionStudioExportSelection(layers);

  assert.equal(selection.count, 1);
  assert.equal(selection.layer.layer_id, 'layer-playback');
  assert.notEqual(selection.layer.layer_id, blueDetailLayerId);
  assert.deepEqual(motionStudioExportSelection([
    ...layers,
    { layer_id: 'layer-playback-2', enabled: true },
  ]), { count: 2, layer: null });
});

test('studio UI state is isolated per controller and reset clears project runtime fields', () => {
  const first = createMotionStudioState();
  const second = createMotionStudioState();
  first.mergeLayerIds.add('layer-a');
  first.project = { project_id: 'project-a' };
  first.workspaceProject = { project_id: 'workspace-a' };
  first.editor = { dirty: true };
  first.midi = { selected_motion_ids: ['1-1'] };
  first.busy = true;
  first.playbackClock = { runtimeState: 'playing' };
  first.playbackAnimationFrame = 17;
  first.recordingPreviewKey = 'project-a:recording';
  first.layerManagerTab = 'merge';

  resetMotionStudioProjectState(first);

  assert.equal(first.project, null);
  assert.equal(first.workspaceProject, null);
  assert.equal(first.editor, null);
  assert.deepEqual(first.midi, {});
  assert.equal(first.busy, false);
  assert.equal(first.playbackClock, null);
  assert.equal(first.playbackAnimationFrame, 0);
  assert.equal(first.recordingPreviewKey, '');
  assert.equal(first.layerManagerTab, 'create');
  assert.equal(first.mergeLayerIds.size, 0);
  assert.equal(second.project, null);
  assert.equal(second.editor, null);
  assert.equal(second.mergeLayerIds.size, 0);
});

test('resetting one project state does not mutate another project state', () => {
  const projectA = createMotionStudioState();
  const projectB = createMotionStudioState();
  projectA.project = { project_id: 'project-a' };
  projectA.mergeLayerIds.add('layer-a');
  projectB.project = { project_id: 'project-b' };
  projectB.editor = { layerId: 'layer-b' };
  projectB.mergeLayerIds.add('layer-b');

  resetMotionStudioProjectState(projectA);

  assert.equal(projectA.project, null);
  assert.equal(projectA.mergeLayerIds.size, 0);
  assert.deepEqual(projectB.project, { project_id: 'project-b' });
  assert.deepEqual(projectB.editor, { layerId: 'layer-b' });
  assert.deepEqual([...projectB.mergeLayerIds], ['layer-b']);
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
