import {
  applyMotorConfig,
  fetchMotorConfig,
  requestMotorScan,
  requestAcServoScan,
  requestDynamixelScan,
  fetchMotorScanProgress,
  writeEthercatAlias,
  saveMotorConfig,
  deleteMotorConfig,
} from './api.js?v=20260722-motor-config-delete';
import {
  clone,
  displayText,
  escapeHtml,
  formatInt,
  stateLabel,
} from './format.js?v=20260718-korean-ui';
import {
  activeRegistryMotors as selectActiveRegistryMotors,
  activeVisibleRegistryMotors as selectActiveVisibleRegistryMotors,
  hasRegistryChanges,
  normalizeMotor,
  normalizeRegistry,
  registryMotorById as selectRegistryMotorById,
  registryMotorLabel,
  upsertMotorInRegistry,
} from './motor_registry.js?v=20260718-korean-ui';
import {
  detectedScanRow,
  runtimeIsAcServo,
  runtimeMotorConfirmsRegistryMotor,
  scanKey,
  scanRowMatchesRegistryMotor,
  scanRowMatchesRuntimeMotor,
  scanRowSharesConfiguredPosition,
  scanRowToMotor as acServoScanRowToMotor,
  verifiedAcServoModel,
} from './motor_type_ac_servo.js?v=20260722-runtime-axis-confirm';
import {
  dynamixelScanDeviceKey,
  dynamixelScanDeviceToMotor as buildDynamixelScanDeviceToMotor,
  firstDefined,
  modelTextFromDevice,
  runtimeIsDynamixel,
} from './motor_type_dynamixel.js';
import { showConfirm, showPrompt } from './ui_dialogs.js?v=20260727-popup-common-3';

export function isEditableMotorConfigPath(pathValue) {
  const path = String(pathValue || '');
  const item = path.split('.').pop() || '';
  if (/^masters\[\d+\]\.slaves\[\d+\]\./.test(path)) {
    return ['controller_index', 'name'].includes(item);
  }
  if (/^drivers\[\d+\]\./.test(path)) {
    return [
      'lower',
      'upper',
      'speed',
      'acceleration',
      'deceleration',
      'profile_velocity',
      'profile_acceleration',
      'profile_deceleration',
    ].includes(item);
  }
  return false;
}

export function normalizeProjectLoadToken(candidate, currentToken) {
  return Number.isInteger(candidate) ? candidate : currentToken;
}

export function conciseMotorScanMessage(value, maxLength = 180) {
  const text = String(value || '').replace(/\s+/g, ' ').trim();
  if (!text) return '모터 검색 실패';
  if (text.includes('모터 런타임 피드백이 수신 중')) {
    return '검색 중단: 서보가 운전 중이어서 EtherCAT 버스 재검색을 안전하게 실행하지 않았습니다.';
  }
  if (text.includes('Dynamixel 직렬 포트를 찾지 못')) {
    return 'Dynamixel 검색 실패: 연결된 직렬 포트를 찾지 못했습니다.';
  }
  if (text.length <= maxLength) return text;
  return `${text.slice(0, Math.max(1, maxLength - 1)).trimEnd()}…`;
}

export function motorControlConfigurationError(scope, configuredAxisCount) {
  if (Number(configuredAxisCount) <= 0) return '';
  if (scope?.runtime_matches_selected !== true) {
    return '현재 선택 프로젝트와 실행 중인 모터 설정의 프로젝트가 다릅니다.';
  }
  if (scope?.motor_config_applied !== true) {
    return '현재 프로젝트에 저장한 모터축 설정이 실행 시스템에 아직 적용되지 않았습니다. 설정 적용·재시작을 실행하세요.';
  }
  return '';
}

export function motorConfigApplyIdentityBlock(identityError, scanAvailable, aliasWritePending) {
  if (aliasWritePending) return String(identityError || '');
  return scanAvailable ? String(identityError || '') : '';
}

export function motorModelProfileApplyBlock(motors) {
  const axes = (Array.isArray(motors) ? motors : [])
    .filter((motor) => (
      motor &&
      motor.enabled &&
      !motor.deleted &&
      motor.transport === 'ethercat' &&
      (
        ['', 'UNVERIFIED_MINAS'].includes(
          String(motor.profile?.driver_model || '').trim().toUpperCase(),
        ) ||
        motor.profile?.model_confirmed !== true
      )
    ))
    .map((motor) => {
      const axis = Number(motor.config?.controller_index ?? motor.axis);
      return Number.isInteger(axis) ? axis : '?';
    });
  if (axes.length === 0) return '';
  return `실행 적용 불가 · 모델·운전 프로필 미확인 축: ${axes.join(', ')}. `
    + '프로젝트 저장은 가능하지만 모터 실행 설정으로 적용할 수 없습니다.';
}

export function createMotorConfigController({
  el,
  operationProgress,
  getLatestState,
  renderLatestState,
  onWorkContextChange,
  onProjectFilesChange,
  onConfigApplyStart,
  onConfigApplyComplete,
  onIdentityStatusChange,
  onAcServoControl,
}) {
  let activeRegistrationTab = 'ac_servo';
  let latestScan = null;
  let savedRegistry = normalizeAxisRegistry({});
  let axisConfig = normalizeAxisRegistry({});
  let selectedAxisIds = new Set();
  let lastAxisRenderSignature = '';
  let configApplyPending = false;
  let rowEditDrafts = new Map();
  let activeAxisSettingsTab = 'current';
  let motorConfigRawText = '';
  let savedMotorConfigRawText = '';
  let motorConfigFilePath = '';
  let motorConfigRevision = '';
  let motorConfigFileNameDraft = '';
  let configTableDrafts = new Map();
  let selectedConfigMotorId = '';
  let identityUpdatePending = false;
  let pendingAliasWrite = null;
  let lastConfigTableRenderSignature = '';
  let lastConfigRawTextRenderSignature = '';
  let projectLoadToken = 0;
  let scanProgressTimer = null;
  let scanProgressBaselineId = '';
  let scanProgressActiveId = '';
  let scanProgressRenderedCount = 0;
  let scanRequestRunning = false;

  function setScanButtonsDisabled(disabled) {
    [el.scanButton, el.dynamixelScanButton, el.scanAllButton].forEach((button) => {
      if (button) button.disabled = disabled;
    });
  }

  function beginScanRequest(button, runningText) {
    if (!button || scanRequestRunning) return '';
    scanRequestRunning = true;
    const originalText = button.textContent;
    setScanButtonsDisabled(true);
    button.textContent = runningText;
    return originalText;
  }

  function finishScanRequest(button, originalText) {
    window.setTimeout(() => {
      if (button) button.textContent = originalText;
      scanRequestRunning = false;
      setScanButtonsDisabled(false);
    }, 1200);
  }

  function stopScanProgressPolling() {
    if (scanProgressTimer !== null) {
      window.clearInterval(scanProgressTimer);
      scanProgressTimer = null;
    }
  }

  function appendScanProgressLine(message, state = '') {
    const fullMessage = String(message || '');
    const displayMessage = conciseMotorScanMessage(fullMessage);
    const normalizedState = {
      failed: 'failure',
      warning: 'partial',
      done: 'success',
    }[state] || 'running';
    operationProgress?.appendLog(displayMessage, normalizedState);
  }

  async function pollScanProgress() {
    if (!operationProgress?.activeId().startsWith('scan:')) return;
    try {
      const payload = await fetchMotorScanProgress();
      const progress = payload?.progress || {};
      const scanId = String(progress.scan_id || '');
      if (!scanId || (scanId === scanProgressBaselineId && !scanProgressActiveId)) return;
      if (scanProgressActiveId && scanId !== scanProgressActiveId) return;
      if (!scanProgressActiveId) {
        scanProgressActiveId = scanId;
        scanProgressRenderedCount = 0;
        operationProgress.update({ detail: `스캔 ID ${scanId}` });
      }
      const events = Array.isArray(progress.events) ? progress.events : [];
      events.slice(scanProgressRenderedCount).forEach((event) => {
        const phase = String(event.phase || '');
        const lineState = phase === 'failed' || phase.endsWith('_failed')
          ? 'failed'
          : phase === 'partial' || phase === 'dynamixel_unavailable'
            ? 'warning'
            : phase === 'complete' || phase === 'completed' || phase.endsWith('_done')
            ? 'done'
            : 'running';
        appendScanProgressLine(String(event.message || phase || '스캔 진행 중'), lineState);
      });
      scanProgressRenderedCount = events.length;
      operationProgress.update({
        phase: progress.running ? '실시간 스캔 진행 중' : '스캔 종료 확인',
      });
      if (!progress.running && scanProgressActiveId) stopScanProgressPolling();
    } catch (error) {
      if (!error?.staleProjectResponse) {
        appendScanProgressLine(`진행 상태 수신 실패: ${error?.message || error}`, 'failed');
      }
    }
  }

  async function openScanProgressPopup(id, title) {
    stopScanProgressPolling();
    scanProgressBaselineId = '';
    scanProgressActiveId = '';
    scanProgressRenderedCount = 0;
    const started = operationProgress?.begin({
      id,
      title,
      message: '물리 모터 검색을 실행하고 있습니다.',
      detail: '새 스캔 요청 준비',
      phase: '스캔 요청 준비 중',
      mode: 'log',
    });
    if (!started) return false;
    appendScanProgressLine('새 직접 스캔 요청을 전송합니다', 'running');
    try {
      const payload = await fetchMotorScanProgress();
      scanProgressBaselineId = String(payload?.progress?.scan_id || '');
    } catch (_error) {
      scanProgressBaselineId = '';
    }
    scanProgressTimer = window.setInterval(pollScanProgress, 100);
    return true;
  }

  async function finishScanProgressPopup(success, fallbackMessage, outcome = '') {
    await pollScanProgress();
    if (!scanProgressActiveId && fallbackMessage) {
      appendScanProgressLine(fallbackMessage, success ? 'done' : 'failed');
    }
    const partial = outcome === 'partial';
    operationProgress?.finish({
      outcome: success ? 'success' : (partial ? 'partial' : 'failure'),
      title: success ? '모터 검색 완료' : (partial ? '모터 검색 부분 완료' : '모터 검색 실패'),
      message: fallbackMessage,
      detail: scanProgressActiveId ? `스캔 ID ${scanProgressActiveId}` : '스캔 결과 확인',
    });
    stopScanProgressPolling();
  }

  function normalizeAxisRegistry(value) {
    const registry = normalizeRegistry(value || {});
    registry.motors = registry.motors.map((motor) => normalizeMotor({
      ...motor,
      hidden: false,
    }));
    return registry;
  }

  function saveableAxisRegistry(value = axisConfig) {
    const registry = normalizeAxisRegistry(value || {});
    return normalizeRegistry({
      version: registry.version || 1,
      motors: registry.motors
        .filter((motor) => !motor.deleted)
        .map((motor) => normalizeMotor({
          ...motor,
          hidden: false,
          deleted: false,
        })),
    });
  }

  function hasAxisChanges() {
    return hasRegistryChanges(saveableAxisRegistry(savedRegistry), saveableAxisRegistry(axisConfig));
  }

  function hasMotorConfigDataChanges() {
    return String(savedMotorConfigRawText || '') !== String(motorConfigRawText || '');
  }

  function normalizedMotorConfigFileName() {
    return String(motorConfigFileNameDraft || '').trim();
  }

  function hasMotorConfigFileNameChanges() {
    const draft = normalizedMotorConfigFileName();
    return Boolean(draft) && draft !== pathBasename(motorConfigFilePath);
  }

  function hasMotorConfigTableSaveChanges() {
    return hasMotorConfigDataChanges() || hasMotorConfigFileNameChanges();
  }

  function hasAnyConfigChanges() {
    return hasAxisChanges() || hasMotorConfigTableSaveChanges();
  }

  function getWorkContext() {
    return {
      motorConfigFile: motorConfigFilePath,
      motorConfigLoaded: Boolean(motorConfigFilePath),
      motorConfigChanged: hasAnyConfigChanges(),
      motorConfigApplyPending: configApplyPending,
    };
  }

  function setStatusMessage(message) {
    if (el.motorConfigState) el.motorConfigState.textContent = message;
    if (el.configState) el.configState.textContent = message;
    onWorkContextChange?.();
  }

  function setAxisMessage(message, error = false) {
    if (el.axisActionMessage) {
      el.axisActionMessage.textContent = message;
      el.axisActionMessage.classList.toggle('error-text', error);
    }
    if (el.registrySummary) {
      el.registrySummary.textContent = message;
      el.registrySummary.classList.toggle('error-text', error);
    }
  }

  function uiMessage(message, fallback) {
    return String(message || fallback || '')
      .replace(/YAML/gi, '설정 파일')
      .replace(/yaml/gi, '설정 파일');
  }

  function renderRegistrationTabs() {
    if (!el.registrationTabs) return;
    el.registrationTabs.querySelectorAll('[data-registration-tab]').forEach((button) => {
      const active = button.dataset.registrationTab === activeRegistrationTab;
      button.classList.toggle('active', active);
      button.setAttribute('aria-selected', active ? 'true' : 'false');
    });
    if (!el.registrationPanels) return;
    el.registrationPanels.forEach((panel) => {
      panel.classList.toggle('hidden', panel.dataset.registrationPanel !== activeRegistrationTab);
    });
  }

  function renderAxisSettingsTabs() {
    if (el.axisSettingsTabs) {
      el.axisSettingsTabs.querySelectorAll('[data-axis-settings-tab]').forEach((button) => {
        const active = button.dataset.axisSettingsTab === activeAxisSettingsTab;
        button.classList.toggle('active', active);
        button.setAttribute('aria-selected', active ? 'true' : 'false');
      });
    }
    if (!el.axisSettingsPanels) return;
    el.axisSettingsPanels.forEach((panel) => {
      panel.classList.toggle('hidden', panel.dataset.axisSettingsPanel !== activeAxisSettingsTab);
    });
  }

  function axisMotors() {
    return Array.isArray(axisConfig?.motors) ? axisConfig.motors : [];
  }

  function savedMotorById(id) {
    return selectRegistryMotorById(savedRegistry, id);
  }

  function activeAxisMotors() {
    return selectActiveRegistryMotors(axisConfig);
  }

  function activeVisibleAxisMotors() {
    return selectActiveVisibleRegistryMotors(axisConfig);
  }

  function runtimeMotors() {
    const state = getLatestState ? getLatestState() : null;
    if (state?.project_scope?.runtime_matches_selected === false) return [];
    return Array.isArray(state?.motors) ? state.motors : [];
  }

  function selectedMotorConfigAlreadyApplied() {
    const scope = getLatestState?.()?.project_scope || {};
    return scope.runtime_matches_selected === true && scope.motor_config_applied === true;
  }

  function directEthercatScanAvailable() {
    const scan = latestScan?.ethercat_scan;
    return Boolean(scan && !scan.skipped && scan.available && Array.isArray(scan.slaves));
  }

  function acHardwareApplyBlockMessage() {
    return motorConfigApplyIdentityBlock(
      acHardwareIdentityErrorMessage(),
      directEthercatScanAvailable(),
      Boolean(pendingAliasWrite),
    );
  }

  function modelProfileApplyBlockMessage() {
    return motorModelProfileApplyBlock(activeAxisMotors());
  }

  function motionControlBlockMessage() {
    const scope = getLatestState?.()?.project_scope || {};
    const configuredAxisCount = activeAxisMotors().filter((motor) => motor.enabled).length;
    return motorControlConfigurationError(scope, configuredAxisCount)
      || (identityUpdatePending ? '확인한 모터 연결값을 프로젝트에 저장해야 합니다.' : '');
  }

  function motorAxisValue(motor) {
    const axis = Number(motor?.config?.controller_index ?? motor?.axis);
    return Number.isInteger(axis) ? axis : null;
  }

  function assignedAlias(value) {
    if (value === null || value === undefined || value === '') return false;
    const alias = Number(value);
    return Number.isInteger(alias) && alias > 0;
  }

  function rowDraft(row) {
    return rowEditDrafts.get(row.id) || {};
  }

  function setRowDraft(rowId, patch) {
    const next = {
      ...(rowEditDrafts.get(rowId) || {}),
      ...patch,
    };
    rowEditDrafts.set(rowId, next);
  }

  function nextAxisAboveMax(motors = axisMotors()) {
    const axes = motors
      .filter((motor) => !motor.deleted)
      .map(motorAxisValue)
      .filter((axis) => axis !== null);
    if (axes.length === 0) return 0;
    return Math.max(...axes) + 1;
  }

  function createAxisAllocator(motors = axisMotors()) {
    let nextAxis = nextAxisAboveMax(motors);
    return () => {
      const axis = nextAxis;
      nextAxis += 1;
      return axis;
    };
  }

  function nextAvailableAxis() {
    return nextAxisAboveMax();
  }

  function uniqueAxisForMotor(motor) {
    const used = new Set(axisMotors()
      .filter((item) => !item.deleted && item.id !== motor.id)
      .map(motorAxisValue)
      .filter((axis) => axis !== null));
    const proposed = Number(motor.config?.controller_index ?? motor.axis);
    if (Number.isInteger(proposed) && !used.has(proposed)) {
      return proposed;
    }
    return used.size === 0 ? 0 : Math.max(...used) + 1;
  }

  function firstDynamixelDriverId() {
    const motor = axisMotors().find((item) => {
      const driverId = item.config?.driver_id;
      return driverId !== null && driverId !== undefined && driverId !== '';
    });
    return motor ? Number(motor.config.driver_id) : 1;
  }

  function dynamixelScanDeviceToMotor(device, baseMotor = null, nextAxis = nextAvailableAxis) {
    return buildDynamixelScanDeviceToMotor(device, baseMotor, {
      nextAvailableAxis: nextAxis,
      firstDynamixelDriverId,
    });
  }

  function registryMotorMatchesMonitoringMotor(item, motor) {
    const identity = item.identity || {};
    const config = item.config || {};
    if (item.transport === 'ethercat') {
      const configuredAlias = firstDefined(identity.ethercat_alias, config.alias);
      if (
        configuredAlias !== null && configuredAlias !== undefined &&
        motor.alias !== null && motor.alias !== undefined
      ) {
        if (Number(configuredAlias) === 0 && Number(motor.alias) === 0) {
          const controllerIndex = motorAxisValue(item);
          return controllerIndex !== null && controllerIndex !== undefined &&
            motor.controller_index !== null && motor.controller_index !== undefined &&
            Number(controllerIndex) === Number(motor.controller_index);
        }
        return Number(configuredAlias) === Number(motor.alias);
      }
      if (
        Number(identity.rotary_alias) > 0 &&
        Number(motor.station_alias_register) > 0
      ) {
        return Number(identity.rotary_alias) === Number(motor.station_alias_register);
      }
    }
    if (item.transport === 'serial') {
      const configuredId = firstDefined(identity.bus_id, identity.node_id, config.bus_id);
      const runtimeId = firstDefined(motor.bus_id, motor.node_id);
      if (
        configuredId !== null &&
        configuredId !== undefined &&
        runtimeId !== null &&
        runtimeId !== undefined &&
        Number(configuredId) === Number(runtimeId)
      ) {
        return true;
      }
    }
    const controllerIndex = motorAxisValue(item);
    return controllerIndex !== null &&
      controllerIndex !== undefined &&
      motor.controller_index !== null &&
      motor.controller_index !== undefined &&
      Number(controllerIndex) === Number(motor.controller_index);
  }

  function runtimeMotorForRegistryMotor(motor, motors = runtimeMotors()) {
    if (!motor) return null;
    return motors.find((item) => registryMotorMatchesMonitoringMotor(motor, item)) || null;
  }

  function runtimeMotorForScanRow(row, motors = runtimeMotors()) {
    if (!row) return null;
    return motors.find((motor) => scanRowMatchesRuntimeMotor(row, motor)) || null;
  }

  function dynamixelMotorMatchesDevice(motor, device) {
    if (!motor || !device) return false;
    const identity = motor.identity || {};
    const config = motor.config || {};
    const configuredId = firstDefined(identity.bus_id, identity.node_id, config.bus_id);
    if (configuredId === null || configuredId === undefined) return false;
    if (Number(configuredId) !== Number(device.id)) return false;
    const configuredPort = firstDefined(identity.serial_port, config.serial_port);
    if (configuredPort && device.port && String(configuredPort) !== String(device.port)) return false;
    return true;
  }

  function runtimeMotorForDynamixelDevice(device, motors = runtimeMotors()) {
    if (!device) return null;
    return motors.find((motor) => {
      const runtimeId = firstDefined(motor.bus_id, motor.node_id);
      return runtimeId !== null && runtimeId !== undefined && Number(runtimeId) === Number(device.id);
    }) || null;
  }

  function acServoScanRows() {
    const slaves = Array.isArray(latestScan?.ethercat_scan?.slaves)
      ? latestScan.ethercat_scan.slaves
      : [];
    return slaves.filter((row) => detectedScanRow(row)).map((row) => ({
      ...row,
      sii_order_number: row.sii_order_number || row.order_number || '',
      sii_device_name: row.sii_device_name || row.device_name || '',
    }));
  }

  function selectedProjectAcMatchingSummary(slaves) {
    const configured = activeAxisMotors().filter(
      (motor) => motor.transport === 'ethercat' && motor.enabled,
    );
    const matchedIds = new Set();
    let matched = 0;
    slaves.forEach((slave) => {
      const motor = configured.find((item) => scanRowMatchesRegistryMotor(slave, item));
      if (!motor) return;
      matched += 1;
      matchedIds.add(motor.id);
    });
    return {
      matched,
      configured: 0,
      unregistered: Math.max(0, slaves.length - matched),
      missing: configured.filter((motor) => !matchedIds.has(motor.id)).length,
      duplicate_axis: 0,
      total: slaves.length + configured.filter((motor) => !matchedIds.has(motor.id)).length,
    };
  }

  function dynamixelScanDevices() {
    return Array.isArray(latestScan?.dynamixel_scan?.devices)
      ? latestScan.dynamixel_scan.devices
      : [];
  }

  function motorKind(motor, fallback = 'unknown') {
    const type = motor?.motor_type || fallback;
    if (type === 'ac_servo') return 'AC 서보';
    if (type === 'dynamixel') return '다이나믹셀';
    if (type === 'cubemars') return '큐브마스';
    return type || '확인 불가';
  }

  function rowMotorType(row) {
    const motor = row.motor || row.proposedMotor;
    if (motor?.motor_type) return motor.motor_type;
    if (row.scanDevice) return 'dynamixel';
    if (row.scanRow) return 'ac_servo';
    return 'unknown';
  }

  function rowAxisRaw(row) {
    const draft = rowDraft(row);
    if (row.associationCandidate && draft.axis === undefined) return null;
    return firstDefined(
      draft.axis,
      row.motor?.config?.controller_index,
      row.motor?.axis,
      row.proposedMotor?.config?.controller_index,
      row.proposedMotor?.axis,
      row.scanRow?.controller_index,
      row.runtimeMotor?.controller_index,
    );
  }

  function axisLabel(row) {
    const axis = rowAxisRaw(row);
    return axis === null || axis === undefined ? '-' : formatInt(axis);
  }

  function rowIdPrefix(row) {
    const motorType = rowMotorType(row);
    if (motorType === 'ac_servo' || row.scanRow) return 'alias';
    if (motorType === 'dynamixel' || row.scanDevice) return 'ID';
    return 'ID';
  }

  function rowIdRaw(row) {
    const motor = row.motor || row.proposedMotor;
    const motorType = rowMotorType(row);
    if (motorType === 'ac_servo' || motor?.transport === 'ethercat' || row.scanRow) {
      return firstDefined(
        motor?.config?.alias,
        motor?.identity?.ethercat_alias,
        row.scanRow?.ethercat_alias,
        row.runtimeMotor?.alias,
      );
    }
    if (motorType === 'dynamixel' || motor?.transport === 'serial' || row.scanDevice) {
      return firstDefined(
        motor?.config?.bus_id,
        motor?.identity?.bus_id,
        motor?.identity?.node_id,
        row.scanDevice?.id,
        row.runtimeMotor?.bus_id,
        row.runtimeMotor?.node_id,
      );
    }
    return null;
  }

  function projectAliasValue(row) {
    const motor = row.motor;
    if (!motor || (motor.transport !== 'ethercat' && motor.motor_type !== 'ac_servo')) return '-';
    return firstDefined(motor.identity?.ethercat_alias, motor.config?.alias, '-');
  }

  function directScanAliasValue(row) {
    if (!row.scanRow) return '-';
    return row.scanRow.ethercat_alias === null || row.scanRow.ethercat_alias === undefined
      ? '읽기 실패'
      : row.scanRow.ethercat_alias;
  }

  function axisIdLabel(row) {
    const value = rowIdRaw(row);
    if (value === null || value === undefined) return '-';
    return `${rowIdPrefix(row)} ${formatInt(value)}`;
  }

  function acIdentityValue(row, field) {
    const motor = row.motor || row.proposedMotor;
    if (rowMotorType(row) !== 'ac_servo' && motor?.transport !== 'ethercat') return '-';
    if (field === 'eeprom_alias') {
      return row.scanRow
        ? firstDefined(row.scanRow.ethercat_alias, '읽기 실패')
        : firstDefined(motor?.identity?.ethercat_alias, motor?.config?.alias, '-');
    }
    if (field === 'rotary_alias') {
      return row.scanRow
        ? firstDefined(row.scanRow.rotary_alias, '읽기 실패')
        : firstDefined(motor?.identity?.rotary_alias, '-');
    }
    if (field === 'slave_position') {
      return row.scanRow
        ? firstDefined(row.scanRow.slave_position, '읽기 실패')
        : firstDefined(motor?.identity?.slave_position, '-');
    }
    return '-';
  }

  function acIdentityView(row, field) {
    const value = acIdentityValue(row, field);
    if (!row.motor || !row.scanRow || row.motor.transport !== 'ethercat') {
      return { text: value, mismatch: false };
    }
    const expected = field === 'eeprom_alias'
      ? firstDefined(row.motor.identity?.ethercat_alias, row.motor.config?.alias)
      : field === 'rotary_alias'
        ? row.motor.identity?.rotary_alias
        : row.motor.identity?.slave_position;
    const scanned = field === 'eeprom_alias'
      ? row.scanRow.ethercat_alias
      : field === 'rotary_alias'
        ? row.scanRow.rotary_alias
        : row.scanRow.slave_position;
    if (scanned === null || scanned === undefined) return { text: value, mismatch: false };
    if (expected === null || expected === undefined || Number(expected) !== Number(scanned)) {
      return {
        text: `${expected === null || expected === undefined ? '미등록' : formatInt(expected)} → ${formatInt(scanned)}`,
        mismatch: true,
      };
    }
    return { text: expected, mismatch: false };
  }

  function rowNameRaw(row) {
    const draft = rowDraft(row);
    if (draft.name !== undefined) return draft.name;
    if (row.associationCandidate) {
      return `검색 장비 · Slave ${formatInt(row.scanRow?.slave_position)}`;
    }
    if (row.motor) return registryMotorLabel(row.motor);
    if (row.proposedMotor) return registryMotorLabel(row.proposedMotor);
    return '-';
  }

  function rowDriverModelRaw(row) {
    const draft = rowDraft(row);
    if (draft.driver_model !== undefined) return draft.driver_model;
    const motor = row.motor || row.proposedMotor;
    return String(motor?.profile?.driver_model || '');
  }

  function axisSortValue(row) {
    const axis = Number(firstDefined(rowAxisRaw(row), 9999));
    return Number.isFinite(axis) ? axis : 9999;
  }

  function driverLabel(row) {
    if (row.motor) {
      return firstDefined(
        row.motor.profile?.driver_model,
        row.motor.driver_family,
        row.motor.config?.driver_id !== null && row.motor.config?.driver_id !== undefined
          ? `driver ${row.motor.config.driver_id}`
          : null,
      ) || '-';
    }
    if (row.scanRow) {
      const siiName = row.scanRow.sii_order_number || row.scanRow.sii_device_name;
      return siiName ? `SII ${siiName}` : '실제 모델 미확인';
    }
    if (row.scanDevice) return modelTextFromDevice(row.scanDevice) || '-';
    return '-';
  }

  function hasConfigTableDrafts() {
    return configTableDrafts.size > 0;
  }

  function updateConfigTableButtonState() {
    if (el.updateConfigTableButton) {
      el.updateConfigTableButton.disabled = !hasConfigTableDrafts();
      el.updateConfigTableButton.textContent = '표 변경값을 초안에 반영';
    }
  }

  function pathBasename(path) {
    return String(path || '').split(/[\\/]/).filter(Boolean).pop() || '';
  }

  function yamlPathText(tokens) {
    if (!tokens.length) return '(root)';
    return tokens.map((token, index) => {
      if (typeof token === 'number') return `[${token}]`;
      return index === 0 ? String(token) : `.${String(token)}`;
    }).join('');
  }

  function yamlSectionText(tokens) {
    if (!tokens.length) return '(root)';
    return String(tokens[0]);
  }

  function splitYamlValueAndComment(text) {
    const marker = text.indexOf(' #');
    if (marker < 0) return { value: text.trim(), comment: '' };
    return {
      value: text.slice(0, marker).trim(),
      comment: text.slice(marker),
    };
  }

  function yamlScalarType(rawValue) {
    const text = String(rawValue ?? '').trim();
    if (text === '' || text.toLowerCase() === 'null') return 'null';
    if (['true', 'false'].includes(text.toLowerCase())) return 'boolean';
    if (/^-?(?:0|[1-9]\d*)(?:\.\d+)?$/.test(text)) return 'number';
    return 'string';
  }

  function parseYamlKeyValue(text) {
    const index = text.indexOf(':');
    if (index < 0) return null;
    return {
      key: text.slice(0, index).trim(),
      rest: text.slice(index + 1),
    };
  }

  function yamlScalarRows() {
    const rows = [];
    const lines = String(motorConfigRawText || '').split('\n');
    const contextStack = [{ indent: -1, path: [] }];
    const collectionStack = [];
    const listIndexes = new Map();

    function popForIndent(indent) {
      while (contextStack.length > 1 && contextStack[contextStack.length - 1].indent >= indent) {
        contextStack.pop();
      }
      while (collectionStack.length > 0 && collectionStack[collectionStack.length - 1].indent > indent) {
        collectionStack.pop();
      }
    }

    function parentContext(indent) {
      for (let index = contextStack.length - 1; index >= 0; index -= 1) {
        if (contextStack[index].indent < indent) return contextStack[index];
      }
      return contextStack[0];
    }

    function parentCollection(indent) {
      for (let index = collectionStack.length - 1; index >= 0; index -= 1) {
        if (collectionStack[index].indent <= indent) return collectionStack[index];
      }
      return null;
    }

    lines.forEach((line, lineIndex) => {
      const indent = line.match(/^\s*/)?.[0]?.length || 0;
      const trimmed = line.trim();
      if (!trimmed || trimmed.startsWith('#')) return;

      if (trimmed.startsWith('- ')) {
        popForIndent(indent);
        const collection = parentCollection(indent);
        if (!collection) return;
        const listKey = yamlPathText(collection.path);
        const itemIndex = listIndexes.get(listKey) || 0;
        listIndexes.set(listKey, itemIndex + 1);
        const itemPath = [...collection.path, itemIndex];
        contextStack.push({ indent, path: itemPath });

        const itemText = trimmed.slice(2).trim();
        const parsed = parseYamlKeyValue(itemText);
        if (!parsed || parsed.rest.trim() === '') return;
        const valueInfo = splitYamlValueAndComment(parsed.rest);
        const tokens = [...itemPath, parsed.key];
        const prefix = `${line.slice(0, line.indexOf('-'))}- ${parsed.key}: `;
        rows.push({
          path: yamlPathText(tokens),
          tokens,
          section: yamlSectionText(tokens),
          type: yamlScalarType(valueInfo.value),
          value: valueInfo.value,
          lineIndex,
          prefix,
          comment: valueInfo.comment,
        });
        return;
      }

      popForIndent(indent);
      const parsed = parseYamlKeyValue(trimmed);
      if (!parsed) return;
      const parent = parentContext(indent);
      const keyPath = [...parent.path, parsed.key];
      const valueInfo = splitYamlValueAndComment(parsed.rest);

      if (parsed.rest.trim() === '') {
        collectionStack.push({ indent, path: keyPath });
        contextStack.push({ indent, path: keyPath });
        return;
      }

      const prefix = `${line.slice(0, line.indexOf(parsed.key))}${parsed.key}: `;
      rows.push({
        path: yamlPathText(keyPath),
        tokens: keyPath,
        section: yamlSectionText(keyPath),
        type: yamlScalarType(valueInfo.value),
        value: valueInfo.value,
        lineIndex,
        prefix,
        comment: valueInfo.comment,
      });
    });

    return rows;
  }

  function yamlRowsBySection() {
    const groups = new Map();
    yamlScalarRows().forEach((row) => {
      if (!groups.has(row.section)) groups.set(row.section, []);
      groups.get(row.section).push(row);
    });
    return [...groups.entries()];
  }

  function numberTextWithCommas(value) {
    const text = String(value ?? '').trim();
    const match = text.match(/^(-?)(\d+)(\.\d+)?$/);
    if (!match) return text;
    const [, sign, integerPart, decimalPart = ''] = match;
    return `${sign}${integerPart.replace(/\B(?=(\d{3})+(?!\d))/g, ',')}${decimalPart}`;
  }

  function yamlDisplayValue(value, type = '') {
    if (value === null) return 'null';
    if (value === undefined) return '';
    if (type === 'number') return numberTextWithCommas(value);
    return String(value);
  }

  function configTableInput(row) {
    const draft = configTableDrafts.get(row.path);
    const value = draft ? draft.value : row.value;
    if (!isEditableConfigRow(row)) {
      return `<span class="mono config-readonly-value">${displayText(yamlDisplayValue(value, row.type))}</span>`;
    }
    const inputType = row.type === 'number' ? 'number' : 'text';
    const step = configInputStep(row);
    return `
      <input
        class="config-value-input ${row.type === 'number' ? 'mono' : ''}"
        data-config-path="${escapeHtml(row.path)}"
        type="${inputType}"
        ${step ? `step="${step}"` : ''}
        value="${escapeHtml(String(value ?? ''))}"
      >
    `;
  }

  function isEditableConfigRow(row) {
    return isEditableMotorConfigPath(row?.path);
  }

  function configInputStep(row) {
    const item = yamlItemName(row);
    if (['controller_index', 'position'].includes(item)) return '1';
    if (row.type === 'number') return '0.001';
    return null;
  }

  function configMotorFromYamlSlavePrefix(rows, slavePrefix, index) {
    const masterPrefix = masterPrefixForSlavePrefix(slavePrefix);
    const driverId = configRowValue(rows, slavePrefix, 'driver_id');
    const driverPrefix = configDriverPrefixForMotor({ config: { driver_id: driverId } }, rows, slavePrefix);
    const masterType = String(configRowValue(rows, masterPrefix, 'type') || '').toLowerCase();
    const driverType = String(configRowValue(rows, driverPrefix, 'type') || '').toLowerCase();
    const axis = configRowValue(rows, slavePrefix, 'controller_index');
    const alias = configRowValue(rows, slavePrefix, 'alias');
    const busId = configRowValue(rows, slavePrefix, 'bus_id');
    const name = configRowValue(rows, slavePrefix, 'name') || `축 ${axis ?? index}`;
    const motorType = driverType === 'dynamixel'
      ? 'dynamixel'
      : masterType === 'ethercat'
        ? 'ac_servo'
        : driverType || masterType || 'unknown';
    const transport = masterType === 'ethercat'
      ? 'ethercat'
      : masterType === 'serial'
        ? 'serial'
        : 'unknown';

    return normalizeMotor({
      id: `config:${slavePrefix}`,
      enabled: true,
      hidden: false,
      deleted: false,
      axis,
      name,
      motor_type: motorType,
      driver_family: driverType || motorType,
      transport,
      identity: {
        ethercat_alias: alias,
        bus_id: busId,
      },
      profile: {
        driver_model: configRowValue(rows, driverPrefix, 'driver_model'),
      },
      config: {
        controller_index: axis,
        driver_id: driverId,
        alias,
        bus_id: busId,
      },
    });
  }

  function configAxisMotors(rows = yamlScalarRows()) {
    const motorsFromConfigFile = yamlSlavePrefixes(rows)
      .map((prefix, index) => configMotorFromYamlSlavePrefix(rows, prefix, index));
    if (motorsFromConfigFile.length > 0) {
      return motorsFromConfigFile.sort((a, b) => {
        const axisDiff = Number(firstDefined(motorAxisValue(a), 9999)) -
          Number(firstDefined(motorAxisValue(b), 9999));
        if (axisDiff !== 0) return axisDiff;
        return String(a.id || '').localeCompare(String(b.id || ''));
      });
    }

    return axisMotors()
      .filter((motor) => !motor.deleted)
      .slice()
      .sort((a, b) => {
        const axisDiff = Number(firstDefined(motorAxisValue(a), 9999)) -
          Number(firstDefined(motorAxisValue(b), 9999));
        if (axisDiff !== 0) return axisDiff;
        return String(a.id || '').localeCompare(String(b.id || ''));
      });
  }

  function configRowValue(rows, prefix, field) {
    const row = rows.find((item) => item.path === `${prefix}.${field}`);
    return row ? row.value : null;
  }

  function configRow(rows, prefix, field) {
    return rows.find((item) => item.path === `${prefix}.${field}`) || null;
  }

  function yamlSlavePrefixes(rows) {
    return [...new Set(rows
      .map((row) => row.path.match(/^(masters\[\d+\]\.slaves\[\d+\])\./)?.[1])
      .filter(Boolean))];
  }

  function yamlMasterPrefixes(rows) {
    return [...new Set(rows
      .map((row) => row.path.match(/^(masters\[\d+\])\./)?.[1])
      .filter(Boolean))];
  }

  function yamlDriverPrefixes(rows) {
    return [...new Set(rows
      .map((row) => row.path.match(/^(drivers\[\d+\])\./)?.[1])
      .filter(Boolean))];
  }

  function configSlavePrefixForMotor(motor, rows = yamlScalarRows()) {
    const axis = motorAxisValue(motor);
    const alias = firstDefined(motor.config?.alias, motor.identity?.ethercat_alias);
    const busId = firstDefined(motor.config?.bus_id, motor.identity?.bus_id, motor.identity?.node_id);
    const prefixes = yamlSlavePrefixes(rows);
    return prefixes.find((prefix) => {
      const rowAxis = configRowValue(rows, prefix, 'controller_index');
      const rowAlias = configRowValue(rows, prefix, 'alias');
      const rowBusId = configRowValue(rows, prefix, 'bus_id');
      if (alias !== null && alias !== undefined && rowAlias !== null && Number(rowAlias) === Number(alias)) return true;
      if (busId !== null && busId !== undefined && rowBusId !== null && Number(rowBusId) === Number(busId)) return true;
      return axis !== null && rowAxis !== null && Number(rowAxis) === Number(axis);
    }) || '';
  }

  function configDriverPrefixForMotor(motor, rows = yamlScalarRows(), slavePrefix = '') {
    const driverId = firstDefined(
      slavePrefix ? configRowValue(rows, slavePrefix, 'driver_id') : null,
      motor.config?.driver_id,
    );
    if (driverId === null || driverId === undefined) return '';
    return yamlDriverPrefixes(rows).find((prefix) => (
      Number(configRowValue(rows, prefix, 'id')) === Number(driverId)
    )) || '';
  }

  function rowsForYamlPrefix(rows, prefix) {
    if (!prefix) return [];
    return rows.filter((row) => row.path.startsWith(`${prefix}.`));
  }

  function slavePrefixFromConfigMotorId(id) {
    const text = String(id || '');
    return text.startsWith('config:') ? text.slice('config:'.length) : '';
  }

  function globalConfigRows(rows) {
    return rows.filter((row) => (
      !row.path.startsWith('masters[') &&
      !row.path.startsWith('drivers[') &&
      !row.path.startsWith('web_axis_identities[')
    ));
  }

  function masterPrefixForSlavePrefix(slavePrefix) {
    return slavePrefix.match(/^(masters\[\d+\])\.slaves\[\d+\]$/)?.[1] || '';
  }

  function rowsForMasterPrefix(rows, masterPrefix) {
    if (!masterPrefix) return [];
    return rows.filter((row) => (
      row.path.startsWith(`${masterPrefix}.`) &&
      !row.path.startsWith(`${masterPrefix}.slaves[`)
    ));
  }

  function yamlItemName(row) {
    const token = row?.tokens?.[row.tokens.length - 1];
    return token === null || token === undefined ? '-' : String(token);
  }

  function yamlItemKoreanName(row) {
    const labels = {
      period: '제어 주기',
      id: '식별 번호',
      type: '종류',
      number_of_slaves: '슬레이브 수',
      ethercat_master_index: 'EtherCAT 마스터 번호',
      serial_port: '시리얼 포트',
      serial_baudrate: '통신 속도',
      controller_index: '제어 축 번호',
      name: '축 이름',
      driver_id: '드라이버 ID',
      alias: 'EtherCAT 별칭',
      position: '슬레이브 위치',
      vendor_id: '제조사 ID',
      product_id: '제품 ID',
      profile_mode: '프로파일 모드',
      bus_id: '버스 ID',
      driver_model: '확인된 모델',
      pulse_per_revolution: '회전당 펄스 수',
      rated_effort: '정격 토크',
      unit_effort: '토크 단위 환산값',
      rated_current: '정격 전류',
      rated_power_w: '정격 출력',
      rated_speed_rpm: '정격 속도',
      lower: '최소 위치',
      upper: '최대 위치',
      speed: '속도 설정값',
      acceleration: '가속도',
      deceleration: '감속도',
      profile_velocity: '프로파일 속도',
      profile_acceleration: '프로파일 가속도',
      profile_deceleration: '프로파일 감속도',
      profile_position_value: '위치 명령 인터페이스 ID',
      profile_velocity_value: '속도 명령 인터페이스 ID',
      profile_effort_value: '토크 명령 인터페이스 ID',
      param_file: '파라미터 파일 경로',
      index: '오브젝트 인덱스',
      subindex: '오브젝트 하위 인덱스',
      size: '데이터 크기',
      direction: '통신 방향',
      data_type: '데이터 형식',
      default_value: '기본값',
    };
    return labels[yamlItemName(row)] || '설명 없음';
  }

  function yamlTypeDisplay(type) {
    const labels = {
      number: '숫자 (number)',
      string: '문자열 (string)',
      boolean: '논리값 (boolean)',
      null: '빈 값 (null)',
    };
    return labels[String(type || '')] || String(type || '-');
  }

  function yamlDriverTypeForPath(path, rows) {
    const driverPrefix = String(path || '').match(/^(drivers\[\d+\])\./)?.[1];
    if (!driverPrefix) return '';
    return String(configRowValue(rows, driverPrefix, 'type') || '').toLowerCase();
  }

  function yamlRowUnit(row, rows = []) {
    const item = yamlItemName(row);
    const comment = String(row?.comment || '').toLowerCase();
    const driverType = yamlDriverTypeForPath(row?.path, rows);

    if (item === 'period') return 'ns';
    if (item === 'serial_baudrate') return 'bps';
    if (item === 'rated_current') return 'A';
    if (item === 'rated_power_w') return 'W';
    if (item === 'rated_speed_rpm') return 'rpm';
    if (item === 'pulse_per_revolution') return 'count/rev';
    if (item === 'lower' || item === 'upper') return 'deg';
    if (item === 'profile_velocity') return 'deg/s';
    if (item === 'profile_acceleration' || item === 'profile_deceleration') return 'deg/s²';
    if (item === 'acceleration' || item === 'deceleration') return 'deg/s²';
    if (item === 'speed') {
      if (driverType === 'dynamixel') return 'deg/s';
      if (driverType === 'minas') return 'rpm (0x6080)';
      return '드라이버 단위';
    }
    if (item === 'size') return 'byte';

    if (comment.includes('position') && comment.includes('limit')) return 'count';
    if (comment.includes('target position') || comment.includes('position actual')) return 'count';
    if (comment.includes('profile velocity') || comment.includes('end velocity')) return 'count/s';
    if (comment.includes('target velocity') || comment.includes('velocity actual')) return 'count/s';
    if (comment.includes('profile acceleration') || comment.includes('profile deceleration')) return 'count/s²';
    if (comment.includes('max acceleration') || comment.includes('max deceleration')) return 'count/s²';
    if (comment.includes('max motor speed') || comment.includes('over-speed')) return 'rpm';

    return '-';
  }

  function renderConfigRowsTable(title, rows, emptyText) {
    return `
      <section class="config-detail-group">
        <div class="config-table-group-head">
          <strong>${displayText(title)}</strong>
          <span>${formatInt(rows.length)}개 값</span>
        </div>
        <div class="matching-table-wrap">
          <table class="matching-table config-value-table">
            <thead>
              <tr>
                <th>한글명</th>
                <th>실제 파라미터</th>
                <th>값</th>
                <th>단위</th>
                <th>자료형</th>
                <th>경로</th>
              </tr>
            </thead>
            <tbody>
              ${rows.length > 0
                ? rows.map((row) => `
                  <tr data-config-yaml-row="${escapeHtml(row.path)}">
                    <td class="config-item-cell">${displayText(yamlItemKoreanName(row))}</td>
                    <td class="config-item-cell mono">${displayText(yamlItemName(row))}</td>
                    <td>${configTableInput(row)}</td>
                    <td class="config-unit-cell">${displayText(yamlRowUnit(row, rows))}</td>
                    <td>${displayText(yamlTypeDisplay(row.type))}</td>
                    <td class="mono yaml-path-cell">${displayText(row.path)}</td>
                  </tr>
                `).join('')
                : `<tr><td colspan="6" class="empty">${displayText(emptyText)}</td></tr>`}
            </tbody>
          </table>
        </div>
      </section>
    `;
  }

  function configRowDisplayValue(row) {
    if (!row) return '-';
    return yamlDisplayValue(row.value, row.type);
  }

  function renderConfigKeyValueList(rows, emptyText = '-') {
    if (!rows.length) return `<span class="empty-inline">${displayText(emptyText)}</span>`;
    return `
      <dl class="config-kv-list">
        ${rows.map((row) => `
          <div>
            <dt>${displayText(yamlItemKoreanName(row))} <span class="mono">(${displayText(yamlItemName(row))})</span></dt>
            <dd class="mono">${displayText(configRowDisplayValue(row))}${yamlRowUnit(row, rows) !== '-' ? ` <span class="config-unit-inline">${displayText(yamlRowUnit(row, rows))}</span>` : ''}</dd>
          </div>
        `).join('')}
      </dl>
    `;
  }

  function renderMasterConfigOverview(title, rows, emptyText) {
    const prefixes = yamlMasterPrefixes(rows).sort((a, b) => {
      const aId = Number(configRowValue(rows, a, 'id'));
      const bId = Number(configRowValue(rows, b, 'id'));
      if (Number.isFinite(aId) && Number.isFinite(bId) && aId !== bId) return aId - bId;
      return a.localeCompare(b);
    });

    return `
      <section class="config-detail-group">
        <div class="config-table-group-head">
          <strong>${displayText(title)}</strong>
          <span>${formatInt(prefixes.length)}개 마스터</span>
        </div>
        <div class="matching-table-wrap">
          <table class="matching-table config-master-overview-table">
            <thead>
              <tr>
                <th>마스터 ID</th>
                <th>종류</th>
                <th>슬레이브 수</th>
                <th>마스터 항목</th>
                <th>경로</th>
              </tr>
            </thead>
            <tbody>
              ${prefixes.length > 0
                ? prefixes.map((prefix) => {
                  const idRow = configRow(rows, prefix, 'id');
                  const typeRow = configRow(rows, prefix, 'type');
                  const slaveCountRow = configRow(rows, prefix, 'number_of_slaves');
                  const groupedRows = rowsForMasterPrefix(rows, prefix).filter((row) => (
                    !['id', 'type', 'number_of_slaves'].includes(yamlItemName(row))
                  ));
                  return `
                    <tr>
                      <td class="mono">${displayText(configRowDisplayValue(idRow))}</td>
                      <td>${displayText(configRowDisplayValue(typeRow))}</td>
                      <td class="mono">${displayText(configRowDisplayValue(slaveCountRow))}</td>
                      <td class="config-kv-cell">${renderConfigKeyValueList(groupedRows)}</td>
                      <td class="mono yaml-path-cell">${displayText(prefix)}</td>
                    </tr>
                  `;
                }).join('')
                : `<tr><td colspan="5" class="empty">${displayText(emptyText)}</td></tr>`}
            </tbody>
          </table>
        </div>
      </section>
    `;
  }

  function ensureSelectedConfigMotor(motors) {
    if (motors.some((motor) => motor.id === selectedConfigMotorId)) return;
    selectedConfigMotorId = motors[0]?.id || '';
  }

  function renderMotorConfigTable() {
    if (!el.motorConfigTableRows) return;
    if (el.motorConfigTablePath) {
      el.motorConfigTablePath.textContent = `현재 프로젝트 파일: ${pathBasename(motorConfigFilePath) || '-'}`;
    }
    if (el.motorConfigFileNameInput && document.activeElement !== el.motorConfigFileNameInput) {
      el.motorConfigFileNameInput.value = motorConfigFileNameDraft || pathBasename(motorConfigFilePath);
    }
    const rows = yamlScalarRows();
    const motors = configAxisMotors(rows);
    const globalRows = globalConfigRows(rows);
    ensureSelectedConfigMotor(motors);
    const selectedMotor = motors.find((motor) => motor.id === selectedConfigMotorId) || null;
    if (motors.length === 0) {
      el.motorConfigTableRows.innerHTML = `
        <div class="config-section-stack">
          ${renderConfigRowsTable('전역 설정', globalRows, '전역 설정 항목이 없습니다')}
          ${renderMasterConfigOverview('마스터 설정', rows, '마스터 설정 항목이 없습니다')}
          <div class="empty config-table-empty">표시할 설정 축이 없습니다</div>
        </div>
      `;
      updateConfigTableButtonState();
      return;
    }

    const slavePrefix = selectedMotor
      ? slavePrefixFromConfigMotorId(selectedMotor.id) || configSlavePrefixForMotor(selectedMotor, rows)
      : '';
    const masterPrefix = masterPrefixForSlavePrefix(slavePrefix);
    const driverPrefix = selectedMotor ? configDriverPrefixForMotor(selectedMotor, rows, slavePrefix) : '';
    const masterRows = rowsForMasterPrefix(rows, masterPrefix);
    const slaveRows = rowsForYamlPrefix(rows, slavePrefix);
    const driverRows = rowsForYamlPrefix(rows, driverPrefix);
    const renderSignature = JSON.stringify({
      raw: motorConfigRawText,
      file: motorConfigFilePath,
      fileName: motorConfigFileNameDraft,
      selectedConfigMotorId,
    });

    if (renderSignature === lastConfigTableRenderSignature) {
      updateConfigTableButtonState();
      return;
    }
    lastConfigTableRenderSignature = renderSignature;

    el.motorConfigTableRows.innerHTML = `
      <div class="config-section-stack">
        ${renderConfigRowsTable('전역 설정', globalRows, '전역 설정 항목이 없습니다')}
        ${renderMasterConfigOverview('마스터 설정', rows, '마스터 설정 항목이 없습니다')}
        <div class="config-master-detail">
          <section class="config-axis-list" aria-label="설정 축 목록">
            <div class="config-table-group-head">
              <strong>축 목록</strong>
              <span>${formatInt(motors.length)}축</span>
            </div>
            <div class="matching-table-wrap">
              <table class="matching-table config-axis-table">
                <thead>
                  <tr>
                    <th>축 번호</th>
                    <th>ID</th>
                    <th>모터 종류</th>
                    <th>이름</th>
                  </tr>
                </thead>
                <tbody>
                  ${motors.map((motor) => {
                    const row = { motor };
                    const selected = motor.id === selectedConfigMotorId;
                    return `
                      <tr class="${selected ? 'selected-row' : ''}" data-config-axis-select="${escapeHtml(motor.id)}">
                        <td class="mono">${displayText(firstDefined(motor.config?.controller_index, motor.axis, '-'))}</td>
                        <td class="mono">${displayText(axisIdLabel(row))}</td>
                        <td>${displayText(motorKind(motor))}</td>
                        <td>${displayText(registryMotorLabel(motor))}</td>
                      </tr>
                    `;
                  }).join('')}
                </tbody>
              </table>
            </div>
          </section>
          <section class="config-axis-detail" aria-label="선택 축 설정 상세">
            <div class="config-selected-summary">
              <strong>${displayText(selectedMotor ? registryMotorLabel(selectedMotor) : '축을 선택하세요')}</strong>
              <span>${displayText(selectedMotor ? `${motorKind(selectedMotor)} / ${axisIdLabel({ motor: selectedMotor })}` : '')}</span>
            </div>
            ${renderConfigRowsTable('선택 마스터 설정', masterRows, '선택 축에 해당하는 마스터 설정 항목을 찾지 못했습니다')}
            ${renderConfigRowsTable('축 설정', slaveRows, '선택 축에 해당하는 축 설정 항목을 찾지 못했습니다')}
            ${renderConfigRowsTable('드라이버 설정', driverRows, '선택 축에 해당하는 드라이버 설정 항목을 찾지 못했습니다')}
          </section>
        </div>
      </div>
    `;
    updateConfigTableButtonState();
  }

  function renderMotorConfigRawText() {
    if (!el.motorConfigRawText) return;
    const renderSignature = motorConfigRawText || '';
    if (renderSignature === lastConfigRawTextRenderSignature) return;
    lastConfigRawTextRenderSignature = renderSignature;
    el.motorConfigRawText.textContent = motorConfigRawText || '설정 파일 원본이 없습니다';
  }

  function setConfigTableDraft(motorId, field, value) {
    if (!motorId) return;
    const row = yamlScalarRows().find((item) => item.path === motorId);
    if (!row || !isEditableConfigRow(row)) return;
    configTableDrafts.set(motorId, {
      value,
      tokens: row.tokens,
      lineIndex: row.lineIndex,
      prefix: row.prefix,
      comment: row.comment,
      originalType: row.type,
      originalValue: row.value,
    });
    updateConfigTableButtonState();
  }

  function handleConfigTableEdit(input) {
    const path = input.dataset.configPath || '';
    setConfigTableDraft(path, 'value', input.value);
  }

  function parseYamlScalarValue(draft, path, errors) {
    const type = draft.originalType;
    const value = draft.value;
    const raw = String(value ?? '').trim();
    if (type === 'number') {
      if (raw === '') {
        errors.push(`${path} 값이 비어 있습니다.`);
        return null;
      }
      const parsed = Number(raw);
      if (!Number.isFinite(parsed)) {
        errors.push(`${path} 값은 숫자여야 합니다.`);
        return null;
      }
      if (/^-?\d+$/.test(String(draft.originalValue ?? '').trim()) && !Number.isInteger(parsed)) {
        errors.push(`${path} 값은 정수여야 합니다.`);
        return null;
      }
      return parsed;
    }
    if (type === 'boolean') {
      const normalized = raw.toLowerCase();
      if (['true', '1', 'yes', 'on'].includes(normalized)) return true;
      if (['false', '0', 'no', 'off'].includes(normalized)) return false;
      errors.push(`${path} 값은 true/false 형식이어야 합니다.`);
      return null;
    }
    if (type === 'null') {
      return raw === '' || raw.toLowerCase() === 'null' ? null : value;
    }
    return String(value ?? '');
  }

  function formatYamlScalarValue(value, type) {
    if (type === 'null') return value === null ? 'null' : String(value ?? '');
    if (type === 'boolean') return value ? 'true' : 'false';
    return String(value ?? '');
  }

  function applyConfigTableUpdates() {
    if (!hasConfigTableDrafts()) {
      setAxisMessage('업데이트할 설정 파일 표 변경 없음');
      return;
    }

    const errors = [];
    const lines = String(motorConfigRawText || '').split('\n');
    configTableDrafts.forEach((draft, path) => {
      const parsed = parseYamlScalarValue(draft, path, errors);
      if (errors.length > 0) return;
      lines[draft.lineIndex] = `${draft.prefix}${formatYamlScalarValue(parsed, draft.originalType)}${draft.comment || ''}`;
    });

    if (errors.length > 0) {
      const message = errors.join('\n');
      window.alert(message);
      setAxisMessage('설정 파일 표 업데이트 중단');
      return;
    }

    motorConfigRawText = lines.join('\n');
    configTableDrafts = new Map();
    lastAxisRenderSignature = '';
    lastConfigTableRenderSignature = '';
    lastConfigRawTextRenderSignature = '';
    setAxisMessage('설정 파일 초안 반영 완료. 저장하려면 설정 파일 저장을 누르세요.');
    renderAxisSettings();
  }

  function scanStatus(row) {
    if (row.scanRow) {
      const deviceState = String(row.scanRow.device_state || '').toUpperCase();
      if (deviceState.includes('ERROR')) {
        return [`EtherCAT ${deviceState}`, 'delete'];
      }
      if (row.associationCandidate) {
        return ['기존 축 연결 필요', 'review'];
      }
      const motor = row.motor || row.proposedMotor;
      const identity = motor?.identity || {};
      const expectedRotary = identity.rotary_alias;
      const scannedRotary = row.scanRow.rotary_alias;
      const expectedSlave = identity.slave_position;
      if (expectedRotary === null || expectedRotary === undefined ||
          expectedSlave === null || expectedSlave === undefined ||
          scannedRotary === null || scannedRotary === undefined) {
        return ['연결정보 확인 필요', 'review'];
      }
      if (expectedRotary !== null && expectedRotary !== undefined &&
          scannedRotary !== null && scannedRotary !== undefined &&
          Number(expectedRotary) !== Number(scannedRotary)) {
        return ['Station Alias 불일치', 'delete'];
      }
      if (expectedSlave !== null && expectedSlave !== undefined &&
          row.scanRow.slave_position !== null &&
          row.scanRow.slave_position !== undefined &&
          Number(expectedSlave) !== Number(row.scanRow.slave_position)) {
        return ['Slave Position 불일치', 'delete'];
      }
      const expectedVendor = motor?.config?.vendor_id;
      const expectedProduct = motor?.config?.product_id;
      if ((expectedVendor !== null && expectedVendor !== undefined &&
           row.scanRow.vendor_id !== null && row.scanRow.vendor_id !== undefined &&
           Number(expectedVendor) !== Number(row.scanRow.vendor_id)) ||
          (expectedProduct !== null && expectedProduct !== undefined &&
           row.scanRow.product_code !== null && row.scanRow.product_code !== undefined &&
           Number(expectedProduct) !== Number(row.scanRow.product_code))) {
        return ['모델 불일치', 'delete'];
      }
      return ['식별값 일치', 'matched'];
    }
    if (row.scanDevice) return ['스캔 감지', 'matched'];
    if (latestScan) return ['스캔 미감지', 'review'];
    return ['스캔 안함', 'unknown'];
  }

  function runtimeStatus(row) {
    const runtime = row.runtimeMotor;
    if (!runtime) return ['미수신', row.motor ? 'review' : 'unknown'];
    const state = runtime.state || 'unknown';
    const axis = runtime.controller_index === null || runtime.controller_index === undefined
      ? '-'
      : formatInt(runtime.controller_index);
    const savedAxis = row.motor ? motorAxisValue(row.motor) : null;
    if (savedAxis !== null && runtime.controller_index !== null &&
        runtime.controller_index !== undefined &&
        Number(savedAxis) !== Number(runtime.controller_index)) {
      return [`Control Index 불일치 ${formatInt(savedAxis)}→${axis}`, 'delete'];
    }
    return [`${stateLabel(state)} / 축 ${axis}`, state === 'detected' ? 'matched' : 'review'];
  }

  function scanRowLikelyExistingMotor(scanRow) {
    if (!scanRow) return null;
    return activeAxisMotors().find(
      (motor) => scanRowSharesConfiguredPosition(scanRow, motor),
    ) || null;
  }

  function settingStatus(row) {
    if (row.associationCandidate) return ['연결 후보', 'review'];
    const motor = row.motor;
    if (!motor) return ['미설정', 'unregistered'];
    if (motor.deleted) return ['삭제 예정', 'delete'];
    const saved = savedMotorById(motor.id);
    if (!saved) return ['추가 예정', 'review'];
    if (JSON.stringify(normalizeMotor(saved)) !== JSON.stringify(normalizeMotor(motor))) {
      return ['변경 예정', 'review'];
    }
    return ['설정됨', 'matched'];
  }

  function finiteRuntimeNumber(value) {
    const number = Number(value);
    return Number.isFinite(number) ? number : null;
  }

  function rowDeviceIdentity(row) {
    const runtime = row.runtimeMotor || {};
    if (rowMotorType(row) === 'ac_servo') {
      const identity = row.motor?.identity || {};
      const config = row.motor?.config || {};
      const vendor = firstDefined(row.scanRow?.vendor_id, identity.vendor_id, config.vendor_id);
      const product = firstDefined(
        row.scanRow?.product_code,
        identity.product_code,
        config.product_id,
      );
      const revision = firstDefined(
        row.scanRow?.revision_number,
        identity.revision_number,
        config.revision_number,
      );
      const serial = firstDefined(
        row.scanRow?.serial_number,
        identity.serial_number,
        runtime.serial_number,
      );
      const eepromAlias = firstDefined(
        row.scanRow?.ethercat_alias,
        identity.ethercat_alias,
        config.alias,
      );
      const position = firstDefined(
        row.scanRow?.slave_position,
        identity.slave_position,
        config.position,
      );
      return {
        title: `Vendor ${displayText(vendor)} · Product ${displayText(product)}`,
        detail: [
          `Revision ${displayText(revision)}`,
          serial === null || serial === undefined || serial === ''
            ? 'Serial 미수신'
            : `Serial ${serial}`,
          `EEPROM Alias ${displayText(eepromAlias)}`,
          `Slave Position ${displayText(position)}`,
        ].filter(Boolean).join(' · '),
      };
    }
    if (rowMotorType(row) === 'dynamixel') {
      const model = firstDefined(
        row.scanDevice?.model_name,
        row.scanDevice?.model_number,
        runtime.model_name,
        driverLabel(row),
      );
      const firmware = firstDefined(row.scanDevice?.firmware_version, runtime.firmware_version);
      return {
        title: model ? `Dynamixel ${model}` : 'Dynamixel',
        detail: firmware === null || firmware === undefined
          ? `${axisIdLabel(row)} · FW 미수신`
          : `${axisIdLabel(row)} · FW ${firmware}`,
      };
    }
    return { title: driverLabel(row), detail: axisIdLabel(row) };
  }

  function rowModelProfileView(row) {
    const motor = row.motor || row.proposedMotor;
    if (!motor) {
      const siiName = firstDefined(row.scanRow?.sii_order_number, row.scanRow?.sii_device_name);
      return {
        title: '모델 미확인',
        detail: siiName ? `SII 참고값 ${siiName}` : '모델·운전 프로필 미설정',
      };
    }
    const model = String(motor.profile?.driver_model || '').trim();
    const confirmed = motor.profile?.model_confirmed === true;
    const source = String(motor.profile?.model_source || '');
    const sourceLabel = source === 'verified_catalog'
      ? '카탈로그 확인'
      : source === 'physical_protocol'
        ? '장치 프로토콜 확인'
        : source === 'user_nameplate'
          ? '사용자 명판 확인'
          : '확인 근거 없음';
    const driverId = motor.config?.driver_id;
    const siiName = firstDefined(
      row.scanRow?.sii_order_number,
      row.scanRow?.sii_device_name,
      motor.identity?.sii_order_number,
      motor.identity?.sii_device_name,
    );
    return {
      title: confirmed && model ? model : '모델 미확인',
      detail: [
        confirmed ? sourceLabel : null,
        driverId === null || driverId === undefined ? '운전 프로필 미설정' : `운전 프로필 driver ${driverId}`,
        !confirmed && siiName ? `SII 참고값 ${siiName}` : null,
      ].filter(Boolean).join(' · '),
    };
  }

  function rowConnectionIdentity(row) {
    if (rowMotorType(row) === 'ac_servo') {
      return {
        title: `${axisIdLabel(row)} · Slave ${displayText(acIdentityValue(row, 'slave_position'))}`,
        detail: `EEPROM ${displayText(directScanAliasValue(row))} · Station ${displayText(acIdentityValue(row, 'rotary_alias'))}`,
      };
    }
    const motor = row.motor || row.proposedMotor;
    const port = firstDefined(
      row.scanDevice?.port,
      motor?.identity?.serial_port,
      motor?.config?.serial_port,
      row.runtimeMotor?.serial_port,
    );
    return {
      title: axisIdLabel(row),
      detail: port ? String(port) : '직렬 포트 미수신',
    };
  }

  function rowMappingView(row) {
    const runtime = row.runtimeMotor;
    if (row.associationCandidate) {
      return {
        text: '연결 대상 선택',
        detail: '기존 축과 1:1 선택',
        className: 'review',
        ready: false,
      };
    }
    if (!row.motor || row.motor.deleted || !row.motor.enabled) {
      return { text: '미사용 축', detail: '모션 적용 제외', className: 'unknown', ready: false };
    }
    if (!runtime) {
      return { text: '확인 불가', detail: '실행 상태 미수신', className: 'review', ready: false };
    }
    if (runtime.motion_axis_configured === true && runtime.motion_id) {
      return {
        text: '매칭됨',
        detail: `Motion ${runtime.motion_id}`,
        className: 'matched',
        ready: true,
      };
    }
    if (runtime.motion_axis_configured === true) {
      return { text: '매칭 오류', detail: 'Motion ID 중복·누락', className: 'duplicate', ready: false };
    }
    return { text: '미매칭', detail: '모션축 설정 필요', className: 'review', ready: false };
  }

  function rowDriveView(row) {
    const runtime = row.runtimeMotor;
    const driveName = rowMotorType(row) === 'dynamixel' ? '토크' : '서보';
    if (!runtime) {
      return { text: `${driveName} 확인 불가`, detail: '실행 상태 미수신', className: 'review', ready: false };
    }
    if (runtime.fault) {
      return { text: '오류 발생', detail: runtime.error_text || runtime.error || '장치 오류 확인', className: 'duplicate', ready: false };
    }
    const ready = runtime.servo_on === true;
    return {
      text: `${driveName} ${ready ? 'ON' : 'OFF'}`,
      detail: rowMotorType(row) === 'dynamixel'
        ? 'Torque Enable 상태'
        : '서보 드라이버 상태',
      className: ready ? 'matched' : 'review',
      ready,
    };
  }

  function rowMotionPermissionView(row, mapping, drive) {
    const runtime = row.runtimeMotor;
    const latest = getLatestState?.() || {};
    const context = latest.execution_context || {};
    if (!row.motor || row.motor.deleted || !row.motor.enabled) {
      return { text: '사용 안 함', detail: '프로젝트에서 비활성', className: 'unknown', ready: false };
    }
    if (!runtime || runtime.state !== 'detected') {
      return { text: '동작 차단', detail: '모터 연결 확인 필요', className: 'duplicate', ready: false };
    }
    if (runtime.fault) {
      return { text: '동작 차단', detail: '장치 오류 해제 필요', className: 'duplicate', ready: false };
    }
    const current = finiteRuntimeNumber(firstDefined(runtime.position_deg, runtime.position));
    const lower = finiteRuntimeNumber(runtime.lower);
    const upper = finiteRuntimeNumber(runtime.upper);
    if (current === null) {
      return { text: '동작 차단', detail: '현재 위치 미수신', className: 'duplicate', ready: false };
    }
    if (lower === null || upper === null || lower > upper) {
      return { text: '동작 차단', detail: 'lower / upper 확인 필요', className: 'duplicate', ready: false };
    }
    if (current < lower || current > upper) {
      return { text: '범위 복귀만', detail: `${lower.toFixed(1)}° ~ ${upper.toFixed(1)}°`, className: 'review', ready: false };
    }
    if (!drive.ready) {
      return { text: '동작 대기', detail: `${rowMotorType(row) === 'dynamixel' ? '토크' : '서보'} ON 필요`, className: 'review', ready: false };
    }
    if (!context.ready) {
      return { text: '동작 대기', detail: '실행 설정 적용 필요', className: 'review', ready: false };
    }
    return { text: '조그·동작 가능', detail: '현재 조건 충족', className: 'matched', ready: true };
  }

  function rowMotionRunView(row, mapping, permission) {
    const run = getLatestState?.()?.motion_run_status || {};
    if (!mapping.ready) {
      return { text: '실행 불가', detail: mapping.detail, className: 'review', ready: false };
    }
    if (!permission.ready) {
      return { text: '실행 대기', detail: permission.detail, className: 'review', ready: false };
    }
    if (run.active || run.running) {
      return { text: '모션 실행 중', detail: '현재 실행 상태', className: 'configured', ready: true };
    }
    return {
      text: '실행 가능',
      detail: '축별 실행 이력은 미지원',
      className: 'matched',
      ready: true,
    };
  }

  function rowOverallView(row, mapping, drive, permission, motionRun) {
    if (!row.motor || row.motor.deleted || !row.motor.enabled) {
      return { text: '관리 제외', detail: '미사용 축', className: 'unknown', ready: false };
    }
    if (row.runtimeMotor?.fault) {
      return { text: '오류', detail: '오류 팝업에서 확인', className: 'duplicate', ready: false };
    }
    const physicalMatched = rowMotorType(row) === 'ac_servo'
      ? Boolean(row.scanRow)
      : rowMotorType(row) === 'dynamixel'
        ? Boolean(row.scanDevice)
        : false;
    if (!physicalMatched) {
      return { text: '물리 확인 필요', detail: '장비 검색 후 판정', className: 'review', ready: false };
    }
    if (permission.text === '범위 복귀만') {
      return { text: '복귀 필요', detail: '경계 복귀 후 재확인', className: 'review', ready: false };
    }
    if (mapping.ready && drive.ready && permission.ready && motionRun.ready) {
      return { text: '구동 준비', detail: '실물 검증 미확인', className: 'matched', ready: true };
    }
    return { text: '준비 중', detail: permission.detail || mapping.detail, className: 'review', ready: false };
  }

  function motorStatusTypeKey(motor) {
    const type = String(motor?.motor_type || motor?.motor_type_label || '').toLowerCase();
    const transport = String(motor?.transport || motor?.transport_label || '').toLowerCase();
    if (type.includes('dynamixel')) return 'dynamixel';
    if (type.includes('cubemars')) return 'cubemars';
    if (
      type.includes('ac_servo') ||
      type.includes('ac servo') ||
      type.includes('minas') ||
      transport.includes('ethercat')
    ) return 'ac_servo';
    return 'unknown';
  }

  function motorStatusTypeLabel(key) {
    if (key === 'ac_servo') return 'AC 서보';
    if (key === 'dynamixel') return 'Dynamixel';
    if (key === 'cubemars') return 'CubeMars';
    return '기타·확인 불가';
  }

  function countMotorsByStatusType(motors, predicate = () => true) {
    const counts = new Map();
    motors.filter(predicate).forEach((motor) => {
      const key = motorStatusTypeKey(motor);
      counts.set(key, (counts.get(key) || 0) + 1);
    });
    return counts;
  }

  function physicalScanStatus(typeKey) {
    if (typeKey === 'ac_servo') {
      const scan = latestScan?.ethercat_scan;
      if (!scan || scan.skipped) return { code: 'unknown', text: '미확인', count: null };
      const count = Array.isArray(scan.slaves) ? scan.slaves.length : 0;
      if (scan.available && scan.complete) return { code: count > 0 ? 'good' : 'off', text: `${formatInt(count)}축`, count };
      if (scan.available && count > 0) return { code: 'warning', text: `부분 ${formatInt(count)}축`, count };
      return { code: 'error', text: '검증 불가', count: null };
    }
    if (typeKey === 'dynamixel') {
      const scan = latestScan?.dynamixel_scan;
      if (!scan || scan.skipped) return { code: 'unknown', text: '미확인', count: null };
      const count = Array.isArray(scan.devices) ? scan.devices.length : 0;
      if (scan.available && scan.complete) return { code: count > 0 ? 'good' : 'off', text: `${formatInt(count)}축`, count };
      if (scan.available && count > 0) return { code: 'warning', text: `부분 ${formatInt(count)}축`, count };
      return { code: 'error', text: '검증 불가', count: null };
    }
    if (typeKey === 'cubemars') return { code: 'unknown', text: '검색 미지원', count: null };
    return { code: 'unknown', text: '미확인', count: null };
  }

  function statusCell(text, code = '') {
    return `<span class="motor-status-value${code ? ` ${escapeHtml(code)}` : ''}">${displayText(text)}</span>`;
  }

  function renderMotorTypeStatus(rowViews, changed) {
    if (!el.motorTypeRows) return;
    const latest = getLatestState?.() || {};
    const runtime = runtimeMotors();
    const projectMotors = selectActiveVisibleRegistryMotors(savedRegistry);
    const projectCounts = countMotorsByStatusType(projectMotors);
    const runtimeCounts = countMotorsByStatusType(runtime);
    const feedbackCounts = countMotorsByStatusType(
      runtime,
      (motor) => motor.connection_connected === true ||
        (!Object.prototype.hasOwnProperty.call(motor, 'connection_connected') && motor.state === 'detected'),
    );
    const driveCounts = countMotorsByStatusType(runtime, (motor) => motor.servo_on === true);
    const faultCounts = countMotorsByStatusType(
      runtime,
      (motor) => Boolean(motor.fault) || Number(motor.errorcode || 0) !== 0,
    );
    const scope = latest.project_scope || {};
    const runtimeMatchesProject = scope.runtime_matches_selected === true;
    const runtimeApplied = runtimeMatchesProject && scope.motor_config_applied === true;
    const types = new Set(['ac_servo', 'dynamixel']);
    [...projectMotors, ...runtime].forEach((motor) => types.add(motorStatusTypeKey(motor)));
    const orderedTypes = ['ac_servo', 'dynamixel', 'cubemars', 'unknown']
      .filter((key) => types.has(key));

    const html = orderedTypes.map((typeKey) => {
      const physical = physicalScanStatus(typeKey);
      const typeRows = rowViews.filter(
        (view) => view.row.motor &&
          !view.row.motor.deleted &&
          view.row.motor.enabled &&
          motorStatusTypeKey(view.row.motor) === typeKey,
      );
      const physicalConfirmed = physical.count !== null;
      const controllable = physicalConfirmed && runtimeApplied && !changed
        ? typeRows.filter((view) => {
          const matchedByScan = typeKey === 'ac_servo'
            ? Boolean(view.row.scanRow)
            : typeKey === 'dynamixel'
              ? Boolean(view.row.scanDevice)
              : false;
          return matchedByScan && view.overall.ready;
        }).length
        : null;
      const appliedText = !runtimeMatchesProject
        ? '다른 프로젝트'
        : !runtimeApplied
          ? '미적용'
          : `${formatInt(runtimeCounts.get(typeKey) || 0)}축`;
      const appliedCode = runtimeApplied ? 'good' : 'warning';
      const feedbackCount = feedbackCounts.get(typeKey) || 0;
      const driveCount = driveCounts.get(typeKey) || 0;
      const faultCount = faultCounts.get(typeKey) || 0;
      const controllableText = controllable === null
        ? (physical.code === 'error' ? '검증 불가' : '미확인')
        : `${formatInt(controllable)}축`;
      const controllableCode = controllable === null
        ? (physical.code === 'error' ? 'error' : 'unknown')
        : controllable > 0 ? 'good' : 'off';
      return `
        <tr>
          <th scope="row">${displayText(motorStatusTypeLabel(typeKey))}</th>
          <td>${statusCell(`${formatInt(projectCounts.get(typeKey) || 0)}축`, 'configured')}</td>
          <td>${statusCell(physical.text, physical.code)}</td>
          <td>${statusCell(appliedText, appliedCode)}</td>
          <td>${statusCell(`${formatInt(feedbackCount)}축`, feedbackCount > 0 ? 'received' : 'off')}</td>
          <td>${statusCell(`${formatInt(faultCount)}축`, faultCount > 0 ? 'error' : 'good')}</td>
          <td>${statusCell(`${formatInt(driveCount)}축`, driveCount > 0 ? 'good' : 'off')}</td>
          <td>${statusCell(controllableText, controllableCode)}</td>
        </tr>
      `;
    }).join('');
    if (el.motorTypeRows.innerHTML !== html) el.motorTypeRows.innerHTML = html;

    if (el.motorTypeSummaryDetail) {
      const summary = getDiscoverySummary();
      el.motorTypeSummaryDetail.textContent = summary.hasDirectScan
        ? `최근 물리 검색 결과 · 감지 ${formatInt(summary.discoveredCount)}축`
        : '물리 감지 미확인 · 장비 검색 후 판정';
    }
  }

  function renderMotorReadiness(rows, rowViews, changed) {
    const latest = getLatestState?.() || {};
    const configuredRows = rowViews.filter(
      (view) => view.row.motor && !view.row.motor.deleted && view.row.motor.enabled,
    );
    const runtimeFresh = latest.motion_state_age_sec === null ||
      latest.motion_state_age_sec === undefined ||
      Number(latest.motion_state_age_sec) <= 2;
    const runtimeResponding = Array.isArray(latest.motors);
    const serviceReady = runtimeFresh && (
      runtimeResponding || Boolean(latest.service_management?.motor_managed)
    );
    const connectionReady = configuredRows.length > 0 && configuredRows.every((view) => (
      view.row.runtimeMotor?.state === 'detected' &&
      (rowMotorType(view.row) === 'ac_servo'
        ? Boolean(view.row.scanRow)
        : rowMotorType(view.row) === 'dynamixel'
          ? Boolean(view.row.scanDevice)
          : false)
    ));
    const configurationReady = configuredRows.length > 0 && !changed;
    const applicationReady = configurationReady && selectedMotorConfigAlreadyApplied() && !configApplyPending;
    const mappingReady = configuredRows.length > 0 && configuredRows.every((view) => view.mapping.ready);
    const driveReady = configuredRows.length > 0 && configuredRows.every((view) => view.drive.ready);
    const faults = configuredRows.filter((view) => Boolean(view.row.runtimeMotor?.fault)).length;
    const readyAxes = configuredRows.filter((view) => view.overall.ready).length;
    const steps = [
      { key: 'service', ready: serviceReady, text: serviceReady ? '서비스 응답 정상' : '모터 제어 재시작·응답 확인', next: '모터 제어 서비스를 시작하거나 재시작하세요.' },
      { key: 'connection', ready: connectionReady, text: connectionReady ? '등록 축 연결됨' : '모터 전원·연결 및 검색 필요', next: '모터 전원을 확인한 뒤 장비 검색을 실행하세요.' },
      { key: 'configuration', ready: configurationReady, text: configurationReady ? '축 설정 저장됨' : '축 설정 저장 필요', next: '검색 결과를 확인하고 모터축 설정을 저장하세요.' },
      { key: 'application', ready: applicationReady, text: applicationReady ? '실행 시스템 적용됨' : '설정 적용 필요', next: '설정 적용 및 재시작을 실행하세요.' },
      { key: 'mapping', ready: mappingReady, text: mappingReady ? '모션축 매칭됨' : '모션축 매칭 필요', next: '모션축 설정에서 각 모터축의 Motion ID를 연결하세요.' },
      { key: 'drive', ready: driveReady, text: driveReady ? '서보·토크 준비됨' : '서보·토크 상태 확인', next: 'AC 서보를 켜고 Dynamixel 토크 상태를 확인하세요.' },
      { key: 'verification', ready: false, text: '실물 조그·동작 확인 필요', next: '실제 장비에서 조그와 동작 모드를 확인하세요.' },
    ];
    const currentIndex = steps.findIndex((step) => !step.ready);

    if (el.motorReadinessSteps) {
      steps.forEach((step, index) => {
        const item = el.motorReadinessSteps.querySelector(`[data-motor-readiness-step="${step.key}"]`);
        if (!item) return;
        const state = step.ready ? 'complete' : index === currentIndex
          ? (faults > 0 ? 'error' : 'current')
          : 'pending';
        item.dataset.state = state;
        const detail = item.querySelector('small');
        if (detail) detail.textContent = step.text;
      });
    }
    if (el.motorReadinessHeadline) {
      el.motorReadinessHeadline.textContent = faults > 0
        ? `오류 ${formatInt(faults)}축 확인 필요`
        : readyAxes === configuredRows.length && configuredRows.length > 0
          ? '구동 준비 · 실물 검증 미확인'
          : '준비 작업 진행 중';
    }
    if (el.motorReadinessAxisCount) {
      el.motorReadinessAxisCount.textContent = `${formatInt(readyAxes)}/${formatInt(configuredRows.length)}축`;
    }
    if (el.motorReadinessCurrentStep) {
      el.motorReadinessCurrentStep.textContent = `${currentIndex + 1}단계 · ${steps[currentIndex]?.text || '확인 완료'}`;
    }
    if (el.motorReadinessFaultCount) el.motorReadinessFaultCount.textContent = `${formatInt(faults)}축`;
    if (el.motorReadinessNextAction) {
      el.motorReadinessNextAction.textContent = steps[currentIndex]?.next || '현재 조건에서 추가 작업이 없습니다.';
    }
  }

  function rowById(rowId) {
    return axisRowsData().find((row) => row.id === rowId) || null;
  }

  function editedMotor(motor, row, field, value) {
    const next = normalizeMotor(motor);
    if (field === 'name') {
      next.name = String(value ?? '');
    } else if (field === 'driver_model') {
      next.profile = {
        ...(next.profile || {}),
        driver_model: String(value ?? '').trim(),
        model_confirmed: String(value ?? '').trim().length > 0,
        model_source: String(value ?? '').trim().length > 0 ? 'user_nameplate' : '',
      };
    } else if (field === 'axis') {
      const axis = Number(value);
      if (!Number.isInteger(axis) || axis < 0) return next;
      next.axis = axis;
      next.config = {
        ...(next.config || {}),
        controller_index: axis,
      };
    }
    return normalizeMotor(next);
  }

  function motorWithRowDraft(motor, row) {
    let next = normalizeMotor(motor);
    const draft = rowDraft(row);
    if (draft.name !== undefined) next = editedMotor(next, row, 'name', draft.name);
    if (draft.axis !== undefined) next = editedMotor(next, row, 'axis', draft.axis);
    if (draft.driver_model !== undefined) {
      next = editedMotor(next, row, 'driver_model', draft.driver_model);
    }
    return next;
  }

  function setAxisEditValue(row, field, value) {
    if (row.motor) {
      upsertMotorInRegistry(axisConfig, editedMotor(row.motor, row, field, value));
      return;
    }
    setRowDraft(row.id, { [field]: value });
  }

  function resetAxisEditInput(input, row, field) {
    if (field === 'name') input.value = rowNameRaw(row) ?? '';
    if (field === 'axis') input.value = rowAxisRaw(row) ?? '';
    if (field === 'driver_model') input.value = rowDriverModelRaw(row);
  }

  function handleAxisEdit(input) {
    const rowId = input.dataset.axisRowId || '';
    const field = input.dataset.axisEdit || '';
    const row = rowById(rowId);
    if (!row) return;

    if (field === 'name') {
      setAxisEditValue(row, 'name', input.value);
    } else if (field === 'driver_model') {
      setAxisEditValue(row, 'driver_model', input.value);
    } else if (field === 'axis') {
      const axis = Number(input.value);
      if (!Number.isInteger(axis) || axis < 0) {
        resetAxisEditInput(input, row, field);
        setAxisMessage('축 번호는 0 이상의 정수여야 합니다.');
        return;
      }
      setAxisEditValue(row, 'axis', axis);
    } else {
      resetAxisEditInput(input, row, field);
      return;
    }

    lastAxisRenderSignature = '';
    setAxisMessage('축 목록 변경됨. 저장하려면 변경 내용 저장을 누르세요.');
    renderAxisSettings();
  }

  function axisRowsData() {
    const rows = [];
    const byId = new Map();
    const usedAcScan = new Set();
    const usedDynamixelScan = new Set();
    const runtime = runtimeMotors();
    const acRuntime = runtime.filter((motor) => runtimeIsAcServo(motor));
    const dynamixelRuntime = runtime.filter((motor) => runtimeIsDynamixel(motor));
    const scanAxisAllocator = createAxisAllocator(axisMotors());

    axisMotors().forEach((motor) => {
      const row = {
        id: motor.id,
        motor,
        proposedMotor: null,
        scanRow: null,
        scanDevice: null,
        runtimeMotor: runtimeMotorForRegistryMotor(motor, runtime),
      };
      rows.push(row);
      byId.set(motor.id, row);
    });

    acServoScanRows().forEach((scanRow) => {
      const matched = axisMotors().find((motor) => scanRowMatchesRegistryMotor(scanRow, motor)) || null;
      if (matched) {
        const row = byId.get(matched.id);
        if (row) {
          row.scanRow = scanRow;
          row.runtimeMotor = row.runtimeMotor || runtimeMotorForRegistryMotor(matched, acRuntime);
        }
        usedAcScan.add(scanKey(scanRow));
        return;
      }
      const proposedMotor = acServoScanRowToMotor(scanRow, scanAxisAllocator);
      const associationCandidate = scanRowLikelyExistingMotor(scanRow);
      rows.push({
        id: `scan:ac:${scanKey(scanRow)}`,
        motor: null,
        proposedMotor,
        associationCandidate,
        scanRow,
        scanDevice: null,
        runtimeMotor: runtimeMotorForScanRow(scanRow, acRuntime),
      });
      usedAcScan.add(scanKey(scanRow));
    });

    dynamixelScanDevices().forEach((device) => {
      const key = dynamixelScanDeviceKey(device);
      const matched = axisMotors().find((motor) => dynamixelMotorMatchesDevice(motor, device)) || null;
      if (matched) {
        const row = byId.get(matched.id);
        if (row) {
          row.scanDevice = device;
          row.runtimeMotor = row.runtimeMotor || runtimeMotorForRegistryMotor(matched, dynamixelRuntime);
        }
        usedDynamixelScan.add(key);
        return;
      }
      rows.push({
        id: `scan:dynamixel:${key}`,
        motor: null,
        proposedMotor: dynamixelScanDeviceToMotor(device, null, scanAxisAllocator),
        scanRow: null,
        scanDevice: device,
        runtimeMotor: runtimeMotorForDynamixelDevice(device, dynamixelRuntime),
      });
      usedDynamixelScan.add(key);
    });

    return rows.sort((a, b) => {
      const axisDiff = axisSortValue(a) - axisSortValue(b);
      if (axisDiff !== 0) return axisDiff;
      return String(a.id).localeCompare(String(b.id));
    });
  }

  function selectedAxisRows(rows = axisRowsData()) {
    return rows.filter((row) => selectedAxisIds.has(row.id));
  }

  function removeMissingSelectedAxisIds(rows) {
    const validIds = new Set(rows.map((row) => row.id));
    selectedAxisIds = new Set([...selectedAxisIds].filter((id) => validIds.has(id)));
  }

  function autoSelectNewScanAxes() {
    const rows = axisRowsData();
    const newRows = rows.filter(
      (row) => !row.motor && row.proposedMotor && !row.associationCandidate,
    );
    const candidateCount = rows.filter(
      (row) => !row.motor && row.associationCandidate,
    ).length;
    selectedAxisIds = new Set(newRows.map((row) => row.id));
    lastAxisRenderSignature = '';
    renderAxisSettings();
    return { newCount: newRows.length, candidateCount };
  }

  function toggleAxisSelection(id) {
    if (!id) return;
    selectedAxisIds = new Set(selectedAxisIds);
    if (selectedAxisIds.has(id)) {
      selectedAxisIds.delete(id);
    } else {
      selectedAxisIds.add(id);
    }
  }

  function renderAxisButtons(rows) {
    const selectedRows = selectedAxisRows(rows);
    const addRows = selectedRows.filter((row) => (
      (!row.motor || row.motor.deleted) && (row.proposedMotor || row.motor)
    ));
    const editableRows = selectedRows.filter((row) => row.motor && !row.motor.deleted);
    const hasConfiguredAxes = axisMotors().some((motor) => !motor.deleted);
    const selectedAcProjectRows = selectedRows.filter(
      (row) => row.motor?.transport === 'ethercat' && !row.motor.deleted,
    );
    const selectedAcScanRows = selectedRows.filter((row) => Boolean(row.scanRow));
    const suspectedExistingRows = addRows.filter(
      (row) => row.scanRow && scanRowLikelyExistingMotor(row.scanRow),
    );
    const canAdd = addRows.length > 0 && selectedAcProjectRows.length === 0 &&
      suspectedExistingRows.length === 0;
    const canUpdateIdentity = (
      selectedRows.length === 1 &&
      selectedAcProjectRows.length === 1 &&
      selectedAcScanRows.length === 1
    ) || (
      selectedRows.length === 2 &&
      selectedAcProjectRows.length === 1 &&
      selectedAcScanRows.length === 1
    );
    const canSetModelProfile = selectedAcProjectRows.length > 0 &&
      selectedAcProjectRows.length === selectedRows.length;
    const writableAliasRows = selectedRows.filter((row) => (
      row.scanRow &&
      row.scanRow.slave_position !== null && row.scanRow.slave_position !== undefined &&
      row.scanRow.ethercat_alias !== null && row.scanRow.ethercat_alias !== undefined &&
      row.scanRow.vendor_id !== null && row.scanRow.vendor_id !== undefined &&
      row.scanRow.product_code !== null && row.scanRow.product_code !== undefined &&
      row.scanRow.serial_number !== null && row.scanRow.serial_number !== undefined
    ));
    const canWriteAlias = selectedRows.length === 1 && writableAliasRows.length === 1 &&
      !pendingAliasWrite;
    const canDelete = editableRows.length > 0;
    const canToggle = editableRows.length > 0;
    const canSort = hasConfiguredAxes;
    const changed = hasAnyConfigChanges();
    const recoveryMessage = acHardwareRecoveryMessage();
    const identityError = acHardwareIdentityErrorMessage();
    const identityApplyBlockMessage = acHardwareApplyBlockMessage();
    const modelApplyBlockMessage = modelProfileApplyBlockMessage();
    const applyBlockMessage = modelApplyBlockMessage || identityApplyBlockMessage;
    const alreadyApplied = selectedMotorConfigAlreadyApplied();
    const canAttemptApply = hasConfiguredAxes && !alreadyApplied && !changed;
    onIdentityStatusChange?.(motionControlBlockMessage());

    if (el.addAxisButton) el.addAxisButton.disabled = !canAdd;
    if (el.updateAxisIdentityButton) el.updateAxisIdentityButton.disabled = !canUpdateIdentity;
    if (el.setAxisModelProfileButton) {
      el.setAxisModelProfileButton.disabled = !canSetModelProfile;
      el.setAxisModelProfileButton.title = canSetModelProfile
        ? '선택한 AC 서보 축의 명판 모델을 확인하고 현재 운전 프로필에 연결합니다.'
        : '모델을 확인할 프로젝트 AC 서보 축을 하나 이상 선택하세요.';
    }
    if (el.writeEthercatAliasButton) el.writeEthercatAliasButton.disabled = !canWriteAlias;
    if (el.deleteAxisButton) el.deleteAxisButton.disabled = !canDelete;
    if (el.sortAxisButton) el.sortAxisButton.disabled = !canSort;
    if (el.toggleAxisButton) {
      el.toggleAxisButton.disabled = !canToggle;
      if (editableRows.length > 0) {
        const shouldTurnOn = editableRows.some((row) => !row.motor.enabled);
        el.toggleAxisButton.textContent = shouldTurnOn
          ? '선택 축 사용'
          : '선택 축 미사용';
      } else {
        el.toggleAxisButton.textContent = '선택 축 사용상태 변경';
      }
    }
    if (el.saveAxisConfigButton) el.saveAxisConfigButton.disabled = !changed;
    if (el.deleteMotorConfigButton) {
      el.deleteMotorConfigButton.disabled = !motorConfigFilePath;
      el.deleteMotorConfigButton.title = motorConfigFilePath
        ? '현재 프로젝트의 활성 모터축 설정 파일을 프로젝트 휴지통으로 이동합니다. 실행 중인 장비 설정은 바뀌지 않습니다.'
        : '현재 프로젝트에 삭제할 모터축 설정 파일이 없습니다.';
    }
    if (el.applyAxisConfigButton) el.applyAxisConfigButton.disabled = !canAttemptApply;
    if (el.applyAxisConfigButton) {
      el.applyAxisConfigButton.textContent = alreadyApplied
        ? '설정 적용 완료'
        : '설정 적용 및 재시작';
      el.applyAxisConfigButton.title = canAttemptApply
        ? applyBlockMessage || recoveryMessage
          || '저장된 현재 프로젝트 설정을 실행 시스템에 적용합니다.'
        : alreadyApplied
          ? '현재 프로젝트의 저장 설정이 실행 시스템에 이미 적용됐습니다.'
          : changed
            ? '변경 내용을 먼저 저장하세요.'
            : applyBlockMessage || '적용할 프로젝트 축 설정이 없습니다.';
    }
    if (el.addAxisButton) {
      el.addAxisButton.title = canAdd
        ? '선택한 검색 축을 편집 초안에 추가합니다. 파일은 아직 변경되지 않습니다.'
        : '프로젝트에 없는 검색 축을 선택하세요.';
    }
    if (el.updateAxisIdentityButton) {
      el.updateAxisIdentityButton.title = canUpdateIdentity
        ? '검색된 실제 연결정보를 선택한 프로젝트 축의 편집 초안에 반영합니다.'
        : '연결정보를 반영할 프로젝트 AC 서보 축과 검색 축을 선택하세요.';
    }
    if (el.saveAxisConfigButton) {
      el.saveAxisConfigButton.title = changed
        ? '현재 편집 초안을 프로젝트 모터축 설정 파일에 저장합니다.'
        : '저장할 변경 내용이 없습니다.';
    }
    if (el.configState) {
      el.configState.textContent = changed
        ? '저장 필요'
        : configApplyPending
          ? '적용 필요'
          : '설정 저장됨';
    }

    renderAxisWorkflowStatus({
      rows,
      hasConfiguredAxes,
      changed,
      recoveryMessage,
      identityError,
    });
  }

  function renderAxisWorkflowStatus({
    rows,
    hasConfiguredAxes,
    changed,
    recoveryMessage,
    identityError,
  }) {
    const ethercatScan = latestScan?.ethercat_scan;
    const hasAcScan = Boolean(
      ethercatScan && !ethercatScan.skipped && Array.isArray(ethercatScan.slaves),
    );
    const errorSlaves = hasAcScan
      ? ethercatScan.slaves.filter(
        (item) => String(item.device_state || '').toUpperCase().includes('ERROR'),
      )
      : [];
    const connectionCandidateCount = rows.filter(
      (row) => !row.motor && row.associationCandidate,
    ).length;
    const scanOnlyCount = rows.filter(
      (row) => !row.motor && !row.associationCandidate && (row.scanRow || row.scanDevice),
    ).length;
    let state = '정상';
    let detail = '프로젝트 저장값과 검색된 실제 연결값이 일치합니다.';
    let next = '다음 작업: 모터 동작 상태를 확인하세요.';
    let stateCode = 'normal';

    if (pendingAliasWrite) {
      state = '전원 재투입 및 재검색 필요';
      detail = acHardwareIdentityErrorMessage();
      next = '다음 작업: 서보 드라이버 제어 전원을 재투입한 뒤 전체 모터 검색';
      stateCode = 'warning';
    } else if (changed) {
      state = '변경 내용 저장 필요';
      detail = '화면의 편집 내용은 아직 프로젝트 파일에 반영되지 않았습니다.';
      next = '다음 작업: 변경 내용 저장';
      stateCode = 'warning';
    } else if (recoveryMessage) {
      state = '설정 적용 필요';
      detail = recoveryMessage;
      next = '다음 작업: 설정 적용 및 재시작';
      stateCode = 'error';
    } else if (!hasConfiguredAxes && !hasAcScan && !latestScan?.dynamixel_scan) {
      state = '검색 필요';
      detail = '현재 프로젝트에 등록된 축이 없거나 실제 모터 검색을 하지 않았습니다.';
      next = '다음 작업: 전체 모터 검색';
      stateCode = 'notice';
    } else if (errorSlaves.length > 0) {
      state = 'EtherCAT 통신 오류';
      detail = `오류 상태 Slave ${errorSlaves.map(
        (item) => formatInt(item.slave_position),
      ).join(', ')} · ${errorSlaves.map((item) => item.device_state).join(', ')}`;
      next = identityError
        ? '다음 작업: 표의 실제값과 프로젝트 저장값을 확인하세요.'
        : '다음 작업: EtherCAT 상태를 확인하세요.';
      stateCode = 'error';
    } else if (connectionCandidateCount > 0) {
      state = '기존 축 연결 확인 필요';
      detail = `Serial이 없는 기존 축과 같은 위치에서 ${formatInt(connectionCandidateCount)}축이 검색됐습니다.`;
      next = '다음 작업: 기존 축과 같은 Slave의 검색 장비를 하나씩 선택하고 선택 축 연결정보 변경';
      stateCode = 'warning';
    } else if (identityError) {
      const needsScan = identityError.includes('검색이 필요');
      state = needsScan ? '검색 필요' : '연결정보 확인 필요';
      detail = identityError;
      next = needsScan
        ? '다음 작업: 전체 모터 검색'
        : '다음 작업: 차이가 있는 축을 선택하고 연결정보 반영';
      stateCode = 'warning';
    } else if (scanOnlyCount > 0) {
      state = '신규 축 확인 필요';
      detail = `검색되었지만 프로젝트에 없는 모터가 ${formatInt(scanOnlyCount)}축 있습니다.`;
      next = '다음 작업: 신규 축을 선택하고 선택 축 추가';
      stateCode = 'warning';
    } else if (configApplyPending) {
      state = '설정 적용 필요';
      detail = '프로젝트 파일은 저장됐지만 실행 시스템에는 아직 반영되지 않았습니다.';
      next = '다음 작업: 설정 적용 및 재시작';
      stateCode = 'warning';
    }

    if (el.axisWorkflowStatus) el.axisWorkflowStatus.dataset.state = stateCode;
    if (el.axisWorkflowState) el.axisWorkflowState.textContent = state;
    if (el.axisWorkflowDetail) el.axisWorkflowDetail.textContent = detail;
    if (el.axisWorkflowNext) el.axisWorkflowNext.textContent = next;
  }

  function renderAxisSettings() {
    const rows = axisRowsData();
    removeMissingSelectedAxisIds(rows);

    const configured = axisMotors().filter((motor) => !motor.deleted);
    const disabled = configured.filter((motor) => !motor.enabled);
    const connectionCandidates = rows.filter(
      (row) => !row.motor && row.associationCandidate,
    );
    const scanOnly = rows.filter(
      (row) => !row.motor && !row.associationCandidate && (row.scanRow || row.scanDevice),
    );
    const changed = hasAnyConfigChanges();
    const selectedCount = selectedAxisIds.size;

    if (el.axisSummary) {
      el.axisSummary.textContent = `설정 ${formatInt(configured.length)}축, 미사용 ${formatInt(disabled.length)}축, 연결 후보 ${formatInt(connectionCandidates.length)}축, 신규 ${formatInt(scanOnly.length)}축, 선택 ${formatInt(selectedCount)}축, ${changed ? '저장 필요' : '저장됨'}`;
    }

    if (el.axisRows) {
      const rowViews = rows.map((row) => {
          const selected = selectedAxisIds.has(row.id);
          const [settingText, settingClass] = settingStatus(row);
          const [scanText, scanClass] = scanStatus(row);
          const [runtimeText, runtimeClass] = runtimeStatus(row);
          const motor = row.motor || row.proposedMotor;
          const typeText = row.motor
            ? motorKind(row.motor)
            : row.scanDevice
              ? '다이나믹셀'
              : row.scanRow
                ? 'AC 서보'
                : motorKind(motor);
          const name = rowNameRaw(row);
          const onOff = row.motor && !row.motor.deleted
            ? (row.motor.enabled ? '사용' : '미사용')
            : '-';
          const idText = axisIdLabel(row);
          const eepromView = acIdentityView(row, 'eeprom_alias');
          const rotaryView = acIdentityView(row, 'rotary_alias');
          const slaveView = acIdentityView(row, 'slave_position');
          const axisValue = rowAxisRaw(row);
          const editable = !row.associationCandidate && !(row.motor && row.motor.deleted);
          const identity = rowDeviceIdentity(row);
          const modelProfile = rowModelProfileView(row);
          const driverModel = rowDriverModelRaw(row);
          const connection = rowConnectionIdentity(row);
          const mapping = rowMappingView(row);
          const drive = rowDriveView(row);
          const permission = rowMotionPermissionView(row, mapping, drive);
          const motionRun = rowMotionRunView(row, mapping, permission);
          const overall = rowOverallView(row, mapping, drive, permission, motionRun);
          const showAcServoControls = rowMotorType(row) === 'ac_servo' &&
            Boolean(row.motor && !row.motor.deleted && row.motor.enabled);
          return {
            row,
            selected,
            settingText,
            settingClass,
            scanText,
            scanClass,
            runtimeText,
            runtimeClass,
            typeText,
            name,
            idText,
            projectAlias: projectAliasValue(row),
            directScanAlias: directScanAliasValue(row),
            eepromAlias: eepromView.text,
            eepromMismatch: eepromView.mismatch,
            rotaryAlias: rotaryView.text,
            rotaryMismatch: rotaryView.mismatch,
            slavePosition: slaveView.text,
            slaveMismatch: slaveView.mismatch,
            axisValue,
            editable,
            onOff,
            identity,
            modelProfile,
            driverModel,
            connection,
            mapping,
            drive,
            permission,
            motionRun,
            overall,
            showAcServoControls,
          };
        });
      const renderSignature = JSON.stringify(rowViews.map((view) => ({
        id: view.row.id,
        selected: view.selected,
        axis: view.axisValue,
        idText: view.idText,
        projectAlias: view.projectAlias,
        directScanAlias: view.directScanAlias,
        eepromAlias: view.eepromAlias,
        eepromMismatch: view.eepromMismatch,
        rotaryAlias: view.rotaryAlias,
        rotaryMismatch: view.rotaryMismatch,
        slavePosition: view.slavePosition,
        slaveMismatch: view.slaveMismatch,
        typeText: view.typeText,
        name: view.name,
        editable: view.editable,
        driver: driverLabel(view.row),
        settingText: view.settingText,
        settingClass: view.settingClass,
        scanText: view.scanText,
        scanClass: view.scanClass,
        runtimeText: view.runtimeText,
        runtimeClass: view.runtimeClass,
        onOff: view.onOff,
        identity: view.identity,
        modelProfile: view.modelProfile,
        driverModel: view.driverModel,
        connection: view.connection,
        mapping: view.mapping,
        drive: view.drive,
        permission: view.permission,
        motionRun: view.motionRun,
        overall: view.overall,
        showAcServoControls: view.showAcServoControls,
      })));

      if (renderSignature !== lastAxisRenderSignature) {
        lastAxisRenderSignature = renderSignature;
        el.axisRows.innerHTML = rows.length > 0
          ? rowViews.map((view) => {
          const row = view.row;
          const disabled = view.editable ? '' : ' disabled';
          return `
            <tr class="${view.selected ? 'selected-row' : ''}" data-axis-row="${escapeHtml(row.id)}">
              <td><input type="checkbox" data-axis-select="${escapeHtml(row.id)}"${view.selected ? ' checked' : ''}></td>
              <td class="axis-combined-cell">
                <input class="axis-edit-input axis-number-input mono" aria-label="축 번호" data-axis-edit="axis" data-axis-row-id="${escapeHtml(row.id)}" type="number" min="0" step="1" value="${escapeHtml(view.axisValue ?? '')}"${disabled}>
                <input class="axis-edit-input axis-name-input" aria-label="축 이름" data-axis-edit="name" data-axis-row-id="${escapeHtml(row.id)}" value="${escapeHtml(view.name === '-' ? '' : view.name)}"${disabled}>
              </td>
              <td class="axis-status-stack">
                <strong>${displayText(view.identity.title)}</strong>
                <small>${displayText(view.identity.detail)}</small>
              </td>
              <td class="axis-status-stack">
                <strong>${displayText(view.modelProfile.title)}</strong>
                <small>${displayText(view.modelProfile.detail)}</small>
              </td>
              <td class="axis-status-stack mono"><strong>${displayText(view.connection.title)}</strong><small>${displayText(view.connection.detail)}</small></td>
              <td class="axis-status-stack">
                <span class="match-state ${escapeHtml(view.settingClass)}">${displayText(view.settingText)}</span>
                <small>${displayText(view.runtimeText)}</small>
              </td>
              <td class="axis-status-stack"><span class="match-state ${escapeHtml(view.mapping.className)}">${displayText(view.mapping.text)}</span><small>${displayText(view.mapping.detail)}</small></td>
              <td class="axis-status-stack">
                <span class="match-state ${escapeHtml(view.drive.className)}">${displayText(view.drive.text)}</span>
                <small>${displayText(view.drive.detail)}</small>
                ${view.showAcServoControls ? `
                  <div class="axis-inline-actions">
                    <button type="button" data-axis-servo-action="servo_on" data-axis-servo-index="${escapeHtml(view.axisValue ?? '')}">ON</button>
                    <button type="button" data-axis-servo-action="servo_off" data-axis-servo-index="${escapeHtml(view.axisValue ?? '')}">OFF</button>
                    <button type="button" data-axis-servo-action="fault_reset" data-axis-servo-index="${escapeHtml(view.axisValue ?? '')}">오류 초기화</button>
                  </div>
                ` : ''}
              </td>
              <td class="axis-status-stack"><span class="match-state ${escapeHtml(view.permission.className)}">${displayText(view.permission.text)}</span><small>${displayText(view.permission.detail)}</small></td>
              <td class="axis-status-stack"><span class="match-state ${escapeHtml(view.motionRun.className)}">${displayText(view.motionRun.text)}</span><small>${displayText(view.motionRun.detail)}</small></td>
              <td class="axis-status-stack"><span class="match-state ${escapeHtml(view.overall.className)}">${displayText(view.overall.text)}</span><small>${displayText(view.overall.detail)}</small></td>
            </tr>
          `;
        }).join('')
          : '<tr><td colspan="11" class="empty">설정 파일을 불러오거나 모터 스캔을 실행하세요</td></tr>';
      }
      renderMotorReadiness(rows, rowViews, changed);
      renderMotorTypeStatus(rowViews, changed);
    }

    renderAxisButtons(rows);
    renderAxisSettingsTabs();
    renderMotorConfigTable();
    renderMotorConfigRawText();
  }

  function applyMotorConfigPayload(payload) {
    savedRegistry = normalizeAxisRegistry(payload.registry || {});
    axisConfig = clone(savedRegistry);
    configApplyPending = false;
    identityUpdatePending = false;
    rowEditDrafts = new Map();
    configTableDrafts = new Map();
    motorConfigRawText = String(payload.content || '');
    savedMotorConfigRawText = motorConfigRawText;
    motorConfigFilePath = String(payload.config_file || '');
    motorConfigRevision = String(payload.config_revision || '');
    motorConfigFileNameDraft = pathBasename(motorConfigFilePath);
    lastConfigTableRenderSignature = '';
    lastConfigRawTextRenderSignature = '';
    if (el.motorConfigState) {
      el.motorConfigState.textContent = payload.success === false
        ? uiMessage(payload.message, '설정 파일 불러오기 실패')
        : motorConfigFilePath
          ? '설정 파일 불러옴'
          : '저장된 설정 파일 없음';
    }
    renderAxisSettings();
    renderLatestState();
  }

  async function fetchRegistry(expectedToken = projectLoadToken) {
    expectedToken = normalizeProjectLoadToken(expectedToken, projectLoadToken);
    setStatusMessage('설정 파일 불러오는 중');
    if (el.reloadMotorConfigButton) el.reloadMotorConfigButton.disabled = true;
    if (el.deleteMotorConfigButton) el.deleteMotorConfigButton.disabled = true;
    try {
      const payload = await fetchMotorConfig();
      if (expectedToken !== projectLoadToken) return;
      applyMotorConfigPayload(payload);
      const message = payload.success === false
        ? uiMessage(payload.message, '설정 파일 불러오기 실패')
        : motorConfigFilePath
          ? `설정 파일 불러옴 ${new Date().toLocaleTimeString()}`
          : '현재 프로젝트에 저장된 모터축 설정 파일이 없습니다.';
      setStatusMessage(message);
      setAxisMessage(message);
    } catch (error) {
      if (error?.staleProjectResponse || expectedToken !== projectLoadToken) return;
      savedRegistry = normalizeAxisRegistry({});
      axisConfig = normalizeAxisRegistry({});
      configTableDrafts = new Map();
      motorConfigRawText = '';
      savedMotorConfigRawText = '';
      motorConfigFilePath = '';
      motorConfigRevision = '';
      lastConfigTableRenderSignature = '';
      lastConfigRawTextRenderSignature = '';
      setStatusMessage('설정 파일 불러오기 실패');
      setAxisMessage('설정 파일 불러오기 실패');
      renderAxisSettings();
    } finally {
      if (expectedToken === projectLoadToken && el.reloadMotorConfigButton) {
        el.reloadMotorConfigButton.disabled = false;
      }
    }
  }

  async function deleteCurrentMotorConfig() {
    if (!motorConfigFilePath) {
      setStatusMessage('삭제할 모터축 설정 파일 없음');
      setAxisMessage('현재 프로젝트에 삭제할 모터축 설정 파일이 없습니다.');
      renderAxisSettings();
      return false;
    }
    const fileName = pathBasename(motorConfigFilePath);
    const confirmed = await showConfirm(
      `${fileName} 파일을 현재 프로젝트의 휴지통으로 이동할까요?\n\n`
      + '프로젝트의 모터축 설정 목록에서는 제거됩니다.\n'
      + '현재 실행 중인 모터 제어 설정은 자동으로 변경되거나 재시작되지 않습니다.',
      { title: '모터축 설정 파일 삭제', confirmLabel: '휴지통으로 이동', tone: 'danger' },
    );
    if (!confirmed) {
      setAxisMessage('모터축 설정 파일 삭제 취소');
      return false;
    }

    const expectedToken = projectLoadToken;
    const button = el.deleteMotorConfigButton;
    const originalText = button?.textContent || '';
    if (button) {
      button.disabled = true;
      button.textContent = '휴지통 이동 중';
    }
    try {
      const payload = await deleteMotorConfig();
      if (expectedToken !== projectLoadToken) return false;
      if (!payload.success) {
        const message = uiMessage(payload.message, '모터축 설정 파일 삭제 실패');
        setStatusMessage(message);
        setAxisMessage(message);
        return false;
      }
      applyMotorConfigPayload(payload);
      const replacement = String(payload.replacement_active_file || '');
      const message = replacement
        ? `${fileName} 파일을 휴지통으로 이동하고 ${replacement} 파일을 불러왔습니다. 실행 설정은 변경되지 않았습니다.`
        : `${fileName} 파일을 휴지통으로 이동했습니다. 현재 프로젝트에 모터축 설정 파일이 없습니다. 실행 설정은 변경되지 않았습니다.`;
      setStatusMessage(message);
      setAxisMessage(message);
      await onProjectFilesChange?.();
      onWorkContextChange?.();
      return true;
    } catch (error) {
      if (error?.staleProjectResponse || expectedToken !== projectLoadToken) return false;
      const message = `모터축 설정 파일 삭제 실패: ${error?.message || error}`;
      setStatusMessage(message);
      setAxisMessage(message);
      return false;
    } finally {
      if (button) button.textContent = originalText;
      if (expectedToken === projectLoadToken) renderAxisSettings();
    }
  }

  async function loadProjectRegistry() {
    stopScanProgressPolling();
    if (operationProgress?.activeId().startsWith('scan:')) {
      operationProgress.close({ force: true });
    }
    projectLoadToken += 1;
    const expectedToken = projectLoadToken;
    savedRegistry = normalizeAxisRegistry({});
    axisConfig = normalizeAxisRegistry({});
    latestScan = null;
    selectedAxisIds = new Set();
    selectedConfigMotorId = '';
    rowEditDrafts = new Map();
    configTableDrafts = new Map();
    configApplyPending = false;
    identityUpdatePending = false;
    pendingAliasWrite = null;
    motorConfigRawText = '';
    savedMotorConfigRawText = '';
    motorConfigFilePath = '';
    motorConfigRevision = '';
    motorConfigFileNameDraft = '';
    lastAxisRenderSignature = '';
    lastConfigTableRenderSignature = '';
    lastConfigRawTextRenderSignature = '';
    renderAxisSettings();
    if (el.scanResult) el.scanResult.textContent = '새 프로젝트에서 아직 검색하지 않았습니다';
    if (el.scanAllResult) {
      el.scanAllResult.textContent = '검색 전 · 새로 발견된 축은 자동으로 선택됩니다';
    }
    if (el.dynamixelScanResult) {
      el.dynamixelScanResult.textContent = '새 프로젝트에서 아직 검색하지 않았습니다';
    }
    await fetchRegistry(expectedToken);
  }

  function axisOrderErrorMessage() {
    const motors = axisMotors().filter((motor) => !motor.deleted);
    if (motors.length === 0) return '';

    const axes = motors.map((motor) => motorAxisValue(motor));
    const invalidIndex = axes.findIndex((axis) => (
      axis === null ||
      axis === undefined ||
      !Number.isInteger(axis) ||
      axis < 0
    ));
    if (invalidIndex >= 0) {
      const motor = motors[invalidIndex];
      return `축 번호가 없는 축이 있습니다: ${registryMotorLabel(motor)}. 축 번호 정렬을 먼저 실행하세요.`;
    }

    const counts = new Map();
    axes.forEach((axis) => counts.set(axis, (counts.get(axis) || 0) + 1));
    const duplicate = [...counts.entries()].find(([, count]) => count > 1);
    if (duplicate) {
      return `축 번호 ${formatInt(duplicate[0])} 값이 중복되어 있습니다. 축 번호 정렬을 먼저 실행하세요.`;
    }

    const missing = [];
    for (let index = 0; index < motors.length; index += 1) {
      if (!counts.has(index)) missing.push(index);
    }
    if (missing.length > 0) {
      const current = axes.slice().sort((a, b) => a - b).map(formatInt).join(', ');
      return `축 번호가 0부터 연속으로 정렬되어 있지 않습니다. 현재 축 번호: ${current}. 축 번호 정렬을 먼저 실행하세요.`;
    }

    const acMotors = motors.filter((motor) => motor.transport === 'ethercat');
    const nonzeroAliases = new Map();
    const zeroAliasPositions = new Map();
    for (const motor of acMotors) {
      const alias = Number(firstDefined(motor.identity?.ethercat_alias, motor.config?.alias, 0));
      const position = Number(motor.config?.position ?? 0);
      const target = alias === 0 ? zeroAliasPositions : nonzeroAliases;
      const key = alias === 0 ? position : alias;
      if (target.has(key)) {
        return alias === 0
          ? `EEPROM Alias가 0인 AC 서보의 Slave Position ${formatInt(position)} 값이 중복되어 있습니다.`
          : `AC 서보의 EEPROM Alias ${formatInt(alias)} 값이 중복되어 있습니다.`;
      }
      target.set(key, motor);
    }

    return '';
  }

  function acHardwareIdentityErrorMessage() {
    if (selectedMotorConfigAlreadyApplied()) return '';
    if (pendingAliasWrite) {
      return `Slave Position ${formatInt(pendingAliasWrite.slavePosition)}의 EEPROM Alias를 `
        + `${formatInt(pendingAliasWrite.newAlias)}(으)로 기록했습니다. `
        + '서보 드라이버 제어 전원을 재투입한 뒤 전체 모터 검색이 필요합니다.';
    }

    const recoveryMessage = acHardwareRecoveryMessage();
    if (recoveryMessage) return recoveryMessage;

    const enabledAcMotors = activeAxisMotors().filter(
      (item) => item.transport === 'ethercat' && item.enabled,
    );
    for (const motor of activeAxisMotors().filter((item) => item.enabled)) {
      const runtime = runtimeMotorForRegistryMotor(motor);
      if (!runtime || runtime.controller_index === null ||
          runtime.controller_index === undefined) continue;
      const savedAxis = motorAxisValue(motor);
      if (savedAxis !== null && Number(savedAxis) !== Number(runtime.controller_index)) {
        return `프로젝트 Control Index ${formatInt(savedAxis)}와 실행 중인 Control Index ${formatInt(runtime.controller_index)}가 다릅니다.`;
      }
    }

    const runtime = runtimeMotors();
    if (enabledAcMotors.length > 0 && enabledAcMotors.every((motor) => (
      runtimeMotorConfirmsRegistryMotor(motor, runtimeMotorForRegistryMotor(motor, runtime))
    ))) return '';

    const scan = latestScan?.ethercat_scan;
    if (enabledAcMotors.length > 0 &&
        (!scan || scan.skipped || !Array.isArray(scan.slaves))) {
      return 'AC 서보 저장값과 실제값 확인을 위해 전체 모터 검색이 필요합니다.';
    }
    if (!scan || scan.skipped || !Array.isArray(scan.slaves)) return '';
    const scannedAliases = new Map();
    scan.slaves.forEach((slave) => {
      const alias = Number(slave.ethercat_alias ?? 0);
      if (alias !== 0) scannedAliases.set(alias, (scannedAliases.get(alias) || 0) + 1);
    });
    const duplicateAlias = [...scannedAliases.entries()].find(([, count]) => count > 1);
    if (duplicateAlias) return `검색된 EEPROM Alias ${formatInt(duplicateAlias[0])} 값이 중복되어 적용할 수 없습니다.`;

    for (const motor of activeAxisMotors().filter(
      (item) => item.transport === 'ethercat' && item.enabled,
    )) {
      const scanRow = scan.slaves.find((item) => scanRowMatchesRegistryMotor(item, motor));
      if (!scanRow) {
        return `Control Index ${formatInt(motorAxisValue(motor))}의 Slave Position을 검색 결과에서 찾지 못했습니다.`;
      }
      const expectedRotary = motor.identity?.rotary_alias;
      const expectedSlave = motor.identity?.slave_position;
      if (expectedSlave === null || expectedSlave === undefined ||
          scanRow.slave_position === null || scanRow.slave_position === undefined) {
        return `Control Index ${formatInt(motorAxisValue(motor))}의 연결정보 확인 및 업데이트가 필요합니다.`;
      }
      if (assignedAlias(expectedRotary) && assignedAlias(scanRow.rotary_alias) &&
          Number(expectedRotary) !== Number(scanRow.rotary_alias)) {
        return `Control Index ${formatInt(motorAxisValue(motor))}의 Station Alias가 프로젝트와 다릅니다.`;
      }
      if (Number(expectedSlave) !== Number(scanRow.slave_position)) {
        return `Control Index ${formatInt(motorAxisValue(motor))}의 Slave Position이 프로젝트와 다릅니다.`;
      }
      if ((motor.config?.vendor_id !== null && motor.config?.vendor_id !== undefined &&
           scanRow.vendor_id !== null && scanRow.vendor_id !== undefined &&
           Number(motor.config.vendor_id) !== Number(scanRow.vendor_id)) ||
          (motor.config?.product_id !== null && motor.config?.product_id !== undefined &&
           scanRow.product_code !== null && scanRow.product_code !== undefined &&
           Number(motor.config.product_id) !== Number(scanRow.product_code))) {
        return `Control Index ${formatInt(motorAxisValue(motor))}의 드라이버 모델 정보가 프로젝트와 다릅니다.`;
      }
    }
    return '';
  }

  function acHardwareRecoveryMessage() {
    if (pendingAliasWrite) return '';
    const scan = latestScan?.ethercat_scan;
    if (!scan || scan.skipped || !Array.isArray(scan.slaves)) return '';
    const enabledAcMotors = activeAxisMotors().filter(
      (item) => item.transport === 'ethercat' && item.enabled,
    );
    if (enabledAcMotors.length === 0) return '';

    let unavailableCount = 0;
    for (const motor of enabledAcMotors) {
      const scanRow = scan.slaves.find((item) => scanRowMatchesRegistryMotor(item, motor));
      if (!scanRow) return '';
      const expectedSlave = motor.identity?.slave_position;
      if (expectedSlave === null || expectedSlave === undefined ||
          scanRow.slave_position === null || scanRow.slave_position === undefined ||
          Number(expectedSlave) !== Number(scanRow.slave_position)) return '';
      if ((motor.config?.vendor_id !== null && motor.config?.vendor_id !== undefined &&
           Number(motor.config.vendor_id) !== Number(scanRow.vendor_id)) ||
          (motor.config?.product_id !== null && motor.config?.product_id !== undefined &&
           Number(motor.config.product_id) !== Number(scanRow.product_code))) return '';

      const expectedStation = motor.identity?.rotary_alias;
      const observedStation = scanRow.rotary_alias;
      if (assignedAlias(expectedStation) && assignedAlias(observedStation)) {
        if (Number(expectedStation) !== Number(observedStation)) return '';
        continue;
      }
      if (!assignedAlias(expectedStation)) continue;
      const deviceState = String(scanRow.device_state || '').toUpperCase();
      if (observedStation !== null && observedStation !== undefined ||
          !deviceState.includes('ERROR') || !scanRow.rotary_alias_error) return '';
      unavailableCount += 1;
    }
    if (unavailableCount === 0) return '';
    return `EEPROM Alias 변경 후 ${formatInt(unavailableCount)}축이 EtherCAT 오류 상태라 `
      + 'Station Alias를 읽지 못했습니다. 저장된 새 설정을 적용·재시작한 뒤 다시 검색해야 합니다.';
  }

  async function saveAxisConfig() {
    if (!hasAnyConfigChanges()) {
      setStatusMessage('저장할 축 설정 변경 없음');
      setAxisMessage('저장할 축 설정 변경 없음');
      return false;
    }

    const axisError = hasAxisChanges() ? axisOrderErrorMessage() : '';
    if (axisError) {
      window.alert(axisError);
      setStatusMessage('축 설정 저장 중단');
      setAxisMessage(axisError);
      renderAxisSettings();
      return false;
    }
    const saveButton = el.saveAxisConfigButton;
    const originalText = saveButton ? saveButton.textContent : '';
    if (saveButton) {
      saveButton.disabled = true;
      saveButton.textContent = '저장 중';
    }
    setStatusMessage('축 설정 저장 중');
    setAxisMessage('축 설정 저장 중');

    try {
      if (hasConfigTableDrafts()) {
        setStatusMessage('표 업데이트 필요');
        setAxisMessage('설정 파일 저장 전 표 변경값을 초안에 먼저 반영하세요.');
        return false;
      }
      const fileName = normalizedMotorConfigFileName() || pathBasename(motorConfigFilePath);
      const payload = await saveMotorConfig(
        hasMotorConfigTableSaveChanges()
          ? {
            content: motorConfigRawText,
            file_name: fileName,
            base_revision: motorConfigRevision,
          }
          : {
            registry: saveableAxisRegistry(axisConfig),
            file_name: fileName,
            base_revision: motorConfigRevision,
          },
      );
      if (!payload.success) {
        const message = uiMessage(payload.message, '축 설정 저장 실패');
        setStatusMessage(message);
        setAxisMessage(message);
        return false;
      }
      applyMotorConfigPayload(payload);
      configApplyPending = true;
      setStatusMessage('축 설정 저장됨');
      const modelWarning = modelProfileApplyBlockMessage();
      setAxisMessage(
        modelWarning
          ? `프로젝트 축 목록 저장됨 · ${modelWarning}`
          : '프로젝트 축 목록 저장됨. 실제 반영은 4단계의 설정 적용 및 재시작을 눌러야 합니다.',
        Boolean(modelWarning),
      );
      await onProjectFilesChange?.();
      return true;
    } catch (error) {
      const message = `축 설정 저장 실패: ${error?.message || error}`;
      setStatusMessage(message);
      setAxisMessage(message);
      return false;
    } finally {
      if (saveButton) {
        saveButton.textContent = originalText;
        saveButton.disabled = !hasAnyConfigChanges();
      }
      renderAxisSettings();
    }
  }

  async function applyConfigRestart() {
    if (hasAnyConfigChanges()) {
      setAxisMessage('저장하지 않은 축 설정이 있습니다. 먼저 변경 내용 저장을 누르세요.');
      renderAxisSettings();
      return false;
    }
    if (!axisMotors().some((motor) => !motor.deleted)) {
      setAxisMessage('설정 적용할 축이 없습니다.');
      renderAxisSettings();
      return false;
    }
    const recoveryMessage = acHardwareRecoveryMessage();
    const modelApplyBlockMessage = modelProfileApplyBlockMessage();
    const identityApplyBlockMessage = acHardwareApplyBlockMessage();
    const applyBlockMessage = modelApplyBlockMessage
      || (identityApplyBlockMessage && !recoveryMessage ? identityApplyBlockMessage : '');
    if (applyBlockMessage) {
      window.alert(applyBlockMessage);
      setAxisMessage(applyBlockMessage, true);
      renderAxisSettings();
      return false;
    }

    const recoveryWarning = recoveryMessage
      ? `복구 적용 안내:\n${recoveryMessage}\n\n`
      : '';
    const confirmed = await showConfirm(
      recoveryWarning
      + '주의: 설정 적용 중 motor_manager_node를 재시작합니다.\n\n'
      + '재시작 중에는 AC 서보 / 다이나믹셀 통신이 잠시 끊기거나 재초기화될 수 있습니다.\n'
      + '현재 서보가 부하를 잡고 있는 축은 순간적으로 토크가 해제되어 부하가 풀릴 수 있습니다.\n'
      + '이때 중력, 외력, 기구 하중 때문에 의도하지 않은 움직임이 발생할 수 있습니다.\n\n'
      + '기구를 안전하게 지지하고, 작업자 접근을 막고, 움직여도 위험하지 않은 상태에서만 진행하세요.\n'
      + '웹 연결은 잠깐 끊긴 뒤 자동으로 다시 연결됩니다.\n\n'
      + '위 위험을 확인했고 설정을 적용하기 위해 노드를 재시작할까요?',
      { title: '설정 적용·재시작', confirmLabel: '적용·재시작', tone: 'danger' },
    );
    if (!confirmed) {
      setAxisMessage('설정 적용 취소');
      return false;
    }

    const applyButton = el.applyAxisConfigButton;
    const originalText = applyButton ? applyButton.textContent : '';
    if (applyButton) {
      applyButton.disabled = true;
      applyButton.textContent = '재시작 중';
    }

    setStatusMessage('설정 반영 중');
    setAxisMessage('설정 반영 중. 웹 연결이 잠시 끊겨도 이 화면에서 자동 재연결을 기다립니다.');
    onConfigApplyStart?.();
    try {
      const payload = await applyMotorConfig();
      if (!payload.success) {
        const message = uiMessage(payload.message, '설정 반영 실패');
        setStatusMessage(message);
        setAxisMessage(message);
        onConfigApplyComplete?.();
        return false;
      }
      configApplyPending = false;
      latestScan = null;
      renderAxisSettings();
      return true;
    } catch (error) {
      if (error instanceof TypeError) {
        latestScan = null;
        setStatusMessage('웹 연결 재시작 중');
        setAxisMessage('웹 연결이 끊겼습니다. 재연결 후 모든 모터의 서보/토크가 켜질 때까지 기다립니다.');
        return true;
      }
      setStatusMessage('설정 반영 실패');
      setAxisMessage('설정 반영 실패');
      onConfigApplyComplete?.();
      return false;
    } finally {
      if (applyButton) {
        applyButton.textContent = originalText;
        applyButton.disabled = !axisMotors().some((motor) => !motor.deleted) || hasAnyConfigChanges();
      }
    }
  }

  function addSelectedAxis() {
    const rows = selectedAxisRows();
    if (rows.length === 0) {
      setAxisMessage('축을 먼저 선택하세요');
      return 0;
    }

    const nextSelectedIds = new Set();
    let changedCount = 0;
    let skippedCount = 0;

    rows.forEach((row) => {
      if (row.motor && row.motor.deleted) {
        const restored = normalizeMotor({
          ...row.motor,
          deleted: false,
          enabled: true,
          hidden: false,
        });
        upsertMotorInRegistry(axisConfig, restored);
        nextSelectedIds.add(restored.id);
        changedCount += 1;
        return;
      }
      if (row.motor || !row.proposedMotor) {
        skippedCount += 1;
        return;
      }
      const proposed = motorWithRowDraft(row.proposedMotor, row);
      const axis = uniqueAxisForMotor(proposed);
      const motor = normalizeMotor({
        ...proposed,
        axis,
        config: {
          ...(proposed.config || {}),
          controller_index: axis,
        },
        enabled: true,
        hidden: false,
        deleted: false,
      });
      upsertMotorInRegistry(axisConfig, motor);
      rowEditDrafts.delete(row.id);
      nextSelectedIds.add(motor.id);
      changedCount += 1;
    });

    if (changedCount === 0) {
      setAxisMessage('추가할 축 정보가 없습니다');
      return 0;
    }
    selectedAxisIds = nextSelectedIds;
    const suffix = skippedCount > 0 ? `, 제외 ${formatInt(skippedCount)}축` : '';
    setAxisMessage(`선택 ${formatInt(changedCount)}축 추가 예정${suffix}`);
    renderAxisSettings();
    return changedCount;
  }

  function deleteSelectedAxis() {
    const rows = selectedAxisRows().filter((row) => row.motor && !row.motor.deleted);
    if (rows.length === 0) {
      setAxisMessage('삭제할 설정 축을 선택하세요');
      return;
    }

    const nextSelectedIds = new Set();
    let removedUnsavedCount = 0;
    let markedDeletedCount = 0;

    rows.forEach((row) => {
      const saved = savedMotorById(row.motor.id);
      if (!saved) {
        axisConfig.motors = axisConfig.motors.filter((motor) => motor.id !== row.motor.id);
        removedUnsavedCount += 1;
        return;
      }
      const deleted = normalizeMotor({
        ...row.motor,
        deleted: true,
        hidden: false,
      });
      upsertMotorInRegistry(axisConfig, deleted);
      nextSelectedIds.add(deleted.id);
      markedDeletedCount += 1;
    });

    selectedAxisIds = nextSelectedIds;
    const parts = [];
    if (markedDeletedCount > 0) parts.push(`삭제 예정 ${formatInt(markedDeletedCount)}축`);
    if (removedUnsavedCount > 0) parts.push(`추가 예정 제거 ${formatInt(removedUnsavedCount)}축`);
    setAxisMessage(parts.join(', '));
    renderAxisSettings();
  }

  function toggleSelectedAxis() {
    const rows = selectedAxisRows().filter((row) => row.motor && !row.motor.deleted);
    if (rows.length === 0) {
      setAxisMessage('사용 상태를 바꿀 설정 축을 선택하세요');
      return;
    }
    const shouldTurnOn = rows.some((row) => !row.motor.enabled);
    const nextSelectedIds = new Set();
    rows.forEach((row) => {
      const updated = normalizeMotor({
        ...row.motor,
        enabled: shouldTurnOn,
        hidden: false,
      });
      upsertMotorInRegistry(axisConfig, updated);
      nextSelectedIds.add(updated.id);
    });
    selectedAxisIds = nextSelectedIds;
    setAxisMessage(`선택 ${formatInt(rows.length)}축 ${shouldTurnOn ? '사용' : '미사용'} 예정`);
    renderAxisSettings();
  }

  async function setSelectedAxisModelProfile() {
    const rows = selectedAxisRows().filter(
      (row) => row.motor?.transport === 'ethercat' && !row.motor.deleted,
    );
    if (rows.length === 0 || rows.length !== selectedAxisRows().length) {
      setAxisMessage('모델을 확인할 프로젝트 AC 서보 축을 하나 이상 선택하세요.', true);
      return false;
    }
    const models = [...new Set(rows.map((row) => rowDriverModelRaw(row)).filter(Boolean))];
    const input = await showPrompt(
      `선택 ${formatInt(rows.length)}축에 적용할 서보 드라이버 명판 모델을 입력하세요.\n`
      + '실제 장치 식별값은 변경되지 않으며 각 축의 기존 운전 프로필 값은 유지됩니다.',
      {
        title: '모델·운전 프로필 설정',
        defaultValue: models.length === 1 && models[0] !== 'UNVERIFIED_MINAS' ? models[0] : '',
        confirmLabel: '확인',
      },
    );
    if (input === null) return false;
    const model = String(input).trim();
    if (!model) {
      setAxisMessage('명판 모델을 입력해야 합니다.', true);
      return false;
    }
    const confirmed = await showConfirm(
      `선택 ${formatInt(rows.length)}축의 실제 명판 모델을 "${model}"로 확인합니다.\n`
      + 'Vendor ID, Product Code, Revision Number, Serial Number, EEPROM Alias, '
      + 'Slave Position은 장비 검색값 그대로 유지됩니다.',
      {
        title: '명판 모델 확인',
        confirmLabel: '모델 확인 반영',
      },
    );
    if (!confirmed) return false;

    rows.forEach((row) => {
      upsertMotorInRegistry(axisConfig, editedMotor(row.motor, row, 'driver_model', model));
    });
    lastAxisRenderSignature = '';
    setAxisMessage(
      `선택 ${formatInt(rows.length)}축 모델 확인됨 · ${model} · 변경 내용 저장 필요`,
    );
    renderAxisSettings();
    return true;
  }

  async function updateSelectedAxisIdentity() {
    const selectedRows = selectedAxisRows();
    const projectRows = selectedRows.filter(
      (row) => row.motor?.transport === 'ethercat' && !row.motor.deleted,
    );
    const scanRows = selectedRows.filter((row) => Boolean(row.scanRow));
    if (projectRows.length !== 1 || scanRows.length !== 1 ||
        ![1, 2].includes(selectedRows.length)) {
      setAxisMessage(
        '연결값을 바꿀 프로젝트 AC 서보 축과 검색된 AC 서보 축을 하나씩 선택하세요.',
        true,
      );
      return;
    }

    const motor = projectRows[0].motor;
    const scanRow = scanRows[0].scanRow;
    const oldIdentity = motor.identity || {};
    const oldAlias = firstDefined(oldIdentity.ethercat_alias, motor.config?.alias);
    const changes = [
      ['EEPROM Alias', oldAlias, scanRow.ethercat_alias],
      ['Station Alias', oldIdentity.rotary_alias, scanRow.rotary_alias],
      ['Slave Position', oldIdentity.slave_position, scanRow.slave_position],
      ['Vendor ID', motor.config?.vendor_id, scanRow.vendor_id],
      ['Product ID', motor.config?.product_id, scanRow.product_code],
      ['Revision', oldIdentity.revision_number, scanRow.revision_number],
      ['Serial Number', oldIdentity.serial_number, scanRow.serial_number],
      ['SII 장치명', oldIdentity.sii_device_name, scanRow.sii_device_name],
    ];
    const changeText = changes.map(([label, before, after]) => (
      `${label}: ${before ?? '미등록'} → ${after ?? '확인 불가'}`
    )).join('\n');
    const confirmed = await showConfirm(
      `Control Index ${formatInt(motorAxisValue(motor))}의 연결값을 변경합니다.\n\n`
      + `${changeText}\n\n`
      + '이 검색 장비가 프로젝트의 해당 축이 맞는지 확인했습니까?\n'
      + '확인 후에도 변경 내용 저장을 눌러야 프로젝트 파일에 반영됩니다.',
      { title: 'AC Servo 연결정보 반영', confirmLabel: '연결정보 반영', tone: 'warning' },
    );
    if (!confirmed) {
      setAxisMessage('연결정보 반영 취소');
      return;
    }

    const eepromAlias = scanRow.ethercat_alias ?? 0;
    const catalogModel = verifiedAcServoModel(scanRow);
    const updated = normalizeMotor({
      ...motor,
      identity: {
        ...oldIdentity,
        ethercat_alias: eepromAlias,
        rotary_alias: scanRow.rotary_alias ?? null,
        slave_position: scanRow.slave_position ?? null,
        identity_source: scanRow.identity_source || 'physical_sii',
        vendor_id: scanRow.vendor_id ?? null,
        product_code: scanRow.product_code ?? null,
        revision_number: scanRow.revision_number ?? null,
        serial_number: scanRow.serial_number ?? null,
        sii_order_number: scanRow.sii_order_number || scanRow.order_number || '',
        sii_device_name: scanRow.sii_device_name || scanRow.device_name || '',
      },
      profile: catalogModel ? {
        ...(motor.profile || {}),
        driver_model: catalogModel,
        model_confirmed: true,
        model_source: 'verified_catalog',
      } : motor.profile,
      config: {
        ...(motor.config || {}),
        alias: eepromAlias,
        position: Number(eepromAlias) === 0 ? Number(scanRow.slave_position ?? 0) : 0,
        vendor_id: scanRow.vendor_id ?? motor.config?.vendor_id,
        product_id: scanRow.product_code ?? motor.config?.product_id,
      },
    });
    upsertMotorInRegistry(axisConfig, updated);
    identityUpdatePending = true;
    selectedAxisIds = new Set([updated.id]);
    lastAxisRenderSignature = '';
    setAxisMessage(
      '연결정보를 편집 초안에 반영했습니다. 변경 내용 저장 전까지 프로젝트 파일은 바뀌지 않습니다.',
    );
    renderAxisSettings();
  }

  async function writeSelectedEthercatAlias() {
    const selectedRows = selectedAxisRows();
    if (selectedRows.length !== 1 || !selectedRows[0].scanRow) {
      setAxisMessage('EEPROM Alias를 쓸 검색된 AC 서보 축 하나를 선택하세요.', true);
      return false;
    }
    const scanRow = selectedRows[0].scanRow;
    const currentAlias = Number(scanRow.ethercat_alias);
    const input = await showPrompt(
      `Slave Position ${formatInt(scanRow.slave_position)}의 새 EEPROM Alias를 입력하세요.\n`
      + '범위: 0~65535 (0은 Alias 제거)',
      {
        title: 'EEPROM Alias 변경',
        defaultValue: String(currentAlias),
        confirmLabel: '다음',
        tone: 'danger',
      },
    );
    if (input === null) return false;
    const text = String(input).trim();
    const newAlias = /^0x[0-9a-f]+$/i.test(text)
      ? Number.parseInt(text.slice(2), 16)
      : Number(text);
    if (!Number.isInteger(newAlias) || newAlias < 0 || newAlias > 65535) {
      window.alert('EEPROM Alias는 0~65535 범위의 정수여야 합니다.');
      return false;
    }
    if (newAlias === currentAlias) {
      window.alert('새 EEPROM Alias가 현재 값과 같습니다.');
      return false;
    }
    const confirmed = await showConfirm(
      '실제 서보 드라이버의 SII EEPROM 값을 변경합니다.\n\n'
      + `Slave Position: ${formatInt(scanRow.slave_position)}\n`
      + `Serial Number: ${formatInt(scanRow.serial_number)}\n`
      + `EEPROM Alias: ${formatInt(currentAlias)} → ${formatInt(newAlias)}\n\n`
      + '프로젝트 파일은 자동 변경되지 않습니다.\n'
      + '쓰기 후 서보 드라이버 제어 전원을 재투입하고 다시 검색해야 합니다.\n'
      + '선택한 실제 장비와 새 Alias를 확인했습니까?',
      { title: 'EEPROM Alias 쓰기', confirmLabel: '실제 장비에 쓰기', tone: 'danger' },
    );
    if (!confirmed) {
      setAxisMessage('EEPROM Alias 쓰기 취소');
      return false;
    }

    const button = el.writeEthercatAliasButton;
    const originalText = button?.textContent || '';
    if (button) {
      button.disabled = true;
      button.textContent = 'EEPROM 쓰는 중';
    }
    try {
      const payload = await writeEthercatAlias({
        confirmed: true,
        slave_position: Number(scanRow.slave_position),
        new_alias: newAlias,
        expected: {
          ethercat_alias: currentAlias,
          vendor_id: Number(scanRow.vendor_id),
          product_code: Number(scanRow.product_code),
          serial_number: Number(scanRow.serial_number),
        },
      });
      if (!payload.success) {
        const message = uiMessage(payload.message, 'EEPROM Alias 쓰기 실패');
        window.alert(message);
        setAxisMessage(message, true);
        return false;
      }
      pendingAliasWrite = {
        slavePosition: Number(scanRow.slave_position),
        newAlias,
      };
      selectedAxisIds = new Set();
      const message = uiMessage(payload.message, 'EEPROM Alias 쓰기 완료');
      window.alert(message);
      setAxisMessage(message, true);
      renderAxisSettings();
      return true;
    } catch (error) {
      const message = `EEPROM Alias 쓰기 실패: ${error?.message || error}`;
      window.alert(message);
      setAxisMessage(message, true);
      return false;
    } finally {
      if (button) button.textContent = originalText;
    }
  }

  function sortAxisNumbers() {
    const motors = axisMotors()
      .filter((motor) => !motor.deleted)
      .slice()
      .sort((a, b) => {
        const axisDiff = Number(firstDefined(motorAxisValue(a), 9999)) -
          Number(firstDefined(motorAxisValue(b), 9999));
        if (axisDiff !== 0) return axisDiff;
        const typeDiff = motorKind(a).localeCompare(motorKind(b));
        if (typeDiff !== 0) return typeDiff;
        return String(a.id || '').localeCompare(String(b.id || ''));
      });

    if (motors.length === 0) {
      setAxisMessage('정렬할 설정 축이 없습니다');
      return;
    }

    let changedCount = 0;
    motors.forEach((motor, index) => {
      const currentAxis = motorAxisValue(motor);
      if (currentAxis === index) return;
      const updated = normalizeMotor({
        ...motor,
        axis: index,
        config: {
          ...(motor.config || {}),
          controller_index: index,
        },
      });
      upsertMotorInRegistry(axisConfig, updated);
      changedCount += 1;
    });

    if (changedCount === 0) {
      setAxisMessage('축 번호가 이미 0부터 연속으로 정렬되어 있습니다');
      renderAxisSettings();
      return;
    }

    lastAxisRenderSignature = '';
    setAxisMessage(`축 번호 정렬됨: ${formatInt(motors.length)}축을 0부터 연속 번호로 재배정`);
    renderAxisSettings();
  }

  function mergeAcServoScan(scan) {
    latestScan = {
      ...(latestScan || {}),
      ...(scan || {}),
      ethercat_scan: scan?.ethercat_scan,
      matching_rows: scan?.matching_rows,
      matching_summary: scan?.matching_summary,
      dynamixel_scan: latestScan?.dynamixel_scan,
    };
    return latestScan;
  }

  function mergeDynamixelScan(scan) {
    latestScan = {
      ...(latestScan || {}),
      ...(scan || {}),
      ethercat_scan: latestScan?.ethercat_scan,
      matching_rows: latestScan?.matching_rows,
      matching_summary: latestScan?.matching_summary,
      dynamixel_scan: scan?.dynamixel_scan,
    };
    return latestScan;
  }

  function getDiscoverySummary() {
    const ethercatScan = latestScan?.ethercat_scan;
    const dynamixelScan = latestScan?.dynamixel_scan;
    const ethercatScanned = Boolean(ethercatScan && !ethercatScan.skipped);
    const dynamixelScanned = Boolean(dynamixelScan && !dynamixelScan.skipped);
    const ethercatCount = ethercatScanned && Array.isArray(ethercatScan.slaves)
      ? ethercatScan.slaves.length
      : 0;
    const dynamixelCount = dynamixelScanned && Array.isArray(dynamixelScan.devices)
      ? dynamixelScan.devices.length
      : 0;
    const connectionSummary = latestScan?.connection_summary || {};
    const connectionRows = Array.isArray(latestScan?.connection_rows)
      ? latestScan.connection_rows
      : [];
    return {
      hasDirectScan: ethercatScanned || dynamixelScanned,
      ethercatScanned,
      dynamixelScanned,
      ethercatCount,
      dynamixelCount,
      connectedCount: Number(connectionSummary.online || 0),
      discoveredCount: ethercatCount + dynamixelCount,
      connectionSummary,
      connectionRows,
      scannedAt: Number(latestScan?.scanned_at || 0),
    };
  }

  function renderScan(scan) {
    latestScan = scan;
    if (pendingAliasWrite) {
      const observed = scan?.ethercat_scan?.slaves?.find(
        (item) => Number(item.slave_position) === Number(pendingAliasWrite.slavePosition),
      );
      if (observed && Number(observed.ethercat_alias) === Number(pendingAliasWrite.newAlias)) {
        pendingAliasWrite = null;
      }
    }
    renderAxisSettings();
    if (!el.scanResult) return;
    if (!scan) {
      el.scanResult.textContent = 'AC 서보 검색 실패';
      return;
    }
    const ethercatScan = scan.ethercat_scan || {};
    const slaves = Array.isArray(ethercatScan.slaves) ? ethercatScan.slaves : [];
    const resultState = ethercatScan.available && ethercatScan.complete
      ? '검색 완료'
      : ethercatScan.available
        ? '검색 부분 완료'
        : '검색 실패';
    el.scanResult.textContent = `${resultState} · ${formatInt(slaves.length)}축`;
  }

  function renderDynamixelScan(scan) {
    if (!el.dynamixelScanResult) return;
    const dynamixelScan = scan?.dynamixel_scan;
    if (!dynamixelScan) {
      el.dynamixelScanResult.textContent = '다이나믹셀 검색 안 함';
      return;
    }
    const devices = Array.isArray(dynamixelScan.devices) ? dynamixelScan.devices : [];
    const resultState = dynamixelScan.available && dynamixelScan.complete
      ? '검색 완료'
      : dynamixelScan.available
        ? '검색 부분 완료'
        : '검색 실패';
    el.dynamixelScanResult.textContent = `${resultState} · ${formatInt(devices.length)}개`;
  }

  async function scanMotors() {
    const originalText = beginScanRequest(el.scanButton, '검색 중');
    if (!originalText) return;
    const expectedToken = projectLoadToken;
    if (el.scanResult) el.scanResult.textContent = 'AC 서보 검색 중';
    if (!await openScanProgressPopup('scan:ac-servo', 'AC Servo 검색')) {
      finishScanRequest(el.scanButton, originalText);
      return;
    }
    try {
      const payload = await requestAcServoScan();
      if (expectedToken !== projectLoadToken) return;
      renderScan(mergeAcServoScan(payload.scan));
      const selection = autoSelectNewScanAxes();
      const identityError = acHardwareIdentityErrorMessage();
      setAxisMessage(selection.candidateCount > 0
        ? `AC 서보 검색 완료 · 기존 축 연결 후보 ${formatInt(selection.candidateCount)}축. `
          + '기존 프로젝트 축과 같은 Slave의 검색 장비를 하나씩 선택한 뒤 선택 축 연결정보 변경을 누르세요.'
        : identityError
          ? `${identityError} 기존 프로젝트 축과 검색 축을 선택해 연결정보 반영을 확인하세요.`
          : `AC 서보 검색 완료 · 신규 ${formatInt(selection.newCount)}축 자동 선택`,
      Boolean(identityError) || selection.candidateCount > 0);
      if (payload.motion_state) renderLatestState(payload.motion_state);
      el.scanButton.textContent = payload.success ? '직접 검색 완료' : '직접 검색 실패';
      await finishScanProgressPopup(payload.success, payload.success ? 'AC 서보 검색 완료' : 'AC 서보 검색 실패');
    } catch (error) {
      if (el.scanResult) el.scanResult.textContent = 'AC 서보 검색 실패';
      el.scanButton.textContent = '검색 실패';
      await finishScanProgressPopup(false, `AC 서보 검색 실패: ${error?.message || error}`);
    } finally {
      finishScanRequest(el.scanButton, originalText);
    }
  }

  async function scanDynamixel() {
    const originalText = beginScanRequest(el.dynamixelScanButton, '검색 중');
    if (!originalText) return;
    const expectedToken = projectLoadToken;
    if (el.dynamixelScanResult) el.dynamixelScanResult.textContent = '다이나믹셀 연결 확인 중';
    if (!await openScanProgressPopup('scan:dynamixel', 'Dynamixel 검색')) {
      finishScanRequest(el.dynamixelScanButton, originalText);
      return;
    }
    try {
      const payload = await requestDynamixelScan();
      if (expectedToken !== projectLoadToken) return;
      renderDynamixelScan(mergeDynamixelScan(payload.scan));
      const selection = autoSelectNewScanAxes();
      setAxisMessage(`다이나믹셀 검색 완료 · 신규 ${formatInt(selection.newCount)}축 자동 선택`);
      if (payload.motion_state) renderLatestState(payload.motion_state);
      el.dynamixelScanButton.textContent = payload.success ? '검색 완료' : '검색 실패';
      await finishScanProgressPopup(payload.success, payload.success ? 'Dynamixel 검색 완료' : 'Dynamixel 검색 실패');
    } catch (error) {
      if (el.dynamixelScanResult) el.dynamixelScanResult.textContent = '다이나믹셀 연결 확인 실패';
      el.dynamixelScanButton.textContent = '검색 실패';
      await finishScanProgressPopup(false, `Dynamixel 검색 실패: ${error?.message || error}`);
    } finally {
      finishScanRequest(el.dynamixelScanButton, originalText);
    }
  }

  async function scanAllMotors() {
    const originalText = beginScanRequest(el.scanAllButton, '전체 검색 중');
    if (!originalText) return;
    const expectedToken = projectLoadToken;
    if (el.scanAllResult) el.scanAllResult.textContent = 'AC 서보와 다이나믹셀을 검색하고 있습니다';
    if (!await openScanProgressPopup('scan:all', '전체 모터 검색')) {
      finishScanRequest(el.scanAllButton, originalText);
      return;
    }
    try {
      const payload = await requestMotorScan();
      if (expectedToken !== projectLoadToken) return;
      latestScan = payload.scan || null;
      renderScan(latestScan);
      renderDynamixelScan(latestScan);
      const selection = autoSelectNewScanAxes();
      const summary = getDiscoverySummary();
      const identityError = acHardwareIdentityErrorMessage();
      const scanComplete = payload.scan?.scan_complete === true;
      const scanPartial = payload.scan?.scan_outcome === 'partial';
      const dynamixelError = payload.scan?.dynamixel_scan?.error || '';
      if (el.scanAllResult) {
        el.scanAllResult.textContent = scanComplete
          ? `검색 완료 · AC 서보 ${formatInt(summary.ethercatCount)}축 · 다이나믹셀 ${formatInt(summary.dynamixelCount)}축 · 신규 ${formatInt(selection.newCount)}축`
          : scanPartial
            ? conciseMotorScanMessage(`부분 완료 · AC 서보 ${formatInt(summary.ethercatCount)}축 · 다이나믹셀 실패: ${dynamixelError || '직접 응답 없음'}`)
            : conciseMotorScanMessage(uiMessage(payload.message, '전체 모터 검색 실패'));
      }
      setAxisMessage(scanComplete
        ? selection.candidateCount > 0
          ? `기존 축 연결 후보 ${formatInt(selection.candidateCount)}축입니다. `
            + '기존 프로젝트 축과 같은 Slave의 검색 장비를 하나씩 선택한 뒤 선택 축 연결정보 변경을 누르세요.'
          : identityError
            ? `${identityError} 기존 프로젝트 축과 검색 축을 선택해 연결정보 반영을 확인하세요.`
            : `신규 ${formatInt(selection.newCount)}축을 자동 선택했습니다. 이름과 축 번호를 확인한 뒤 선택 축 추가를 누르세요.`
        : scanPartial
          ? conciseMotorScanMessage(`일부 검색만 완료됐습니다. ${dynamixelError || '연결되지 않은 모터 종류를 확인하세요.'}`)
          : conciseMotorScanMessage(uiMessage(payload.message, '전체 모터 검색 실패')),
      Boolean(identityError) || selection.candidateCount > 0 || !scanComplete);
      if (payload.motion_state) renderLatestState(payload.motion_state);
      el.scanAllButton.textContent = scanComplete ? '검색 완료' : (scanPartial ? '부분 완료' : '검색 실패');
      await finishScanProgressPopup(
        scanComplete,
        scanComplete ? '전체 모터 검색 완료' : (scanPartial ? '전체 모터 검색 부분 완료' : '전체 모터 검색 실패'),
        scanPartial ? 'partial' : '',
      );
    } catch (error) {
      if (el.scanAllResult) {
        el.scanAllResult.textContent = conciseMotorScanMessage(
          `전체 모터 검색 실패: ${error?.message || error}`,
        );
      }
      setAxisMessage('전체 모터 검색에 실패했습니다. 고급 종류별 검색으로 연결을 확인할 수 있습니다.');
      el.scanAllButton.textContent = '검색 실패';
      await finishScanProgressPopup(false, `전체 모터 검색 실패: ${error?.message || error}`);
    } finally {
      finishScanRequest(el.scanAllButton, originalText);
    }
  }

  function renderAfterDisplayModeChange() {
    renderAxisSettings();
  }

  function renderRuntimeState() {
    renderAxisSettings();
  }

  function shouldShowMonitoringMotor(motor) {
    const configured = activeAxisMotors().find((item) => registryMotorMatchesMonitoringMotor(item, motor));
    if (!configured) return false;
    return Boolean(configured.enabled) && !configured.hidden;
  }

  function bindEvents() {
    if (el.registrationTabs) {
      el.registrationTabs.addEventListener('click', (event) => {
        const button = event.target.closest('button[data-registration-tab]');
        if (!button) return;
        activeRegistrationTab = button.dataset.registrationTab || 'ac_servo';
        renderRegistrationTabs();
      });
    }

    if (el.axisSettingsTabs) {
      el.axisSettingsTabs.addEventListener('click', (event) => {
        const button = event.target.closest('button[data-axis-settings-tab]');
        if (!button) return;
        activeAxisSettingsTab = button.dataset.axisSettingsTab || 'current';
        renderAxisSettingsTabs();
      });
    }

    if (el.axisRows) {
      el.axisRows.addEventListener('pointerdown', (event) => {
        if (event.target.closest('[data-axis-edit], button')) return;
        if (event.button !== undefined && event.button !== 0) return;
        const row = event.target.closest('tr[data-axis-row]');
        if (!row) return;
        event.preventDefault();
        toggleAxisSelection(row.dataset.axisRow || '');
        renderAxisSettings();
      });

      el.axisRows.addEventListener('change', (event) => {
        const input = event.target.closest('[data-axis-edit]');
        if (!input) return;
        handleAxisEdit(input);
      });

      el.axisRows.addEventListener('click', async (event) => {
        const button = event.target.closest('button[data-axis-servo-action]');
        if (!button || !onAcServoControl) return;
        event.stopPropagation();
        const axis = Number(button.dataset.axisServoIndex);
        if (!Number.isInteger(axis) || axis < 0) {
          setAxisMessage('AC 서보 제어 축 번호를 확인할 수 없습니다.', true);
          return;
        }
        button.disabled = true;
        try {
          await onAcServoControl(button.dataset.axisServoAction || '', axis);
        } finally {
          button.disabled = false;
        }
      });
    }

    if (el.motorConfigTableRows) {
      el.motorConfigTableRows.addEventListener('pointerdown', (event) => {
        if (event.target.closest('[data-config-path]')) return;
        if (event.button !== undefined && event.button !== 0) return;
        const row = event.target.closest('[data-config-axis-select]');
        if (!row) return;
        event.preventDefault();
        selectedConfigMotorId = row.dataset.configAxisSelect || '';
        renderMotorConfigTable();
      });
      el.motorConfigTableRows.addEventListener('input', (event) => {
        const input = event.target.closest('[data-config-path]');
        if (!input) return;
        handleConfigTableEdit(input);
      });
      el.motorConfigTableRows.addEventListener('change', (event) => {
        const input = event.target.closest('[data-config-path]');
        if (!input) return;
        handleConfigTableEdit(input);
      });
    }

    if (el.addAxisButton) el.addAxisButton.addEventListener('click', addSelectedAxis);
    if (el.updateAxisIdentityButton) {
      el.updateAxisIdentityButton.addEventListener('click', updateSelectedAxisIdentity);
    }
    if (el.setAxisModelProfileButton) {
      el.setAxisModelProfileButton.addEventListener('click', setSelectedAxisModelProfile);
    }
    if (el.writeEthercatAliasButton) {
      el.writeEthercatAliasButton.addEventListener('click', writeSelectedEthercatAlias);
    }
    if (el.deleteAxisButton) el.deleteAxisButton.addEventListener('click', deleteSelectedAxis);
    if (el.toggleAxisButton) el.toggleAxisButton.addEventListener('click', toggleSelectedAxis);
    if (el.sortAxisButton) el.sortAxisButton.addEventListener('click', sortAxisNumbers);
    if (el.saveAxisConfigButton) el.saveAxisConfigButton.addEventListener('click', saveAxisConfig);
    if (el.applyAxisConfigButton) el.applyAxisConfigButton.addEventListener('click', applyConfigRestart);
    if (el.updateConfigTableButton) el.updateConfigTableButton.addEventListener('click', applyConfigTableUpdates);
    if (el.motorConfigFileNameInput) {
      el.motorConfigFileNameInput.addEventListener('input', (event) => {
        motorConfigFileNameDraft = event.target.value || '';
        setAxisMessage('파일명 변경값은 설정 파일 저장을 누르면 적용됩니다.');
        renderAxisSettings();
      });
    }
    if (el.reloadMotorConfigButton) {
      el.reloadMotorConfigButton.addEventListener('click', () => fetchRegistry());
    }
    if (el.deleteMotorConfigButton) {
      el.deleteMotorConfigButton.addEventListener('click', deleteCurrentMotorConfig);
    }
    if (el.scanAllButton) el.scanAllButton.addEventListener('click', scanAllMotors);
    if (el.scanButton) el.scanButton.addEventListener('click', scanMotors);
    if (el.dynamixelScanButton) el.dynamixelScanButton.addEventListener('click', scanDynamixel);
  }

  return {
    bindEvents,
    fetchRegistry,
    loadProjectRegistry,
    getDiscoverySummary,
    getWorkContext,
    getRegistryCount: () => activeVisibleAxisMotors().length,
    getConfiguredMotors: () => clone(activeVisibleAxisMotors()),
    renderAfterDisplayModeChange,
    renderRuntimeState,
    renderRegistrationTabs,
    shouldShowMonitoringMotor,
  };
}
