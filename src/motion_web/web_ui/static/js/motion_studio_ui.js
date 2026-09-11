import { escapeHtml } from './format.js?v=20260911095307';

export function createMotionStudioState() {
  return {
    mappings: [], motionFiles: [], project: null, workspaceProject: null,
    status: {}, midi: {}, composition: {
      conflicts: [], point_curve_mismatches: [], conflict_free: true,
    }, busy: false,
    axisRenderKey: '', selectedLayerId: '', layerDetailMode: 'composition',
    activeLayerDetailTab: 'graph',
    editor: null, detailGraph: null, playbackGraphRenderedAt: 0,
    lastPlaybackDisplayState: 'idle',
    playbackClock: null, playbackAnimationFrame: 0,
    recordingPreviewKey: '',
    snapshotAxesKey: '',
    snapshotControlsKey: '',
    layerManagerTab: 'create', mergeLayerIds: new Set(),
    mergeMode: 'preserve', mergeAppendLayerId: '',
    mergeResultMessage: '', mergeResultError: false,
  };
}

export function resetMotionStudioProjectState(state) {
  state.mappings = [];
  state.motionFiles = [];
  state.project = null;
  state.workspaceProject = null;
  state.status = {};
  state.midi = {};
  state.composition = {
    conflicts: [], point_curve_mismatches: [], conflict_free: true,
  };
  state.busy = false;
  state.axisRenderKey = '';
  state.selectedLayerId = '';
  state.layerDetailMode = 'composition';
  state.activeLayerDetailTab = 'graph';
  state.editor = null;
  state.detailGraph = null;
  state.playbackGraphRenderedAt = 0;
  state.lastPlaybackDisplayState = 'idle';
  state.playbackClock = null;
  state.playbackAnimationFrame = 0;
  state.recordingPreviewKey = '';
  state.snapshotAxesKey = '';
  state.snapshotControlsKey = '';
  state.layerManagerTab = 'create';
  state.mergeLayerIds = new Set();
  state.mergeMode = 'preserve';
  state.mergeAppendLayerId = '';
  state.mergeResultMessage = '';
  state.mergeResultError = false;
  return state;
}

export function setMotionStudioMessage(element, message, error = false) {
  if (!element) return;
  element.textContent = message || '';
  element.classList.toggle('error-text', error);
}

export function motionStudioExportResultMessage(result, error = null) {
  if (error) {
    return `모션 실행 파일이 저장되지 않았습니다.\n원인 · ${error.message || String(error)}`;
  }
  return (
    '모션 실행 파일 저장 완료\n'
    + `파일 · ${result?.file_id || '-'}\n`
    + `프레임 · ${Number(result?.frame_count) || 0}개`
  );
}

export function motionStudioExportSelection(layers = []) {
  const selectedLayers = (layers || []).filter(
    (layer) => layer && layer.enabled !== false,
  );
  return {
    count: selectedLayers.length,
    layer: selectedLayers.length === 1 ? selectedLayers[0] : null,
  };
}

export function bindMotionStudioEvent(target, type, handler, options) {
  if (!target) return () => {};
  target.addEventListener(type, handler, options);
  return () => target.removeEventListener(type, handler, options);
}

export function bindMotionStudioProjectTransportEvents(el, handlers = {}) {
  const unbind = [];
  const bind = (target, type, handler) => {
    unbind.push(bindMotionStudioEvent(target, type, handler));
  };
  bind(el.studioRecordButton, 'click', () => handlers.onRecord?.({
    mode: 'record',
    initialMoveTimeSec: Number(el.studioInitialMoveTime?.value || 5),
  }));
  // 추가 녹화 · 기존 레이어가 쓰지 않는 축만 새 레이어로 녹화한다 · §6-71
  bind(el.studioOverdubButton, 'click', () => handlers.onRecord?.({
    mode: 'overdub',
    initialMoveTimeSec: Number(el.studioInitialMoveTime?.value || 5),
  }));
  bind(el.studioInitializeButton, 'click', () => handlers.onInitialize?.({
    initialMoveTimeSec: Number(el.studioInitialMoveTime?.value || 5),
  }));
  bind(el.studioPlayButton, 'click', () => handlers.onPlay?.({
    initialMoveTimeSec: Number(el.studioInitialMoveTime?.value || 5),
  }));
  bind(el.studioStopButton, 'click', () => {
    el.studioStopButton.disabled = true;
    handlers.onStop?.();
  });
  bind(el.studioCreateLayerButton, 'click', () => handlers.onCreateLayer?.());
  bind(el.studioExportButton, 'click', () => handlers.onExport?.(
    el.studioExportName?.value || handlers.defaultExportName?.() || 'motion',
  ));
  return () => unbind.forEach((remove) => remove());
}
