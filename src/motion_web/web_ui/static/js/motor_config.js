import {
  applyMotorConfig,
  fetchMotorConfig,
  requestMotorScan,
  requestAcServoScan,
  requestDynamixelScan,
  writeEthercatAlias,
  saveMotorConfig,
} from './api.js?v=20260720-eeprom-alias-write';
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
  scanKey,
  scanRowMatchesRegistryMotor,
  scanRowMatchesRuntimeMotor,
  scanRowToMotor as acServoScanRowToMotor,
} from './motor_type_ac_servo.js';
import {
  dynamixelScanDeviceKey,
  dynamixelScanDeviceToMotor as buildDynamixelScanDeviceToMotor,
  firstDefined,
  modelTextFromDevice,
  runtimeIsDynamixel,
} from './motor_type_dynamixel.js';

export function createMotorConfigController({
  el,
  getLatestState,
  renderLatestState,
  onWorkContextChange,
  onConfigApplyStart,
  onConfigApplyComplete,
  onIdentityStatusChange,
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
  let motorConfigFileNameDraft = '';
  let configTableDrafts = new Map();
  let selectedConfigMotorId = '';
  let identityUpdatePending = false;
  let pendingAliasWrite = null;
  let lastConfigTableRenderSignature = '';
  let lastConfigRawTextRenderSignature = '';

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
    return Array.isArray(state?.motors) ? state.motors : [];
  }

  function motorAxisValue(motor) {
    const axis = Number(motor?.config?.controller_index ?? motor?.axis);
    return Number.isInteger(axis) ? axis : null;
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
        identity.rotary_alias !== null && identity.rotary_alias !== undefined &&
        motor.station_alias_register !== null &&
        motor.station_alias_register !== undefined
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
    return Array.isArray(latestScan?.matching_rows)
      ? latestScan.matching_rows.filter((row) => detectedScanRow(row))
      : [];
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

  function axisIdLabel(row) {
    const value = rowIdRaw(row);
    if (value === null || value === undefined) return '-';
    return `${rowIdPrefix(row)} ${formatInt(value)}`;
  }

  function acIdentityValue(row, field) {
    const motor = row.motor || row.proposedMotor;
    if (rowMotorType(row) !== 'ac_servo' && motor?.transport !== 'ethercat') return '-';
    if (field === 'eeprom_alias') {
      return firstDefined(
        motor?.identity?.ethercat_alias,
        motor?.config?.alias,
        row.scanRow?.ethercat_alias,
        row.runtimeMotor?.alias,
      );
    }
    if (field === 'rotary_alias') {
      return firstDefined(motor?.identity?.rotary_alias, row.scanRow?.rotary_alias);
    }
    if (field === 'slave_position') {
      return firstDefined(motor?.identity?.slave_position, row.scanRow?.slave_position);
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
    if (row.proposedMotor) return registryMotorLabel(row.proposedMotor);
    return '-';
  }

  function axisSortValue(row) {
    const axis = Number(firstDefined(rowAxisRaw(row), 9999));
    return Number.isFinite(axis) ? axis : 9999;
  }

  function driverLabel(row) {
    if (row.motor) {
      return firstDefined(
        row.motor.identity?.driver_model,
        row.motor.driver_family,
        row.motor.config?.driver_id !== null && row.motor.config?.driver_id !== undefined
          ? `driver ${row.motor.config.driver_id}`
          : null,
      ) || '-';
    }
    if (row.scanRow) return row.scanRow.driver_model || '-';
    if (row.scanDevice) return modelTextFromDevice(row.scanDevice) || '-';
    return '-';
  }

  function hasConfigTableDrafts() {
    return configTableDrafts.size > 0;
  }

  function updateConfigTableButtonState() {
    if (el.updateConfigTableButton) {
      el.updateConfigTableButton.disabled = !hasConfigTableDrafts();
      el.updateConfigTableButton.textContent = '표 업데이트';
    }
    if (el.saveConfigTableButton) {
      el.saveConfigTableButton.disabled = hasConfigTableDrafts() || !hasMotorConfigTableSaveChanges();
      el.saveConfigTableButton.textContent = hasConfigTableDrafts() ? '표 업데이트 필요' : '파일 저장';
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
    const item = yamlItemName(row);
    const path = String(row?.path || '');
    if (/^masters\[\d+\]\.slaves\[\d+\]\./.test(path)) {
      return ['controller_index', 'name', 'position'].includes(item);
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
      driver_model: '드라이버 모델',
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
    setAxisMessage('설정 파일 표 업데이트 완료. 저장하려면 파일 저장을 누르세요.');
    renderAxisSettings();
  }

  function scanStatus(row) {
    if (row.scanRow) {
      const deviceState = String(row.scanRow.device_state || '').toUpperCase();
      if (deviceState.includes('ERROR')) {
        return [`EtherCAT ${deviceState}`, 'delete'];
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
    return activeAxisMotors().find((motor) => {
      if (motor.transport !== 'ethercat') return false;
      const identity = motor.identity || {};
      const sameSlave = identity.slave_position !== null &&
        identity.slave_position !== undefined &&
        scanRow.slave_position !== null && scanRow.slave_position !== undefined &&
        Number(identity.slave_position) === Number(scanRow.slave_position);
      return sameSlave;
    }) || null;
  }

  function settingStatus(row) {
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

  function rowById(rowId) {
    return axisRowsData().find((row) => row.id === rowId) || null;
  }

  function editedMotor(motor, row, field, value) {
    const next = normalizeMotor(motor);
    if (field === 'name') {
      next.name = String(value ?? '');
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
  }

  function handleAxisEdit(input) {
    const rowId = input.dataset.axisRowId || '';
    const field = input.dataset.axisEdit || '';
    const row = rowById(rowId);
    if (!row) return;

    if (field === 'name') {
      setAxisEditValue(row, 'name', input.value);
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
      rows.push({
        id: `scan:ac:${scanKey(scanRow)}`,
        motor: null,
        proposedMotor,
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
    const newRows = axisRowsData().filter((row) => !row.motor && row.proposedMotor);
    selectedAxisIds = new Set(newRows.map((row) => row.id));
    lastAxisRenderSignature = '';
    renderAxisSettings();
    return newRows.length;
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
    const canApply = hasConfiguredAxes && !changed && (!identityError || Boolean(recoveryMessage));
    onIdentityStatusChange?.(
      identityError || (identityUpdatePending ? '확인한 모터 연결값을 프로젝트에 저장해야 합니다.' : ''),
    );

    if (el.addAxisButton) el.addAxisButton.disabled = !canAdd;
    if (el.updateAxisIdentityButton) el.updateAxisIdentityButton.disabled = !canUpdateIdentity;
    if (el.writeEthercatAliasButton) el.writeEthercatAliasButton.disabled = !canWriteAlias;
    if (el.deleteAxisButton) el.deleteAxisButton.disabled = !canDelete;
    if (el.sortAxisButton) el.sortAxisButton.disabled = !canSort;
    if (el.toggleAxisButton) {
      el.toggleAxisButton.disabled = !canToggle;
      if (editableRows.length > 0) {
        const shouldTurnOn = editableRows.some((row) => !row.motor.enabled);
        el.toggleAxisButton.textContent = shouldTurnOn ? '선택 축 사용' : '선택 축 미사용';
      } else {
        el.toggleAxisButton.textContent = '축 사용/미사용';
      }
    }
    if (el.saveAxisConfigButton) el.saveAxisConfigButton.disabled = !changed;
    if (el.applyAxisConfigButton) el.applyAxisConfigButton.disabled = !canApply;
    if (el.applyAxisConfigButton) {
      el.applyAxisConfigButton.textContent = '설정 적용 및 재시작';
      el.applyAxisConfigButton.title = canApply
        ? recoveryMessage || '저장된 현재 프로젝트 설정을 실행 시스템에 적용합니다.'
        : changed
          ? '변경 내용을 먼저 저장하세요.'
          : identityError || '적용할 프로젝트 축 설정이 없습니다.';
    }
    if (el.headerNodeRestartButton) {
      el.headerNodeRestartButton.disabled = !canApply;
      el.headerNodeRestartButton.textContent = '설정 적용·재시작';
      el.headerNodeRestartButton.title = el.applyAxisConfigButton?.title || '';
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
    const scanOnlyCount = rows.filter(
      (row) => !row.motor && (row.scanRow || row.scanDevice),
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
    const scanOnly = rows.filter((row) => !row.motor && (row.scanRow || row.scanDevice));
    const changed = hasAnyConfigChanges();
    const selectedCount = selectedAxisIds.size;

    if (el.axisSummary) {
      el.axisSummary.textContent = `설정 ${formatInt(configured.length)}축, 미사용 ${formatInt(disabled.length)}축, 검색 후 미설정 ${formatInt(scanOnly.length)}축, 선택 ${formatInt(selectedCount)}축, ${changed ? '저장 필요' : '저장됨'}`;
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
          const editable = !(row.motor && row.motor.deleted);
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
            eepromAlias: eepromView.text,
            eepromMismatch: eepromView.mismatch,
            rotaryAlias: rotaryView.text,
            rotaryMismatch: rotaryView.mismatch,
            slavePosition: slaveView.text,
            slaveMismatch: slaveView.mismatch,
            axisValue,
            editable,
            onOff,
          };
        });
      const renderSignature = JSON.stringify(rowViews.map((view) => ({
        id: view.row.id,
        selected: view.selected,
        axis: view.axisValue,
        idText: view.idText,
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
              <td><input class="axis-edit-input axis-number-input mono" data-axis-edit="axis" data-axis-row-id="${escapeHtml(row.id)}" type="number" min="0" step="1" value="${escapeHtml(view.axisValue ?? '')}"${disabled}></td>
              <td class="axis-id-cell mono">${displayText(view.idText)}</td>
              <td class="mono${view.eepromMismatch ? ' identity-mismatch' : ''}">${displayText(view.eepromAlias)}</td>
              <td class="mono${view.rotaryMismatch ? ' identity-mismatch' : ''}">${displayText(view.rotaryAlias)}</td>
              <td class="mono${view.slaveMismatch ? ' identity-mismatch' : ''}">${displayText(view.slavePosition)}</td>
              <td>${displayText(view.typeText)}</td>
              <td><input class="axis-edit-input axis-name-input" data-axis-edit="name" data-axis-row-id="${escapeHtml(row.id)}" value="${escapeHtml(view.name === '-' ? '' : view.name)}"${disabled}></td>
              <td>${displayText(driverLabel(row))}</td>
              <td><span class="match-state ${escapeHtml(view.settingClass)}">${displayText(view.settingText)}</span></td>
              <td><span class="match-state ${escapeHtml(view.scanClass)}">${displayText(view.scanText)}</span></td>
              <td><span class="match-state ${escapeHtml(view.runtimeClass)}">${displayText(view.runtimeText)}</span></td>
              <td class="mono">${displayText(view.onOff)}</td>
            </tr>
          `;
        }).join('')
          : '<tr><td colspan="13" class="empty">설정 파일을 불러오거나 모터 스캔을 실행하세요</td></tr>';
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
    motorConfigFileNameDraft = pathBasename(motorConfigFilePath);
    lastConfigTableRenderSignature = '';
    lastConfigRawTextRenderSignature = '';
    if (el.motorConfigState) {
      el.motorConfigState.textContent = payload.success === false
        ? uiMessage(payload.message, '설정 파일 불러오기 실패')
        : '설정 파일 불러옴';
    }
    renderAxisSettings();
    renderLatestState();
  }

  async function fetchRegistry() {
    setStatusMessage('설정 파일 불러오는 중');
    if (el.reloadMotorConfigButton) el.reloadMotorConfigButton.disabled = true;
    try {
      const payload = await fetchMotorConfig();
      applyMotorConfigPayload(payload);
      const message = payload.success === false
        ? uiMessage(payload.message, '설정 파일 불러오기 실패')
        : `설정 파일 불러옴 ${new Date().toLocaleTimeString()}`;
      setStatusMessage(message);
      setAxisMessage(message);
    } catch (error) {
      savedRegistry = normalizeAxisRegistry({});
      axisConfig = normalizeAxisRegistry({});
      configTableDrafts = new Map();
      motorConfigRawText = '';
      savedMotorConfigRawText = '';
      motorConfigFilePath = '';
      lastConfigTableRenderSignature = '';
      lastConfigRawTextRenderSignature = '';
      setStatusMessage('설정 파일 불러오기 실패');
      setAxisMessage('설정 파일 불러오기 실패');
      renderAxisSettings();
    } finally {
      if (el.reloadMotorConfigButton) el.reloadMotorConfigButton.disabled = false;
    }
  }

  async function loadProjectRegistry() {
    latestScan = null;
    selectedAxisIds = new Set();
    selectedConfigMotorId = '';
    rowEditDrafts = new Map();
    configTableDrafts = new Map();
    lastAxisRenderSignature = '';
    if (el.scanResult) el.scanResult.textContent = '새 프로젝트에서 아직 검색하지 않았습니다';
    if (el.scanAllResult) {
      el.scanAllResult.textContent = '검색 전 · 새로 발견된 축은 자동으로 선택됩니다';
    }
    if (el.dynamixelScanResult) {
      el.dynamixelScanResult.textContent = '새 프로젝트에서 아직 검색하지 않았습니다';
    }
    await fetchRegistry();
  }

  async function resetAllAxisConfig() {
    const confirmed = window.confirm(
      '현재 프로젝트의 모터축 설정을 전부 비우는 편집 초안을 만듭니다.\n'
      + '프로젝트 파일은 변경 내용 저장을 눌러야 바뀝니다.\n'
      + '실행 중인 장비에는 자동 적용되지 않습니다.',
    );
    if (!confirmed) return;
    axisConfig = normalizeAxisRegistry({ version: 1, updated_at: null, motors: [] });
    selectedAxisIds = new Set();
    selectedConfigMotorId = '';
    rowEditDrafts = new Map();
    configTableDrafts = new Map();
    lastAxisRenderSignature = '';
    setStatusMessage('모터축 설정 전체 초기화 예정 · 저장 필요');
    setAxisMessage('모터축 설정을 비우는 초안입니다. 변경 내용 저장 전에는 파일이 바뀌지 않습니다.');
    renderAxisSettings();
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
        return `Control Index ${formatInt(motorAxisValue(motor))}의 EEPROM Alias를 검색 결과에서 찾지 못했습니다.`;
      }
      const expectedRotary = motor.identity?.rotary_alias;
      const expectedSlave = motor.identity?.slave_position;
      if (expectedRotary === null || expectedRotary === undefined ||
          expectedSlave === null || expectedSlave === undefined ||
          scanRow.rotary_alias === null || scanRow.rotary_alias === undefined ||
          scanRow.slave_position === null || scanRow.slave_position === undefined) {
        return `Control Index ${formatInt(motorAxisValue(motor))}의 연결정보 확인 및 업데이트가 필요합니다.`;
      }
      if (Number(expectedRotary) !== Number(scanRow.rotary_alias)) {
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
      if (expectedStation !== null && expectedStation !== undefined &&
          observedStation !== null && observedStation !== undefined) {
        if (Number(expectedStation) !== Number(observedStation)) return '';
        continue;
      }
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
        setAxisMessage('파일 저장 전 표 업데이트를 먼저 누르세요.');
        return false;
      }
      const fileName = normalizedMotorConfigFileName() || pathBasename(motorConfigFilePath);
      const payload = await saveMotorConfig(
        hasMotorConfigTableSaveChanges()
          ? { content: motorConfigRawText, file_name: fileName }
          : { registry: saveableAxisRegistry(axisConfig), file_name: fileName },
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
      setAxisMessage('프로젝트 축 목록 저장됨. 실제 반영은 4단계의 설정 적용 및 재시작을 눌러야 합니다.');
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
    const identityError = acHardwareIdentityErrorMessage();
    if (identityError && !recoveryMessage) {
      window.alert(identityError);
      setAxisMessage(identityError, true);
      renderAxisSettings();
      return false;
    }

    const recoveryWarning = recoveryMessage
      ? `복구 적용 안내:\n${recoveryMessage}\n\n`
      : '';
    const confirmed = window.confirm(
      recoveryWarning
      + '주의: 설정 적용 중 motor_manager_node를 재시작합니다.\n\n'
      + '재시작 중에는 AC 서보 / 다이나믹셀 통신이 잠시 끊기거나 재초기화될 수 있습니다.\n'
      + '현재 서보가 부하를 잡고 있는 축은 순간적으로 토크가 해제되어 부하가 풀릴 수 있습니다.\n'
      + '이때 중력, 외력, 기구 하중 때문에 의도하지 않은 움직임이 발생할 수 있습니다.\n\n'
      + '기구를 안전하게 지지하고, 작업자 접근을 막고, 움직여도 위험하지 않은 상태에서만 진행하세요.\n'
      + '웹 연결은 잠깐 끊긴 뒤 자동으로 다시 연결됩니다.\n\n'
      + '위 위험을 확인했고 설정을 적용하기 위해 노드를 재시작할까요?'
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

  function updateSelectedAxisIdentity() {
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
    ];
    const changeText = changes.map(([label, before, after]) => (
      `${label}: ${before ?? '미등록'} → ${after ?? '확인 불가'}`
    )).join('\n');
    const confirmed = window.confirm(
      `Control Index ${formatInt(motorAxisValue(motor))}의 연결값을 변경합니다.\n\n`
      + `${changeText}\n\n`
      + '이 검색 장비가 프로젝트의 해당 축이 맞는지 확인했습니까?\n'
      + '확인 후에도 변경 내용 저장을 눌러야 프로젝트 파일에 반영됩니다.',
    );
    if (!confirmed) {
      setAxisMessage('연결정보 반영 취소');
      return;
    }

    const eepromAlias = scanRow.ethercat_alias ?? 0;
    const updated = normalizeMotor({
      ...motor,
      identity: {
        ...oldIdentity,
        ethercat_alias: eepromAlias,
        rotary_alias: scanRow.rotary_alias ?? null,
        slave_position: scanRow.slave_position ?? null,
        driver_model: scanRow.driver_model || oldIdentity.driver_model || '',
      },
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
    const input = window.prompt(
      `Slave Position ${formatInt(scanRow.slave_position)}의 새 EEPROM Alias를 입력하세요.\n`
      + '범위: 0~65535 (0은 Alias 제거)',
      String(currentAlias),
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
    const confirmed = window.confirm(
      '실제 서보 드라이버의 SII EEPROM 값을 변경합니다.\n\n'
      + `Slave Position: ${formatInt(scanRow.slave_position)}\n`
      + `Serial Number: ${formatInt(scanRow.serial_number)}\n`
      + `EEPROM Alias: ${formatInt(currentAlias)} → ${formatInt(newAlias)}\n\n`
      + '프로젝트 파일은 자동 변경되지 않습니다.\n'
      + '쓰기 후 서보 드라이버 제어 전원을 재투입하고 다시 검색해야 합니다.\n'
      + '선택한 실제 장비와 새 Alias를 확인했습니까?',
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
    const matching = scan.matching_summary || {};
    const names = slaves.map((slave) => {
      const slaveText = `Slave ${formatInt(slave.slave_position)}`;
      const driver = slave.driver_model || slave.device_name || '확인 불가';
      const alias = slave.ethercat_alias !== null && slave.ethercat_alias !== undefined
        ? `alias ${formatInt(slave.ethercat_alias)}`
        : 'alias -';
      return `${slaveText} (${driver}, ${alias})`;
    });
    const slaveText = names.length > 0 ? names.join(', ') : '없음';
    el.scanResult.textContent = `AC 서보 검색 결과: EtherCAT ${formatInt(slaves.length)}축, 매칭 ${formatInt(matching.matched || 0)}축, 설정 외 ${formatInt(matching.unregistered || 0)}축, 검색된 축: ${slaveText}`;
  }

  function renderDynamixelScan(scan) {
    if (!el.dynamixelScanResult) return;
    const dynamixelScan = scan?.dynamixel_scan;
    if (!dynamixelScan) {
      el.dynamixelScanResult.textContent = '다이나믹셀 검색 안 함';
      return;
    }
    const devices = Array.isArray(dynamixelScan.devices) ? dynamixelScan.devices : [];
    const targetCount = Array.isArray(dynamixelScan.targets) ? dynamixelScan.targets.length : 0;
    const targetPorts = Array.isArray(dynamixelScan.targets)
      ? [...new Set(dynamixelScan.targets.map((target) => target.port || '-'))]
      : [];
    const targetBaudrates = Array.isArray(dynamixelScan.targets)
      ? [...new Set(dynamixelScan.targets.map((target) => target.baudrate).filter((baudrate) => baudrate))]
      : [];
    const protocol = dynamixelScan.protocol || '2.0';
    const targetText = `${targetPorts.join(', ') || '-'}, protocol ${protocol}, baudrate ${targetBaudrates.map(formatInt).join(', ') || '-'}`;
    const deviceText = devices.length > 0
      ? devices.map((device) => {
        const model = modelTextFromDevice(device) || '모델 확인 불가';
        return `ID ${formatInt(device.id)} (${model})`;
      }).join(', ')
      : '없음';
    const warningText = dynamixelScan.warning ? ` / ${dynamixelScan.warning}` : '';
    const errorText = dynamixelScan.error ? ` / ${dynamixelScan.error}` : '';
    if (dynamixelScan.mode === 'runtime_topic') {
      el.dynamixelScanResult.textContent = `다이나믹셀 실행 상태 연결 확인: 온라인 피드백 ${formatInt(devices.length)}개, ${deviceText} / 실행 중인 제어기의 피드백 기준${warningText}${errorText}`;
    } else {
      el.dynamixelScanResult.textContent = `다이나믹셀 직접 응답 확인 결과: 후보 ${formatInt(targetCount)}개 (${targetText}), 감지 ${formatInt(devices.length)}개, ${deviceText}${warningText}${errorText}`;
    }
  }

  async function scanMotors() {
    if (!el.scanButton) return;
    el.scanButton.disabled = true;
    const originalText = el.scanButton.textContent;
    el.scanButton.textContent = '검색 중';
    if (el.scanResult) el.scanResult.textContent = 'AC 서보 검색 중';
    try {
      const payload = await requestAcServoScan();
      renderScan(mergeAcServoScan(payload.scan));
      const selectedCount = autoSelectNewScanAxes();
      const identityError = acHardwareIdentityErrorMessage();
      setAxisMessage(identityError
        ? `${identityError} 기존 프로젝트 축과 검색 축을 선택해 연결정보 반영을 확인하세요.`
        : `AC 서보 검색 완료 · 신규 ${formatInt(selectedCount)}축 자동 선택`, Boolean(identityError));
      if (payload.motion_state) renderLatestState(payload.motion_state);
      el.scanButton.textContent = payload.success ? '검색 완료' : '검색 실패';
    } catch (error) {
      if (el.scanResult) el.scanResult.textContent = 'AC 서보 검색 실패';
      el.scanButton.textContent = '검색 실패';
    } finally {
      setTimeout(() => {
        el.scanButton.textContent = originalText;
        el.scanButton.disabled = false;
      }, 1200);
    }
  }

  async function scanDynamixel() {
    if (!el.dynamixelScanButton) return;
    el.dynamixelScanButton.disabled = true;
    const originalText = el.dynamixelScanButton.textContent;
    el.dynamixelScanButton.textContent = '검색 중';
    if (el.dynamixelScanResult) el.dynamixelScanResult.textContent = '다이나믹셀 연결 확인 중';
    try {
      const payload = await requestDynamixelScan();
      renderDynamixelScan(mergeDynamixelScan(payload.scan));
      const selectedCount = autoSelectNewScanAxes();
      setAxisMessage(`다이나믹셀 검색 완료 · 신규 ${formatInt(selectedCount)}축 자동 선택`);
      if (payload.motion_state) renderLatestState(payload.motion_state);
      el.dynamixelScanButton.textContent = payload.success ? '검색 완료' : '검색 실패';
    } catch (error) {
      if (el.dynamixelScanResult) el.dynamixelScanResult.textContent = '다이나믹셀 연결 확인 실패';
      el.dynamixelScanButton.textContent = '검색 실패';
    } finally {
      setTimeout(() => {
        el.dynamixelScanButton.textContent = originalText;
        el.dynamixelScanButton.disabled = false;
      }, 1200);
    }
  }

  async function scanAllMotors() {
    if (!el.scanAllButton) return;
    el.scanAllButton.disabled = true;
    const originalText = el.scanAllButton.textContent;
    el.scanAllButton.textContent = '전체 검색 중';
    if (el.scanAllResult) el.scanAllResult.textContent = 'AC 서보와 다이나믹셀을 검색하고 있습니다';
    try {
      const payload = await requestMotorScan();
      latestScan = payload.scan || null;
      renderScan(latestScan);
      renderDynamixelScan(latestScan);
      const selectedCount = autoSelectNewScanAxes();
      const summary = getDiscoverySummary();
      const identityError = acHardwareIdentityErrorMessage();
      if (el.scanAllResult) {
        el.scanAllResult.textContent = payload.success
          ? `검색 완료 · AC 서보 ${formatInt(summary.ethercatCount)}축 · 다이나믹셀 ${formatInt(summary.dynamixelCount)}축 · 신규 ${formatInt(selectedCount)}축 자동 선택`
          : uiMessage(payload.message, '전체 모터 검색 실패');
      }
      setAxisMessage(payload.success
        ? identityError
          ? `${identityError} 기존 프로젝트 축과 검색 축을 선택해 연결정보 반영을 확인하세요.`
          : `신규 ${formatInt(selectedCount)}축을 자동 선택했습니다. 이름과 축 번호를 확인한 뒤 선택 축 추가를 누르세요.`
        : uiMessage(payload.message, '전체 모터 검색 실패'), Boolean(identityError) || !payload.success);
      if (payload.motion_state) renderLatestState(payload.motion_state);
      el.scanAllButton.textContent = payload.success ? '검색 완료' : '검색 실패';
    } catch (error) {
      if (el.scanAllResult) el.scanAllResult.textContent = `전체 모터 검색 실패: ${error?.message || error}`;
      setAxisMessage('전체 모터 검색에 실패했습니다. 고급 종류별 검색으로 연결을 확인할 수 있습니다.');
      el.scanAllButton.textContent = '검색 실패';
    } finally {
      setTimeout(() => {
        el.scanAllButton.textContent = originalText;
        el.scanAllButton.disabled = false;
      }, 1200);
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
        if (event.target.closest('[data-axis-edit]')) return;
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
    if (el.writeEthercatAliasButton) {
      el.writeEthercatAliasButton.addEventListener('click', writeSelectedEthercatAlias);
    }
    if (el.deleteAxisButton) el.deleteAxisButton.addEventListener('click', deleteSelectedAxis);
    if (el.toggleAxisButton) el.toggleAxisButton.addEventListener('click', toggleSelectedAxis);
    if (el.sortAxisButton) el.sortAxisButton.addEventListener('click', sortAxisNumbers);
    if (el.saveAxisConfigButton) el.saveAxisConfigButton.addEventListener('click', saveAxisConfig);
    if (el.resetAxisConfigButton) el.resetAxisConfigButton.addEventListener('click', resetAllAxisConfig);
    if (el.saveConfigTableButton) el.saveConfigTableButton.addEventListener('click', saveAxisConfig);
    if (el.applyAxisConfigButton) el.applyAxisConfigButton.addEventListener('click', applyConfigRestart);
    if (el.updateConfigTableButton) el.updateConfigTableButton.addEventListener('click', applyConfigTableUpdates);
    if (el.motorConfigFileNameInput) {
      el.motorConfigFileNameInput.addEventListener('input', (event) => {
        motorConfigFileNameDraft = event.target.value || '';
        setAxisMessage('파일명 변경값은 파일 저장을 누르면 적용됩니다.');
        renderAxisSettings();
      });
    }
    if (el.reloadMotorConfigButton) el.reloadMotorConfigButton.addEventListener('click', fetchRegistry);
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
    renderAfterDisplayModeChange,
    renderRuntimeState,
    renderRegistrationTabs,
    shouldShowMonitoringMotor,
  };
}
