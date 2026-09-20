import { normalizeMotor } from './motor_registry.js';

const DYNAMIXEL_BAUDRATE = 1000000;

export function runtimeIsDynamixel(motor) {
  const value = [
    motor?.motor_type,
    motor?.motor_type_label,
    motor?.transport,
    motor?.transport_label,
    motor?.driver_model,
    motor?.driver_name,
  ].join(' ').toLowerCase();
  return value.includes('dynamixel') || value.includes('serial') || value.includes('xm540');
}

export function firstDefined(...values) {
  return values.find((value) => value !== null && value !== undefined && value !== '') ?? null;
}

function normalizedModelName(value) {
  return String(value || '').trim().toLowerCase();
}

/** 모델 이름은 **서버가 정한다** · §6-216
 *
 * 전에는 여기에 이름표가 있었다 · 검색기가 모델 번호 1120 을
 * `XM540-W270` 이라 읽고, 서버는 드라이버를 만들 때 `XM540-W270-R` 로
 * 적어서 같은 모터를 두 이름으로 불렀다 · 화면에 표를 하나 더 두고 두 언어를
 * 시험으로 묶어 두는 방식이었다 (누더기였다).
 *
 * 이제 검색 응답(`scan.axis_rows`)이 이미 정해진 이름을 실어 온다 · 여기서는
 * 장치가 말한 날것만 꺼내고, 이름은 서버가 준 것을 쓴다.
 */
export function modelTextFromDevice(device) {
  if (!device) return '';
  return String(
    device.model_name
    || device.model
    || device.driver_model
    || (device.model_number
      ? `Model ${Number(device.model_number).toLocaleString('ko-KR', { maximumFractionDigits: 0 })}`
      : ''),
  ).trim();
}

export function dynamixelScanDeviceKey(device) {
  return [
    device?.port || '',
    device?.baudrate ?? '',
    device?.id ?? '',
  ].join('|');
}

/** 다이나믹셀 축의 id · **서버와 글자까지 같아야 한다** · §6-209
 *
 * 저장하면 서버가 설정 파일을 읽어 id 를 **다시 만들어** 돌려준다 · 화면이
 * 다른 규칙으로 만들면 저장 직후 id 가 바뀌고, 그 id 로 기억하던 **선택이
 * 통째로 풀린다** · 실제로 축을 추가하고 저장하면 다이나믹셀 두 줄의 체크가
 * 사라졌다.
 *
 * AC 서보는 두 쪽 규칙이 이미 같아서(`ac_servo_ethercat_master_0_alias_103`)
 * 선택이 살아남았다 · 그래서 다이나믹셀만 풀리는 것으로 보였다.
 *
 * 서버 규칙 (`motor_config_rules.registry_from_motor_config`):
 *
 *     f'{motor_type}_{transport}_port_{quote(serial_port, safe="")}_id_{bus_id}'
 *
 * `quote(safe='')` 와 `encodeURIComponent` 는 이 경로에 쓰이는 글자
 * (영숫자 · `/` · `-` · `_`)에 대해 같은 결과를 낸다.
 */
export function dynamixelMotorIdFromDevice(device) {
  const port = encodeURIComponent(String(device?.port || ''));
  const busId = device?.id;
  if (busId === null || busId === undefined) {
    return `dynamixel_serial_axis_${device?.controller_index ?? 'unknown'}`;
  }
  return `dynamixel_serial_port_${port}_id_${busId}`;
}

export function dynamixelScanDeviceToMotor(device, baseMotor = null, options = {}) {
  const axis = baseMotor?.config?.controller_index ?? baseMotor?.axis ?? options.nextAvailableAxis();
  const scannedModel = String(options.model || modelTextFromDevice(device) || '').trim();
  const model = scannedModel || baseMotor?.profile?.driver_model || 'Dynamixel';
  const busId = device?.id === null || device?.id === undefined ? null : Number(device.id);
  const baudrate = DYNAMIXEL_BAUDRATE;
  const port = String(device?.port || baseMotor?.identity?.serial_port || baseMotor?.config?.serial_port || '');
  const existingConfig = baseMotor?.config || {};
  const existingIdentity = baseMotor?.identity || {};
  const name = busId === null || busId === undefined ? 'ID -' : `ID ${busId}`;
  return normalizeMotor({
    id: baseMotor?.id || dynamixelMotorIdFromDevice(device),
    enabled: baseMotor ? Boolean(baseMotor.enabled) : true,
    hidden: baseMotor ? Boolean(baseMotor.hidden) : false,
    deleted: false,
    axis,
    name,
    motor_type: 'dynamixel',
    driver_family: 'dynamixel',
    transport: 'serial',
    identity: {
      ...existingIdentity,
      node_id: busId,
      bus_id: busId,
      serial_port: port,
      serial_baudrate: baudrate,
    },
    profile: {
      driver_model: model,
      model_confirmed: Boolean(scannedModel),
      model_source: scannedModel ? 'physical_protocol' : '',
    },
    config: {
      ...existingConfig,
      controller_index: axis,
      driver_id: existingConfig.driver_id ?? options.firstDynamixelDriverId(),
      bus_id: busId,
      serial_port: port,
      serial_baudrate: baudrate,
      profile_mode: existingConfig.profile_mode ?? 0,
    },
  });
}

export function dynamixelScanDeviceForValues(values, devices) {
  const nodeId = values.nodeId === null || values.nodeId === undefined
    ? null
    : Number(values.nodeId);
  if (nodeId === null || Number.isNaN(nodeId)) return null;
  const port = String(values.serialPort || values.port || '').trim();
  const matches = devices.filter((device) => (
    Number(device.id) === nodeId
    && (!port || String(device.port || '') === port)
  ));
  return matches.length === 1 ? matches[0] : null;
}

function dynamixelScanDeviceForRow(row, values, devices) {
  if (row.scanDevice) return row.scanDevice;
  return dynamixelScanDeviceForValues(values, devices);
}

