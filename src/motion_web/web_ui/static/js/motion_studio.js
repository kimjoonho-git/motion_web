import {
  exportMotionStudio,
  fetchMotionStudio,
  importMotionStudioFile,
  resetMotionStudioLayers,
  startMotionStudioPlayback,
  startMotionStudioRecord,
  stopMotionStudio,
  updateMotionStudioLayer,
} from './api.js?v=20260716-project-delete-copy-reset';

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

export function createMotionStudioController({ el }) {
  const state = {
    mappings: [], motionFiles: [], project: null, workspaceProject: null,
    status: {}, midi: {}, composition: { conflicts: [], conflict_free: true }, busy: false,
    axisRenderKey: '',
  };

  function setProject(project) {
    state.project = project || null;
  }

  function selectedMidi() {
    const result = new Map();
    for (const channel of state.midi?.channels || []) {
      if (channel?.control_enabled && channel.motion_id) result.set(String(channel.motion_id), channel);
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
        `<option value="${escapeHtml(item.file_id)}" ${item.valid ? '' : 'disabled'}>${escapeHtml(item.title || item.file_id)} · ${item.frame_count} frames${item.valid ? '' : ' · 오류'}</option>`
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
    el.studioMappingInfo.textContent = `읽기 전용 · ${mapping.motion_ids.length}개 Motion ID · SHA-256 ${checksum}…`;
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
          <td>${escapeHtml(row.motion_id)}</td><td>${escapeHtml(row.motor_ref || `Legacy Axis ${row.motor_axis ?? '-'}`)}</td>
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
        selectState.textContent = selectLocked ? '초기화 잠금' : channel ? 'SELECT' : '미선택';
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
      el.studioLayerRows.innerHTML = '<tr><td colspan="5" class="empty">녹화 레이어가 없습니다</td></tr>';
      return;
    }
    const conflictLayers = new Set((state.composition?.conflicts || []).flatMap((item) => (
      [item.first_layer_id, item.second_layer_id]
    )));
    el.studioLayerRows.innerHTML = layers.map((layer) => {
      const frames = layer.frames || [];
      const duration = frames.length ? Number(frames[frames.length - 1].time_sec || 0) : 0;
      return `<tr data-studio-layer-id="${escapeHtml(layer.layer_id)}">
        <td><input type="checkbox" data-layer-enabled ${layer.enabled !== false ? 'checked' : ''}></td>
        <td><input type="checkbox" data-layer-locked ${layer.locked ? 'checked' : ''}></td>
        <td>${escapeHtml(layer.name)}${conflictLayers.has(layer.layer_id) ? ' <span class="status-chip warn">충돌</span>' : ''}</td>
        <td>${frames.length}</td><td>${duration.toFixed(3)} s</td></tr>`;
    }).join('');
  }

  function renderConflicts() {
    if (!el.studioConflictInfo) return;
    const conflicts = state.composition?.conflicts || [];
    el.studioConflictInfo.classList.toggle('error-text', conflicts.length > 0);
    el.studioConflictInfo.textContent = conflicts.length
      ? conflicts.map((item) => (
        `${item.motion_id} · ${item.first_layer_name} / ${item.second_layer_name} · `
        + `${Number(item.start_sec).toFixed(3)}~${Number(item.end_sec).toFixed(3)}초`
      )).join(' | ')
      : '다중 레이어 축·시간 충돌 없음';
  }

  function renderControls() {
    const runtimeState = String(state.status?.state || 'idle');
    const running = ['initializing', 'recording', 'playing', 'stopping'].includes(runtimeState);
    const hasProject = Boolean(state.project);
    const hasMotionAxes = Boolean(activeMapping()?.rows?.length);
    const hasConflicts = Boolean(state.composition?.conflicts?.length);
    if (el.studioState) el.studioState.textContent = state.status?.message || '대기';
    if (el.studioElapsed) el.studioElapsed.textContent = timeText(state.status?.elapsed_sec);
    if (el.studioFrameCount) el.studioFrameCount.textContent = `${state.status?.recorded_frames || 0} frames · 20ms`;
    if (el.studioImportButton) {
      el.studioImportButton.disabled = (
        state.busy
        || running
        || !hasProject
        || !el.studioImportFileSelect?.value
      );
    }
    if (el.studioRecordButton) el.studioRecordButton.disabled = state.busy || running || !hasProject || !hasMotionAxes;
    if (el.studioPlayButton) {
      el.studioPlayButton.disabled = state.busy || running || !state.project?.layers?.length || hasConflicts;
      el.studioPlayButton.title = hasConflicts ? '충돌 레이어 중 하나의 사용을 해제하세요' : '';
    }
    if (el.studioStopButton) el.studioStopButton.disabled = state.busy || !running;
    if (el.studioExportButton) {
      el.studioExportButton.disabled = state.busy || running || !state.project?.layers?.length || hasConflicts;
      el.studioExportButton.title = hasConflicts ? '축·시간 충돌을 해결한 뒤 내보낼 수 있습니다' : '';
    }
    if (el.studioResetButton) el.studioResetButton.disabled = state.busy || running || !state.project?.layers?.length;
  }

  function render() {
    renderLists(); renderMapping(); renderAxes(); renderLayers(); renderConflicts(); renderControls();
  }

  async function run(action) {
    setBusy(true);
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
      state.composition = result.composition || { conflicts: [], conflict_free: true };
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
    state.composition = { conflicts: [], conflict_free: true };
    state.axisRenderKey = '';
    setMessage('현재 프로젝트 모션 스튜디오를 불러오세요');
    render();
  }

  function bindEvents() {
    el.studioImportFileSelect?.addEventListener('change', renderControls);
    el.studioImportButton?.addEventListener('click', () => run(() => importMotionStudioFile({
      motion_file_id: el.studioImportFileSelect?.value,
    })));
    el.studioRecordButton?.addEventListener('click', () => run(() => startMotionStudioRecord({
      mode: el.studioRecordMode?.value || 'record',
      initial_move_time_sec: Number(el.studioInitialMoveTime?.value || 5),
    })));
    el.studioPlayButton?.addEventListener('click', () => run(() => startMotionStudioPlayback({
      initial_move_time_sec: Number(el.studioInitialMoveTime?.value || 5),
    })));
    el.studioStopButton?.addEventListener('click', () => run(stopMotionStudio));
    el.studioResetButton?.addEventListener('click', () => {
      if (!window.confirm('현재 모션 스튜디오의 레이어를 모두 삭제하고 빈 작업공간으로 초기화할까요?')) return;
      run(resetMotionStudioLayers);
    });
    el.studioExportButton?.addEventListener('click', () => run(() => exportMotionStudio(
      el.studioExportName?.value || state.project?.name || 'motion',
    )));
    el.studioLayerRows?.addEventListener('change', (event) => {
      const row = event.target.closest('tr[data-studio-layer-id]');
      if (!row) return;
      run(() => updateMotionStudioLayer({
        layer_id: row.dataset.studioLayerId,
        enabled: row.querySelector('[data-layer-enabled]')?.checked,
        locked: row.querySelector('[data-layer-locked]')?.checked,
      }));
    });
  }

  async function addMotionFile(fileId) {
    const result = await run(() => importMotionStudioFile({ motion_file_id: fileId }));
    if (!result) throw new Error('모션 파일을 레이어로 추가하지 못했습니다');
    return result;
  }

  function renderSnapshot(studioStatus, midiStatus) {
    if (studioStatus && Object.keys(studioStatus).length) state.status = studioStatus;
    if (midiStatus) state.midi = midiStatus;
    renderAxes(); renderControls();
  }

  return { bindEvents, refresh, resetProjectState, renderSnapshot, addMotionFile };
}
