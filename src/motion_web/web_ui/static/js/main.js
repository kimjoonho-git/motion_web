import {
  fetchStatusSnapshot,
  getProjectGeneration,
  requestEmergencySafetyStop,
  requestMotionSafetyStop,
  restartManagedProgram,
  restartMotorControlSystem,
  setMonitoringEnabled,
  stopMotionRun,
  stopMotionStudio,
  setProjectGeneration,
} from './api.js?v=20260728-restart-guard-1';
import { getElements } from './dom.js?v=20260728-servo-alarm-2';
import { createMotorEventLogController } from './event_log.js?v=20260727-popup-common-3';
import { createMidiMonitorController } from './midi_monitor.js?v=20260727-popup-common-3';
import { createMotionDataController } from './motion_data.js?v=20260727-popup-common-3';
import { createMotionStudioController } from './motion_studio.js?v=20260727-editor-compact-feedback-1';
import { createMotionTestController } from './motion_test.js?v=20260728-servo-alarm-2';
import { createMotorConfigController } from './motor_config.js?v=20260727-popup-common-3';
import { createProjectExplorerController } from './project_explorer.js?v=20260727-popup-common-3';
import { renderAccess, renderMonitoring } from './monitoring.js?v=20260724-runtime-fix-1';
import { createOperationProgressManager } from './operation_progress.js?v=20260728-restart-guard-1';
import { installDialogManager } from './ui_dialogs.js?v=20260727-popup-common-3';
import { StatusSocket } from './socket.js';
import {
  createWorkspaceRouteState,
  defaultWorkspaceForGroup,
  motionTabForWorkspace,
  MOTION_WORKSPACE_DETAILS,
  normalizeWorkspaceRoute,
  workspaceForLegacyNavigation,
  workspaceGroupFor,
  workspacePanelFor,
  workspaceForProjectCategory,
} from './workspace_navigation.js?v=20260724-ui-navigation-2';
import { installFeedbackPresentation } from './ui_feedback.js?v=20260724-ui-finish-1';
import { createServoAlarmController } from './servo_alarm.js?v=20260728-servo-alarm-2';

const el = getElements();
const operationProgress = createOperationProgressManager({ el });
const appDialogs = installDialogManager({ el });
window.alert = (message) => {
  void appDialogs.alert(message, {
    title: '알림',
    tone: /실패|오류|위험|중단/.test(String(message || '')) ? 'danger' : 'info',
  });
};
installFeedbackPresentation(document);
const appState = {
  latestState: null,
  rawMode: false,
  activeMonitoringFilter: 'all',
  activeMonitoringDetailTab: 'basic',
  emergencyLatched: false,
  configApplyInProgress: false,
  configApplyStartedAtMs: null,
  configApplyReadySinceMs: null,
  configApplyConnectionInterrupted: false,
  restartCheckMode: '',
  bridgeInstanceId: '',
  restartPreviousBridgeInstanceId: '',
  restartPreviousMotorStatusAt: null,
  restartRequestAccepted: false,
  restartProgressTimer: null,
  motorIdentityBlockMessage: '',
  motorErrorActiveKeys: new Set(),
  motorErrorDismissedKeys: new Set(),
  motorErrorLatchedEntries: new Map(),
  executionContext: null,
  projectGeneration: null,
};
const workspaceRouteState = createWorkspaceRouteState('monitoring');
const RESTART_READY_STABLE_MS = 3500;
const RESTART_TIMEOUT_MS = 45000;
const IDENTITY_BLOCKED_WORKSPACES = new Set(['manual', 'motion-run']);

function blockWorkspaceForMotorIdentity(workspace) {
  if (!appState.motorIdentityBlockMessage || !IDENTITY_BLOCKED_WORKSPACES.has(workspace)) {
    return false;
  }
  window.alert(
    appState.motorIdentityBlockMessage,
  );
  workspaceRouteState.select('config');
  renderWorkspacePanel();
  return true;
}

function studioMotorActionBlockReason() {
  if (appState.emergencyLatched) return '긴급정지 잠김 상태입니다. 프로그램 재시작이 필요합니다.';
  const safety = appState.latestState?.safety_status || {};
  if (safety.commands_blocked) {
    return safety.message || '서보 에러로 모터 동작이 제한된 상태입니다.';
  }
  if (appState.motorIdentityBlockMessage) return appState.motorIdentityBlockMessage;
  if (!appState.executionContext?.ready) {
    return appState.executionContext?.message || '현재 프로젝트 실행 설정 적용 대기 중입니다.';
  }
  const state = appState.latestState;
  if (!state) return '모터 상태를 아직 수신하지 못했습니다.';
  if (state.monitoring_enabled !== true) return '모터 상태 모니터링이 꺼져 있습니다.';
  const generatedAt = Number(state.generated_at);
  const lastStatusAt = Number(state.last_motor_status_at);
  if (
    !Number.isFinite(lastStatusAt)
    || lastStatusAt <= 0
    || (Number.isFinite(generatedAt) && generatedAt - lastStatusAt > 1.5)
  ) return '최신 모터 상태를 확인할 수 없습니다.';
  const motors = Array.isArray(state.motors) ? state.motors : [];
  const online = motors.filter((motor) => String(
    motor.connection_state || (motor.state === 'detected' ? 'online' : motor.state) || '',
  ) === 'online');
  if (!online.length) return '현재 연결되어 동작 가능한 모터가 없습니다.';
  return '';
}

function renderWorkspacePanel() {
  const activeWorkspace = normalizeWorkspaceRoute(workspaceRouteState.current());
  const activeGroup = workspaceGroupFor(activeWorkspace);
  const activePanel = workspacePanelFor(activeWorkspace);
  if (el.workspaceTabs) {
    el.workspaceTabs.querySelectorAll('[data-workspace-group]').forEach((button) => {
      const active = button.dataset.workspaceGroup === activeGroup;
      button.classList.toggle('active', active);
      button.setAttribute('aria-selected', active ? 'true' : 'false');
    });
    el.workspaceTabs.querySelectorAll('[data-workspace-group-panel]').forEach((panel) => {
      panel.classList.toggle('hidden', panel.dataset.workspaceGroupPanel !== activeGroup);
    });
    el.workspaceTabs.querySelectorAll('[data-workspace-tab]').forEach((button) => {
      const active = button.dataset.workspaceTab === activeWorkspace;
      button.classList.toggle('active', active);
      button.setAttribute('aria-selected', active ? 'true' : 'false');
    });
  }
  if (el.workspacePanels) {
    el.workspacePanels.forEach((panel) => {
      panel.classList.toggle('hidden', panel.dataset.workspacePanel !== activePanel);
    });
  }
  const motionTab = motionTabForWorkspace(activeWorkspace);
  if (motionTab) {
    motionData.showTab(motionTab);
    const details = MOTION_WORKSPACE_DETAILS[activeWorkspace]
      || MOTION_WORKSPACE_DETAILS['motion-files'];
    if (el.motionWorkspaceTitle) el.motionWorkspaceTitle.textContent = details[0];
    if (el.motionWorkspaceSubtitle) el.motionWorkspaceSubtitle.textContent = details[1];
    el.motionWorkflowGuide?.classList.toggle('hidden', motionTab === 'run');
  }
}

function setActiveWorkspace(workspace, motionTab = '') {
  const target = workspaceForLegacyNavigation(workspace, motionTab);
  if (blockWorkspaceForMotorIdentity(target)) return '';
  workspaceRouteState.select(target);
  renderWorkspacePanel();
  return target;
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
  if (motionContext.mappingChanged) return '모션축 설정 변경 있음';
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
    const generation = Number(nextState.project_generation);
    if (
      Number.isInteger(appState.projectGeneration)
      && (!Number.isInteger(generation) || generation !== appState.projectGeneration)
    ) return;
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
    activeMonitoringDetailTab: appState.activeMonitoringDetailTab,
  });
  motorConfig.renderRuntimeState();
  motionTest.renderLatestState();
  motionData.renderRuntimeState();
  servoAlarm?.renderRuntimeState();
  updateWorkContext();
  updateMotorErrorPopup(appState.latestState);
  enforceEmergencyUi();
}

function clearBrowserProjectMemory(projectGeneration) {
  setProjectGeneration(projectGeneration);
  appState.projectGeneration = getProjectGeneration();
  appState.latestState = null;
  appState.executionContext = null;
  appState.motorIdentityBlockMessage = '';
  appState.motorErrorActiveKeys.clear();
  appState.motorErrorDismissedKeys.clear();
  appState.motorErrorLatchedEntries.clear();
  setMotorErrorPopup(false);
  renderMonitoring({
    project_generation: appState.projectGeneration,
    motors: [],
    motor_count: 0,
    monitoring_enabled: false,
    connection_summary: {},
  }, {
    el,
    rawMode: appState.rawMode,
    activeMonitoringFilter: appState.activeMonitoringFilter,
    shouldShowMonitoringMotor: () => false,
    registryCount: 0,
    selectedMotionTestAxis: null,
    activeMonitoringDetailTab: appState.activeMonitoringDetailTab,
  });
}

function acceptProjectPayload(payload) {
  const generation = Number(payload?.project_generation);
  if (!Number.isInteger(generation)) return false;
  if (!Number.isInteger(appState.projectGeneration)) {
    appState.projectGeneration = generation;
    setProjectGeneration(generation);
  }
  if (generation > appState.projectGeneration) {
    clearBrowserProjectMemory(generation);
    motionTest.resetProjectState();
    motionData.resetProjectState();
    midiMonitor.resetProjectState();
    motionStudio.resetProjectState();
    motorEventLog.resetProjectState();
    servoAlarm?.resetProjectState();
    Promise.resolve().then(async () => {
      await projectExplorer.refresh(true);
      await motorConfig.loadProjectRegistry();
      await motionData.fetchFiles();
      await motionStudio.refresh(false);
      await servoAlarm?.refresh();
      updateWorkContext();
    }).catch(() => {});
  }
  return generation === appState.projectGeneration;
}

function enforceEmergencyUi() {
  const latched = Boolean(appState.emergencyLatched);
  document.body.classList.toggle('emergency-latched', latched);
  el.emergencyStopBanner?.classList.toggle('hidden', !latched);
  if (!latched) {
    document.querySelectorAll('button[data-emergency-forced-disabled]').forEach((button) => {
      if (button.dataset.emergencyPreviousDisabled === 'false') button.disabled = false;
      delete button.dataset.emergencyForcedDisabled;
      delete button.dataset.emergencyPreviousDisabled;
    });
    return;
  }
  document.querySelectorAll('button').forEach((button) => {
    if (button === el.programRestartButton || button === el.headerProgramRestartButton) return;
    if (!button.dataset.emergencyForcedDisabled) {
      button.dataset.emergencyPreviousDisabled = String(button.disabled);
      button.dataset.emergencyForcedDisabled = 'true';
    }
    button.disabled = true;
  });
}

function motionStateFromPayload(payload) {
  if (!payload?.motion_state) return null;
  return {
    ...payload.motion_state,
    motion_test_limits: payload.motion_test_limits || payload.motion_state.motion_test_limits || {},
    motion_run_status: payload.motion_run_status || payload.motion_state.motion_run_status || {},
    execution_context: payload.execution_context || {},
    service_management: payload.service_management || {},
    safety_status: payload.safety_status || {},
    motion_state_age_sec: payload.motion_state_age_sec,
    project_scope: payload.project_scope || payload.motion_state.project_scope || {},
  };
}

function renderServiceManagement(payload) {
  const incomingBridgeInstanceId = String(payload?.bridge_instance_id || '');
  if (incomingBridgeInstanceId) appState.bridgeInstanceId = incomingBridgeInstanceId;
  const managed = Boolean(payload?.service_management?.managed);
  const motorManaged = Boolean(payload?.service_management?.motor_managed);
  const motorConfigApplied = Boolean(payload?.project_scope?.motor_config_applied);
  if (el.serviceMode) {
    el.serviceMode.textContent = managed
      ? (motorManaged ? '분리 자동 실행 · 자동 복구' : '분리 서비스 설치 필요')
      : '수동 실행';
    el.serviceMode.classList.toggle('warning-text', !managed || !motorManaged);
  }
  if (el.programRestartButton) el.programRestartButton.disabled = !(managed && motorManaged);
  if (el.motorControlRestartButton) {
    el.motorControlRestartButton.disabled = !(motorManaged && motorConfigApplied);
    el.motorControlRestartButton.title = motorConfigApplied
      ? '현재 프로젝트의 모터 제어 서비스를 재시작합니다'
      : '현재 프로젝트의 모터축 설정을 먼저 적용하세요';
  }
  if (el.headerProgramRestartButton) {
    el.headerProgramRestartButton.disabled = !(managed && motorManaged);
  }
  appState.emergencyLatched = Boolean(payload?.safety_status?.emergency_latched);
  appState.executionContext = payload?.execution_context || null;
  const contextReady = Boolean(appState.executionContext?.ready);
  const contextText = contextReady ? '저장 = 실행' : (
    appState.executionContext?.state === 'motor_apply_required'
      ? '모터 설정 적용 필요'
      : appState.executionContext?.state === 'configuration_required'
        ? '설정 파일 필요'
        : '적용 대기'
  );
  if (el.headerContextState) {
    el.headerContextState.textContent = contextText;
    el.headerContextState.title = appState.executionContext?.message || contextText;
    el.headerContextState.classList.toggle('ready', contextReady);
    el.headerContextState.classList.toggle('waiting', !contextReady);
  }
  if (el.executionContextState) {
    el.executionContextState.textContent = contextText;
    el.executionContextState.title = appState.executionContext?.message || contextText;
    el.executionContextState.classList.toggle('status-ok', contextReady);
    el.executionContextState.classList.toggle('warning-text', !contextReady);
  }
  if (appState.emergencyLatched && el.summaryText) {
    el.summaryText.textContent = '긴급정지 잠김 · 프로그램 재시작 필요';
  }
  if (el.programHealthDetail) {
    const age = Number(payload?.motion_state_age_sec);
    const stateText = Number.isFinite(age) && age <= 1.0
      ? '모터 상태 수신 정상'
      : '모터 상태 수신 확인 필요';
    el.programHealthDetail.textContent = managed
      ? (
        motorManaged
          ? `분리 자동실행 정상 · ${stateText}`
          : `상위 서비스만 설치됨 · ${stateText} · 최초 서비스 설치 다시 실행 필요`
      )
      : `수동 실행 중 · ${stateText} · 최초 서비스 설치 필요`;
    el.programHealthDetail.classList.toggle(
      'warning-text',
      !managed || !motorManaged || !(Number.isFinite(age) && age <= 1.0),
    );
  }
  enforceEmergencyUi();
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
    .map((motor) => `축 ${motor.controller_index}`)
    .join(', ');
}

function setRestartOverlay(visible, title = '', message = '', detail = '') {
  if (!visible) {
    operationProgress.close({ force: true });
    return;
  }
  const id = `restart:${appState.restartCheckMode || 'system'}`;
  if (operationProgress.activeId() !== id) {
    operationProgress.begin({
      id,
      title,
      message,
      detail,
      phase: '진행 중',
      mode: 'standard',
      cancelable: true,
      onCancel: cancelRestartCompletionCheck,
    });
    return;
  }
  operationProgress.update({ title, message, detail, phase: '진행 중' });
}

function finishRestartOverlay({
  outcome = 'success',
  title,
  message,
  detail,
} = {}) {
  operationProgress.finish({ outcome, title, message, detail });
}

function stopRestartProgressPolling() {
  if (appState.restartProgressTimer !== null) {
    window.clearTimeout(appState.restartProgressTimer);
    appState.restartProgressTimer = null;
  }
}

function clearRestartTracking() {
  stopRestartProgressPolling();
  appState.configApplyInProgress = false;
  appState.configApplyStartedAtMs = null;
  appState.configApplyReadySinceMs = null;
  appState.configApplyConnectionInterrupted = false;
  appState.restartCheckMode = '';
  appState.restartPreviousBridgeInstanceId = '';
  appState.restartPreviousMotorStatusAt = null;
  appState.restartRequestAccepted = false;
}

function cancelRestartCompletionCheck() {
  if (!appState.configApplyInProgress) return;
  clearRestartTracking();
  if (el.bridgeState) el.bridgeState.textContent = '재시작 확인 중단';
  if (el.summaryText) {
    el.summaryText.textContent = '완료 확인만 중단했습니다. 이미 요청된 서비스 재시작은 취소되지 않습니다.';
  }
}

function startRestartProgressPolling() {
  stopRestartProgressPolling();
  const poll = async () => {
    if (!appState.configApplyInProgress) {
      stopRestartProgressPolling();
      return;
    }
    await fetchStatus();
    if (appState.configApplyInProgress) {
      appState.restartProgressTimer = window.setTimeout(poll, 1500);
    }
  };
  appState.restartProgressTimer = window.setTimeout(poll, 1500);
}

function setStatusCheckPopup({
  visible,
  running = false,
  title = '',
  message = '',
  detail = '',
} = {}) {
  if (!visible) {
    operationProgress.close({ force: true });
    return;
  }
  const id = title.includes('모터') ? 'status:motor' : 'status:program';
  if (running) {
    operationProgress.begin({
      id,
      title,
      message,
      detail,
      phase: '확인 중',
      mode: 'compact',
    });
    return;
  }
  operationProgress.finish({
    outcome: title.includes('실패')
      ? 'failure'
      : title.includes('취소')
        ? 'cancelled'
        : message.includes('지연')
          ? 'partial'
          : 'success',
    title,
    message,
    detail,
  });
}

function formatStatusHex(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return '-';
  return `0x${(number & 0xFFFF).toString(16).toUpperCase().padStart(4, '0')}`;
}

function motorErrorDetected(motor) {
  const rawErrorCode = Number(motor?.errorcode_raw);
  const communicationUnavailable = rawErrorCode === 0xFFFF
    || (
      String(motor?.state || '') === 'disconnected'
      && /communication unavailable/i.test(String(motor?.error_text || ''))
    );
  if (communicationUnavailable) return false;
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
  const type = motor?.motor_type_label || motor?.motor_type || '모터';
  const name = motor?.display_name || motor?.driver_name || motor?.driver_model || '';
  return `축 ${axis} · ${type}${name ? ` · ${name}` : ''}`;
}

function motorErrorDetailRows(motor) {
  const alarm = servoAlarm?.entryForCode(motor?.errorcode);
  const rows = [
    ['상태', motor?.status_text || motor?.state || '-'],
    ['오류', motor?.error_text || (Number(motor?.errorcode || 0) === 0 ? '고장 상태' : `오류 ${motor.errorcode}`)],
    ['상태워드', formatStatusHex(motor?.statusword)],
  ];
  const errorCode = motor?.errorcode_raw ?? motor?.errorcode;
  if (errorCode !== null && errorCode !== undefined) {
    rows.push(['오류 코드', formatStatusHex(errorCode)]);
  }
  if (alarm) {
    rows.push(['에러 내용', `${alarm.code_label} · ${alarm.name}`]);
    rows.push(['적용 등급', `${alarm.effective_grade || alarm.default_grade}등급 · ${alarm.action}`]);
    rows.push(['사용자 대처', alarm.guidance]);
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
  const restartMode = appState.restartCheckMode;
  const elapsedMs = appState.configApplyStartedAtMs
    ? Date.now() - appState.configApplyStartedAtMs
    : 0;
  if (elapsedMs >= RESTART_TIMEOUT_MS) {
    return {
      ready: false,
      failed: true,
      title: '재시작 확인 시간 초과',
      detail: `${(elapsedMs / 1000).toFixed(1)}초 동안 완료 조건을 확인하지 못했습니다.`,
    };
  }
  const requiresBridgeRestart = restartMode === 'program' || restartMode === 'motor_apply';
  const incomingBridgeInstanceId = String(payload?.bridge_instance_id || '');
  const bridgeInstanceChanged = Boolean(
    appState.restartPreviousBridgeInstanceId
    && incomingBridgeInstanceId
    && incomingBridgeInstanceId !== appState.restartPreviousBridgeInstanceId
  );
  if (
    appState.configApplyInProgress
    && requiresBridgeRestart
    && !appState.configApplyConnectionInterrupted
    && !bridgeInstanceChanged
  ) {
    return {
      ready: false,
      title: '설정 적용·재시작 시작 대기',
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
  if (restartMode === 'motor_control' && !appState.restartRequestAccepted) {
    return {
      ready: false,
      title: '모터 제어 재시작 요청 중',
      detail: '재시작 요청 응답 대기',
    };
  }
  if (restartMode === 'program') {
    if (!bridgeInstanceChanged) {
      return {
        ready: false,
        title: '프로그램 재시작 확인 중',
        detail: '새 motion_web_bridge 인스턴스 확인 대기',
      };
    }
    return {
      ready: true,
      title: '프로그램 재시작 완료',
      detail: '웹·Supervisor·모션 실행·MIDI 재연결 확인 · 모터 제어 상태는 변경하지 않음',
    };
  }
  const runtime = payload?.service_management?.runtime || {};
  if (runtime.phase === 'motor_manager_start_blocked') {
    return {
      ready: false,
      failed: true,
      title: '모터 관리 노드 시작 차단',
      detail: runtime.message || 'EtherCAT 오류 상태를 먼저 해제해야 합니다.',
    };
  }
  if (runtime.motor_manager_expected === false) {
    return {
      ready: false,
      failed: true,
      title: '모터 관리 노드 미실행',
      detail: `${runtime.message || '모터 실행 설정이 없습니다.'} · ${runtime.runtime_config_file || '-'}`,
    };
  }
  if (!state) {
    return {
      ready: false,
      title: '설정 적용·재시작 중입니다',
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
    const elapsedSec = appState.configApplyStartedAtMs
      ? (Date.now() - appState.configApplyStartedAtMs) / 1000
      : 0;
    if (elapsedSec >= 30) {
      return {
        ready: false,
        failed: true,
        title: '모터 상태 수신 실패',
        detail: `30초 안에 motor_status를 받지 못했습니다 · ${runtime.runtime_config_file || '실행 설정 확인 필요'}`,
      };
    }
    return {
      ready: false,
      title: '모터 상태 수신 대기',
      detail: `경과 ${elapsedSec.toFixed(1)}초 · /motion_control/motor_status 대기`,
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
  if (
    restartMode === 'motor_control'
    && appState.restartPreviousMotorStatusAt !== null
    && Number.isFinite(Number(appState.restartPreviousMotorStatusAt))
    && lastMotorStatusAt <= Number(appState.restartPreviousMotorStatusAt)
  ) {
    return {
      ready: false,
      title: '모터 제어 재시작 확인 중',
      detail: '재시작 이후의 새로운 motor_status 수신 대기',
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
  if (restartMode === 'motor_control') {
    return {
      ready: true,
      title: connectedMotors.length > 0
        ? '모터 제어 재시작 완료'
        : '모터 제어 재시작 완료 · 모터 미연결',
      detail: [
        `런타임 보고 ${motors.length}축`,
        `온라인 ${connectedMotors.length}축`,
        `미연결 ${disconnectedMotors.length}축`,
        `오류 ${faultMotors.length}축`,
      ].join(' · '),
    };
  }
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
    title: '설정 적용·재시작 완료',
    detail: [
      `런타임 온라인 ${connectedMotors.length}축`,
      discovery.hasDirectScan ? `버스 검색 감지 ${Number(discovery.discoveredCount || 0)}축` : '',
      discovery.ethercatScanned ? `EtherCAT ${discovery.ethercatCount}축` : '',
      discovery.dynamixelScanned ? `다이나믹셀 ${discovery.dynamixelCount}축` : '',
      `미연결 ${disconnectedMotors.length}축`,
      `오류 ${faultMotors.length}축`,
    ].filter(Boolean).join(' · '),
  };
}

function updateRestartProgress(payload = null) {
  if (!appState.configApplyInProgress) return;
  const programRestart = appState.restartCheckMode === 'program';
  const motorControlRestart = appState.restartCheckMode === 'motor_control';
  const state = restartReadyState(payload);
  const message = programRestart
    ? '웹·Supervisor·모션 실행·MIDI가 다시 연결됐는지 확인하는 중입니다.'
    : motorControlRestart
      ? 'Motor Manager 실행과 재시작 이후의 새로운 모터 상태 수신을 확인하는 중입니다.'
    : [
      'motor_manager_node, motion_state_monitor, motion_supervisor, motion_web_bridge 상태를 확인하는 중입니다.',
      'YAML 등록 수가 아니라 직접 검색되거나 실제 감지된 모터를 기준으로 확인합니다.',
    ].join(' ');

  if (state.failed) {
    finishRestartOverlay({
      outcome: state.title.includes('시간 초과') ? 'timeout' : 'failure',
      title: state.title,
      message: '재시작 완료 조건을 확인하지 못했습니다.',
      detail: state.detail,
    });
    clearRestartTracking();
    if (el.bridgeState) el.bridgeState.textContent = state.title;
    if (el.summaryText) el.summaryText.textContent = state.detail;
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
    setRestartOverlay(
      true,
      programRestart
        ? '프로그램 재시작 완료 확인 중'
        : motorControlRestart
          ? '모터 제어 재시작 완료 확인 중'
          : '설정 적용·재시작 완료 확인 중',
      message,
      state.detail,
    );
    return;
  }

  if (Date.now() - appState.configApplyReadySinceMs < RESTART_READY_STABLE_MS) {
    setRestartOverlay(
      true,
      programRestart
        ? '프로그램 재시작 완료 확인 중'
        : motorControlRestart
          ? '모터 제어 재시작 완료 확인 중'
          : '설정 적용·재시작 완료 확인 중',
      message,
      state.detail,
    );
    return;
  }

  clearRestartTracking();
  finishRestartOverlay({
    outcome: 'success',
    title: state.title,
    message: programRestart
      ? '프로그램 재시작과 웹 자동 재연결을 확인했습니다.'
      : motorControlRestart
        ? 'Motor Manager 실행과 새로운 모터 상태 수신을 확인했습니다.'
        : '설정 적용·재시작과 모터 상태 수신을 확인했습니다.',
    detail: state.detail,
  });
  if (el.bridgeState) el.bridgeState.textContent = '연결됨';
  if (el.summaryText) {
    el.summaryText.textContent = programRestart
      ? '프로그램 재시작 완료'
      : motorControlRestart
        ? '모터 제어 재시작 완료'
      : '설정 적용·재시작 완료';
  }
}

let motionTest = null;
let servoAlarm = null;

const motorConfig = createMotorConfigController({
  el,
  operationProgress,
  getRawMode: () => appState.rawMode,
  getLatestState: () => appState.latestState,
  renderLatestState,
  onWorkContextChange: updateWorkContext,
  onProjectFilesChange: () => projectExplorer.refresh(true),
  onConfigApplyStart: () => {
    appState.configApplyInProgress = true;
    appState.configApplyStartedAtMs = Date.now();
    appState.configApplyReadySinceMs = null;
    appState.configApplyConnectionInterrupted = false;
    appState.restartCheckMode = 'motor_apply';
    appState.restartPreviousBridgeInstanceId = appState.bridgeInstanceId;
    appState.restartPreviousMotorStatusAt = null;
    appState.restartRequestAccepted = false;
    if (el.bridgeState) el.bridgeState.textContent = '설정 반영 중';
    if (el.summaryText) el.summaryText.textContent = '설정 반영 중입니다. 웹 연결이 자동으로 다시 연결됩니다.';
    setRestartOverlay(
      true,
      '설정 적용·재시작 중입니다',
      'motor_manager_node, motion_state_monitor, motion_supervisor, motion_web_bridge가 다시 시작될 때까지 기다려 주세요.',
      '재시작 요청 전송 중',
    );
    startRestartProgressPolling();
  },
  onConfigApplyComplete: () => {
    clearRestartTracking();
    setRestartOverlay(false);
  },
  onIdentityStatusChange: (message) => {
    appState.motorIdentityBlockMessage = String(message || '');
  },
  onAcServoControl: (action, axis) => motionTest?.controlAcServo(action, axis),
});

motionTest = createMotionTestController({
  el,
  getLatestState: () => appState.latestState,
});

const motionData = createMotionDataController({
  el,
  getLatestState: () => appState.latestState,
  getConfiguredMotors: () => motorConfig.getConfiguredMotors(),
  onWorkContextChange: updateWorkContext,
  onProjectFilesChange: () => projectExplorer.refresh(true),
});

const midiMonitor = createMidiMonitorController({
  el,
  onMappingFileSaved: (file) => motionData.syncMappingFileRevision(file),
});
const motionStudio = createMotionStudioController({
  el,
  getMotorActionBlockReason: studioMotorActionBlockReason,
  getConfiguredMotors: () => motorConfig.getConfiguredMotors(),
});
const projectExplorer = createProjectExplorerController({
  el,
  onOpenEditor: async (result, requestedWorkspace = '') => {
    const targetRoute = requestedWorkspace || workspaceForProjectCategory(
      result.category,
      result.workspace || 'system',
      result.motion_tab,
    );
    const target = setActiveWorkspace(targetRoute);
    if (!target) return;
    if (target === 'config') motorConfig.fetchRegistry();
    if (['motions', 'motion_axis_matching'].includes(result.category)) {
      await motionData.openProjectFile(result.category, result.file_name);
    }
    if (target === 'studio') await motionStudio.refresh(false);
  },
  onManageFile: () => {
    setActiveWorkspace('system');
    document.getElementById('projectFileManager')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  },
  onAddMotionLayer: async (fileName) => {
    setActiveWorkspace('studio');
    await motionStudio.refresh(false);
    await motionStudio.addMotionFile(fileName);
  },
  onProjectChange: async (project, projectGeneration) => {
    clearBrowserProjectMemory(projectGeneration);
    motionTest.resetProjectState();
    motionData.resetProjectState();
    midiMonitor.resetProjectState();
    motionStudio.resetProjectState();
    motorEventLog.resetProjectState();
    servoAlarm?.resetProjectState();
    await motorConfig.loadProjectRegistry();
    await motionData.fetchFiles();
    await motionStudio.refresh(false);
    await servoAlarm?.refresh();
    updateWorkContext();
  },
  onNavigate: (workspace, motionTab) => {
    setActiveWorkspace(workspace || 'monitoring', motionTab);
  },
});

const motorEventLog = createMotorEventLogController({ el });
servoAlarm = createServoAlarmController({
  el,
  getLatestState: () => appState.latestState,
});

function statusCheckResult(triggerButton, payload) {
  if (triggerButton === el.programStatusRefreshButton) {
    const management = payload?.service_management || {};
    return {
      title: '프로그램 상태 확인 완료',
      message: '서비스 상태 응답을 받았습니다.',
      detail: [
        `상위 프로그램 ${management.managed ? '관리 중' : '확인 필요'}`,
        `모터 제어 ${management.motor_managed ? '관리 중' : '확인 필요'}`,
      ].join(' · '),
    };
  }
  const state = motionStateFromPayload(payload);
  const motors = Array.isArray(state?.motors) ? state.motors : [];
  const online = motors.filter((motor) => {
    const stateText = String(
      motor.connection_state || (motor.state === 'detected' ? 'online' : motor.state),
    );
    return stateText === 'online';
  }).length;
  const faults = motors.filter((motor) => motorErrorDetected(motor)).length;
  const generatedAt = Number(state?.generated_at);
  const receivedAt = Number(state?.last_motor_status_at);
  const age = Number.isFinite(generatedAt) && Number.isFinite(receivedAt)
    ? Math.max(generatedAt - receivedAt, 0)
    : null;
  return {
    title: '모터 상태 확인 완료',
    message: age !== null && age <= 1.5
      ? '최신 모터 상태를 확인했습니다.'
      : '모터 상태 수신이 없거나 지연되고 있습니다.',
    detail: [
      `런타임 보고 ${motors.length}축`,
      `온라인 ${online}축`,
      `오류 ${faults}축`,
      age === null ? '수신 시각 없음' : `수신 지연 ${age.toFixed(2)}초`,
    ].join(' · '),
  };
}

async function fetchStatus(triggerButton = el.refreshButton) {
  if (!triggerButton) return;
  const showStatusPopup = (
    triggerButton === el.programStatusRefreshButton
    || triggerButton === el.motorStatusRefreshButton
  );
  if (showStatusPopup) {
    const motorStatus = triggerButton === el.motorStatusRefreshButton;
    setStatusCheckPopup({
      visible: true,
      running: true,
      title: motorStatus ? '모터 상태 확인 중' : '프로그램 상태 확인 중',
      message: motorStatus
        ? '최신 모터 상태와 수신 시각을 확인하고 있습니다.'
        : '서비스 상태 응답을 기다리고 있습니다.',
      detail: '응답 대기',
    });
  }
  triggerButton.disabled = true;
  const originalText = triggerButton.textContent;
  triggerButton.textContent = '확인 중';
  try {
    const payload = await fetchStatusSnapshot();
    if (!acceptProjectPayload(payload)) {
      if (showStatusPopup) {
        setStatusCheckPopup({
          visible: true,
          running: false,
          title: '상태 확인 취소',
          message: '프로젝트가 변경되어 이전 요청 결과를 적용하지 않았습니다.',
          detail: '현재 프로젝트에서 다시 확인하세요.',
        });
      }
      return;
    }
    if (el.bridgeState) {
      el.bridgeState.textContent = payload.bridge_state === 'ok'
        ? '정상'
        : (payload.bridge_state || 'HTTP 연결');
    }
    renderServiceManagement(payload);
    renderAccess(payload, el);
    midiMonitor.renderSnapshot(payload.midi_monitor || {});
    motionStudio.renderSnapshot(payload.motion_studio || {}, payload.midi_monitor || {});
    renderLatestState(motionStateFromPayload(payload));
    updateRestartProgress(payload);
    triggerButton.textContent = `확인 완료 ${new Date().toLocaleTimeString()}`;
    if (showStatusPopup) {
      setStatusCheckPopup({
        visible: true,
        running: false,
        ...statusCheckResult(triggerButton, payload),
      });
    }
  } catch (error) {
    if (Number.isInteger(Number(error?.projectBoundaryGeneration))) {
      acceptProjectPayload({
        project_generation: Number(error.projectBoundaryGeneration),
      });
    }
    if (el.bridgeState) el.bridgeState.textContent = 'HTTP 오류';
    triggerButton.textContent = '확인 실패';
    updateRestartProgress(null);
    if (showStatusPopup) {
      setStatusCheckPopup({
        visible: true,
        running: false,
        title: '상태 확인 실패',
        message: '상태 응답을 받지 못했습니다.',
        detail: error?.message || String(error),
      });
    }
  } finally {
    setTimeout(() => {
      triggerButton.textContent = originalText;
      triggerButton.disabled = false;
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
        el.bridgeState.textContent = appState.configApplyInProgress ? '재연결 완료, 상태 확인 중' : '연결됨';
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
      if (!acceptProjectPayload(payload)) return;
      renderServiceManagement(payload);
      renderAccess(payload, el);
      midiMonitor.renderSnapshot(payload.midi_monitor || {});
      motionStudio.renderSnapshot(payload.motion_studio || {}, payload.midi_monitor || {});
      renderLatestState(motionStateFromPayload(payload));
      updateRestartProgress(payload);
    },
    onClose: () => {
      if (appState.configApplyInProgress) {
        appState.configApplyConnectionInterrupted = true;
        appState.configApplyReadySinceMs = null;
      }
      if (el.bridgeState) {
        el.bridgeState.textContent = appState.configApplyInProgress ? '설정 반영 대기' : '재연결 중';
      }
      if (appState.configApplyInProgress) {
        setRestartOverlay(
          true,
          '웹 재연결 대기',
          '설정 적용·재시작으로 웹 연결이 잠시 끊겼습니다. 자동 재연결을 기다리는 중입니다.',
          'motion_web_bridge 재시작 대기',
        );
      }
    },
    onError: () => {
      if (el.bridgeState) {
        el.bridgeState.textContent = appState.configApplyInProgress ? '설정 반영 대기' : '오류';
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

if (el.programStatusRefreshButton) {
  el.programStatusRefreshButton.addEventListener('click', () => {
    fetchStatus(el.programStatusRefreshButton);
  });
}

if (el.motorStatusRefreshButton) {
  el.motorStatusRefreshButton.addEventListener('click', () => {
    fetchStatus(el.motorStatusRefreshButton);
  });
}

if (el.programPageReloadButton) {
  el.programPageReloadButton.addEventListener('click', () => {
    window.location.reload();
  });
}

if (el.programRestartButton) {
  el.programRestartButton.addEventListener('click', async () => {
    const confirmed = await appDialogs.confirm(
      '프로그램을 재시작합니다.\n\n'
      + 'Motor Manager와 EtherCAT은 계속 실행되며 현재 서보 ON/OFF 상태를 유지합니다.\n'
      + '모션 녹화·재생이 정지된 상태인지 확인했습니까?',
      { title: '프로그램 재시작', confirmLabel: '재시작', tone: 'warning' },
    );
    if (!confirmed) return;
    appState.configApplyInProgress = true;
    appState.configApplyStartedAtMs = Date.now();
    appState.configApplyReadySinceMs = null;
    appState.configApplyConnectionInterrupted = false;
    appState.restartCheckMode = 'program';
    appState.restartPreviousBridgeInstanceId = appState.bridgeInstanceId;
    appState.restartPreviousMotorStatusAt = null;
    appState.restartRequestAccepted = false;
    setRestartOverlay(
      true,
      '프로그램 재시작 중입니다',
      '웹·Supervisor·모션 실행·MIDI가 다시 실행되고 웹도 자동으로 연결됩니다.',
      '재시작 요청 전송 중',
    );
    startRestartProgressPolling();
    try {
      const payload = await restartManagedProgram();
      if (payload?.success === false) throw new Error(payload.message || '재시작 요청 실패');
    } catch (error) {
      clearRestartTracking();
      setRestartOverlay(false);
      window.alert(error?.message || String(error));
    }
  });
}

if (el.motorControlRestartButton) {
  el.motorControlRestartButton.addEventListener('click', async () => {
    const confirmed = await appDialogs.confirm(
      '모터 제어 시스템을 재시작합니다.\n\n'
      + 'Motor Manager와 EtherCAT 통신이 중단되며 AC Servo가 OFF됐다가 자동 ON될 수 있습니다.\n'
      + '모든 모션이 정지됐고 장비가 안전한지 확인했습니까?',
      { title: '모터 제어 재시작', confirmLabel: '재시작', tone: 'danger' },
    );
    if (!confirmed) return;
    appState.configApplyInProgress = true;
    appState.configApplyStartedAtMs = Date.now();
    appState.configApplyReadySinceMs = null;
    appState.configApplyConnectionInterrupted = false;
    appState.restartCheckMode = 'motor_control';
    appState.restartPreviousBridgeInstanceId = '';
    appState.restartPreviousMotorStatusAt = Number(appState.latestState?.last_motor_status_at) || null;
    appState.restartRequestAccepted = false;
    setRestartOverlay(
      true,
      '모터 제어 재시작 중입니다',
      'Motor Manager 실행과 새로운 모터 상태 수신을 확인합니다.',
      '재시작 요청 전송 중',
    );
    startRestartProgressPolling();
    el.motorControlRestartButton.disabled = true;
    const originalText = el.motorControlRestartButton.textContent;
    el.motorControlRestartButton.textContent = '모터 제어 재시작 중';
    try {
      const payload = await restartMotorControlSystem();
      if (payload?.success === false) throw new Error(payload.message || '재시작 요청 실패');
      appState.restartPreviousMotorStatusAt = Number(appState.latestState?.last_motor_status_at) || null;
      appState.restartRequestAccepted = true;
      window.setTimeout(() => fetchStatus(), 1500);
    } catch (error) {
      clearRestartTracking();
      setRestartOverlay(false);
      window.alert(error?.message || String(error));
    } finally {
      el.motorControlRestartButton.textContent = originalText;
      window.setTimeout(() => fetchStatus(), 3000);
    }
  });
}

if (el.headerProgramRestartButton) {
  el.headerProgramRestartButton.addEventListener('click', () => {
    if (!el.programRestartButton || el.programRestartButton.disabled) {
      window.alert('프로그램 재시작을 사용할 수 없습니다. 시스템 정보에서 프로그램 상태를 확인하세요.');
      return;
    }
    el.programRestartButton.click();
  });
}

async function runSafetyStop(emergency) {
  const button = emergency ? el.headerEmergencyStopButton : el.headerMotionStopButton;
  if (!button || button.disabled) return;
  const originalText = button.textContent;
  button.disabled = true;
  button.textContent = emergency ? '긴급정지 요청 중' : '동작 정지 중';
  if (el.summaryText) {
    el.summaryText.textContent = emergency ? '긴급정지 요청 중' : '전체 동작 정지 요청 중';
  }

  try {
    const stopCommandSources = async () => {
      const cleanup = [
        ['모션 동작', stopMotionRun()],
        ['모션 스튜디오', stopMotionStudio()],
      ];
      const results = await Promise.allSettled(cleanup.map(([, request]) => request));
      return results.flatMap((result, index) => {
        const label = cleanup[index][0];
        if (result.status === 'rejected') return [`${label}: ${result.reason?.message || result.reason}`];
        if (result.value?.success === false) return [`${label}: ${result.value.message || '정리 실패'}`];
        return [];
      });
    };

    // 두 정지 모두 최종 모터 출력 정지를 가장 먼저 요청한다. 응답을 확인하지
    // 못해도 이미 전달됐을 수 있으므로 아래 명령 생성원 정리는 반드시 수행한다.
    let safetyFailure = '';
    try {
      const safetyResult = emergency
        ? await requestEmergencySafetyStop()
        : await requestMotionSafetyStop();
      if (safetyResult?.success === false) {
        safetyFailure = safetyResult.message || '안전 정지 요청 실패';
      }
    } catch (error) {
      safetyFailure = error?.message || String(error);
    }

    if (emergency) {
      appState.emergencyLatched = true;
      enforceEmergencyUi();
    }
    const failures = await stopCommandSources();
    if (safetyFailure) failures.unshift(`최종 모터 출력: ${safetyFailure}`);
    if (el.summaryText) {
      el.summaryText.textContent = emergency
        ? '긴급정지 잠김 · 프로그램 재시작 필요'
        : failures.length ? '전체 동작 정지 · 일부 상태 확인 필요' : '전체 동작 정지 완료';
    }
    if (failures.length) {
      window.alert(`정지 요청 후 다음 상태를 확인해야 합니다.\n\n${failures.join('\n')}`);
    }
  } catch (error) {
    if (el.summaryText) el.summaryText.textContent = '안전 정지 실패 · 즉시 장비 상태 확인';
    window.alert(error?.message || String(error));
  } finally {
    button.textContent = originalText;
    if (!emergency || !appState.emergencyLatched) button.disabled = false;
  }
}

el.headerMotionStopButton?.addEventListener('click', () => runSafetyStop(false));
el.headerEmergencyStopButton?.addEventListener('click', () => runSafetyStop(true));

window.addEventListener('keydown', (event) => {
  if (!(event.ctrlKey && event.shiftKey && event.code === 'KeyE')) return;
  event.preventDefault();
  if (!appState.emergencyLatched) runSafetyStop(true);
});

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

if (el.monitoringDetailTabs) {
  el.monitoringDetailTabs.addEventListener('click', (event) => {
    const button = event.target.closest('button[data-monitoring-detail-tab]');
    if (!button) return;
    appState.activeMonitoringDetailTab = button.dataset.monitoringDetailTab || 'basic';
    renderLatestState();
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
    const groupButton = event.target.closest('button[data-workspace-group]');
    if (groupButton) {
      const group = groupButton.dataset.workspaceGroup || 'operations';
      if (workspaceGroupFor(workspaceRouteState.current()) === group) return;
      const target = setActiveWorkspace(
        workspaceRouteState.forGroup(group) || defaultWorkspaceForGroup(group),
      );
      if (!target) return;
      renderLatestState();
      if (target === 'log') motorEventLog.activate();
      if (target === 'studio') motionStudio.refresh(false);
      if (target === 'config') motorConfig.fetchRegistry();
      if (target === 'servo-errors') servoAlarm.refresh();
      projectExplorer.refresh(true);
      return;
    }
    const button = event.target.closest('button[data-workspace-tab]');
    if (!button) return;
    const target = setActiveWorkspace(button.dataset.workspaceTab || 'monitoring');
    if (!target) return;
    renderLatestState();
    if (target === 'log') motorEventLog.activate();
    if (target === 'studio') motionStudio.refresh(false);
    if (target === 'config') motorConfig.fetchRegistry();
    if (target === 'servo-errors') servoAlarm.refresh();
    projectExplorer.refresh(true);
  });
}

motorConfig.bindEvents();
motionTest.bindEvents();
motionData.bindEvents();
motionStudio.bindEvents();
projectExplorer.bindEvents();
motorEventLog.bindEvents();
servoAlarm.bindEvents();
motorConfig.renderRegistrationTabs();
renderWorkspacePanel();
updateWorkContext();
connectSocket();
fetchStatus();
motorConfig.fetchRegistry();
motionData.fetchFiles();
motionStudio.refresh(false);
projectExplorer.refresh(true);
servoAlarm.refresh();
