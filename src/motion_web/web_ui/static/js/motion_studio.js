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
  motionStudioCanCreatePointCurve,
  motionStudioCanvasEventPoint,
  motionStudioEditorNextValueScale,
  motionStudioEditorValueBounds,
  motionStudioLayerDataEqual,
  motionStudioLayerDuration,
  motionStudioLayerMotionIds,
  motionStudioPointCurvePreview,
  motionStudioPointCurveViewEnd,
  motionStudioPointDragStarted,
  motionStudioPointHitTarget,
  motionStudioRuntimeStatusMessage,
  motionStudioSelectionKindsMatch,
  motionStudioShouldEditPoint,
  resolveMotionStudioSelectedLayerId,
  synchronizeMotionStudioEditorTimeline,
} from './motion_studio_calculations.js?v=20260724-studio-cleanup-3';
import {
  drawMotionStudioEditorGraph,
  drawMotionStudioLayerGraph,
  motionStudioCompositionTracks as compositionTracks,
  motionStudioLayerTracks as layerTracks,
} from './motion_studio_graph.js?v=20260724-studio-cleanup-3';
import {
  bindMotionStudioEvent,
  bindMotionStudioProjectTransportEvents,
  createMotionStudioState,
  renderMotionStudioWorkspace,
  resetMotionStudioProjectState,
  setMotionStudioMessage,
} from './motion_studio_ui.js?v=20260724-studio-cleanup-3';
import {
  motionStudioEditorAxisLabel,
  motionStudioEditorInspectorState,
  renderMotionStudioEditorPresentation,
  requestMotionStudioEditorSave,
} from './motion_studio_editor_ui.js?v=20260724-studio-editor-ui';
import { showConfirm } from './ui_dialogs.js?v=20260727-popup-common-3';

export {
  motionStudioCanCreatePointCurve,
  motionStudioCanvasEventPoint,
  motionStudioEditorNextValueScale,
  motionStudioEditorValueBounds,
  motionStudioLayerDataEqual,
  motionStudioLayerDuration,
  motionStudioLayerMotionIds,
  motionStudioPointCurvePreview,
  motionStudioPointCurveViewEnd,
  motionStudioPointDragStarted,
  motionStudioPointHitTarget,
  motionStudioRuntimeStatusMessage,
  motionStudioSelectionKindsMatch,
  motionStudioShouldEditPoint,
  resolveMotionStudioSelectedLayerId,
  synchronizeMotionStudioEditorTimeline,
};

const MOTION_ID_PATTERN = /^[1-9]\d*-[1-9]\d*$/;

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
}) {
  const state = createMotionStudioState();

  const clone = (value) => JSON.parse(JSON.stringify(value));

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
      const frames = layer.frames || [];
      const duration = frames.length ? Number(frames[frames.length - 1].time_sec || 0) : 0;
      const motionIds = new Set(frames.flatMap((frame) => Object.keys(frame.values || {})));
      const selected = layer.layer_id === state.selectedLayerId;
      return `<tr class="${selected ? 'selected-row' : ''}" data-studio-layer-id="${escapeHtml(layer.layer_id)}">
        <td><label class="studio-playback-choice"><input type="checkbox" data-layer-enabled ${layer.enabled !== false ? 'checked' : ''} ${layer.locked ? 'disabled' : ''}><span>${layer.enabled !== false ? '선택' : '제외'}</span></label></td>
        <td><input class="studio-layer-main-name" type="text" data-layer-main-name maxlength="40" value="${escapeHtml(layer.name)}" ${layer.locked ? 'disabled' : ''} aria-label="레이어 이름"></td>
        <td><div class="studio-layer-info-cell">
          <span>${frames.length}프레임</span><span>${duration.toFixed(3)}초</span><span>${motionIds.size}축</span>
          ${layer.locked ? '<span class="status-chip off">잠금</span>' : ''}
          ${conflictLayers.has(layer.layer_id) ? '<span class="status-chip warn">충돌</span>' : ''}
          ${transitionLayers.has(layer.layer_id) ? '<span class="status-chip warn">급변</span>' : ''}
        </div></td></tr>`;
    }).join('');
  }

  function layerSummary(layer) {
    const frames = layer?.frames || [];
    const duration = frames.length ? Number(frames[frames.length - 1].time_sec || 0) : 0;
    const motionIds = new Set(frames.flatMap((frame) => Object.keys(frame.values || {})));
    return `${frames.length}프레임 · ${duration.toFixed(3)}초 · ${motionIds.size}축`;
  }

  function renderLayerManager() {
    const layers = state.project?.layers || [];
    const layerIds = new Set(layers.map((layer) => String(layer.layer_id || '')));
    const mergeableLayerIds = new Set(layers
      .filter((layer) => !layer.locked)
      .map((layer) => String(layer.layer_id || '')));
    if (state.selectedLayerId && !layerIds.has(state.selectedLayerId)) state.selectedLayerId = '';
    state.mergeLayerIds = new Set(
      [...state.mergeLayerIds].filter((layerId) => mergeableLayerIds.has(layerId)),
    );

    el.studioLayerManagerTabs?.querySelectorAll('[data-layer-manager-tab]').forEach((button) => {
      const active = button.dataset.layerManagerTab === state.layerManagerTab;
      button.classList.toggle('active', active);
      button.setAttribute('aria-selected', active ? 'true' : 'false');
    });
    el.studioLayerManagerPanels?.forEach((panel) => {
      panel.classList.toggle('hidden', panel.dataset.layerManagerPanel !== state.layerManagerTab);
    });

    if (el.studioManagerLayerRows) {
      el.studioManagerLayerRows.innerHTML = layers.length ? layers.map((layer) => {
        const selected = layer.layer_id === state.selectedLayerId;
        return `<tr class="${selected ? 'selected-row' : ''}" data-manager-layer-id="${escapeHtml(layer.layer_id)}">
          <td><input type="radio" name="studio-manager-layer" data-manager-layer-select ${selected ? 'checked' : ''} aria-label="개별 관리 레이어 선택"></td>
          <td><strong>${escapeHtml(layer.name)}</strong></td>
          <td><span>${layerSummary(layer)}</span>${layer.locked ? ' <span class="status-chip off">잠금</span>' : ''}</td>
        </tr>`;
      }).join('') : '<tr><td colspan="3" class="empty">레이어가 없습니다</td></tr>';
    }

    if (el.studioManagerMergeRows) {
      el.studioManagerMergeRows.innerHTML = layers.length ? layers.map((layer) => {
        const checked = state.mergeLayerIds.has(layer.layer_id);
        return `<tr data-manager-merge-layer-id="${escapeHtml(layer.layer_id)}">
          <td><input type="checkbox" data-manager-layer-merge ${checked ? 'checked' : ''} ${layer.locked ? 'disabled' : ''} aria-label="합칠 레이어 선택"></td>
          <td><strong>${escapeHtml(layer.name)}</strong></td>
          <td><span>${layerSummary(layer)}</span>${layer.locked ? ' <span class="status-chip off">잠금</span>' : ''}</td>
        </tr>`;
      }).join('') : '<tr><td colspan="3" class="empty">레이어가 없습니다</td></tr>';
    }
  }

  function playbackView(duration = 0) {
    const runtimeState = String(state.status?.state || 'idle');
    const phase = String(state.status?.phase || runtimeState);
    const initializing = runtimeState === 'initializing';
    const playing = runtimeState === 'playing';
    const recording = runtimeState === 'recording';
    const stopping = runtimeState === 'stopping';
    const progress = state.status?.runtime_progress || {};
    const initializationProgress = state.status?.initialization_progress || {};
    const sourceElapsed = playing || stopping
      ? Math.max(0, Number(progress.elapsed_sec ?? state.status?.elapsed_sec) || 0)
      : recording ? Math.max(0, Number(state.status?.elapsed_sec) || 0) : 0;
    const clock = state.playbackClock;
    const elapsed = clock && clock.runtimeState === runtimeState
      ? Math.max(0, clock.sourceElapsed + ((performance.now() - clock.receivedAt) / 1000))
      : sourceElapsed;
    const total = recording
      ? Math.max(elapsed, Number(duration) || 0)
      : Math.max(0, Number(state.status?.playback_duration_sec) || Number(duration) || 0);
    let label = '대기'; let chip = 'off'; let displayState = 'idle';
    if (runtimeState === 'error') {
      label = '오류'; chip = 'danger'; displayState = 'error';
    } else if (initializing && phase === 'countdown') {
      label = '재생 준비'; chip = 'warn'; displayState = 'countdown';
    } else if (initializing) {
      label = '초기 위치 이동'; chip = 'warn'; displayState = 'initializing';
    } else if (playing) {
      label = '모션 재생'; chip = 'on'; displayState = 'playing';
    } else if (stopping) {
      label = '정지 중'; chip = 'warn'; displayState = 'stopping';
    } else if (runtimeState === 'recording') {
      label = '녹화 중'; chip = 'on'; displayState = 'recording';
    }
    const initElapsed = Math.max(0, Number(initializationProgress.elapsed_sec) || 0);
    const initDuration = Math.max(0, Number(initializationProgress.duration_sec) || 0);
    return {
      runtimeState, displayState, label, chip, elapsed, total,
      ratio: total > 0 ? Math.min(1, elapsed / total) : 0,
      showPlayhead: initializing || playing || stopping || recording,
      playheadTime: playing || stopping || recording ? Math.min(total, elapsed) : 0,
      message: initializing && phase !== 'countdown' && initDuration > 0
        ? `초기 위치 이동 ${timeText(initElapsed)} / ${timeText(initDuration)} · 완료 후 3초 준비 뒤 재생합니다.`
        : String(state.status?.message || '합성 미리보기를 시작하면 진행 위치가 그래프에 표시됩니다.'),
    };
  }

  function syncPlaybackClock() {
    const runtimeState = String(state.status?.state || 'idle');
    const progress = state.status?.runtime_progress || {};
    const sourceElapsed = runtimeState === 'playing' || runtimeState === 'stopping'
      ? Math.max(0, Number(progress.elapsed_sec ?? state.status?.elapsed_sec) || 0)
      : runtimeState === 'recording'
        ? Math.max(0, Number(state.status?.elapsed_sec) || 0)
        : 0;
    const running = ['playing', 'recording'].includes(runtimeState);
    const previous = state.playbackClock;
    if (!running) {
      state.playbackClock = null;
      return;
    }
    if (
      !previous
      || previous.runtimeState !== runtimeState
      || Math.abs(previous.sourceElapsed - sourceElapsed) > 0.0005
    ) {
      const now = performance.now();
      const previousEstimate = previous && previous.runtimeState === runtimeState
        ? previous.sourceElapsed + ((now - previous.receivedAt) / 1000)
        : sourceElapsed;
      state.playbackClock = {
        runtimeState,
        sourceElapsed: Math.max(sourceElapsed, previousEstimate),
        receivedAt: now,
      };
    }
  }

  function updatePlaybackPlayhead(view) {
    const playhead = el.studioLayerPlayhead;
    const canvas = el.studioLayerGraph;
    if (!playhead || !canvas || !view.showPlayhead || !state.detailGraph?.duration) {
      playhead?.classList.add('hidden');
      return;
    }
    const width = canvas.getBoundingClientRect().width || canvas.clientWidth || 0;
    if (width <= 70) return;
    const graphDuration = Math.max(0.02, Number(state.detailGraph.duration) || 0.02);
    const ratio = Math.min(1, Math.max(0, Number(view.playheadTime) / graphDuration));
    playhead.style.left = `${52 + (ratio * (width - 70))}px`;
    playhead.classList.toggle('initializing', ['initializing', 'countdown'].includes(view.displayState));
    playhead.classList.remove('hidden');
    const label = playhead.querySelector('span');
    if (label) label.textContent = view.displayState === 'initializing' ? '시작 위치' : timeText(view.playheadTime);
  }

  function animatePlaybackGraph() {
    if (state.playbackAnimationFrame) return;
    const tick = () => {
      state.playbackAnimationFrame = 0;
      const runtimeState = String(state.status?.state || 'idle');
      const view = renderPlaybackMonitor();
      updatePlaybackPlayhead(view);
      if (['playing', 'recording'].includes(runtimeState)) {
        state.playbackAnimationFrame = window.requestAnimationFrame(tick);
      }
    };
    state.playbackAnimationFrame = window.requestAnimationFrame(tick);
  }

  function renderPlaybackMonitor(duration = state.detailGraph?.duration || 0) {
    const view = playbackView(duration);
    if (el.studioPlaybackMonitor) el.studioPlaybackMonitor.dataset.state = view.displayState;
    if (el.studioPlaybackPhase) {
      el.studioPlaybackPhase.className = `status-chip ${view.chip}`;
      el.studioPlaybackPhase.textContent = view.label;
    }
    if (el.studioPlaybackTime) {
      el.studioPlaybackTime.textContent = `${timeText(view.elapsed)} / ${timeText(view.total)}`;
    }
    if (el.studioPlaybackLayerCount) {
      const count = state.detailGraph?.enabledLayerCount
        ?? Number(state.status?.playback_layer_count || 0);
      el.studioPlaybackLayerCount.textContent = `재생 선택 ${count}개 · 합성 그래프`;
    }
    if (el.studioPlaybackProgressBar) {
      el.studioPlaybackProgressBar.style.width = `${(view.ratio * 100).toFixed(2)}%`;
    }
    if (el.studioPlaybackMessage) el.studioPlaybackMessage.textContent = view.message;
    if (el.studioPlaybackQuickPhase) {
      el.studioPlaybackQuickPhase.className = `status-chip ${view.chip}`;
      el.studioPlaybackQuickPhase.textContent = view.label;
    }
    if (el.studioPlaybackQuickTime) {
      el.studioPlaybackQuickTime.textContent = `${timeText(view.elapsed)} / ${timeText(view.total)}`;
    }
    if (el.studioPlaybackQuickMessage) {
      el.studioPlaybackQuickMessage.textContent = view.message;
    }
    return view;
  }

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
    return Array.isArray(layer?.point_curves) ? layer.point_curves : [];
  }

  function selectedDraftPoint(editor = state.editor) {
    return editor?.pointDraft?.points?.find(
      (point) => point.point_id === editor.selectedPointId,
    ) || null;
  }

  function storedCurveForDraft(editor = state.editor) {
    const curveId = String(editor?.pointDraft?.curve_id || '');
    if (!curveId) return null;
    return editorPointCurves(editor?.working).find(
      (curve) => String(curve.curve_id || '') === curveId,
    ) || null;
  }

  function pointCurveIsSaved(editor, curveId) {
    const targetId = String(curveId || '');
    return Boolean(targetId) && editorPointCurves(editor?.original).some(
      (curve) => String(curve.curve_id || '') === targetId,
    );
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
    if (!editor?.pointDraft) return false;
    const stored = storedCurveForDraft(editor);
    if (!stored) return true;
    const comparable = (curve) => {
      const normalized = clone(curve);
      if (Number(normalized?.interpolation_order) === 1) {
        (normalized.points || []).forEach((point) => {
          if (point.tangent_mode === 'linear') point.tangent_mode = 'auto';
        });
      }
      return normalized;
    };
    return JSON.stringify(comparable(stored))
      !== JSON.stringify(comparable(editor.pointDraft));
  }

  function loadPointDraft(curve, pointId = '') {
    const editor = state.editor;
    if (!editor || !curve) return;
    editor.pointDraft = clone(curve);
    if (![1, 3, 5].includes(Number(editor.pointDraft.interpolation_order))) {
      editor.pointDraft.interpolation_order = (editor.pointDraft.points || []).every(
        (point) => point.tangent_mode === 'linear',
      ) ? 1 : 3;
    }
    editor.pointCurveOrder = Number(editor.pointDraft.interpolation_order);
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

  function setPointCurveMode(curve = null, pointId = '') {
    if (el.studioEditorOperation) el.studioEditorOperation.value = 'point_curve';
    if (state.editor) state.editor.operation = 'point_curve';
    if (curve) {
      selectOnlyEditorAxis(curve.motion_id);
      loadPointDraft(curve, pointId);
      const points = curve.points || [];
      if (el.studioEditorStart) {
        el.studioEditorStart.value = Number(points[0]?.time_sec || 0).toFixed(2);
      }
      if (el.studioEditorEnd) {
        el.studioEditorEnd.value = Number(
          points[points.length - 1]?.time_sec || 0,
        ).toFixed(2);
      }
      state.editor.selectionKind = 'point';
      state.editor.selectionStage = 0;
      state.editor.selectionAnchor = null;
    }
    renderEditor();
  }

  function syncPointControls() {
    const editor = state.editor;
    const point = selectedDraftPoint(editor);
    const pointMode = el.studioEditorOperation?.value === 'point_curve';
    const savedPointCurve = pointCurveIsSaved(editor, editor?.pointDraft?.curve_id);
    const editablePointCurve = savedPointCurve || pointCurveCanBeCreated(editor);
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
    const storedPointCurve = editorPointCurves(editor?.working).some(
      (curve) => curve.curve_id === editor?.pointDraft?.curve_id,
    );
    if (el.studioEditorCurveDetachButton) {
      el.studioEditorCurveDetachButton.disabled = (
        !pointMode || !storedPointCurve || !savedPointCurve
      );
    }
    if (el.studioEditorCurveDeleteButton) {
      el.studioEditorCurveDeleteButton.disabled = !pointMode || !editor?.pointDraft?.curve_id;
    }
    if (el.studioEditorPointCurveOrder) {
      el.studioEditorPointCurveOrder.disabled = !pointMode || !editablePointCurve;
      if (document.activeElement !== el.studioEditorPointCurveOrder) {
        el.studioEditorPointCurveOrder.value = String(
          editor?.pointDraft?.interpolation_order || editor?.pointCurveOrder || 3,
        );
      }
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
  }

  function setEditorMessage(message, error = false) {
    if (!el.studioEditorMessage) return;
    el.studioEditorMessage.textContent = message || '';
    el.studioEditorMessage.classList.toggle('error-text', error);
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
      end: times.length ? Math.max(...times) : 0.02,
    };
  }

  function openLayerEditor(layer) {
    if (!layer || layer.locked) return;
    const duration = editorDuration(layer);
    const operation = el.studioEditorOperation?.value || 'time_scale';
    const pointTimelineEnd = motionStudioPointCurveViewEnd(duration);
    state.editor = {
      layerId: layer.layer_id,
      original: clone(layer),
      working: clone(layer),
      preview: null,
      previewValidation: null,
      undo: [], redo: [],
      viewStart: 0,
      viewEnd: operation === 'point_curve'
        ? pointTimelineEnd : Math.max(0.02, duration),
      valueScale: 1,
      selectionStage: 0,
      selectionAnchor: null,
      selectionKind: '',
      lastGraphClick: null,
      cursor: null,
      graphMetrics: null,
      pointDraft: null,
      pointCurveOrder: 3,
      pointTimelineEnd,
      selectedPointId: '',
      pointHitTargets: [],
      handleHitTargets: [],
      draggingHandle: null,
      draggingPoint: null,
      panningGraph: null,
      operationReport: null,
      operation,
      validation: { conflicts: [], transition_warnings: [], playable: true },
      saveState: 'saved',
      savedAt: '',
      saveError: '',
      saveFailureFingerprint: '',
    };
    if (el.studioEditorTitle) el.studioEditorTitle.textContent = `레이어 편집 · ${layer.name}`;
    if (el.studioEditorSubtitle) el.studioEditorSubtitle.textContent = '편집 반영 0회';
    refreshEditorAxisControls(new Set(editorMotionIds(layer)), layer);
    if (el.studioEditorStart) el.studioEditorStart.value = '';
    if (el.studioEditorEnd) el.studioEditorEnd.value = '';
    el.studioLayerEditorModal?.classList.remove('hidden');
    document.body.classList.add('modal-open');
    setEditorMessage('값을 입력하고 결과를 미리 본 뒤 편집 반영하세요. 저장 후에도 계속 편집할 수 있습니다.');
    renderEditor();
  }

  function closeLayerEditor() {
    state.editor = null;
    if (el.studioEditorStart) el.studioEditorStart.value = '';
    if (el.studioEditorEnd) el.studioEditorEnd.value = '';
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
    const savedPointCurve = pointCurveIsSaved(editor, editor?.pointDraft?.curve_id);
    const creatablePointCurve = pointMode && pointCurveCanBeCreated(editor);
    const workingPointCurve = Boolean(storedCurveForDraft(editor));
    const hasTransientChange = Boolean(editor?.preview) || pointDraftHasUnsavedChanges(editor);
    const workingFingerprint = JSON.stringify({
      frames: editor?.working?.frames || [],
      point_curves: editor?.working?.point_curves || [],
      pointDraft: editor?.pointDraft || null,
    });
    const layerDirty = Boolean(editor) && !motionStudioLayerDataEqual(
      editor.original,
      editor.working,
    );
    if (editor?.saveState === 'failed'
      && editor.saveFailureFingerprint !== workingFingerprint) {
      editor.saveState = 'dirty';
      editor.saveError = '';
    }
    let saveState = editor?.saveState || 'saved';
    if (editor?.preview) saveState = 'preview';
    else if (!['saving', 'failed'].includes(saveState)) {
      saveState = layerDirty || pointDraftHasUnsavedChanges(editor) ? 'dirty' : 'saved';
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
        Boolean(editor?.preview) || (!savedPointCurve && !creatablePointCurve)
      );
    }
    if (el.studioEditorSaveButton) {
      el.studioEditorSaveButton.disabled = (
        !['dirty', 'failed'].includes(saveState)
        || pointDraftHasUnsavedChanges(editor)
      );
    }
    if (el.studioEditorOperationTitle) {
      el.studioEditorOperationTitle.textContent = pointMode
        ? '포인트 곡선 편집'
        : '선택 구간 편집';
    }
    el.studioEditorScopeControls?.classList.toggle('hidden', pointMode);
    document.querySelector('.studio-editor-conversion-controls')?.classList.toggle(
      'hidden',
      workingPointCurve,
    );
    document.querySelector('.studio-editor-operations')?.classList.toggle(
      'hidden',
      !workingPointCurve,
    );
    if (el.studioEditorFitSelectionButton) {
      el.studioEditorFitSelectionButton.disabled = pointMode;
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
    if (el.studioEditorOperation) {
      el.studioEditorOperation.disabled = Boolean(editor?.preview);
    }
    if (el.studioEditorConvertToPointsButton) {
      el.studioEditorConvertToPointsButton.disabled = (
        Boolean(editor?.preview)
        || pointMode
        || editor?.selectionStage === 1
        || editor?.selectionKind !== 'motion'
        || editorSelectedMotionIds().length !== 1
        || !el.studioEditorStart?.value
        || !el.studioEditorEnd?.value
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
    if (el.studioEditorOperationHelp) {
      const help = {
        time_scale: '저장된 포인트 모션 구간에서 포인트 두 개를 선택한 뒤 시간을 조절합니다.',
        value_scale: '저장된 포인트 모션 구간에서 포인트 두 개를 선택한 뒤 모션값 변화량을 조절합니다.',
        time_shift: '저장된 포인트 모션 구간과 포인트를 선택한 뒤 시간을 이동합니다.',
        value_offset: '저장된 포인트 모션 구간과 포인트를 선택한 뒤 모션값을 이동합니다.',
        point_curve: savedPointCurve
          ? '저장된 포인트 모션입니다. 포인트 추가·이동과 탄젠트 편집이 가능합니다.'
          : creatablePointCurve
            ? '새로 추가한 축입니다. 그래프를 클릭해 포인트를 두 개 이상 만드세요.'
            : '변환된 포인트 모션을 먼저 저장해야 편집할 수 있습니다.',
      };
      el.studioEditorOperationHelp.textContent = help[operation] || '';
    }
    renderMotionStudioEditorPresentation(el, {
      saveState,
      savedAt: editor?.savedAt || '',
      saveError: editor?.saveError || '',
      inspector: motionStudioEditorInspectorState({
        preview: Boolean(editor?.preview),
        pointDraftUnsaved: pointDraftHasUnsavedChanges(editor)
          || (workingPointCurve && !savedPointCurve),
        savedPointCurve,
        pointSelected: Boolean(selectedDraftPoint(editor)),
        rangeSelected: Boolean(
          el.studioEditorStart?.value?.trim() && el.studioEditorEnd?.value?.trim(),
        ),
      }),
      showDangerZone: pointMode && Boolean(editor?.pointDraft?.curve_id),
    });
    syncPointControls();
  }

  function drawEditorGraph() {
    drawMotionStudioEditorGraph({
      editor: state.editor,
      canvas: el.studioEditorGraph,
      legend: el.studioEditorLegend,
      selectedMotionIds: editorSelectedMotionIds(),
      operation: el.studioEditorOperation?.value || '',
      selectionStartText: el.studioEditorStart?.value?.trim() || '',
      selectionEndText: el.studioEditorEnd?.value?.trim() || '',
      devicePixelRatio: window.devicePixelRatio || 1,
    });
  }

  function renderEditor() {
    if (!state.editor) return;
    renderEditorControls();
    drawEditorGraph();
  }

  function refreshEditorTimeline(layer, previousLayer = null) {
    const editor = state.editor;
    if (!synchronizeMotionStudioEditorTimeline(editor, layer, previousLayer)) return false;
    const bounds = editorTimeBounds(layer);
    if (el.studioEditorStart) el.studioEditorStart.value = bounds.start.toFixed(2);
    if (el.studioEditorEnd) el.studioEditorEnd.value = bounds.end.toFixed(2);
    return true;
  }

  function discardEditorPreview(message = '') {
    const editor = state.editor;
    if (!editor) return false;
    if (!editor.preview) {
      if (!editor.operationReport) return false;
      editor.operationReport = null;
      if (message) setEditorMessage(message);
      renderEditor();
      return true;
    }
    const preview = editor.preview;
    editor.preview = null;
    editor.previewValidation = null;
    editor.operationReport = null;
    editor.pendingCurveId = '';
    refreshEditorTimeline(editor.working, preview);
    refreshEditorAxisControls(null, editor.working);
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
    const appliedOperation = editor.operationReport?.operation || '';
    const activeCurveId = editor.pointDraft?.curve_id || editor.pendingCurveId || '';
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
    editor.pendingCurveId = '';
    if (activeCurveId) {
      const updatedCurve = editorPointCurves(editor.working).find(
        (curve) => curve.curve_id === activeCurveId,
      );
      if (updatedCurve) loadPointDraft(updatedCurve, selectedPointId);
    }
    refreshEditorAxisControls(selectedIds, editor.working);
    if (el.studioEditorSubtitle) {
      el.studioEditorSubtitle.textContent = `편집 반영 ${editor.undo.length}회`;
    }
    const appliedRangeWarningCount = editor.validation?.range_warnings?.length || 0;
    const appliedMessage = appliedOperation === 'convert_motion_to_point_curve'
      ? '포인트 변환을 작업본에 반영했습니다. 지금 저장해야 포인트 편집이 활성화됩니다.'
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
        project: state.project,
        operation: 'add_axis',
        motion_ids: [motionId],
        initial_value_deg: initialValue,
        mapping_rows: activeMapping()?.rows || [],
      });
      if (result.success === false) throw new Error(result.message || '축 추가 실패');
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
        project: state.project,
        operation: 'copy_axis',
        source_motion_id: sourceMotionId,
        motion_ids: [targetMotionId],
        mapping_rows: activeMapping()?.rows || [],
      });
      if (result.success === false) throw new Error(result.message || '축 복사 실패');
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

  async function applyEditorOperation(operationOverride = '', confirmDelete = true) {
    const editor = state.editor;
    if (!editor) return;
    const operation = typeof operationOverride === 'string' && operationOverride
      ? operationOverride
      : (el.studioEditorOperation?.value || 'value_offset');
    const pointMetadataOperation = [
      'point_curve', 'delete_point_curve', 'detach_point_curve',
      'convert_point_curve_to_motion',
    ].includes(operation);
    if (!pointMetadataOperation && editor.selectionStage === 1) {
      setEditorMessage('종료 시간을 그래프에서 한 번 더 클릭하세요.', true);
      return;
    }
    const motionIds = editorSelectedMotionIds();
    if (
      !motionIds.length
      && !['delete_point_curve', 'detach_point_curve'].includes(operation)
    ) { setEditorMessage('편집할 Motion ID를 선택하세요', true); return; }
    if (
      !pointMetadataOperation
      && (!el.studioEditorStart?.value?.trim() || !el.studioEditorEnd?.value?.trim())
    ) {
      setEditorMessage('그래프에서 시작점과 끝점을 선택하거나 시간을 직접 입력하세요.', true);
      return;
    }
    if (operation === 'point_curve' && (!editor.pointDraft || editor.pointDraft.points.length < 2)) {
      setEditorMessage('그래프에서 같은 Motion ID의 포인트를 두 개 이상 만드세요.', true);
      return;
    }
    if (
      operation === 'delete_data'
      && confirmDelete
      && !await showConfirm(
        '선택한 축의 선택 구간 데이터를 삭제할까요?',
        { title: '선택 구간 데이터 삭제', confirmLabel: '삭제', tone: 'danger' },
      )
    ) return;
    const payload = {
      layer: editor.working,
      project: state.project,
      operation,
      motion_ids: motionIds,
      selection_kind: editor.selectionKind || 'motion',
      start_sec: Number(el.studioEditorStart?.value || 0),
      end_sec: Number(el.studioEditorEnd?.value || 0),
      offset_deg: Number(el.studioEditorOffset?.value || 0),
      factor: Number(el.studioEditorFactor?.value || 1),
      delta_sec: Number(el.studioEditorDelta?.value || 0) / 1000,
      interpolation_order: operation === 'point_curve'
        ? Number(editor.pointDraft?.interpolation_order || el.studioEditorPointCurveOrder?.value || 3)
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
      editor.preview = clone(result.layer);
      refreshEditorTimeline(editor.preview, editor.working);
      if (operation === 'point_curve' && editor.pointDraft) {
        const calculated = editorPointCurves(editor.preview).find(
          (curve) => curve.curve_id === editor.pointDraft.curve_id,
        );
        if (calculated) loadPointDraft(calculated, editor.selectedPointId);
      }
      if (
        operation === 'delete_point_curve'
        || operation === 'detach_point_curve'
        || operation === 'convert_point_curve_to_motion'
      ) {
        editor.pointDraft = null;
        editor.selectedPointId = '';
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
      if (operation === 'convert_motion_to_point_curve') {
        const report = editor.operationReport || {};
        setEditorMessage(
          `포인트 근사 미리보기 · ${Number(report.interpolation_order || 1)}차 곡선 · `
          + `${Number(report.source_sample_count || 0)}개 모션점 → `
          + `${Number(report.point_count || 0)}개 포인트 · 최대 오차 `
          + `${Number(report.maximum_error_deg || 0).toFixed(4)}° · `
          + `편집 반영 후 저장해야 포인트 편집이 활성화됩니다.${rangeWarningText}`,
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
      if (operation === 'convert_motion_to_point_curve') editor.pendingCurveId = '';
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
    if (el.studioMergeButton) el.studioMergeButton.disabled = state.busy || count < 2;
    if (el.studioMergeStatus) {
      el.studioMergeStatus.textContent = state.mergeResultMessage || (
        count < 2
          ? '합칠 레이어를 2개 이상 선택하세요'
          : `합칠 레이어 ${count}개 · 합치기를 누르면 충돌 검사를 진행합니다`
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
    const running = ['initializing', 'recording', 'playing', 'stopping'].includes(runtimeState);
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
    const composition = compositionTracks(layers, activeMapping()?.rows || []);
    const compositionMode = state.layerDetailMode !== 'layer';
    const frames = layer?.frames || [];
    const tracks = compositionMode ? composition.tracks : layerTracks(layer);
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
        ? `기록 중 · 화면 표시 간격 ${Math.round(stride * 20)}ms · 원본은 20ms로 기록`
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
      <span>${escapeHtml(firstMismatch.layer_name)} · 처리 방법을 선택해야 재생할 수 있습니다.</span>
      <button type="button" data-resolve-point-curve="points" data-layer-id="${escapeHtml(firstMismatch.layer_id)}">포인트 기준으로 다시 계산</button>
      <button type="button" data-resolve-point-curve="frames" data-layer-id="${escapeHtml(firstMismatch.layer_id)}">현재 프레임 유지</button>
    </div>` : ''}`;
  }

  async function resolvePointCurveMismatch(layerId, strategy) {
    const layer = state.project?.layers?.find((item) => item.layer_id === layerId);
    const issues = (state.composition?.point_curve_mismatches || []).filter(
      (item) => item.layer_id === layerId,
    );
    if (!layer || !issues.length) return null;
    if (layer.locked) {
      setMessage(`'${layer.name}' 레이어 잠금을 먼저 해제하세요.`, true);
      return null;
    }
    const pointBased = strategy === 'points';
    const question = pointBased
      ? `'${layer.name}'의 포인트·탄젠트를 기준으로 20ms 프레임을 다시 계산할까요?`
      : `'${layer.name}'의 현재 20ms 프레임을 유지하고 불일치한 포인트 편집 정보를 제거할까요?`;
    if (!await showConfirm(question, {
      title: '포인트 곡선 불일치 정리',
      confirmLabel: pointBased ? '다시 계산' : '현재 프레임 유지',
      tone: 'warning',
    })) return null;
    return run(async () => {
      const calculated = await editMotionStudioLayer({
        layer,
        project: state.project,
        operation: 'resolve_point_curve_consistency',
        strategy,
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
      el.studioStopButton.disabled = !running || runtimeState === 'stopping';
    }
    if (el.studioExportButton) {
      el.studioExportButton.disabled = state.busy || running || !hasEnabledLayer || hasCompositionErrors;
      el.studioExportButton.title = hasCurveMismatches
        ? '포인트 곡선과 20ms 프레임 불일치를 먼저 정리하세요'
        : (hasCompositionErrors ? '레이어 충돌 또는 모션값 급변을 해결한 뒤 내보낼 수 있습니다' : '');
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
    renderLists(); renderMapping(); renderAxes(); renderLayers(); renderLayerManager(); renderLayerDetail();
    renderConflicts(); renderControls();
  }

  async function run(action, { onError = null } = {}) {
    setBusy(true);
    setMessage('요청 처리 중입니다…');
    try {
      const result = await action();
      if (result.success === false) throw new Error(result.message || '요청 실패');
      if (result.project) setProject(result.project);
      if (result.status) state.status = result.status;
      if (result.composition) state.composition = result.composition;
      setMessage(result.message || '완료');
      await refresh(false);
      render();
      return result;
    } catch (error) {
      setMessage(error.message || String(error), true);
      onError?.(error);
      return null;
    } finally {
      setBusy(false);
    }
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
      resolvePointCurveMismatch(
        String(button.dataset.layerId || ''),
        String(button.dataset.resolvePointCurve || ''),
      );
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
        run(() => startMotionStudioRecord({
          mode,
          initial_move_time_sec: initialMoveTimeSec,
        }));
      },
      onInitialize: ({ initialMoveTimeSec }) => {
        if (!requireMotorActionReady('초기 위치 이동')) return;
        showLayerGraph({ composition: true });
        run(() => startMotionStudioInitialization({
          initial_move_time_sec: initialMoveTimeSec,
        }));
      },
      onPlay: ({ initialMoveTimeSec }) => {
        if (!requireMotorActionReady('합성 미리보기 재생')) return;
        showLayerGraph({ composition: true });
        run(() => startMotionStudioPlayback({
          initial_move_time_sec: initialMoveTimeSec,
        }));
      },
      // The helper disables duplicate stop clicks before this callback runs.
      onStop: () => run(stopMotionStudio),
      onCreateLayer: async () => {
        const result = await run(() => createMotionStudioLayer());
        if (!result?.layer_id) return;
        state.selectedLayerId = result.layer_id;
        render();
      },
      defaultExportName: () => state.project?.name || 'motion',
      onExport: (name) => run(() => exportMotionStudio(name)),
    });
    el.studioLayerRows?.addEventListener('change', (event) => {
      const row = event.target.closest('tr[data-studio-layer-id]');
      if (!row) return;
      state.selectedLayerId = row.dataset.studioLayerId;
      if (event.target.matches('[data-layer-enabled]')) {
        run(() => updateMotionStudioLayer({
          layer_id: row.dataset.studioLayerId,
          enabled: event.target.checked,
        }));
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
        result.project?.layers || [], state.selectedLayerId,
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
      const name = nextMergedLayerName();
      if (!await showConfirm(
        `선택한 ${layerIds.length}개 레이어를 '${name}'로 합칠까요?\n원본과 결과는 재생 선택 상태를 변경하지 않습니다.`,
        { title: '레이어 합치기', confirmLabel: '합치기', tone: 'warning' },
      )) return;
      state.mergeResultMessage = `${layerIds.length}개 레이어 충돌 검사 중…`;
      state.mergeResultError = false;
      renderMergeControl();
      let failureMessage = '';
      const result = await run(async () => {
        try {
          const preview = await previewMotionStudioMerge({
            project: state.project,
            layer_ids: layerIds,
            name,
            mapping_rows: activeMapping()?.rows || [],
          });
          if (preview.success === false) {
            throw new Error(preview.message || '레이어 합치기 충돌 검사 실패');
          }
          const committed = await commitMotionStudioMerge({
            source_layer_ids: layerIds,
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
        state.layerManagerTab = 'merge';
        state.mergeResultMessage = `합치기 성공 · '${name}' 레이어를 생성했습니다`;
        state.mergeResultError = false;
        render();
      } else {
        state.mergeResultMessage = failureMessage || '레이어 합치기 중단 · 결과를 확인하세요';
        state.mergeResultError = true;
        renderMergeControl();
      }
    });
    const selectEditorAxes = (checked) => {
      el.studioEditorAxisList?.querySelectorAll('input').forEach((input) => { input.checked = checked; });
      if (!discardEditorPreview('축 선택이 바뀌어 결과 미리보기를 취소했습니다.')) renderEditor();
    };
    el.studioEditorSelectAllButton?.addEventListener('click', () => selectEditorAxes(true));
    el.studioEditorSelectNoneButton?.addEventListener('click', () => selectEditorAxes(false));
    el.studioEditorAddAxisSelect?.addEventListener('input', renderEditorControls);
    el.studioEditorAddAxisButton?.addEventListener('click', previewEditorAxisAddition);
    el.studioEditorCopyAxisSource?.addEventListener('change', renderEditorControls);
    el.studioEditorCopyAxisTarget?.addEventListener('input', renderEditorControls);
    el.studioEditorCopyAxisButton?.addEventListener('click', previewEditorAxisCopy);
    el.studioEditorAxisList?.addEventListener('change', () => {
      if (!discardEditorPreview('축 선택이 바뀌어 결과 미리보기를 취소했습니다.')) renderEditor();
    });
    el.studioEditorOperation?.addEventListener('change', () => {
      const editor = state.editor;
      if (!editor) return;
      const nextOperation = el.studioEditorOperation.value;
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
        editor.operation === 'point_curve' && nextOperation !== 'point_curve'
      ) ? storedCurveForDraft(editor) : null;
      editor.operation = nextOperation;
      if (linkedCurve) {
        const points = linkedCurve.points || [];
        el.studioEditorStart.value = Number(points[0]?.time_sec || 0).toFixed(2);
        el.studioEditorEnd.value = Number(points[points.length - 1]?.time_sec || 0).toFixed(2);
        editor.selectionStage = 0;
        editor.selectionAnchor = null;
        editor.selectionKind = 'point';
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
          `${linkedCurve.motion_id} 포인트 곡선 전체 구간을 선택했습니다. `
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
      if (Number.isFinite(timeSec)) {
        point.time_sec = Math.max(0, Math.round(timeSec / 0.02) * 0.02);
        if (point.time_sec > editor.viewEnd) {
          editor.pointTimelineEnd = motionStudioPointCurveViewEnd(
            editorDuration(editor.working),
            editor.viewEnd,
            point.time_sec + Math.max(1, point.time_sec * 0.05),
          );
          editor.viewEnd = editor.pointTimelineEnd;
        }
      }
      if (Number.isFinite(valueDeg)) point.value_deg = valueDeg;
      point.tangent_mode = tangentMode;
      editor.pointDraft.points.sort((first, second) => first.time_sec - second.time_sec);
      setEditorMessage('포인트 변경 완료 · 결과 미리보기를 눌러 곡선을 다시 계산하세요.');
      renderEditor();
    };
    el.studioEditorPointTime?.addEventListener('change', updateSelectedPointFromControls);
    el.studioEditorPointValue?.addEventListener('change', updateSelectedPointFromControls);
    el.studioEditorPointMode?.addEventListener('change', updateSelectedPointFromControls);
    el.studioEditorPointCurveOrder?.addEventListener('change', () => {
      const editor = state.editor;
      if (!editor) return;
      const interpolationOrder = Number(el.studioEditorPointCurveOrder?.value || 3);
      editor.pointCurveOrder = interpolationOrder;
      if (editor.pointDraft) editor.pointDraft.interpolation_order = interpolationOrder;
      discardEditorPreview('구간 곡선 방식이 바뀌어 결과 미리보기를 취소했습니다.');
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
      editor.pointTimelineEnd = Math.max(0.02, requested);
      editor.viewStart = 0;
      editor.viewEnd = editor.pointTimelineEnd;
      setEditorMessage(
        `포인트 작업 시간축을 0초~${editor.pointTimelineEnd.toFixed(2)}초로 표시합니다.`,
      );
      renderEditor();
    });
    el.studioEditorPointDeleteButton?.addEventListener('click', () => {
      const editor = state.editor; const point = selectedDraftPoint(editor);
      if (!editor?.pointDraft || !point) return;
      if ((editor.pointDraft.points || []).length <= 2) {
        setEditorMessage(
          '곡선을 유지하려면 포인트가 최소 2개 필요하므로 더 삭제할 수 없습니다.',
          true,
        );
        return;
      }
      discardEditorPreview();
      editor.pointDraft.points = editor.pointDraft.points.filter(
        (item) => item.point_id !== point.point_id,
      );
      editor.selectedPointId = editor.pointDraft.points[0]?.point_id || '';
      setEditorMessage('포인트를 작업본에서 제거했습니다 · 결과 계산 전에는 저장되지 않습니다.');
      renderEditor();
    });
    el.studioEditorCurveDetachButton?.addEventListener('click', async () => {
      const editor = state.editor;
      const curve = storedCurveForDraft(editor);
      if (!curve) return;
      if (!await showConfirm(
        '현재 20ms 그래프를 그대로 유지하고 일반 모션으로 변환할까요? '
        + '저장 후에는 다시 포인트 모션으로 변환하기 전까지 편집할 수 없습니다.',
        { title: '일반 모션으로 변환', confirmLabel: '변환', tone: 'warning' },
      )) return;
      applyEditorOperation('convert_point_curve_to_motion', false);
    });
    el.studioEditorCurveDeleteButton?.addEventListener('click', async () => {
      const editor = state.editor;
      if (!editor?.pointDraft?.curve_id) return;
      const stored = editorPointCurves(editor.working).some(
        (curve) => curve.curve_id === editor.pointDraft.curve_id,
      );
      if (!stored) {
        editor.pointDraft = null; editor.selectedPointId = '';
        setEditorMessage('저장 전 포인트 곡선을 취소했습니다.'); renderEditor();
        return;
      }
      if (!await showConfirm(
        '선택한 포인트와 해당 곡선 구간의 그래프 데이터를 모두 삭제할까요?',
        { title: '포인트 곡선 삭제', confirmLabel: '삭제', tone: 'danger' },
      )) return;
      applyEditorOperation('delete_point_curve', false);
    });
    [el.studioEditorOffset, el.studioEditorFactor, el.studioEditorDelta].forEach((input) => {
      input?.addEventListener('input', () => {
        discardEditorPreview('편집값이 바뀌어 결과 미리보기를 취소했습니다. 다시 계산하세요.');
      });
    });
    el.studioEditorConvertToPointsButton?.addEventListener('click', () => {
      const editor = state.editor;
      if (!editor) return;
      if (editor.selectionKind !== 'motion' || editor.selectionStage === 1) {
        setEditorMessage('일반 모션점 두 개로 변환 구간을 먼저 선택하세요.', true);
        return;
      }
      if (editorSelectedMotionIds().length !== 1) {
        setEditorMessage('포인트로 변환할 Motion ID를 하나만 선택하세요.', true);
        return;
      }
      editor.pendingCurveId = editorId('curve');
      applyEditorOperation('convert_motion_to_point_curve', false);
    });
    const handleEditorRangeInput = () => {
      if (state.editor) {
        state.editor.selectionStage = 0;
        state.editor.selectionAnchor = null;
        state.editor.selectionKind = 'motion';
      }
      if (!discardEditorPreview('편집 구간이 바뀌어 결과 미리보기를 취소했습니다.')) {
        renderEditorControls();
        drawEditorGraph();
      }
    };
    el.studioEditorStart?.addEventListener('input', handleEditorRangeInput);
    el.studioEditorEnd?.addEventListener('input', handleEditorRangeInput);
    el.studioEditorSelectWholeRangeButton?.addEventListener('click', () => {
      const editor = state.editor;
      if (!editor) return;
      discardEditorPreview();
      const bounds = editorTimeBounds(editor.working);
      el.studioEditorStart.value = bounds.start.toFixed(2);
      el.studioEditorEnd.value = bounds.end.toFixed(2);
      editor.selectionStage = 0;
      editor.selectionAnchor = null;
      editor.selectionKind = 'motion';
      setEditorMessage(`레이어 전체 구간 선택 · ${bounds.start.toFixed(2)}초 ~ ${bounds.end.toFixed(2)}초`);
      renderEditorControls();
      drawEditorGraph();
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
    const acceptSavedEditorLayer = (editor, savedLayer, message) => {
      const previousWorking = editor.working;
      const selectedPointId = editor.selectedPointId;
      const activeCurveId = editor.pointDraft?.curve_id;
      editor.original = clone(savedLayer);
      editor.working = clone(savedLayer);
      editor.preview = null;
      editor.previewValidation = null;
      editor.operationReport = null;
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
          editor.saveFailureFingerprint = JSON.stringify({
            frames: editor.working?.frames || [],
            point_curves: editor.working?.point_curves || [],
            pointDraft: editor.pointDraft || null,
          });
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
        editor.saveFailureFingerprint = JSON.stringify({
          frames: editor.working?.frames || [],
          point_curves: editor.working?.point_curves || [],
          pointDraft: editor.pointDraft || null,
        });
        setEditorMessage('저장 실패 · 현재 작업본은 유지됩니다.', true);
        renderEditor();
      }
    });
    const setEditorView = (start, end) => {
      const editor = state.editor;
      if (!editor) return;
      const span = Math.max(0.04, end - start);
      editor.viewStart = Math.max(0, start);
      editor.viewEnd = editor.viewStart + span;
      drawEditorGraph();
    };
    const scaleEditorValues = (factor) => {
      const editor = state.editor;
      if (!editor) return;
      // No user-visible maximum: retain the last finite value only at the
      // JavaScript numeric overflow boundary.
      editor.valueScale = motionStudioEditorNextValueScale(editor.valueScale, factor);
    };
    el.studioEditorTimeZoomInButton?.addEventListener('click', () => {
      const editor = state.editor; if (!editor) return;
      const center = (editor.viewStart + editor.viewEnd) / 2;
      const span = (editor.viewEnd - editor.viewStart) * 0.6;
      setEditorView(center - span / 2, center + span / 2);
    });
    el.studioEditorTimeZoomOutButton?.addEventListener('click', () => {
      const editor = state.editor; if (!editor) return;
      const center = (editor.viewStart + editor.viewEnd) / 2;
      const span = (editor.viewEnd - editor.viewStart) * 1.7;
      setEditorView(center - span / 2, center + span / 2);
    });
    el.studioEditorValueZoomInButton?.addEventListener('click', () => {
      scaleEditorValues(0.6);
      drawEditorGraph();
    });
    el.studioEditorValueZoomOutButton?.addEventListener('click', () => {
      scaleEditorValues(1.7);
      drawEditorGraph();
    });
    el.studioEditorFitAllButton?.addEventListener('click', () => {
      const editor = state.editor; if (!editor) return;
      editor.valueScale = 1;
      setEditorView(0, Math.max(0.04, editorDuration(editor.working)));
    });
    el.studioEditorFitSelectionButton?.addEventListener('click', () => {
      if (!el.studioEditorStart?.value?.trim() || !el.studioEditorEnd?.value?.trim()) {
        setEditorMessage('먼저 시작점과 끝점을 선택하세요.', true);
        return;
      }
      const start = Number(el.studioEditorStart?.value || 0);
      const end = Number(el.studioEditorEnd?.value || start + 0.04);
      if (state.editor) state.editor.valueScale = 1;
      setEditorView(Math.min(start, end), Math.max(start, end));
    });
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
          setPointCurveMode(
            pendingDrag.curve,
            pendingDrag.pointId,
          );
          editor.draggingPoint = pendingDrag;
        }
        const point = selectedDraftPoint(editor);
        if (!point) return;
        const activeMetrics = editor.graphMetrics || metrics;
        const snappedTime = Math.max(
          0,
          Math.round(activeMetrics.timeFor(x) / 0.02) * 0.02,
        );
        const collides = (editor.pointDraft?.points || []).some(
          (candidate) => candidate.point_id !== point.point_id
            && Math.abs(Number(candidate.time_sec) - snappedTime) < 0.02 - 1e-9,
        );
        if (!collides) point.time_sec = Number(snappedTime.toFixed(2));
        point.value_deg = Number(activeMetrics.valueFor(y).toFixed(6));
        editor.pointDraft.points.sort((first, second) => first.time_sec - second.time_sec);
        editor.draggingPoint.moved = true;
        editor.suppressGraphClick = true;
        syncPointControls();
        drawEditorGraph();
        return;
      }
      if (editor.panningGraph) {
        const pixelDelta = x - editor.panningGraph.startX;
        if (Math.abs(pixelDelta) >= 3) editor.panningGraph.moved = true;
        if (editor.panningGraph.moved) {
          const timeDelta = -(pixelDelta / metrics.plotWidth) * editor.panningGraph.span;
          editor.suppressGraphClick = true;
          setEditorView(
            editor.panningGraph.startViewStart + timeDelta,
            editor.panningGraph.startViewEnd + timeDelta,
          );
        }
        return;
      }
      if (editor.draggingHandle) {
        const point = selectedDraftPoint(editor);
        if (!point) return;
        const side = editor.draggingHandle.side;
        let dtSec = metrics.timeFor(x) - Number(point.time_sec);
        if (side === 'in') dtSec = Math.min(-0.001, dtSec);
        else dtSec = Math.max(0.001, dtSec);
        const dvDeg = metrics.valueFor(y) - Number(point.value_deg);
        point[`${side}_handle`] = { dt_sec: dtSec, dv_deg: dvDeg };
        if ((point.tangent_mode || 'auto') !== 'smooth') point.tangent_mode = 'smooth';
        if (point.tangent_mode === 'smooth') {
          const opposite = side === 'in' ? 'out' : 'in';
          const oppositeHandle = point[`${opposite}_handle`] || {};
          const oppositeDt = Number(oppositeHandle.dt_sec || (side === 'in' ? 0.1 : -0.1));
          const slope = dvDeg / dtSec;
          point[`${opposite}_handle`] = { dt_sec: oppositeDt, dv_deg: slope * oppositeDt };
        }
        editor.suppressGraphClick = true;
        syncPointControls();
        drawEditorGraph();
        return;
      }
      const { padding } = metrics;
      if (x < padding.left || x > padding.left + metrics.plotWidth || y < padding.top || y > padding.top + metrics.plotHeight) {
        editor.cursor = null;
        if (el.studioEditorCursorInfo) el.studioEditorCursorInfo.textContent = '그래프 안쪽에서 지점을 선택하세요';
        drawEditorGraph();
        return;
      }
      const timeSec = Math.max(0, metrics.timeFor(x)); const value = metrics.valueFor(y);
      let nearest = null; let nearestDistance = 18;
      const selected = new Set(editorSelectedMotionIds());
      for (const [motionId, points] of layerTracks(editor.preview || editor.working).entries()) {
        if (!selected.has(motionId)) continue;
        for (const point of points) {
          const distance = Math.hypot(metrics.xFor(point.timeSec) - x, metrics.yFor(point.value) - y);
          if (distance < nearestDistance) {
            nearestDistance = distance; nearest = { motionId, ...point };
          }
        }
      }
      editor.cursor = { x, y, timeSec, value, nearest };
      if (el.studioEditorCursorInfo) el.studioEditorCursorInfo.textContent = nearest
        ? `${nearest.motionId} · ${nearest.timeSec.toFixed(3)}초 · ${nearest.value.toFixed(3)}°`
        : `${timeSec.toFixed(3)}초 · ${value.toFixed(3)}°`;
      drawEditorGraph();
    });
    el.studioEditorGraph?.addEventListener('mouseleave', () => {
      if (state.editor) state.editor.cursor = null;
      if (el.studioEditorCursorInfo) el.studioEditorCursorInfo.textContent = '그래프 위에 마우스를 올리세요';
      drawEditorGraph();
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
      if (pointTarget && el.studioEditorOperation?.value !== 'point_curve') {
        setPointCurveMode(pointTarget.curve, pointTarget.point.point_id);
        setEditorMessage(
          pointCurveIsSaved(editor, pointTarget.curve.curve_id)
            ? `${pointTarget.curve.motion_id} 포인트 모션 구간 선택 · 포인트 편집이 가능합니다.`
            : '변환된 포인트 모션을 먼저 저장해야 편집할 수 있습니다.',
          !pointCurveIsSaved(editor, pointTarget.curve.curve_id),
        );
        return;
      }
      if (motionStudioShouldEditPoint(el.studioEditorOperation?.value, pointTarget)) {
        if (
          !pointCurveIsSaved(editor, pointTarget.curve.curve_id)
          && !pointCurveCanBeCreated(editor)
        ) {
          setPointCurveMode(pointTarget.curve, pointTarget.point.point_id);
          setEditorMessage('변환된 포인트 모션을 먼저 저장해야 편집할 수 있습니다.', true);
          return;
        }
        if (editor.preview) {
          setEditorMessage(
            '현재 결과 미리보기를 먼저 편집 반영한 뒤 포인트를 수정하세요.',
            true,
          );
          return;
        }
        setPointCurveMode(pointTarget.curve, pointTarget.point.point_id);
        setEditorMessage(
          `${pointTarget.curve.motion_id} 포인트 선택 · 포인트를 드래그하거나 시간·모션값을 수정하세요.`,
        );
        return;
      }
      discardEditorPreview('편집 구간을 다시 선택하여 결과 미리보기를 취소했습니다.');
      if (el.studioEditorOperation?.value === 'point_curve') {
        if (
          !pointCurveIsSaved(editor, editor.pointDraft?.curve_id)
          && !pointCurveCanBeCreated(editor)
        ) {
          setEditorMessage(
            editor.pointDraft
              ? '변환된 포인트 모션을 먼저 저장해야 편집할 수 있습니다.'
              : '일반 모션을 포인트 모션으로 변환하고 저장한 뒤 편집하세요.',
            true,
          );
          return;
        }
        const selectedIds = editorSelectedMotionIds();
        if (selectedIds.length !== 1) {
          setEditorMessage('포인트를 만들 Motion ID를 하나만 선택하세요.', true);
          return;
        }
        const motionId = selectedIds[0];
        if (!editor.pointDraft || editor.pointDraft.motion_id !== motionId) {
          editor.pointDraft = {
            curve_id: editorId('curve'), motion_id: motionId,
            interpolation_order: Number(editor.pointCurveOrder || 3),
            points: [],
          };
        }
        const timeSec = Math.max(0, Math.round(metrics.timeFor(clickPoint.x) / 0.02) * 0.02);
        if (editor.pointDraft.points.some(
          (point) => Math.abs(Number(point.time_sec) - timeSec) < 0.01,
        )) {
          setEditorMessage('같은 시간에는 포인트를 하나만 만들 수 있습니다.', true);
          return;
        }
        const point = {
          point_id: editorId('point'),
          time_sec: Number(timeSec.toFixed(2)),
          value_deg: Number(metrics.valueFor(clickPoint.y).toFixed(6)),
          tangent_mode: 'auto', in_handle: {}, out_handle: {},
        };
        editor.pointDraft.points.push(point);
        editor.pointDraft.points.sort((first, second) => first.time_sec - second.time_sec);
        editor.selectedPointId = point.point_id;
        setEditorMessage(
          `${motionId} 포인트 추가 · ${point.time_sec.toFixed(2)}초 · ${point.value_deg.toFixed(3)}°`,
        );
        renderEditor();
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
      const cursorMatchesClick = editor.cursor
        && Math.hypot(editor.cursor.x - clickPoint.x, editor.cursor.y - clickPoint.y) < 3;
      const clickKind = pointTarget ? 'point' : 'motion';
      const motionTarget = cursorMatchesClick ? editor.cursor.nearest : null;
      if (clickKind === 'motion' && !motionTarget) {
        setEditorMessage('포인트 또는 그래프의 모션점 가까이를 클릭하세요.', true);
        return;
      }
      if (clickKind === 'motion') {
        const pointRegion = editorPointCurves(editor.preview || editor.working).find(
          (curve) => {
            if (curve.motion_id !== motionTarget.motionId) return false;
            const points = curve.points || [];
            const startSec = Number(points[0]?.time_sec);
            const endSec = Number(points[points.length - 1]?.time_sec);
            return (
              Number(motionTarget.timeSec) >= startSec - 1e-9
              && Number(motionTarget.timeSec) <= endSec + 1e-9
            );
          },
        );
        if (pointRegion) {
          setPointCurveMode(pointRegion, pointRegion.points?.[0]?.point_id || '');
          setEditorMessage(
            pointCurveIsSaved(editor, pointRegion.curve_id)
              ? '포인트 모션 구간입니다. 동그란 포인트를 선택해 편집하세요.'
              : '변환된 포인트 모션을 먼저 저장해야 편집할 수 있습니다.',
            !pointCurveIsSaved(editor, pointRegion.curve_id),
          );
          return;
        }
      }
      if (clickKind === 'motion' && editor.pointDraft) {
        editor.pointDraft = null;
        editor.selectedPointId = '';
        renderEditorControls();
      }
      const targetTime = pointTarget
        ? Number(pointTarget.point.time_sec)
        : Number(motionTarget.timeSec);
      const snapped = Math.max(0, Math.round(targetTime / 0.02) * 0.02);
      if (editor.selectionStage === 0) {
        el.studioEditorStart.value = snapped.toFixed(2);
        el.studioEditorEnd.value = '';
        editor.selectionStage = 1;
        editor.selectionAnchor = snapped;
        editor.selectionKind = clickKind;
        const kindText = clickKind === 'point' ? '포인트' : '모션점';
        setEditorMessage(
          `${kindText} 구간 선택 1/2 · 시작 ${snapped.toFixed(2)}초 · `
          + `종료할 다른 ${kindText}을 한 번 클릭하세요.`,
        );
      } else {
        if (!motionStudioSelectionKindsMatch(editor.selectionKind, clickKind)) {
          setEditorMessage(
            '포인트는 포인트끼리, 모션점은 모션점끼리 선택하세요.',
            true,
          );
          return;
        }
        const first = Number.isFinite(editor.selectionAnchor)
          ? editor.selectionAnchor
          : Number(el.studioEditorStart.value || 0);
        el.studioEditorStart.value = Math.min(first, snapped).toFixed(2);
        el.studioEditorEnd.value = Math.max(first, snapped).toFixed(2);
        editor.selectionStage = 0;
        editor.selectionAnchor = null;
        const kindText = clickKind === 'point' ? '포인트' : '모션점';
        setEditorMessage(
          `${kindText} 구간 선택 2/2 완료 · `
          + `${el.studioEditorStart.value}초 ~ ${el.studioEditorEnd.value}초`,
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
          !pointCurveIsSaved(editor, pointTarget.curve.curve_id)
          && !pointCurveCanBeCreated(editor)
        ) {
          setPointCurveMode(pointTarget.curve, pointTarget.point.point_id);
          setEditorMessage('변환된 포인트 모션을 먼저 저장해야 편집할 수 있습니다.', true);
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
        if (pointMode) {
          setPointCurveMode(pointTarget.curve, pointTarget.point.point_id);
        }
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
            : '한 번 클릭하면 현재 편집 구간으로 선택하고, 드래그하면 포인트를 이동합니다.',
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
          startViewStart: editor.viewStart,
          startViewEnd: editor.viewEnd,
          span: editor.viewEnd - editor.viewStart,
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
          setEditorMessage('시간축 표시 구간을 좌우로 이동했습니다.');
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
        setEditorView(editor.viewStart + delta, editor.viewEnd + delta);
        return;
      }
      const center = editor.cursor?.timeSec ?? ((editor.viewStart + editor.viewEnd) / 2);
      const zoomFactor = event.deltaY < 0 ? 0.8 : 1.25;
      const newSpan = span * zoomFactor;
      const ratio = (center - editor.viewStart) / span;
      setEditorView(center - (newSpan * ratio), center + (newSpan * (1 - ratio)));
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
      state.status = studioStatus;
      const feedback = motionStudioRuntimeStatusMessage(previousStatus, studioStatus);
      if (feedback) setMessage(feedback.message, feedback.error);
    }
    if (midiStatus) state.midi = midiStatus;
    syncPlaybackClock();
    renderAxes(); renderControls();
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
