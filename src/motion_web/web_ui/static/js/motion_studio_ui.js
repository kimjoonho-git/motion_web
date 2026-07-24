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
  state.composition = {
    conflicts: [], transition_warnings: [], point_curve_mismatches: [], conflict_free: true,
  };
  state.axisRenderKey = '';
  state.selectedLayerId = '';
  state.layerDetailMode = 'composition';
  state.activeLayerDetailTab = 'graph';
  state.detailGraph = null;
  state.playbackGraphRenderedAt = 0;
  state.lastPlaybackDisplayState = 'idle';
  state.mergeResultMessage = '';
  state.mergeResultError = false;
  return state;
}

export function setMotionStudioMessage(element, message, error = false) {
  if (!element) return;
  element.textContent = message || '';
  element.classList.toggle('error-text', error);
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
