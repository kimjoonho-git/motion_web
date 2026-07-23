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

export function motionStudioLayerDuration(layer) {
  return Math.max(
    0,
    ...(layer?.frames || [])
      .map((frame) => Number(frame.time_sec))
      .filter((timeSec) => Number.isFinite(timeSec) && timeSec >= 0),
  );
}

export function motionStudioEditorValueBounds(minimum, maximum, scale = 1) {
  let minValue = Number.isFinite(Number(minimum)) ? Number(minimum) : -1;
  let maxValue = Number.isFinite(Number(maximum)) ? Number(maximum) : 1;
  if (maxValue < minValue) [minValue, maxValue] = [maxValue, minValue];
  if (Math.abs(maxValue - minValue) < 1e-9) {
    minValue -= 1;
    maxValue += 1;
  }
  const numericScale = Number(scale);
  const valueScale = Number.isFinite(numericScale) && numericScale > 0 ? numericScale : 1;
  const center = (minValue + maxValue) / 2;
  const halfSpan = ((maxValue - minValue) / 2) * valueScale;
  return { minValue: center - halfSpan, maxValue: center + halfSpan };
}

export function motionStudioEditorNextValueScale(currentScale, factor) {
  const current = Number.isFinite(Number(currentScale)) && Number(currentScale) > 0
    ? Number(currentScale) : 1;
  const multiplier = Number(factor);
  if (!Number.isFinite(multiplier) || multiplier <= 0) return current;
  const next = current * multiplier;
  return Number.isFinite(next) && next > 0 ? next : current;
}

export function synchronizeMotionStudioEditorTimeline(editor, layer, previousLayer = null) {
  if (!editor) return false;
  const duration = motionStudioLayerDuration(layer);
  const previousDuration = previousLayer === null
    ? Number.NaN
    : motionStudioLayerDuration(previousLayer);
  if (Number.isFinite(previousDuration) && Math.abs(duration - previousDuration) <= 1e-9) {
    return false;
  }
  editor.viewStart = 0;
  editor.viewEnd = Math.max(0.02, duration);
  editor.selectionStage = 0;
  editor.selectionAnchor = null;
  return true;
}

export function motionStudioLayerDataEqual(first, second) {
  return JSON.stringify({
    frames: first?.frames || [],
    point_curves: first?.point_curves || [],
  }) === JSON.stringify({
    frames: second?.frames || [],
    point_curves: second?.point_curves || [],
  });
}

export function motionStudioLayerMotionIds(layer) {
  return [...new Set((layer?.frames || []).flatMap(
    (frame) => Object.keys(frame?.values || {}),
  ))];
}

export function resolveMotionStudioSelectedLayerId(layers, selectedLayerId = '') {
  const available = Array.isArray(layers) ? layers : [];
  if (available.some((layer) => layer.layer_id === selectedLayerId)) return selectedLayerId;
  return String(available[0]?.layer_id || '');
}

export function motionStudioShouldEditPoint(operation, pointTarget) {
  return operation === 'point_curve' && Boolean(pointTarget);
}

export function motionStudioSelectionKindsMatch(firstKind, secondKind) {
  return ['point', 'motion'].includes(firstKind) && firstKind === secondKind;
}

export function motionStudioPointHitTarget(targets, x, y, radius = 14) {
  return (Array.isArray(targets) ? targets : []).find(
    (target) => Math.hypot(Number(target.x) - x, Number(target.y) - y) <= radius,
  ) || null;
}

export function motionStudioPointDragStarted(draggingPoint, x, y) {
  if (!draggingPoint) return false;
  if (draggingPoint.moved) return true;
  return Math.hypot(
    x - Number(draggingPoint.startX),
    y - Number(draggingPoint.startY),
  ) >= 3;
}

export function motionStudioPointCurveViewEnd(
  layerDuration,
  currentViewEnd = 0,
  requestedViewEnd = 0,
) {
  return Math.max(
    10,
    Number(layerDuration) || 0,
    Number(currentViewEnd) || 0,
    Number(requestedViewEnd) || 0,
  );
}

export function motionStudioPointCurvePreview(rawPoints, interpolationOrder = 3) {
  const points = (Array.isArray(rawPoints) ? rawPoints : [])
    .map((point) => ({
      ...point,
      time_sec: Number(point?.time_sec),
      value_deg: Number(point?.value_deg),
    }))
    .filter((point) => Number.isFinite(point.time_sec) && Number.isFinite(point.value_deg))
    .sort((first, second) => first.time_sec - second.time_sec);
  if (points.length < 2) return [];
  const order = [1, 3, 5].includes(Number(interpolationOrder))
    ? Number(interpolationOrder) : 3;
  const automaticSlope = (index) => {
    const before = points[Math.max(0, index - 1)];
    const after = points[Math.min(points.length - 1, index + 1)];
    const span = after.time_sec - before.time_sec;
    return span > 1e-9 ? (after.value_deg - before.value_deg) / span : 0;
  };
  const pointSlope = (index) => {
    if (index === 0 || index === points.length - 1) return 0;
    const point = points[index];
    if (point.tangent_mode === 'broken') return 0;
    if (point.tangent_mode === 'smooth') {
      const handle = point.out_handle || point.in_handle || {};
      const dt = Number(handle.dt_sec);
      const dv = Number(handle.dv_deg);
      if (Number.isFinite(dt) && Number.isFinite(dv) && Math.abs(dt) > 1e-9) {
        return dv / dt;
      }
    }
    return automaticSlope(index);
  };
  const acceleration = (index) => {
    if (index <= 0 || index >= points.length - 1) return 0;
    if (points[index].tangent_mode === 'broken') return 0;
    const before = points[index - 1];
    const point = points[index];
    const after = points[index + 1];
    const previousSpan = point.time_sec - before.time_sec;
    const followingSpan = after.time_sec - point.time_sec;
    if (previousSpan <= 1e-9 || followingSpan <= 1e-9) return 0;
    const previousSlope = (point.value_deg - before.value_deg) / previousSpan;
    const followingSlope = (after.value_deg - point.value_deg) / followingSpan;
    return 2 * (followingSlope - previousSlope) / (previousSpan + followingSpan);
  };
  const result = [];
  points.slice(0, -1).forEach((first, index) => {
    const second = points[index + 1];
    const span = second.time_sec - first.time_sec;
    if (span <= 1e-9) return;
    const steps = Math.max(8, Math.min(80, Math.ceil(span / 0.02)));
    for (let step = 0; step <= steps; step += 1) {
      if (index > 0 && step === 0) continue;
      const ratio = step / steps;
      let value;
      if (order === 1) {
        value = first.value_deg + ((second.value_deg - first.value_deg) * ratio);
      } else if (order === 3) {
        const ratio2 = ratio * ratio;
        const ratio3 = ratio2 * ratio;
        value = (
          (((2 * ratio3) - (3 * ratio2) + 1) * first.value_deg)
          + ((ratio3 - (2 * ratio2) + ratio) * span * pointSlope(index))
          + (((-2 * ratio3) + (3 * ratio2)) * second.value_deg)
          + ((ratio3 - ratio2) * span * pointSlope(index + 1))
        );
      } else {
        const firstSlope = pointSlope(index);
        const secondSlope = pointSlope(index + 1);
        const firstAcceleration = acceleration(index);
        const secondAcceleration = acceleration(index + 1);
        const delta = second.value_deg - first.value_deg;
        const c0 = first.value_deg;
        const c1 = firstSlope * span;
        const c2 = 0.5 * firstAcceleration * span * span;
        const remainingValue = delta - c1 - c2;
        const remainingSlope = (secondSlope * span) - c1 - (2 * c2);
        const remainingAcceleration = (secondAcceleration * span * span) - (2 * c2);
        const c3 = (10 * remainingValue) - (4 * remainingSlope)
          + (0.5 * remainingAcceleration);
        const c4 = (-15 * remainingValue) + (7 * remainingSlope)
          - remainingAcceleration;
        const c5 = (6 * remainingValue) - (3 * remainingSlope)
          + (0.5 * remainingAcceleration);
        value = c0 + (c1 * ratio) + (c2 * ratio ** 2) + (c3 * ratio ** 3)
          + (c4 * ratio ** 4) + (c5 * ratio ** 5);
      }
      result.push({ timeSec: first.time_sec + (span * ratio), value });
    }
  });
  return result;
}

export function createMotionStudioController({ el, getMotorActionBlockReason = () => '' }) {
  const state = {
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

  function setMessage(message, error = false) {
    if (!el.studioMessage) return;
    el.studioMessage.textContent = message || '';
    el.studioMessage.classList.toggle('error-text', error);
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
    if (el.studioWorkspaceName) {
      el.studioWorkspaceName.textContent = state.workspaceProject?.name || '통합 프로젝트 미선택';
    }
    if (el.studioWorkspaceFiles) {
      const active = state.workspaceProject?.active_files || {};
      el.studioWorkspaceFiles.textContent = state.workspaceProject
        ? `모터축: ${active.motor_axes || '미선택'} · 매칭: ${active.motion_axis_matching || '미선택'} · 모션: ${active.motions || '미선택'}`
        : '왼쪽에서 프로젝트와 현재 파일을 선택하세요';
    }
    if (el.studioImportFileSelect) {
      const selected = el.studioImportFileSelect.value;
      el.studioImportFileSelect.innerHTML = '<option value="">가져올 모션 파일 선택</option>' + state.motionFiles.map((item) => (
        `<option value="${escapeHtml(item.file_id)}" ${item.valid ? '' : 'disabled'}>${escapeHtml(item.title || item.file_id)} · ${item.frame_count}프레임${item.valid ? '' : ' · 오류'}</option>`
      )).join('');
      el.studioImportFileSelect.value = selected;
    }
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

  function layerTracks(layer) {
    const tracks = new Map();
    for (const frame of layer?.frames || []) {
      const timeSec = Number(frame.time_sec || 0);
      for (const [motionId, rawValue] of Object.entries(frame.values || {})) {
        const value = Number(rawValue);
        if (!Number.isFinite(timeSec) || !Number.isFinite(value)) continue;
        if (!tracks.has(motionId)) tracks.set(motionId, []);
        tracks.get(motionId).push({ timeSec, value });
      }
    }
    for (const points of tracks.values()) {
      points.sort((left, right) => left.timeSec - right.timeSec);
    }
    return tracks;
  }

  function sampleTrack(points, timeSec) {
    if (!points?.length) return null;
    let low = 0; let high = points.length;
    while (low < high) {
      const middle = Math.floor((low + high) / 2);
      if (points[middle].timeSec < timeSec) low = middle + 1;
      else high = middle;
    }
    const point = points[low];
    if (point && Math.abs(point.timeSec - timeSec) < 1e-7) return point.value;
    const previous = points[low - 1];
    if (!point || !previous) return null;
    const span = point.timeSec - previous.timeSec;
    if (span > 0.031 || span <= 0) return null;
    const ratio = (timeSec - previous.timeSec) / span;
    return previous.value + ((point.value - previous.value) * ratio);
  }

  function compositionTracks(layers, mappingRows = []) {
    const enabledLayers = layers.filter((layer) => layer.enabled !== false);
    const sources = enabledLayers.map((layer) => layerTracks(layer));
    const motionIds = new Set(sources.flatMap((tracks) => [...tracks.keys()]));
    const manualInitialValues = new Map(mappingRows.filter((row) => (
      String(row.initial_mode || 'first_frame') === 'manual'
    )).map((row) => [String(row.motion_id), Number(row.initial_motion_position_deg || 0)]));
    const firstPoints = new Map();
    for (const source of sources) {
      for (const [motionId, points] of source.entries()) {
        if (!points.length) continue;
        const current = firstPoints.get(motionId);
        if (!current || points[0].timeSec < current.timeSec) firstPoints.set(motionId, points[0]);
      }
    }
    const duration = Math.max(0, ...enabledLayers.flatMap((layer) => (
      (layer.frames || []).map((frame) => Number(frame.time_sec || 0))
    )));
    const sampleCount = Math.max(0, Math.ceil(duration / 0.02));
    const tracks = new Map([...motionIds].map((motionId) => [motionId, []]));
    const lastValues = new Map([...motionIds].map((motionId) => {
      const firstPoint = firstPoints.get(motionId);
      return [motionId, manualInitialValues.has(motionId)
        ? manualInitialValues.get(motionId) : Number(firstPoint?.value || 0)];
    }));
    for (let index = 1; index <= sampleCount; index += 1) {
      const timeSec = Number((index * 0.02).toFixed(9));
      for (const motionId of motionIds) {
        let value = null;
        for (const source of sources) {
          const candidate = sampleTrack(source.get(motionId), timeSec);
          if (candidate !== null) value = candidate;
        }
        const firstPoint = firstPoints.get(motionId);
        if (value === null && firstPoint && timeSec < firstPoint.timeSec) {
          value = manualInitialValues.has(motionId)
            ? manualInitialValues.get(motionId)
            : firstPoint.value;
        }
        if (value === null) value = lastValues.get(motionId) ?? 0;
        lastValues.set(motionId, value);
        tracks.get(motionId).push({ timeSec, value });
      }
    }
    return { tracks, duration, sampleCount, enabledLayers };
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
    const canvas = el.studioLayerGraph;
    if (!canvas) return;
    const width = Math.max(520, Math.floor(canvas.getBoundingClientRect().width || 760));
    const height = 320;
    const ratio = window.devicePixelRatio || 1;
    canvas.width = Math.floor(width * ratio);
    canvas.height = Math.floor(height * ratio);
    const context = canvas.getContext('2d');
    context.setTransform(ratio, 0, 0, ratio, 0, 0);
    context.clearRect(0, 0, width, height);
    const allPoints = [...tracks.values()].flat();
    if (!allPoints.length) {
      el.studioLayerPlayhead?.classList.add('hidden');
      context.fillStyle = '#5d6b78';
      context.font = '13px sans-serif';
      context.fillText('그래프 데이터 없음', 16, 28);
      return;
    }
    const padding = { left: 52, right: 18, top: 18, bottom: 34 };
    const plotWidth = width - padding.left - padding.right;
    const plotHeight = height - padding.top - padding.bottom;
    const maxTime = Math.max(...allPoints.map((point) => point.timeSec), 0.02);
    let minValue = Math.min(...allPoints.map((point) => point.value));
    let maxValue = Math.max(...allPoints.map((point) => point.value));
    if (Math.abs(maxValue - minValue) < 1e-9) {
      minValue -= 1;
      maxValue += 1;
    }
    context.strokeStyle = '#d9e0e7';
    context.lineWidth = 1;
    context.strokeRect(padding.left, padding.top, plotWidth, plotHeight);
    context.fillStyle = '#5d6b78';
    context.font = '11px sans-serif';
    context.fillText(`${maxValue.toFixed(2)}°`, 4, padding.top + 4);
    context.fillText(`${minValue.toFixed(2)}°`, 4, padding.top + plotHeight);
    context.fillText('0초', padding.left, height - 10);
    context.fillText(`${maxTime.toFixed(3)}초`, width - padding.right - 58, height - 10);
    const colors = ['#1f6feb', '#d97706', '#16803c', '#a23ab7', '#d33b3b', '#0f8b8d'];
    [...tracks.entries()].forEach(([, points], index) => {
      context.beginPath();
      context.strokeStyle = colors[index % colors.length];
      context.lineWidth = 2;
      points.forEach((point, pointIndex) => {
        const x = padding.left + ((point.timeSec / maxTime) * plotWidth);
        const y = padding.top + (((maxValue - point.value) / (maxValue - minValue)) * plotHeight);
        const previous = pointIndex > 0 ? points[pointIndex - 1] : null;
        if (!previous || point.timeSec - previous.timeSec > 0.031) context.moveTo(x, y);
        else context.lineTo(x, y);
      });
      context.stroke();
    });
    context.save();
    context.strokeStyle = '#d33b3b';
    context.fillStyle = '#d33b3b';
    context.setLineDash([4, 3]);
    warnings.forEach((warning) => {
      const timeSec = Number(warning.second_time_sec);
      if (!Number.isFinite(timeSec)) return;
      const x = padding.left + ((Math.min(maxTime, Math.max(0, timeSec)) / maxTime) * plotWidth);
      context.beginPath();
      context.moveTo(x, padding.top);
      context.lineTo(x, padding.top + plotHeight);
      context.stroke();
    });
    context.restore();
    updatePlaybackPlayhead(playback);
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

  function pointDraftHasUnsavedChanges(editor = state.editor) {
    if (!editor?.pointDraft) return false;
    const stored = storedCurveForDraft(editor);
    return !stored || JSON.stringify(stored) !== JSON.stringify(editor.pointDraft);
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
    }
    renderEditor();
  }

  function syncPointControls() {
    const editor = state.editor;
    const point = selectedDraftPoint(editor);
    const pointMode = el.studioEditorOperation?.value === 'point_curve';
    [el.studioEditorPointTime, el.studioEditorPointValue, el.studioEditorPointMode].forEach((field) => {
      if (field) field.disabled = !pointMode || !point;
    });
    if (el.studioEditorPointDeleteButton) {
      const canDeletePoint = pointMode
        && Boolean(point)
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
      el.studioEditorCurveDetachButton.disabled = !pointMode || !storedPointCurve;
    }
    if (el.studioEditorCurveDeleteButton) {
      el.studioEditorCurveDeleteButton.disabled = !pointMode || !editor?.pointDraft?.curve_id;
    }
    if (el.studioEditorPointCurveOrder) {
      el.studioEditorPointCurveOrder.disabled = !pointMode;
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
        }>${escapeHtml(motionId)}</label>`
      )).join('');
    }

    if (el.studioEditorAddAxisButton) {
      el.studioEditorAddAxisButton.disabled = !el.studioEditorAddAxisSelect?.value.trim()
        || Boolean(editor.preview);
    }
    if (el.studioEditorCopyAxisSource) {
      const previousSource = el.studioEditorCopyAxisSource.value;
      el.studioEditorCopyAxisSource.innerHTML = ids.length
        ? ids.map((motionId) => `<option value="${escapeHtml(motionId)}">${escapeHtml(motionId)}</option>`).join('')
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
    };
    if (el.studioEditorTitle) el.studioEditorTitle.textContent = `레이어 편집 · ${layer.name}`;
    if (el.studioEditorSubtitle) el.studioEditorSubtitle.textContent = '편집 반영 0회 · 아직 저장되지 않음';
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
    if (el.studioEditorUndoButton) el.studioEditorUndoButton.disabled = !editor?.undo.length;
    if (el.studioEditorRedoButton) el.studioEditorRedoButton.disabled = !editor?.redo.length;
    if (el.studioEditorUpdateButton) el.studioEditorUpdateButton.disabled = !editor?.preview;
    if (el.studioEditorApplyButton) el.studioEditorApplyButton.disabled = Boolean(editor?.preview);
    if (el.studioEditorSaveButton) el.studioEditorSaveButton.disabled = Boolean(editor?.preview);
    if (el.studioEditorOperationTitle) {
      el.studioEditorOperationTitle.textContent = pointMode
        ? '포인트 곡선 편집'
        : '선택 구간 편집';
    }
    el.studioEditorScopeControls?.classList.toggle('hidden', pointMode);
    if (el.studioEditorFitSelectionButton) {
      el.studioEditorFitSelectionButton.disabled = pointMode;
    }
    if (el.studioEditorDeleteAxisDataButton) {
      el.studioEditorDeleteAxisDataButton.disabled = pointMode || Boolean(editor?.preview);
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
    document.querySelectorAll('[data-studio-editor-value]').forEach((field) => {
      const kind = field.dataset.studioEditorValue;
      field.classList.toggle('hidden', !(
        (operation === 'value_offset' && kind === 'offset')
        || ((operation === 'value_scale' || operation === 'time_scale') && kind === 'factor')
        || (operation === 'time_shift' && kind === 'delta')
        || (operation === 'repair_spikes' && kind === 'spike')
        || (operation === 'interpolate' && kind === 'curve')
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
        time_scale: '시작점을 기준으로 선택 구간의 시간을 조절합니다. 포인트는 포인트끼리, 모션점은 모션점끼리 선택하세요. 포인트 구간은 포인트·탄젠트가 함께 변경됩니다.',
        value_scale: '시작점의 모션값을 기준으로 변화량을 조절합니다. 포인트는 포인트끼리, 모션점은 모션점끼리 선택하세요. 포인트 구간은 포인트·탄젠트가 함께 변경됩니다.',
        time_shift: '선택 구간 전체를 입력한 시간만큼 이동합니다. 포인트 곡선 안의 모션점을 편집하려면 먼저 포인트 연결을 해제하세요.',
        value_offset: '선택 구간 전체를 입력한 각도만큼 이동합니다. 포인트 곡선 안의 모션점을 편집하려면 먼저 포인트 연결을 해제하세요.',
        repair_spikes: '선택 구간의 20ms 프레임 중 주변 흐름에서 혼자 벗어난 한 프레임만 보정합니다. 연속 튀임·최대 보정량 초과·포인트 곡선은 변경하지 않습니다.',
        interpolate: '선택한 실제 시작점과 끝점 사이를 20ms 간격으로 다시 채웁니다. 1차·3차·5차 결과를 미리 본 뒤 편집 반영하세요.',
        point_curve: '축의 기존 종료시간과 관계없이 포인트를 추가할 수 있습니다. 일반 편집에서는 포인트끼리 또는 모션점끼리 구간을 선택하며, 포인트를 드래그하면 포인트 편집으로 전환해 이동합니다.',
      };
      el.studioEditorOperationHelp.textContent = help[operation] || '';
    }
    if (el.studioEditorSpikeReport) {
      const report = operation === 'repair_spikes' ? editor?.operationReport : null;
      el.studioEditorSpikeReport.classList.toggle('hidden', !report);
      if (report) {
        const changed = report.changed || [];
        const excluded = report.excluded || [];
        const rows = [
          ...changed.map((item) => ({ ...item, status: '보정' })),
          ...excluded.map((item) => ({
            ...item,
            status: `제외 · ${item.reason_text || '기준 미충족'}`,
          })),
        ];
        const detail = rows.slice(0, 20).map((item) => (
          `<div>${escapeHtml(item.motion_id)} · ${Number(item.time_sec).toFixed(3)}초 · `
          + `${Number(item.before_deg).toFixed(3)}° → ${Number(item.after_deg).toFixed(3)}° · `
          + `${escapeHtml(item.status)}</div>`
        )).join('');
        el.studioEditorSpikeReport.innerHTML = (
          `<strong>보정 ${changed.length}개 · 제외 ${excluded.length}개 · `
          + `최대 변경 ${Number(report.maximum_applied_correction_deg || 0).toFixed(3)}°</strong>`
          + detail
          + (rows.length > 20 ? `<div>외 ${rows.length - 20}개</div>` : '')
        );
      } else {
        el.studioEditorSpikeReport.textContent = '';
      }
    }
    syncPointControls();
  }

  function drawEditorGraph() {
    const editor = state.editor;
    const canvas = el.studioEditorGraph;
    if (!editor || !canvas) return;
    const selected = new Set(editorSelectedMotionIds());
    const originalTracks = layerTracks(editor.original);
    const displayedLayer = editor.preview || editor.working;
    const workingTracks = layerTracks(displayedLayer);
    const ids = [...new Set([...originalTracks.keys(), ...workingTracks.keys()])]
      .filter((motionId) => selected.has(motionId));
    const draftPreview = (
      editor.pointDraft && selected.has(editor.pointDraft.motion_id)
    ) ? motionStudioPointCurvePreview(
        editor.pointDraft.points,
        editor.pointDraft.interpolation_order || editor.pointCurveOrder,
      ) : [];
    const width = Math.max(680, Math.floor(canvas.getBoundingClientRect().width || 900));
    const height = Math.max(210, Math.floor(canvas.getBoundingClientRect().height || 320));
    const ratio = window.devicePixelRatio || 1;
    canvas.width = Math.floor(width * ratio);
    canvas.height = Math.floor(height * ratio);
    const context = canvas.getContext('2d');
    context.setTransform(ratio, 0, 0, ratio, 0, 0);
    context.clearRect(0, 0, width, height);
    const padding = { left: 62, right: 22, top: 22, bottom: 42 };
    const plotWidth = width - padding.left - padding.right;
    const plotHeight = height - padding.top - padding.bottom;
    const viewStart = Math.max(0, Number(editor.viewStart || 0));
    const viewEnd = Math.max(viewStart + 0.02, Number(editor.viewEnd || 0.02));
    const visiblePoints = ids.flatMap((motionId) => [
      ...(workingTracks.get(motionId) || []), ...(originalTracks.get(motionId) || []),
    ].filter((point) => point.timeSec >= viewStart - 1e-9 && point.timeSec <= viewEnd + 1e-9))
      .concat(draftPreview.filter(
        (point) => point.timeSec >= viewStart - 1e-9 && point.timeSec <= viewEnd + 1e-9,
      ));
    const valueSource = visiblePoints.length ? visiblePoints : ids.flatMap((id) => [
      ...(workingTracks.get(id) || []), ...(originalTracks.get(id) || []),
    ]);
    const automaticMinValue = valueSource.length
      ? Math.min(...valueSource.map((point) => point.value)) : -1;
    const automaticMaxValue = valueSource.length
      ? Math.max(...valueSource.map((point) => point.value)) : 1;
    const { minValue, maxValue } = motionStudioEditorValueBounds(
      automaticMinValue,
      automaticMaxValue,
      editor.valueScale,
    );
    const xFor = (timeSec) => padding.left + (((timeSec - viewStart) / (viewEnd - viewStart)) * plotWidth);
    const yFor = (value) => padding.top + (((maxValue - value) / (maxValue - minValue)) * plotHeight);
    const timeFor = (x) => viewStart + (((x - padding.left) / plotWidth) * (viewEnd - viewStart));
    const valueFor = (y) => maxValue - (((y - padding.top) / plotHeight) * (maxValue - minValue));
    editor.graphMetrics = { padding, plotWidth, plotHeight, width, height, viewStart, viewEnd, minValue, maxValue, xFor, yFor, timeFor, valueFor };
    context.fillStyle = '#fff'; context.fillRect(0, 0, width, height);
    const selectionStartText = el.studioEditorStart?.value?.trim() || '';
    const selectionEndText = el.studioEditorEnd?.value?.trim() || '';
    const selectionStart = Number(selectionStartText);
    const selectionEnd = Number(selectionEndText);
    if (editor.selectionStage === 1 && Number.isFinite(editor.selectionAnchor)) {
      const anchor = Math.max(viewStart, Math.min(viewEnd, editor.selectionAnchor));
      context.strokeStyle = '#1f6feb';
      context.lineWidth = 2;
      context.beginPath();
      context.moveTo(xFor(anchor), padding.top);
      context.lineTo(xFor(anchor), padding.top + plotHeight);
      context.stroke();
    } else if (
      selectionStartText && selectionEndText
      && Number.isFinite(selectionStart) && Number.isFinite(selectionEnd)
    ) {
      const shadeStart = Math.max(viewStart, Math.min(selectionStart, selectionEnd));
      const shadeEnd = Math.min(viewEnd, Math.max(selectionStart, selectionEnd));
      if (shadeEnd >= shadeStart) {
        context.fillStyle = 'rgba(31, 111, 235, 0.10)';
        context.fillRect(xFor(shadeStart), padding.top, Math.max(1, xFor(shadeEnd) - xFor(shadeStart)), plotHeight);
      }
    }
    context.strokeStyle = '#d9e0e7'; context.strokeRect(padding.left, padding.top, plotWidth, plotHeight);
    context.fillStyle = '#5d6b78'; context.font = '11px sans-serif';
    context.fillText(`${maxValue.toFixed(2)}°`, 6, padding.top + 4);
    context.fillText(`${minValue.toFixed(2)}°`, 6, padding.top + plotHeight);
    context.fillText(`${viewStart.toFixed(3)}초`, padding.left, height - 12);
    context.fillText(`${viewEnd.toFixed(3)}초`, width - padding.right - 66, height - 12);
    const colors = ['#1f6feb', '#d97706', '#16803c', '#a23ab7', '#d33b3b', '#0f8b8d'];
    const drawTracks = (tracks, dashed, alpha) => {
      ids.forEach((motionId, colorIndex) => {
        const points = tracks.get(motionId) || [];
        context.beginPath(); context.strokeStyle = colors[colorIndex % colors.length];
        context.globalAlpha = alpha; context.lineWidth = dashed ? 1.3 : 2.2;
        context.setLineDash(dashed ? [5, 4] : []);
        let previous = null;
        points.forEach((point) => {
          if (point.timeSec < viewStart - 1e-9 || point.timeSec > viewEnd + 1e-9) return;
          const x = xFor(point.timeSec); const y = yFor(point.value);
          if (!previous || point.timeSec - previous.timeSec > 0.031) context.moveTo(x, y);
          else context.lineTo(x, y);
          previous = point;
        });
        context.stroke();
      });
    };
    drawTracks(originalTracks, true, 0.4); drawTracks(workingTracks, false, 1);
    context.globalAlpha = 1; context.setLineDash([]);
    if (draftPreview.length) {
      const colorIndex = ids.indexOf(editor.pointDraft.motion_id);
      context.beginPath();
      context.strokeStyle = colors[(colorIndex < 0 ? 0 : colorIndex) % colors.length];
      context.lineWidth = 3;
      context.setLineDash([3, 2]);
      let started = false;
      draftPreview.forEach((point) => {
        if (point.timeSec < viewStart - 1e-9 || point.timeSec > viewEnd + 1e-9) return;
        const x = xFor(point.timeSec);
        const y = yFor(point.value);
        if (!started) {
          context.moveTo(x, y);
          started = true;
        } else {
          context.lineTo(x, y);
        }
      });
      if (started) context.stroke();
      context.setLineDash([]);
    }
    if (editor.operationReport?.operation === 'repair_spikes') {
      (editor.operationReport.changed || []).forEach((item) => {
        const timeSec = Number(item.time_sec); const value = Number(item.after_deg);
        if (timeSec < viewStart || timeSec > viewEnd) return;
        context.beginPath(); context.fillStyle = '#d33b3b'; context.strokeStyle = '#fff';
        context.lineWidth = 1.5; context.arc(xFor(timeSec), yFor(value), 5, 0, Math.PI * 2);
        context.fill(); context.stroke();
      });
      (editor.operationReport.excluded || []).forEach((item) => {
        const timeSec = Number(item.time_sec); const value = Number(item.before_deg);
        if (timeSec < viewStart || timeSec > viewEnd) return;
        const x = xFor(timeSec); const y = yFor(value);
        context.strokeStyle = '#d97706'; context.lineWidth = 2;
        context.beginPath(); context.moveTo(x - 4, y - 4); context.lineTo(x + 4, y + 4);
        context.moveTo(x + 4, y - 4); context.lineTo(x - 4, y + 4); context.stroke();
      });
    }
    let displayedCurves = editorPointCurves(displayedLayer).map((curve) => clone(curve));
    if (editor.pointDraft) {
      displayedCurves = displayedCurves.filter(
        (curve) => curve.curve_id !== editor.pointDraft.curve_id,
      );
      displayedCurves.push(editor.pointDraft);
    }
    editor.pointHitTargets = [];
    editor.handleHitTargets = [];
    displayedCurves.filter((curve) => selected.has(curve.motion_id)).forEach((curve) => {
      const colorIndex = ids.indexOf(curve.motion_id);
      const color = colors[(colorIndex < 0 ? 0 : colorIndex) % colors.length];
      (curve.points || []).forEach((point) => {
        const timeSec = Number(point.time_sec); const value = Number(point.value_deg);
        if (!Number.isFinite(timeSec) || !Number.isFinite(value)) return;
        if (timeSec < viewStart - 1e-9 || timeSec > viewEnd + 1e-9) return;
        const x = xFor(timeSec); const y = yFor(value);
        const isSelected = point.point_id === editor.selectedPointId
          && curve.curve_id === editor.pointDraft?.curve_id;
        context.beginPath(); context.fillStyle = '#fff'; context.strokeStyle = color;
        context.lineWidth = isSelected ? 3 : 2;
        context.arc(x, y, isSelected ? 6 : 4.5, 0, Math.PI * 2);
        context.fill(); context.stroke();
        editor.pointHitTargets.push({ x, y, curve, point });
        if (!isSelected) return;
        const handles = [
          ['in', point.in_handle], ['out', point.out_handle],
        ];
        handles.forEach(([side, handle]) => {
          const dt = Number(handle?.dt_sec); const dv = Number(handle?.dv_deg);
          if (!Number.isFinite(dt) || !Number.isFinite(dv) || Math.abs(dt) < 1e-9) return;
          const handleX = xFor(timeSec + dt); const handleY = yFor(value + dv);
          context.beginPath(); context.strokeStyle = '#65788a'; context.lineWidth = 1.4;
          context.moveTo(x, y); context.lineTo(handleX, handleY); context.stroke();
          context.beginPath(); context.fillStyle = '#1f6feb'; context.strokeStyle = '#fff';
          context.arc(handleX, handleY, 5, 0, Math.PI * 2); context.fill(); context.stroke();
          editor.handleHitTargets.push({ x: handleX, y: handleY, side, point });
        });
      });
    });
    const displayedValidation = editor.previewValidation || editor.validation;
    const issueTimes = [
      ...(displayedValidation?.conflicts || []).map((item) => Number(item.start_sec)),
      ...(displayedValidation?.transition_warnings || []).map((item) => Number(item.second_time_sec)),
    ].filter(Number.isFinite);
    context.strokeStyle = '#d33b3b'; context.setLineDash([4, 3]);
    issueTimes.forEach((timeSec) => {
      if (timeSec < viewStart || timeSec > viewEnd) return;
      const x = xFor(timeSec); context.beginPath();
      context.moveTo(x, padding.top); context.lineTo(x, padding.top + plotHeight); context.stroke();
    });
    context.setLineDash([]);
    if (editor.cursor) {
      const { x, y, timeSec, value } = editor.cursor;
      context.strokeStyle = '#596775'; context.lineWidth = 1; context.setLineDash([3, 3]);
      context.beginPath(); context.moveTo(x, padding.top); context.lineTo(x, padding.top + plotHeight);
      context.moveTo(padding.left, y); context.lineTo(padding.left + plotWidth, y); context.stroke();
      context.setLineDash([]); context.fillStyle = '#263442';
      context.fillText(`${timeSec.toFixed(3)}초`, Math.min(width - 90, x + 5), height - 24);
      context.fillText(`${value.toFixed(3)}°`, 5, Math.max(12, Math.min(height - 8, y)));
      if (editor.cursor.nearest) {
        const nearest = editor.cursor.nearest;
        context.beginPath(); context.fillStyle = '#d33b3b';
        context.arc(xFor(nearest.timeSec), yFor(nearest.value), 4, 0, Math.PI * 2);
        context.fill();
      }
    }
    if (el.studioEditorLegend) {
      el.studioEditorLegend.innerHTML = ids.map((motionId, index) => (
        `<span><i style="background:${colors[index % colors.length]}"></i>${escapeHtml(motionId)}</span>`
      )).join('') + (editor.preview
        ? '<span>점선: 저장 원본 · 실선: 결과 미리보기</span>'
        : '<span>점선: 저장 원본 · 실선: 현재 작업본</span>');
    }
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
    refreshEditorTimeline(editor.working, preview);
    refreshEditorAxisControls(null, editor.working);
    if (el.studioEditorSubtitle) {
      el.studioEditorSubtitle.textContent = `편집 반영 ${editor.undo.length}회 · 아직 저장되지 않음`;
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
    if (editor.operationReport?.operation === 'repair_spikes') {
      const conflicts = editor.previewValidation?.conflicts?.length || 0;
      const warnings = editor.previewValidation?.transition_warnings?.length || 0;
      if (conflicts || warnings) {
        setEditorMessage('보정 결과에 충돌 또는 20ms 급변이 남아 편집 반영을 차단했습니다.', true);
        return;
      }
      const count = Number(editor.operationReport.changed_count || 0);
      const maximum = Number(editor.operationReport.maximum_applied_correction_deg || 0);
      if (!window.confirm(
        `튀는 점 ${count}개를 보정합니다. 최대 변경량 ${maximum.toFixed(3)}°입니다. 편집에 반영할까요?`,
      )) return;
    }
    const previousIds = new Set(editorMotionIds(editor.working));
    const selectedIds = new Set(editorSelectedMotionIds());
    const previewIds = editorMotionIds(editor.preview);
    previewIds.forEach((motionId) => {
      if (!previousIds.has(motionId)) selectedIds.add(motionId);
    });
    editor.undo.push({ layer: clone(editor.working), validation: clone(editor.validation) });
    editor.redo = [];
    editor.working = clone(editor.preview);
    editor.validation = clone(
      editor.previewValidation || { conflicts: [], transition_warnings: [], playable: true },
    );
    editor.preview = null;
    editor.previewValidation = null;
    editor.operationReport = null;
    refreshEditorAxisControls(selectedIds, editor.working);
    if (el.studioEditorSubtitle) {
      el.studioEditorSubtitle.textContent = `편집 반영 ${editor.undo.length}회 · 아직 저장되지 않음`;
    }
    setEditorMessage(`편집 반영 ${editor.undo.length}회 완료 · 다음 편집을 계속할 수 있습니다.`);
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
      && !window.confirm('선택한 축의 선택 구간 데이터를 삭제할까요?')
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
      spike_detection_threshold_deg: Number(el.studioEditorSpikeThreshold?.value || 0.1),
      spike_maximum_correction_deg: Number(el.studioEditorSpikeMaximum?.value || 1.0),
      interpolation_order: operation === 'point_curve'
        ? Number(editor.pointDraft?.interpolation_order || el.studioEditorPointCurveOrder?.value || 3)
        : Number(el.studioEditorInterpolationChoices?.querySelector('input:checked')?.value || 1),
      curve_id: editor.pointDraft?.curve_id || '',
      points: operation === 'point_curve' ? clone(editor.pointDraft?.points || []) : [],
      mapping_rows: activeMapping()?.rows || [],
    };
    el.studioEditorApplyButton.disabled = true;
    if (el.studioEditorUpdateButton) el.studioEditorUpdateButton.disabled = true;
    if (el.studioEditorDeleteAxisDataButton) {
      el.studioEditorDeleteAxisDataButton.disabled = true;
    }
    if (el.studioEditorDeleteWholeAxisButton) {
      el.studioEditorDeleteWholeAxisButton.disabled = true;
    }
    try {
      const result = await editMotionStudioLayer(payload);
      if (result.success === false) throw new Error(result.message || '편집 실패');
      editor.operationReport = result.operation_report || null;
      if (operation === 'repair_spikes' && !editor.operationReport?.changed_count) {
        editor.preview = null;
        editor.previewValidation = result.validation
          || { conflicts: [], transition_warnings: [], playable: true };
        const excluded = Number(editor.operationReport?.excluded_count || 0);
        setEditorMessage(excluded
          ? `보정할 점은 없고 ${excluded}개는 연속 튀짐 또는 최대 보정량 초과로 제외했습니다.`
          : '선택 구간에서 검출 기준을 넘는 고립된 튀는 점을 찾지 못했습니다.');
        renderEditor();
        return;
      }
      editor.preview = clone(result.layer);
      refreshEditorTimeline(editor.preview, editor.working);
      if (operation === 'point_curve' && editor.pointDraft) {
        const calculated = editorPointCurves(editor.preview).find(
          (curve) => curve.curve_id === editor.pointDraft.curve_id,
        );
        if (calculated) loadPointDraft(calculated, editor.selectedPointId);
      }
      if (operation === 'delete_point_curve' || operation === 'detach_point_curve') {
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
      if (operation === 'repair_spikes') {
        const report = editor.operationReport;
        setEditorMessage(
          `튀는 점 ${report.changed_count}개 보정 미리보기 · `
          + `최대 ${Number(report.maximum_applied_correction_deg).toFixed(3)}° 변경 · `
          + `제외 ${report.excluded_count}개`,
          issueCount > 0,
        );
      } else {
        setEditorMessage(issueCount
          ? `결과 미리보기 · 충돌 또는 급변 ${issueCount}건 · 확인 후 값을 바꾸거나 편집 반영하세요`
          : '결과 미리보기 완료 · 결과가 맞으면 편집 반영을 누르세요.', issueCount > 0);
      }
      renderEditor();
    } catch (error) {
      setEditorMessage(error.message || String(error), true);
    } finally {
      el.studioEditorApplyButton.disabled = false;
      if (el.studioEditorDeleteAxisDataButton) {
        el.studioEditorDeleteAxisDataButton.disabled = false;
      }
      if (el.studioEditorDeleteWholeAxisButton) {
        el.studioEditorDeleteWholeAxisButton.disabled = false;
      }
      renderEditorControls();
    }
  }

  async function deleteSelectedWholeAxes() {
    const editor = state.editor;
    if (!editor) return;
    const selectedIds = editorSelectedMotionIds();
    if (!selectedIds.length) {
      setEditorMessage('전체 삭제할 Motion ID를 선택하세요', true);
      return;
    }
    const allIds = editorMotionIds(editor.working);
    if (selectedIds.length >= allIds.length) {
      setEditorMessage('모든 축을 없애려면 편집 창을 닫고 레이어 삭제를 사용하세요.', true);
      return;
    }
    if (!window.confirm(`선택한 축 ${selectedIds.join(', ')}의 전체 데이터를 삭제할까요?`)) return;
    const times = (editor.working.frames || [])
      .filter((frame) => selectedIds.some((motionId) => motionId in (frame.values || {})))
      .map((frame) => Number(frame.time_sec))
      .filter(Number.isFinite);
    if (!times.length) {
      setEditorMessage('선택한 축의 모션 데이터가 없습니다.', true);
      return;
    }
    if (el.studioEditorStart) el.studioEditorStart.value = Math.min(...times).toFixed(3);
    if (el.studioEditorEnd) el.studioEditorEnd.value = Math.max(...times).toFixed(3);
    await applyEditorOperation('delete_data', false);
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
    if (!window.confirm(question)) return null;
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

  async function run(action) {
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
      }
      render();
    } catch (error) {
      setMessage(error.message || String(error), true);
    }
  }

  function resetProjectState() {
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
    el.studioConflictInfo?.addEventListener('click', (event) => {
      const button = event.target.closest('[data-resolve-point-curve]');
      if (!button) return;
      resolvePointCurveMismatch(
        String(button.dataset.layerId || ''),
        String(button.dataset.resolvePointCurve || ''),
      );
    });
    el.studioTransitionSafetyLevel?.addEventListener('change', (event) => {
      // Capture the user's selection before run() marks the screen busy and
      // renderControls() redraws the select from the last saved project value.
      const transitionSafetyLevel = Number(event.currentTarget?.value || 4);
      run(() => saveMotionStudioProject({
        transition_safety_level: transitionSafetyLevel,
      }));
    });
    el.studioImportFileSelect?.addEventListener('change', renderControls);
    el.studioImportButton?.addEventListener('click', () => run(() => importMotionStudioFile({
      motion_file_id: el.studioImportFileSelect?.value,
    })));
    el.studioRecordButton?.addEventListener('click', () => {
      if (!requireMotorActionReady('녹화')) return;
      showLayerGraph({ composition: true });
      run(() => startMotionStudioRecord({
        mode: el.studioRecordMode?.value || 'record',
        initial_move_time_sec: Number(el.studioInitialMoveTime?.value || 5),
      }));
    });
    el.studioInitializeButton?.addEventListener('click', () => {
      if (!requireMotorActionReady('초기 위치 이동')) return;
      showLayerGraph({ composition: true });
      run(() => startMotionStudioInitialization({
        initial_move_time_sec: Number(el.studioInitialMoveTime?.value || 5),
      }));
    });
    el.studioPlayButton?.addEventListener('click', () => {
      if (!requireMotorActionReady('합성 미리보기 재생')) return;
      showLayerGraph({ composition: true });
      run(() => startMotionStudioPlayback({
        initial_move_time_sec: Number(el.studioInitialMoveTime?.value || 5),
      }));
    });
    el.studioStopButton?.addEventListener('click', () => {
      // 중복 정지 요청은 즉시 막되, 응답 실패 시 run()의 최종 렌더에서 다시 활성화한다.
      el.studioStopButton.disabled = true;
      run(stopMotionStudio);
    });
    el.studioCreateLayerButton?.addEventListener('click', async () => {
      const result = await run(() => createMotionStudioLayer());
      if (!result?.layer_id) return;
      state.selectedLayerId = result.layer_id;
      render();
    });
    el.studioExportButton?.addEventListener('click', () => run(() => exportMotionStudio(
      el.studioExportName?.value || state.project?.name || 'motion',
    )));
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
      if (!layer || !window.confirm(`선택한 '${layer.name}'을 복사할까요?\n복사본은 재생 미선택 상태로 생성됩니다.`)) return;
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
      if (!layer || !window.confirm(`레이어 '${layer.name}'을 삭제할까요?`)) return;
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
      if (!window.confirm(`선택한 ${layerIds.length}개 레이어를 '${name}'로 합칠까요?\n원본과 결과는 재생 선택 상태를 변경하지 않습니다.`)) return;
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
        editor.pointDraft = null;
        editor.selectedPointId = '';
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
    el.studioEditorCurveDetachButton?.addEventListener('click', () => {
      const editor = state.editor;
      const curve = storedCurveForDraft(editor);
      if (!curve) return;
      if (!window.confirm(
        '현재 그래프 데이터는 그대로 유지하고 포인트·탄젠트 편집 정보만 연결 해제할까요?',
      )) return;
      applyEditorOperation('detach_point_curve', false);
    });
    el.studioEditorCurveDeleteButton?.addEventListener('click', () => {
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
      if (!window.confirm(
        '선택한 포인트와 해당 곡선 구간의 그래프 데이터를 모두 삭제할까요?',
      )) return;
      applyEditorOperation('delete_point_curve', false);
    });
    [el.studioEditorOffset, el.studioEditorFactor, el.studioEditorDelta].forEach((input) => {
      input?.addEventListener('input', () => {
        discardEditorPreview('편집값이 바뀌어 결과 미리보기를 취소했습니다. 다시 계산하세요.');
      });
    });
    el.studioEditorInterpolationChoices?.addEventListener('change', () => {
      discardEditorPreview('보간 그래프가 바뀌어 결과 미리보기를 취소했습니다. 다시 계산하세요.');
    });
    const handleEditorRangeInput = () => {
      if (state.editor) {
        state.editor.selectionStage = 0;
        state.editor.selectionAnchor = null;
        state.editor.selectionKind = 'motion';
      }
      if (!discardEditorPreview('편집 구간이 바뀌어 결과 미리보기를 취소했습니다.')) drawEditorGraph();
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
      drawEditorGraph();
    });
    el.studioEditorApplyButton?.addEventListener('click', () => applyEditorOperation());
    el.studioEditorUpdateButton?.addEventListener('click', updateEditorWorkingCopy);
    el.studioEditorDeleteAxisDataButton?.addEventListener('click', () => (
      applyEditorOperation('delete_data')
    ));
    el.studioEditorDeleteWholeAxisButton?.addEventListener('click', deleteSelectedWholeAxes);
    const discardEditor = () => {
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
        && !window.confirm('저장하지 않은 편집 내용과 결과 미리보기를 버리고 닫을까요?')
      ) return;
      closeLayerEditor();
    };
    el.studioEditorCloseButton?.addEventListener('click', discardEditor);
    el.studioEditorDiscardButton?.addEventListener('click', discardEditor);
    el.studioEditorUndoButton?.addEventListener('click', () => {
      const editor = state.editor;
      if (!editor?.undo.length) return;
      discardEditorPreview();
      editor.redo.push({ layer: clone(editor.working), validation: clone(editor.validation) });
      const previous = editor.undo.pop();
      const replacedLayer = editor.working;
      editor.working = previous.layer;
      editor.validation = previous.validation;
      refreshEditorTimeline(editor.working, replacedLayer);
      refreshEditorAxisControls(null, editor.working);
      if (el.studioEditorSubtitle) {
        el.studioEditorSubtitle.textContent = `편집 반영 ${editor.undo.length}회 · 아직 저장되지 않음`;
      }
      setEditorMessage('직전 편집을 취소했습니다'); renderEditor();
    });
    el.studioEditorRedoButton?.addEventListener('click', () => {
      const editor = state.editor;
      if (!editor?.redo.length) return;
      discardEditorPreview();
      editor.undo.push({ layer: clone(editor.working), validation: clone(editor.validation) });
      const following = editor.redo.pop();
      const replacedLayer = editor.working;
      editor.working = following.layer;
      editor.validation = following.validation;
      refreshEditorTimeline(editor.working, replacedLayer);
      refreshEditorAxisControls(null, editor.working);
      if (el.studioEditorSubtitle) {
        el.studioEditorSubtitle.textContent = `편집 반영 ${editor.undo.length}회 · 아직 저장되지 않음`;
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
        el.studioEditorSubtitle.textContent = '저장 완료 · 계속 편집 가능';
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
      const result = await run(() => saveMotionStudioLayerData({
        layer_id: editor.layerId,
        original_revision: Number(editor.original.edit_revision || 0),
        layer: editor.working,
      }));
      if (result) {
        const savedLayer = state.project?.layers?.find(
          (layer) => layer.layer_id === editor.layerId,
        ) || editor.working;
        acceptSavedEditorLayer(
          editor,
          savedLayer,
          '저장 완료 · 창을 닫지 않고 편집을 계속할 수 있습니다.',
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
        setEditorMessage(
          '저장 중 원본 레이어가 변경되었습니다. 현재 작업은 저장되지 않았습니다. '
          + '편집 창을 닫고 최신 레이어를 다시 열어 작업하세요.',
          true,
        );
        return;
      }
      setEditorMessage('레이어 저장에 실패했습니다. 화면 상단 오류 내용을 확인하세요.', true);
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
    el.studioEditorZoomInButton?.addEventListener('click', () => {
      const editor = state.editor; if (!editor) return;
      const center = (editor.viewStart + editor.viewEnd) / 2;
      const span = (editor.viewEnd - editor.viewStart) * 0.6;
      scaleEditorValues(0.6);
      setEditorView(center - span / 2, center + span / 2);
    });
    el.studioEditorZoomOutButton?.addEventListener('click', () => {
      const editor = state.editor; if (!editor) return;
      const center = (editor.viewStart + editor.viewEnd) / 2;
      const span = (editor.viewEnd - editor.viewStart) * 1.7;
      scaleEditorValues(1.7);
      setEditorView(center - span / 2, center + span / 2);
    });
    el.studioEditorInterpolationButton?.addEventListener('click', () => {
      if (!el.studioEditorOperation) return;
      el.studioEditorOperation.value = 'interpolate';
      el.studioEditorOperation.dispatchEvent(new Event('change'));
      setEditorMessage('시작점과 끝점을 선택하고 1차·3차·5차 그래프를 고른 뒤 결과 미리보기를 누르세요.');
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
      const x = event.clientX - rect.left; const y = event.clientY - rect.top;
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
        const pixelDelta = event.clientX - editor.panningGraph.startClientX;
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
        x: event.clientX - rect.left,
        y: event.clientY - rect.top,
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
      if (motionStudioShouldEditPoint(el.studioEditorOperation?.value, pointTarget)) {
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
      drawEditorGraph();
    });
    el.studioEditorGraph?.addEventListener('mousedown', (event) => {
      const editor = state.editor;
      const metrics = editor?.graphMetrics;
      if (!editor || !metrics || event.button !== 0) return;
      const rect = el.studioEditorGraph.getBoundingClientRect();
      const x = event.clientX - rect.left; const y = event.clientY - rect.top;
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
          startClientX: event.clientX,
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
      scaleEditorValues(zoomFactor);
      setEditorView(center - (newSpan * ratio), center + (newSpan * (1 - ratio)));
    }, { passive: false });
  }

  async function addMotionFile(fileId) {
    const result = await run(() => importMotionStudioFile({ motion_file_id: fileId }));
    if (!result) throw new Error('모션 파일을 레이어로 추가하지 못했습니다');
    return result;
  }

  function renderSnapshot(studioStatus, midiStatus) {
    if (studioStatus && Object.keys(studioStatus).length) state.status = studioStatus;
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
