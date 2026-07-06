import {
  applyMotorConfig,
  fetchMotorConfig,
  requestAcServoScan,
  requestDynamixelScan,
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
  normalizeMotor,
  normalizeRegistry,
  registryMotorById as selectRegistryMotorById,
  registryMotorLabel,
  upsertMotorInRegistry,
} from './motor_registry.js';
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
  let configTableDrafts = new Map();
  let selectedConfigMotorId = '';
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

  function hasAnyConfigChanges() {
    return hasAxisChanges() || hasMotorConfigDataChanges();
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

  function setAxisMessage(message) {
    if (el.axisActionMessage) el.axisActionMessage.textContent = message;
    if (el.registrySummary) el.registrySummary.textContent = message;
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
      if (
        motor.station_alias_register !== null &&
        motor.station_alias_register !== undefined &&
        identity.rotary_alias !== null &&
        identity.rotary_alias !== undefined &&
        Number(identity.rotary_alias) === Number(motor.station_alias_register)
      ) {
        return true;
      }
      if (
        motor.alias !== null &&
        motor.alias !== undefined &&
        identity.ethercat_alias !== null &&
        identity.ethercat_alias !== undefined &&
        Number(identity.ethercat_alias) === Number(motor.alias)
      ) {
        return true;
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
    if (type === 'ac_servo') return 'AC Servo';
    if (type === 'dynamixel') return 'Dynamixel';
    if (type === 'cubemars') return 'CubeMars';
    return type || 'Unknown';
  }

  function rowMotorType(row) {
    const motor = row.motor || row.proposedMotor;
    if (motor?.motor_type) return motor.motor_type;
    if (row.scanDevice) return 'dynamixel';
    if (row.scanRow) return 'ac_servo';
    return 'unknown';
  }

  function rowAxisRaw(row) {
    return firstDefined(
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
      el.updateConfigTableButton.disabled = true;
      el.updateConfigTableButton.textContent = '읽기 전용';
    }
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
    return `<span class="mono config-readonly-value">${displayText(yamlDisplayValue(row.value, row.type))}</span>`;
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
    const name = configRowValue(rows, slavePrefix, 'name') || `Axis ${axis ?? index}`;
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
      !row.path.startsWith('drivers[')
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
      if (driverType === 'minas') return 'driver raw';
      return 'driver unit';
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
                <th>항목</th>
                <th>값</th>
                <th>단위</th>
                <th>타입</th>
                <th>경로</th>
              </tr>
            </thead>
            <tbody>
              ${rows.length > 0
                ? rows.map((row) => `
                  <tr data-config-yaml-row="${escapeHtml(row.path)}">
                    <td class="config-item-cell">${displayText(yamlItemName(row))}</td>
                    <td>${configTableInput(row)}</td>
                    <td class="config-unit-cell">${displayText(yamlRowUnit(row, rows))}</td>
                    <td class="mono">${displayText(row.type)}</td>
                    <td class="mono yaml-path-cell">${displayText(row.path)}</td>
                  </tr>
                `).join('')
                : `<tr><td colspan="5" class="empty">${displayText(emptyText)}</td></tr>`}
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
            <dt>${displayText(yamlItemName(row))}</dt>
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
                <th>Master ID</th>
                <th>Type</th>
                <th>Slave 수</th>
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
      el.motorConfigTablePath.textContent = `설정 파일: ${motorConfigFilePath || '-'}`;
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
                    <th>Axis</th>
                    <th>ID</th>
                    <th>Motor Type</th>
                    <th>Name</th>
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
    if (!row || row.readonly) return;
    configTableDrafts.set(motorId, {
      value,
      tokens: row.tokens,
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
      if (Number.isInteger(draft.originalValue) && !Number.isInteger(parsed)) {
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
    setAxisMessage('설정 파일 표 업데이트 완료. 저장하려면 축 설정 저장을 누르세요.');
    renderAxisSettings();
  }

  function scanStatus(row) {
    if (row.scanRow || row.scanDevice) return ['스캔 감지', 'matched'];
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
    return [`${stateLabel(state)} / Axis ${axis}`, state === 'detected' ? 'matched' : 'review'];
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
    }
    return normalizeMotor(next);
  }

  function motorWithRowDraft(motor, row) {
    let next = normalizeMotor(motor);
    const draft = rowDraft(row);
    if (draft.name !== undefined) next = editedMotor(next, row, 'name', draft.name);
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
  }

  function handleAxisEdit(input) {
    const rowId = input.dataset.axisRowId || '';
    const field = input.dataset.axisEdit || '';
    const row = rowById(rowId);
    if (!row) return;

    if (field === 'name') {
      setAxisEditValue(row, 'name', input.value);
    } else {
      resetAxisEditInput(input, row, field);
      return;
    }

    lastAxisRenderSignature = '';
    setAxisMessage('축 설정 변경됨. 저장하려면 축 설정 저장을 누르세요.');
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
    const canAdd = addRows.length > 0;
    const canDelete = editableRows.length > 0;
    const canToggle = editableRows.length > 0;
    const canSort = hasConfiguredAxes;
    const changed = hasAnyConfigChanges();
    const canApply = hasConfiguredAxes && !changed;

    if (el.addAxisButton) el.addAxisButton.disabled = !canAdd;
    if (el.deleteAxisButton) el.deleteAxisButton.disabled = !canDelete;
    if (el.sortAxisButton) el.sortAxisButton.disabled = !canSort;
    if (el.toggleAxisButton) {
      el.toggleAxisButton.disabled = !canToggle;
      if (editableRows.length > 0) {
        const shouldTurnOn = editableRows.some((row) => !row.motor.enabled);
        el.toggleAxisButton.textContent = shouldTurnOn ? '선택 축 ON' : '선택 축 OFF';
      } else {
        el.toggleAxisButton.textContent = '축 ON/OFF';
      }
    }
    if (el.saveAxisConfigButton) el.saveAxisConfigButton.disabled = !changed;
    if (el.applyAxisConfigButton) el.applyAxisConfigButton.disabled = !canApply;
    if (el.configState) {
      el.configState.textContent = changed
        ? '저장 필요'
        : configApplyPending
          ? '적용 필요'
          : '설정 저장됨';
    }
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
      el.axisSummary.textContent = `설정 ${formatInt(configured.length)}축, OFF ${formatInt(disabled.length)}축, 스캔 미설정 ${formatInt(scanOnly.length)}축, 선택 ${formatInt(selectedCount)}축, ${changed ? '저장 필요' : '저장됨'}`;
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
              ? 'Dynamixel'
              : row.scanRow
                ? 'AC Servo'
                : motorKind(motor);
          const name = rowNameRaw(row);
          const onOff = row.motor && !row.motor.deleted
            ? (row.motor.enabled ? 'ON' : 'OFF')
            : '-';
          const idText = axisIdLabel(row);
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
              <td class="axis-readonly-cell mono">${displayText(view.axisValue ?? '-')}</td>
              <td class="axis-id-cell mono">${displayText(view.idText)}</td>
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
          : '<tr><td colspan="10" class="empty">설정 파일을 불러오거나 모터 스캔을 실행하세요</td></tr>';
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
    rowEditDrafts = new Map();
    configTableDrafts = new Map();
    motorConfigRawText = String(payload.content || '');
    savedMotorConfigRawText = motorConfigRawText;
    motorConfigFilePath = String(payload.config_file || '');
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
      return `Axis 값이 없는 축이 있습니다: ${registryMotorLabel(motor)}. 축 번호 정렬을 먼저 실행하세요.`;
    }

    const counts = new Map();
    axes.forEach((axis) => counts.set(axis, (counts.get(axis) || 0) + 1));
    const duplicate = [...counts.entries()].find(([, count]) => count > 1);
    if (duplicate) {
      return `Axis ${formatInt(duplicate[0])} 값이 중복되어 있습니다. 축 번호 정렬을 먼저 실행하세요.`;
    }

    const missing = [];
    for (let index = 0; index < motors.length; index += 1) {
      if (!counts.has(index)) missing.push(index);
    }
    if (missing.length > 0) {
      const current = axes.slice().sort((a, b) => a - b).map(formatInt).join(', ');
      return `Axis 번호가 0부터 연속으로 정렬되어 있지 않습니다. 현재 Axis: ${current}. 축 번호 정렬을 먼저 실행하세요.`;
    }

    return '';
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
      const payload = await saveMotorConfig(
        hasMotorConfigDataChanges()
          ? { content: motorConfigRawText }
          : { registry: saveableAxisRegistry(axisConfig) },
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
      setAxisMessage('축 설정 저장됨. 실제 반영은 설정 적용/노드 재시작 버튼을 누르세요.');
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
      setAxisMessage('저장하지 않은 축 설정이 있습니다. 먼저 축 설정 저장을 누르세요.');
      renderAxisSettings();
      return false;
    }
    if (!axisMotors().some((motor) => !motor.deleted)) {
      setAxisMessage('설정 적용할 축이 없습니다.');
      renderAxisSettings();
      return false;
    }

    const confirmed = window.confirm(
      '주의: 설정 적용 중 motor_manager_node를 재시작합니다.\n\n'
      + '재시작 중에는 AC Servo / Dynamixel 통신이 잠시 끊기거나 재초기화될 수 있습니다.\n'
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
      renderAxisSettings();
      return true;
    } catch (error) {
      if (error instanceof TypeError) {
        setStatusMessage('웹 연결 재시작 중');
        setAxisMessage('웹 연결이 끊겼습니다. 재연결 후 모든 모터 Servo/Torque ON 완료까지 기다립니다.');
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
      return;
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
      return;
    }
    selectedAxisIds = nextSelectedIds;
    const suffix = skippedCount > 0 ? `, 제외 ${formatInt(skippedCount)}축` : '';
    setAxisMessage(`선택 ${formatInt(changedCount)}축 추가 예정${suffix}`);
    renderAxisSettings();
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
      setAxisMessage('ON/OFF를 바꿀 설정 축을 선택하세요');
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
    setAxisMessage(`선택 ${formatInt(rows.length)}축 ${shouldTurnOn ? 'ON' : 'OFF'} 예정`);
    renderAxisSettings();
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

  function renderScan(scan) {
    latestScan = scan;
    renderAxisSettings();
    if (!el.scanResult) return;
    if (!scan) {
      el.scanResult.textContent = 'AC Servo scan 실패';
      return;
    }
    const ethercatScan = scan.ethercat_scan || {};
    const slaves = Array.isArray(ethercatScan.slaves) ? ethercatScan.slaves : [];
    const matching = scan.matching_summary || {};
    const names = slaves.map((slave) => {
      const slaveText = `Slave ${formatInt(slave.slave_position)}`;
      const driver = slave.driver_model || slave.device_name || 'Unknown';
      const alias = slave.ethercat_alias !== null && slave.ethercat_alias !== undefined
        ? `alias ${formatInt(slave.ethercat_alias)}`
        : 'alias -';
      return `${slaveText} (${driver}, ${alias})`;
    });
    const slaveText = names.length > 0 ? names.join(', ') : '없음';
    el.scanResult.textContent = `AC Servo scan 결과: EtherCAT ${formatInt(slaves.length)}축, 매칭 ${formatInt(matching.matched || 0)}축, 설정 외 ${formatInt(matching.unregistered || 0)}축, 스캔 축: ${slaveText}`;
  }

  function renderDynamixelScan(scan) {
    if (!el.dynamixelScanResult) return;
    const dynamixelScan = scan?.dynamixel_scan;
    if (!dynamixelScan) {
      el.dynamixelScanResult.textContent = 'Dynamixel scan 안함';
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
        const model = modelTextFromDevice(device) || 'Unknown model';
        return `ID ${formatInt(device.id)} (${model})`;
      }).join(', ')
      : '없음';
    const warningText = dynamixelScan.warning ? ` / ${dynamixelScan.warning}` : '';
    const errorText = dynamixelScan.error ? ` / ${dynamixelScan.error}` : '';
    el.dynamixelScanResult.textContent = `Dynamixel Wizard scan 결과: 후보 ${formatInt(targetCount)}개 (${targetText}), 감지 ${formatInt(devices.length)}개, ${deviceText}${warningText}${errorText}`;
  }

  async function scanMotors() {
    if (!el.scanButton) return;
    el.scanButton.disabled = true;
    const originalText = el.scanButton.textContent;
    el.scanButton.textContent = 'Scanning';
    if (el.scanResult) el.scanResult.textContent = 'AC Servo scan 중';
    try {
      const payload = await requestAcServoScan();
      renderScan(mergeAcServoScan(payload.scan));
      if (payload.motion_state) renderLatestState(payload.motion_state);
      el.scanButton.textContent = payload.success ? 'Scan Complete' : 'Scan Failed';
    } catch (error) {
      if (el.scanResult) el.scanResult.textContent = 'AC Servo scan 실패';
      el.scanButton.textContent = 'Scan Failed';
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
    el.dynamixelScanButton.textContent = 'Scanning';
    if (el.dynamixelScanResult) el.dynamixelScanResult.textContent = 'Dynamixel Wizard scan 중';
    try {
      const payload = await requestDynamixelScan();
      renderDynamixelScan(mergeDynamixelScan(payload.scan));
      renderAxisSettings();
      if (payload.motion_state) renderLatestState(payload.motion_state);
      el.dynamixelScanButton.textContent = payload.success ? 'Scan Complete' : 'Scan Failed';
    } catch (error) {
      if (el.dynamixelScanResult) el.dynamixelScanResult.textContent = 'Dynamixel Wizard scan 실패';
      el.dynamixelScanButton.textContent = 'Scan Failed';
    } finally {
      setTimeout(() => {
        el.dynamixelScanButton.textContent = originalText;
        el.dynamixelScanButton.disabled = false;
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
    if (el.deleteAxisButton) el.deleteAxisButton.addEventListener('click', deleteSelectedAxis);
    if (el.toggleAxisButton) el.toggleAxisButton.addEventListener('click', toggleSelectedAxis);
    if (el.sortAxisButton) el.sortAxisButton.addEventListener('click', sortAxisNumbers);
    if (el.saveAxisConfigButton) el.saveAxisConfigButton.addEventListener('click', saveAxisConfig);
    if (el.applyAxisConfigButton) el.applyAxisConfigButton.addEventListener('click', applyConfigRestart);
    if (el.updateConfigTableButton) el.updateConfigTableButton.addEventListener('click', applyConfigTableUpdates);
    if (el.reloadMotorConfigButton) el.reloadMotorConfigButton.addEventListener('click', fetchRegistry);
    if (el.scanButton) el.scanButton.addEventListener('click', scanMotors);
    if (el.dynamixelScanButton) el.dynamixelScanButton.addEventListener('click', scanDynamixel);
  }

  return {
    bindEvents,
    fetchRegistry,
    getWorkContext,
    getRegistryCount: () => activeVisibleAxisMotors().length,
    renderAfterDisplayModeChange,
    renderRuntimeState,
    renderRegistrationTabs,
    shouldShowMonitoringMotor,
  };
}
