import { fetchStatusSnapshot, setMonitoringEnabled } from './api.js?v=20260714-motor-event-log-clear';
import { getElements } from './dom.js?v=20260714-midi-monitor';
import { createMotorEventLogController } from './event_log.js?v=20260714-motion-log-mode';
import { createMidiMonitorController } from './midi_monitor.js?v=20260714-midi-readonly';
import { createMotionDataController } from './motion_data.js?v=20260714-shared-graph-toggle';
import { createMotionTestController } from './motion_test.js?v=20260714-stable-live-render';
import { createMotorConfigController } from './motor_config.js?v=20260714-generic-connection-state';
import { renderAccess, renderMonitoring } from './monitoring.js?v=20260714-stable-live-render';
import { StatusSocket } from './socket.js';

const el = getElements();
const appState = {
  latestState: null,
  rawMode: false,
  activeMonitoringFilter: 'all',
  activeWorkspacePanel: 'monitoring',
  configApplyInProgress: false,
  configApplyStartedAtMs: null,
  configApplyReadySinceMs: null,
  configApplyConnectionInterrupted: false,
  motorErrorActiveKeys: new Set(),
  motorErrorDismissedKeys: new Set(),
  motorErrorLatchedEntries: new Map(),
};
const RESTART_READY_STABLE_MS = 3500;

function renderWorkspacePanel() {
  if (el.workspaceTabs) {
    el.workspaceTabs.querySelectorAll('[data-workspace-tab]').forEach((button) => {
      const active = button.dataset.workspaceTab === appState.activeWorkspacePanel;
      button.classList.toggle('active', active);
      button.setAttribute('aria-selected', active ? 'true' : 'false');
    });
  }
  if (el.workspacePanels) {
    el.workspacePanels.forEach((panel) => {
      panel.classList.toggle('hidden', panel.dataset.workspacePanel !== appState.activeWorkspacePanel);
    });
  }
}

function basename(path) {
  return String(path || '').split(/[\\/]/).filter(Boolean).pop() || '';
}

function workContextStatus(configContext, motionContext) {
  if (!configContext.motorConfigLoaded) return '모터축 설정 미로드';
  if (configContext.motorConfigChanged) return '모터축 설정 변경 있음';
  if (configContext.motorConfigApplyPending) return '설정 적용 대기';
  if (!motionContext.motionFileSelected) return '모션 파일 미선택';
  if (!motionContext.motionFileValid) return '모션 파일 확인 필요';
  if (!motionContext.mappingFileSelected) return '매핑 파일 미선택';
  if (!motionContext.mappingValidated) return '매핑 검증 필요';
  return motionContext.mappingValid ? '검증 완료' : '매핑 검증 오류';
}

function updateWorkContext() {
  const configContext = motorConfig?.getWorkContext?.() || {};
  const motionContext = motionData?.getWorkContext?.() || {};
  if (el.workContextMotorConfig) {
    el.workContextMotorConfig.textContent = basename(configContext.motorConfigFile);
  }
  if (el.workContextMotionFile) {
    el.workContextMotionFile.textContent = motionContext.motionFile || '';
  }
  if (el.workContextMappingFile) {
    el.workContextMappingFile.textContent = basename(motionContext.mappingFile);
  }
  if (el.workContextState) {
    el.workContextState.textContent = workContextStatus(configContext, motionContext);
  }
}

function renderLatestState(nextState = null) {
  if (nextState) {
    appState.latestState = nextState;
  }
  if (!appState.latestState) return;
  renderMonitoring(appState.latestState, {
    el,
    rawMode: appState.rawMode,
    activeMonitoringFilter: appState.activeMonitoringFilter,
    shouldShowMonitoringMotor: motorConfig.shouldShowMonitoringMotor,
    registryCount: motorConfig.getRegistryCount(),
    selectedMotionTestAxis: motionTest.getSelectedAxis(),
  });
  motorConfig.renderRuntimeState();
  motionTest.renderLatestState();
  motionData.renderRuntimeState();
  updateWorkContext();
  updateMotorErrorPopup(appState.latestState);
}

function motionStateFromPayload(payload) {
  if (!payload?.motion_state) return null;
  return {
    ...payload.motion_state,
    motion_test_limits: payload.motion_test_limits || payload.motion_state.motion_test_limits || {},
    motion_run_status: payload.motion_run_status || payload.motion_state.motion_run_status || {},
  };
}

function isAcServoMotor(motor) {
  const text = [
    motor?.motor_type,
    motor?.motor_type_label,
    motor?.driver_model,
    motor?.driver_name,
    motor?.transport,
  ].join(' ').toLowerCase();
  return text.includes('minas') || text.includes('ac servo') || text.includes('ac_servo');
}

function axisListText(motors) {
  return motors
    .map((motor) => `Axis ${motor.controller_index}`)
    .join(', ');
}

function setRestartOverlay(visible, title = '', message = '', detail = '') {
  if (!el.restartOverlay) return;
  el.restartOverlay.classList.toggle('hidden', !visible);
  document.body.classList.toggle('ui-locked', visible);
  if (el.restartOverlayTitle && title) el.restartOverlayTitle.textContent = title;
  if (el.restartOverlayMessage && message) el.restartOverlayMessage.textContent = message;
  if (el.restartOverlayDetail && detail) el.restartOverlayDetail.textContent = detail;
}

function formatStatusHex(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return '-';
  return `0x${(number & 0xFFFF).toString(16).toUpperCase().padStart(4, '0')}`;
}

function motorErrorDetected(motor) {
  const errorCode = Number(motor?.errorcode ?? 0);
  const statusword = Number(motor?.statusword ?? 0);
  const statusText = String(motor?.status_text || '');
  return Boolean(motor?.fault)
    || (Number.isFinite(errorCode) && errorCode !== 0)
    || Boolean(statusword & 0x0008)
    || /fault|alarm|error/i.test(statusText);
}

function motorErrorKey(motor) {
  const axis = motor?.controller_index ?? motor?.axis ?? motor?.id ?? '?';
  const statusword = motor?.statusword ?? '';
  const errorCode = motor?.errorcode ?? motor?.errorcode_raw ?? '';
  const statusText = motor?.status_text ?? '';
  const errorIdentity = Number(errorCode) !== 0
    ? `code:${errorCode}`
    : (Number(statusword) & 0x0008 ? 'fault-bit' : `status:${statusText}`);
  return [axis, errorIdentity].join('|');
}

function motorErrorTitle(motor) {
  const axis = motor?.controller_index ?? motor?.axis ?? motor?.id ?? '?';
  const type = motor?.motor_type_label || motor?.motor_type || 'Motor';
  const name = motor?.display_name || motor?.driver_name || motor?.driver_model || '';
  return `Axis ${axis} · ${type}${name ? ` · ${name}` : ''}`;
}

function motorErrorDetailRows(motor) {
  const rows = [
    ['상태', motor?.status_text || motor?.state || '-'],
    ['에러', motor?.error_text || (Number(motor?.errorcode || 0) === 0 ? 'Fault' : `Error ${motor.errorcode}`)],
    ['Statusword', formatStatusHex(motor?.statusword)],
  ];
  const errorCode = motor?.errorcode_raw ?? motor?.errorcode;
  if (errorCode !== null && errorCode !== undefined) {
    rows.push(['Error code', formatStatusHex(errorCode)]);
  }
  return rows;
}

function clearNode(node) {
  while (node?.firstChild) node.removeChild(node.firstChild);
}

function renderMotorErrorList(errors) {
  if (!el.motorErrorList) return;
  clearNode(el.motorErrorList);
  errors.forEach(({ motor }) => {
    const item = document.createElement('div');
    item.className = 'motor-error-item';

    const title = document.createElement('strong');
    title.textContent = motorErrorTitle(motor);
    item.appendChild(title);

    motorErrorDetailRows(motor).forEach(([label, value]) => {
      const row = document.createElement('div');
      row.className = 'motor-error-row';
      const labelNode = document.createElement('span');
      labelNode.textContent = label;
      const valueNode = document.createElement('b');
      valueNode.textContent = value;
      row.appendChild(labelNode);
      row.appendChild(valueNode);
      item.appendChild(row);
    });

    el.motorErrorList.appendChild(item);
  });
}

function setMotorErrorPopup(visible, errors = []) {
  if (!el.motorErrorPopup) return;
  el.motorErrorPopup.classList.toggle('hidden', !visible);
  if (!visible) return;
  if (el.motorErrorTitle) {
    el.motorErrorTitle.textContent = errors.length > 1
      ? `모터 에러 발생 · ${errors.length}축`
      : '모터 에러 발생';
  }
  if (el.motorErrorMessage) {
    el.motorErrorMessage.textContent = '동작을 멈추고 모터 상태와 드라이버 알람을 확인하세요.';
  }
  renderMotorErrorList(errors);
}

function updateMotorErrorPopup(state) {
  const motors = Array.isArray(state?.motors) ? state.motors : [];
  const errors = motors
    .filter(motorErrorDetected)
    .map((motor) => ({ motor, key: motorErrorKey(motor) }));
  const currentKeys = new Set(errors.map((entry) => entry.key));

  appState.motorErrorDismissedKeys.forEach((key) => {
    if (!currentKeys.has(key)) appState.motorErrorDismissedKeys.delete(key);
  });
  appState.motorErrorActiveKeys = currentKeys;

  errors.forEach((entry) => {
    if (!appState.motorErrorDismissedKeys.has(entry.key)) {
      appState.motorErrorLatchedEntries.set(entry.key, entry);
    }
  });

  if (appState.motorErrorLatchedEntries.size > 0) {
    setMotorErrorPopup(true, Array.from(appState.motorErrorLatchedEntries.values()));
  }
}

function dismissMotorErrorPopup() {
  appState.motorErrorLatchedEntries.forEach((_entry, key) => {
    appState.motorErrorDismissedKeys.add(key);
  });
  appState.motorErrorLatchedEntries.clear();
  setMotorErrorPopup(false);
}

function restartReadyState(payload) {
  const state = motionStateFromPayload(payload) || appState.latestState;
  if (appState.configApplyInProgress && !appState.configApplyConnectionInterrupted) {
    return {
      ready: false,
      title: '노드 재시작 시작 대기',
      detail: '이전 웹 연결 종료 대기',
    };
  }
  if (!payload) {
    return {
      ready: false,
      title: '웹 재연결 대기',
      detail: 'motion_web_bridge 재시작을 기다리는 중',
    };
  }
  if (!state) {
    return {
      ready: false,
      title: '노드 재시작 중입니다',
      detail: 'motion_state 수신 대기',
    };
  }
  if (state.monitoring_enabled !== true) {
    return {
      ready: false,
      title: '모니터링 시작 대기',
      detail: 'monitoring_enabled=true 상태 대기',
    };
  }

  const generatedAt = Number(state.generated_at);
  const lastMotorStatusAt = Number(state.last_motor_status_at);
  if (!Number.isFinite(lastMotorStatusAt) || lastMotorStatusAt <= 0) {
    return {
      ready: false,
      title: '모터 상태 수신 대기',
      detail: '/motion_control/motor_status 기반 상태 대기',
    };
  }
  if (
    Number.isFinite(generatedAt)
    && generatedAt > 0
    && generatedAt - lastMotorStatusAt > 1.5
  ) {
    return {
      ready: false,
      title: '모터 상태 갱신 대기',
      detail: `마지막 motor_status age ${Math.max(generatedAt - lastMotorStatusAt, 0).toFixed(2)}초`,
    };
  }

  const motors = Array.isArray(state.motors) ? state.motors : [];
  if (motors.length === 0) {
    return {
      ready: false,
      title: '모터 목록 수신 대기',
      detail: '설정 축과 런타임 축 상태 대기',
    };
  }

  const expectedMotorCount = Math.max(
    Number(state.motor_count || 0),
    Number(state.known_motors_count || 0),
    motors.length,
  );
  if (expectedMotorCount > 0 && motors.length < expectedMotorCount) {
    return {
      ready: false,
      title: '모터 목록 수신 대기',
      detail: `모터 목록 ${motors.length}/${expectedMotorCount}축 수신`,
    };
  }

  const connectionState = (motor) => String(
    motor.connection_state
      || (motor.state === 'detected' ? 'online' : motor.state)
      || 'unknown'
  );
  const pendingStates = new Set(['', 'unknown', 'monitoring_off', 'stale', 'initializing']);
  const pendingMotors = motors.filter((motor) => pendingStates.has(connectionState(motor)));
  if (pendingMotors.length > 0) {
    return {
      ready: false,
      title: '모터 상태 판정 대기',
      detail: `${axisListText(pendingMotors)} 연결 상태 확인 중`,
    };
  }

  const connectedMotors = motors.filter((motor) => connectionState(motor) === 'online');
  const disconnectedMotors = motors.filter((motor) => connectionState(motor) !== 'online');
  const faultMotors = motors.filter((motor) => Boolean(motor.fault));
  const discovery = motorConfig?.getDiscoverySummary?.() || {};
  if (connectedMotors.length === 0) {
    return {
      ready: false,
      failed: true,
      title: '모터 연결 실패',
      detail: '모터 검색 여부와 관계없이 제어 런타임에서 통신 중인 모터가 없습니다.',
    };
  }
  return {
    ready: true,
    title: '노드 재시작 완료',
    detail: [
      `런타임 온라인 ${connectedMotors.length}축`,
      discovery.hasDirectScan ? `버스 검색 감지 ${Number(discovery.discoveredCount || 0)}축` : '',
      discovery.ethercatScanned ? `EtherCAT ${discovery.ethercatCount}축` : '',
      discovery.dynamixelScanned ? `Dynamixel ${discovery.dynamixelCount}축` : '',
      `미연결 ${disconnectedMotors.length}축`,
      `Fault ${faultMotors.length}축`,
    ].filter(Boolean).join(' · '),
  };
}

function updateRestartProgress(payload = null) {
  if (!appState.configApplyInProgress) return;
  const state = restartReadyState(payload);
  const message = [
    'motor_manager_node, motion_state_monitor, motion_supervisor, motion_web_bridge 상태를 확인하는 중입니다.',
    'YAML 등록 수가 아니라 직접 검색되거나 실제 감지된 모터를 기준으로 확인합니다.',
  ].join(' ');

  if (state.failed) {
    appState.configApplyInProgress = false;
    appState.configApplyStartedAtMs = null;
    appState.configApplyReadySinceMs = null;
    appState.configApplyConnectionInterrupted = false;
    setRestartOverlay(true, state.title, '노드는 재시작됐지만 연결된 모터를 찾지 못했습니다.', state.detail);
    if (el.bridgeState) el.bridgeState.textContent = state.title;
    if (el.summaryText) el.summaryText.textContent = state.detail;
    setTimeout(() => {
      if (!appState.configApplyInProgress) setRestartOverlay(false);
    }, 5000);
    return;
  }

  if (!state.ready) {
    appState.configApplyReadySinceMs = null;
    setRestartOverlay(true, state.title, message, state.detail);
    if (el.bridgeState) el.bridgeState.textContent = state.title;
    if (el.summaryText) el.summaryText.textContent = state.detail;
    return;
  }

  if (!appState.configApplyReadySinceMs) {
    appState.configApplyReadySinceMs = Date.now();
    setRestartOverlay(true, '노드 재시작 완료 확인 중', message, state.detail);
    return;
  }

  if (Date.now() - appState.configApplyReadySinceMs < RESTART_READY_STABLE_MS) {
    setRestartOverlay(true, '노드 재시작 완료 확인 중', message, state.detail);
    return;
  }

  appState.configApplyInProgress = false;
  appState.configApplyStartedAtMs = null;
  appState.configApplyReadySinceMs = null;
  appState.configApplyConnectionInterrupted = false;
  setRestartOverlay(true, state.title, '노드 재시작과 모터 상태 수신을 확인했습니다.', state.detail);
  if (el.bridgeState) el.bridgeState.textContent = 'Connected';
  if (el.summaryText) el.summaryText.textContent = '노드 재시작 완료';
  setTimeout(() => {
    if (!appState.configApplyInProgress) {
      setRestartOverlay(false);
    }
  }, 800);
}

const motorConfig = createMotorConfigController({
  el,
  getRawMode: () => appState.rawMode,
  getLatestState: () => appState.latestState,
  renderLatestState,
  onWorkContextChange: updateWorkContext,
  onConfigApplyStart: () => {
    appState.configApplyInProgress = true;
    appState.configApplyStartedAtMs = Date.now();
    appState.configApplyReadySinceMs = null;
    appState.configApplyConnectionInterrupted = false;
    if (el.bridgeState) el.bridgeState.textContent = '설정 반영 중';
    if (el.summaryText) el.summaryText.textContent = '설정 반영 중입니다. 웹 연결이 자동으로 다시 연결됩니다.';
    setRestartOverlay(
      true,
      '노드 재시작 중입니다',
      'motor_manager_node, motion_state_monitor, motion_supervisor, motion_web_bridge가 다시 시작될 때까지 기다려 주세요.',
      '재시작 요청 전송 중',
    );
  },
  onConfigApplyComplete: () => {
    appState.configApplyInProgress = false;
    appState.configApplyStartedAtMs = null;
    appState.configApplyReadySinceMs = null;
    appState.configApplyConnectionInterrupted = false;
    setRestartOverlay(false);
  },
});

const motionTest = createMotionTestController({
  el,
  getLatestState: () => appState.latestState,
});

const motionData = createMotionDataController({
  el,
  getLatestState: () => appState.latestState,
  onWorkContextChange: updateWorkContext,
});

const midiMonitor = createMidiMonitorController({ el });

const motorEventLog = createMotorEventLogController({ el });

async function fetchStatus() {
  if (!el.refreshButton) return;
  el.refreshButton.disabled = true;
  const originalText = el.refreshButton.textContent;
  el.refreshButton.textContent = 'Refreshing';
  try {
    const payload = await fetchStatusSnapshot();
    if (el.bridgeState) el.bridgeState.textContent = payload.bridge_state || 'HTTP';
    renderAccess(payload, el);
    midiMonitor.renderSnapshot(payload.midi_monitor || {});
    renderLatestState(motionStateFromPayload(payload));
    updateRestartProgress(payload);
    el.refreshButton.textContent = `Refreshed ${new Date().toLocaleTimeString()}`;
  } catch (error) {
    if (el.bridgeState) el.bridgeState.textContent = 'HTTP Error';
    el.refreshButton.textContent = 'Refresh Failed';
  } finally {
    setTimeout(() => {
      el.refreshButton.textContent = originalText;
      el.refreshButton.disabled = false;
    }, 1200);
  }
}

async function setMonitoring(enabled) {
  if (!el.monitorToggle) return;
  el.monitorToggle.disabled = true;
  try {
    const payload = await setMonitoringEnabled(enabled);
    renderLatestState(motionStateFromPayload(payload));
  } finally {
    el.monitorToggle.disabled = false;
  }
}

function connectSocket() {
  const socket = new StatusSocket({
    onOpen: () => {
      if (el.bridgeState) {
        el.bridgeState.textContent = appState.configApplyInProgress ? '재연결 완료, 상태 확인 중' : 'Connected';
      }
      if (appState.configApplyInProgress) {
        setRestartOverlay(
          true,
          '웹 재연결 완료',
          '모터 상태 수신과 각 축의 연결 상태를 확인하는 중입니다.',
          '상태 payload 대기',
        );
        fetchStatus();
      }
    },
    onMessage: (payload) => {
      renderAccess(payload, el);
      midiMonitor.renderSnapshot(payload.midi_monitor || {});
      renderLatestState(motionStateFromPayload(payload));
      updateRestartProgress(payload);
    },
    onClose: () => {
      if (appState.configApplyInProgress) {
        appState.configApplyConnectionInterrupted = true;
        appState.configApplyReadySinceMs = null;
      }
      if (el.bridgeState) {
        el.bridgeState.textContent = appState.configApplyInProgress ? '설정 반영 대기' : 'Reconnecting';
      }
      if (appState.configApplyInProgress) {
        setRestartOverlay(
          true,
          '웹 재연결 대기',
          '노드 재시작으로 웹 연결이 잠시 끊겼습니다. 자동 재연결을 기다리는 중입니다.',
          'motion_web_bridge 재시작 대기',
        );
      }
    },
    onError: () => {
      if (el.bridgeState) {
        el.bridgeState.textContent = appState.configApplyInProgress ? '설정 반영 대기' : 'Error';
      }
    },
  });
  socket.connect();
}

if (el.monitorToggle) {
  el.monitorToggle.addEventListener('click', () => {
    const enabled = !(appState.latestState && appState.latestState.monitoring_enabled);
    setMonitoring(enabled);
  });
}

if (el.refreshButton) {
  el.refreshButton.addEventListener('click', () => {
    fetchStatus();
  });
}

if (el.motorErrorConfirmButton) {
  el.motorErrorConfirmButton.addEventListener('click', () => {
    dismissMotorErrorPopup();
  });
}

if (el.monitoringTabs) {
  el.monitoringTabs.addEventListener('click', (event) => {
    const button = event.target.closest('button[data-monitoring-filter]');
    if (!button) return;
    appState.activeMonitoringFilter = button.dataset.monitoringFilter || 'all';
    renderLatestState();
  });
}

if (el.displayModeToggle) {
  el.displayModeToggle.addEventListener('click', () => {
    appState.rawMode = !appState.rawMode;
    renderLatestState();
    motorConfig.renderAfterDisplayModeChange();
  });
}

if (el.rows) {
  el.rows.addEventListener('click', (event) => {
    const row = event.target.closest('tr[data-monitoring-axis]');
    if (!row || row.dataset.monitoringAxis === '') return;
    motionTest.selectAxis(row.dataset.monitoringAxis);
    renderLatestState();
  });
}

if (el.workspaceTabs) {
  el.workspaceTabs.addEventListener('click', (event) => {
    const button = event.target.closest('button[data-workspace-tab]');
    if (!button) return;
    appState.activeWorkspacePanel = button.dataset.workspaceTab || 'monitoring';
    renderWorkspacePanel();
    renderLatestState();
    if (appState.activeWorkspacePanel === 'log') motorEventLog.activate();
  });
}

motorConfig.bindEvents();
motionTest.bindEvents();
motionData.bindEvents();
motorEventLog.bindEvents();
motorConfig.renderRegistrationTabs();
renderWorkspacePanel();
updateWorkContext();
connectSocket();
fetchStatus();
motorConfig.fetchRegistry();
motionData.fetchFiles();
