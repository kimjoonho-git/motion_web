import {
  checkCoordinationReadiness,
  fetchCoordinationStatus,
  sendCoordinationControl,
  saveCoordinationSettings,
} from './api.js?v=20260804-coordination-2';

function text(value) {
  return String(value ?? '').replace(/[&<>"']/g, (character) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[character]));
}

const modeLabels = { off: '연동 끔', status: '상태 공유', participant: '연동 참여' };
const roleLabels = { peer: '참여 PC', coordinator: '중앙 PC' };
const stateLabels = {
  ready: '준비 완료', rejected: '확인 필요', unavailable: '응답 없음',
  online: '정상', offline: '연결 끊김', error: '오류', unknown: '확인 불가',
  blocked: '차단', running: '실행 중', waiting: '대기', completed: '완료',
  active: '활성', waiting_start: '시작 대기', initializing: '초기화 중',
};

function stateText(value) {
  const key = String(value || 'unknown');
  return stateLabels[key] || key;
}

function stateClass(value) {
  if (['ready', 'online', 'active', 'completed'].includes(value)) return 'coordination-state-ok';
  if (['error', 'blocked', 'rejected', 'conflict'].includes(value)) return 'coordination-state-bad';
  return 'coordination-state-warn';
}

export function createCoordinationController({ el }) {
  let snapshot = null;
  let loading = false;
  let readiness = new Map();
  let timer = null;
  let formDirty = false;

  function renderSettings(config = {}, force = false) {
    if (formDirty && !force) return;
    if (el.coordinationModeSelect) el.coordinationModeSelect.value = config.mode || 'off';
    if (el.coordinationRoleSelect) el.coordinationRoleSelect.value = config.role || 'peer';
    if (el.coordinationCoordinatorInput) {
      el.coordinationCoordinatorInput.value = config.coordinator_machine_id || '';
      el.coordinationCoordinatorInput.disabled = config.role === 'coordinator' || config.mode === 'off';
    }
    if (el.coordinationRoleSelect) el.coordinationRoleSelect.disabled = config.mode === 'off';
  }

  function peerRow(machineId, payload = {}, local = false) {
    const coordination = payload.coordination || {};
    let ready = readiness.get(machineId);
    if (
      ready
      && payload.session?.readiness_session_id
      && ready.readiness_session_id !== payload.session.readiness_session_id
    ) {
      readiness.delete(machineId);
      ready = null;
    }
    const readinessCell = ready
      ? `<strong class="${stateClass(ready.state)}">${text(ready.message || stateText(ready.state))}</strong>`
      : '<span class="empty">미확인</span>';
    const label = payload.display_name || machineId;
    return `<tr>
      <td><strong>${text(label)}</strong><small>${local ? '이 PC' : text(machineId)}</small></td>
      <td>${text(modeLabels[coordination.mode] || coordination.mode || '-')} · ${text(roleLabels[coordination.role] || coordination.role || '-')}</td>
      <td class="${stateClass(payload.program?.state)}">${text(stateText(payload.program?.state))}</td>
      <td class="${stateClass(payload.motors?.state)}">${text(stateText(payload.motors?.state))} ${Number(payload.motors?.online_count || 0)}/${Number(payload.motors?.total_count || 0)}축</td>
      <td class="${stateClass(payload.safety?.state)}">${text(stateText(payload.safety?.state))}</td>
      <td class="${stateClass(payload.motion?.state)}">${text(stateText(payload.motion?.state))}</td>
      <td>${readinessCell}</td>
    </tr>`;
  }

  function render() {
    const config = snapshot?.config || {};
    const runtime = snapshot?.runtime || {};
    const peers = Array.isArray(runtime.peers) ? runtime.peers : [];
    const coordinator = runtime.coordinator || {};
    const executionControl = runtime.execution_control || { state: 'local' };
    renderSettings(config);
    if (el.coordinationNodeState) {
      el.coordinationNodeState.textContent = snapshot?.node_connected ? '연동 노드 연결됨' : '연동 노드 응답 없음';
      el.coordinationNodeState.className = snapshot?.node_connected ? 'coordination-state-ok' : 'coordination-state-warn';
    }
    if (el.coordinationConfigMessage) {
      el.coordinationConfigMessage.textContent = snapshot?.config_error
        || (config.access_enabled || config.mode === 'off'
          ? '전역 설정과 등록 peer를 기준으로 동작합니다.'
          : '활성 모드 사용 전 내부망 IP·허용 네트워크·peer·HMAC 키 설정이 필요합니다.');
    }
    if (el.coordinationUpdatedAt) {
      el.coordinationUpdatedAt.textContent = snapshot?.status_age_sec === null
        ? '수신 없음' : `${Number(snapshot?.status_age_sec || 0).toFixed(1)}초 전`;
    }
    if (el.coordinationMachineId) el.coordinationMachineId.textContent = config.machine_id || '-';
    if (el.coordinationModeRole) {
      el.coordinationModeRole.textContent = `${modeLabels[config.mode] || '-'} · ${roleLabels[config.role] || '-'}`;
    }
    if (el.coordinationCoordinatorState) {
      el.coordinationCoordinatorState.textContent = coordinator.state === 'conflict'
        ? `중복 중앙: ${(coordinator.claims || []).join(', ')}`
        : `${stateText(coordinator.state)}${coordinator.machine_id ? ` · ${coordinator.machine_id}` : ''}`;
      el.coordinationCoordinatorState.className = stateClass(coordinator.state);
    }
    if (el.coordinationPeerCount) el.coordinationPeerCount.textContent = `${peers.length}대`;
    const canCheck = snapshot?.node_connected
      && runtime.mode === 'participant'
      && runtime.role === 'coordinator'
      && coordinator.authority_allowed === true;
    const networkOwned = executionControl.state === 'network';
    if (el.coordinationExecutionOwner) {
      el.coordinationExecutionOwner.textContent = networkOwned
        ? `모션 실행 제어권 · 네트워크 (${executionControl.owner || '-'})`
        : '모션 실행 제어권 · 로컬';
    }
    if (el.coordinationAcquireButton) el.coordinationAcquireButton.disabled = loading || !canCheck || networkOwned;
    if (el.coordinationReleaseButton) el.coordinationReleaseButton.disabled = loading || !canCheck || !networkOwned;
    if (el.coordinationReadinessButton) {
      el.coordinationReadinessButton.disabled = loading || !canCheck;
      el.coordinationReadinessButton.title = canCheck ? '' : '연동 참여 중앙 PC가 활성 상태여야 합니다';
    }
    [
      el.coordinationRunOnceButton, el.coordinationMotionStopButton,
      el.coordinationInitializeButton, el.coordinationInitializeStopButton,
      el.coordinationSynchronizedRunButton,
    ].forEach((button) => { if (button) button.disabled = loading || !canCheck; });
    if (el.coordinationPeerRows) {
      const rows = [];
      if (runtime.local && runtime.machine_id) rows.push(peerRow(runtime.machine_id, runtime.local, true));
      peers.forEach((record) => rows.push(peerRow(record.machine_id, record.payload || {}, false)));
      el.coordinationPeerRows.innerHTML = rows.length
        ? rows.join('')
        : '<tr><td colspan="7" class="empty">연결된 PC가 없습니다</td></tr>';
    }
  }

  async function refresh() {
    try {
      snapshot = await fetchCoordinationStatus();
      render();
    } catch (error) {
      if (el.coordinationNodeState) el.coordinationNodeState.textContent = error?.message || '상태 확인 실패';
    }
  }

  async function save() {
    if (loading) return;
    loading = true;
    render();
    const button = el.coordinationSaveButton;
    const original = button?.textContent || '';
    if (button) {
      button.disabled = true;
      button.textContent = '저장 중';
    }
    try {
      const result = await saveCoordinationSettings({
        mode: el.coordinationModeSelect?.value || 'off',
        role: el.coordinationRoleSelect?.value || 'peer',
        coordinator_machine_id: el.coordinationCoordinatorInput?.value || '',
      });
      if (result.saved) formDirty = false;
      if (el.coordinationConfigMessage) el.coordinationConfigMessage.textContent = result.message || '';
      if (result.success === false) window.alert(result.message || '연동 설정 적용 실패');
      window.setTimeout(refresh, 1200);
    } catch (error) {
      window.alert(error?.message || String(error));
    } finally {
      loading = false;
      if (button) {
        button.disabled = false;
        button.textContent = original;
      }
      render();
    }
  }

  async function checkReadiness() {
    if (loading) return;
    loading = true;
    readiness = new Map();
    if (el.coordinationReadinessSummary) el.coordinationReadinessSummary.textContent = '각 PC의 로컬 실행 준비 확인 중';
    render();
    try {
      const result = await checkCoordinationReadiness();
      (result.results || []).forEach((item) => readiness.set(item.machine_id, item));
      if (el.coordinationReadinessSummary) el.coordinationReadinessSummary.textContent = result.message || '준비 확인 완료';
      if (result.stale_project_generation) window.alert(result.message);
    } catch (error) {
      if (el.coordinationReadinessSummary) el.coordinationReadinessSummary.textContent = error?.message || '준비 확인 실패';
    } finally {
      loading = false;
      render();
    }
  }

  async function control(command) {
    if (loading) return;
    loading = true;
    if (el.coordinationControlSummary) el.coordinationControlSummary.textContent = '명령 전달 중';
    render();
    try {
      const payload = { command };
      if (command === 'synchronized_run') {
        payload.repeat_count = Number(el.coordinationRepeatCountInput?.value || 1);
        payload.dwell_sec = Number(el.coordinationDwellInput?.value || 0);
        payload.lead_sec = Number(el.coordinationLeadInput?.value || 15);
      }
      const result = await sendCoordinationControl(payload);
      if (el.coordinationControlSummary) {
        const schedule = result.schedule;
        el.coordinationControlSummary.textContent = schedule
          ? `${result.message} · 최장 ${Number(schedule.longest_motion_sec).toFixed(2)}초 · 공통 주기 ${Number(schedule.cycle_sec).toFixed(2)}초`
          : (result.message || '명령 처리 완료');
      }
      if (!result.success) window.alert(result.message || '연동 실행 명령 실패');
    } catch (error) {
      if (el.coordinationControlSummary) el.coordinationControlSummary.textContent = error?.message || '명령 전달 실패';
    } finally {
      loading = false;
      render();
    }
  }

  function bindEvents() {
    el.coordinationRefreshButton?.addEventListener('click', refresh);
    el.coordinationSaveButton?.addEventListener('click', save);
    el.coordinationReadinessButton?.addEventListener('click', checkReadiness);
    el.coordinationAcquireButton?.addEventListener('click', () => control('acquire_control'));
    el.coordinationReleaseButton?.addEventListener('click', () => control('release_control'));
    el.coordinationRunOnceButton?.addEventListener('click', () => control('run_once'));
    el.coordinationMotionStopButton?.addEventListener('click', () => control('stop_motion'));
    el.coordinationInitializeButton?.addEventListener('click', () => control('initialize'));
    el.coordinationInitializeStopButton?.addEventListener('click', () => control('stop_initialize'));
    el.coordinationSynchronizedRunButton?.addEventListener('click', () => control('synchronized_run'));
    el.coordinationModeSelect?.addEventListener('change', () => {
      formDirty = true;
      const off = el.coordinationModeSelect.value === 'off';
      if (off && el.coordinationRoleSelect) el.coordinationRoleSelect.value = 'peer';
      renderSettings({
        ...(snapshot?.config || {}),
        mode: el.coordinationModeSelect.value,
        role: el.coordinationRoleSelect?.value || 'peer',
        coordinator_machine_id: el.coordinationCoordinatorInput?.value || '',
      }, true);
    });
    el.coordinationRoleSelect?.addEventListener('change', () => {
      formDirty = true;
      if (el.coordinationRoleSelect.value === 'coordinator' && el.coordinationCoordinatorInput) {
        el.coordinationCoordinatorInput.value = snapshot?.config?.machine_id || '';
      }
      renderSettings({
        ...(snapshot?.config || {}),
        mode: el.coordinationModeSelect?.value || 'off',
        role: el.coordinationRoleSelect?.value || 'peer',
        coordinator_machine_id: el.coordinationCoordinatorInput?.value || '',
      }, true);
    });
    el.coordinationCoordinatorInput?.addEventListener('input', () => { formDirty = true; });
  }

  function start() {
    bindEvents();
    refresh();
    if (!timer) timer = window.setInterval(refresh, 1000);
  }

  return { start, refresh, render };
}
