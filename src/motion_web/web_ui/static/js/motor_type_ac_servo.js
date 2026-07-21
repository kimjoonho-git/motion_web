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
  if (row.ethercat_alias !== null && row.ethercat_alias !== undefined) {
    return `ac_servo_ethercat_alias_${row.ethercat_alias}`;
  }
  if (row.rotary_alias !== null && row.rotary_alias !== undefined) {
    return `ac_servo_ethercat_rotary_${row.rotary_alias}`;
  }
  return `ac_servo_ethercat_slave_${row.slave_position}`;
}

export function scanKey(row) {
  if (!row) return '';
  return `master:${row.master_index ?? 0}:slave:${row.slave_position ?? '-'}`;
}

export function scanRowMatchesRegistryMotor(row, motor) {
  const identity = motor.identity || {};
  const configuredAlias = motor.config?.alias ?? identity.ethercat_alias;
  if (
    configuredAlias !== null && configuredAlias !== undefined &&
    row.ethercat_alias !== null && row.ethercat_alias !== undefined
  ) {
    if (Number(configuredAlias) === 0 && Number(row.ethercat_alias) === 0) {
      const configuredPosition = identity.slave_position ?? motor.config?.position;
      return configuredPosition !== null && configuredPosition !== undefined &&
        row.slave_position !== null && row.slave_position !== undefined &&
        Number(configuredPosition) === Number(row.slave_position);
    }
    return Number(row.ethercat_alias) === Number(configuredAlias);
  }
  if (
    identity.rotary_alias !== null && identity.rotary_alias !== undefined &&
    row.rotary_alias !== null && row.rotary_alias !== undefined
  ) {
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
    },
    config: {
      controller_index: axis,
      driver_id: 0,
      alias: ethercatAlias,
      position,
      vendor_id: row.vendor_id ?? null,
      product_id: row.product_code ?? null,
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
