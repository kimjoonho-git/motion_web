import { normalizeMotor } from './motor_registry.js';

export const DYNAMIXEL_BAUDRATE = 1000000;

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

export function normalizedModelName(value) {
  return String(value || '').trim().toLowerCase();
}

export function modelTextFromDevice(device) {
  if (!device) return '';
  return device.model_name ||
    device.model ||
    device.driver_model ||
    (device.model_number ? `Model ${Number(device.model_number).toLocaleString('ko-KR', { maximumFractionDigits: 0 })}` : '');
}

export function dynamixelScanDeviceKey(device) {
  return [
    device?.port || '',
    device?.baudrate ?? '',
    device?.id ?? '',
  ].join('|');
}

export function dynamixelMotorIdFromDevice(device) {
  const port = String(device?.port || 'serial').replace(/[^a-zA-Z0-9]+/g, '_').replace(/^_+|_+$/g, '');
  return `dynamixel_${port}_id_${device?.id ?? 'unknown'}_${device?.baudrate ?? 'baud'}`;
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

export function dynamixelDeviceFromButton(button) {
  if (!button) return null;
  const rawId = button.dataset.deviceId;
  if (rawId === null || rawId === undefined || rawId === '') return null;
  const id = Number(rawId);
  if (!Number.isFinite(id)) return null;
  const baudrate = Number(button.dataset.deviceBaudrate || DYNAMIXEL_BAUDRATE);
  const modelNumberRaw = button.dataset.deviceModelNumber;
  const modelNumber = modelNumberRaw === null || modelNumberRaw === undefined || modelNumberRaw === ''
    ? null
    : Number(modelNumberRaw);
  return {
    id,
    port: button.dataset.devicePort || '',
    baudrate: Number.isFinite(baudrate) ? baudrate : DYNAMIXEL_BAUDRATE,
    model_name: button.dataset.deviceModel || '',
    model_number: Number.isFinite(modelNumber) ? modelNumber : null,
    source: 'button',
  };
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

export function dynamixelScanDeviceForRow(row, values, devices) {
  if (row.scanDevice) return row.scanDevice;
  return dynamixelScanDeviceForValues(values, devices);
}

export function dynamixelScanMismatch(row, values, devices) {
  const device = dynamixelScanDeviceForRow(row, values, devices);
  if (!row.motor || !device) return false;
  const yamlBaudrate = firstDefined(row.motor.identity?.serial_baudrate, row.motor.config?.serial_baudrate);
  if (
    yamlBaudrate !== null &&
    yamlBaudrate !== undefined &&
    device.baudrate !== null &&
    device.baudrate !== undefined &&
    Number(yamlBaudrate) !== Number(device.baudrate)
  ) {
    return true;
  }
  const yamlModel = normalizedModelName(row.motor.profile?.driver_model);
  const scanModel = normalizedModelName(modelTextFromDevice(device));
  if (yamlModel && scanModel && !yamlModel.includes(scanModel) && !scanModel.includes(yamlModel)) {
    return true;
  }
  return false;
}
