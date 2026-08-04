import {
  commitMotionStudioMerge,
  createMotionStudioLayer,
  deleteMotionStudioLayer,
  duplicateMotionStudioLayer,
  editMotionStudioLayer,
  exportMotionStudio,
  fetchMotionStudio,
  importMotionStudioFile,
  previewMotionStudioMerge,
  saveMotionStudioLayerData,
  saveMotionStudioProject,
  startMotionStudioInitialization,
  startMotionStudioPlayback,
  startMotionStudioRecord,
  stopMotionStudio,
  updateMotionStudioLayer,
} from './api.js?v=20260722-motor-config-delete';
import {
  applyMotionStudioProjectPatch, motionStudioCanCreatePointCurve,
  motionStudioCanSwitchPointDraftCurve, motionStudioCanvasEventPoint,
  motionStudioEditorNextValueScale, motionStudioEditorGraphClickAction,
  motionStudioEditorValidationProject, motionStudioEditorValueBounds,
  motionStudioLayerDataEqual, motionStudioLayerDuration, motionStudioLayerMotionIds,
  motionStudioMergePreviewProject, motionStudioSetLayerEnabled, motionStudioMotionAxisRange,
  motionStudioValueViewAfterRangeUnlock, motionStudioMotionTargetAtTime,
  motionStudioNearestMotionTarget, motionStudioPointCurveAtTime,
  motionStudioPointCurveIsApplied, motionStudioPointCurveOrder,
  motionStudioPointCurvePreview, motionStudioPointCurveViewEnd,
  motionStudioCopyPointRange, motionStudioDeletePointRange, motionStudioPointDragStarted,
  motionStudioPointHitTarget, motionStudioPointRangePoints, motionStudioPointRangeReady,
  motionStudioPointRangeTargetsMatch, motionStudioRuntimeStatusMessage,
  motionStudioShouldProtectPointAxisSelection, motionStudioSnapFrameTime,
  resolveMotionStudioSelectedLayerId, synchronizeMotionStudioEditorTimeline,
} from './motion_studio_calculations.js?v=20260803-studio-structure-12';
import {
  drawMotionStudioLayerGraph,
  motionStudioCompositionTracks as compositionTracks,
  motionStudioLayerTracks as layerTracks,
} from './motion_studio_graph.js?v=20260803-studio-structure-12';
import {
  bindMotionStudioEvent,
  bindMotionStudioProjectTransportEvents,
  createMotionStudioState,
  renderMotionStudioWorkspace,
  motionStudioExportSelection,
  motionStudioExportResultMessage,
  resetMotionStudioProjectState,
  setMotionStudioMessage,
} from './motion_studio_ui.js?v=20260729-editor-workflow-export-1';
import {
  motionStudioEditorAxisLabel,
} from './motion_studio_editor_ui.js?v=20260804-point-actions-range-1';
import {
  createMotionStudioPlaybackController,
} from './motion_studio_playback.js?v=20260803-studio-structure-4';
import {
  renderMotionStudioLayerManager,
} from './motion_studio_layer_manager.js?v=20260803-studio-structure-4';
import {
  MOTION_STUDIO_PERIOD_MS,
} from './motion_studio_constants.js?v=20260803-studio-structure-4';
import {
  createMotionStudioLayerController, closeMotionStudioLayerManager, openMotionStudioLayerManager,
  selectMotionStudioLayer,
  updateMotionStudioMergeSelection,
} from './motion_studio_layer_controller.js?v=20260803-studio-structure-10';
import {
  createMotionStudioEditorController,
} from './motion_studio_editor_controller.js?v=20260804-point-actions-range-1';
import { motionStudioEditorPointCurves } from './motion_studio_editor_state.js?v=20260804-multi-axis-range-1';
import {
  createMotionStudioRequestFence,
} from './motion_studio_controller_events.js?v=20260803-studio-structure-10';
import { showAlert, showConfirm } from './ui_dialogs.js?v=20260727-popup-common-3';
export {
  applyMotionStudioProjectPatch, motionStudioCanCreatePointCurve,
  motionStudioCanSwitchPointDraftCurve, motionStudioCanvasEventPoint,
  motionStudioEditorNextValueScale, motionStudioEditorGraphClickAction,
  motionStudioEditorValidationProject, motionStudioEditorValueBounds,
  motionStudioLayerDataEqual, motionStudioLayerDuration, motionStudioLayerMotionIds,
  motionStudioMergePreviewProject, motionStudioSetLayerEnabled, motionStudioMotionAxisRange,
  motionStudioValueViewAfterRangeUnlock, motionStudioMotionTargetAtTime,
  motionStudioNearestMotionTarget, motionStudioPointCurveAtTime,
  motionStudioPointCurveIsApplied, motionStudioPointCurveOrder,
  motionStudioPointCurvePreview, motionStudioPointCurveViewEnd,
  motionStudioCopyPointRange, motionStudioDeletePointRange, motionStudioPointDragStarted,
  motionStudioPointHitTarget, motionStudioPointRangePoints, motionStudioPointRangeReady,
  motionStudioPointRangeTargetsMatch, motionStudioRuntimeStatusMessage,
  motionStudioShouldProtectPointAxisSelection, motionStudioSnapFrameTime,
  resolveMotionStudioSelectedLayerId, synchronizeMotionStudioEditorTimeline,
};
function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"]/g, (character) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;',
  }[character]));
}

function timeText(seconds) {
  const value = Math.max(0, Number(seconds) || 0);
  const minutes = Math.floor(value / 60);
  const remain = value - (minutes * 60);
  return `${String(minutes).padStart(2, '0')}:${remain.toFixed(3).padStart(6, '0')}`;
}

export function createMotionStudioController({
  el,
  getMotorActionBlockReason = () => '',
  getConfiguredMotors = () => [],
  onMotionFilesChange = async () => {},
}) {
  const state = createMotionStudioState();
  const clone = structuredClone;
  const layerMetricsCache = new WeakMap();
  const layerTracksCache = new WeakMap();
  const layerPointCoverageCache = new WeakMap();
  let compositionViewCache = null;
  let motorCommandRevision = 0;
  let pendingMotorStartAt = 0;
  let eventsBound = false;
  const projectRequestFence = createMotionStudioRequestFence();
  let refreshRevision = 0;

  function layerMetrics(layer) {
    if (!layer || typeof layer !== 'object') {
      return { frameCount: 0, duration: 0, motionIds: [] };
    }
    const frames = Array.isArray(layer.frames) ? layer.frames : [];
    const cached = layerMetricsCache.get(layer);
    if (cached?.frames === frames) return cached.value;
    const motionIds = new Set();
    let duration = 0;
    for (const frame of frames) {
      duration = Math.max(duration, Number(frame?.time_sec) || 0);
      for (const motionId of Object.keys(frame?.values || {})) {
        motionIds.add(motionId);
      }
    }
    const value = {
      frameCount: frames.length,
      duration,
      motionIds: [...motionIds],
    };
    layerMetricsCache.set(layer, { frames, value });
    return value;
  }

  function cachedLayerTracks(layer) {
    if (!layer || typeof layer !== 'object') return new Map();
    const frames = Array.isArray(layer.frames) ? layer.frames : [];
    const cached = layerTracksCache.get(layer);
    if (cached?.frames === frames) return cached.value;
    const value = layerTracks(layer);
    layerTracksCache.set(layer, { frames, value });
    return value;
  }

  function cachedCompositionTracks(layers, mappingRows) {
    const frames = layers.map((layer) => layer?.frames);
    const enabled = layers.map((layer) => layer?.enabled !== false);
    if (
      compositionViewCache
      && compositionViewCache.mappingRows === mappingRows
      && compositionViewCache.frames.length === frames.length
      && frames.every((value, index) => (
        value === compositionViewCache.frames[index]
        && enabled[index] === compositionViewCache.enabled[index]
      ))
    ) return compositionViewCache.value;
    const value = compositionTracks(layers, mappingRows);
    compositionViewCache = {
      mappingRows,
      frames,
      enabled,
      value,
    };
    return value;
  }

  function setProject(project) {
    state.project = project || null;
    state.selectedLayerId = resolveMotionStudioSelectedLayerId(
      state.project?.layers || [], state.selectedLayerId,
    );
  }

  function selectedMidi() {
    const result = new Map();
    for (const channel of state.midi?.channels || []) {
      if ((channel?.select_enabled ?? channel?.control_enabled) && channel.motion_id) {
        result.set(String(channel.motion_id), channel);
      }
    }
    return result;
  }

  function activeMapping() {
    const fileId = state.project?.mapping_file_id;
    return state.mappings.find((mapping) => mapping.file_id === fileId) || null;
  }

  function configuredMotors() {
    try {
      const motors = getConfiguredMotors?.();
      return Array.isArray(motors) ? motors : [];
    } catch (_error) {
      return [];
    }
  }

  function editorAxisLabel(motionId) {
    return motionStudioEditorAxisLabel(
      motionId,
      activeMapping()?.rows || [],
      configuredMotors(),
    );
  }

  function setMessage(message, error = false) {
    setMotionStudioMessage(el.studioMessage, message, error);
  }

  function setBusy(busy) {
    state.busy = busy;
    renderControls();
  }

  function motorActionBlockReason() {
    try {
      return String(getMotorActionBlockReason?.() || '');
    } catch (_error) {
      return '모터 동작 가능 상태를 확인하지 못했습니다.';
    }
  }

  function requireMotorActionReady(actionName) {
    const reason = motorActionBlockReason();
    if (!reason) return true;
    window.alert(`${actionName}은 모터가 동작하는 기능입니다.\n\n${reason}`);
    return false;
  }

  function renderLists() {
    renderMotionStudioWorkspace(el, state);
  }

  function renderMapping() {
    const mapping = activeMapping();
    if (!el.studioMappingInfo) return;
    if (!mapping) {
      el.studioMappingInfo.textContent = '현재 프로젝트의 모션축 설정 파일을 선택하세요';
      return;
    }
    const checksum = String(mapping.sha256 || '').slice(0, 12);
    el.studioMappingInfo.textContent = `읽기 전용 · 모션 ID ${mapping.motion_ids.length}개 · SHA-256 ${checksum}…`;
  }

  function renderAxes() {
    if (!el.studioAxisRows) return;
    const mapping = activeMapping();
    if (!mapping || !state.project) {
      if (state.axisRenderKey !== 'empty') {
        el.studioAxisRows.innerHTML = '<tr><td colspan="4" class="empty">왼쪽에서 프로젝트를 선택하세요</td></tr>';
        state.axisRenderKey = 'empty';
      }
      return;
    }
    const selected = selectedMidi();
    const selectLocked = Boolean(state.midi?.select_locked);
    const renderKey = JSON.stringify([
      state.project.project_id,
      mapping.file_id,
      mapping.rows.map((row) => [row.motion_id, row.motor_ref, row.motor_axis]),
    ]);
    if (state.axisRenderKey !== renderKey) {
      el.studioAxisRows.innerHTML = mapping.rows.map((row) => (
        `<tr data-studio-motion-id="${escapeHtml(row.motion_id)}">
          <td>${escapeHtml(row.motion_id)}</td><td>${escapeHtml(row.motor_ref || `이전 형식 축 ${row.motor_axis ?? '-'}`)}</td>
          <td><span class="status-chip off" data-studio-select-state>미선택</span></td>
          <td data-studio-motion-value>-</td></tr>`
      )).join('');
      state.axisRenderKey = renderKey;
    }
    const rows = new Map(
      [...el.studioAxisRows.querySelectorAll('tr[data-studio-motion-id]')]
        .map((row) => [row.dataset.studioMotionId, row]),
    );
    for (const mappingRow of mapping.rows) {
      const motionId = String(mappingRow.motion_id);
      const row = rows.get(motionId);
      if (!row) continue;
      const channel = selected.get(motionId);
      const selectState = row.querySelector('[data-studio-select-state]');
      if (selectState) {
        selectState.className = `status-chip ${selectLocked ? 'warn' : channel ? 'on' : 'off'}`;
        selectState.textContent = selectLocked ? '초기화 잠금' : channel ? '선택됨' : '미선택';
      }
      const value = Number(channel?.motion_value_deg);
      const valueCell = row.querySelector('[data-studio-motion-value]');
      if (valueCell) valueCell.textContent = Number.isFinite(value) ? `${value.toFixed(3)}°` : '-';
    }
  }

  function renderLayers() {
    if (!el.studioLayerRows) return;
    const layers = state.project?.layers || [];
    if (!layers.length) {
      state.selectedLayerId = '';
      state.layerDetailMode = 'composition';
      el.studioLayerRows.innerHTML = '<tr><td colspan="3" class="empty">녹화 레이어가 없습니다</td></tr>';
      return;
    }
    if (state.selectedLayerId
      && !layers.some((layer) => layer.layer_id === state.selectedLayerId)) {
      state.layerDetailMode = 'composition';
      state.selectedLayerId = '';
    }
    const conflictLayers = new Set((state.composition?.conflicts || []).flatMap((item) => (
      [item.first_layer_id, item.second_layer_id]
    )));
    const transitionLayers = new Set((state.composition?.transition_warnings || []).flatMap((item) => (
      [item.first_layer_id, item.second_layer_id]
    )));
    el.studioLayerRows.innerHTML = layers.map((layer) => {
      const metrics = layerMetrics(layer);
      const selected = layer.layer_id === state.selectedLayerId;
      return `<tr class="${selected ? 'selected-row' : ''}" data-studio-layer-id="${escapeHtml(layer.layer_id)}">
        <td><label class="studio-playback-choice"><input type="checkbox" data-layer-enabled ${layer.enabled !== false ? 'checked' : ''} ${layer.locked ? 'disabled' : ''}><span>${layer.enabled !== false ? '선택' : '제외'}</span></label></td>
        <td><input class="studio-layer-main-name" type="text" data-layer-main-name maxlength="40" value="${escapeHtml(layer.name)}" ${layer.locked ? 'disabled' : ''} aria-label="레이어 이름"></td>
        <td><div class="studio-layer-info-cell">
          <span>${metrics.frameCount}프레임</span><span>${metrics.duration.toFixed(3)}초</span><span>${metrics.motionIds.length}축</span>
          ${layer.locked ? '<span class="status-chip off">잠금</span>' : ''}
          ${conflictLayers.has(layer.layer_id) ? '<span class="status-chip warn">충돌</span>' : ''}
          ${transitionLayers.has(layer.layer_id) ? '<span class="status-chip warn">급변</span>' : ''}
        </div></td></tr>`;
    }).join('');
  }

  function layerSummary(layer) {
    const metrics = layerMetrics(layer);
    return `${metrics.frameCount}프레임 · ${metrics.duration.toFixed(3)}초 · ${metrics.motionIds.length}축`;
  }

  function layerPointCoverageIssues(layer) {
    if (!layer || typeof layer !== 'object') return ['모션 데이터 없음'];
    const frames = Array.isArray(layer.frames) ? layer.frames : [];
    const pointCurves = motionStudioEditorPointCurves(layer);
    const cached = layerPointCoverageCache.get(layer);
    if (cached?.frames === frames && cached?.pointCurves === pointCurves) {
      return cached.value;
    }
    const tracks = cachedLayerTracks(layer);
    if (!tracks.size) {
      const value = ['모션 데이터 없음'];
      layerPointCoverageCache.set(layer, { frames, pointCurves, value });
      return value;
    }
    const value = [...tracks.entries()].filter(([motionId, samples]) => {
      const bounds = pointCurves.filter((curve) => curve.motion_id === motionId).map((curve) => {
        const points = curve.points || [];
        return [
          Number(points[0]?.time_sec),
          Number(points[points.length - 1]?.time_sec),
        ];
      }).filter(([start, end]) => Number.isFinite(start) && Number.isFinite(end));
      return !bounds.length || samples.some((sample) => !bounds.some(
        ([start, end]) => sample.timeSec >= start - 1e-9 && sample.timeSec <= end + 1e-9,
      ));
    }).map(([motionId]) => motionId);
    layerPointCoverageCache.set(layer, { frames, pointCurves, value });
    return value;
  }

  function editorValidationProject(layer, extraMotionIds = []) {
    return motionStudioEditorValidationProject(
      state.project, layer, extraMotionIds,
    );
  }

  function mergePreviewProject(layerIds) {
    return motionStudioMergePreviewProject(state.project, layerIds);
  }

  function renderLayerManager() {
    renderMotionStudioLayerManager({
      state,
      el,
      escapeHtml,
      layerSummary,
      layerPointCoverageIssues,
    });
  }

  const playbackController = createMotionStudioPlaybackController({
    state,
    el,
    timeText,
    now: () => performance.now(),
    requestFrame: (callback) => window.requestAnimationFrame(callback),
    cancelFrame: (frameId) => window.cancelAnimationFrame(frameId),
  });
  const playbackView = playbackController.view;
  const syncPlaybackClock = playbackController.syncClock;
  const updatePlaybackPlayhead = playbackController.updatePlayhead;
  const animatePlaybackGraph = playbackController.animate;
  const renderPlaybackMonitor = playbackController.renderMonitor;

  function showLayerGraph({ composition = false } = {}) {
    if (composition) {
      state.layerDetailMode = 'composition';
    }
    state.activeLayerDetailTab = 'graph';
    renderLayerDetail();
    window.requestAnimationFrame(() => {
      el.studioLayerDetail?.scrollIntoView({ behavior: 'smooth', block: 'start' });
      el.studioLayerDetail?.classList.add('attention');
      window.setTimeout(() => el.studioLayerDetail?.classList.remove('attention'), 1200);
    });
  }

  function drawLayerGraph(tracks, warnings = [], playback = playbackView()) {
    drawMotionStudioLayerGraph({
      canvas: el.studioLayerGraph,
      playhead: el.studioLayerPlayhead,
      tracks,
      warnings,
      playback,
      updatePlayhead: updatePlaybackPlayhead,
      devicePixelRatio: window.devicePixelRatio || 1,
    });
  }

  const editorController = createMotionStudioEditorController({
    state,
    el,
    clone,
    escapeHtml,
    activeMapping,
    configuredMotors,
    editorAxisLabel,
    layerPointCoverageIssues,
    editorValidationProject,
    cachedLayerTracks,
    run,
  });
  const {
    openLayerEditor,
    closeLayerEditor,
    renderEditor,
  } = editorController;
  const openLayerManager = () => openMotionStudioLayerManager(el, renderLayerManager);
  const closeLayerManager = () => closeMotionStudioLayerManager(el);

  function nextMergedLayerName() {
    const names = new Set((state.project?.layers || []).map((layer) => String(layer.name || '').trim()));
    let sequence = 1;
    while (names.has(`합친 레이어 ${sequence}`)) sequence += 1;
    return `합친 레이어 ${sequence}`;
  }

  function renderMergeControl() {
    const count = state.mergeLayerIds.size;
    const appendMode = state.mergeMode === 'append';
    const appendLayerReady = state.mergeLayerIds.has(state.mergeAppendLayerId);
    if (el.studioMergeButton) {
      el.studioMergeButton.disabled = (
        state.busy || count < 2 || (appendMode && !appendLayerReady)
      );
      el.studioMergeButton.textContent = appendMode
        ? '뒤에 이어 붙이기'
        : '레이어 합치기';
    }
    if (el.studioMergeStatus) {
      el.studioMergeStatus.textContent = state.mergeResultMessage || (
        count < 2
          ? '합칠 레이어를 2개 이상 선택하세요'
          : appendMode && !appendLayerReady
            ? '뒤로 이동할 레이어를 선택하세요'
            : appendMode
              ? `합칠 레이어 ${count}개 · 지정한 레이어 전체를 나머지 레이어 뒤로 이동합니다`
              : `합칠 레이어 ${count}개 · 현재 시간 위치로 충돌 검사를 진행합니다`
      );
      el.studioMergeStatus.classList.toggle('error', Boolean(state.mergeResultError));
      el.studioMergeStatus.classList.toggle(
        'success', Boolean(state.mergeResultMessage) && !state.mergeResultError,
      );
    }
  }

  function selectedLayer() {
    return state.project?.layers?.find((layer) => layer.layer_id === state.selectedLayerId) || null;
  }

  function renderSelectedLayerActions() {
    const layer = selectedLayer();
    const runtimeState = String(state.status?.state || 'idle');
    const running = (
      ['initializing', 'recording', 'playing', 'stopping'].includes(runtimeState)
      || pendingMotorStartAt > 0
    );
    const blocked = state.busy || running;
    const unavailableReason = !layer
      ? '선택할 레이어가 없습니다'
      : state.busy
        ? '요청 처리 중입니다'
        : running
          ? '초기 이동·녹화·재생·정지 처리 중에는 변경할 수 없습니다'
          : layer.locked
            ? '잠금을 해제한 뒤 사용할 수 있습니다'
            : '';
    if (el.studioSelectedLayerDetailButton) {
      el.studioSelectedLayerDetailButton.disabled = !layer;
    }
    if (el.studioSelectedLayerCopyButton) {
      el.studioSelectedLayerCopyButton.disabled = blocked || !layer;
      el.studioSelectedLayerCopyButton.textContent = state.busy
        ? '레이어 복사 처리 중…' : '선택 레이어 복사';
      el.studioSelectedLayerCopyButton.title = unavailableReason;
    }
    if (el.studioSelectedLayerEditButton) {
      el.studioSelectedLayerEditButton.disabled = blocked || !layer || Boolean(layer?.locked);
      el.studioSelectedLayerEditButton.title = unavailableReason;
    }
    if (el.studioSelectedLayerLockButton) {
      el.studioSelectedLayerLockButton.disabled = blocked || !layer;
      el.studioSelectedLayerLockButton.textContent = layer?.locked ? '잠금 해제' : '잠금';
      el.studioSelectedLayerLockButton.title = layer?.locked
        ? '이름·재생 선택 상태·편집·삭제를 다시 허용합니다'
        : (unavailableReason || '이름·재생 선택 상태·편집·삭제를 막아 레이어를 보호합니다');
    }
    if (el.studioSelectedLayerDeleteButton) {
      el.studioSelectedLayerDeleteButton.disabled = blocked || !layer || Boolean(layer?.locked);
      el.studioSelectedLayerDeleteButton.title = unavailableReason;
    }
  }

  function renderLayerDetail() {
    const layers = state.project?.layers || [];
    const layer = state.layerDetailMode === 'layer'
      ? layers.find((item) => item.layer_id === state.selectedLayerId) || null
      : null;
    if (state.layerDetailMode === 'layer' && !layer) {
      state.layerDetailMode = 'composition';
      state.selectedLayerId = '';
    }
    const mappingRows = activeMapping()?.rows || [];
    const composition = cachedCompositionTracks(layers, mappingRows);
    const compositionMode = state.layerDetailMode !== 'layer';
    const frames = layer?.frames || [];
    const tracks = compositionMode ? composition.tracks : cachedLayerTracks(layer);
    const duration = compositionMode
      ? composition.duration
      : (frames.length ? Number(frames[frames.length - 1].time_sec || 0) : 0);
    const frameCount = compositionMode ? composition.sampleCount : frames.length;
    const layerWarnings = compositionMode
      ? (state.composition?.transition_warnings || [])
      : (state.composition?.transition_warnings || []).filter((warning) => (
        warning.first_layer_id === layer?.layer_id || warning.second_layer_id === layer?.layer_id
      ));
    const conflicts = state.composition?.conflicts || [];
    state.detailGraph = {
      tracks,
      warnings: layerWarnings,
      duration,
      enabledLayerCount: composition.enabledLayers.length,
      compositionMode,
    };
    if (el.studioLayerDetailName) {
      el.studioLayerDetailName.textContent = compositionMode
        ? '재생 선택 레이어 합성 결과'
        : (layer?.name || '이름 없음');
    }
    if (el.studioLayerDetailStatus) {
      el.studioLayerDetailStatus.textContent = !layers.length
        ? '레이어를 추가하면 정보가 표시됩니다'
        : compositionMode
          ? `재생 선택 ${composition.enabledLayers.length}개 / 전체 ${layers.length}개 · ${
            !composition.enabledLayers.length
              ? '재생 선택 레이어 없음'
              : (conflicts.length || layerWarnings.length ? '검증 필요' : '합성 가능')
          }`
          : `${layer.enabled !== false ? '재생 선택' : '재생 미선택'} · ${layer.locked ? '잠금' : '잠금 안 함'}`;
    }
    if (el.studioLayerDetailFrames) el.studioLayerDetailFrames.textContent = `${frameCount}개`;
    if (el.studioLayerDetailDuration) el.studioLayerDetailDuration.textContent = `${duration.toFixed(3)}초`;
    if (el.studioLayerDetailAxisCount) el.studioLayerDetailAxisCount.textContent = `${tracks.size}개`;
    if (el.studioCompositionDetailButton) {
      el.studioCompositionDetailButton.disabled = !layers.length || compositionMode;
      el.studioCompositionDetailButton.classList.toggle('primary', compositionMode);
    }
    el.studioLayerDetailTabs?.querySelectorAll('[data-studio-layer-detail-tab]').forEach((button) => {
      const active = button.dataset.studioLayerDetailTab === state.activeLayerDetailTab;
      button.classList.toggle('active', active);
      button.setAttribute('aria-selected', active ? 'true' : 'false');
    });
    el.studioLayerDetail?.querySelectorAll('[data-studio-layer-detail-panel]').forEach((panel) => {
      panel.classList.toggle(
        'hidden', panel.dataset.studioLayerDetailPanel !== state.activeLayerDetailTab,
      );
    });
    const colors = ['#1f6feb', '#d97706', '#16803c', '#a23ab7', '#d33b3b', '#0f8b8d'];
    if (el.studioLayerGraphLegend) {
      el.studioLayerGraphLegend.innerHTML = [...tracks.keys()].map((motionId, index) => (
        `<span><i style="background:${colors[index % colors.length]}"></i>${escapeHtml(motionId)}</span>`
      )).join('') + (layerWarnings.length
        ? `<span class="danger"><i></i>급변 경고 ${layerWarnings.length}건</span>` : '');
    }
    if (el.studioLayerAxisDetailRows) {
      el.studioLayerAxisDetailRows.innerHTML = tracks.size
        ? [...tracks.entries()].map(([motionId, points]) => {
          const first = points[0];
          const last = points[points.length - 1];
          const values = points.map((point) => point.value);
          return `<tr><td>${escapeHtml(motionId)}</td><td>${first.timeSec.toFixed(3)}초</td>`
            + `<td>${first.value.toFixed(3)}°</td><td>${last.timeSec.toFixed(3)}초</td>`
            + `<td>${last.value.toFixed(3)}°</td><td>${Math.min(...values).toFixed(3)}°</td>`
            + `<td>${Math.max(...values).toFixed(3)}°</td><td>${points.length}</td></tr>`;
        }).join('')
        : '<tr><td colspan="8" class="empty">기록된 Motion ID가 없습니다</td></tr>';
    }
    if (el.studioLayerInfoRows) {
      const infoRows = compositionMode ? [
        ['보기 대상', '재생 선택 레이어 합성 결과'],
        ['재생 선택', `${composition.enabledLayers.length}개`],
        ['전체 레이어', `${layers.length}개`],
        ['시간 충돌', `${conflicts.length}건`],
        ['급변 경고', `${layerWarnings.length}건`],
        ['재생 가능', conflicts.length || layerWarnings.length
          ? '불가'
          : (composition.enabledLayers.length ? '가능' : '데이터 없음')],
      ] : [
        ['보기 대상', '개별 레이어'],
        ['레이어 이름', layer?.name || '-'],
        ['재생 상태', layer?.enabled !== false ? '선택' : '미선택'],
        ['잠금 상태', layer?.locked ? '잠금' : '잠금 안 함'],
        ['원본 모션 파일', layer?.source_motion_file_id || '-'],
        ['급변 경고', `${layerWarnings.length}건`],
      ];
      el.studioLayerInfoRows.innerHTML = infoRows.map(([label, value]) => (
        `<div><dt>${escapeHtml(label)}</dt><dd>${escapeHtml(value)}</dd></div>`
      )).join('');
    }
    const playback = renderPlaybackMonitor(duration);
    drawLayerGraph(tracks, layerWarnings, playback);
    state.lastPlaybackDisplayState = playback.displayState;
  }

  function renderRecordingPreview() {
    if (String(state.status?.state || '') !== 'recording') return false;
    const frames = Array.isArray(state.status?.recording_preview_frames)
      ? state.status.recording_preview_frames : [];
    const duration = Math.max(
      Number(state.status?.elapsed_sec) || 0,
      ...frames.map((frame) => Number(frame.time_sec) || 0),
    );
    const previewKey = `${Number(state.status?.recorded_frames || 0)}:${Number(state.status?.recording_preview_stride || 1)}`;
    if (state.recordingPreviewKey === previewKey && state.detailGraph?.recordingPreview) {
      renderPlaybackMonitor(duration);
      return true;
    }
    state.recordingPreviewKey = previewKey;
    const tracks = layerTracks({ frames });
    state.layerDetailMode = 'composition';
    state.selectedLayerId = '';
    state.activeLayerDetailTab = 'graph';
    state.detailGraph = {
      tracks,
      warnings: [],
      duration,
      enabledLayerCount: Number(state.status?.playback_layer_count || 0),
      compositionMode: true,
      recordingPreview: true,
    };
    if (el.studioLayerDetailName) el.studioLayerDetailName.textContent = '녹화 중 실시간 그래프';
    if (el.studioLayerDetailStatus) {
      const stride = Math.max(1, Number(state.status?.recording_preview_stride) || 1);
      el.studioLayerDetailStatus.textContent = tracks.size
        ? `기록 중 · 화면 표시 간격 ${Math.round(stride * MOTION_STUDIO_PERIOD_MS)}ms · 원본은 20ms로 기록`
        : '기록 중 · MIDI SELECT 후 움직인 축이 그래프에 표시됩니다';
    }
    if (el.studioLayerDetailFrames) {
      el.studioLayerDetailFrames.textContent = `${Number(state.status?.recorded_frames || 0)}개`;
    }
    if (el.studioLayerDetailDuration) {
      el.studioLayerDetailDuration.textContent = `${duration.toFixed(3)}초`;
    }
    if (el.studioLayerDetailAxisCount) el.studioLayerDetailAxisCount.textContent = `${tracks.size}개`;
    el.studioLayerDetailTabs?.querySelectorAll('[data-studio-layer-detail-tab]').forEach((button) => {
      const active = button.dataset.studioLayerDetailTab === 'graph';
      button.classList.toggle('active', active);
      button.setAttribute('aria-selected', active ? 'true' : 'false');
    });
    el.studioLayerDetail?.querySelectorAll('[data-studio-layer-detail-panel]').forEach((panel) => {
      panel.classList.toggle('hidden', panel.dataset.studioLayerDetailPanel !== 'graph');
    });
    if (el.studioLayerGraphLegend) {
      const colors = ['#1f6feb', '#d97706', '#16803c', '#a23ab7', '#d33b3b', '#0f8b8d'];
      el.studioLayerGraphLegend.innerHTML = [...tracks.keys()].map((motionId, index) => (
        `<span><i style="background:${colors[index % colors.length]}"></i>${escapeHtml(motionId)}</span>`
      )).join('');
    }
    const view = renderPlaybackMonitor(duration);
    drawLayerGraph(tracks, [], view);
    return true;
  }

  function renderConflicts() {
    if (!el.studioConflictInfo) return;
    const conflicts = state.composition?.conflicts || [];
    const transitions = state.composition?.transition_warnings || [];
    const curveMismatches = state.composition?.point_curve_mismatches || [];
    const issues = conflicts.map((item) => (
      `${item.motion_id}: ${item.first_layer_name}/${item.second_layer_name} 시간 충돌`
    ));
    issues.push(...transitions.map((item) => (
      `${item.motion_id}: ${({
        manual_initial: '초기값 급변',
        late_start: '늦은 시작 급변',
        frame_step: '프레임 급변',
        gap_to_zero: '빈 구간 진입 급변',
        gap_from_zero: '빈 구간 이탈 급변',
        segment_transition: '레이어 전환 급변',
        early_end: '종료 급변',
      })[item.kind] || '모션값 급변'} ${Number(item.jump_deg).toFixed(2)}°`
      + `(허용 ${Number(item.limit_deg).toFixed(2)}°)`
    )));
    issues.push(...curveMismatches.map((item) => (
      `${item.layer_name}/${item.motion_id}: 포인트 곡선과 20ms 프레임 불일치`
    )));
    const remaining = Math.max(0, issues.length - 3);
    const summary = issues.slice(0, 3).join(' · ');
    el.studioConflictInfo.classList.toggle('hidden', issues.length === 0);
    if (!issues.length) {
      el.studioConflictInfo.textContent = '';
      return;
    }
    const firstMismatch = curveMismatches[0];
    el.studioConflictInfo.innerHTML = `<div><strong>경고</strong> · ${escapeHtml(summary)}${
      remaining ? ` · 외 ${remaining}건` : ''
    }</div>${firstMismatch ? `<div class="registry-actions studio-consistency-actions">
      <span>${escapeHtml(firstMismatch.layer_name)} · 포인트 기준으로 다시 계산해야 재생할 수 있습니다.</span>
      <button type="button" data-resolve-point-curve data-layer-id="${escapeHtml(firstMismatch.layer_id)}">포인트 기준으로 다시 계산</button>
    </div>` : ''}`;
  }

  async function resolvePointCurveMismatch(layerId) {
    const layer = state.project?.layers?.find((item) => item.layer_id === layerId);
    const issues = (state.composition?.point_curve_mismatches || []).filter(
      (item) => item.layer_id === layerId,
    );
    if (!layer || !issues.length) return null;
    if (layer.locked) {
      setMessage(`'${layer.name}' 레이어 잠금을 먼저 해제하세요.`, true);
      return null;
    }
    if (!await showConfirm(
      `'${layer.name}'의 포인트·탄젠트를 기준으로 20ms 프레임을 다시 계산할까요?`,
      {
      title: '포인트 곡선 불일치 정리',
      confirmLabel: '다시 계산',
      tone: 'warning',
      },
    )) return null;
    return run(async () => {
      const calculated = await editMotionStudioLayer({
        layer,
        project: editorValidationProject(layer),
        operation: 'resolve_point_curve_consistency',
        strategy: 'points',
        curve_ids: [...new Set(issues.map((item) => item.curve_id))],
        mapping_rows: activeMapping()?.rows || [],
      });
      if (calculated.success === false) {
        throw new Error(calculated.message || '포인트 곡선 불일치 정리 실패');
      }
      const saved = await saveMotionStudioLayerData({
        layer_id: layerId,
        original_revision: Number(layer.edit_revision || 0),
        layer: calculated.layer,
      });
      if (saved.success === false) throw new Error(saved.message || '레이어 저장 실패');
      return saved;
    });
  }

  function renderControls() {
    const runtimeState = String(state.status?.state || 'idle');
    const running = ['initializing', 'recording', 'playing', 'stopping'].includes(runtimeState);
    const hasProject = Boolean(state.project);
    const hasMotionAxes = Boolean(activeMapping()?.rows?.length);
    const hasEnabledLayer = Boolean(
      state.project?.layers?.some((layer) => layer.enabled !== false),
    );
    const hasConflicts = Boolean(state.composition?.conflicts?.length);
    const hasTransitionWarnings = Boolean(state.composition?.transition_warnings?.length);
    const hasCurveMismatches = Boolean(state.composition?.point_curve_mismatches?.length);
    const hasCompositionErrors = hasConflicts || hasTransitionWarnings || hasCurveMismatches;
    const exportSelection = motionStudioExportSelection(state.project?.layers);
    const enabledLayerCount = exportSelection.count;
    const hasSingleExportLayer = enabledLayerCount === 1;
    const motorBlockReason = motorActionBlockReason();
    if (el.studioState) el.studioState.textContent = state.status?.message || '대기';
    if (el.studioElapsed) el.studioElapsed.textContent = timeText(state.status?.elapsed_sec);
    if (el.studioFrameCount) el.studioFrameCount.textContent = `${state.status?.recorded_frames || 0}프레임 · 20ms`;
    if (el.studioTransitionSafetyLevel && state.project) {
      const level = Number(state.project.transition_safety_level || 4);
      el.studioTransitionSafetyLevel.value = String(level);
      el.studioTransitionSafetyLevel.disabled = state.busy || running || !hasProject;
      if (el.studioTransitionRuleInfo) {
        el.studioTransitionRuleInfo.textContent = `${level}단계: ${level}° 또는 축 모션 범위의 ${level}% 중 큰 값을 허용`;
      }
    } else if (el.studioTransitionSafetyLevel) {
      el.studioTransitionSafetyLevel.disabled = true;
    }
    if (el.studioImportButton) {
      el.studioImportButton.disabled = (
        state.busy
        || running
        || !hasProject
        || !el.studioImportFileSelect?.value
      );
    }
    if (el.studioRecordButton) {
      el.studioRecordButton.disabled = state.busy || running || !hasProject || !hasMotionAxes || Boolean(motorBlockReason);
      el.studioRecordButton.title = motorBlockReason || '';
    }
    if (el.studioInitializeButton) {
      el.studioInitializeButton.disabled = state.busy || running || !hasProject || !hasMotionAxes || hasConflicts || hasCurveMismatches || Boolean(motorBlockReason);
      el.studioInitializeButton.title = motorBlockReason
        || (hasCurveMismatches ? '포인트 곡선과 20ms 프레임 불일치를 먼저 정리하세요' : '')
        || (hasConflicts ? '같은 Motion ID의 레이어 충돌을 해결하세요' : '');
    }
    if (el.studioPlayButton) {
      el.studioPlayButton.disabled = state.busy || running || !hasEnabledLayer || hasCompositionErrors || Boolean(motorBlockReason);
      el.studioPlayButton.title = motorBlockReason
        || (hasCurveMismatches ? '포인트 곡선과 20ms 프레임 불일치를 먼저 정리하세요' : '')
        || (hasCompositionErrors ? '레이어 충돌 또는 모션값 급변을 해결하세요' : '');
    }
    // 정지는 다른 스튜디오 요청 처리 중에도 항상 우선 입력할 수 있어야 한다.
    if (el.studioStopButton) {
      el.studioStopButton.disabled = pendingMotorStartAt > 0
        ? false
        : (!running || runtimeState === 'stopping');
    }
    if (el.studioExportButton) {
      el.studioExportButton.disabled = state.busy || running || !hasSingleExportLayer || hasCompositionErrors;
      el.studioExportButton.title = !hasSingleExportLayer
        ? '최종 모션 파일은 재생 선택 레이어가 정확히 1개일 때만 저장할 수 있습니다'
        : hasCurveMismatches
        ? '포인트 곡선과 20ms 프레임 불일치를 먼저 정리하세요'
        : (hasCompositionErrors ? '레이어 충돌 또는 모션값 급변을 해결한 뒤 내보낼 수 있습니다' : '');
    }
    if (el.studioExportTarget) {
      const exportLayer = exportSelection.layer;
      el.studioExportTarget.dataset.state = exportLayer
        ? 'ready'
        : enabledLayerCount > 1 ? 'blocked' : 'empty';
      el.studioExportTarget.textContent = exportLayer
        ? `내보내기 대상 · ${exportLayer.name} · ${layerSummary(exportLayer)}`
        : enabledLayerCount > 1
          ? `내보내기 불가 · 재생 선택 ${enabledLayerCount}개 · 1개만 체크하세요`
          : '내보내기 대상 없음 · 재생 선택 레이어를 1개만 체크하세요';
    }
    if (el.studioCreateLayerButton) {
      el.studioCreateLayerButton.disabled = state.busy || running || !hasProject;
      el.studioCreateLayerButton.textContent = state.busy
        ? '레이어 생성 처리 중…' : '새 레이어 생성';
    }
    if (el.studioLayerManagerOpenButton) {
      el.studioLayerManagerOpenButton.disabled = state.busy || running || !hasProject;
    }
    renderMergeControl();
    renderSelectedLayerActions();
  }

  function render() {
    renderLists(); renderMapping(); renderAxes(); renderLayers();
    if (!el.studioLayerManagerModal?.classList.contains('hidden')) {
      renderLayerManager();
    }
    renderLayerDetail();
    renderConflicts(); renderControls();
  }

  async function run(
    action,
    {
      onError = null,
      refreshAfter = false,
      isCurrent = () => true,
    } = {},
  ) {
    const projectRequestToken = projectRequestFence.capture();
    const requestIsCurrent = () => (
      projectRequestFence.isCurrent(projectRequestToken) && isCurrent()
    );
    setBusy(true);
    setMessage('요청 처리 중입니다…');
    try {
      const result = await action();
      if (!requestIsCurrent()) return null;
      if (result.success === false) throw new Error(result.message || '요청 실패');
      if (result.project_patch) {
        setProject(applyMotionStudioProjectPatch(state.project, result.project_patch));
      } else if (result.project) {
        setProject(result.project);
      }
      if (result.status) state.status = result.status;
      if (result.composition) state.composition = result.composition;
      setMessage(result.message || '완료');
      if (refreshAfter) await refresh(false);
      render();
      return result;
    } catch (error) {
      if (!requestIsCurrent()) return null;
      setMessage(error.message || String(error), true);
      onError?.(error);
      return null;
    } finally {
      if (requestIsCurrent()) setBusy(false);
    }
  }

  async function runMotorStart(action, pendingMessage) {
    const revision = ++motorCommandRevision;
    const previousStatus = state.status;
    pendingMotorStartAt = Date.now() / 1000;
    state.status = {
      ...(state.status || {}),
      state: 'initializing',
      phase: 'initializing',
      message: pendingMessage,
      updated_at: pendingMotorStartAt,
    };
    renderControls();
    const result = await run(action, {
      isCurrent: () => revision === motorCommandRevision,
      onError: () => {
        pendingMotorStartAt = 0;
        state.status = previousStatus;
        renderControls();
      },
    });
    if (revision !== motorCommandRevision) return null;
    if (!result) {
      pendingMotorStartAt = 0;
      state.status = previousStatus;
      render();
      return null;
    }
    if (result.status) pendingMotorStartAt = 0;
    renderControls();
    return result;
  }

  async function refresh(showMessage = true) {
    const projectRequestToken = projectRequestFence.capture();
    const requestRevision = ++refreshRevision;
    try {
      const result = await fetchMotionStudio();
      if (
        !projectRequestFence.isCurrent(projectRequestToken)
        || requestRevision !== refreshRevision
      ) return null;
      state.mappings = result.mappings || [];
      state.motionFiles = result.motion_files || [];
      state.workspaceProject = result.workspace_project || null;
      state.composition = result.composition || {
        conflicts: [], transition_warnings: [], point_curve_mismatches: [], conflict_free: true,
      };
      setProject(result.project || null);
      if (result.status) state.status = result.status;
      if (result.success === false) {
        setMessage(result.message || '통합 프로젝트를 확인하세요', true);
      } else if (showMessage) {
        setMessage('현재 통합 프로젝트를 갱신했습니다');
      } else if ([
        '',
        '프로젝트 상태 확인 중',
        '현재 프로젝트 모션 스튜디오를 불러오세요',
      ].includes(String(el.studioMessage?.textContent || '').trim())) {
        setMessage(state.status?.message || result.message || '현재 통합 프로젝트를 불러왔습니다');
      }
      render();
    } catch (error) {
      if (
        !projectRequestFence.isCurrent(projectRequestToken)
        || requestRevision !== refreshRevision
      ) return null;
      setMessage(error.message || String(error), true);
    }
  }

  async function exportFinalMotionFile(name) {
    const exportSelection = motionStudioExportSelection(state.project?.layers);
    if (!exportSelection.layer) {
      await showAlert(
        `모션 실행 파일을 저장할 수 없습니다.\n재생 선택 · ${exportSelection.count}개\n필요 조건 · 재생 선택 레이어 1개`,
        {
          title: '내보내기 대상 확인',
          confirmLabel: '확인',
          tone: 'warning',
        },
      );
      return null;
    }
    const exportLayer = exportSelection.layer;
    const confirmed = await showConfirm(
      `내보내기 대상 · ${exportLayer.name}\n`
      + `레이어 정보 · ${layerSummary(exportLayer)}\n`
      + '선택 기준 · 재생 선택 체크\n'
      + '연한 파란색 행 · 상세보기 대상이며 내보내기와 무관',
      {
        title: '모션 실행 파일 저장',
        confirmLabel: '이 레이어 저장',
        tone: 'primary',
      },
    );
    if (!confirmed) return null;
    const result = await run(
      () => exportMotionStudio(name),
      {
        onError: (error) => {
          void showAlert(motionStudioExportResultMessage(null, error), {
            title: '모션 실행 파일 저장 실패',
            confirmLabel: '확인',
            tone: 'danger',
          });
        },
      },
    );
    if (!result) return null;
    await onMotionFilesChange(result);
    await showAlert(motionStudioExportResultMessage(result), {
      title: '모션 실행 파일 저장 완료',
      confirmLabel: '확인',
      tone: 'info',
    });
    return result;
  }

  function resetProjectState() {
    projectRequestFence.invalidate();
    refreshRevision += 1;
    if (state.playbackAnimationFrame) {
      window.cancelAnimationFrame(state.playbackAnimationFrame);
    }
    closeLayerEditor();
    closeLayerManager();
    resetMotionStudioProjectState(state);
    setMessage('현재 프로젝트 모션 스튜디오를 불러오세요');
    render();
  }

  function bindEvents() {
    if (eventsBound) return;
    eventsBound = true;
    const studioGrid = el.studioLayerDetail?.closest('.studio-grid');
    const layerPanel = el.studioLayerRows?.closest('.studio-layer-panel');
    const axisPanel = el.studioAxisRows?.closest('.studio-axis-panel');
    if (studioGrid && layerPanel && axisPanel && el.studioLayerDetail) {
      el.studioLayerDetail.classList.add('motion-file-subsection', 'studio-graph-panel');
      studioGrid.insertBefore(el.studioLayerDetail, layerPanel);
      studioGrid.appendChild(axisPanel);
    }
    bindMotionStudioEvent(el.studioConflictInfo, 'click', (event) => {
      const button = event.target.closest('[data-resolve-point-curve]');
      if (!button) return;
      resolvePointCurveMismatch(String(button.dataset.layerId || ''));
    });
    bindMotionStudioProjectTransportEvents(el, {
      // Capture the selection before run() redraws the control from saved state.
      onTransitionSafetyChange: (transitionSafetyLevel) => run(() => saveMotionStudioProject({
        transition_safety_level: transitionSafetyLevel,
      })),
      onImportSelectionChange: renderControls,
      onImport: (motionFileId) => run(() => importMotionStudioFile({
        motion_file_id: motionFileId,
      })),
      onRecord: ({ mode, initialMoveTimeSec }) => {
        if (!requireMotorActionReady('녹화')) return;
        showLayerGraph({ composition: true });
        runMotorStart(
          () => startMotionStudioRecord({
            mode,
            initial_move_time_sec: initialMoveTimeSec,
          }),
          '모션 녹화 초기 위치 이동 요청 중',
        );
      },
      onInitialize: ({ initialMoveTimeSec }) => {
        if (!requireMotorActionReady('초기 위치 이동')) return;
        showLayerGraph({ composition: true });
        runMotorStart(
          () => startMotionStudioInitialization({
            initial_move_time_sec: initialMoveTimeSec,
          }),
          '초기 위치 이동 요청 중',
        );
      },
      onPlay: ({ initialMoveTimeSec }) => {
        if (!requireMotorActionReady('합성 미리보기 재생')) return;
        showLayerGraph({ composition: true });
        runMotorStart(
          () => startMotionStudioPlayback({
            initial_move_time_sec: initialMoveTimeSec,
          }),
          '합성 미리보기 초기 위치 이동 요청 중',
        );
      },
      // The helper disables duplicate stop clicks before this callback runs.
      onStop: () => {
        motorCommandRevision += 1;
        pendingMotorStartAt = 0;
        state.status = {
          ...(state.status || {}),
          state: 'stopping',
          phase: 'stopping',
          message: '정지 명령 전달 중',
          updated_at: Date.now() / 1000,
        };
        renderControls();
        run(stopMotionStudio, { refreshAfter: true });
      },
      onCreateLayer: async () => {
        const result = await run(() => createMotionStudioLayer());
        if (!result?.layer_id) return;
        state.selectedLayerId = result.layer_id;
        render();
      },
      defaultExportName: () => state.project?.name || 'motion',
      onExport: (name) => exportFinalMotionFile(name),
    });
    const onLayerChange = async (event) => {
      const row = event.target.closest('tr[data-studio-layer-id]');
      if (!row) return;
      if (!selectMotionStudioLayer(state, row.dataset.studioLayerId)) return;
      if (event.target.matches('[data-layer-enabled]')) {
        const layerId = row.dataset.studioLayerId;
        const layer = state.project?.layers?.find((item) => item.layer_id === layerId);
        const previousEnabled = layer?.enabled !== false;
        if (state.busy) {
          event.target.checked = previousEnabled;
          return;
        }
        const nextEnabled = event.target.checked;
        setProject(motionStudioSetLayerEnabled(state.project, layerId, nextEnabled));
        render();
        const result = await run(() => updateMotionStudioLayer({
          layer_id: layerId,
          enabled: nextEnabled,
        }));
        if (!result) {
          setProject(motionStudioSetLayerEnabled(
            state.project, layerId, previousEnabled,
          ));
          render();
        }
        return;
      }
      if (event.target.matches('[data-layer-main-name]')) {
        run(() => updateMotionStudioLayer({
          layer_id: row.dataset.studioLayerId,
          name: event.target.value,
        }));
      }
    };
    const onLayerClick = (event) => {
      if (event.target.closest('.studio-playback-choice')) return;
      const row = event.target.closest('tr[data-studio-layer-id]');
      if (!row) return;
      if (!selectMotionStudioLayer(state, row.dataset.studioLayerId)) return;
      el.studioLayerRows.querySelectorAll('tr[data-studio-layer-id]').forEach((item) => {
        item.classList.toggle('selected-row', item.dataset.studioLayerId === state.selectedLayerId);
      });
      renderSelectedLayerActions();
    };

    const onLayerManagerTab = (event) => {
      const button = event.target.closest('[data-layer-manager-tab]');
      if (!button) return;
      state.layerManagerTab = button.dataset.layerManagerTab || 'create';
      if (state.layerManagerTab === 'merge' && !state.mergeLayerIds.size) {
        state.mergeLayerIds = new Set((state.project?.layers || [])
          .filter((layer) => layer.enabled !== false && !layer.locked)
          .map((layer) => layer.layer_id));
      }
      state.mergeResultMessage = '';
      state.mergeResultError = false;
      renderLayerManager();
      renderMergeControl();
    };
    const onManagerLayerClick = (event) => {
      const row = event.target.closest('tr[data-manager-layer-id]');
      if (!row) return;
      if (!selectMotionStudioLayer(state, row.dataset.managerLayerId)) return;
      el.studioManagerLayerRows.querySelectorAll('tr[data-manager-layer-id]').forEach((item) => {
        const selected = item.dataset.managerLayerId === state.selectedLayerId;
        item.classList.toggle('selected-row', selected);
        const radio = item.querySelector('[data-manager-layer-select]');
        if (radio) radio.checked = selected;
      });
      renderSelectedLayerActions();
    };
    const onManagerLayerChange = (event) => {
      const row = event.target.closest('tr[data-manager-layer-id]');
      if (!row) return;
      if (!selectMotionStudioLayer(state, row.dataset.managerLayerId)) return;
      renderLayerManager();
      renderSelectedLayerActions();
    };
    const onMergeSelectionChange = (event) => {
      if (!event.target.matches('[data-manager-layer-merge]')) return;
      const row = event.target.closest('tr[data-manager-merge-layer-id]');
      if (!row) return;
      const layerId = row.dataset.managerMergeLayerId;
      updateMotionStudioMergeSelection(state, layerId, event.target.checked);
      state.mergeResultMessage = '';
      state.mergeResultError = false;
      renderLayerManager();
      renderMergeControl();
    };
    const onMergeModeChange = () => {
      state.mergeMode = el.studioMergeMode.value === 'append' ? 'append' : 'preserve';
      if (state.mergeMode !== 'append') state.mergeAppendLayerId = '';
      state.mergeResultMessage = '';
      state.mergeResultError = false;
      renderLayerManager();
      renderMergeControl();
    };
    const onAppendLayerChange = () => {
      const layerId = String(el.studioMergeAppendLayer.value || '');
      state.mergeAppendLayerId = state.mergeLayerIds.has(layerId) ? layerId : '';
      state.mergeResultMessage = '';
      state.mergeResultError = false;
      renderMergeControl();
    };
    const onSelectedLayerDetail = () => {
      if (!selectedLayer()) return;
      state.layerDetailMode = 'layer';
      showLayerGraph();
    };
    const onSelectedLayerCopy = async () => {
      const layer = selectedLayer();
      if (!layer || !await showConfirm(
        `선택한 '${layer.name}'을 복사할까요?\n복사본은 재생 미선택 상태로 생성됩니다.`,
        { title: '레이어 복사', confirmLabel: '복사' },
      )) return;
      const result = await run(() => duplicateMotionStudioLayer(layer.layer_id));
      if (!result?.layer_id) return;
      state.selectedLayerId = result.layer_id;
      render();
    };
    const onSelectedLayerEdit = () => {
      openLayerEditor(selectedLayer());
    };
    const onSelectedLayerLock = () => {
      const layer = selectedLayer();
      if (!layer) return;
      run(() => updateMotionStudioLayer({
        layer_id: layer.layer_id,
        locked: !layer.locked,
      }));
    };
    const onSelectedLayerDelete = async () => {
      const layer = selectedLayer();
      if (!layer || !await showConfirm(
        `레이어 '${layer.name}'을 삭제할까요?`,
        { title: '레이어 삭제', confirmLabel: '삭제', tone: 'danger' },
      )) return;
      let failureMessage = '';
      const result = await run(async () => {
        try {
          const response = await deleteMotionStudioLayer(layer.layer_id);
          if (response.success === false) {
            throw new Error(response.message || '레이어 삭제 실패');
          }
          return response;
        } catch (error) {
          failureMessage = error.message || String(error);
          throw error;
        }
      });
      if (!result) {
        window.alert(`레이어 삭제 실패\n\n${failureMessage || '삭제 응답을 확인하지 못했습니다.'}`);
        return;
      }
      state.selectedLayerId = resolveMotionStudioSelectedLayerId(
        state.project?.layers || [], state.selectedLayerId,
      );
      state.layerDetailMode = 'composition';
      setMessage(`레이어 '${layer.name}'을 삭제했습니다.`);
      render();
    };
    const onPlaybackGraph = () => {
      showLayerGraph({ composition: true });
    };
    const onCompositionDetail = () => {
      state.layerDetailMode = 'composition';
      renderLayerDetail();
    };
    const onLayerDetailTab = (event) => {
      const button = event.target.closest('[data-studio-layer-detail-tab]');
      if (!button) return;
      state.activeLayerDetailTab = button.dataset.studioLayerDetailTab || 'graph';
      renderLayerDetail();
    };
    const onMergeLayers = async () => {
      const layerIds = [...state.mergeLayerIds];
      if (layerIds.length < 2) return;
      const appendLayerId = state.mergeMode === 'append'
        ? state.mergeAppendLayerId
        : '';
      if (state.mergeMode === 'append' && !state.mergeLayerIds.has(appendLayerId)) return;
      const appendLayer = state.project?.layers?.find(
        (layer) => layer.layer_id === appendLayerId,
      );
      const name = nextMergedLayerName();
      if (!await showConfirm(
        appendLayerId
          ? `선택한 ${layerIds.length}개 레이어를 '${name}'로 합칠까요?\n`
            + `'${appendLayer?.name || appendLayerId}' 레이어 전체를 나머지 레이어의 마지막 시간 뒤로 이동합니다.\n`
            + '원본과 결과는 재생 선택 상태를 변경하지 않습니다.'
          : `선택한 ${layerIds.length}개 레이어를 '${name}'로 합칠까요?\n`
            + '현재 시간 위치를 유지하며, 원본과 결과는 재생 선택 상태를 변경하지 않습니다.',
        {
          title: appendLayerId ? '레이어 뒤에 이어 붙이기' : '레이어 합치기',
          confirmLabel: appendLayerId ? '이어 붙이기' : '합치기',
          tone: 'warning',
        },
      )) return;
      state.mergeResultMessage = appendLayerId
        ? `'${appendLayer?.name || appendLayerId}' 레이어 시간 이동 및 충돌 검사 중…`
        : `${layerIds.length}개 레이어 충돌 검사 중…`;
      state.mergeResultError = false;
      renderMergeControl();
      let failureMessage = '';
      const result = await run(async () => {
        try {
          const preview = await previewMotionStudioMerge({
            project: mergePreviewProject(layerIds),
            layer_ids: layerIds,
            append_layer_id: appendLayerId,
            name,
            mapping_rows: activeMapping()?.rows || [],
          });
          if (preview.success === false) {
            throw new Error(preview.message || '레이어 합치기 충돌 검사 실패');
          }
          const committed = await commitMotionStudioMerge({
            source_layer_ids: layerIds,
            append_layer_id: appendLayerId,
            name: preview.layer?.name || name,
            layer: preview.layer,
            source_revisions: Object.fromEntries(layerIds.map((layerId) => {
              const source = state.project?.layers?.find((item) => item.layer_id === layerId);
              return [layerId, Number(source?.edit_revision || 0)];
            })),
          });
          if (committed.success === false) {
            throw new Error(committed.message || '레이어 합치기 저장 실패');
          }
          return committed;
        } catch (error) {
          failureMessage = error.message || String(error);
          throw error;
        }
      });
      if (result) {
        state.selectedLayerId = result.layer_id || '';
        state.mergeLayerIds.clear();
        state.mergeAppendLayerId = '';
        state.layerManagerTab = 'merge';
        state.mergeResultMessage = appendLayerId
          ? `뒤에 이어 붙이기 성공 · '${name}' 레이어를 생성했습니다`
          : `합치기 성공 · '${name}' 레이어를 생성했습니다`;
        state.mergeResultError = false;
        render();
      } else {
        state.mergeResultMessage = failureMessage || '레이어 합치기 중단 · 결과를 확인하세요';
        state.mergeResultError = true;
        renderMergeControl();
      }
    };
    const layerController = createMotionStudioLayerController({
      el,
      handlers: {
        onLayerChange,
        onLayerClick,
        onManagerOpen: openLayerManager,
        onManagerClose: closeLayerManager,
        onManagerTab: onLayerManagerTab,
        onManagerLayerClick,
        onManagerLayerChange,
        onMergeSelectionChange,
        onMergeModeChange,
        onAppendLayerChange,
        onDetail: onSelectedLayerDetail,
        onCopy: onSelectedLayerCopy,
        onEdit: onSelectedLayerEdit,
        onLock: onSelectedLayerLock,
        onDelete: onSelectedLayerDelete,
        onPlaybackGraph,
        onCompositionDetail,
        onDetailTab: onLayerDetailTab,
        onMerge: onMergeLayers,
      },
    });
    layerController.bind();
    editorController.bind();

  }

  async function addMotionFile(fileId) {
    const result = await run(() => importMotionStudioFile({ motion_file_id: fileId }));
    if (!result) throw new Error('모션 파일을 레이어로 추가하지 못했습니다');
    return result;
  }

  function renderSnapshot(studioStatus, midiStatus) {
    const previousStatus = state.status;
    if (studioStatus && Object.keys(studioStatus).length) {
      const statusUpdatedAt = Number(studioStatus.updated_at);
      if (
        pendingMotorStartAt > 0
        && Number.isFinite(statusUpdatedAt)
        && statusUpdatedAt >= pendingMotorStartAt
        && ['initializing', 'recording', 'playing'].includes(
          String(studioStatus.state || ''),
        )
      ) {
        pendingMotorStartAt = 0;
      }
      state.status = studioStatus;
      const feedback = motionStudioRuntimeStatusMessage(previousStatus, studioStatus);
      if (feedback) setMessage(feedback.message, feedback.error);
    }
    if (midiStatus) state.midi = midiStatus;
    syncPlaybackClock();
    const axesKey = JSON.stringify([
      Boolean(state.midi?.select_locked),
      (state.midi?.channels || []).map((channel) => [
        channel?.motion_id,
        channel?.select_enabled ?? channel?.control_enabled,
        Number(channel?.motion_value_deg),
      ]),
    ]);
    if (state.snapshotAxesKey !== axesKey) {
      state.snapshotAxesKey = axesKey;
      renderAxes();
    }
    if (el.studioState) el.studioState.textContent = state.status?.message || '대기';
    if (el.studioElapsed) {
      el.studioElapsed.textContent = timeText(state.status?.elapsed_sec);
    }
    if (el.studioFrameCount) {
      el.studioFrameCount.textContent = `${state.status?.recorded_frames || 0}프레임 · 20ms`;
    }
    const controlsKey = JSON.stringify([
      state.status?.state,
      state.status?.phase,
      state.busy,
      pendingMotorStartAt > 0,
      state.project?.project_id,
      activeMapping()?.file_id,
      activeMapping()?.rows?.length || 0,
      (state.project?.layers || []).map((layer) => [
        layer.layer_id, layer.enabled !== false, Boolean(layer.locked),
      ]),
      state.composition?.conflicts?.length || 0,
      state.composition?.transition_warnings?.length || 0,
      state.composition?.point_curve_mismatches?.length || 0,
      motorActionBlockReason(),
    ]);
    if (state.snapshotControlsKey !== controlsKey) {
      state.snapshotControlsKey = controlsKey;
      renderControls();
    }
    if (renderRecordingPreview()) {
      animatePlaybackGraph();
      return;
    }
    state.recordingPreviewKey = '';
    const playback = renderPlaybackMonitor();
    animatePlaybackGraph();
    const now = performance.now();
    if (
      state.detailGraph
      && state.detailGraph.compositionMode
      && playback.displayState !== state.lastPlaybackDisplayState
      && now - state.playbackGraphRenderedAt > 80
    ) {
      drawLayerGraph(state.detailGraph.tracks, state.detailGraph.warnings, playback);
      state.playbackGraphRenderedAt = now;
      state.lastPlaybackDisplayState = playback.displayState;
    }
  }

  return { bindEvents, refresh, resetProjectState, renderSnapshot, addMotionFile };
}
