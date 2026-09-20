import {
  applyMotorConfig,
  fetchMotorConfig,
  requestMotorScan,
  requestAcServoScan,
  requestDynamixelScan,
  fetchMotorScanProgress,
  writeEthercatAlias,
  saveMotorConfig,
} from './api.js';
import {
  clone,
  displayText,
  escapeHtml,
  formatInt,
  stateLabel,
} from './format.js';
import {
  activeRegistryMotors as selectActiveRegistryMotors,
  activeVisibleRegistryMotors as selectActiveVisibleRegistryMotors,
  hasRegistryChanges,
  modelIsUnknown,
  normalizeMotor,
  normalizeRegistry,
  registryMotorById as selectRegistryMotorById,
  registryMotorLabel,
  upsertMotorInRegistry,
} from './motor_registry.js';
import {
  duplicateEthercatAddress,
  detectedScanRow,
  runtimeIsAcServo,
  runtimeMotorConfirmsRegistryMotor,
  scanKey,
  scanRowMatchesRuntimeMotor,
  scanRowToMotor as acServoScanRowToMotor,
  siiReportedAcServoModel,
  verifiedAcServoModel,
} from './motor_type_ac_servo.js';
import {
  dynamixelScanDeviceKey,
  dynamixelScanDeviceToMotor as buildDynamixelScanDeviceToMotor,
  firstDefined,
  modelTextFromDevice,
  runtimeIsDynamixel,
} from './motor_type_dynamixel.js';
import { showAlert, showConfirm, showPrompt } from './ui_dialogs.js';

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

// **이번 검색값이 먼저다** · §6-211
//
// 한 행 안에서 칸마다 보는 순서가 달랐다 · Slave·EEPROM·Station 은
// `row.scanRow ? 검색값 : 저장값` 으로 **검색을 먼저** 보는데 모델 칸만
// 저장값만 봤다 · 그래서 방금 검색을 해도 모델만 옛 값이 남았다.
//
//     검색 직후    Slave 1 · EEPROM 403    ← 방금 읽은 값
//                  모델 미확인             ← 프로젝트에 저장된 옛 값
//
// 「새로 검색했는데 왜 지금 것이 안 나오냐」가 여기였다 · 모델을 못 읽던
// 시절에 저장된 프로젝트는 「검색값 반영」을 누르기 전까지 영영 옛 값을
// 보여 줬다 · 다이나믹셀도 같다 (검색은 XM540-W150 을 읽어 왔다).
// **검색 결과가 곧 목록이다** · §6-219
//
// 전에는 축 목록이 둘이었다 · 편집 중인 `axisConfig` 와, 검색이 찾았지만
// 아직 목록에 없는 `proposedMotor` · 화면은 둘을 합쳐 그렸고 저장은 앞의
// 것만 썼다 · 그래서 네 줄이 보이는데 「0축 모터 설정은 저장할 수 없습니다」
// 가 떴고, 「선택 축 추가」로 사람이 손수 옮겨야 했다.
//
// 이제 검색이 찾은 축은 `adoptScanIntoDraft` 가 곧바로 목록에 넣는다 ·
// 여기서는 목록을 그리고 검색 날것을 붙이기만 한다.
export function buildAxisRows({
  motors = [],
  served = null,
  findAcServoScanRow = () => null,
  findDynamixelDevice = () => null,
  runtimeForMotor = () => null,
  sortValue = axisRowSortValue,
} = {}) {
  const servedById = new Map(
    (served?.axes || []).map((item) => [String(item.id), item]),
  );

  return motors.map((motor) => {
    const servedRow = servedById.get(String(motor.id)) || null;
    const scanned = servedRow?.scanned || null;
    const acServo = servedRow?.transport === 'ethercat';
    return {
      id: motor.id,
      motor,
      servedRow,
      scanRow: scanned && acServo ? findAcServoScanRow(scanned) : null,
      scanDevice: scanned && !acServo ? findDynamixelDevice(scanned) : null,
      runtimeMotor: runtimeForMotor(motor),
    };
  }).sort((a, b) => {
    const axisDiff = sortValue(a) - sortValue(b);
    if (axisDiff !== 0) return axisDiff;
    return String(a.id).localeCompare(String(b.id));
  });
}

function axisRowSortValue(row) {
  const motor = row?.motor;
  const axis = Number(motor?.config?.controller_index ?? motor?.axis ?? 9999);
  return Number.isFinite(axis) ? axis : 9999;
}

export function axisRowScannedModel(row) {
  if (!row) return '';
  // 서버가 정해 보낸 이름이 있으면 그것이 답이다 · §6-216
  if (row.servedRow?.scanned?.model) return String(row.servedRow.scanned.model).trim();
  if (row.servedRow?.model) return String(row.servedRow.model).trim();
  if (row.scanRow) return siiReportedAcServoModel(row.scanRow);
  if (row.scanDevice) return modelTextFromDevice(row.scanDevice);
  return '';
}

//: 한 행의 드라이버 모델 · 이번 검색값 → 프로젝트 저장값 · 모르면 빈 글자
export function axisRowDriverModel(row) {
  const scanned = axisRowScannedModel(row);
  if (scanned) return scanned;
  const motor = row?.motor;
  const stored = String(motor?.profile?.driver_model || '').trim();
  return modelIsUnknown(stored) ? '' : stored;
}

// **모델을 몰라도 막지 않는다** · §6-213
//
// 전에는 이 글이 적용을 **거부**했다 · 그런데 AC 서보는 모델 이름이
// 라벨일 뿐이다 · minas 드라이버의 운전 값(pulse_per_revolution ·
// profile_velocity · 가감속 …)은 전부 템플릿에서 오고 모델 이름은 맨 끝에
// 라벨로만 덮인다 (`append_driver_for_registry_motor`) · 이 막음이 지키던
// 기계적 값이 하나도 없었다.
//
// 반대로 막히는 비용은 컸다 · 검색이 SII 를 못 읽은 축 하나 때문에 **잘
// 붙은 축까지 전부** 못 올렸다 · 장비가 이상할 때 사람이 가장 먼저 누르고
// 싶은 것이 적용(모터 재시작)이다.
//
// 서버도 같이 걷었다 (`motor_profile_validation`) · 값이 위험한 경우는
// 거기서 **값을 보고** 따로 막는다 · 이름을 모르는 것과는 다른 일이다.
//
// 이제 이 글은 적용 확인창에 함께 띄우는 **알림**이다.
export function motorModelProfileWarning(motors) {
  const axes = (Array.isArray(motors) ? motors : [])
    .filter((motor) => (
      motor &&
      motor.enabled &&
      !motor.deleted &&
      motor.transport === 'ethercat' &&
      modelIsUnknown(motor.profile?.driver_model)
    ))
    .map((motor) => {
      const axis = Number(motor.config?.controller_index ?? motor.axis);
      return Number.isInteger(axis) ? axis : '?';
    });
  if (axes.length === 0) return '';
  return `모델을 읽지 못한 축: ${axes.join(', ')}. `
    + '적용은 진행됩니다 · 운전 프로필은 등록된 드라이버 값을 그대로 씁니다.';
}

export function motorRuntimeReadyForAppliedConfig(state) {
  const scope = state?.project_scope || {};
  const runtime = state?.service_management?.runtime || {};
  return scope.runtime_matches_selected === true
    && scope.motor_config_applied === true
    && runtime.phase === 'ready';
}

export function createMotorConfigController({
  el,
  operationProgress,
  getLatestState,
  renderLatestState,
  onProjectFilesChange,
  onConfigApplyStart,
  onConfigApplyComplete,
  onIdentityStatusChange,
  onAcServoControl,
}) {
  let latestScan = null;
  let savedRegistry = normalizeAxisRegistry({});
  let axisConfig = normalizeAxisRegistry({});
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
    // 표에서 고친 것도 「저장할 것」으로 센다 · §6-154
    //
    // 전에는 초안에 반영되기 전까지 "변경 없음" 이었다 · 값을 고쳐도 저장
    // 단추가 잠겨 있었고, 왜 잠겼는지는 툴팁에만 있어서 아무도 못 봤다 ·
    // 저장이 반영까지 대신 하므로 이제 고친 순간부터 저장할 것이 있다.
    return hasAxisChanges() || hasMotorConfigTableSaveChanges()
      || hasConfigTableDrafts();
  }

  function setStatusMessage(message) {
    if (el.configState) el.configState.textContent = message;
  }

  function setAxisMessage(message, error = false) {
    if (el.axisActionMessage) {
      el.axisActionMessage.textContent = message;
      el.axisActionMessage.classList.toggle('error-text', error);
    }
  }

  function uiMessage(message, fallback) {
    return String(message || fallback || '')
      .replace(/YAML/gi, '설정 파일')
      .replace(/yaml/gi, '설정 파일');
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
    return motorRuntimeReadyForAppliedConfig(getLatestState?.() || {});
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

  function modelProfileWarningMessage() {
    return motorModelProfileWarning(activeAxisMotors());
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

  function firstDynamixelDriverId() {
    const motor = axisMotors().find((item) => {
      const driverId = item.config?.driver_id;
      return driverId !== null && driverId !== undefined && driverId !== '';
    });
    return motor ? Number(motor.config.driver_id) : 1;
  }

  // 이름을 겹치지 않게 한다 · §6-217
  //
  // 전에는 이 감싸개가 바깥 함수와 **같은 이름**이었고 세 번째 인자만 달랐다
  // (여기선 함수, 바깥에선 옵션 객체) · 부르는 쪽이 옵션 객체를 넘기자
  // `options.nextAvailableAxis is not a function` 으로 검색이 통째로 죽었다.
  function proposedDynamixelMotor(device, { axis, model } = {}) {
    return buildDynamixelScanDeviceToMotor(device, null, {
      nextAvailableAxis: typeof axis === 'function' ? axis : nextAvailableAxis,
      firstDynamixelDriverId,
      model,
    });
  }

  function registryMotorMatchesMonitoringMotor(item, motor) {
    const identity = item.identity || {};
    const config = item.config || {};
    if (item.transport === 'ethercat') {
      const configuredMasterIndex = Number(firstDefined(
        config.ethercat_master_index,
        identity.ethercat_master_index,
        0,
      ));
      const runtimeMasterIndex = Number(motor.ethercat_master_index ?? 0);
      if (configuredMasterIndex !== runtimeMasterIndex) return false;
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
      const configuredPort = firstDefined(identity.serial_port, config.serial_port);
      const runtimePort = motor.serial_port;
      if (
        configuredId !== null &&
        configuredId !== undefined &&
        runtimeId !== null &&
        runtimeId !== undefined &&
        Number(configuredId) === Number(runtimeId) &&
        configuredPort &&
        runtimePort &&
        String(configuredPort) === String(runtimePort)
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

  function runtimeMotorForDynamixelDevice(device, motors = runtimeMotors()) {
    if (!device) return null;
    return motors.find((motor) => {
      const runtimeId = firstDefined(motor.bus_id, motor.node_id);
      return runtimeId !== null && runtimeId !== undefined
        && Number(runtimeId) === Number(device.id)
        && motor.serial_port
        && device.port
        && String(motor.serial_port) === String(device.port);
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
    const motor = row.motor;
    if (motor?.motor_type) return motor.motor_type;
    if (row.scanDevice) return 'dynamixel';
    if (row.scanRow) return 'ac_servo';
    return 'unknown';
  }

  // 축 번호는 사람이 고치지 않는다 · §6-219 · 이름만 고친다
  function rowAxisRaw(row) {
    return firstDefined(
      row.motor?.config?.controller_index,
      row.motor?.axis,
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
    const motor = row.motor;
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
    const motor = row.motor;
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
    if (row.motor) return registryMotorLabel(row.motor);
    return '-';
  }

  function rowDriverModelRaw(row) {
    const draft = rowDraft(row);
    if (draft.driver_model !== undefined) return draft.driver_model;
    return axisRowDriverModel(row);
  }

  function axisSortValue(row) {
    const axis = Number(firstDefined(rowAxisRaw(row), 9999));
    return Number.isFinite(axis) ? axis : 9999;
  }

  function driverLabel(row) {
    if (row.motor) {
      return firstDefined(
        axisRowScannedModel(row) || row.motor.profile?.driver_model,
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

  /** 저장 버튼은 **끄지 않는다** · §6-203
   *
   * 전에는 「바뀐 게 없으면」 껐다 · 그런데 다시 저장해서 나쁠 일이 없다 ·
   * 오히려 파일이 어긋났나 싶을 때 다시 눌러 맞추고 싶어진다 · 그때 회색이면
   * 사용자는 「왜 막지」만 남는다.
   *
   * 무엇이 바뀌었는지는 **글로** 알린다 · 버튼을 막아서 말하지 않는다.
   */
  function updateSaveButtonState() {
    // 표를 다시 그리지 않는다 · 타이핑 중에 다시 그리면 입력칸이 초점을 잃는다
    if (el.saveAxisConfigButton) {
      el.saveAxisConfigButton.title = hasAnyConfigChanges()
        ? '변경한 내용을 설정 파일에 씁니다.'
        : '바뀐 내용은 없지만 지금 값 그대로 다시 저장합니다.';
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
      updateSaveButtonState();
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
      updateSaveButtonState();
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
    updateSaveButtonState();
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
    updateSaveButtonState();
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

  /** 표 입력값을 설정 원문에 써 넣는다 · 성공하면 true · §6-154
   *
   * 전에는 이것만 따로 누르는 단추가 있었다 · 그런데 이 일은 **브라우저 안에서만**
   * 일어난다 · 서버에 아무것도 보내지 않는 중간 단계라 사람이 알 이유가 없었다 ·
   * 이제 저장이 알아서 부른다.
   */
  function applyConfigTableUpdates() {
    if (!hasConfigTableDrafts()) return true;

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
      setAxisMessage('값이 올바르지 않아 저장하지 않았습니다');
      return false;
    }

    motorConfigRawText = lines.join('\n');
    configTableDrafts = new Map();
    lastAxisRenderSignature = '';
    lastConfigTableRenderSignature = '';
    lastConfigRawTextRenderSignature = '';
    renderAxisSettings();
    return true;
  }

  function scanStatus(row) {
    if (row.scanRow) {
      const deviceState = String(row.scanRow.device_state || '').toUpperCase();
      if (deviceState.includes('ERROR')) {
        return [`EtherCAT ${deviceState}`, 'delete'];
      }
      if (row.servedRow?.confirmation_required) {
        return ['기존 축 연결 필요', 'review'];
      }
      const motor = row.motor;
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

  function settingStatus(row) {
    if (row.servedRow?.confirmation_required) {
      return ['연결 확인 필요', 'review'];
    }
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
      const masterIndex = firstDefined(
        row.scanRow?.master_index,
        identity.ethercat_master_index,
        config.ethercat_master_index,
        0,
      );
      return {
        title: `Vendor ${displayText(vendor)} · Product ${displayText(product)}`,
        detail: [
          `Master ${displayText(masterIndex)}`,
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
    const motor = row.motor;
    if (!motor) {
      const siiName = firstDefined(row.scanRow?.sii_order_number, row.scanRow?.sii_device_name);
      return {
        title: '모델 미확인',
        detail: siiName ? `SII 참고값 ${siiName}` : '모델·운전 프로필 미설정',
      };
    }
    const scanned = axisRowScannedModel(row);
    const model = scanned || String(motor.profile?.driver_model || '').trim();
    const confirmed = !modelIsUnknown(model);
    const source = scanned ? 'physical_sii' : String(motor.profile?.model_source || '');
    const sourceLabel = source === 'verified_catalog'
      ? '카탈로그 확인'
      : source === 'physical_protocol'
        ? '장치 프로토콜 확인'
        : source === 'physical_sii_user_confirmed'
          ? 'SII 검색값 사용자 확인'
        : source === 'physical_sii'
          ? 'SII 검색값'
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
      const masterIndex = firstDefined(
        row.scanRow?.master_index,
        row.motor?.identity?.ethercat_master_index,
        row.motor?.config?.ethercat_master_index,
        0,
      );
      return {
        title: (
          `${axisIdLabel(row)} · Master ${displayText(masterIndex)}`
          + ` · Slave ${displayText(acIdentityValue(row, 'slave_position'))}`
        ),
        detail: `EEPROM ${displayText(directScanAliasValue(row))} · Station ${displayText(acIdentityValue(row, 'rotary_alias'))}`,
      };
    }
    const motor = row.motor;
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

  // **짝은 서버가 맞춘다** · §6-216
  //
  // 전에는 여기서 「이 슬레이브가 프로젝트의 몇 번 축인가」를 화면이 정했다 ·
  // 같은 판단이 서버에도 있었고 규칙이 미묘하게 달라서 모델 이름과 축 이름이
  // 갈렸다 · 저장하면 선택이 통째로 풀린 것도 그 때문이다.
  //
  // 이제 서버가 `scan.axis_rows` 로 짝을 지어 보낸다 · 화면은 그 짝에 맞는
  // 날것을 찾아 붙이기만 한다 (찾기는 판단이 아니다).
  // 「이 축의 검색 행」도 서버 짝을 따른다 · §6-216
  function servedRowFor(motorId) {
    return (serverAxisRows()?.axes || []).find(
      (row) => String(row.id) === String(motorId),
    ) || null;
  }

  function scanRowForMotor(motor) {
    const served = servedRowFor(motor?.id);
    return served?.scanned ? rawAcServoScanRow(served.scanned) : null;
  }

  function registryMotorForAxis(axis) {
    if (axis === null || axis === undefined) return null;
    return activeAxisMotors().find(
      (motor) => Number(motorAxisValue(motor)) === Number(axis),
    ) || null;
  }

  function serverAxisRows() {
    const rows = latestScan?.axis_rows;
    return rows && Array.isArray(rows.axes) ? rows : null;
  }

  function rawAcServoScanRow(scanned) {
    if (!scanned) return null;
    return acServoScanRows().find((row) => (
      Number(row.master_index ?? 0) === Number(scanned.master_index ?? 0)
      && Number(row.slave_position) === Number(scanned.slave_position)
    )) || null;
  }

  function rawDynamixelDevice(scanned) {
    if (!scanned) return null;
    return dynamixelScanDevices().find((device) => (
      Number(device.id) === Number(scanned.bus_id)
      && String(device.port || '') === String(scanned.serial_port || '')
    )) || null;
  }

  function attachScanned(row, served) {
    const scanned = served?.scanned;
    if (!scanned) return;
    if (served.transport === 'ethercat') row.scanRow = rawAcServoScanRow(scanned);
    else row.scanDevice = rawDynamixelDevice(scanned);
  }

  function axisRowsData() {
    const runtime = runtimeMotors();
    return buildAxisRows({
      motors: axisMotors(),
      served: serverAxisRows(),
      findAcServoScanRow: rawAcServoScanRow,
      findDynamixelDevice: rawDynamixelDevice,
      runtimeForMotor: (motor) => runtimeMotorForRegistryMotor(motor, runtime),
      sortValue: axisSortValue,
    });
  }

  // 검색이 목록에 무엇을 넣었는지 말한다 · §6-219
  function scanAdoptedMessage(head) {
    const rows = axisRowsData();
    const parts = [`${head} · ${formatInt(rows.length)}축`];
    parts.push('이름을 고친 뒤 「설정 저장」을 누르세요');
    return parts.join(' · ');
  }

  // **검색하면 목록을 갈아 끼운다** · §6-219
  //
  // 기존 목록을 지우고 이번 검색이 찾은 것으로 채운다 · 그것이 목록이고
  // 그대로 저장된다 · 「선택 축 추가」로 사람이 옮기던 단계를 없앴다.
  //
  // 「AC Servo 검색」을 누르면 서보만 남는다 · 둘 다 쓰려면 「전체 모터
  // 검색」을 누른다 · 통로별로 반쪽만 바꾸게 했더니 서보를 검색했는데
  // 표에 4축이 떠서 무엇을 찾은 것인지 알 수 없었다.
  //
  // 사람이 붙인 이름만 남긴다 · 고칠 수 있는 것이 이름 하나뿐이므로
  // 다시 검색했다고 지워지면 안 된다.
  function adoptScanIntoDraft() {
    const served = serverAxisRows();
    if (!served) return;

    const namesById = new Map(
      axisConfig.motors.map((motor) => [motor.id, motor.name]),
    );
    axisConfig.motors = [];

    const found = [
      ...(served.axes || []).filter((item) => item.state === 'matched'),
      ...(served.new_devices || []),
    ];
    found.forEach((item) => {
      const acServo = item.transport === 'ethercat';
      const scanRow = acServo ? rawAcServoScanRow(item.scanned) : null;
      const scanDevice = acServo ? null : rawDynamixelDevice(item.scanned);
      if (acServo ? !scanRow : !scanDevice) return;
      const axisFor = () => Number(item.proposed_axis ?? item.axis ?? 0);
      const motor = acServo
        ? acServoScanRowToMotor(scanRow, axisFor)
        : proposedDynamixelMotor(scanDevice, { axis: axisFor, model: item.model });
      const name = namesById.get(motor.id);
      upsertMotorInRegistry(
        axisConfig,
        name ? normalizeMotor({ ...motor, name }) : motor,
      );
    });
  }



  function renderAxisButtons(rows) {
    const hasConfiguredAxes = axisMotors().some((motor) => !motor.deleted);
    const changed = hasAnyConfigChanges();
    const recoveryMessage = acHardwareRecoveryMessage();
    const identityError = acHardwareIdentityErrorMessage();
    const identityApplyBlockMessage = acHardwareApplyBlockMessage();
    const modelWarningMessage = modelProfileWarningMessage();
    const applyBlockMessage = identityApplyBlockMessage || modelWarningMessage;
    const alreadyApplied = selectedMotorConfigAlreadyApplied();
    onIdentityStatusChange?.(motionControlBlockMessage());

    // 저장은 언제나 누를 수 있다 · §6-203 · 다시 저장해서 나쁠 일이 없다
    if (el.saveAxisConfigButton) {
      el.saveAxisConfigButton.disabled = false;
      el.saveAxisConfigButton.title = changed
        ? '검색해서 나온 축과 고친 이름을 설정 파일에 씁니다.'
        : '바뀐 내용은 없지만 지금 값 그대로 다시 저장합니다.';
    }
    // 축이 없어도 누를 수 있다 · 왜 안 되는지는 서버가 말한다 · §6-203
    if (el.applyAxisConfigButton) {
      el.applyAxisConfigButton.disabled = false;
      // **버튼 이름은 바뀌지 않는다** · §6-226
      //
      // 전에는 이미 적용된 상태면 「설정 다시 적용 · 모터 재시작」으로
      // 글자가 바뀌었다 · 같은 버튼을 부를 이름이 둘이 되어 말이 안 통했다.
      el.applyAxisConfigButton.textContent = '설정 적용 · 모터 재시작';
      el.applyAxisConfigButton.title = !hasConfiguredAxes
        ? (applyBlockMessage || '적용할 프로젝트 축 설정이 없습니다.')
        : changed
          ? '저장하지 않은 변경이 있습니다 · **저장된 파일**이 적용됩니다.'
          : alreadyApplied
            ? '이미 적용된 설정입니다 · 다시 누르면 모터를 재시작합니다.'
            : applyBlockMessage || recoveryMessage
              || '저장된 현재 프로젝트 설정을 실행 시스템에 적용합니다.';
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
      (row) => row.servedRow?.confirmation_required,
    ).length;
    const scanOnlyCount = 0;
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
      next = '다음 작업: 설정 적용 · 모터 재시작';
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
      next = '다음 작업: 자동 선택된 축을 확인하고 선택 축 검색값 반영';
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
      next = '다음 작업: 설정 적용 · 모터 재시작';
      stateCode = 'warning';
    }

    if (el.axisWorkflowStatus) el.axisWorkflowStatus.dataset.state = stateCode;
    if (el.axisWorkflowState) el.axisWorkflowState.textContent = state;
    if (el.axisWorkflowDetail) el.axisWorkflowDetail.textContent = detail;
    if (el.axisWorkflowNext) el.axisWorkflowNext.textContent = next;
  }

  function renderAxisSettings() {
    const rows = axisRowsData();

    const configured = axisMotors().filter((motor) => !motor.deleted);
    const disabled = configured.filter((motor) => !motor.enabled);
    const connectionCandidates = rows.filter(
      (row) => row.servedRow?.confirmation_required,
    );
    const unreachable = rows.filter((row) => row.servedRow?.state === 'unreachable');
    const changed = hasAnyConfigChanges();

    if (el.axisSummary) {
      el.axisSummary.textContent = `설정 ${formatInt(configured.length)}축, 미사용 ${formatInt(disabled.length)}축, 연결 확인 ${formatInt(connectionCandidates.length)}축, 응답 없음 ${formatInt(unreachable.length)}축, ${changed ? '저장 필요' : '저장됨'}`;
    }

    if (el.axisRows) {
      const rowViews = rows.map((row) => {

          const [settingText, settingClass] = settingStatus(row);
          const [scanText, scanClass] = scanStatus(row);
          const [runtimeText, runtimeClass] = runtimeStatus(row);
          const motor = row.motor;
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
          const editable = !(row.motor && row.motor.deleted);
          const identity = rowDeviceIdentity(row);
          const modelProfile = rowModelProfileView(row);
          const driverModel = rowDriverModelRaw(row);
          const connection = rowConnectionIdentity(row);
          const drive = rowDriveView(row);
          const showAcServoControls = rowMotorType(row) === 'ac_servo' &&
            Boolean(row.motor && !row.motor.deleted && row.motor.enabled);
          return {
            row,
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
            drive,
            showAcServoControls,
          };
        });
      const renderSignature = JSON.stringify(rowViews.map((view) => ({
        id: view.row.id,
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
        drive: view.drive,
        showAcServoControls: view.showAcServoControls,
      })));

      if (renderSignature !== lastAxisRenderSignature) {
        lastAxisRenderSignature = renderSignature;
        el.axisRows.innerHTML = rows.length > 0
          ? rowViews.map((view) => {
          const row = view.row;
          const disabled = view.editable ? '' : ' disabled';
          return `
            <tr data-axis-row="${escapeHtml(row.id)}">
              <td class="axis-combined-cell">
                <span class="axis-number-label mono">${displayText(view.axisValue)}</span>
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
            </tr>
          `;
        }).join('')
          : '<tr><td colspan="6" class="empty">설정 파일을 불러오거나 모터 스캔을 실행하세요</td></tr>';
      }
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
    renderAxisSettings();
    renderLatestState();
  }

  async function fetchRegistry(expectedToken = projectLoadToken) {
    expectedToken = normalizeProjectLoadToken(expectedToken, projectLoadToken);
    setStatusMessage('설정 파일 불러오는 중');
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

    const duplicateAddress = duplicateEthercatAddress(motors);
    if (duplicateAddress) {
      const { masterIndex, addressType, value } = duplicateAddress;
      return addressType === 'position'
        ? `EtherCAT Master ${formatInt(masterIndex)}에서 EEPROM Alias가 0인 AC 서보의 Slave Position ${formatInt(value)} 값이 중복되어 있습니다.`
        : `EtherCAT Master ${formatInt(masterIndex)}의 AC 서보 EEPROM Alias ${formatInt(value)} 값이 중복되어 있습니다.`;
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
      if (alias === 0) return;
      const masterIndex = Number(slave.master_index ?? 0);
      const key = `${masterIndex}:${alias}`;
      scannedAliases.set(key, {
        masterIndex,
        alias,
        count: (scannedAliases.get(key)?.count || 0) + 1,
      });
    });
    const duplicateAlias = [...scannedAliases.values()].find((item) => item.count > 1);
    if (duplicateAlias) {
      return `검색된 EtherCAT Master ${formatInt(duplicateAlias.masterIndex)}의 EEPROM Alias ${formatInt(duplicateAlias.alias)} 값이 중복되어 적용할 수 없습니다.`;
    }

    for (const motor of activeAxisMotors().filter(
      (item) => item.transport === 'ethercat' && item.enabled,
    )) {
      const scanRow = scanRowForMotor(motor);
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
      const scanRow = scanRowForMotor(motor);
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
    // **바뀐 게 없어도 저장한다** · §6-203
    //
    // 버튼은 풀었는데 여기서 첫 줄에 거부하고 있었다 · 눌러도 아무 일이
    // 일어나지 않아 「고장인가」로 보였다 · 같은 값을 다시 적는 것이라
    // 해로울 일이 없고, 파일이 어긋났나 싶을 때 다시 눌러 맞출 수 있어야 한다.

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
      // 표에서 고친 값을 먼저 원문에 써 넣는다 · §6-154
      //
      // 전에는 여기서 "먼저 반영하세요" 라고 **거부만** 했다 · 거부할 줄 알면
      // 대신 할 줄도 알아야 한다 · 값이 올바르지 않으면 반영이 false 를
      // 돌려주고, 그때는 이미 무엇이 틀렸는지 알린 뒤다.
      if (!applyConfigTableUpdates()) return false;
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
        await showAlert(message, { title: '설정 저장 실패', tone: 'danger' });
        return false;
      }
      applyMotorConfigPayload(payload);
      configApplyPending = true;
      setStatusMessage('축 설정 저장됨');
      const modelWarning = modelProfileWarningMessage();
      setAxisMessage(
        modelWarning
          ? `프로젝트 축 목록 저장됨 · ${modelWarning}`
          : '저장했습니다 · 실제 모터에 반영하려면 오른쪽 「설정 적용」을 누르세요.',
        Boolean(modelWarning),
      );
      await onProjectFilesChange?.();
      // **저장했으면 눈에 보이게 말한다** · §6-203
      //
      // 전에는 작은 글씨 한 줄뿐이었다 · 바뀐 내용이 없을 때는 화면이
      // 그대로라 「눌렀는데 아무 일도 안 일어났다」로 보였다.
      await showAlert(
        modelWarning
          ? `설정 파일에 저장했습니다.\n\n${modelWarning}`
          : '설정 파일에 저장했습니다.\n\n'
            + '실제 모터에 반영하려면 오른쪽 「장비에 적용 · 모터 재시작」을 누르세요.',
        { title: '설정 저장 완료', tone: modelWarning ? 'warning' : 'info' },
      );
      return true;
    } catch (error) {
      const message = `축 설정 저장 실패: ${error?.message || error}`;
      setStatusMessage(message);
      setAxisMessage(message);
      return false;
    } finally {
      if (saveButton) {
        saveButton.textContent = originalText;
        // 저장이 끝났다고 버튼을 도로 잠그지 않는다 · §6-203
        //
        // 여기서 `!hasAnyConfigChanges()` 로 다시 껐다 · 그래서 한 번 저장하면
        // 버튼이 회색이 되고, 사용자는 「눌렀는데 아무 변화가 없다」고 본다.
        saveButton.disabled = false;
      }
      renderAxisSettings();
    }
  }

  async function applyConfigRestart() {
    // **「설정 적용 · 모터 재시작」은 파일을 바꾸지 않는다** · §6-221
    //
    // 한때 여기서 저장을 대신 눌러 줬다 · 그러면 사람이 「설정 저장」을
    // 누르지 않았는데 프로젝트 파일이 바뀐다 · 검색이 아무것도 못 잡은
    // 상태에서 이 버튼을 누르면 프로젝트가 비워진다.
    //
    // **파일은 「설정 저장」을 눌렀을 때만 바뀐다** · 여기서는 저장된
    // 파일을 그대로 적용하고, 다른 점이 있으면 확인창에서 말로 알린다.
    const unsavedWarning = hasAnyConfigChanges()
      ? '저장하지 않은 변경이 있습니다 · **저장된 파일**이 적용됩니다.\n\n'
      : '';
    if (!axisMotors().some((motor) => !motor.deleted)) {
      setAxisMessage('설정 적용할 축이 없습니다.');
      renderAxisSettings();
      return false;
    }
    const recoveryMessage = acHardwareRecoveryMessage();
    const modelWarningMessage = modelProfileWarningMessage();
    const identityApplyBlockMessage = acHardwareApplyBlockMessage();
    const applyBlockMessage = identityApplyBlockMessage && !recoveryMessage
      ? identityApplyBlockMessage
      : '';
    if (applyBlockMessage) {
      window.alert(applyBlockMessage);
      setAxisMessage(applyBlockMessage, true);
      renderAxisSettings();
      return false;
    }

    const recoveryWarning = recoveryMessage
      ? `복구 적용 안내:\n${recoveryMessage}\n\n`
      : '';
    // 모델을 몰라도 막지 않는다 · 확인창에서 말로 알린다 · §6-213
    const modelWarning = modelWarningMessage ? `${modelWarningMessage}\n\n` : '';
    const confirmed = await showConfirm(
      unsavedWarning
      + modelWarning
      + recoveryWarning
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
        (item) => (
          Number(item.master_index ?? 0) === Number(pendingAliasWrite.masterIndex)
          && Number(item.slave_position) === Number(pendingAliasWrite.slavePosition)
        ),
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
    const masters = Array.isArray(ethercatScan.masters) ? ethercatScan.masters : [];
    const resultState = ethercatScan.available && ethercatScan.complete
      ? '검색 완료'
      : ethercatScan.available
        ? '검색 부분 완료'
        : '검색 실패';
    const masterSummary = masters.length
      ? ` · ${masters.map((master) => (
        `Master ${formatInt(master.master_index ?? 0)} `
        + `${formatInt(master.slaves_count ?? 0)}축`
      )).join(' / ')}`
      : '';
    el.scanResult.textContent = (
      `${resultState} · ${formatInt(slaves.length)}축${masterSummary}`
    );
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
      adoptScanIntoDraft();
      renderAxisSettings();
      setAxisMessage(scanAdoptedMessage('AC 서보 검색 완료'));
      if (payload.motion_state) renderLatestState(payload.motion_state);
      const scanPartial = payload.partial === true
        || payload.motor_operation?.status === 'partial';
      el.scanButton.textContent = payload.success
        ? '직접 검색 완료'
        : scanPartial
          ? '직접 검색 부분 완료'
          : '직접 검색 실패';
      await finishScanProgressPopup(
        payload.success,
        payload.success
          ? 'AC 서보 검색 완료'
          : conciseMotorScanMessage(
            uiMessage(payload.message, scanPartial ? 'AC 서보 검색 부분 완료' : 'AC 서보 검색 실패'),
          ),
        scanPartial ? 'partial' : '',
      );
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
      adoptScanIntoDraft();
      renderAxisSettings();
      setAxisMessage(scanAdoptedMessage('다이나믹셀 검색 완료'));
      if (payload.motion_state) renderLatestState(payload.motion_state);
      el.dynamixelScanButton.textContent = payload.success ? '검색 완료' : '검색 실패';
      await finishScanProgressPopup(
        payload.success,
        payload.success
          ? 'Dynamixel 검색 완료'
          : conciseMotorScanMessage(uiMessage(payload.message, 'Dynamixel 검색 실패')),
      );
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
      adoptScanIntoDraft();
      renderAxisSettings();
      const summary = getDiscoverySummary();
      // **서버 판정을 따른다** · §6-206
      //
      // 전에는 `scan.scan_complete` 를 봤다 · 그 값은 「등록된 Master 가 전부
      // 응답했나」라서, 프로젝트가 쓰지 않는 Master 하나가 비어 있으면 늘
      // false 다 · 그래서 다 찾았는데도 「일부 검색만 완료됐습니다」가 떴다.
      //
      // 서버는 프로젝트 기준으로 다시 판정해 `success`/`partial` 에 담아
      // 보낸다 (§6-198) · AC 서보 검색은 그것을 보고 있었는데 전체 검색만
      // 옛 칸을 보고 있었다.
      const scanComplete = payload.success === true;
      const scanPartial = payload.partial === true;
      const dynamixelError = payload.scan?.dynamixel_scan?.error || '';
      if (el.scanAllResult) {
        el.scanAllResult.textContent = scanComplete
          ? `검색 완료 · AC 서보 ${formatInt(summary.ethercatCount)}축 · 다이나믹셀 ${formatInt(summary.dynamixelCount)}축`
          : scanPartial
            ? conciseMotorScanMessage(`부분 완료 · AC 서보 ${formatInt(summary.ethercatCount)}축 · 다이나믹셀 실패: ${dynamixelError || '직접 응답 없음'}`)
            : conciseMotorScanMessage(uiMessage(payload.message, '전체 모터 검색 실패'));
      }
      setAxisMessage(scanComplete
        ? scanAdoptedMessage('전체 모터 검색 완료')
        : scanPartial
          ? conciseMotorScanMessage(`일부 검색만 완료됐습니다. ${dynamixelError || '연결되지 않은 모터 종류를 확인하세요.'}`)
          : conciseMotorScanMessage(uiMessage(payload.message, '전체 모터 검색 실패')),
      !scanComplete);
      if (payload.motion_state) renderLatestState(payload.motion_state);
      el.scanAllButton.textContent = scanComplete ? '검색 완료' : (scanPartial ? '부분 완료' : '검색 실패');
      // **실패하면 서버가 말한 이유를 그대로 보여준다** · §6-224
      //
      // 전에는 고정 문구 「전체 모터 검색 실패」만 띄우고 `payload.message`
      // 를 버렸다 · 서버는 「AC Servo 축 0이 움직이는 중입니다」처럼 무엇을
      // 해야 하는지까지 말해 주는데 사람은 그걸 못 봤다.
      await finishScanProgressPopup(
        scanComplete,
        scanComplete
          ? '전체 모터 검색 완료'
          : conciseMotorScanMessage(
            uiMessage(payload.message, scanPartial ? '전체 모터 검색 부분 완료' : '전체 모터 검색 실패'),
          ),
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
    if (el.axisSettingsTabs) {
      el.axisSettingsTabs.addEventListener('click', (event) => {
        const button = event.target.closest('button[data-axis-settings-tab]');
        if (!button) return;
        activeAxisSettingsTab = button.dataset.axisSettingsTab || 'current';
        renderAxisSettingsTabs();
      });
    }

    if (el.axisRows) {
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

    if (el.saveAxisConfigButton) el.saveAxisConfigButton.addEventListener('click', saveAxisConfig);
    if (el.applyAxisConfigButton) el.applyAxisConfigButton.addEventListener('click', applyConfigRestart);
    if (el.scanAllButton) el.scanAllButton.addEventListener('click', scanAllMotors);
    if (el.scanButton) el.scanButton.addEventListener('click', scanMotors);
    if (el.dynamixelScanButton) el.dynamixelScanButton.addEventListener('click', scanDynamixel);
  }

  return {
    bindEvents,
    fetchRegistry,
    loadProjectRegistry,
    getDiscoverySummary,
    getRegistryCount: () => activeVisibleAxisMotors().length,
    getConfiguredMotors: () => clone(activeVisibleAxisMotors()),
    renderAfterDisplayModeChange,
    renderRuntimeState,
    shouldShowMonitoringMotor,
  };
}
