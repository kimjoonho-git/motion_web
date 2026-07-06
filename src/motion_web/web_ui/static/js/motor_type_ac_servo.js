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
  if (row.rotary_alias !== null && row.rotary_alias !== undefined) {
    return `ac_servo_ethercat_rotary_${row.rotary_alias}`;
  }
  if (row.ethercat_alias !== null && row.ethercat_alias !== undefined) {
    return `ac_servo_ethercat_alias_${row.ethercat_alias}`;
  }
  return `ac_servo_ethercat_slave_${row.slave_position}`;
}

export function scanKey(row) {
  if (!row) return '';
  if (row.rotary_alias !== null && row.rotary_alias !== undefined) {
    return `rotary:${row.rotary_alias}`;
  }
  if (row.ethercat_alias !== null && row.ethercat_alias !== undefined) {
    return `ethercat:${row.ethercat_alias}`;
  }
  return `slave:${row.slave_position}`;
}

export function scanRowMatchesRegistryMotor(row, motor) {
  const identity = motor.identity || {};
  if (
    row.rotary_alias !== null &&
    row.rotary_alias !== undefined &&
    identity.rotary_alias !== null &&
    identity.rotary_alias !== undefined &&
    Number(row.rotary_alias) === Number(identity.rotary_alias)
  ) {
    return true;
  }
  if (
    row.ethercat_alias !== null &&
    row.ethercat_alias !== undefined &&
    identity.ethercat_alias !== null &&
    identity.ethercat_alias !== undefined &&
    Number(row.ethercat_alias) === Number(identity.ethercat_alias)
  ) {
    return true;
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
  if (
    row.ethercat_alias !== null &&
    row.ethercat_alias !== undefined &&
    motor.alias !== null &&
    motor.alias !== undefined &&
    Number(row.ethercat_alias) === Number(motor.alias)
  ) {
    return true;
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
      position: 0,
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
