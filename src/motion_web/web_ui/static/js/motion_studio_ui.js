function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"]/g, (character) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;',
  }[character]));
}

export function createMotionStudioState() {
  return {
    mappings: [], motionFiles: [], project: null, workspaceProject: null,
    status: {}, midi: {}, composition: {
      conflicts: [], transition_warnings: [], point_curve_mismatches: [], conflict_free: true,
    }, busy: false,
    axisRenderKey: '', selectedLayerId: '', layerDetailMode: 'composition',
    activeLayerDetailTab: 'graph',
    editor: null, detailGraph: null, playbackGraphRenderedAt: 0,
    lastPlaybackDisplayState: 'idle',
    playbackClock: null, playbackAnimationFrame: 0,
    recordingPreviewKey: '',
    layerManagerTab: 'create', mergeLayerIds: new Set(),
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
    conflicts: [], transition_warnings: [], point_curve_mismatches: [], conflict_free: true,
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
  state.layerManagerTab = 'create';
  state.mergeLayerIds = new Set();
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

export function renderMotionStudioWorkspace(el, state) {
  if (el.studioWorkspaceName) {
    el.studioWorkspaceName.textContent = (
      state.workspaceProject?.name || '통합 프로젝트 미선택'
    );
  }
  if (el.studioWorkspaceFiles) {
    const active = state.workspaceProject?.active_files || {};
    el.studioWorkspaceFiles.textContent = state.workspaceProject
      ? `모터축: ${active.motor_axes || '미선택'} · 매칭: ${active.motion_axis_matching || '미선택'} · 모션: ${active.motions || '미선택'}`
      : '왼쪽에서 프로젝트와 현재 파일을 선택하세요';
  }
  if (el.studioImportFileSelect) {
    const selected = el.studioImportFileSelect.value;
    el.studioImportFileSelect.innerHTML = (
      '<option value="">가져올 모션 파일 선택</option>'
      + state.motionFiles.map((item) => (
        `<option value="${escapeHtml(item.file_id)}" ${item.valid ? '' : 'disabled'}>${escapeHtml(item.title || item.file_id)} · ${item.frame_count}프레임${item.valid ? '' : ' · 오류'}</option>`
      )).join('')
    );
    el.studioImportFileSelect.value = selected;
  }
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
  bind(el.studioTransitionSafetyLevel, 'change', (event) => {
    handlers.onTransitionSafetyChange?.(Number(event.currentTarget?.value || 4));
  });
  bind(el.studioImportFileSelect, 'change', () => handlers.onImportSelectionChange?.());
  bind(el.studioImportButton, 'click', () => {
    handlers.onImport?.(String(el.studioImportFileSelect?.value || ''));
  });
  bind(el.studioRecordButton, 'click', () => handlers.onRecord?.({
    mode: el.studioRecordMode?.value || 'record',
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
