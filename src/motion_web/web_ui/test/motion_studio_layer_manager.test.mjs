import assert from 'node:assert/strict';
import test from 'node:test';

import {
  reconcileMotionStudioLayerManagerSelection,
  renderMotionStudioLayerManager,
} from '../static/js/motion_studio_layer_manager.js';

test('layer manager removes locked and incomplete merge selections', () => {
  const layers = [
    { layer_id: 'ready', locked: false },
    { layer_id: 'locked', locked: true },
    { layer_id: 'incomplete', locked: false },
  ];
  const state = {
    layerManagerTab: 'merge',
    selectedLayerId: 'missing',
    mergeLayerIds: new Set(['ready', 'locked', 'incomplete']),
    mergeAppendLayerId: 'locked',
  };
  reconcileMotionStudioLayerManagerSelection(
    state,
    layers,
    (layer) => (layer.layer_id === 'incomplete' ? ['1-1'] : []),
  );

  assert.equal(state.selectedLayerId, '');
  assert.deepEqual([...state.mergeLayerIds], ['ready']);
  assert.equal(state.mergeAppendLayerId, '');
});

test('layer manager renders copy and append choices from current selection', () => {
  const el = {
    studioManagerLayerRows: { innerHTML: '' },
    studioManagerMergeRows: { innerHTML: '' },
    studioMergeMode: { value: '' },
    studioMergeAppendLayer: { innerHTML: '', value: '', disabled: true },
  };
  const layers = [
    { layer_id: 'first', name: '첫 레이어', locked: false },
    { layer_id: 'second', name: '둘째 레이어', locked: false },
  ];
  const state = {
    project: { layers },
    layerManagerTab: 'copy',
    selectedLayerId: 'second',
    mergeLayerIds: new Set(),
    mergeAppendLayerId: '',
    mergeMode: 'overlay',
  };
  const options = {
    state,
    el,
    escapeHtml: String,
    layerSummary: () => '2프레임',
    layerPointCoverageIssues: () => [],
  };
  renderMotionStudioLayerManager(options);
  assert.match(el.studioManagerLayerRows.innerHTML, /둘째 레이어/);
  assert.match(el.studioManagerLayerRows.innerHTML, /selected-row/);

  state.layerManagerTab = 'merge';
  state.mergeMode = 'append';
  state.mergeLayerIds = new Set(['first', 'second']);
  state.mergeAppendLayerId = 'second';
  renderMotionStudioLayerManager(options);
  assert.match(el.studioManagerMergeRows.innerHTML, /data-manager-layer-merge checked/);
  assert.match(el.studioMergeAppendLayer.innerHTML, /첫 레이어/);
  assert.equal(el.studioMergeAppendLayer.value, 'second');
  assert.equal(el.studioMergeAppendLayer.disabled, false);
});
