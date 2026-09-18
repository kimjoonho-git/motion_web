import {
  fetchCoordinationStatus,
  sendCoordinationControl,
  saveCoordinationSettings,
} from './api.js';
import { showAlert, showConfirm, dismissAllDialogs } from './ui_dialogs.js';
import { midiTargetView } from './midi_target.js';

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
    if (el.coordinationRequiredPeers) el.coordinationRequiredPeers.value = Array.isArray(config.required_peers) ? config.required_peers.join(', ') : '';
  }

  /** MIDI 를 쓸 PC · 판정은 `midi_target.js` 가 하고 여기서는 그리기만 한다. */
  function renderMidiTarget(runtime = {}, config = {}) {
    if (!el.midiTargetChoices) return;
    const view = midiTargetView({
      relay: runtime.midi_relay || {},
      config: runtime.config || config,
      peers: Array.isArray(runtime.peers) ? runtime.peers : [],
      joined: runtime.joined === true,
    });
    if (el.midiTargetState) el.midiTargetState.textContent = view.state;
    if (el.midiTargetMessage) {
      el.midiTargetMessage.textContent = view.reason;
      el.midiTargetMessage.classList.toggle('hidden', !view.reason);
    }
    el.midiTargetChoices.innerHTML = view.choices.map((choice) => {
      const label = choice.isDevice ? `${choice.label} · 장치` : choice.label;
      return `<button type="button" class="midi-target-choice${choice.active ? ' active' : ''}"`
        + ` data-midi-target="${text(choice.pc_id)}"${choice.disabled ? ' disabled' : ''}>`
        + `${text(label)}</button>`;
    }).join('');
  }

  function peerRow(peer = {}, requiredPeers = new Set(), fixedParticipants = new Set()) {
    const alarm = peer.alarm || {};
    const alarmText = Number(peer.servo_alarm_grade || 0) > 0
      ? `${Number(peer.servo_alarm_grade)} · ${alarm.message || alarm.error_code || '확인 필요'}`
      : '0';
    
    const isRequired = requiredPeers.has(peer.pc_id);
    const pcNameHtml = `<strong>${text(peer.display_name || peer.pc_id || '-')}</strong><small>${peer.display_name ? text(peer.pc_id || '') : ''}</small>`;
    // 색 이름은 `01-base.css` 의 것을 쓴다 · §6-147
    //
    // 전에는 `--color-primary` 를 썼는데 **이 프로젝트에 없는 이름**이다 ·
    // 배경색이 통째로 비고 글씨는 흰색이라, 흰 바탕에 흰 글씨가 찍혀서
    // 「필수」가 안 보였다 · 이모지만 자기 색이라 ⭐ 하나만 남았다.
    const badgeHtml = isRequired ? `<span style="display: inline-block; margin-left: 6px; padding: 2px 6px; background-color: var(--green); color: white; border-radius: 4px; font-size: 10px; font-weight: bold;">⭐ 필수</span>` : '';
    
    const executionStateText = fixedParticipants.has(peer.pc_id) ? '고정 참가' : (isRequired ? '명단 포함' : '대기');
    const executionStateClass = fixedParticipants.has(peer.pc_id) ? 'coordination-state-ok' : (isRequired ? 'coordination-state-ok' : 'coordination-state-warn');
    
    const actionButton = isRequired
      ? `<button type="button" class="danger remove-peer-btn" data-pc-id="${text(peer.pc_id)}" style="padding: 2px 8px; font-size: 11px; cursor: pointer;">명단 제외</button>`
      : `<button type="button" class="primary add-peer-btn" data-pc-id="${text(peer.pc_id)}" style="padding: 2px 8px; font-size: 11px; cursor: pointer;">명단 추가</button>`;

    // 표가 둘이다 · §6-100
    //
    // **구성**(누가 참가했나 · 어떤 버전인가 · 명단)은 `PC 연동 설정` 탭,
    // **진행**(회차 · 단계 · 진행률 · 동기화 · 알람)은 `모션 실행` 탭 ·
    // 실행을 시작한 탭에서 진행을 못 보면 탭을 왔다갔다 하게 된다.
    const pcCell = `<td>${pcNameHtml}${badgeHtml}</td>`;
    const joinCell = `<td class="${executionStateClass}"><strong>${executionStateText}</strong></td>`;
    return {
      setup: `<tr>
      ${pcCell}
      <td class="${stateClass(peer.state)}"><span class="peer-status-dot ${peer.state || 'offline'}"></span>${text(stateText(peer.state))}</td>
      ${joinCell}
      <td title="${text(peer.git_message || '')}">[${text(peer.git_branch || '?')}] ${text(peer.git_hash || '-')}</td>
      <td style="text-align: center;">${actionButton}</td>
    </tr>`,
      progress: `<tr>
      ${pcCell}
      ${joinCell}
      <td>${text(peerCycleText(peer))}</td>
      <td class="${peerPhaseClass(peer)}">${text(peerMotionStep(peer))}</td>
      <td>${text(peerProgressText(peer))}</td>
      <td class="${stateClass(peer.trigger_sync_state)}">${text(stateText(peer.trigger_sync_state))}</td>
      <td>${Number(peer.trigger_sync_uncertainty_ms || 0).toFixed(3)} ms</td>
      <td class="${Number(peer.servo_alarm_grade || 0) > 0 ? 'coordination-state-bad' : 'coordination-state-ok'}">${text(alarmText)}</td>
    </tr>`,
    };
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
    
    // 랜이 이 서비스보다 늦게 올라왔는가 · §6-96 · 판정은 연동 노드가 한다 ·
    // 화면이 주소를 따로 재면 주인이 둘이 된다
    if (el.coordinationNetworkStaleBanner) {
      const networkStale = runtime.network_stale || {};
      el.coordinationNetworkStaleBanner.textContent = networkStale.active === true
        ? `⚠️ ${networkStale.message || '랜 주소가 기동 뒤에 바뀌었습니다'}`
        : '';
    }

    // 이 모듈의 다른 요소는 모두 주입받은 등록부를 쓴다 · 여기만 전역
    // `document` 를 잡고 있어서 노드 없이 렌더를 검증할 수 없었다.
    const rosterBanner = el.coordinationConfirmedRosterBanner;
    if (rosterBanner) {
      if (requiredPeersList.length > 0) {
        // 색조도 실제 팔레트(`--green` #16834a)에 맞춘다 · 전에는 테두리만
        // 없는 변수라 바탕과 테두리가 서로 다른 초록이었다
        rosterBanner.innerHTML = `<span style="color: var(--green);">✅ 현재 그룹 필수 참가 명단:</span> ${requiredPeersList.join(', ')}`;
        rosterBanner.style.backgroundColor = 'rgba(22, 131, 74, 0.10)';
        rosterBanner.style.border = '1px solid var(--green)';
        rosterBanner.style.color = '';
      } else {
        rosterBanner.innerHTML = `⚠️ 시스템을 시작하려면 아래 표에서 명단을 확정하세요 (명단 미확정)`;
        rosterBanner.style.backgroundColor = 'rgba(198, 40, 40, 0.10)';
        rosterBanner.style.border = '1px solid var(--red)';
        rosterBanner.style.color = 'var(--red)';
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
    // 빠져 있을 때만 「다시 참가」가 보인다 · §6-132
    //
    // 참가 초기값은 「연동 사용」 설정이다 (`_joined = configured`) · 그래서
    // 평소에는 이미 참가한 채로 뜨고, 참가 버튼은 누를 일이 없다 · 늘 보이면
    // "눌러야 하나" 를 매번 묻게 된다.
    if (el.coordinationJoinButton) {
      el.coordinationJoinButton.hidden = joined;
      el.coordinationJoinButton.disabled = loading || !nodeReady || joined || active;
      el.coordinationJoinButton.title = '이 PC 를 그룹에 다시 넣습니다';
    }
    if (el.coordinationTemporaryDisableButton) {
      el.coordinationTemporaryDisableButton.hidden = !joined;
      el.coordinationTemporaryDisableButton.disabled = loading || !nodeReady || !joined;
      el.coordinationTemporaryDisableButton.title = active
        ? '이 PC와 다른 PC의 그룹 모션을 즉시 정지한 뒤 이 PC 를 그룹에서 뺍니다'
        : '이 PC 를 그룹에서 빼 단독 모션·모션 스튜디오를 사용합니다';
    }
    // 실행 제어는 모션 실행 화면으로 옮겼다 · 여기서는 왜 못 하는지만 알린다 · §6-65
    if (el.coordinationRunAvailability) {
      const availability = groupRunAvailability();
      el.coordinationRunAvailability.textContent = availability.ok
        ? '그룹 실행 준비됨 · 모션 실행 화면에서 시작하세요'
        : `그룹 실행 불가 · ${availability.reason}`;
      el.coordinationRunAvailability.classList.toggle('warning-text', !availability.ok);
    }

    if (el.coordinationAcknowledgeErrorButton) el.coordinationAcknowledgeErrorButton.disabled = loading || !groupErrorActive;
    renderMidiTarget(runtime, config);
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
      ].includes(code);

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
    if (el.coordinationPeerRows || el.motionRunPeerRows) {
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
      
      if (el.coordinationPeerRows) {
        el.coordinationPeerRows.innerHTML = rows.length
          ? rows.map((row) => row.setup).join('')
          : '<tr><td colspan="5" class="empty">그룹에 참가하면 PC 상태가 표시됩니다</td></tr>';
      }
      if (el.motionRunPeerRows) {
        el.motionRunPeerRows.innerHTML = rows.length
          ? rows.map((row) => row.progress).join('')
          : '<tr><td colspan="8" class="empty">그룹에 참가하면 각 PC 진행이 표시됩니다</td></tr>';
      }
      // 명단은 그룹에 참가했으면 늘 보인다
      el.coordinationRosterSection?.classList.toggle('hidden', !joined);
      // 그룹 실행은 **마스터에서만** 보인다 · 조정 노드가 슬레이브를 거부한다
      el.coordinationGroupRunSection?.classList.toggle(
        'hidden', !(joined && config.is_master === true),
      );
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
    // 마스터를 켜는데 그룹에 이미 마스터가 있으면 저장 뒤에야 MULTIPLE_MASTERS
    // 오류로 알게 된다 · 실행 중이면 그 자리에서 멈춘다. 저장 전에 묻는다 · §6-70
    const turningMaster = el.coordinationIsMaster?.value === 'true'
      && (snapshot?.config || {}).is_master !== true;
    const otherMaster = (snapshot?.runtime?.peers || []).find((peer) => peer.is_master);
    if (turningMaster && otherMaster) {
      const name = otherMaster.display_name || otherMaster.pc_id;
      const confirmed = await showConfirm(
        `그룹에 이미 마스터가 있습니다 · ${name}\n`
        + '마스터가 둘이면 그룹 오류로 실행이 멈춥니다.\n'
        + `계속하려면 먼저 ${name} 의 마스터를 해제하세요.`,
        { title: '마스터가 이미 있습니다', confirmLabel: '그래도 저장', tone: 'danger' },
      );
      if (!confirmed) return;
    }
    loading = true;
    render();
    try {
      const result = await saveCoordinationSettings({
        enabled: el.coordinationEnabled?.value === 'true',
        is_master: el.coordinationIsMaster?.value === 'true',
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
        ? '지금 그룹 모션이 돌고 있습니다.\n\n'
          + '참가한 모든 PC 의 모션이 즉시 정지된 뒤 이 PC 가 그룹에서 빠집니다.\n'
          + '회차가 끝나기를 기다리지 않습니다.'
        : '이 PC 를 그룹에서 뺍니다.\n\n'
          + '다른 PC의 확인 없이 빠집니다. 단독 모션·모션 스튜디오를 사용할 수 있습니다.\n'
          + '프로그램을 다시 켜면 「연동 사용」 설정을 따라 자동으로 다시 참가합니다.',
      {
        title: '그룹에서 빠지기',
        confirmLabel: '빠지기',
        tone: 'warning',
      },
    );
    if (!confirmed) return;
    await control('temporarily_disable');
  }

  /** 그룹 실행에 넘길 반복 옵션 · 이 화면의 입력값을 그대로 쓴다. */
  function groupRunFields() {
    return {
      repeat_mode: String(el.coordinationRepeatMode?.value || 'reinitialize'),
      dwell_sec: Number(el.coordinationDwellSec?.value || 0),
      target_cycle_count: Math.max(
        0, parseInt(el.coordinationTargetCycle?.value || '0', 10),
      ),
    };
  }

  function groupRunOptions(runMode, overrides = {}) {
    const dwellSec = Number(overrides.dwell_sec);
    const targetCycleCount = Number(overrides.target_cycle_count);
    return {
      run_mode: runMode,
      // 안 적었으면 「초기 위치 이동 후 다음」이다 · §6-135
      // 지금은 `groupRunFields()` 가 늘 값을 채워 주지만, 기본값이 두 가지면
      // 언젠가 갈라진다 · 스케줄이 바로 그렇게 죽었다
      repeat_mode: String(overrides.repeat_mode || 'reinitialize'),
      dwell_sec: Number.isFinite(dwellSec) && dwellSec >= 0 ? dwellSec : 0,
      target_cycle_count: Number.isFinite(targetCycleCount) && targetCycleCount >= 0
        ? targetCycleCount
        : 0,
    };
  }

  /** 그룹 실행을 지금 시작할 수 있는가 · 못 하면 이유를 함께 준다.
   *
   * 판정을 버튼에서 떼어냈다 · 실행 화면이 "이 PC / 그룹" 중 그룹을 고를 때
   * 같은 규칙을 그대로 써야 하고, 규칙만 따로 시험할 수 있어야 한다 · §6-65
   */
  function groupRunAvailability() {
    const runtime = snapshot?.runtime || {};
    const execution = runtime.execution || {};
    const peers = Array.isArray(runtime.peers) ? runtime.peers : [];
    const active = Boolean(execution.execution_id);
    const state = {
      active,
      peerCount: peers.length + 1,
      state: String(execution.state || 'idle'),
    };
    if (loading) return { ...state, ok: false, reason: '명령 전달 중' };
    if (runtime.joined !== true) return { ...state, ok: false, reason: '그룹에 참가하지 않았습니다' };
    // 시작은 마스터만 · 정지는 누구나 · §6-70
    if ((snapshot?.config || {}).is_master !== true) {
      return { ...state, ok: false, reason: '이 PC 는 슬레이브입니다 · 마스터 PC 에서 시작하세요' };
    }
    if (active) return { ...state, ok: false, reason: '그룹 실행이 진행 중입니다' };
    if (peers.length < 1) return { ...state, ok: false, reason: '연결된 다른 PC가 없습니다' };
    if (peers.some((peer) => peer.state !== 'online' || Number(peer.servo_alarm_grade || 0) > 0)) {
      return { ...state, ok: false, reason: '참가 PC 중 통신 이상이나 알람이 있습니다' };
    }
    if ((runtime.coordination_error || {}).active === true) {
      return { ...state, ok: false, reason: '그룹 오류를 확인해야 합니다' };
    }
    return { ...state, ok: true, reason: '' };
  }

  function bindEvents() {
    el.coordinationSaveButton?.addEventListener('click', save);
    el.coordinationJoinButton?.addEventListener('click', () => control('join'));
    // 그룹 실행은 **여기가 주인**이다 · §6-100
    //
    // 그룹 실행은 모션 파일을 들고 가지 않는다 · 참가한 PC 들에게 시작·정지
    // 신호만 보내고 각 PC 는 제 모션을 돌린다 · `모션 실행` 탭의 "이 파일을
    // 이 PC 에서 돌린다" 와는 다른 일이라 버튼도 따로 둔다 · 전에는 한 버튼이
    // 대상에 따라 둘 다 했고, 모터가 움직이는 버튼에서 그 애매함은 위험했다.
    el.coordinationInitializeButton?.addEventListener(
      'click', () => groupRun.initialize());
    el.coordinationStartOnceButton?.addEventListener(
      'click', () => groupRun.start(groupRunFields()));
    el.coordinationStartContinuousButton?.addEventListener(
      'click', () => groupRun.startContinuous(groupRunFields()));
    el.coordinationStopNowButton?.addEventListener(
      'click', () => groupRun.stopNow());
    el.coordinationStopAfterButton?.addEventListener(
      'click', () => groupRun.stopAfterCycle());
    el.coordinationTemporaryDisableButton?.addEventListener('click', temporarilyDisable);

    el.coordinationAcknowledgeErrorButton?.addEventListener('click', () => control('acknowledge_group_error'));
    [el.coordinationDisplayName, el.coordinationGroupId, el.coordinationDomainId, el.coordinationEnabled, el.coordinationIsMaster, el.coordinationRequiredPeers]
      .forEach((field) => field?.addEventListener('input', () => { formDirty = true; }));

    el.midiTargetChoices?.addEventListener('click', async (event) => {
      const button = event.target.closest('button[data-midi-target]');
      if (!button || button.disabled) return;
      const target = button.dataset.midiTarget || '';
      // 장치를 든 PC 를 고르면 되돌리기다 · 빈 값으로 보낸다
      const relay = snapshot?.runtime?.midi_relay || {};
      const wanted = target === String(relay.device_pc_id || '') ? '' : target;
      await control('set_midi_target', { pc_id: wanted });
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

  /** 실행 화면이 "그룹" 범위를 골랐을 때 쓰는 창구 · §6-65 */
  /** 이 PC 의 역할과 현재 마스터 · 실행 화면이 알린다 · §6-66 */
  function groupRole() {
    const config = snapshot?.config || {};
    const runtime = snapshot?.runtime || {};
    const peers = Array.isArray(runtime.peers) ? runtime.peers : [];
    const masterPeer = peers.find((peer) => peer.is_master);
    return {
      isMaster: config.is_master === true,
      joined: runtime.joined === true,
      peerCount: peers.length + 1,
      // 실행 화면이 한 줄로 요약해 보여 준다 · 판정이 아니라 표시용이다 · §6-98
      peers: peers.map((peer) => ({
        pc_id: String(peer.pc_id || ''),
        display_name: String(peer.display_name || peer.pc_id || ''),
        state: String(peer.state || ''),
        is_master: peer.is_master === true,
      })),
      master: config.is_master === true
        ? (config.display_name || config.pc_id || '이 PC')
        : (masterPeer?.display_name || masterPeer?.pc_id || ''),
    };
  }

  const groupRun = {
    availability: groupRunAvailability,
    role: groupRole,
    initialize: initializeGroup,
    start: (options) => control('start_group', groupRunOptions('once', options)),
    startContinuous: (options) => control('start_group', groupRunOptions('continuous', options)),
    stopAfterCycle: () => {
      // 아직 모션이 돌기 전이면 "회차 후"가 의미가 없다 · 바로 세운다
      const state = snapshot?.runtime?.execution?.state;
      return ['preparing', 'initializing', 'armed', 'start_scheduled'].includes(state)
        ? control('stop_now')
        : control('stop_after_cycle');
    },
    stopNow: () => control('stop_now'),
  };

  return { start, refresh, render, renderSnapshot, groupRun };
}
