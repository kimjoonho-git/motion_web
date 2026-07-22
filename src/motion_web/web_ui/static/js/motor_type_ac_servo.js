import { escapeHtml } from './format.js';
import { normalizeMotor } from './motor_registry.js';

export function yamlRegisteredScanRow(row) {
  if (!row) return false;
  if (row.match_state === 'unregistered') return false;
  return row.controller_index !== null && row.controller_index !== undefined;
}

export function detectedScanRow(row) {
  return Boolean(row && row.slave_position !== null && row.slave_position !== undefined);
}

export function motorIdFromScan(row) {
  if (isAssignedAlias(row.ethercat_alias)) {
    return `ac_servo_ethercat_alias_${row.ethercat_alias}`;
  }
  if (isAssignedAlias(row.rotary_alias)) {
    return `ac_servo_ethercat_rotary_${row.rotary_alias}`;
  }
  return `ac_servo_ethercat_master_${row.master_index ?? 0}_slave_${row.slave_position}`;
}

function isAssignedAlias(value) {
  if (value === null || value === undefined || value === '') return false;
  const alias = Number(value);
  return Number.isInteger(alias) && alias > 0;
}

export function scanKey(row) {
  if (!row) return '';
  return `master:${row.master_index ?? 0}:slave:${row.slave_position ?? '-'}`;
}

export function scanRowMatchesRegistryMotor(row, motor) {
  const identity = motor.identity || {};
  const configuredAlias = motor.config?.alias ?? identity.ethercat_alias;
  const configuredSerial = motor.config?.serial_number ?? identity.serial_number;
  if (
    configuredSerial !== null && configuredSerial !== undefined &&
    row.serial_number !== null && row.serial_number !== undefined &&
    Number(configuredSerial) !== Number(row.serial_number)
  ) return false;
  if (isAssignedAlias(configuredAlias) &&
      row.ethercat_alias !== null && row.ethercat_alias !== undefined) {
    return Number(row.ethercat_alias) === Number(configuredAlias);
  }
  // Alias 0 is an unconfigured value, not a physical identity requirement.
  // In that mode the EtherCAT chain position is the stable key used by the
  // current runtime configuration, even if the drive EEPROM contains a
  // non-zero alias left by a previous installation.
  if (!isAssignedAlias(configuredAlias)) {
    const configuredPosition = identity.slave_position ?? motor.config?.position;
    if (
      configuredPosition !== null && configuredPosition !== undefined &&
      row.slave_position !== null && row.slave_position !== undefined
    ) {
      return Number(configuredPosition) === Number(row.slave_position);
    }
  }
  if (isAssignedAlias(identity.rotary_alias) && isAssignedAlias(row.rotary_alias)) {
    return Number(row.rotary_alias) === Number(identity.rotary_alias);
  }
  if (
    identity.slave_position !== null && identity.slave_position !== undefined &&
    row.slave_position !== null && row.slave_position !== undefined
  ) {
    return Number(row.slave_position) === Number(identity.slave_position);
  }
  const controllerIndex = motor.config?.controller_index ?? motor.axis;
  return controllerIndex !== null &&
    controllerIndex !== undefined &&
    row.controller_index !== null &&
    row.controller_index !== undefined &&
    Number(controllerIndex) === Number(row.controller_index);
}

export function scanRowMatchesRuntimeMotor(row, motor) {
  if (!row || !motor) return false;
  if (motor.alias !== null && motor.alias !== undefined &&
      row.ethercat_alias !== null && row.ethercat_alias !== undefined) {
    if (Number(motor.alias) === 0 && Number(row.ethercat_alias) === 0) {
      return row.controller_index !== null && row.controller_index !== undefined &&
        motor.controller_index !== null && motor.controller_index !== undefined &&
        Number(row.controller_index) === Number(motor.controller_index);
    }
    return Number(row.ethercat_alias) === Number(motor.alias);
  }
  return row.controller_index !== null &&
    row.controller_index !== undefined &&
    motor.controller_index !== null &&
    motor.controller_index !== undefined &&
    Number(row.controller_index) === Number(motor.controller_index);
}

export function runtimeMotorConfirmsRegistryMotor(motor, runtime) {
  if (!motor || !runtime) return false;
  const configuredAxis = motor.config?.controller_index ?? motor.axis;
  if (configuredAxis === null || configuredAxis === undefined ||
      runtime.controller_index === null || runtime.controller_index === undefined ||
      Number(configuredAxis) !== Number(runtime.controller_index)) return false;

  const configuredPosition = motor.identity?.slave_position ?? motor.config?.position;
  if (configuredPosition === null || configuredPosition === undefined ||
      runtime.slave_position === null || runtime.slave_position === undefined ||
      Number(configuredPosition) !== Number(runtime.slave_position)) return false;

  const configuredAlias = motor.config?.alias ?? motor.identity?.ethercat_alias;
  if (isAssignedAlias(configuredAlias) &&
      runtime.alias !== null && runtime.alias !== undefined &&
      Number(configuredAlias) !== Number(runtime.alias)) return false;

  const configuredModel = String(motor.identity?.driver_model || '').trim();
  const runtimeModel = String(runtime.driver_model || '').trim();
  if (configuredModel && runtimeModel && configuredModel !== runtimeModel) return false;

  return String(runtime.connection_state || '') === 'online' &&
    runtime.connection_confirmed === true;
}

export function runtimeIsAcServo(motor) {
  const value = [
    motor?.motor_type,
    motor?.motor_type_label,
    motor?.transport,
    motor?.transport_label,
    motor?.driver_model,
    motor?.driver_name,
  ].join(' ').toLowerCase();
  return value.includes('minas') ||
    value.includes('madln') ||
    value.includes('panasonic') ||
    value.includes('ac_servo') ||
    value.includes('ac servo') ||
    value.includes('ethercat');
}

export function scanRowToMotor(row, nextAvailableAxis) {
  const axis = row.controller_index === null || row.controller_index === undefined
    ? nextAvailableAxis()
    : Number(row.controller_index);
  const ethercatAlias = row.ethercat_alias ?? null;
  const position = Number(ethercatAlias) === 0
    ? Number(row.slave_position ?? 0)
    : 0;
  const name = ethercatAlias !== null && ethercatAlias !== undefined
    ? `alias ${ethercatAlias}`
    : `slave ${row.slave_position ?? '-'}`;
  return normalizeMotor({
    id: motorIdFromScan(row),
    enabled: true,
    hidden: false,
    deleted: false,
    axis,
    name,
    motor_type: 'ac_servo',
    driver_family: 'minas',
    transport: 'ethercat',
    identity: {
      rotary_alias: row.rotary_alias ?? null,
      ethercat_alias: ethercatAlias,
      slave_position: row.slave_position ?? null,
      driver_model: row.driver_model || '',
      serial_number: row.serial_number ?? null,
    },
    config: {
      controller_index: axis,
      driver_id: 0,
      alias: ethercatAlias,
      position,
      vendor_id: row.vendor_id ?? null,
      product_id: row.product_code ?? null,
      revision_number: row.revision_number ?? null,
      serial_number: row.serial_number ?? null,
      profile_mode: 0,
    },
  });
}

export function datasetNumber(value, fallback = null) {
  if (value === null || value === undefined || value === '') return fallback;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

export function scanRowButtonAttrs(row) {
  if (!row) return '';
  return [
    `data-scan-master-index="${escapeHtml(String(row.master_index ?? 0))}"`,
    `data-scan-controller-index="${escapeHtml(String(row.controller_index ?? ''))}"`,
    `data-scan-ethercat-alias="${escapeHtml(String(row.ethercat_alias ?? ''))}"`,
    `data-scan-rotary-alias="${escapeHtml(String(row.rotary_alias ?? ''))}"`,
    `data-scan-slave-position="${escapeHtml(String(row.slave_position ?? ''))}"`,
    `data-scan-driver-model="${escapeHtml(String(row.driver_model || ''))}"`,
    `data-scan-vendor-id="${escapeHtml(String(row.vendor_id ?? ''))}"`,
    `data-scan-product-code="${escapeHtml(String(row.product_code ?? ''))}"`,
    `data-scan-match-state="${escapeHtml(String(row.match_state || ''))}"`,
  ].join(' ');
}

export function scanRowFromButton(button) {
  if (!button || button.dataset.scanSlavePosition === undefined) return null;
  return {
    master_index: datasetNumber(button.dataset.scanMasterIndex, 0),
    controller_index: datasetNumber(button.dataset.scanControllerIndex),
    ethercat_alias: datasetNumber(button.dataset.scanEthercatAlias),
    rotary_alias: datasetNumber(button.dataset.scanRotaryAlias),
    slave_position: datasetNumber(button.dataset.scanSlavePosition),
    driver_model: button.dataset.scanDriverModel || '',
    vendor_id: datasetNumber(button.dataset.scanVendorId),
    product_code: datasetNumber(button.dataset.scanProductCode),
    match_state: button.dataset.scanMatchState || '',
  };
}
