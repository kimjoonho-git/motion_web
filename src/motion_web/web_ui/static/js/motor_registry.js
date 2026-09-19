import { clone } from './format.js';

//: 서버가 「모델을 모른다」고 적는 표식 · 값이 아니다 · §6-210
//: `motion_web_bridge/motor_identity.py` 의 `UNKNOWN_DRIVER_MODEL` 과 같은 글자다.
const UNKNOWN_DRIVER_MODEL = 'UNVERIFIED_MINAS';

export function modelIsUnknown(value) {
  const text = String(value ?? '').trim().toUpperCase();
  return text === '' || text === UNKNOWN_DRIVER_MODEL;
}

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
  const profile = motor && typeof motor.profile === 'object' && motor.profile !== null
    ? clone(motor.profile)
    : {};
  // Older project registries mixed the user-confirmed model into physical
  // discovery identity. Migrate it while loading and keep identity read-only.
  if (!profile.driver_model && identity.driver_model) {
    profile.driver_model = identity.driver_model;
  }
  // **「모름」 표식은 값이 아니다** · §6-210
  //
  // 서버는 기본 minas 드라이버에 `UNVERIFIED_MINAS` 를 달아 「모델을
  // 모른다」고 적는다 · 화면은 그것을 모델 이름으로 받아 들고 다녔고,
  // 옆의 드라이버가 `MADLN05BE` 를 알고 있어도 영영 「모델 미확인」이
  // 떴다 · 여기서 한 번 걷어내면 아래 화면 전부가 같은 값을 본다.
  if (modelIsUnknown(profile.driver_model)) {
    profile.driver_model = String(
      identity.sii_order_number || identity.sii_device_name || '',
    ).trim();
  }
  // 모델을 알면 확인된 것이다 · 두 값이 갈릴 자리를 없앤다.
  profile.model_confirmed = String(profile.driver_model || '').trim().length > 0;
  if (!profile.model_confirmed) {
    profile.model_source = '';
  } else if (!profile.model_source) {
    profile.model_source = identity.nameplate_confirmed === true
      ? 'user_nameplate'
      : 'physical_sii';
  }
  delete identity.driver_model;
  delete identity.nameplate_confirmed;
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
    profile,
    config: syncedConfig,
  };
}

function stableRegistry(value) {
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
