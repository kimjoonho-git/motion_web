import {
  fetchCoordinationStatus,
  sendCoordinationControl,
  saveCoordinationSettings,
} from './api.js?v=20260821-api-single-1';
import { showAlert, showConfirm, dismissAllDialogs } from './ui_dialogs.js?v=20260727-popup-common-3';

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
    if (el.coordinationEnabled && el.coordinationEnabled.value !== String(config.enabled === true)) {
      el.coordinationEnabled.value = String(config.enabled === true);
    }
    if (el.coordinationIsMaster && el.coordinationIsMaster.value !== String(config.is_master === true)) {
      el.coordinationIsMaster.value = String(config.is_master === true);
    }
    if (el.coordinationAutoPlayToggle && el.coordinationAutoPlayToggle.checked !== (config.auto_play === true)) {
      el.coordinationAutoPlayToggle.checked = config.auto_play === true;
    }
    if (el.coordinationRequiredPeers) el.coordinationRequiredPeers.value = Array.isArray(config.required_peers) ? config.required_peers.join(', ') : '';
  }

  function peerRow(peer = {}, requiredPeers = new Set(), fixedParticipants = new Set()) {
    const alarm = peer.alarm || {};
    const alarmText = Number(peer.servo_alarm_grade || 0) > 0
      ? `${Number(peer.servo_alarm_grade)} · ${alarm.message || alarm.error_code || '확인 필요'}`
      : '0';
    
    const isRequired = requiredPeers.has(peer.pc_id);
    const pcNameHtml = `<strong>${text(peer.display_name || peer.pc_id || '-')}</strong><small>${peer.display_name ? text(peer.pc_id || '') : ''}</small>`;
    const badgeHtml = isRequired ? `<span style="display: inline-block; margin-left: 6px; padding: 2px 6px; background-color: var(--color-primary); color: white; border-radius: 4px; font-size: 10px; font-weight: bold;">⭐ 필수</span>` : '';
    
    const executionStateText = fixedParticipants.has(peer.pc_id) ? '고정 참가' : (isRequired ? '명단 포함' : '대기');
    const executionStateClass = fixedParticipants.has(peer.pc_id) ? 'coordination-state-ok' : (isRequired ? 'coordination-state-ok' : 'coordination-state-warn');
    
    const actionButton = isRequired
      ? `<button type="button" class="danger remove-peer-btn" data-pc-id="${text(peer.pc_id)}" style="padding: 2px 8px; font-size: 11px; cursor: pointer;">명단 제외</button>`
      : `<button type="button" class="primary add-peer-btn" data-pc-id="${text(peer.pc_id)}" style="padding: 2px 8px; font-size: 11px; cursor: pointer;">명단 추가</button>`;

    return `<tr>
      <td>${pcNameHtml}${badgeHtml}</td>
      <td class="${stateClass(peer.state)}"><span class="peer-status-dot ${peer.state || 'offline'}"></span>${text(stateText(peer.state))}</td>
      <td class="${executionStateClass}"><strong>${executionStateText}</strong></td>
      <td>${text(peerCycleText(peer))}</td>
      <td class="${peerPhaseClass(peer)}">${text(peerMotionStep(peer))}</td>
      <td>${text(peerProgressText(peer))}</td>
      <td class="${stateClass(peer.trigger_sync_state)}">${text(stateText(peer.trigger_sync_state))}</td>
      <td>${Number(peer.trigger_sync_uncertainty_ms || 0).toFixed(3)} ms</td>
      <td class="${Number(peer.servo_alarm_grade || 0) > 0 ? 'coordination-state-bad' : 'coordination-state-ok'}">${text(alarmText)}</td>
      <td title="${text(peer.git_message || '')}">[${text(peer.git_branch || '?')}] ${text(peer.git_hash || '-')}</td>
      <td style="text-align: center;">${actionButton}</td>
    </tr>`;
  }

  function render() {
    const config = snapshot?.config || {};
    const runtime = snapshot?.runtime || {};
    const runtimeConfig = runtime.config || config;
    const execution = runtime.execution || { state: 'idle', participants: [] };
    const peers = Array.isArray(runtime.peers) ? runtime.peers : [];
    const fixedParticipants = new Set(Array.isArray(execution.participants) ? execution.participants : []);
    const requiredPeersList = Array.isArray(config.required_peers) ? config.required_peers : [];
    const requiredPeers = new Set(requiredPeersList);
    const coordinationError = runtime.coordination_error || {};
    const failure = coordinationError.message || '';
    const alarms = new Map(
      (Array.isArray(runtime.alarms) ? runtime.alarms : [])
        .map((alarm) => [alarm.pc_id, alarm]),
    );
    peers.forEach((peer) => { peer.alarm = alarms.get(peer.pc_id) || null; });
    const joined = runtime.joined === true;
    const active = Boolean(execution.execution_id);
    const configured = config.enabled === true && Boolean(config.group_id);
    renderSettings(config);
    
    const rosterBanner = document.getElementById('coordinationConfirmedRosterBanner');
    if (rosterBanner) {
      if (requiredPeersList.length > 0) {
        rosterBanner.innerHTML = `<span style="color: var(--color-primary);">✅ 현재 그룹 필수 참가 명단:</span> ${requiredPeersList.join(', ')}`;
        rosterBanner.style.backgroundColor = 'rgba(16, 185, 129, 0.1)';
        rosterBanner.style.border = '1px solid var(--color-primary)';
      } else {
        rosterBanner.innerHTML = `⚠️ 시스템을 시작하려면 아래 표에서 명단을 확정하세요 (명단 미확정)`;
        rosterBanner.style.backgroundColor = 'rgba(239, 68, 68, 0.1)';
        rosterBanner.style.border = '1px solid var(--color-danger)';
        rosterBanner.style.color = 'var(--color-danger)';
      }
    }

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
      if (el.coordJoinBadge) {
        el.coordJoinBadge.classList.toggle('active', joined);
      }
    }
    
    let currentMaster = '';
    if (config.is_master) currentMaster = config.display_name || config.pc_id;
    peers.forEach((p) => { if (p.is_master) currentMaster = p.display_name || p.pc_id; });
    
    if (el.coordMasterBadge) {
      el.coordMasterBadge.style.display = currentMaster ? 'flex' : 'none';
      if (el.coordinationMasterName) el.coordinationMasterName.textContent = currentMaster || '-';
    }

    if (el.coordinationAutoPlayGroup) {
      el.coordinationAutoPlayGroup.style.display = config.is_master ? 'flex' : 'none';
    }

    if (el.coordinationPeerCount) el.coordinationPeerCount.textContent = `${peers.length + (joined ? 1 : 0)}대`;
    if (el.coordinationExecutionState) {
      const coordinator = execution.coordinator_id ? ` · 진행 ${execution.coordinator_id}` : '';
      const tc = Number(execution.target_cycle_count || 0);
      const stopAfter = execution.stop_after_cycle === true;
      let cycle = '';
      if (tc > 0) {
        cycle = ` · [${Number(execution.cycle_number || 0)} / ${tc}회차]${stopAfter ? '(정지 중)' : ''}`;
      } else if (Number(execution.cycle_number || 0) > 0) {
        cycle = ` · ${execution.cycle_number}회차${stopAfter ? '(정지 중)' : ''}`;
      }
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

    let continuousUnavailable = false;
    let continuousReason = '';
    const globalState = typeof getLatestState === 'function' ? getLatestState() : null;
    const continuousCapability = globalState?.motion_run_status?.capabilities?.continuous_run;
    if (continuousCapability && continuousCapability.available === false) {
      const repeatMode = String(el.coordinationRepeatMode?.value || 'reinitialize');
      if (repeatMode !== 'reinitialize' && repeatMode !== 'dwell_reinitialize') {
        continuousUnavailable = true;
        continuousReason = continuousCapability.reason || '단차 5도 초과';
      }
    }

    if (el.coordinationInitializeButton) el.coordinationInitializeButton.disabled = startDisabled;
    if (el.coordinationStartButton) el.coordinationStartButton.disabled = startDisabled;
    if (el.coordinationContinuousStartButton) {
      el.coordinationContinuousStartButton.disabled = startDisabled || continuousUnavailable;
      el.coordinationContinuousStartButton.title = continuousUnavailable 
        ? `연속 모션 불가: ${continuousReason}` 
        : '';
    }
    if (el.coordinationRepeatMode) el.coordinationRepeatMode.disabled = loading || active;
    if (el.coordinationDwellSec) el.coordinationDwellSec.disabled = loading || active;
    if (el.coordinationStopAfterButton) el.coordinationStopAfterButton.disabled = loading || !active;
    if (el.coordinationStopNowButton) el.coordinationStopNowButton.disabled = loading || !active;
    
    if (el.coordinationTargetStopCycle) {
      el.coordinationTargetStopCycle.disabled = loading || active;
    }

    if (el.coordinationAcknowledgeErrorButton) el.coordinationAcknowledgeErrorButton.disabled = loading || !groupErrorActive;
    if (el.coordinationErrorSummary) {
      const code = coordinationError.code || '';
      const failedPc = coordinationError.pc_id ? ` · PC ${coordinationError.pc_id}` : '';
      el.coordinationErrorSummary.textContent = failure ? `${code || 'GROUP_ERROR'}${failedPc} · ${failure}` : '';
      el.coordinationErrorSummary.classList.toggle('hidden', !failure);
      el.coordinationErrorSummary.classList.toggle('coordination-state-bad', Boolean(failure));
    }
    if (groupErrorActive && failure) {
      const code = coordinationError.code || '';
      const isTransientRecoveryError = [
        'GROUP_PARTICIPANT_DISCONNECTED',
        'GROUP_SCHEDULE_ACK_TIMEOUT',
        'GROUP_MOTION_START_REPORT_TIMEOUT',
      ].includes(code) || config.auto_play;

      if (!isTransientRecoveryError) {
        const errorKey = [
          coordinationError.execution_id || 'no-execution',
          code || 'GROUP_ERROR',
          failure,
        ].join('|');
        if (shownCoordinationError !== errorKey && typeof document !== 'undefined') {
          shownCoordinationError = errorKey;
          const pc = coordinationError.pc_id ? `\n발생 PC: ${coordinationError.pc_id}` : '';
          const cycle = Number(execution.cycle_number || 0);
          const cycleText = cycle > 0 ? `\n회차: ${cycle}회차` : '';
          const messageBody = `${failure}${pc}${cycleText}\n\n그룹 오류를 확인하고 원인을 해소한 뒤 다시 실행하세요.`;
            
          queueMicrotask(() => showAlert(
            messageBody,
            {
              title: `${coordinationError.code || '그룹 모션'} 정지`,
              confirmLabel: '확인',
              tone: 'danger',
            },
          ));
        }
      }
    } else if (shownCoordinationError) {
      shownCoordinationError = '';
      dismissAllDialogs();
    }
    if (el.coordinationPeerRows) {
      const rows = [];
      const seenPcs = new Set();
      
      const localId = runtime.local?.pc_id || config.pc_id;
      if (joined) {
        rows.push(peerRow({
          ...(runtime.local || {}),
          pc_id: localId,
          display_name: `${runtime.local?.display_name || runtimeConfig.display_name || config.pc_id} (이 PC)`,
          state: 'online',
        }, requiredPeers, fixedParticipants));
        if (localId) seenPcs.add(localId);
      }
      
      peers.forEach((peer) => {
        rows.push(peerRow(peer, requiredPeers, fixedParticipants));
        if (peer.pc_id) seenPcs.add(peer.pc_id);
      });
      
      requiredPeers.forEach((requiredId) => {
        if (!seenPcs.has(requiredId)) {
          rows.push(peerRow({
            pc_id: requiredId,
            display_name: '(통신 단절 / 재부팅 대기)',
            state: 'offline',
          }, requiredPeers, fixedParticipants));
        }
      });
      
      el.coordinationPeerRows.innerHTML = rows.length
        ? rows.join('') : '<tr><td colspan="11" class="empty">그룹에 참가하면 PC 상태가 표시됩니다</td></tr>';
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

  async function save(customSuccessMessage = null, customSuccessTitle = null) {
    if (loading) return;
    loading = true;
    render();
    try {
      const result = await saveCoordinationSettings({
        enabled: el.coordinationEnabled?.value === 'true',
        is_master: el.coordinationIsMaster?.value === 'true',
        auto_play: el.coordinationAutoPlayToggle?.checked === true,
        required_peers: el.coordinationRequiredPeers?.value?.split(',').map(s => s.trim()).filter(Boolean) || [],
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
      await new Promise((resolve) => window.setTimeout(resolve, 2000));
      await refresh();
      const restarted = snapshot?.node_connected === true;
      await showAlert(
        restarted
          ? (customSuccessMessage || 'DDS 연동 설정 저장 및 재시작 완료')
          : '설정은 저장됐지만 DDS 연동 노드 재시작을 확인하지 못했습니다.',
        {
          title: restarted ? (customSuccessTitle || '연동 재시작 완료') : '연동 재시작 확인 실패',
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
    const active = Boolean(snapshot?.runtime?.execution?.execution_id);
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
    const targetCycleCount = Math.max(0, parseInt(el.coordinationTargetStopCycle?.value || '0', 10));
    return {
      run_mode: runMode,
      repeat_mode: repeatMode,
      dwell_sec: Number.isFinite(dwellSec) && dwellSec >= 0 ? dwellSec : 0,
      target_cycle_count: Number.isFinite(targetCycleCount) ? targetCycleCount : 0,
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
    el.coordinationStopAfterButton?.addEventListener('click', () => {
      const state = snapshot?.execution?.state;
      if (['preparing', 'initializing', 'armed', 'start_scheduled'].includes(state)) {
        control('stop_now');
      } else {
        control('stop_after_cycle');
      }
    });
    el.coordinationStopNowButton?.addEventListener('click', () => control('stop_now'));

    el.coordinationAcknowledgeErrorButton?.addEventListener('click', () => control('acknowledge_group_error'));
    [el.coordinationDisplayName, el.coordinationGroupId, el.coordinationDomainId, el.coordinationEnabled, el.coordinationIsMaster, el.coordinationRequiredPeers]
      .forEach((field) => field?.addEventListener('input', () => { formDirty = true; }));
      
    el.coordinationAutoPlayToggle?.addEventListener('change', () => {
      formDirty = true;
      save();
    });
    
    el.coordinationConfirmRosterButton?.addEventListener('click', async () => {
      const peers = Array.isArray(snapshot?.runtime?.peers) ? snapshot.runtime.peers : [];
      const ids = new Set(peers.map(p => p.pc_id));
      const localId = el.coordinationPcId?.value || snapshot?.config?.pc_id;
      if (localId) ids.add(localId);
      
      const rosterList = Array.from(ids);
      if (rosterList.length > 0 && el.coordinationRequiredPeers) {
        const confirmed = await showConfirm(
          `현재 접속된 아래 PC 인원으로 필수 참가 명단을 확정하고 시스템에 저장하시겠습니까?\n\n`
          + `[ 확정 명단 (${rosterList.length}대) ]\n`
          + `${rosterList.join(', ')}\n\n`
          + `(부팅 자동 재생 시 위 PC들이 모두 켜진 후 모션이 시작됩니다)`,
          {
            title: 'DDS 그룹 필수 참가 명단 확정',
            confirmLabel: '명단 확정 및 저장',
            tone: 'info',
          },
        );
        if (!confirmed) return;
        el.coordinationRequiredPeers.value = rosterList.join(', ');
        formDirty = true;
        await save(
          `[ 확정 명단: ${rosterList.join(', ')} ]\n\n`
          + `필수 참가 명단 확정이 완료되었습니다.\n`
          + `PC 재부팅 시 해당 명단의 모든 PC가 준비되면 모션이 자동 시작됩니다.`,
          '명단 확정 저장 완료',
        );
      } else {
        await showAlert('현재 방에 접속한 참가 PC가 없거나 네트워크 통신 연결을 확인해야 합니다.', { title: '명단 확정 불가', tone: 'danger' });
      }
    });

    el.coordinationPeerRows?.addEventListener('click', async (event) => {
      const removeBtn = event.target.closest('.remove-peer-btn');
      const addBtn = event.target.closest('.add-peer-btn');
      const btn = removeBtn || addBtn;
      if (!btn) return;
      
      const targetPcId = btn.dataset.pcId;
      const isRemoving = Boolean(removeBtn);
      if (!targetPcId) return;

      const confirmed = await showConfirm(
        isRemoving
          ? `PC [ ${targetPcId} ]를 그룹 필수 참가 명단에서 제외하시겠습니까?\n\n제외 후 저장하면 부팅 자동 재생 시 해당 PC를 기다리지 않고 모션이 시작될 수 있습니다.`
          : `PC [ ${targetPcId} ]를 그룹 필수 참가 명단에 추가하시겠습니까?\n\n추가 후 저장하면 부팅 자동 재생 시 해당 PC가 켜질 때까지 대기하게 됩니다.`,
        {
          title: isRemoving ? '참가 PC 명단 제외 확인' : '참가 PC 명단 추가 확인',
          confirmLabel: isRemoving ? '명단에서 제외' : '명단에 추가',
          tone: isRemoving ? 'danger' : 'info',
        },
      );
      if (!confirmed) return;

      const currentRequired = (el.coordinationRequiredPeers?.value || '')
        .split(',')
        .map(s => s.trim())
        .filter(Boolean);

      let updatedList = [];
      if (isRemoving) {
        if (currentRequired.length > 0) {
          updatedList = currentRequired.filter(id => id !== targetPcId);
        } else {
          const peers = Array.isArray(snapshot?.runtime?.peers) ? snapshot.runtime.peers : [];
          const ids = new Set(peers.map(p => p.pc_id));
          const localId = el.coordinationPcId?.value || snapshot?.config?.pc_id;
          if (localId) ids.add(localId);
          ids.delete(targetPcId);
          updatedList = Array.from(ids);
        }
      } else {
        const ids = new Set(currentRequired);
        ids.add(targetPcId);
        updatedList = Array.from(ids);
      }

      if (el.coordinationRequiredPeers) {
        el.coordinationRequiredPeers.value = updatedList.join(', ');
        formDirty = true;
        await save(
          isRemoving
            ? `PC [ ${targetPcId} ]를 필수 참가 명단에서 제외했습니다.\n\n[ 변경된 확정 명단: ${updatedList.join(', ') || '없음'} ]`
            : `PC [ ${targetPcId} ]를 필수 참가 명단에 추가했습니다.\n\n[ 변경된 확정 명단: ${updatedList.join(', ')} ]`,
          isRemoving ? '명단 제외 저장 완료' : '명단 추가 저장 완료',
        );
      }
    });
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
