import assert from 'node:assert/strict';
import test from 'node:test';

import {
  reconcileMotionStudioLayerManagerSelection,
  renderMotionStudioLayerManager,
} from '../static/js/motion_studio_layer_manager.js';

test('layer manager removes merge selections that cannot be merged', () => {
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
    // 막는 이유를 문자열 하나로 낸다 · 잠금이든 곡선 불일치든 · §6-90
    (layer) => (layer.locked ? '잠금'
      : layer.layer_id === 'incomplete' ? '포인트 곡선 불일치 · 1-1' : ''),
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
    layerMergeBlockReason: () => '',
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


/**
 * 녹화한 레이어는 포인트 곡선이 없다 · 스튜디오가 만들어 내는 것이 바로
 * 그것인데, 합치기만 "모든 축이 포인트로 덮여야 한다" 고 요구해서 **영영 합칠
 * 수 없었다** · §6-90
 *
 * 나머지 시스템이 지키는 불변식은 "곡선이 **있으면** 프레임과 맞아야 한다" 이다.
 */
test('a recorded layer can be picked for merging', () => {
  const recorded = { layer_id: 'rec', name: '녹화 1', locked: false, frames: [
    { time_sec: 0.02, values: { '1-1': 0 } },
  ] };
  const state = {
    layerManagerTab: 'merge',
    selectedLayerId: '',
    mergeLayerIds: new Set(['rec']),
    mergeAppendLayerId: '',
    composition: { point_curve_mismatches: [] },
  };

  reconcileMotionStudioLayerManagerSelection(state, [recorded], blockReason(state));

  assert.deepEqual([...state.mergeLayerIds], ['rec'], '녹화 레이어가 합치기에서 빠졌다');
});

test('a layer whose curves disagree with its frames is still refused', () => {
  const edited = { layer_id: 'edit', name: '편집한 레이어', locked: false, frames: [
    { time_sec: 0.02, values: { '1-1': 0 } },
  ] };
  const state = {
    layerManagerTab: 'merge',
    selectedLayerId: '',
    mergeLayerIds: new Set(['edit']),
    mergeAppendLayerId: '',
    composition: {
      point_curve_mismatches: [{ layer_id: 'edit', motion_id: '1-1' }],
    },
  };

  reconcileMotionStudioLayerManagerSelection(state, [edited], blockReason(state));

  assert.deepEqual([...state.mergeLayerIds], [], '어긋난 곡선을 그대로 합치려 한다');
});

/** 화면이 쓰는 것과 같은 판정 · 서버가 낸 불일치 목록만 본다. */
function blockReason(state) {
  return (layer) => {
    if (layer?.locked) return '잠금';
    const mismatched = (state.composition?.point_curve_mismatches || [])
      .filter((item) => String(item.layer_id || '') === String(layer?.layer_id || ''));
    if (mismatched.length) return '포인트 곡선 불일치';
    if (!(layer?.frames || []).length) return '모션 데이터 없음';
    return '';
  };
}
