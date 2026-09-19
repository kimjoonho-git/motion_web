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

/** 서버와 **같은 이름으로 부른다** · §6-212
 *
 * 검색기는 모델 번호 1120 을 `XM540-W270` 이라고 읽는다 · 서버는 드라이버를
 * 만들 때 그것을 `XM540-W270-R` 로 적는다
 * (`motor_config_build.default_dynamixel_driver`).
 *
 * 그래서 같은 모터를 두 곳이 다른 이름으로 불렀다 · 화면은 검색한 이름,
 * 파일은 정규 이름 · 검색할 때마다 모델 칸의 글자가 왔다 갔다 했다.
 *
 * 표가 두 언어에 나뉘어 있으므로 `dynamixel_model_names.test.mjs` 가 두
 * 쪽이 같은 글자인지 지킨다.
 */
const DYNAMIXEL_CANONICAL_MODELS = [
  ['XM540-W150', 'XM540-W150'],
  ['XM540-W270', 'XM540-W270-R'],
];

export function canonicalDynamixelModel(value) {
  const text = String(value ?? '').trim();
  const model = text.toUpperCase().replace(/_/g, '-');
  const hit = DYNAMIXEL_CANONICAL_MODELS.find(([needle]) => model.includes(needle));
  return hit ? hit[1] : text;
}

export function modelTextFromDevice(device) {
  if (!device) return '';
  return canonicalDynamixelModel(
    device.model_name ||
    device.model ||
    device.driver_model ||
    (device.model_number ? `Model ${Number(device.model_number).toLocaleString('ko-KR', { maximumFractionDigits: 0 })}` : ''),
  );
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
  const model = modelTextFromDevice(device) || baseMotor?.profile?.driver_model || 'Dynamixel';
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
      model_confirmed: Boolean(modelTextFromDevice(device)),
      model_source: modelTextFromDevice(device) ? 'physical_protocol' : '',
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

