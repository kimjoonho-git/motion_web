import {
  fetchCoordinationStatus,
  sendCoordinationControl,
  saveCoordinationSettings,
} from './api.js?v=20260810-dds-release-4';
import { showAlert, showConfirm } from './ui_dialogs.js?v=20260727-popup-common-3';

function text(value) {
  return String(value ?? '').replace(/[&<>"']/g, (character) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[character]));
}

const stateLabels = {
  idle: '대기', preparing: '준비 확인', initializing: '초기위치 이동',
  armed: '시작 대기', start_scheduled: '예약됨', waiting: '예약 대기', running: '모션 실행 중',
  waiting_cycle_ready: '회차 준비 중', cycle_ready: '다음 시작 준비',
  stop_after_cycle: '현재 회차 후 정지 대기', releasing: '이전 그룹 실행 정리 확인 중',
  stopped: '정지', error: '오류',
  online: '정상', warning: '지연', offline: '통신 단절', ready: '정상',
  unavailable: '확인 불가', out_of_tolerance: '동기화 불량', unknown: '확인 불가',
  syncing: '측정 중', sync_waiting: '측정 대기', failed: '측정 실패',
};

function stateText(value) {
  const key = String(value || 'unknown');
  return stateLabels[key] || key;
}

function stateClass(value) {
  if (['online', 'ready', 'armed', 'cycle_ready'].includes(value)) return 'coordination-state-ok';
  if (['offline', 'error', 'unsynchronized', 'failed', 'out_of_tolerance'].includes(value)) return 'coordination-state-bad';
  return 'coordination-state-warn';
}

const activeStates = new Set([
  'preparing', 'initializing', 'armed', 'start_scheduled', 'waiting', 'running',
  'waiting_cycle_ready', 'cycle_ready', 'stop_after_cycle',
  'releasing',
]);

function peerCycleText(peer = {}) {
  return String(peer.motion_cycle_text || '-');
}

function peerMotionStep(peer = {}) {
  return String(peer.motion_step || '그룹 대기');
}

function peerProgressText(peer = {}) {
  return String(peer.motion_progress || '-');
}

function peerPhaseClass(peer = {}) {
  const step = String(peer.motion_step || '');
  if (step.includes('오류')) return 'coordination-state-bad';
  if (step.includes('정지') || step.includes('완료')) return 'coordination-state-warn';
  if (step.includes('실행') || step.includes('이동') || step.includes('예약')) {
    return 'coordination-state-warn';
  }
  return stateClass(peer.motion_state || 'ready');
}

export function createCoordinationController({ el }) {
  let snapshot = null;
  let loading = false;
  let timer = null;
  let formDirty = false;
  let shownCoordinationError = '';
  let pendingInitializationId = '';
  let initializationStarted = false;

  function renderSettings(config = {}) {
    if (formDirty) return;
    if (el.coordinationPcId) el.coordinationPcId.value = config.pc_id || '';
    if (el.coordinationDisplayName) el.coordinationDisplayName.value = config.display_name || '';
    if (el.coordinationGroupId) el.coordinationGroupId.value = config.group_id || '';
    if (el.coordinationDomainId) el.coordinationDomainId.value = Number(config.dds_domain_id ?? 21);
    if (el.coordinationEnabled) el.coordinationEnabled.value = config.enabled ? 'true' : 'false';
  }

  function peerRow(peer = {}, fixedParticipants = new Set()) {
    const alarm = peer.alarm || {};
    const alarmText = Number(peer.servo_alarm_grade || 0) > 0
      ? `${Number(peer.servo_alarm_grade)} · ${alarm.message || alarm.error_code || '확인 필요'}`
      : '0';
    return `<tr>
      <td><strong>${text(peer.display_name || peer.pc_id || '-')}</strong><small>${peer.display_name ? text(peer.pc_id || '') : ''}</small></td>
      <td class="${stateClass(peer.state)}">${text(stateText(peer.state))}</td>
      <td>${fixedParticipants.has(peer.pc_id) ? '고정 참가' : '대기'}</td>
      <td>${text(peerCycleText(peer))}</td>
      <td class="${peerPhaseClass(peer)}">${text(peerMotionStep(peer))}</td>
      <td>${text(peerProgressText(peer))}</td>
      <td class="${stateClass(peer.trigger_sync_state)}">${text(stateText(peer.trigger_sync_state))}</td>
      <td>${Number(peer.trigger_sync_uncertainty_ms || 0).toFixed(3)} ms</td>
      <td class="${Number(peer.servo_alarm_grade || 0) > 0 ? 'coordination-state-bad' : 'coordination-state-ok'}">${text(alarmText)}</td>
    </tr>`;
  }

  function render() {
    const config = snapshot?.config || {};
    const runtime = snapshot?.runtime || {};
    const runtimeConfig = runtime.config || config;
    const execution = runtime.execution || { state: 'idle', participants: [] };
    const peers = Array.isArray(runtime.peers) ? runtime.peers : [];
    const fixedParticipants = new Set(Array.isArray(execution.participants) ? execution.participants : []);
    const coordinationError = runtime.coordination_error || {};
    const failure = coordinationError.message || '';
    const alarms = new Map(
      (Array.isArray(runtime.alarms) ? runtime.alarms : [])
        .map((alarm) => [alarm.pc_id, alarm]),
    );
    peers.forEach((peer) => { peer.alarm = alarms.get(peer.pc_id) || null; });
    const joined = runtime.joined === true;
    const active = activeStates.has(execution.state);
    const configured = config.enabled === true && Boolean(config.group_id);
    renderSettings(config);

    if (el.coordinationNodeState) {
      el.coordinationNodeState.textContent = snapshot?.node_connected ? 'DDS 연동 노드 연결됨' : 'DDS 연동 노드 응답 없음';
      el.coordinationNodeState.className = snapshot?.node_connected ? 'coordination-state-ok' : 'coordination-state-warn';
    }
    if (el.coordinationConfigMessage) {
      el.coordinationConfigMessage.textContent = snapshot?.config_error
        || (
          'PC 전역 설정 · 같은 그룹 ID와 DDS Domain ID를 입력한 PC끼리 통신합니다. '
          + `트리거 동기화 허용값 ${Number(runtimeConfig.max_trigger_sync_uncertainty_ms ?? 20).toFixed(0)} ms`
        );
    }
    if (el.coordinationUpdatedAt) {
      el.coordinationUpdatedAt.textContent = snapshot?.status_age_sec == null
        ? '수신 없음' : `${Number(snapshot.status_age_sec).toFixed(1)}초 전`;
    }
    if (el.coordinationMachineId) el.coordinationMachineId.textContent = config.pc_id || '-';
    if (el.coordinationGroupDomain) {
      el.coordinationGroupDomain.textContent = `${config.group_id || '-'} · ${config.dds_domain_id ?? '-'}`;
    }
    if (el.coordinationJoinState) {
      el.coordinationJoinState.textContent = joined ? '참가 중' : '나감';
      el.coordinationJoinState.className = joined ? 'coordination-state-ok' : 'coordination-state-warn';
    }
    if (el.coordinationPeerCount) el.coordinationPeerCount.textContent = `${peers.length + (joined ? 1 : 0)}대`;
    if (el.coordinationExecutionState) {
      const coordinator = execution.coordinator_id ? ` · 진행 ${execution.coordinator_id}` : '';
      const cycle = Number(execution.cycle_number || 0) > 0 ? ` · ${execution.cycle_number}회차` : '';
      const spread = execution.start_spread_ms == null ? '' : ` · 시작 편차 ${Number(execution.start_spread_ms).toFixed(3)}ms`;
      el.coordinationExecutionState.textContent = `그룹 실행 · ${stateText(execution.state)}${coordinator}${cycle}${spread}`;
      el.coordinationExecutionState.className = execution.start_within_20ms === false
        ? 'coordination-state-bad' : stateClass(execution.state);
    }
    const nodeReady = snapshot?.node_connected === true && configured;
    const unhealthyPeer = peers.some((peer) => peer.state !== 'online' || Number(peer.servo_alarm_grade || 0) > 0);
    const groupErrorActive = coordinationError.active === true;
    if (pendingInitializationId) {
      if (execution.execution_id === pendingInitializationId) {
        initializationStarted = true;
      } else if (groupErrorActive) {
        pendingInitializationId = '';
        initializationStarted = false;
      } else if (initializationStarted && !execution.execution_id) {
        pendingInitializationId = '';
        initializationStarted = false;
        if (typeof document !== 'undefined') {
          queueMicrotask(() => showAlert(
            '참가한 모든 PC의 그룹 초기 위치 이동이 완료되었습니다.',
            { title: '그룹 초기 위치 이동 완료', confirmLabel: '확인', tone: 'info' },
          ));
        }
      }
    }
    if (el.coordinationJoinButton) el.coordinationJoinButton.disabled = loading || !nodeReady || joined || active;
    if (el.coordinationLeaveButton) el.coordinationLeaveButton.disabled = loading || !joined || active;
    if (el.coordinationTemporaryDisableButton) {
      el.coordinationTemporaryDisableButton.disabled = loading || !nodeReady || !joined;
      el.coordinationTemporaryDisableButton.title = active
        ? '이 PC와 다른 PC의 그룹 모션을 즉시 정지한 뒤 이 PC의 연동을 해제합니다'
        : '이 PC의 연동을 해제해 단독 모션·모션 스튜디오를 사용합니다';
    }
    const releasePending = Boolean(execution.execution_id);
    const startDisabled = loading || !joined || active || releasePending || peers.length < 1 || unhealthyPeer || groupErrorActive;
    if (el.coordinationInitializeButton) el.coordinationInitializeButton.disabled = startDisabled;
    if (el.coordinationStartButton) el.coordinationStartButton.disabled = startDisabled;
    if (el.coordinationContinuousStartButton) el.coordinationContinuousStartButton.disabled = startDisabled;
    if (el.coordinationRepeatMode) el.coordinationRepeatMode.disabled = loading || active;
    if (el.coordinationDwellSec) el.coordinationDwellSec.disabled = loading || active;
    if (el.coordinationStopAfterButton) el.coordinationStopAfterButton.disabled = loading || !active;
    if (el.coordinationStopNowButton) el.coordinationStopNowButton.disabled = loading || !active;
    if (el.coordinationAcknowledgeErrorButton) el.coordinationAcknowledgeErrorButton.disabled = loading || !groupErrorActive;
    if (el.coordinationErrorSummary) {
      const code = coordinationError.code || '';
      const failedPc = coordinationError.pc_id ? ` · PC ${coordinationError.pc_id}` : '';
      el.coordinationErrorSummary.textContent = failure ? `${code || 'GROUP_ERROR'}${failedPc} · ${failure}` : '';
      el.coordinationErrorSummary.classList.toggle('hidden', !failure);
      el.coordinationErrorSummary.classList.toggle('coordination-state-bad', Boolean(failure));
    }
    if (groupErrorActive && failure) {
      const errorKey = [
        coordinationError.execution_id || 'no-execution',
        coordinationError.code || 'GROUP_ERROR',
        failure,
      ].join('|');
      if (shownCoordinationError !== errorKey && typeof document !== 'undefined') {
        shownCoordinationError = errorKey;
        const pc = coordinationError.pc_id ? `\n발생 PC: ${coordinationError.pc_id}` : '';
        const cycle = Number(execution.cycle_number || 0);
        const cycleText = cycle > 0 ? `\n회차: ${cycle}회차` : '';
        queueMicrotask(() => showAlert(
          `${failure}${pc}${cycleText}\n\n그룹 오류를 확인하고 원인을 해소한 뒤 다시 실행하세요.`,
          {
            title: `${coordinationError.code || '그룹 모션'} 정지`,
            confirmLabel: '확인',
            tone: 'danger',
          },
        ));
      }
    }
    if (el.coordinationPeerRows) {
      const rows = [];
      if (joined) rows.push(peerRow({
        ...(runtime.local || {}),
        pc_id: runtime.local?.pc_id || config.pc_id,
        display_name: `${runtime.local?.display_name || runtimeConfig.display_name || config.pc_id} (이 PC)`,
        state: 'online',
      }, fixedParticipants));
      peers.forEach((peer) => rows.push(peerRow(peer, fixedParticipants)));
      el.coordinationPeerRows.innerHTML = rows.length
        ? rows.join('') : '<tr><td colspan="9" class="empty">그룹에 참가하면 PC 상태가 표시됩니다</td></tr>';
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
    try {
      const result = await saveCoordinationSettings({
        enabled: el.coordinationEnabled?.value === 'true',
        group_id: el.coordinationGroupId?.value?.trim() || '',
        dds_domain_id: Number(el.coordinationDomainId?.value ?? 21),
        display_name: el.coordinationDisplayName?.value?.trim() || '',
      });
      formDirty = false;
      if (el.coordinationConfigMessage) el.coordinationConfigMessage.textContent = result.message || '';
      if (!result.success) {
        await showAlert(result.message || 'DDS 그룹 설정 적용 실패', {
          title: '연동 재시작 실패',
          confirmLabel: '확인',
          tone: 'danger',
        });
        return;
      }
      if (el.coordinationConfigMessage) {
        el.coordinationConfigMessage.textContent = 'DDS 연동 서비스 재시작 확인 중';
      }
      await new Promise((resolve) => window.setTimeout(resolve, 1500));
      await refresh();
      const restarted = snapshot?.node_connected === true;
      await showAlert(
        restarted
          ? 'DDS 연동 설정 저장 및 재시작 완료'
          : '설정은 저장됐지만 DDS 연동 노드 재시작을 확인하지 못했습니다.',
        {
          title: restarted ? '연동 재시작 완료' : '연동 재시작 확인 실패',
          confirmLabel: '확인',
          tone: restarted ? 'info' : 'danger',
        },
      );
    } catch (error) {
      await showAlert(error?.message || String(error), {
        title: '연동 재시작 실패',
        confirmLabel: '확인',
        tone: 'danger',
      });
    } finally {
      loading = false;
      render();
    }
  }

  async function control(command, details = {}) {
    if (loading) return;
    loading = true;
    if (el.coordinationControlSummary) el.coordinationControlSummary.textContent = '명령 전달 중';
    render();
    try {
      const result = await sendCoordinationControl({ command, ...details });
      if (el.coordinationControlSummary) el.coordinationControlSummary.textContent = result.message || '명령 처리 완료';
      if (!result.success) window.alert(result.message || '그룹 명령 실패');
      await refresh();
      return result;
    } catch (error) {
      if (el.coordinationControlSummary) el.coordinationControlSummary.textContent = error?.message || '명령 전달 실패';
      return { success: false, message: error?.message || String(error) };
    } finally {
      loading = false;
      render();
    }
  }

  async function initializeGroup() {
    const confirmed = await showConfirm(
      '참가한 모든 PC를 각자의 모션 초기 위치로 동시에 이동합니다.\n모션 재생은 시작하지 않습니다.',
      {
        title: '그룹 초기 위치 이동',
        confirmLabel: '초기 위치 이동',
        tone: 'warning',
      },
    );
    if (!confirmed) return;
    const result = await control('initialize_group');
    if (result?.success) {
      pendingInitializationId = String(result.execution_id || '');
      initializationStarted = false;
      await showAlert(result.message || '그룹 초기 위치 이동 준비를 시작했습니다', {
        title: '그룹 초기 위치 이동 준비',
        confirmLabel: '확인',
        tone: 'info',
      });
    }
  }

  async function temporarilyDisable() {
    if (loading) return;
    const active = activeStates.has(snapshot?.runtime?.execution?.state);
    const confirmed = await showConfirm(
      active
        ? '이 PC의 DDS 연동을 일시 해제합니다.\n\n'
          + '진행 중이거나 준비 중인 그룹 모션은 두 PC 모두 즉시 정지됩니다. '
          + '다른 PC의 확인 없이 이 PC가 그룹에서 나갑니다.'
        : '이 PC의 DDS 연동을 일시 해제합니다.\n\n'
          + '다른 PC의 확인 없이 이 PC가 그룹에서 나갑니다. '
          + '단독 모션·모션 스튜디오를 사용할 수 있으며, 다시 연동하려면 「그룹 참가」를 누르세요.',
      {
        title: '연동 일시 해제',
        confirmLabel: '연동 해제',
        tone: 'warning',
      },
    );
    if (!confirmed) return;
    await control('temporarily_disable');
  }

  function groupRunOptions(runMode) {
    const repeatMode = String(el.coordinationRepeatMode?.value || 'direct');
    const dwellSec = Number(el.coordinationDwellSec?.value);
    return {
      run_mode: runMode,
      repeat_mode: repeatMode,
      dwell_sec: Number.isFinite(dwellSec) && dwellSec >= 0 ? dwellSec : 0,
    };
  }

  function bindEvents() {
    el.coordinationRefreshButton?.addEventListener('click', refresh);
    el.coordinationSaveButton?.addEventListener('click', save);
    el.coordinationJoinButton?.addEventListener('click', () => control('join'));
    el.coordinationLeaveButton?.addEventListener('click', () => control('leave'));
    el.coordinationTemporaryDisableButton?.addEventListener('click', temporarilyDisable);
    el.coordinationInitializeButton?.addEventListener('click', initializeGroup);
    el.coordinationStartButton?.addEventListener('click', () => control('start_group', groupRunOptions('once')));
    el.coordinationContinuousStartButton?.addEventListener('click', () => control('start_group', groupRunOptions('continuous')));
    el.coordinationStopAfterButton?.addEventListener('click', () => control('stop_after_cycle'));
    el.coordinationStopNowButton?.addEventListener('click', () => control('stop_now'));
    el.coordinationAcknowledgeErrorButton?.addEventListener('click', () => control('acknowledge_group_error'));
    [el.coordinationDisplayName, el.coordinationGroupId, el.coordinationDomainId, el.coordinationEnabled]
      .forEach((field) => field?.addEventListener('input', () => { formDirty = true; }));
  }

  function start() {
    bindEvents();
    refresh();
    if (!timer) timer = window.setInterval(refresh, 1000);
  }

  function renderSnapshot(value) {
    snapshot = value;
    render();
  }

  return { start, refresh, render, renderSnapshot };
}
