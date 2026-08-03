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
  applyMotionStudioProjectPatch,
  motionStudioCanCreatePointCurve,
  motionStudioCanSwitchPointDraftCurve,
  motionStudioCanvasEventPoint,
  motionStudioEditorNextValueScale,
  motionStudioEditorGraphClickAction,
  motionStudioEditorValidationProject,
  motionStudioEditorValueBounds,
  motionStudioLayerDataEqual,
  motionStudioLayerDuration,
  motionStudioLayerMotionIds,
  motionStudioMergePreviewProject,
  motionStudioSetLayerEnabled,
  motionStudioMotionAxisRange,
  motionStudioValueViewAfterRangeUnlock,
  motionStudioMotionTargetAtTime,
  motionStudioNearestMotionTarget,
  motionStudioPointCurveAtTime,
  motionStudioPointCurveIsApplied,
  motionStudioPointCurveOrder,
  motionStudioPointCurvePreview,
  motionStudioPointCurveViewEnd,
  motionStudioCopyPointRange,
  motionStudioDeletePointRange,
  motionStudioPointDragStarted,
  motionStudioPointHitTarget,
  motionStudioPointRangePoints,
  motionStudioPointRangeReady,
  motionStudioPointRangeTargetsMatch,
  motionStudioRuntimeStatusMessage,
  motionStudioShouldProtectPointAxisSelection,
  motionStudioSnapFrameTime,
  resolveMotionStudioSelectedLayerId,
  synchronizeMotionStudioEditorTimeline,
} from './motion_studio_calculations.js?v=20260803-studio-structure-2';
import {
  drawMotionStudioEditorGraph,
  drawMotionStudioLayerGraph,
  motionStudioCompositionTracks as compositionTracks,
  motionStudioLayerTracks as layerTracks,
} from './motion_studio_graph.js?v=20260803-studio-structure-2';
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
  motionStudioEditorInspectorState,
  motionStudioRangeWarningGroups,
  renderMotionStudioEditorPresentation,
  requestMotionStudioEditorSave,
} from './motion_studio_editor_ui.js?v=20260729-range-warning-detail-1';
import {
  createMotionStudioEditorSession,
  motionStudioEditorFailureFingerprint,
  motionStudioEditorLayerIsDirty,
  motionStudioEditorPointCurves,
  motionStudioPointDraftHasUnsavedChanges,
  motionStudioSelectedDraftPoint,
  motionStudioSelectedPointRange,
  motionStudioStoredCurveForDraft,
} from './motion_studio_editor_state.js?v=20260803-studio-structure-1';
import {
  createMotionStudioGraphScheduler,
} from './motion_studio_graph_scheduler.js?v=20260803-studio-structure-1';
import {
  createMotionStudioEditorViewportController,
} from './motion_studio_editor_viewport.js?v=20260803-studio-structure-1';
import {
  createMotionStudioPlaybackController,
} from './motion_studio_playback.js?v=20260803-studio-structure-2';
import {
  renderMotionStudioLayerManager,
} from './motion_studio_layer_manager.js?v=20260803-studio-structure-2';
import {
  MOTION_STUDIO_PERIOD_MS,
  MOTION_STUDIO_PERIOD_SEC,
  MOTION_STUDIO_TIME_EPSILON,
} from './motion_studio_constants.js?v=20260803-studio-structure-2';
import {
  motionStudioGraphPointInside,
  motionStudioMoveDraftPoint,
  motionStudioMoveTangentHandle,
  motionStudioPanEditorGraph,
} from './motion_studio_graph_interactions.js?v=20260803-studio-structure-2';
import {
  addMotionStudioDraftPoint,
  applyMotionStudioCopiedPointRange,
  applyMotionStudioDeletedPointRange,
  deleteMotionStudioDraftPoint,
  updateMotionStudioDraftPoint,
} from './motion_studio_point_editor.js?v=20260803-studio-structure-2';
import { showAlert, showConfirm } from './ui_dialogs.js?v=20260727-popup-common-3';

export {
  applyMotionStudioProjectPatch,
  motionStudioCanCreatePointCurve,
  motionStudioCanSwitchPointDraftCurve,
  motionStudioCanvasEventPoint,
  motionStudioEditorNextValueScale,
  motionStudioEditorGraphClickAction,
  motionStudioEditorValidationProject,
  motionStudioEditorValueBounds,
  motionStudioLayerDataEqual,
  motionStudioLayerDuration,
  motionStudioLayerMotionIds,
  motionStudioMergePreviewProject,
  motionStudioSetLayerEnabled,
  motionStudioMotionAxisRange,
  motionStudioValueViewAfterRangeUnlock,
  motionStudioMotionTargetAtTime,
  motionStudioNearestMotionTarget,
  motionStudioPointCurveAtTime,
  motionStudioPointCurveIsApplied,
  motionStudioPointCurveOrder,
  motionStudioPointCurvePreview,
  motionStudioPointCurveViewEnd,
  motionStudioCopyPointRange,
  motionStudioDeletePointRange,
  motionStudioPointDragStarted,
  motionStudioPointHitTarget,
  motionStudioPointRangePoints,
  motionStudioPointRangeReady,
  motionStudioPointRangeTargetsMatch,
  motionStudioRuntimeStatusMessage,
  motionStudioShouldProtectPointAxisSelection,
  motionStudioSnapFrameTime,
  resolveMotionStudioSelectedLayerId,
  synchronizeMotionStudioEditorTimeline,
};

const MOTION_ID_PATTERN = /^[1-9]\d*-[1-9]\d*$/;
const POINT_RANGE_EDIT_OPERATIONS = new Set([
  'time_shift',
  'time_scale',
  'value_offset',
  'value_scale',
]);

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

function editorId(prefix) {
  if (globalThis.crypto?.randomUUID) return `${prefix}_${globalThis.crypto.randomUUID().slice(0, 8)}`;
  return `${prefix}_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 7)}`;
}

export function createMotionStudioController({
  el,
  getMotorActionBlockReason = () => '',
  getConfiguredMotors = () => [],
  onMotionFilesChange = async () => {},
}) {
  const state = createMotionStudioState();
  let preferredEditorEditOperation = 'time_scale';

  const clone = (value) => JSON.parse(JSON.stringify(value));
  const layerMetricsCache = new WeakMap();
  const layerTracksCache = new WeakMap();
  const layerPointCoverageCache = new WeakMap();
  let compositionViewCache = null;
  let motorCommandRevision = 0;
  let pendingMotorStartAt = 0;

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
    const pointCurves = editorPointCurves(layer);
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

  function editorMotionIds(layer) {
    return motionStudioLayerMotionIds(layer);
  }

  function editorPointCurves(layer) {
    return motionStudioEditorPointCurves(layer);
  }

  function selectedDraftPoint(editor = state.editor) {
    return motionStudioSelectedDraftPoint(editor);
  }

  function storedCurveForDraft(editor = state.editor) {
    return motionStudioStoredCurveForDraft(editor);
  }

  function selectedEditorPointRange(editor = state.editor) {
    return motionStudioSelectedPointRange(editor);
  }

  function pointCurveIsApplied(editor, curveId) {
    return motionStudioPointCurveIsApplied(editor?.working, curveId);
  }

  function pointCurveCanBeCreated(editor = state.editor) {
    const selectedIds = editorSelectedMotionIds();
    const motionId = String(
      editor?.pointDraft?.motion_id
      || (selectedIds.length === 1 ? selectedIds[0] : ''),
    );
    return motionStudioCanCreatePointCurve(editor?.working, motionId);
  }

  function pointDraftHasUnsavedChanges(editor = state.editor) {
    return motionStudioPointDraftHasUnsavedChanges(editor);
  }

  function loadPointDraft(curve, pointId = '') {
    const editor = state.editor;
    if (!editor || !curve) return;
    editor.pointDraft = clone(curve);
    editor.pendingPointCandidate = null;
    const derivedOrder = (editor.pointDraft.points || []).every(
      (point) => point.tangent_mode === 'linear',
    ) ? 1 : 3;
    editor.pointDraft.interpolation_order = motionStudioPointCurveOrder(
      editor.pointDraft.interpolation_order,
      derivedOrder,
    );
    editor.pointCurveOrder = editor.pointDraft.interpolation_order;
    if (el.studioEditorPointCurveOrder) {
      el.studioEditorPointCurveOrder.value = String(editor.pointCurveOrder);
    }
    (editor.pointDraft.points || []).forEach((point) => {
      if (point.tangent_mode === 'linear') point.tangent_mode = 'auto';
    });
    editor.selectedPointId = pointId || curve.points?.[0]?.point_id || '';
    editor.draggingHandle = null;
    editor.draggingPoint = null;
  }

  function selectOnlyEditorAxis(motionId) {
    el.studioEditorAxisList?.querySelectorAll('input').forEach((input) => {
      input.checked = input.value === motionId;
    });
  }

  function setEditorPointRange(editor, startSec, endSec, motionId = '', curveId = '') {
    if (!editor) return;
    editor.selectionStartSec = Number(startSec);
    editor.selectionEndSec = Number(endSec);
    editor.selectionMotionId = String(motionId || '');
    editor.selectionCurveId = String(curveId || '');
    if (
      el.studioEditorRangeCopyTarget
      && Math.abs(Number(endSec) - Number(startSec))
        >= MOTION_STUDIO_PERIOD_SEC - MOTION_STUDIO_TIME_EPSILON
    ) {
      const curve = selectedRangeCurve(editor);
      const curveEnd = Math.max(
        0,
        ...(curve?.points || []).map((point) => Number(point.time_sec) || 0),
      );
      el.studioEditorRangeCopyTarget.value = motionStudioSnapFrameTime(
        Math.max(Number(startSec), Number(endSec), curveEnd) + MOTION_STUDIO_PERIOD_SEC,
      ).toFixed(2);
    }
  }

  function clearEditorPointRange(editor) {
    if (!editor) return;
    editor.selectionStartSec = null;
    editor.selectionEndSec = null;
    editor.selectionStage = 0;
    editor.selectionAnchor = null;
    editor.selectionMotionId = '';
    editor.selectionCurveId = '';
    editor.lastGraphClick = null;
  }

  function clearPendingPointCandidate(editor = state.editor) {
    if (editor) editor.pendingPointCandidate = null;
  }

  function rememberEditorEditOperation(editor, operation) {
    if (!editor || !POINT_RANGE_EDIT_OPERATIONS.has(operation)) return;
    editor.preferredEditOperation = operation;
    preferredEditorEditOperation = operation;
  }

  function enterEditorPointMode(editor) {
    if (!editor) return;
    const currentOperation = String(
      el.studioEditorOperation?.value || editor.operation || '',
    );
    if (POINT_RANGE_EDIT_OPERATIONS.has(currentOperation)) {
      rememberEditorEditOperation(editor, currentOperation);
      editor.pointModeReturnOperation = currentOperation;
    }
    editor.operation = 'point_curve';
    if (el.studioEditorOperation) el.studioEditorOperation.value = 'point_curve';
  }

  function restoreEditorEditOperation(editor) {
    if (!editor) return '';
    const operation = POINT_RANGE_EDIT_OPERATIONS.has(editor.pointModeReturnOperation)
      ? editor.pointModeReturnOperation
      : (
        POINT_RANGE_EDIT_OPERATIONS.has(editor.preferredEditOperation)
          ? editor.preferredEditOperation
          : preferredEditorEditOperation
      );
    editor.operation = operation;
    editor.preferredEditOperation = operation;
    editor.pointModeReturnOperation = '';
    if (el.studioEditorOperation) el.studioEditorOperation.value = operation;
    clearPendingPointCandidate(editor);
    clearEditorPointRange(editor);
    return operation;
  }

  function activatePointDraftMutation(editor, message) {
    if (!editor?.pointDraft) return;
    clearPendingPointCandidate(editor);
    clearEditorPointRange(editor);
    enterEditorPointMode(editor);
    const lastTime = Math.max(
      0,
      ...(editor.pointDraft.points || []).map(
        (point) => Number(point.time_sec) || 0,
      ),
    );
    if (lastTime > editor.pointTimelineEnd) editor.pointTimelineEnd = lastTime;
    if (lastTime > editor.viewEnd) editor.viewEnd = lastTime;
    setEditorMessage(message);
    renderEditor();
  }

  function setPointCurveMode(curve = null, pointId = '') {
    enterEditorPointMode(state.editor);
    if (curve) {
      selectOnlyEditorAxis(curve.motion_id);
      loadPointDraft(curve, pointId);
      const points = curve.points || [];
      setEditorPointRange(
        state.editor,
        Number(points[0]?.time_sec || 0),
        Number(points[points.length - 1]?.time_sec || 0),
        curve.motion_id,
        curve.curve_id,
      );
      state.editor.selectionStage = 0;
      state.editor.selectionAnchor = null;
    }
    renderEditor();
  }

  function selectPointCurveFromGraph(curve, pointId = '', activatePointMode = true) {
    const editor = state.editor;
    if (!editor || !curve) return false;
    const activeCurveId = String(editor.pointDraft?.curve_id || '');
    const targetCurveId = String(curve.curve_id || '');
    if (!motionStudioCanSwitchPointDraftCurve(
      activeCurveId,
      targetCurveId,
      pointDraftHasUnsavedChanges(editor),
    )) {
      setEditorMessage(
        '현재 포인트 변경을 먼저 결과 미리보기하고 작업본에 반영한 뒤 다른 곡선을 선택하세요.',
        true,
      );
      renderEditor();
      return false;
    }
    if (activatePointMode) {
      setPointCurveMode(curve, pointId);
    } else {
      selectOnlyEditorAxis(curve.motion_id);
      loadPointDraft(curve, pointId);
      renderEditorControls();
      drawEditorGraph();
    }
    return true;
  }

  function protectPointDraftAxisSelection() {
    const editor = state.editor;
    if (!editor?.pointDraft) return false;
    const pointMode = el.studioEditorOperation?.value === 'point_curve';
    const unsaved = pointDraftHasUnsavedChanges(editor);
    if (!motionStudioShouldProtectPointAxisSelection(
      true,
      pointMode,
      unsaved,
    )) return false;
    selectOnlyEditorAxis(editor.pointDraft.motion_id);
    setEditorMessage(
      unsaved
        ? '현재 포인트 변경을 먼저 결과 미리보기하고 작업본에 반영한 뒤 Motion ID 선택을 바꾸세요.'
        : '포인트 곡선 편집 중에는 해당 Motion ID만 사용합니다. 다른 곡선은 그래프에서 선택하세요.',
      true,
    );
    renderEditor();
    return true;
  }

  function syncPointControls() {
    const editor = state.editor;
    const point = selectedDraftPoint(editor);
    const pointMode = el.studioEditorOperation?.value === 'point_curve';
    const appliedPointCurve = pointCurveIsApplied(editor, editor?.pointDraft?.curve_id);
    const editablePointCurve = appliedPointCurve || pointCurveCanBeCreated(editor);
    const selectedRange = !pointMode ? selectedEditorPointRange(editor) : null;
    const rangeReady = Boolean(selectedRange);
    const draftPoints = editor?.pointDraft?.points || [];
    const pointAtTime = (timeSec) => draftPoints.find(
      (candidate) => Math.abs(Number(candidate.time_sec) - Number(timeSec)) < 1e-7,
    ) || null;
    const startPoint = rangeReady ? pointAtTime(editor.selectionStartSec) : point;
    const endPoint = rangeReady ? pointAtTime(editor.selectionEndSec) : null;
    el.studioEditorSelectedPointSummary?.classList.toggle('hidden', !startPoint);
    if (el.studioEditorSelectedPointTitle) {
      el.studioEditorSelectedPointTitle.textContent = rangeReady
        ? '선택 범위'
        : '선택 포인트';
    }
    if (el.studioEditorSelectedPointStartTime) {
      el.studioEditorSelectedPointStartTime.textContent = startPoint
        ? `${Number(startPoint.time_sec).toFixed(2)}초`
        : '-';
    }
    if (el.studioEditorSelectedPointStartValue) {
      el.studioEditorSelectedPointStartValue.textContent = startPoint
        ? `${Number(startPoint.value_deg).toFixed(3)}°`
        : '-';
    }
    el.studioEditorSelectedPointEnd?.classList.toggle('pending', !endPoint);
    if (el.studioEditorSelectedPointEndTime) {
      el.studioEditorSelectedPointEndTime.textContent = endPoint
        ? `${Number(endPoint.time_sec).toFixed(2)}초`
        : '선택 필요';
    }
    if (el.studioEditorSelectedPointEndValue) {
      el.studioEditorSelectedPointEndValue.textContent = endPoint
        ? `${Number(endPoint.value_deg).toFixed(3)}°`
        : '-';
    }
    if (el.studioEditorPointAddButton) {
      const candidate = editor?.pendingPointCandidate;
      const canAddPoint = pointMode
        && Boolean(candidate)
        && !editor?.preview
        && editorSelectedMotionIds().length === 1;
      el.studioEditorPointAddButton.disabled = !canAddPoint;
      el.studioEditorPointAddButton.title = candidate
        ? `${candidate.timeSec.toFixed(2)}초 · ${candidate.valueDeg.toFixed(3)}°`
        : '그래프에서 추가할 위치를 먼저 선택하세요';
    }
    [el.studioEditorPointTime, el.studioEditorPointValue, el.studioEditorPointMode].forEach((field) => {
      if (field) field.disabled = !pointMode || !point || !editablePointCurve;
    });
    if (el.studioEditorPointDeleteButton) {
      const canDeletePoint = pointMode
        && Boolean(point)
        && editablePointCurve
        && (editor?.pointDraft?.points?.length || 0) > 2;
      el.studioEditorPointDeleteButton.disabled = !canDeletePoint;
      el.studioEditorPointDeleteButton.title = canDeletePoint
        ? '선택한 포인트만 삭제하고 남은 포인트로 곡선을 다시 계산합니다'
        : '곡선을 유지하려면 포인트가 최소 2개 필요합니다';
    }
    if (el.studioEditorRangeStatus) {
      el.studioEditorRangeStatus.textContent = rangeReady
        ? `${selectedRange.curve.motion_id} · `
          + `${Number(editor.selectionStartSec).toFixed(2)}초 ~ `
          + `${Number(editor.selectionEndSec).toFixed(2)}초 · `
          + `${selectedRange.points.length}개 포인트`
        : pointMode
          ? '시간 이동·시간 배율·모션값 이동·모션값 배율 중 하나를 선택한 뒤 시작·종료 포인트를 선택하세요.'
          : '그래프에서 같은 포인트 곡선의 시작·종료 포인트를 선택하세요.';
      el.studioEditorRangeStatus.classList.toggle('ready', rangeReady);
    }
    if (el.studioEditorRangeCopyTarget) {
      el.studioEditorRangeCopyTarget.disabled = !rangeReady || Boolean(editor?.preview);
    }
    if (el.studioEditorRangeCopyButton) {
      el.studioEditorRangeCopyButton.disabled = !rangeReady || Boolean(editor?.preview);
    }
    if (el.studioEditorRangeDeleteButton) {
      const remainingCount = rangeReady
        ? (selectedRange.curve.points || []).length - selectedRange.points.length
        : 0;
      el.studioEditorRangeDeleteButton.disabled = (
        !rangeReady || Boolean(editor?.preview) || remainingCount < 2
      );
      el.studioEditorRangeDeleteButton.title = !rangeReady
        ? '같은 포인트 곡선의 시작·종료 포인트를 먼저 선택하세요'
        : remainingCount < 2
          ? '곡선을 유지하려면 삭제 후 포인트가 최소 2개 남아야 합니다'
          : '선택 범위의 포인트를 삭제합니다';
    }
    if (el.studioEditorPointCurveOrder) {
      el.studioEditorPointCurveOrder.disabled = !pointMode || !editablePointCurve;
      el.studioEditorPointCurveOrder.value = String(motionStudioPointCurveOrder(
        editor?.pointDraft?.interpolation_order,
        editor?.pointCurveOrder,
      ));
    }
    if (el.studioEditorPointTimelineEnd) {
      el.studioEditorPointTimelineEnd.disabled = !pointMode;
      if (document.activeElement !== el.studioEditorPointTimelineEnd) {
        el.studioEditorPointTimelineEnd.value = Number(
          editor?.pointTimelineEnd
          || motionStudioPointCurveViewEnd(editorDuration(editor?.working)),
        ).toFixed(2);
      }
    }
    if (!point) {
      if (el.studioEditorPointTime) el.studioEditorPointTime.value = '';
      if (el.studioEditorPointValue) el.studioEditorPointValue.value = '';
      return;
    }
    if (el.studioEditorPointTime && document.activeElement !== el.studioEditorPointTime) {
      el.studioEditorPointTime.value = Number(point.time_sec).toFixed(2);
    }
    if (el.studioEditorPointValue && document.activeElement !== el.studioEditorPointValue) {
      el.studioEditorPointValue.value = Number(point.value_deg).toFixed(3);
    }
    if (el.studioEditorPointMode) el.studioEditorPointMode.value = point.tangent_mode || 'auto';
  }

  function editorSelectedMotionIds() {
    return [...(el.studioEditorAxisList?.querySelectorAll('input:checked') || [])]
      .map((input) => input.value);
  }

  function selectedEditorMotionAxisRange() {
    const selectedIds = editorSelectedMotionIds();
    if (selectedIds.length !== 1) return null;
    return motionStudioMotionAxisRange(activeMapping()?.rows || [], selectedIds[0]);
  }

  function resetEditorValueView({
    unlock = false,
    preserveLockedRange = false,
  } = {}) {
    const editor = state.editor;
    if (!editor) return;
    const unlockedView = preserveLockedRange
      ? motionStudioValueViewAfterRangeUnlock(editor.valueRangeLock)
      : null;
    editor.valueScale = 1;
    editor.valueOffset = 0;
    editor.valueView = unlockedView;
    if (unlock) editor.valueRangeLock = null;
  }

  function refreshEditorAxisControls(preferredSelection = null, layerOverride = null) {
    const editor = state.editor;
    if (!editor) return;
    const previousSelection = preferredSelection || new Set(editorSelectedMotionIds());
    const displayedLayer = layerOverride || editor.preview || editor.working;
    const ids = editorMotionIds(displayedLayer);
    if (el.studioEditorAxisList) {
      el.studioEditorAxisList.innerHTML = ids.map((motionId) => (
        `<label><input type="checkbox" value="${escapeHtml(motionId)}"${
          previousSelection.has(motionId) ? ' checked' : ''
        }><span>${escapeHtml(editorAxisLabel(motionId))}</span></label>`
      )).join('');
    }

    if (el.studioEditorAddAxisButton) {
      el.studioEditorAddAxisButton.disabled = !el.studioEditorAddAxisSelect?.value.trim()
        || Boolean(editor.preview);
    }
    if (el.studioEditorCopyAxisSource) {
      const previousSource = el.studioEditorCopyAxisSource.value;
      el.studioEditorCopyAxisSource.innerHTML = ids.length
        ? ids.map((motionId) => (
          `<option value="${escapeHtml(motionId)}">${escapeHtml(editorAxisLabel(motionId))}</option>`
        )).join('')
        : '<option value="">원본 축 없음</option>';
      if (ids.includes(previousSource)) el.studioEditorCopyAxisSource.value = previousSource;
    }
    if (el.studioEditorCopyAxisTarget) {
      const targetId = el.studioEditorCopyAxisTarget.value.trim();
      if (targetId && ids.includes(targetId)) el.studioEditorCopyAxisTarget.value = '';
    }
    if (el.studioEditorCopyAxisButton) {
      el.studioEditorCopyAxisButton.disabled = !ids.length
        || !el.studioEditorCopyAxisTarget?.value.trim()
        || Boolean(editor.preview);
    }
    if (el.studioEditorDeleteAxisButton) {
      el.studioEditorDeleteAxisButton.disabled = !previousSelection.size
        || Boolean(editor.preview);
    }
  }

  function setEditorMessage(message, error = false) {
    if (!el.studioEditorMessage) return;
    el.studioEditorMessage.textContent = message || '';
    el.studioEditorMessage.classList.toggle('error-text', error);
  }

  function renderEditorValidationDetails() {
    if (!el.studioEditorValidationDetails) return;
    const editor = state.editor;
    const validation = editor?.previewValidation || editor?.validation;
    const groups = motionStudioRangeWarningGroups(validation?.range_warnings);
    el.studioEditorValidationDetails.classList.toggle('hidden', !groups.length);
    if (!groups.length) {
      el.studioEditorValidationDetails.replaceChildren();
      return;
    }
    const selectedIds = new Set(editorSelectedMotionIds());
    el.studioEditorValidationDetails.innerHTML = groups.map((group) => {
      const actual = group.belowLower && group.aboveUpper
        ? `실제 ${group.minimumDeg.toFixed(3)}° ~ ${group.maximumDeg.toFixed(3)}°`
        : group.belowLower
          ? `실제 최소 ${group.minimumDeg.toFixed(3)}°`
          : `실제 최대 ${group.maximumDeg.toFixed(3)}°`;
      const periods = group.segments.map((segment) => (
        Math.abs(segment.endSec - segment.startSec) < 0.0005
          ? `${segment.startSec.toFixed(3)}초`
          : `${segment.startSec.toFixed(3)}~${segment.endSec.toFixed(3)}초`
      )).join(', ');
      const visibility = selectedIds.has(group.motionId)
        ? '그래프 표시 중'
        : '그래프에서 숨김';
      return `<div><strong>${escapeHtml(editorAxisLabel(group.motionId))}</strong>`
        + ` · ${visibility}`
        + ` · 허용 ${group.lowerDeg.toFixed(3)}° ~ ${group.upperDeg.toFixed(3)}°`
        + ` · ${actual}`
        + ` · 초과 ${group.count}점`
        + ` · ${escapeHtml(periods)}</div>`;
    }).join('');
  }

  function editorDuration(layer) {
    return motionStudioLayerDuration(layer);
  }

  function editorTimeBounds(layer) {
    const times = (layer?.frames || [])
      .map((frame) => Number(frame.time_sec))
      .filter((timeSec) => Number.isFinite(timeSec) && timeSec >= 0);
    return {
      start: times.length ? Math.min(...times) : 0,
      end: times.length ? Math.max(...times) : MOTION_STUDIO_PERIOD_SEC,
    };
  }

  function openLayerEditor(layer) {
    if (!layer || layer.locked) return;
    const duration = editorDuration(layer);
    const operation = preferredEditorEditOperation;
    if (el.studioEditorOperation) el.studioEditorOperation.value = operation;
    const pointTimelineEnd = motionStudioPointCurveViewEnd(duration);
    state.editor = createMotionStudioEditorSession({
      layer,
      operation,
      duration,
      pointTimelineEnd,
      rangeWarnings: (state.composition?.range_warnings || []).filter(
        (warning) => String(warning.layer_id || '') === String(layer.layer_id || ''),
      ),
    });
    if (el.studioEditorTitle) el.studioEditorTitle.textContent = `레이어 편집 · ${layer.name}`;
    if (el.studioEditorSubtitle) el.studioEditorSubtitle.textContent = '편집 반영 0회';
    refreshEditorAxisControls(new Set(editorMotionIds(layer)), layer);
    el.studioLayerEditorModal?.classList.remove('hidden');
    document.body.classList.add('modal-open');
    const appliedCurveCount = editorPointCurves(layer).length;
    setEditorMessage(
      appliedCurveCount
        ? `반영된 포인트 곡선 ${appliedCurveCount}개 · 편집할 포인트를 선택하세요.`
        : '일반 모션은 Motion ID를 하나 선택해 전체 포인트를 생성한 뒤 편집하세요.',
    );
    renderEditor();
  }

  function closeLayerEditor() {
    editorGraphScheduler.cancel();
    state.editor = null;
    el.studioLayerEditorModal?.classList.add('hidden');
    el.studioEditorSaveConfirmModal?.classList.add('hidden');
    document.body.classList.remove('modal-open');
  }

  function openLayerManager() {
    renderLayerManager();
    el.studioLayerManagerModal?.classList.remove('hidden');
    document.body.classList.add('modal-open');
  }

  function closeLayerManager() {
    el.studioLayerManagerModal?.classList.add('hidden');
    document.body.classList.remove('modal-open');
  }

  function renderEditorControls() {
    const editor = state.editor;
    const operation = el.studioEditorOperation?.value || 'value_offset';
    const pointMode = operation === 'point_curve';
    const selectedIds = editorSelectedMotionIds();
    const selectedAxisPointBacked = selectedIds.length === 1
      && !layerPointCoverageIssues(editor?.working).includes(selectedIds[0]);
    const appliedPointCurve = pointCurveIsApplied(editor, editor?.pointDraft?.curve_id);
    const selectedAppliedPointCurve = editorPointCurves(editor?.working).some(
      (curve) => selectedIds.includes(String(curve.motion_id || '')),
    );
    const creatablePointCurve = pointMode && pointCurveCanBeCreated(editor);
    const workingPointCurve = Boolean(storedCurveForDraft(editor));
    const pointRangeReady = Boolean(selectedEditorPointRange(editor));
    const pointDraftDirty = pointDraftHasUnsavedChanges(editor);
    const hasTransientChange = Boolean(editor?.preview) || pointDraftDirty;
    const layerDirty = motionStudioEditorLayerIsDirty(editor, motionStudioLayerDataEqual);
    if (editor?.saveState === 'failed'
      && editor.saveFailureFingerprint !== motionStudioEditorFailureFingerprint(editor)) {
      editor.saveState = 'dirty';
      editor.saveError = '';
    }
    let saveState = editor?.saveState || 'saved';
    if (editor?.preview) saveState = 'preview';
    else if (!['saving', 'failed'].includes(saveState)) {
      saveState = layerDirty || pointDraftDirty ? 'dirty' : 'saved';
    }
    if (el.studioEditorUndoButton) {
      el.studioEditorUndoButton.disabled = !hasTransientChange && !editor?.undo.length;
    }
    if (el.studioEditorRedoButton) {
      el.studioEditorRedoButton.disabled = hasTransientChange || !editor?.redo.length;
    }
    if (el.studioEditorUpdateButton) el.studioEditorUpdateButton.disabled = !editor?.preview;
    if (el.studioEditorApplyButton) {
      el.studioEditorApplyButton.disabled = (
        Boolean(editor?.preview)
        || (!appliedPointCurve && !creatablePointCurve)
        || (!pointMode && !pointRangeReady)
      );
    }
    if (el.studioEditorSaveButton) {
      el.studioEditorSaveButton.disabled = (
        !['dirty', 'failed'].includes(saveState)
        || pointDraftDirty
      );
    }
    if (el.studioEditorOperationTitle) el.studioEditorOperationTitle.textContent = '포인트 편집';
    document.querySelectorAll(
      '.studio-editor-conversion-controls select, .studio-editor-conversion-controls input',
    ).forEach((control) => {
      control.disabled = Boolean(editor?.preview) || pointMode || selectedAxisPointBacked;
    });
    if (el.studioEditorFitSelectionButton) {
      el.studioEditorFitSelectionButton.disabled = pointMode;
    }
    if (el.studioEditorValueRangeLockButton) {
      const availableRange = selectedEditorMotionAxisRange();
      const locked = Boolean(editor?.valueRangeLock);
      el.studioEditorValueRangeLockButton.disabled = !locked && !availableRange;
      el.studioEditorValueRangeLockButton.textContent = locked
        ? '축 범위 해제'
        : '축 범위 고정';
      el.studioEditorValueRangeLockButton.setAttribute(
        'aria-pressed',
        locked ? 'true' : 'false',
      );
      el.studioEditorValueRangeLockButton.classList.toggle('on', locked);
      el.studioEditorValueRangeLockButton.title = locked
        ? `${editor.valueRangeLock.motionId} · `
          + `${editor.valueRangeLock.minValue}° ~ ${editor.valueRangeLock.maxValue}°`
        : availableRange
          ? `${availableRange.motionId} · `
            + `${availableRange.minValue}° ~ ${availableRange.maxValue}°`
          : '모션축 하나와 유효한 축 범위가 필요합니다';
    }
    if (el.studioEditorValueZoomInButton) {
      el.studioEditorValueZoomInButton.disabled = Boolean(editor?.valueRangeLock);
    }
    if (el.studioEditorValueZoomOutButton) {
      el.studioEditorValueZoomOutButton.disabled = Boolean(editor?.valueRangeLock);
    }
    if (el.studioEditorAddAxisButton) {
      el.studioEditorAddAxisButton.disabled = !el.studioEditorAddAxisSelect?.value
        || Boolean(editor?.preview);
    }
    if (el.studioEditorCopyAxisButton) {
      el.studioEditorCopyAxisButton.disabled = !el.studioEditorCopyAxisSource?.value
        || !el.studioEditorCopyAxisTarget?.value
        || Boolean(editor?.preview);
    }
    if (el.studioEditorDeleteAxisButton) {
      el.studioEditorDeleteAxisButton.disabled = !selectedIds.length
        || Boolean(editor?.preview);
    }
    if (el.studioEditorOperation) {
      el.studioEditorOperation.disabled = Boolean(editor?.preview);
    }
    el.studioEditorOperationButtons?.forEach((button) => {
      const active = button.dataset.studioEditorOperation === operation;
      button.setAttribute('aria-pressed', active ? 'true' : 'false');
      button.disabled = Boolean(editor?.preview) || !workingPointCurve;
    });
    if (el.studioEditorCreatePointsButton) {
      const selectedId = selectedIds.length === 1 ? selectedIds[0] : '';
      const alreadyPointBacked = selectedId
        && !layerPointCoverageIssues(editor?.working).includes(selectedId);
      el.studioEditorCreatePointsButton.disabled = (
        Boolean(editor?.preview)
        || pointMode
        || !selectedId
        || alreadyPointBacked
      );
    }
    document.querySelectorAll('[data-studio-editor-value]').forEach((field) => {
      const kind = field.dataset.studioEditorValue;
      field.classList.toggle('hidden', !(
        (operation === 'value_offset' && kind === 'offset')
        || ((operation === 'value_scale' || operation === 'time_scale') && kind === 'factor')
        || (operation === 'time_shift' && kind === 'delta')
        || (operation === 'point_curve' && kind === 'points')
      ));
    });
    if (el.studioEditorFactorLabel) {
      el.studioEditorFactorLabel.textContent = operation === 'time_scale'
        ? '시간 배율'
        : '동작 크기 배율';
    }
    if (el.studioEditorFactor) {
      el.studioEditorFactor.min = operation === 'value_scale' ? '-100' : '0.01';
      el.studioEditorFactor.max = '100';
    }
    if (el.studioEditorOperationHelp) {
      const help = {
        time_scale: '포인트 한 개는 0초, 두 개 이상은 첫 포인트를 기준으로 시간을 조절합니다.',
        value_scale: '포인트 한 개는 0°, 두 개 이상은 첫 포인트를 기준으로 모션값을 조절합니다. 음수는 곡선을 상하 반전합니다.',
        time_shift: '선택한 포인트 한 개 또는 포인트 범위를 시간축으로 이동합니다.',
        value_offset: '선택한 포인트 한 개 또는 포인트 범위의 모션값을 이동합니다.',
        point_curve: appliedPointCurve
          ? '작업본에 반영된 포인트 모션입니다. 포인트 추가·이동과 탄젠트 편집이 가능합니다.'
          : creatablePointCurve
            ? '새로 추가한 축입니다. 그래프를 클릭해 포인트를 두 개 이상 만드세요.'
            : '선택 축 전체에 포인트를 생성하고 작업본에 반영한 뒤 편집할 수 있습니다.',
      };
      el.studioEditorOperationHelp.textContent = help[operation] || '';
    }
    renderMotionStudioEditorPresentation(el, {
      saveState,
      savedAt: editor?.savedAt || '',
      saveError: editor?.saveError || '',
      inspector: motionStudioEditorInspectorState({
        preview: Boolean(editor?.preview),
        pointDraftUnsaved: pointDraftDirty
          || (workingPointCurve && !appliedPointCurve),
        appliedPointCurve: appliedPointCurve || selectedAppliedPointCurve,
        pointSelected: Boolean(selectedDraftPoint(editor)),
        rangeSelected: pointRangeReady,
      }),
      showDangerZone: pointMode && Boolean(editor?.pointDraft?.curve_id),
    });
    syncPointControls();
  }

  function drawEditorGraph() {
    const editor = state.editor;
    if (!editor) return;
    const displayedLayer = editor.preview || editor.working;
    drawMotionStudioEditorGraph({
      editor,
      canvas: el.studioEditorGraph,
      legend: el.studioEditorLegend,
      originalTrackMap: cachedLayerTracks(editor.original),
      workingTrackMap: cachedLayerTracks(displayedLayer),
      selectedMotionIds: editorSelectedMotionIds(),
      operation: el.studioEditorOperation?.value || '',
      selectionStartText: Number.isFinite(state.editor?.selectionStartSec)
        ? String(state.editor.selectionStartSec) : '',
      selectionEndText: Number.isFinite(state.editor?.selectionEndSec)
        ? String(state.editor.selectionEndSec) : '',
      devicePixelRatio: window.devicePixelRatio || 1,
    });
  }

  const editorGraphScheduler = createMotionStudioGraphScheduler({
    draw: drawEditorGraph,
    requestFrame: (callback) => window.requestAnimationFrame(callback),
    cancelFrame: (frameId) => window.cancelAnimationFrame(frameId),
  });
  const editorViewport = createMotionStudioEditorViewportController({
    el,
    getEditor: () => state.editor,
    drawGraph: drawEditorGraph,
    scheduleGraph: editorGraphScheduler.schedule,
    renderEditor,
    setMessage: setEditorMessage,
    resetValueView: resetEditorValueView,
    selectedMotionAxisRange: selectedEditorMotionAxisRange,
    selectedPointRange: selectedEditorPointRange,
    editorDuration,
  });

  function renderEditor() {
    if (!state.editor) return;
    renderEditorControls();
    renderEditorValidationDetails();
    drawEditorGraph();
  }

  function refreshEditorTimeline(layer, previousLayer = null) {
    const editor = state.editor;
    if (!synchronizeMotionStudioEditorTimeline(editor, layer, previousLayer)) return false;
    return true;
  }

  function discardEditorPreview(message = '') {
    const editor = state.editor;
    if (!editor) return false;
    if (!editor.preview) {
      if (!editor.operationReport) return false;
      editor.operationReport = null;
      editor.previewOperation = '';
      if (message) setEditorMessage(message);
      renderEditor();
      return true;
    }
    const preview = editor.preview;
    const discardedOperation = String(
      editor.previewOperation || editor.operationReport?.operation || '',
    );
    editor.preview = null;
    editor.previewValidation = null;
    editor.operationReport = null;
    editor.previewOperation = '';
    editor.pendingCurveId = '';
    refreshEditorTimeline(editor.working, preview);
    refreshEditorAxisControls(null, editor.working);
    if (discardedOperation === 'point_curve') {
      const stored = storedCurveForDraft(editor);
      if (stored) {
        loadPointDraft(stored, editor.selectedPointId);
        restoreEditorEditOperation(editor);
      }
    }
    if (el.studioEditorSubtitle) {
      el.studioEditorSubtitle.textContent = `편집 반영 ${editor.undo.length}회`;
    }
    if (message) setEditorMessage(message);
    renderEditor();
    return true;
  }

  function updateEditorWorkingCopy() {
    const editor = state.editor;
    if (!editor?.preview) {
      setEditorMessage('먼저 결과 미리보기 버튼으로 편집 결과를 확인하세요.', true);
      return;
    }
    const appliedOperation = String(
      editor.previewOperation || editor.operationReport?.operation || '',
    );
    const activeCurveId = appliedOperation === 'create_axis_point_curve'
      ? (editor.pendingCurveId || '')
      : (editor.pointDraft?.curve_id || editor.pendingCurveId || '');
    const selectedPointId = editor.selectedPointId;
    const previousIds = new Set(editorMotionIds(editor.working));
    const selectedIds = new Set(editorSelectedMotionIds());
    const previewIds = editorMotionIds(editor.preview);
    previewIds.forEach((motionId) => {
      if (!previousIds.has(motionId)) selectedIds.add(motionId);
    });
    editor.undo.push({
      layer: clone(editor.working),
      validation: clone(editor.validation),
      curveId: String(editor.pointDraft?.curve_id || ''),
      selectedPointId: String(editor.selectedPointId || ''),
    });
    editor.redo = [];
    editor.working = clone(editor.preview);
    editor.validation = clone(
      editor.previewValidation || { conflicts: [], transition_warnings: [], playable: true },
    );
    editor.preview = null;
    editor.previewValidation = null;
    editor.operationReport = null;
    editor.previewOperation = '';
    editor.pendingCurveId = '';
    if (appliedOperation === 'create_axis_point_curve') {
      editor.pointDraft = null;
      editor.selectedPointId = '';
    }
    if (
      appliedOperation === 'delete_axis'
      && editor.pointDraft
      && !editorMotionIds(editor.working).includes(String(editor.pointDraft.motion_id || ''))
    ) {
      editor.pointDraft = null;
      editor.selectedPointId = '';
    }
    if (activeCurveId) {
      const updatedCurve = editorPointCurves(editor.working).find(
        (curve) => curve.curve_id === activeCurveId,
      );
      if (updatedCurve) loadPointDraft(
        updatedCurve,
        appliedOperation === 'create_axis_point_curve' ? '' : selectedPointId,
      );
    }
    if (appliedOperation === 'point_curve') {
      restoreEditorEditOperation(editor);
    }
    refreshEditorAxisControls(selectedIds, editor.working);
    if (el.studioEditorSubtitle) {
      el.studioEditorSubtitle.textContent = `편집 반영 ${editor.undo.length}회`;
    }
    const appliedRangeWarningCount = editor.validation?.range_warnings?.length || 0;
    const appliedMessage = appliedOperation === 'create_axis_point_curve'
      ? '선택 축 전체 포인트 생성을 작업본에 반영했습니다. 바로 포인트를 편집할 수 있습니다.'
      : `편집 반영 ${editor.undo.length}회 완료 · 다음 편집을 계속할 수 있습니다.`;
    setEditorMessage(
      appliedMessage + (appliedRangeWarningCount
        ? ` · 축 설정 범위 초과 경고 ${appliedRangeWarningCount}건`
        : ''),
      appliedRangeWarningCount > 0,
    );
    renderEditor();
  }

  async function previewEditorAxisAddition() {
    const editor = state.editor;
    if (!editor) return;
    if (editor.preview) {
      setEditorMessage('현재 결과 미리보기를 먼저 편집 반영하거나 취소하세요.', true);
      return;
    }
    const motionId = String(el.studioEditorAddAxisSelect?.value || '').trim();
    const initialValue = Number(el.studioEditorAddAxisValue?.value);
    if (!motionId) {
      setEditorMessage('추가할 Motion ID를 입력하세요.', true);
      return;
    }
    if (!MOTION_ID_PATTERN.test(motionId)) {
      setEditorMessage('Motion ID는 양의 정수-양의 정수 형식으로 입력하세요. 예: 1-2', true);
      return;
    }
    if (!Number.isFinite(initialValue)) {
      setEditorMessage('축 초기 모션값을 숫자로 입력하세요.', true);
      return;
    }
    el.studioEditorAddAxisButton.disabled = true;
    try {
      const result = await editMotionStudioLayer({
        layer: editor.working,
        project: editorValidationProject(editor.working, [motionId]),
        operation: 'add_axis',
        motion_ids: [motionId],
        initial_value_deg: initialValue,
        mapping_rows: activeMapping()?.rows || [],
      });
      if (result.success === false) throw new Error(result.message || '축 추가 실패');
      editor.previewOperation = 'add_axis';
      editor.preview = clone(result.layer);
      refreshEditorTimeline(editor.preview, editor.working);
      editor.previewValidation = result.validation
        || { conflicts: [], transition_warnings: [], playable: true };
      refreshEditorAxisControls(new Set([motionId]), editor.preview);
      if (el.studioEditorSubtitle) {
        el.studioEditorSubtitle.textContent = '축 추가 미리보기 · 편집 반영 전';
      }
      setEditorMessage(
        `${motionId} 축 추가 미리보기 완료 · 초기값 ${initialValue.toFixed(3)}° · 확인 후 편집 반영하세요.`,
      );
      renderEditor();
    } catch (error) {
      setEditorMessage(error.message || String(error), true);
    } finally {
      renderEditorControls();
    }
  }

  async function previewEditorAxisCopy() {
    const editor = state.editor;
    if (!editor) return;
    if (editor.preview) {
      setEditorMessage('현재 결과 미리보기를 먼저 편집 반영하거나 취소하세요.', true);
      return;
    }
    const sourceMotionId = String(el.studioEditorCopyAxisSource?.value || '').trim();
    const targetMotionId = String(el.studioEditorCopyAxisTarget?.value || '').trim();
    if (!sourceMotionId || !targetMotionId) {
      setEditorMessage('복사할 원본 축을 선택하고 대상 Motion ID를 입력하세요.', true);
      return;
    }
    if (!MOTION_ID_PATTERN.test(targetMotionId)) {
      setEditorMessage('대상 Motion ID는 양의 정수-양의 정수 형식으로 입력하세요. 예: 1-2', true);
      return;
    }
    el.studioEditorCopyAxisButton.disabled = true;
    try {
      const result = await editMotionStudioLayer({
        layer: editor.working,
        project: editorValidationProject(
          editor.working, [sourceMotionId, targetMotionId],
        ),
        operation: 'copy_axis',
        source_motion_id: sourceMotionId,
        motion_ids: [targetMotionId],
        mapping_rows: activeMapping()?.rows || [],
      });
      if (result.success === false) throw new Error(result.message || '축 복사 실패');
      editor.previewOperation = 'copy_axis';
      editor.preview = clone(result.layer);
      refreshEditorTimeline(editor.preview, editor.working);
      editor.previewValidation = result.validation
        || { conflicts: [], transition_warnings: [], playable: true };
      refreshEditorAxisControls(new Set([targetMotionId]), editor.preview);
      if (el.studioEditorSubtitle) {
        el.studioEditorSubtitle.textContent = '축 복사 미리보기 · 편집 반영 전';
      }
      setEditorMessage(
        `${sourceMotionId} → ${targetMotionId} 축 복사 미리보기 완료 · 확인 후 편집 반영하세요.`,
      );
      renderEditor();
    } catch (error) {
      setEditorMessage(error.message || String(error), true);
    } finally {
      renderEditorControls();
    }
  }

  async function previewEditorAxisDeletion() {
    const editor = state.editor;
    if (!editor) return;
    if (editor.preview) {
      setEditorMessage('현재 결과 미리보기를 먼저 편집 반영하거나 취소하세요.', true);
      return;
    }
    if (pointDraftHasUnsavedChanges(editor)) {
      setEditorMessage('포인트 변경을 먼저 미리보기하고 작업본에 반영하세요.', true);
      return;
    }
    const motionIds = editorSelectedMotionIds();
    if (!motionIds.length) {
      setEditorMessage('삭제할 Motion ID를 선택하세요.', true);
      return;
    }
    const confirmed = await showConfirm(
      `현재 레이어에서 다음 축 데이터를 삭제할까요?\n${motionIds.join(', ')}`,
      {
        title: '선택 축 삭제',
        confirmLabel: '삭제 미리보기',
        tone: 'danger',
      },
    );
    if (!confirmed || state.editor !== editor) return;
    el.studioEditorDeleteAxisButton.disabled = true;
    try {
      const result = await editMotionStudioLayer({
        layer: editor.working,
        project: editorValidationProject(editor.working, motionIds),
        operation: 'delete_axis',
        motion_ids: motionIds,
        mapping_rows: activeMapping()?.rows || [],
      });
      if (result.success === false) throw new Error(result.message || '축 삭제 실패');
      editor.previewOperation = 'delete_axis';
      editor.preview = clone(result.layer);
      refreshEditorTimeline(editor.preview, editor.working);
      editor.previewValidation = result.validation
        || { conflicts: [], transition_warnings: [], range_warnings: [], playable: true };
      const remainingIds = new Set(editorMotionIds(editor.preview));
      refreshEditorAxisControls(remainingIds, editor.preview);
      if (el.studioEditorSubtitle) {
        el.studioEditorSubtitle.textContent = '축 삭제 미리보기 · 편집 반영 전';
      }
      setEditorMessage(
        `${motionIds.join(', ')} 축 삭제 미리보기 완료 · 확인 후 작업본에 반영하세요.`,
      );
      renderEditor();
    } catch (error) {
      setEditorMessage(error.message || String(error), true);
    } finally {
      renderEditorControls();
    }
  }

  async function applyEditorOperation(operationOverride = '') {
    const editor = state.editor;
    if (!editor) return;
    const operation = typeof operationOverride === 'string' && operationOverride
      ? operationOverride
      : (el.studioEditorOperation?.value || 'value_offset');
    const pointMetadataOperation = [
      'point_curve', 'create_axis_point_curve',
    ].includes(operation);
    const motionIds = editorSelectedMotionIds();
    if (!motionIds.length) { setEditorMessage('편집할 Motion ID를 선택하세요', true); return; }
    if (
      !pointMetadataOperation
      && !selectedEditorPointRange(editor)
    ) {
      setEditorMessage('같은 포인트 곡선에서 서로 다른 포인트 두 개를 선택하세요.', true);
      return;
    }
    if (operation === 'point_curve' && (!editor.pointDraft || editor.pointDraft.points.length < 2)) {
      setEditorMessage('그래프에서 같은 Motion ID의 포인트를 두 개 이상 만드세요.', true);
      return;
    }
    const payload = {
      layer: editor.working,
      project: editorValidationProject(editor.working, motionIds),
      operation,
      motion_ids: motionIds,
      start_sec: Number(editor.selectionStartSec || 0),
      end_sec: Number(editor.selectionEndSec || 0),
      offset_deg: Number(el.studioEditorOffset?.value || 0),
      factor: Number(el.studioEditorFactor?.value || 1),
      delta_sec: Number(el.studioEditorDelta?.value || 0) / 1000,
      interpolation_order: operation === 'point_curve'
        ? motionStudioPointCurveOrder(
          editor.pointDraft?.interpolation_order,
          editor.pointCurveOrder,
        )
        : 1,
      curve_id: editor.pendingCurveId || editor.pointDraft?.curve_id || '',
      points: operation === 'point_curve' ? clone(editor.pointDraft?.points || []) : [],
      approximation_tolerance_deg: Number(
        el.studioEditorApproximationTolerance?.value || 0.1,
      ),
      approximation_interpolation_order: Number(
        el.studioEditorApproximationOrder?.value || 3,
      ),
      approximation_maximum_points: Number(
        el.studioEditorApproximationMaximumPoints?.value || 50,
      ),
      mapping_rows: activeMapping()?.rows || [],
    };
    el.studioEditorApplyButton.disabled = true;
    if (el.studioEditorUpdateButton) el.studioEditorUpdateButton.disabled = true;
    try {
      const result = await editMotionStudioLayer(payload);
      if (result.success === false) throw new Error(result.message || '편집 실패');
      editor.operationReport = result.operation_report || null;
      editor.previewOperation = operation;
      editor.preview = clone(result.layer);
      refreshEditorTimeline(editor.preview, editor.working);
      if (operation === 'point_curve' && editor.pointDraft) {
        const calculated = editorPointCurves(editor.preview).find(
          (curve) => curve.curve_id === editor.pointDraft.curve_id,
        );
        if (calculated) loadPointDraft(calculated, editor.selectedPointId);
      }
      editor.previewValidation = result.validation
        || { conflicts: [], transition_warnings: [], playable: true };
      if (el.studioEditorSubtitle) {
        el.studioEditorSubtitle.textContent = '결과 미리보기 · 편집 반영 전';
      }
      const issueCount = (editor.previewValidation.conflicts?.length || 0)
        + (editor.previewValidation.transition_warnings?.length || 0);
      const rangeWarningCount = editor.previewValidation.range_warnings?.length || 0;
      const rangeWarningText = rangeWarningCount
        ? ` · 축 설정 범위 초과 경고 ${rangeWarningCount}건 (계속 진행 가능)`
        : '';
      if (operation === 'create_axis_point_curve') {
        const report = editor.operationReport || {};
        setEditorMessage(
          `선택 축 전체 포인트 생성 미리보기 · ${Number(report.interpolation_order || 1)}차 곡선 · `
          + `${Number(report.source_sample_count || 0)}개 모션점 → `
          + `${Number(report.point_count || 0)}개 포인트 · 최대 오차 `
          + `${Number(report.maximum_error_deg || 0).toFixed(4)}° · `
          + `편집 반영 후 바로 포인트를 편집할 수 있습니다.${rangeWarningText}`,
          rangeWarningCount > 0,
        );
      } else {
        const previewMessage = issueCount
          ? `결과 미리보기 · 충돌 또는 급변 ${issueCount}건 · 확인 후 값을 바꾸거나 편집 반영하세요${rangeWarningText}`
          : `결과 미리보기 완료 · 결과가 맞으면 편집 반영을 누르세요.${rangeWarningText}`;
        setEditorMessage(
          previewMessage,
          issueCount > 0 || rangeWarningCount > 0,
        );
      }
      renderEditor();
    } catch (error) {
      if (operation === 'create_axis_point_curve') editor.pendingCurveId = '';
      setEditorMessage(error.message || String(error), true);
    } finally {
      el.studioEditorApplyButton.disabled = false;
      renderEditorControls();
    }
  }

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
    setBusy(true);
    setMessage('요청 처리 중입니다…');
    try {
      const result = await action();
      if (!isCurrent()) return null;
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
      if (!isCurrent()) return null;
      setMessage(error.message || String(error), true);
      onError?.(error);
      return null;
    } finally {
      if (isCurrent()) setBusy(false);
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
    try {
      const result = await fetchMotionStudio();
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
    el.studioLayerRows?.addEventListener('change', async (event) => {
      const row = event.target.closest('tr[data-studio-layer-id]');
      if (!row) return;
      state.selectedLayerId = row.dataset.studioLayerId;
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
    });
    el.studioLayerRows?.addEventListener('click', (event) => {
      if (event.target.closest('.studio-playback-choice')) return;
      const row = event.target.closest('tr[data-studio-layer-id]');
      if (!row) return;
      state.selectedLayerId = row.dataset.studioLayerId;
      el.studioLayerRows.querySelectorAll('tr[data-studio-layer-id]').forEach((item) => {
        item.classList.toggle('selected-row', item.dataset.studioLayerId === state.selectedLayerId);
      });
      renderSelectedLayerActions();
    });

    el.studioLayerManagerOpenButton?.addEventListener('click', openLayerManager);
    el.studioLayerManagerCloseButton?.addEventListener('click', closeLayerManager);
    el.studioLayerManagerTabs?.addEventListener('click', (event) => {
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
    });
    el.studioManagerLayerRows?.addEventListener('click', (event) => {
      const row = event.target.closest('tr[data-manager-layer-id]');
      if (!row) return;
      state.selectedLayerId = row.dataset.managerLayerId;
      el.studioManagerLayerRows.querySelectorAll('tr[data-manager-layer-id]').forEach((item) => {
        const selected = item.dataset.managerLayerId === state.selectedLayerId;
        item.classList.toggle('selected-row', selected);
        const radio = item.querySelector('[data-manager-layer-select]');
        if (radio) radio.checked = selected;
      });
      renderSelectedLayerActions();
    });
    el.studioManagerLayerRows?.addEventListener('change', (event) => {
      const row = event.target.closest('tr[data-manager-layer-id]');
      if (!row) return;
      state.selectedLayerId = row.dataset.managerLayerId;
      renderLayerManager();
      renderSelectedLayerActions();
    });
    el.studioManagerMergeRows?.addEventListener('change', (event) => {
      if (!event.target.matches('[data-manager-layer-merge]')) return;
      const row = event.target.closest('tr[data-manager-merge-layer-id]');
      if (!row) return;
      const layerId = row.dataset.managerMergeLayerId;
      if (event.target.checked) state.mergeLayerIds.add(layerId);
      else state.mergeLayerIds.delete(layerId);
      if (!state.mergeLayerIds.has(state.mergeAppendLayerId)) {
        state.mergeAppendLayerId = '';
      }
      state.mergeResultMessage = '';
      state.mergeResultError = false;
      renderLayerManager();
      renderMergeControl();
    });
    el.studioMergeMode?.addEventListener('change', () => {
      state.mergeMode = el.studioMergeMode.value === 'append' ? 'append' : 'preserve';
      if (state.mergeMode !== 'append') state.mergeAppendLayerId = '';
      state.mergeResultMessage = '';
      state.mergeResultError = false;
      renderLayerManager();
      renderMergeControl();
    });
    el.studioMergeAppendLayer?.addEventListener('change', () => {
      const layerId = String(el.studioMergeAppendLayer.value || '');
      state.mergeAppendLayerId = state.mergeLayerIds.has(layerId) ? layerId : '';
      state.mergeResultMessage = '';
      state.mergeResultError = false;
      renderMergeControl();
    });
    el.studioSelectedLayerDetailButton?.addEventListener('click', () => {
      if (!selectedLayer()) return;
      state.layerDetailMode = 'layer';
      showLayerGraph();
    });
    el.studioSelectedLayerCopyButton?.addEventListener('click', async () => {
      const layer = selectedLayer();
      if (!layer || !await showConfirm(
        `선택한 '${layer.name}'을 복사할까요?\n복사본은 재생 미선택 상태로 생성됩니다.`,
        { title: '레이어 복사', confirmLabel: '복사' },
      )) return;
      const result = await run(() => duplicateMotionStudioLayer(layer.layer_id));
      if (!result?.layer_id) return;
      state.selectedLayerId = result.layer_id;
      render();
    });
    el.studioSelectedLayerEditButton?.addEventListener('click', () => {
      openLayerEditor(selectedLayer());
    });
    el.studioSelectedLayerLockButton?.addEventListener('click', () => {
      const layer = selectedLayer();
      if (!layer) return;
      run(() => updateMotionStudioLayer({
        layer_id: layer.layer_id,
        locked: !layer.locked,
      }));
    });
    el.studioSelectedLayerDeleteButton?.addEventListener('click', async () => {
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
    });
    el.studioPlaybackGraphButton?.addEventListener('click', () => {
      showLayerGraph({ composition: true });
    });
    el.studioCompositionDetailButton?.addEventListener('click', () => {
      state.layerDetailMode = 'composition';
      renderLayerDetail();
    });
    el.studioLayerDetailTabs?.addEventListener('click', (event) => {
      const button = event.target.closest('[data-studio-layer-detail-tab]');
      if (!button) return;
      state.activeLayerDetailTab = button.dataset.studioLayerDetailTab || 'graph';
      renderLayerDetail();
    });
    el.studioMergeButton?.addEventListener('click', async () => {
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
    });
    const selectEditorAxes = (checked) => {
      if (protectPointDraftAxisSelection()) return;
      el.studioEditorAxisList?.querySelectorAll('input').forEach((input) => { input.checked = checked; });
      resetEditorValueView({ unlock: true });
      clearPendingPointCandidate();
      clearEditorPointRange(state.editor);
      if (!discardEditorPreview('축 선택이 바뀌어 결과 미리보기를 취소했습니다.')) renderEditor();
    };
    el.studioEditorSelectAllButton?.addEventListener('click', () => selectEditorAxes(true));
    el.studioEditorSelectNoneButton?.addEventListener('click', () => selectEditorAxes(false));
    el.studioEditorAddAxisSelect?.addEventListener('input', renderEditorControls);
    el.studioEditorAddAxisButton?.addEventListener('click', previewEditorAxisAddition);
    el.studioEditorCopyAxisSource?.addEventListener('change', renderEditorControls);
    el.studioEditorCopyAxisTarget?.addEventListener('input', renderEditorControls);
    el.studioEditorCopyAxisButton?.addEventListener('click', previewEditorAxisCopy);
    el.studioEditorDeleteAxisButton?.addEventListener('click', previewEditorAxisDeletion);
    el.studioEditorAxisList?.addEventListener('change', () => {
      if (protectPointDraftAxisSelection()) return;
      resetEditorValueView({ unlock: true });
      clearPendingPointCandidate();
      clearEditorPointRange(state.editor);
      if (!discardEditorPreview('축 선택이 바뀌어 결과 미리보기를 취소했습니다.')) renderEditor();
    });
    el.studioEditorOperationButtons?.forEach((button) => {
      button.addEventListener('click', () => {
        const operation = button.dataset.studioEditorOperation || '';
        if (!operation || !el.studioEditorOperation || button.disabled) return;
        el.studioEditorOperation.value = operation;
        el.studioEditorOperation.dispatchEvent(new Event('change', { bubbles: true }));
        const inputByOperation = {
          time_shift: el.studioEditorDelta,
          time_scale: el.studioEditorFactor,
          value_offset: el.studioEditorOffset,
          value_scale: el.studioEditorFactor,
        };
        window.requestAnimationFrame(() => inputByOperation[operation]?.focus());
      });
    });
    el.studioEditorOperation?.addEventListener('change', () => {
      const editor = state.editor;
      if (!editor) return;
      const nextOperation = el.studioEditorOperation.value;
      const previousOperation = editor.operation;
      if (editor.preview) {
        el.studioEditorOperation.value = editor.operation || 'time_scale';
        setEditorMessage(
          '현재 결과 미리보기를 먼저 편집 반영한 뒤 편집 기능을 바꾸세요.',
          true,
        );
        renderEditor();
        return;
      }
      if (
        editor.operation === 'point_curve'
        && nextOperation !== 'point_curve'
        && pointDraftHasUnsavedChanges(editor)
      ) {
        el.studioEditorOperation.value = 'point_curve';
        setEditorMessage(
          '변경한 포인트를 먼저 결과 미리보기하고 편집 반영한 뒤 다른 기능으로 이동하세요.',
          true,
        );
        renderEditor();
        return;
      }
      const linkedCurve = (
        previousOperation === 'point_curve' && nextOperation !== 'point_curve'
      ) ? storedCurveForDraft(editor) : null;
      if (
        nextOperation === 'point_curve'
        && POINT_RANGE_EDIT_OPERATIONS.has(previousOperation)
      ) {
        rememberEditorEditOperation(editor, previousOperation);
        editor.pointModeReturnOperation = previousOperation;
      } else if (POINT_RANGE_EDIT_OPERATIONS.has(nextOperation)) {
        rememberEditorEditOperation(editor, nextOperation);
        editor.pointModeReturnOperation = '';
      }
      editor.operation = nextOperation;
      clearPendingPointCandidate(editor);
      if (linkedCurve) {
        const points = linkedCurve.points || [];
        setEditorPointRange(
          editor,
          Number(points[0]?.time_sec || 0),
          Number(points[points.length - 1]?.time_sec || 0),
          linkedCurve.motion_id,
          linkedCurve.curve_id,
        );
        editor.selectionStage = 0;
        editor.selectionAnchor = null;
      }
      if (editor.operation === 'point_curve') {
        editor.pointTimelineEnd = motionStudioPointCurveViewEnd(
          editorDuration(editor.working),
          editor.viewEnd,
          editor.pointTimelineEnd,
        );
        editor.viewStart = 0;
        editor.viewEnd = editor.pointTimelineEnd;
      }
      renderEditor();
      if (linkedCurve) {
        setEditorMessage(
          `${linkedCurve.motion_id}의 포인트 전체를 선택했습니다. `
          + '시간·모션값 편집 시 포인트와 탄젠트도 함께 변경됩니다.',
        );
      }
    });
    const updateSelectedPointFromControls = () => {
      const editor = state.editor;
      const point = selectedDraftPoint(editor);
      if (!editor || !point) return;
      const timeSec = Number(el.studioEditorPointTime?.value);
      const valueDeg = Number(el.studioEditorPointValue?.value);
      const tangentMode = el.studioEditorPointMode?.value || 'auto';
      discardEditorPreview('포인트 값이 바뀌어 결과 미리보기를 취소했습니다.');
      const result = updateMotionStudioDraftPoint(editor, point, {
        timeSec,
        valueDeg,
        tangentMode,
      });
      if (!result.ok) {
        setEditorMessage(
          result.reason === 'time_conflict'
            ? '같은 시간에는 포인트를 하나만 만들 수 있습니다.'
            : '포인트를 변경할 수 없습니다.',
          true,
        );
        syncPointControls();
        return;
      }
      if (Number.isFinite(timeSec)) {
        if (point.time_sec > editor.viewEnd) {
          editor.pointTimelineEnd = motionStudioPointCurveViewEnd(
            editorDuration(editor.working),
            editor.viewEnd,
            point.time_sec + Math.max(1, point.time_sec * 0.05),
          );
          editor.viewEnd = editor.pointTimelineEnd;
        }
      }
      clearEditorPointRange(editor);
      setEditorMessage('포인트 변경 완료 · 결과 미리보기를 눌러 곡선을 다시 계산하세요.');
      renderEditor();
    };
    el.studioEditorPointTime?.addEventListener('change', updateSelectedPointFromControls);
    el.studioEditorPointValue?.addEventListener('change', updateSelectedPointFromControls);
    el.studioEditorPointMode?.addEventListener('change', updateSelectedPointFromControls);
    el.studioEditorPointCurveOrder?.addEventListener('change', () => {
      const editor = state.editor;
      if (!editor) return;
      const interpolationOrder = motionStudioPointCurveOrder(
        el.studioEditorPointCurveOrder?.value,
        editor.pointCurveOrder,
      );
      editor.pointCurveOrder = interpolationOrder;
      if (editor.pointDraft) editor.pointDraft.interpolation_order = interpolationOrder;
      discardEditorPreview('곡선 방식이 바뀌어 결과 미리보기를 취소했습니다.');
      setEditorMessage(`${interpolationOrder === 1 ? '직선' : `${interpolationOrder}차 곡선`} 선택 · 결과 미리보기를 눌러 다시 계산하세요.`);
      renderEditor();
    });
    el.studioEditorPointTimelineEnd?.addEventListener('change', () => {
      const editor = state.editor;
      if (!editor) return;
      const requested = Number(el.studioEditorPointTimelineEnd.value);
      if (!Number.isFinite(requested) || requested <= 0) {
        setEditorMessage('포인트 작업 시간축 끝은 0보다 큰 시간을 입력하세요.', true);
        syncPointControls();
        return;
      }
      editor.pointTimelineEnd = Math.max(MOTION_STUDIO_PERIOD_SEC, requested);
      editor.viewStart = 0;
      editor.viewEnd = editor.pointTimelineEnd;
      setEditorMessage(
        `포인트 작업 시간축을 0초~${editor.pointTimelineEnd.toFixed(2)}초로 표시합니다.`,
      );
      renderEditor();
    });
    el.studioEditorPointAddButton?.addEventListener('click', () => {
      const editor = state.editor;
      const candidate = editor?.pendingPointCandidate;
      if (!editor || !candidate) {
        setEditorMessage('그래프에서 추가할 위치를 먼저 선택하세요.', true);
        return;
      }
      const selectedIds = editorSelectedMotionIds();
      if (selectedIds.length !== 1 || selectedIds[0] !== candidate.motionId) {
        clearPendingPointCandidate(editor);
        setEditorMessage('포인트를 추가할 Motion ID 하나를 다시 선택하세요.', true);
        renderEditor();
        return;
      }
      if (
        !pointCurveIsApplied(editor, editor.pointDraft?.curve_id)
        && !pointCurveCanBeCreated(editor)
      ) {
        setEditorMessage(
          editor.pointDraft
            ? '생성된 포인트를 먼저 작업본에 반영하세요.'
            : '선택 축 전체에 포인트를 생성하고 작업본에 반영한 뒤 편집하세요.',
          true,
        );
        return;
      }
      const result = addMotionStudioDraftPoint(editor, candidate, {
        curveId: editorId('curve'),
        pointId: editorId('point'),
        interpolationOrder: editor.pointCurveOrder,
      });
      if (!result.ok) {
        clearPendingPointCandidate(editor);
        setEditorMessage('같은 시간에는 포인트를 하나만 만들 수 있습니다.', true);
        renderEditor();
        return;
      }
      const { point } = result;
      clearPendingPointCandidate(editor);
      clearEditorPointRange(editor);
      setEditorMessage(
        `${candidate.motionId} 포인트 추가 · `
        + `${point.time_sec.toFixed(2)}초 · ${point.value_deg.toFixed(3)}°`,
      );
      renderEditor();
    });
    el.studioEditorPointDeleteButton?.addEventListener('click', () => {
      const editor = state.editor; const point = selectedDraftPoint(editor);
      if (!editor?.pointDraft || !point) return;
      const result = deleteMotionStudioDraftPoint(editor, point.point_id);
      if (!result.ok) {
        setEditorMessage(
          '곡선을 유지하려면 포인트가 최소 2개 필요하므로 더 삭제할 수 없습니다.',
          true,
        );
        return;
      }
      discardEditorPreview();
      clearEditorPointRange(editor);
      setEditorMessage('포인트를 작업본에서 제거했습니다 · 결과 계산 전에는 저장되지 않습니다.');
      renderEditor();
    });
    el.studioEditorRangeCopyButton?.addEventListener('click', () => {
      const editor = state.editor;
      const selectedRange = selectedEditorPointRange(editor);
      if (!editor || !selectedRange) {
        setEditorMessage('같은 포인트 곡선의 서로 다른 포인트 두 개를 선택하세요.', true);
        return;
      }
      const targetStartSec = Number(el.studioEditorRangeCopyTarget?.value);
      const result = motionStudioCopyPointRange(
        selectedRange.curve,
        editor.selectionStartSec,
        editor.selectionEndSec,
        targetStartSec,
      );
      if (!result.ok) {
        const errors = {
          invalid_range: '복사할 포인트 구간을 다시 선택하세요.',
          invalid_target: '복사 시작 시간은 0초 이상의 20ms 단위 값이어야 합니다.',
          time_conflict: '복사 위치에 기존 포인트가 있습니다. 겹치지 않는 시간을 입력하세요.',
        };
        setEditorMessage(errors[result.reason] || '포인트 구간을 복사할 수 없습니다.', true);
        return;
      }
      discardEditorPreview();
      const copiedPoints = applyMotionStudioCopiedPointRange(
        editor,
        selectedRange.curve,
        result,
        () => editorId('point'),
      );
      activatePointDraftMutation(
        editor,
        `구간 복사 완료 · ${copiedPoints.length}개 포인트 · `
          + `${result.startSec.toFixed(2)}초 ~ ${result.endSec.toFixed(2)}초 · `
          + '결과 미리보기로 곡선을 확인하세요.',
      );
    });
    el.studioEditorRangeDeleteButton?.addEventListener('click', () => {
      const editor = state.editor;
      const selectedRange = selectedEditorPointRange(editor);
      if (!editor || !selectedRange) {
        setEditorMessage('삭제할 포인트 구간을 다시 선택하세요.', true);
        return;
      }
      const result = motionStudioDeletePointRange(
        selectedRange.curve,
        editor.selectionStartSec,
        editor.selectionEndSec,
      );
      if (!result.ok) {
        setEditorMessage(
          result.reason === 'minimum_points'
            ? '곡선을 유지하려면 삭제 후 포인트가 최소 2개 남아야 합니다.'
            : '삭제할 포인트 구간을 다시 선택하세요.',
          true,
        );
        return;
      }
      discardEditorPreview();
      applyMotionStudioDeletedPointRange(editor, selectedRange.curve, result);
      activatePointDraftMutation(
        editor,
        `구간 삭제 완료 · ${result.deletedCount}개 포인트 · `
          + '결과 미리보기로 곡선을 확인하세요.',
      );
    });
    [el.studioEditorOffset, el.studioEditorFactor, el.studioEditorDelta].forEach((input) => {
      input?.addEventListener('input', () => {
        discardEditorPreview('편집값이 바뀌어 결과 미리보기를 취소했습니다. 다시 계산하세요.');
      });
    });
    el.studioEditorCreatePointsButton?.addEventListener('click', () => {
      const editor = state.editor;
      if (!editor) return;
      if (editorSelectedMotionIds().length !== 1) {
        setEditorMessage('전체 포인트를 생성할 Motion ID를 하나만 선택하세요.', true);
        return;
      }
      editor.pendingCurveId = editorId('curve');
      applyEditorOperation('create_axis_point_curve');
    });
    el.studioEditorApplyButton?.addEventListener('click', () => applyEditorOperation());
    el.studioEditorUpdateButton?.addEventListener('click', updateEditorWorkingCopy);
    const discardEditor = async () => {
      if (
        (
          state.editor?.preview
          || (
            state.editor
            && !motionStudioLayerDataEqual(
              state.editor.original,
              state.editor.working,
            )
          )
          || pointDraftHasUnsavedChanges(state.editor)
        )
        && !await showConfirm(
          '저장하지 않은 편집 내용과 결과 미리보기를 버리고 닫을까요?',
          { title: '레이어 편집 닫기', confirmLabel: '변경 버리기', tone: 'warning' },
        )
      ) return;
      closeLayerEditor();
    };
    el.studioEditorCloseButton?.addEventListener('click', discardEditor);
    el.studioEditorUndoButton?.addEventListener('click', () => {
      const editor = state.editor;
      if (!editor) return;
      if (editor.preview) {
        discardEditorPreview('결과 미리보기를 취소하고 현재 작업본으로 돌아왔습니다.');
        return;
      }
      if (pointDraftHasUnsavedChanges(editor)) {
        const stored = storedCurveForDraft(editor);
        if (stored) {
          loadPointDraft(stored, editor.selectedPointId);
          setEditorMessage('편집 반영 전 포인트 변경을 취소했습니다.');
        } else {
          editor.pointDraft = null;
          editor.selectedPointId = '';
          setEditorMessage('편집 반영 전 포인트 작업을 취소했습니다.');
        }
        restoreEditorEditOperation(editor);
        renderEditor();
        return;
      }
      if (!editor.undo.length) return;
      editor.redo.push({
        layer: clone(editor.working),
        validation: clone(editor.validation),
        curveId: String(editor.pointDraft?.curve_id || ''),
        selectedPointId: String(editor.selectedPointId || ''),
      });
      const previous = editor.undo.pop();
      const replacedLayer = editor.working;
      editor.working = previous.layer;
      editor.validation = previous.validation;
      const previousCurve = editorPointCurves(editor.working).find(
        (curve) => String(curve.curve_id || '') === String(previous.curveId || ''),
      );
      if (previousCurve) {
        loadPointDraft(previousCurve, previous.selectedPointId);
      } else {
        editor.pointDraft = null;
        editor.selectedPointId = '';
      }
      refreshEditorTimeline(editor.working, replacedLayer);
      refreshEditorAxisControls(null, editor.working);
      if (el.studioEditorSubtitle) {
        el.studioEditorSubtitle.textContent = `편집 반영 ${editor.undo.length}회`;
      }
      setEditorMessage('직전 편집을 취소했습니다'); renderEditor();
    });
    el.studioEditorRedoButton?.addEventListener('click', () => {
      const editor = state.editor;
      if (!editor?.redo.length) return;
      discardEditorPreview();
      editor.undo.push({
        layer: clone(editor.working),
        validation: clone(editor.validation),
        curveId: String(editor.pointDraft?.curve_id || ''),
        selectedPointId: String(editor.selectedPointId || ''),
      });
      const following = editor.redo.pop();
      const replacedLayer = editor.working;
      editor.working = following.layer;
      editor.validation = following.validation;
      const followingCurve = editorPointCurves(editor.working).find(
        (curve) => String(curve.curve_id || '') === String(following.curveId || ''),
      );
      if (followingCurve) {
        loadPointDraft(followingCurve, following.selectedPointId);
      } else {
        editor.pointDraft = null;
        editor.selectedPointId = '';
      }
      refreshEditorTimeline(editor.working, replacedLayer);
      refreshEditorAxisControls(null, editor.working);
      if (el.studioEditorSubtitle) {
        el.studioEditorSubtitle.textContent = `편집 반영 ${editor.undo.length}회`;
      }
      setEditorMessage('취소한 편집을 다시 반영했습니다'); renderEditor();
    });
    const acceptSavedEditorLayer = (editor, savedLayer, message, validation = null) => {
      const previousWorking = editor.working;
      const selectedPointId = editor.selectedPointId;
      const activeCurveId = editor.pointDraft?.curve_id;
      editor.original = clone(savedLayer);
      editor.working = clone(savedLayer);
      editor.preview = null;
      editor.previewValidation = null;
      if (validation) editor.validation = clone(validation);
      editor.operationReport = null;
      editor.previewOperation = '';
      editor.undo = [];
      editor.redo = [];
      editor.saveState = 'saved';
      editor.savedAt = new Date().toLocaleTimeString('ko-KR', {
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit',
      });
      editor.saveError = '';
      editor.saveFailureFingerprint = '';
      refreshEditorTimeline(editor.working, previousWorking);
      refreshEditorAxisControls(null, editor.working);
      if (activeCurveId && el.studioEditorOperation?.value === 'point_curve') {
        const savedCurve = editorPointCurves(editor.working).find(
          (curve) => curve.curve_id === activeCurveId,
        );
        if (savedCurve) loadPointDraft(savedCurve, selectedPointId);
        else {
          editor.pointDraft = null;
          editor.selectedPointId = '';
        }
      }
      if (el.studioEditorSubtitle) {
        el.studioEditorSubtitle.textContent = '편집 반영 0회';
      }
      setEditorMessage(message);
      renderEditor();
    };
    el.studioEditorSaveButton?.addEventListener('click', async () => {
      const editor = state.editor;
      if (!editor) return;
      if (editor.preview) {
        setEditorMessage('결과 미리보기를 먼저 편집 반영한 뒤 저장하세요.', true);
        return;
      }
      if (pointDraftHasUnsavedChanges(editor)) {
        setEditorMessage(
          '포인트 변경을 먼저 결과 미리보기하고 편집 반영한 뒤 저장하세요.',
          true,
        );
        return;
      }
      if (motionStudioLayerDataEqual(editor.original, editor.working)) {
        setEditorMessage('저장할 변경사항이 없습니다.');
        renderEditor();
        return;
      }
      const confirmed = await requestMotionStudioEditorSave(el, {
        layerName: editor.working?.name || editor.original?.name || editor.layerId,
        editCount: editor.undo.length,
        pointCurvesChanged: JSON.stringify(editor.original?.point_curves || [])
          !== JSON.stringify(editor.working?.point_curves || []),
        warningCount: editor.validation?.range_warnings?.length || 0,
      });
      if (!confirmed || state.editor !== editor) return;
      editor.saveState = 'saving';
      editor.saveError = '';
      renderEditor();
      const result = await run(() => saveMotionStudioLayerData({
        layer_id: editor.layerId,
        original_revision: Number(editor.original.edit_revision || 0),
        layer: editor.working,
      }), {
        onError: (error) => {
          if (state.editor !== editor) return;
          editor.saveState = 'failed';
          editor.saveError = error.message || String(error);
          editor.saveFailureFingerprint = motionStudioEditorFailureFingerprint(editor);
          setEditorMessage(`저장 실패 · ${editor.saveError}`, true);
          renderEditor();
        },
      });
      if (result) {
        const savedLayer = state.project?.layers?.find(
          (layer) => layer.layer_id === editor.layerId,
        ) || editor.working;
        acceptSavedEditorLayer(
          editor,
          savedLayer,
          result.range_warnings?.length
            ? `저장 완료 · 축 설정 범위 초과 경고 ${result.range_warnings.length}건 · 계속 편집할 수 있습니다.`
            : '저장 완료 · 창을 닫지 않고 편집을 계속할 수 있습니다.',
          {
            ...(editor.validation || {}),
            range_warnings: result.range_warnings || [],
          },
        );
        return;
      }
      const currentLayer = state.project?.layers?.find(
        (layer) => layer.layer_id === editor.layerId,
      );
      const originalRevision = Number(editor.original.edit_revision || 0);
      const currentRevision = Number(currentLayer?.edit_revision || 0);
      if (
        currentLayer
        && currentRevision !== originalRevision
        && motionStudioLayerDataEqual(currentLayer, editor.working)
      ) {
        acceptSavedEditorLayer(
          editor,
          currentLayer,
          '같은 편집 결과가 이미 저장되어 현재 작업본을 동기화했습니다.',
        );
        return;
      }
      if (currentLayer && currentRevision !== originalRevision) {
        editor.saveState = 'failed';
        editor.saveError = '저장 중 원본 레이어가 변경되었습니다.';
        setEditorMessage(
          '저장 중 원본 레이어가 변경되었습니다. 현재 작업은 저장되지 않았습니다. '
          + '편집 창을 닫고 최신 레이어를 다시 열어 작업하세요.',
          true,
        );
        renderEditor();
        return;
      }
      if (editor.saveState !== 'failed') {
        editor.saveState = 'failed';
        editor.saveError = '레이어를 저장하지 못했습니다.';
        editor.saveFailureFingerprint = motionStudioEditorFailureFingerprint(editor);
        setEditorMessage('저장 실패 · 현재 작업본은 유지됩니다.', true);
        renderEditor();
      }
    });
    editorViewport.bind();
    el.studioEditorGraph?.addEventListener('mousemove', (event) => {
      const editor = state.editor; const metrics = editor?.graphMetrics;
      if (!editor || !metrics) return;
      const rect = el.studioEditorGraph.getBoundingClientRect();
      const { x, y } = motionStudioCanvasEventPoint(
        rect, event.clientX, event.clientY, metrics.width, metrics.height,
      );
      if (editor.draggingPoint) {
        if (!motionStudioPointDragStarted(editor.draggingPoint, x, y)) return;
        if (!editor.draggingPoint.activated) {
          const pendingDrag = { ...editor.draggingPoint, activated: true };
          if (!selectPointCurveFromGraph(
            pendingDrag.curve,
            pendingDrag.pointId,
          )) {
            editor.draggingPoint = null;
            return;
          }
          editor.draggingPoint = pendingDrag;
        }
        const point = selectedDraftPoint(editor);
        if (!point) return;
        const activeMetrics = editor.graphMetrics || metrics;
        motionStudioMoveDraftPoint(editor, point, x, y, activeMetrics);
        clearEditorPointRange(editor);
        editor.draggingPoint.moved = true;
        editor.suppressGraphClick = true;
        syncPointControls();
        editorGraphScheduler.schedule();
        return;
      }
      if (editor.panningGraph) {
        const nextView = motionStudioPanEditorGraph(editor, metrics, x, y);
        if (nextView) {
          editor.suppressGraphClick = true;
          editorViewport.setView(nextView.viewStart, nextView.viewEnd, true);
        }
        return;
      }
      if (editor.draggingHandle) {
        const point = selectedDraftPoint(editor);
        if (!point) return;
        const side = editor.draggingHandle.side;
        motionStudioMoveTangentHandle(point, side, x, y, metrics);
        clearEditorPointRange(editor);
        editor.suppressGraphClick = true;
        syncPointControls();
        editorGraphScheduler.schedule();
        return;
      }
      if (!motionStudioGraphPointInside(metrics, x, y)) {
        editor.cursor = null;
        if (el.studioEditorCursorInfo) el.studioEditorCursorInfo.textContent = '그래프 안쪽에서 지점을 선택하세요';
        editorGraphScheduler.schedule();
        return;
      }
      const rawValue = metrics.valueFor(y);
      const timeSec = motionStudioSnapFrameTime(metrics.timeFor(x));
      const nearest = motionStudioMotionTargetAtTime(
        cachedLayerTracks(editor.preview || editor.working),
        editorSelectedMotionIds(),
        timeSec,
        rawValue,
      );
      const cursorX = metrics.xFor(timeSec);
      const cursorY = nearest ? metrics.yFor(nearest.value) : y;
      const value = nearest ? nearest.value : rawValue;
      editor.cursor = {
        x: cursorX,
        y: cursorY,
        timeSec,
        value,
        nearest,
      };
      if (el.studioEditorCursorInfo) el.studioEditorCursorInfo.textContent = nearest
        ? `${nearest.motionId} · ${nearest.timeSec.toFixed(3)}초 · ${nearest.value.toFixed(3)}°`
        : `${timeSec.toFixed(3)}초 · ${value.toFixed(3)}°`;
      editorGraphScheduler.schedule();
    });
    el.studioEditorGraph?.addEventListener('mouseleave', () => {
      if (state.editor) state.editor.cursor = null;
      if (el.studioEditorCursorInfo) el.studioEditorCursorInfo.textContent = '그래프 위에 마우스를 올리세요';
      editorGraphScheduler.schedule();
    });
    el.studioEditorGraph?.addEventListener('click', (event) => {
      const editor = state.editor;
      const metrics = editor?.graphMetrics;
      if (!editor || !metrics) return;
      if (editor.suppressGraphClick) {
        editor.suppressGraphClick = false;
        return;
      }
      // 하나의 실제 클릭이 중복 처리되거나 같은 위치에서 더블클릭된 경우에는
      // 두 번째 지점으로 확정하지 않는다.
      if (event.motionStudioRangeHandled) return;
      event.motionStudioRangeHandled = true;
      const rect = el.studioEditorGraph.getBoundingClientRect();
      const clickPoint = {
        ...motionStudioCanvasEventPoint(
          rect, event.clientX, event.clientY, metrics.width, metrics.height,
        ),
        timeStamp: event.timeStamp,
      };
      const { padding } = metrics;
      if (
        clickPoint.x < padding.left
        || clickPoint.x > padding.left + metrics.plotWidth
        || clickPoint.y < padding.top
        || clickPoint.y > padding.top + metrics.plotHeight
      ) return;
      const pointTarget = motionStudioPointHitTarget(
        editor.pointHitTargets,
        clickPoint.x,
        clickPoint.y,
      );
      const selectedMotionIds = editorSelectedMotionIds();
      const motionTarget = motionStudioNearestMotionTarget(
        cachedLayerTracks(editor.preview || editor.working),
        selectedMotionIds,
        metrics,
        clickPoint.x,
        clickPoint.y,
      );
      const pointRegion = pointTarget?.curve || motionStudioPointCurveAtTime(
        editorPointCurves(editor.preview || editor.working),
        selectedMotionIds,
        Math.max(0, metrics.timeFor(clickPoint.x)),
        motionTarget,
      );
      const graphAction = motionStudioEditorGraphClickAction({
        operation: el.studioEditorOperation?.value || '',
        pointTarget,
        motionTarget,
        pointRegion,
        activeCurveId: editor.pointDraft?.curve_id,
      });
      if (graphAction === 'edit_point') {
        if (
          !pointCurveIsApplied(editor, pointTarget.curve.curve_id)
          && !pointCurveCanBeCreated(editor)
        ) {
          if (!selectPointCurveFromGraph(
            pointTarget.curve,
            pointTarget.point.point_id,
          )) return;
          setEditorMessage('생성된 포인트를 먼저 작업본에 반영하세요.', true);
          return;
        }
        if (editor.preview) {
          setEditorMessage(
            '현재 결과 미리보기를 먼저 편집 반영한 뒤 포인트를 수정하세요.',
            true,
          );
          return;
        }
        if (!selectPointCurveFromGraph(
          pointTarget.curve,
          pointTarget.point.point_id,
        )) return;
        setEditorMessage(
          `${pointTarget.curve.motion_id} 포인트 선택 · 포인트를 드래그하거나 시간·모션값을 수정하세요.`,
        );
        return;
      }
      if (graphAction === 'select_curve') {
        const activatePointMode = el.studioEditorOperation?.value === 'point_curve';
        if (!selectPointCurveFromGraph(
          pointRegion,
          pointRegion.points?.[0]?.point_id || '',
          activatePointMode,
        )) return;
        setEditorMessage(
          pointCurveIsApplied(editor, pointRegion.curve_id)
            ? (
              activatePointMode
                ? '포인트 데이터입니다. 동그란 포인트를 선택해 편집하세요.'
                : '현재 편집 항목을 유지합니다. 동그란 포인트 두 개를 선택하세요.'
            )
            : '생성된 포인트를 먼저 작업본에 반영하세요.',
          !pointCurveIsApplied(editor, pointRegion.curve_id),
        );
        return;
      }
      discardEditorPreview('편집할 포인트를 다시 선택하여 결과 미리보기를 취소했습니다.');
      if (graphAction === 'add_point') {
        const selectedIds = editorSelectedMotionIds();
        if (selectedIds.length !== 1) {
          clearPendingPointCandidate(editor);
          setEditorMessage('포인트를 추가할 Motion ID를 하나만 선택하세요.', true);
          return;
        }
        const motionId = selectedIds[0];
        const timeSec = motionStudioSnapFrameTime(metrics.timeFor(clickPoint.x));
        if ((editor.pointDraft?.points || []).some(
          (point) => Math.abs(Number(point.time_sec) - timeSec) < 0.01,
        )) {
          clearPendingPointCandidate(editor);
          setEditorMessage('같은 시간에는 포인트를 하나만 만들 수 있습니다.', true);
          renderEditorControls();
          drawEditorGraph();
          return;
        }
        const graphSample = motionStudioMotionTargetAtTime(
          cachedLayerTracks(editor.preview || editor.working),
          [motionId],
          timeSec,
          metrics.valueFor(clickPoint.y),
        );
        editor.pendingPointCandidate = {
          motionId,
          timeSec: Number(timeSec.toFixed(2)),
          valueDeg: Number(
            (graphSample?.value ?? metrics.valueFor(clickPoint.y)).toFixed(6),
          ),
        };
        setEditorMessage(
          `${motionId} 추가 위치 선택 · ${editor.pendingPointCandidate.timeSec.toFixed(2)}초 · `
          + `${editor.pendingPointCandidate.valueDeg.toFixed(3)}° · 포인트 추가를 누르세요.`,
        );
        renderEditorControls();
        drawEditorGraph();
        return;
      }
      if (graphAction === 'select_motion') {
        setEditorMessage(
          '일반 모션점은 편집할 수 없습니다. Motion ID를 하나 선택해 전체 포인트를 생성하세요.',
          true,
        );
        return;
      }
      if (graphAction !== 'select_point') {
        setEditorMessage('편집할 포인트 가까이를 클릭하세요.', true);
        return;
      }
      const previousClick = editor.lastGraphClick;
      const repeatedSamePoint = previousClick
        && (clickPoint.timeStamp - previousClick.timeStamp) < 500
        && Math.hypot(clickPoint.x - previousClick.x, clickPoint.y - previousClick.y) < 5;
      editor.lastGraphClick = clickPoint;
      if (repeatedSamePoint) {
        setEditorMessage('같은 위치의 더블클릭은 무시했습니다. 종료할 다른 지점을 한 번 클릭하세요.');
        return;
      }
      const targetMotionId = String(pointTarget.curve.motion_id);
      const targetCurveId = String(pointTarget.curve.curve_id);
      const targetTime = Number(pointTarget.point.time_sec);
      const snapped = Math.max(
        0,
        Math.round(targetTime / MOTION_STUDIO_PERIOD_SEC) * MOTION_STUDIO_PERIOD_SEC,
      );
      if (editor.selectionStage === 0) {
        if (!selectPointCurveFromGraph(
          pointTarget.curve,
          pointTarget.point.point_id,
          false,
        )) return;
        setEditorPointRange(editor, snapped, snapped, targetMotionId, targetCurveId);
        editor.selectionStage = 1;
        editor.selectionAnchor = snapped;
        setEditorMessage(
          `포인트 한 개 선택 · ${snapped.toFixed(2)}초 · `
          + '같은 포인트 곡선의 다른 포인트를 선택하세요.',
        );
      } else {
        if (
          !motionStudioPointRangeTargetsMatch(
            editor.selectionMotionId,
            targetMotionId,
            editor.selectionCurveId,
            targetCurveId,
          )
        ) {
          setEditorMessage(
            `같은 포인트 곡선의 포인트를 선택하세요. 현재 선택: ${editor.selectionMotionId}`,
            true,
          );
          return;
        }
        loadPointDraft(pointTarget.curve, pointTarget.point.point_id);
        const first = Number.isFinite(editor.selectionAnchor)
          ? editor.selectionAnchor
          : Number(editor.selectionStartSec || 0);
        if (Math.abs(first - snapped)
          < MOTION_STUDIO_PERIOD_SEC - MOTION_STUDIO_TIME_EPSILON) {
          setEditorMessage('범위를 만들려면 서로 다른 포인트를 선택하세요.', true);
          return;
        }
        setEditorPointRange(
          editor,
          Math.min(first, snapped),
          Math.max(first, snapped),
          targetMotionId,
          targetCurveId,
        );
        editor.selectionStage = 0;
        editor.selectionAnchor = null;
        setEditorMessage(
          '포인트 범위 선택 완료 · '
          + `${editor.selectionStartSec.toFixed(2)}초 ~ ${editor.selectionEndSec.toFixed(2)}초`,
        );
      }
      renderEditorControls();
      drawEditorGraph();
    });
    el.studioEditorGraph?.addEventListener('mousedown', (event) => {
      const editor = state.editor;
      const metrics = editor?.graphMetrics;
      if (!editor || !metrics || event.button !== 0) return;
      const rect = el.studioEditorGraph.getBoundingClientRect();
      const { x, y } = motionStudioCanvasEventPoint(
        rect, event.clientX, event.clientY, metrics.width, metrics.height,
      );
      const handle = (editor.handleHitTargets || []).find(
        (target) => Math.hypot(target.x - x, target.y - y) <= 9,
      );
      if (handle) {
        event.preventDefault();
        editor.draggingHandle = { side: handle.side };
        editor.suppressGraphClick = true;
        discardEditorPreview('탄젠트를 바꾸어 결과 미리보기를 취소했습니다.');
        setEditorMessage('탄젠트 핸들 조절 중 · 놓은 뒤 결과 미리보기로 곡선을 계산하세요.');
        return;
      }
      const pointTarget = motionStudioPointHitTarget(
        editor.pointHitTargets,
        x,
        y,
      );
      if (pointTarget) {
        if (
          !pointCurveIsApplied(editor, pointTarget.curve.curve_id)
          && !pointCurveCanBeCreated(editor)
        ) {
          if (!selectPointCurveFromGraph(
            pointTarget.curve,
            pointTarget.point.point_id,
          )) return;
          setEditorMessage('생성된 포인트를 먼저 작업본에 반영하세요.', true);
          return;
        }
        if (editor.preview) {
          setEditorMessage(
            '현재 결과 미리보기를 먼저 편집 반영한 뒤 포인트를 이동하세요.',
            true,
          );
          return;
        }
        event.preventDefault();
        const pointMode = el.studioEditorOperation?.value === 'point_curve';
        if (
          pointMode
          && !selectPointCurveFromGraph(
            pointTarget.curve,
            pointTarget.point.point_id,
          )
        ) return;
        editor.draggingPoint = {
          pointId: pointTarget.point.point_id,
          curve: pointTarget.curve,
          startX: x,
          startY: y,
          moved: false,
          activated: pointMode,
        };
        if (pointMode) {
          syncPointControls();
          drawEditorGraph();
        }
        setEditorMessage(
          pointMode
            ? '포인트 선택 · 그대로 드래그하면 좌우는 시간, 상하는 모션값을 바꿉니다.'
            : '한 번 클릭하면 편집할 포인트로 선택하고, 드래그하면 포인트를 이동합니다.',
        );
        return;
      }
      const { padding } = metrics;
      if (
        x >= padding.left && x <= padding.left + metrics.plotWidth
        && y >= padding.top && y <= padding.top + metrics.plotHeight
      ) {
        editor.panningGraph = {
          startX: x,
          startY: y,
          startViewStart: editor.viewStart,
          startViewEnd: editor.viewEnd,
          startMinValue: metrics.minValue,
          startMaxValue: metrics.maxValue,
          timeSpan: editor.viewEnd - editor.viewStart,
          valueSpan: metrics.maxValue - metrics.minValue,
          moved: false,
        };
      }
    });
    window.addEventListener('mouseup', () => {
      const editor = state.editor;
      if (!editor) return;
      if (editor.draggingHandle) {
        editor.draggingHandle = null;
        setEditorMessage('탄젠트 핸들 변경 완료 · 결과 미리보기를 눌러 20ms 곡선을 계산하세요.');
        renderEditor();
        return;
      }
      if (editor.draggingPoint) {
        const moved = editor.draggingPoint.moved;
        editor.draggingPoint = null;
        if (moved) {
          setEditorMessage('포인트 이동 완료 · 결과 미리보기를 눌러 곡선을 다시 계산하세요.');
          renderEditor();
        } else {
          syncPointControls();
          drawEditorGraph();
        }
        return;
      }
      if (editor.panningGraph) {
        const moved = editor.panningGraph.moved;
        editor.panningGraph = null;
        if (moved) {
          setEditorMessage(
            editor.valueRangeLock
              ? '그래프 시간축을 이동했습니다. 세로축은 모션축 범위로 고정되어 있습니다.'
              : '그래프 표시 구간을 좌우·상하로 이동했습니다.',
          );
          drawEditorGraph();
        }
      }
    });
    el.studioEditorGraph?.addEventListener('wheel', (event) => {
      const editor = state.editor;
      if (!editor) return;
      event.preventDefault();
      const span = editor.viewEnd - editor.viewStart;
      if (event.shiftKey) {
        const delta = Math.sign(event.deltaY) * span * 0.12;
        editorViewport.setView(editor.viewStart + delta, editor.viewEnd + delta);
        return;
      }
      const center = editor.cursor?.timeSec ?? ((editor.viewStart + editor.viewEnd) / 2);
      const zoomFactor = event.deltaY < 0 ? 0.8 : 1.25;
      const newSpan = span * zoomFactor;
      const ratio = (center - editor.viewStart) / span;
      editorViewport.setView(
        center - (newSpan * ratio),
        center + (newSpan * (1 - ratio)),
      );
    }, { passive: false });

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
