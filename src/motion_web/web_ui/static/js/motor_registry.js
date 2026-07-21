import { clone } from './format.js';

export function normalizeRegistry(value) {
  const motors = Array.isArray(value && value.motors) ? value.motors : [];
  return {
    version: Number(value && value.version) || 1,
    updated_at: value ? value.updated_at ?? null : null,
    motors: motors.map((motor, index) => normalizeMotor(motor, index)),
  };
}

export function normalizeMotor(motor, index = 0) {
  const identity = motor && typeof motor.identity === 'object' && motor.identity !== null
    ? clone(motor.identity)
    : {};
  const config = motor && typeof motor.config === 'object' && motor.config !== null
    ? clone(motor.config)
    : {};
  const motorType = String((motor && motor.motor_type) || 'unknown');
  const transport = String((motor && motor.transport) || 'unknown');
  const driverFamily = String((motor && motor.driver_family) || motorType);
  const id = String((motor && motor.id) || `${transport}_${motorType}_${index}`);
  const parseAxis = (value) => {
    if (value === null || value === undefined || value === '') return null;
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  };
  const configAxis = parseAxis(config.controller_index);
  const motorAxis = parseAxis(motor ? motor.axis : null);
  const axis = configAxis !== null ? configAxis : motorAxis;
  const syncedConfig = clone(config);
  if (axis !== null) {
    syncedConfig.controller_index = axis;
  }

  return {
    id,
    enabled: Boolean(motor && motor.enabled),
    hidden: Boolean(motor && motor.hidden),
    deleted: Boolean(motor && motor.deleted),
    axis,
    name: String((motor && motor.name) || ''),
    motor_type: motorType,
    driver_family: driverFamily,
    transport,
    identity,
    config: syncedConfig,
  };
}

export function stableRegistry(value) {
  const copy = normalizeRegistry(value || {});
  delete copy.updated_at;
  copy.motors = copy.motors
    .map((motor) => clone(motor))
    .sort((a, b) => String(a.id).localeCompare(String(b.id)));
  return JSON.stringify(copy);
}

export function hasRegistryChanges(registry, currentRegistry) {
  return stableRegistry(registry) !== stableRegistry(currentRegistry);
}

export function registryMotorLabel(motor) {
  if (!motor) return '-';
  if (motor.name) return motor.name;
  if (motor.axis !== null && motor.axis !== undefined) return `축 ${motor.axis}`;
  return motor.id || '모터';
}

export function activeRegistryMotors(registry) {
  return (registry?.motors || []).filter((motor) => !motor.deleted);
}

export function activeVisibleRegistryMotors(registry) {
  return activeRegistryMotors(registry).filter((motor) => motor.enabled && !motor.hidden);
}

export function registryMotorById(registry, id) {
  if (!id) return null;
  return (registry?.motors || []).find((motor) => motor.id === id) || null;
}

export function upsertMotorInRegistry(registry, motor) {
  const target = registry || normalizeRegistry({});
  const normalized = normalizeMotor(motor);
  const index = target.motors.findIndex((item) => item.id === normalized.id);
  if (index >= 0) {
    target.motors[index] = normalized;
  } else {
    target.motors.push(normalized);
  }
  return normalized;
}
