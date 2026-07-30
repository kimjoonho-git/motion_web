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
  const masterIndex = Number(row?.master_index ?? 0);
  if (isAssignedAlias(row.ethercat_alias)) {
    return `ac_servo_ethercat_master_${masterIndex}_alias_${row.ethercat_alias}`;
  }
  if (isAssignedAlias(row.rotary_alias)) {
    return `ac_servo_ethercat_master_${masterIndex}_rotary_${row.rotary_alias}`;
  }
  return `ac_servo_ethercat_master_${masterIndex}_slave_${row.slave_position}`;
}

function isAssignedAlias(value) {
  if (value === null || value === undefined || value === '') return false;
  const alias = Number(value);
  return Number.isInteger(alias) && alias > 0;
}

// Entries may be added only from a verified manufacturer/product catalog.
// SII Order Number and Device Name are intentionally not catalog keys.
const VERIFIED_AC_SERVO_MODELS = Object.freeze({});

export function verifiedAcServoModel(row) {
  const vendor = Number(row?.vendor_id);
  const product = Number(row?.product_code);
  const revision = Number(row?.revision_number);
  if (![vendor, product, revision].every(Number.isInteger)) return '';
  return VERIFIED_AC_SERVO_MODELS[`${vendor}:${product}:${revision}`] || '';
}

export function siiReportedAcServoModel(row) {
  return String(
    row?.sii_order_number
    || row?.order_number
    || row?.sii_device_name
    || row?.device_name
    || '',
  ).trim();
}

export function scanKey(row) {
  if (!row) return '';
  return `master:${row.master_index ?? 0}:slave:${row.slave_position ?? '-'}`;
}

export function configuredEthercatMasterIndex(motor) {
  const value = motor?.config?.ethercat_master_index
    ?? motor?.identity?.ethercat_master_index
    ?? 0;
  const parsed = Number(value);
  return Number.isInteger(parsed) && parsed >= 0 ? parsed : 0;
}

export function duplicateEthercatAddress(motors) {
  const addresses = new Map();
  for (const motor of Array.isArray(motors) ? motors : []) {
    if (!motor || motor.transport !== 'ethercat' || motor.deleted) continue;
    const masterIndex = configuredEthercatMasterIndex(motor);
    const alias = Number(
      motor.identity?.ethercat_alias
      ?? motor.config?.alias
      ?? 0,
    );
    const addressType = alias === 0 ? 'position' : 'alias';
    const value = alias === 0
      ? Number(motor.config?.position ?? motor.identity?.slave_position ?? 0)
      : alias;
    const key = `${masterIndex}:${addressType}:${value}`;
    if (addresses.has(key)) {
      return { masterIndex, addressType, value };
    }
    addresses.set(key, motor);
  }
  return null;
}

function scanAndMotorShareMaster(row, motor) {
  const scanned = Number(row?.master_index ?? 0);
  return Number.isInteger(scanned) &&
    scanned >= 0 &&
    scanned === configuredEthercatMasterIndex(motor);
}

export function scanRowMatchesRegistryMotor(row, motor) {
  if (!scanAndMotorShareMaster(row, motor)) return false;
  const identity = motor.identity || {};
  const configuredAlias = motor.config?.alias ?? identity.ethercat_alias;
  const configuredSerial = motor.config?.serial_number ?? identity.serial_number;
  if (
    configuredSerial !== null && configuredSerial !== undefined &&
    row.serial_number !== null && row.serial_number !== undefined
  ) {
    return Number(configuredSerial) === Number(row.serial_number);
  }
  if (configuredSerial !== null && configuredSerial !== undefined) return false;
  if (isAssignedAlias(configuredAlias) &&
      row.ethercat_alias !== null && row.ethercat_alias !== undefined) {
    return Number(row.ethercat_alias) === Number(configuredAlias);
  }
  if (isAssignedAlias(identity.rotary_alias) && isAssignedAlias(row.rotary_alias)) {
    return Number(row.rotary_alias) === Number(identity.rotary_alias);
  }
  // Slave Position and Control Index describe topology/configuration, not a
  // stable physical device.  When all aliases are zero, cable reconnection can
  // change chain positions. Require one explicit user association so the
  // directly read Serial Number is stored for subsequent automatic matching.
  return false;
}

export function scanRowSharesConfiguredPosition(row, motor) {
  if (!row || !motor || motor.transport !== 'ethercat') return false;
  if (!scanAndMotorShareMaster(row, motor)) return false;
  const configuredPosition = motor.identity?.slave_position ?? motor.config?.position;
  return configuredPosition !== null &&
    configuredPosition !== undefined &&
    row.slave_position !== null &&
    row.slave_position !== undefined &&
    Number(configuredPosition) === Number(row.slave_position);
}

export function resolveRegistryMotorForScanRow(row, motors) {
  const configured = Array.isArray(motors)
    ? motors.filter((motor) => motor && motor.transport === 'ethercat')
    : [];
  const identityMatch = configured.find(
    (motor) => scanRowMatchesRegistryMotor(row, motor),
  ) || null;
  if (identityMatch) {
    return { motor: identityMatch, confirmationRequired: false };
  }
  const positionMatches = configured.filter(
    (motor) => scanRowSharesConfiguredPosition(row, motor),
  );
  if (positionMatches.length !== 1) return null;
  return { motor: positionMatches[0], confirmationRequired: true };
}

export function scanRowMatchesRuntimeMotor(row, motor) {
  if (!row || !motor) return false;
  const runtimeMaster = Number(motor.ethercat_master_index ?? 0);
  if (runtimeMaster !== Number(row.master_index ?? 0)) return false;
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
  if (
    configuredEthercatMasterIndex(motor) !==
    Number(runtime.ethercat_master_index ?? 0)
  ) return false;
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

  const configuredModel = String(motor.profile?.driver_model || '').trim();
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
  const catalogModel = verifiedAcServoModel(row);
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
      ethercat_master_index: Number(row.master_index ?? 0),
      rotary_alias: row.rotary_alias ?? null,
      ethercat_alias: ethercatAlias,
      slave_position: row.slave_position ?? null,
      identity_source: row.identity_source || 'physical_sii',
      vendor_id: row.vendor_id ?? null,
      product_code: row.product_code ?? null,
      revision_number: row.revision_number ?? null,
      serial_number: row.serial_number ?? null,
      sii_order_number: row.sii_order_number || row.order_number || '',
      sii_device_name: row.sii_device_name || row.device_name || '',
    },
    profile: {
      driver_model: catalogModel,
      model_confirmed: Boolean(catalogModel),
      model_source: catalogModel ? 'verified_catalog' : '',
    },
    config: {
      controller_index: axis,
      ethercat_master_index: Number(row.master_index ?? 0),
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
    `data-scan-sii-order-number="${escapeHtml(String(row.sii_order_number || row.order_number || ''))}"`,
    `data-scan-sii-device-name="${escapeHtml(String(row.sii_device_name || row.device_name || ''))}"`,
    `data-scan-vendor-id="${escapeHtml(String(row.vendor_id ?? ''))}"`,
    `data-scan-product-code="${escapeHtml(String(row.product_code ?? ''))}"`,
    `data-scan-revision-number="${escapeHtml(String(row.revision_number ?? ''))}"`,
    `data-scan-serial-number="${escapeHtml(String(row.serial_number ?? ''))}"`,
    `data-scan-identity-source="${escapeHtml(String(row.identity_source || ''))}"`,
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
    sii_order_number: button.dataset.scanSiiOrderNumber || '',
    sii_device_name: button.dataset.scanSiiDeviceName || '',
    vendor_id: datasetNumber(button.dataset.scanVendorId),
    product_code: datasetNumber(button.dataset.scanProductCode),
    revision_number: datasetNumber(button.dataset.scanRevisionNumber),
    serial_number: datasetNumber(button.dataset.scanSerialNumber),
    identity_source: button.dataset.scanIdentitySource || '',
    match_state: button.dataset.scanMatchState || '',
  };
}
