import { escapeHtml } from './format.js';
import { normalizeMotor } from './motor_registry.js';

export function detectedScanRow(row) {
  return Boolean(row && row.slave_position !== null && row.slave_position !== undefined);
}

function motorIdFromScan(row) {
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

function configuredEthercatMasterIndex(motor) {
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

/** 짝 맞추기는 **서버가 한다** · §6-216
 *
 * 전에는 여기에 있었다 · `scanRowMatchesRegistryMotor` ·
 * `scanRowSharesConfiguredPosition` · `resolveRegistryMotorForScanRow` ·
 * 같은 규칙이 서버에도 있어야 했고, 두 쪽이 갈리면 화면과 파일이 다른
 * 모터를 가리켰다.
 *
 * 이제 검색 응답(`scan.axis_rows`)이 짝을 지어 온다 · 규칙은
 * `motion_web_bridge/scan_axis_rows.py` 한 곳에 있고, 시험은
 * `test_the_server_pairs_the_scan.py` 가 지킨다.
 */

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
  // **검색이 읽은 모델을 쓴다** · §6-208
  //
  // `VERIFIED_AC_SERVO_MODELS` 는 비어 있다 · 그래서 여기서만 카탈로그를 보면
  // 새로 추가한 서보는 **언제나** 「모델 미확인」이 됐고, 그것을 풀어 주는 길이
  // 「선택 축 검색값 반영」 하나뿐이었다 · 사람이 그 순서를 알아야만 진도가
  // 나갔다.
  //
  // 검색은 SII EEPROM 에서 모델을 이미 읽어 온다 (화면에 「SII 참고값」으로
  // 보이던 그 값) · 「검색값 반영」 쪽은 진작 그것을 쓰고 있었다 · 두 길이
  // 같은 규칙을 쓰게 맞춘다.
  const catalogModel = verifiedAcServoModel(row);
  const siiModel = siiReportedAcServoModel(row);
  const confirmedModel = catalogModel || siiModel;
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
      driver_model: confirmedModel,
      model_confirmed: Boolean(confirmedModel),
      model_source: catalogModel
        ? 'verified_catalog'
        : (siiModel ? 'physical_sii' : ''),
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

function datasetNumber(value, fallback = null) {
  if (value === null || value === undefined || value === '') return fallback;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

