import {
  displayText,
  formatInt,
  formatNumber,
  normalizeMotorTypeKey,
} from './format.js?v=20260718-korean-ui';
import {
  requestAcServoAction,
  requestAcServoControl,
  requestAcServoJog,
  requestDynamixelAction,
  requestDynamixelJog,
  requestMotionSafetyStop,
} from './api.js?v=20260722-motor-config-delete';

const DEFAULT_MAX_JOG_DELTA_DEG = 360.0;
const MOTION_DONE_VELOCITY_DEG_SEC = 0.05;
const DYNAMIXEL_DONE_VELOCITY_DEG_SEC = 2.0;
const MOTION_MOVED_TOLERANCE_DEG = 0.01;
const MOTION_TARGET_TOLERANCE_DEG = 0.05;
const DYNAMIXEL_TARGET_TOLERANCE_RAW_COUNTS = 2.0;
const JOG_MOTION_LOCK_TIMEOUT_MS = 120000;
const DYNAMIXEL_JOG_MOTION_LOCK_TIMEOUT_MS = 8000;
const ACTION_MOTION_LOCK_EXTRA_MS = 10000;
const CUBIC_SMOOTHSTEP_MAX_VELOCITY = 1.5;
const CUBIC_SMOOTHSTEP_MAX_ACCELERATION = 6.0;
const DYNAMIXEL_ACTION_MIN_DEG = -180.0;
const DYNAMIXEL_ACTION_MAX_DEG = 180.0;

function numericValue(value, fallback = null) {
  const number = Number(value);
  return Number.isFinite(number) ? number : fallback;
}

function axisValue(motor) {
  const axis = numericValue(motor?.controller_index, null);
  return axis === null ? null : axis;
}

function motorTypeKey(motor) {
  return normalizeMotorTypeKey(
    [
      motor?.motor_type,
      motor?.motor_type_label,
      motor?.transport,
      motor?.transport_label,
      motor?.driver_model,
      motor?.driver_name,
    ].join(' '),
    '',
  );
}

function maxJogDeltaDeg(state) {
  const value = numericValue(state?.motion_test_limits?.max_jog_delta_deg, DEFAULT_MAX_JOG_DELTA_DEG);
  return value !== null && value > 0 ? value : DEFAULT_MAX_JOG_DELTA_DEG;
}

function motorIdText(motor) {
  const type = motorTypeKey(motor);
  const value = type === 'ac_servo'
    ? motor?.alias
    : (motor?.bus_id ?? motor?.node_id ?? motor?.id ?? motor?.device_id);
  return value === null || value === undefined ? '-' : formatInt(value);
}

function motorLabel(motor) {
  if (!motor) return '-';
  const typeLabels = {
    ac_servo: 'AC 서보',
    dynamixel: '다이나믹셀',
    cubemars: '큐브마스',
  };
  return `축 ${formatInt(motor.controller_index)} / ID ${motorIdText(motor)} / ${typeLabels[motorTypeKey(motor)] || '확인 불가'} / ${motor.display_name || '-'}`;
}

function positionDeg(motor) {
  return numericValue(motor?.position_deg, numericValue(motor?.position, null));
}

function velocityDeg(motor) {
  return numericValue(motor?.velocity_deg_s, numericValue(motor?.velocity, null));
}

function positionRaw(motor) {
  return numericValue(motor?.position_raw, null);
}

function rawDeltaFromDeg(motor, deltaDeg) {
  const rawPerDegree = numericValue(motor?.position_raw_per_degree, null);
  if (rawPerDegree !== null && Number.isFinite(deltaDeg)) {
    return Math.round(deltaDeg * rawPerDegree);
  }
  const pulsePerRev = numericValue(motor?.pulse_per_revolution, null);
  if (pulsePerRev === null || !Number.isFinite(deltaDeg)) return null;
  return Math.round((deltaDeg / 360.0) * pulsePerRev);
}

function rawText(value, signed = false) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '-';
  const number = Number(value);
  const prefix = signed && number > 0 ? '+' : '';
  return `${prefix}${formatInt(number)}`;
}

function degText(value, signed = false) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '-';
  const number = Number(value);
  const prefix = signed && number > 0 ? '+' : '';
  return `${prefix}${formatNumber(number, 3)} deg`;
}

function positionPairText(deg, raw, signed = false) {
  return `${degText(deg, signed)} / raw ${rawText(raw, signed)}`;
}

function emptyValueHtml(message) {
  return `<div class="motion-value-empty">${displayText(message)}</div>`;
}

function valueTableHtml(headers, rows) {
  const head = headers
    .map((header) => `<th>${displayText(header)}</th>`)
    .join('');
  const body = rows
    .map((row) => `<tr>${row.map((cell) => `<td>${displayText(cell)}</td>`).join('')}</tr>`)
    .join('');
  return `<table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`;
}

function statusTableHtml(rows) {
  const body = rows
    .map((row) => (
      `<tr><th>${displayText(row.label)}</th><td><span class="motion-state-pill ${displayText(row.state)}">${displayText(row.value)}</span></td></tr>`
    ))
    .join('');
  return `<table class="motion-state-table"><tbody>${body}</tbody></table>`;
}

function positionSnapshot(motor, gearRatio) {
  const motorDeg = positionDeg(motor);
  const raw = positionRaw(motor);
  return {
    outputDeg: motorDeg === null ? null : motorDeg / gearRatio,
    motorDeg,
    raw,
  };
}

function limitValueDeg(motor, field, mode = '') {
  if (mode === 'action' && isDynamixelMotor(motor)) {
    return field === 'lower' ? DYNAMIXEL_ACTION_MIN_DEG : DYNAMIXEL_ACTION_MAX_DEG;
  }
  return numericValue(motor?.[field], null);
}

function limitSnapshot(motor, gearRatio, field, mode = '') {
  const motorDeg = limitValueDeg(motor, field, mode);
  return {
    outputDeg: motorDeg === null ? null : motorDeg / gearRatio,
    motorDeg,
    raw: motorDeg === null ? null : rawDeltaFromDeg(motor, motorDeg),
  };
}

function finiteDelta(a, b) {
  return a === null || b === null ? null : a - b;
}

function absFiniteDelta(a, b) {
  const delta = finiteDelta(a, b);
  return delta === null ? null : Math.abs(delta);
}

function motionHasStarted(capture, motor, result) {
  if (capture.seenMoving) return true;
  if (motor?.target_reached === false) return true;
  const velocity = velocityDeg(motor);
  if (velocity !== null && Math.abs(velocity) > MOTION_DONE_VELOCITY_DEG_SEC) return true;
  const motorDelta = absFiniteDelta(result.motorDeg, capture.start.motorDeg);
  if (motorDelta !== null && motorDelta > MOTION_MOVED_TOLERANCE_DEG) return true;
  const rawDelta = absFiniteDelta(result.raw, capture.start.raw);
  return rawDelta !== null && rawDelta > 0;
}

function targetReachedOrClose(capture, motor, result) {
  if (motor?.target_reached === true) return true;
  const targetDelta = absFiniteDelta(result.motorDeg, capture.target.motorDeg);
  return targetDelta !== null && targetDelta <= targetToleranceDeg(motor);
}

function motionIsQuiet(motor) {
  const velocity = velocityDeg(motor);
  return velocity === null || Math.abs(velocity) <= doneVelocityDegSec(motor);
}

function targetToleranceDeg(motor) {
  if (!isDynamixelMotor(motor)) return MOTION_TARGET_TOLERANCE_DEG;
  const rawPerDegree = numericValue(motor?.position_raw_per_degree, null)
    || numericValue(motor?.pulse_per_revolution, null) / 360.0;
  if (!rawPerDegree || rawPerDegree <= 0) {
    return Math.max(MOTION_TARGET_TOLERANCE_DEG, 0.2);
  }
  return Math.max(
    MOTION_TARGET_TOLERANCE_DEG,
    DYNAMIXEL_TARGET_TOLERANCE_RAW_COUNTS / rawPerDegree,
  );
}

function doneVelocityDegSec(motor) {
  return isDynamixelMotor(motor)
    ? DYNAMIXEL_DONE_VELOCITY_DEG_SEC
    : MOTION_DONE_VELOCITY_DEG_SEC;
}

function jogMotionLockTimeoutMs(motor) {
  return isDynamixelMotor(motor)
    ? DYNAMIXEL_JOG_MOTION_LOCK_TIMEOUT_MS
    : JOG_MOTION_LOCK_TIMEOUT_MS;
}

function actionMotionLockTimeoutMs(capture) {
  const value = numericValue(capture?.activeTimeoutMs, null);
  return value !== null && value > 0 ? value : ACTION_MOTION_LOCK_EXTRA_MS;
}

function updateOutputCompletion(capture, motor, liveResult) {
  if (!capture || capture.completedAtMs) return;
  if (motionHasStarted(capture, motor, liveResult)) {
    capture.seenMoving = true;
  }
  const targetDelta = absFiniteDelta(liveResult.motorDeg, capture.target.motorDeg);
  if (
    capture.mode === 'action'
    && !capture.seenMoving
    && targetDelta !== null
    && targetDelta <= targetToleranceDeg(motor)
    && motionIsQuiet(motor)
  ) {
    capture.completedAtMs = Date.now();
    capture.completedResult = liveResult;
    return;
  }
  if (
    capture.seenMoving
    && targetReachedOrClose(capture, motor, liveResult)
    && motionIsQuiet(motor)
  ) {
    capture.completedAtMs = Date.now();
    capture.completedResult = liveResult;
  }
}

function gearRatioValue(el) {
  const value = numericValue(el.motionTestGearRatio?.value, 1);
  return value !== null && value > 0 ? value : 1;
}

function minPositive(values) {
  const positive = values
    .map((value) => numericValue(value, null))
    .filter((value) => value !== null && Number.isFinite(value) && value > 0);
  return positive.length ? Math.min(...positive) : null;
}

function maxSpeedDegPerSec(motor) {
  const rpm = numericValue(motor?.rated_speed_rpm, null);
  return rpm === null ? null : rpm * 6.0;
}

function velocityLimitDegSec(motor) {
  const speed = numericValue(motor?.speed, null);
  return minPositive([
    motor?.profile_velocity,
    maxSpeedDegPerSec(motor),
    speed !== null && speed <= 1000000.0 ? speed : null,
  ]);
}

function accelerationLimitDegSec2(motor) {
  return minPositive([
    motor?.profile_acceleration,
    motor?.profile_deceleration,
    motor?.acceleration,
    motor?.deceleration,
  ]);
}

function durationPlan(motor, deltaDeg, requestedSec, durationEnabled) {
  if (!durationEnabled) {
    return {
      requestedSec: null,
      appliedSec: null,
      limited: false,
      velocityLimitDegSec: null,
      accelerationLimitDegSec2: null,
    };
  }
  const requested = Math.max(numericValue(requestedSec, 0), 0.02);
  const distance = Math.abs(deltaDeg);
  const velocityLimit = velocityLimitDegSec(motor);
  const accelerationLimit = accelerationLimitDegSec2(motor);
  let applied = requested;
  if (distance > 0 && velocityLimit !== null && velocityLimit > 0) {
    applied = Math.max(
      applied,
      CUBIC_SMOOTHSTEP_MAX_VELOCITY * distance / velocityLimit,
    );
  }
  if (distance > 0 && accelerationLimit !== null && accelerationLimit > 0) {
    applied = Math.max(
      applied,
      Math.sqrt(CUBIC_SMOOTHSTEP_MAX_ACCELERATION * distance / accelerationLimit),
    );
  }
  return {
    requestedSec: requested,
    appliedSec: applied,
    limited: applied > requested + 1e-9,
    velocityLimitDegSec: velocityLimit,
    accelerationLimitDegSec2: accelerationLimit,
  };
}

function planText(plan) {
  if (plan.requestedSec === null) return '-';
  const requested = `${formatNumber(plan.requestedSec, 2)}초`;
  const applied = `${formatNumber(plan.appliedSec, 2)}초`;
  return plan.limited
    ? `요청 ${requested} / 적용 ${applied} / 속도/가속도 제한 적용`
    : `요청 ${requested} / 적용 ${applied}`;
}

function motorReadyRows(motor) {
  const type = motorTypeKey(motor);
  const hasError = Boolean(motor?.fault);
  const errorRow = {
    label: '에러 유무',
    value: hasError ? '있음' : '없음',
    state: hasError ? 'bad' : 'ok',
  };
  if (type === 'ac_servo') {
    const servoOn = motor?.servo_on === true;
    return [
      {
        label: '서보 상태',
        value: servoOn ? '켜짐' : '꺼짐',
        state: servoOn ? 'ok' : 'warn',
      },
      errorRow,
    ];
  }
  if (type === 'dynamixel') {
    return [errorRow];
  }
  return [errorRow];
}

function isAcServoMotor(motor) {
  return motorTypeKey(motor) === 'ac_servo';
}

function isDynamixelMotor(motor) {
  return motorTypeKey(motor) === 'dynamixel';
}

function actionMotorLabel(motor) {
  return isDynamixelMotor(motor) ? '다이나믹셀' : 'AC 서보';
}

function actionGearRatio(el, motor) {
  return isDynamixelMotor(motor) ? 1 : gearRatioValue(el);
}

function acServoReadyBlockReason(motor, actionText) {
  if (!motor) return '축을 선택하세요';
  if (!isAcServoMotor(motor)) return `AC 서보 축만 ${actionText} 가능합니다`;
  if (String(motor.state || '') !== 'detected') return '선택 축이 감지되지 않았습니다';
  if (motor.servo_on !== true) return '서보가 켜진 상태가 아닙니다';
  if (Boolean(motor.fault)) return '선택 축에 에러가 있습니다';
  return '';
}

function acServoJogBlockReason(motor) {
  return acServoReadyBlockReason(motor, '조그 동작');
}

function jogBlockReason(motor) {
  if (!motor) return '축을 선택하세요';
  if (isAcServoMotor(motor)) return acServoJogBlockReason(motor);
  if (isDynamixelMotor(motor)) {
    if (String(motor.state || '') !== 'detected') return '선택 축이 감지되지 않았습니다';
    if (Boolean(motor.fault)) return '선택 축에 에러가 있습니다';
    return '';
  }
  return 'AC 서보 또는 다이나믹셀 축만 조그 동작 가능합니다';
}

function acServoActionBlockReason(motor) {
  return acServoReadyBlockReason(motor, '절대 위치 동작');
}

function actionBlockReason(motor) {
  if (!motor) return '축을 선택하세요';
  if (isAcServoMotor(motor)) return acServoActionBlockReason(motor);
  if (isDynamixelMotor(motor)) {
    if (String(motor.state || '') !== 'detected') return '선택 축이 감지되지 않았습니다';
    if (Boolean(motor.fault)) return '선택 축에 에러가 있습니다';
    return '';
  }
  return 'AC 서보 또는 다이나믹셀 축만 절대 위치 동작 가능합니다';
}

function positionLimitBlockReason(plan) {
  if (!plan) return '';
  const lower = limitValueDeg(plan.motor, 'lower', plan.mode);
  const upper = limitValueDeg(plan.motor, 'upper', plan.mode);
  if (lower !== null && plan.targetMotorDeg < lower) {
    return `목표 위치가 하한 ${formatNumber(lower, 3)} deg보다 작습니다`;
  }
  if (upper !== null && plan.targetMotorDeg > upper) {
    return `목표 위치가 상한 ${formatNumber(upper, 3)} deg보다 큽니다`;
  }
  return '';
}

function motorByAxis(state, axis) {
  const motors = Array.isArray(state?.motors) ? state.motors : [];
  if (axis === null || axis === undefined || axis === '') return null;
  return motors.find((motor) => Number(motor.controller_index) === Number(axis)) || null;
}

function sortedMotors(state) {
  return (Array.isArray(state?.motors) ? state.motors : [])
    .filter((motor) => axisValue(motor) !== null)
    .slice()
    .sort((a, b) => axisValue(a) - axisValue(b));
}

function detectedAcServoMotors(state) {
  return sortedMotors(state).filter((motor) => (
    isAcServoMotor(motor)
    && String(motor.state || '') === 'detected'
  ));
}

export function createMotionTestController({ el, getLatestState }) {
  let selectedAxis = null;
  let lastAxisOptionsSignature = '';
  let lastJogDirection = 1;
  let lastOutputCapture = null;
  let lastCommandResult = null;
  let jogRequestInFlight = false;
  let servoControlInFlight = false;
  let motionStopInFlight = false;

  function selectedMotor() {
    return motorByAxis(getLatestState(), selectedAxis);
  }

  function commandPlan(options = {}) {
    const motor = selectedMotor();
    if (!motor) return null;

    const currentMotorDeg = positionDeg(motor);
    if (currentMotorDeg === null) return null;

    const mode = el.motionTestMode?.value || 'jog';
    const isJog = mode === 'jog';
    const jogDirection = numericValue(options.jogDirection, lastJogDirection);
    const gearRatio = isJog ? 1 : actionGearRatio(el, motor);
    const currentOutputDeg = currentMotorDeg / gearRatio;
    const command = isJog
      ? numericValue(el.motionTestJogDistance?.value, null)
      : numericValue(el.motionTestPosition?.value, null);
    if (command === null || (isJog && command <= 0)) return null;
    if (isJog && Math.abs(command) > maxJogDeltaDeg(getLatestState())) return null;

    const commandRelativeDeg = isJog
      ? Math.abs(command) * jogDirection
      : null;
    const targetOutputDeg = isJog ? currentOutputDeg + commandRelativeDeg : command;
    const outputDeltaDeg = targetOutputDeg - currentOutputDeg;
    const targetMotorDeg = targetOutputDeg * gearRatio;
    const motorDeltaDeg = targetMotorDeg - currentMotorDeg;
    const currentRaw = positionRaw(motor);
    const deltaRaw = rawDeltaFromDeg(motor, motorDeltaDeg);
    const targetRaw = currentRaw !== null && deltaRaw !== null ? currentRaw + deltaRaw : null;
    const duration = durationPlan(motor, motorDeltaDeg, el.motionTestDurationSec?.value, !isJog);

    return {
      motor,
      mode,
      jogDirection: isJog ? jogDirection : null,
      gearRatio,
      currentOutputDeg,
      currentMotorDeg,
      currentRaw,
      targetOutputDeg,
      targetMotorDeg,
      targetRaw,
      outputDeltaDeg,
      motorDeltaDeg,
      deltaRaw,
      duration,
    };
  }

  function renderAxisOptions() {
    if (!el.motionTestAxisSelect) return;
    const motors = sortedMotors(getLatestState());
    const options = [
      '<option value="">축 선택</option>',
      ...motors.map((motor) => {
        const axis = axisValue(motor);
        const selected = selectedAxis !== null && Number(selectedAxis) === Number(axis);
        return `<option value="${axis}"${selected ? ' selected' : ''}>${displayText(motorLabel(motor))}</option>`;
      }),
    ];
    const html = options.join('');
    if (html === lastAxisOptionsSignature) return;
    lastAxisOptionsSignature = html;
    el.motionTestAxisSelect.innerHTML = html;
  }

  function renderModeButtons() {
    if (!el.motionTestModeButtons || !el.motionTestMode) return;
    const mode = el.motionTestMode.value || 'jog';
    el.motionTestModeButtons.querySelectorAll('[data-motion-test-mode]').forEach((button) => {
      const active = button.dataset.motionTestMode === mode;
      button.classList.toggle('active', active);
      button.setAttribute('aria-pressed', active ? 'true' : 'false');
    });
    if (el.motionTestModePanels) {
      el.motionTestModePanels.forEach((panel) => {
        panel.classList.toggle('hidden', panel.dataset.motionTestPanel !== mode);
      });
    }
    if (el.motionTestModeSummary) {
      el.motionTestModeSummary.textContent = mode === 'action' ? '동작 모드' : '조그 모드';
    }
  }

  function renderResultPreview() {
    const plan = commandPlan();
    if (!el.motionTestResultState || !el.motionTestResultText) return;
    if (!selectedMotor()) {
      el.motionTestResultState.textContent = '대기';
      el.motionTestResultText.textContent = '축을 선택하세요';
      return;
    }
    if (!plan) {
      el.motionTestResultState.textContent = '입력 대기';
      const jogValue = numericValue(el.motionTestJogDistance?.value, null);
      const maxJog = maxJogDeltaDeg(getLatestState());
      el.motionTestResultText.textContent = (
        (el.motionTestMode?.value || 'jog') === 'jog'
        && jogValue !== null
        && Math.abs(jogValue) > maxJog
      )
        ? `조그 이동량은 최대 ${formatNumber(maxJog, 0)} deg까지 가능합니다`
        : '현재 위치 또는 명령 위치값을 확인하세요';
      return;
    }
    const limitReason = positionLimitBlockReason(plan);
    if (limitReason) {
      el.motionTestResultState.textContent = '명령 차단';
      el.motionTestResultText.textContent = limitReason;
      return;
    }

    const isJog = plan.mode === 'jog';
    const modeText = isJog ? '조그 모드' : '동작 모드';
    const directionText = plan.jogDirection === -1 ? '- 방향' : '+ 방향';
    const lines = [
      `명령 모드: ${modeText}`,
    ];
    if (isJog) {
      lines.push(`조그 방향: ${directionText}`);
    } else {
      const gearText = isDynamixelMotor(plan.motor)
        ? `${formatNumber(plan.gearRatio, 3)}:1 (다이나믹셀 고정)`
        : `${formatNumber(plan.gearRatio, 3)}:1`;
      lines.push(`감속비: ${gearText}`);
      lines.push(`모터 명령 절대값: ${positionPairText(plan.targetMotorDeg, plan.targetRaw)}`);
      lines.push(`모터 명령 상대값: ${positionPairText(plan.motorDeltaDeg, plan.deltaRaw, true)}`);
      lines.push('궤적: 3차 S-curve 기준');
    }
    el.motionTestResultState.textContent = '계산 완료';
    el.motionTestResultText.textContent = lines.join('\n');
  }

  function renderCommandPositionSummary() {
    if (!el.motionTestCommandPositionSummary) return;
    const motor = selectedMotor();
    if (!motor) {
      el.motionTestCommandPositionSummary.innerHTML = emptyValueHtml('축을 선택하세요');
      return;
    }

    const mode = el.motionTestMode?.value || 'jog';
    const gearRatio = mode === 'action' ? actionGearRatio(el, motor) : 1;
    const current = positionSnapshot(motor, gearRatio);
    const plan = commandPlan();
    const lower = limitSnapshot(motor, gearRatio, 'lower', mode);
    const upper = limitSnapshot(motor, gearRatio, 'upper', mode);

    el.motionTestCommandPositionSummary.innerHTML = valueTableHtml(
      ['구분', '출력축 각도', '모터축 각도', '원시값'],
      [
        [
          '현재 위치',
          degText(current.outputDeg),
          degText(current.motorDeg),
          rawText(current.raw),
        ],
        [
          '명령 이후 예상 위치',
          plan ? degText(plan.targetOutputDeg) : '-',
          plan ? degText(plan.targetMotorDeg) : '-',
          plan ? rawText(plan.targetRaw) : '-',
        ],
        [
          '위치 제한 하한',
          degText(lower.outputDeg),
          degText(lower.motorDeg),
          rawText(lower.raw),
        ],
        [
          '위치 제한 상한',
          degText(upper.outputDeg),
          degText(upper.motorDeg),
          rawText(upper.raw),
        ],
      ],
    );
  }

  function updateActionPositionInputConstraints(motor, isActionMode) {
    if (!el.motionTestPosition) return;
    if (isDynamixelMotor(motor)) {
      el.motionTestPosition.min = String(DYNAMIXEL_ACTION_MIN_DEG);
      el.motionTestPosition.max = String(DYNAMIXEL_ACTION_MAX_DEG);
      el.motionTestPosition.title = '다이나믹셀 동작 모드 목표 위치는 -180~180도로 제한됩니다';
      if (isActionMode) {
        const value = numericValue(el.motionTestPosition.value, null);
        if (value !== null && value < DYNAMIXEL_ACTION_MIN_DEG) {
          el.motionTestPosition.value = String(DYNAMIXEL_ACTION_MIN_DEG);
        } else if (value !== null && value > DYNAMIXEL_ACTION_MAX_DEG) {
          el.motionTestPosition.value = String(DYNAMIXEL_ACTION_MAX_DEG);
        }
      }
      return;
    }
    el.motionTestPosition.removeAttribute('min');
    el.motionTestPosition.removeAttribute('max');
    el.motionTestPosition.title = '';
  }

  function renderOutputValues() {
    if (!el.motionTestOutputState || !el.motionTestOutputText) return;
    const motor = selectedMotor();
    if (!motor) {
      el.motionTestOutputState.textContent = '대기';
      el.motionTestOutputText.innerHTML = emptyValueHtml('축을 선택하세요');
      return;
    }
    if (!lastOutputCapture || Number(lastOutputCapture.axis) !== Number(selectedAxis)) {
      el.motionTestOutputState.textContent = '동작 전';
      el.motionTestOutputText.innerHTML = emptyValueHtml('동작 명령 후 시작점과 결과값이 표시됩니다');
      return;
    }

    const liveResult = positionSnapshot(motor, lastOutputCapture.gearRatio);
    updateOutputCompletion(lastOutputCapture, motor, liveResult);
    const result = lastOutputCapture.completedResult || liveResult;
    const endMs = lastOutputCapture.completedAtMs || Date.now();
    const elapsedSec = Math.max((endMs - lastOutputCapture.startedAtMs) / 1000, 0);
    const outputDelta = result.outputDeg === null || lastOutputCapture.start.outputDeg === null
      ? null
      : result.outputDeg - lastOutputCapture.start.outputDeg;
    const motorDelta = result.motorDeg === null || lastOutputCapture.start.motorDeg === null
      ? null
      : result.motorDeg - lastOutputCapture.start.motorDeg;
    const rawDelta = result.raw === null || lastOutputCapture.start.raw === null
      ? null
      : result.raw - lastOutputCapture.start.raw;

    el.motionTestOutputState.textContent = lastOutputCapture.mode === 'jog' ? '조그 모드 결과' : '동작 모드 결과';
    el.motionTestOutputText.innerHTML = valueTableHtml(
      ['구분', '출력축 각도', '모터축 각도', '원시값', '동작 시간'],
      [
        [
          '시작점(명령 전)',
          degText(lastOutputCapture.start.outputDeg),
          degText(lastOutputCapture.start.motorDeg),
          rawText(lastOutputCapture.start.raw),
          '-',
        ],
        [
          '결과값(동작 후)',
          degText(result.outputDeg),
          degText(result.motorDeg),
          rawText(result.raw),
          `${formatNumber(elapsedSec, 2)}초`,
        ],
        [
          '위치 변화량(상대값)',
          degText(outputDelta, true),
          degText(motorDelta, true),
          rawText(rawDelta, true),
          '-',
        ],
      ],
    );
  }

  function captureOutputStart(plan = commandPlan()) {
    if (!plan) return;
    const actionTimeoutMs = plan.mode === 'action'
      ? Math.max(
        ((numericValue(plan.duration?.appliedSec, plan.duration?.requestedSec) || 0) * 1000)
          + ACTION_MOTION_LOCK_EXTRA_MS,
        ACTION_MOTION_LOCK_EXTRA_MS,
      )
      : null;
    lastOutputCapture = {
      axis: selectedAxis,
      mode: plan.mode,
      gearRatio: plan.gearRatio,
      start: {
        outputDeg: plan.currentOutputDeg,
        motorDeg: plan.currentMotorDeg,
        raw: plan.currentRaw,
      },
      target: {
        outputDeg: plan.targetOutputDeg,
        motorDeg: plan.targetMotorDeg,
        raw: plan.targetRaw,
      },
      startedAtMs: Date.now(),
      commandAcceptedAtMs: null,
      seenMoving: false,
      completedAtMs: null,
      completedResult: null,
      activeTimeoutMs: actionTimeoutMs,
    };
    renderCurrentState();
  }

  function jogMotionIsActive(motor) {
    if (
      !motor
      || !lastOutputCapture
      || lastOutputCapture.mode !== 'jog'
      || Number(lastOutputCapture.axis) !== Number(selectedAxis)
      || !lastOutputCapture.commandAcceptedAtMs
      || lastOutputCapture.completedAtMs
    ) {
      return false;
    }

    const liveResult = positionSnapshot(motor, lastOutputCapture.gearRatio);
    updateOutputCompletion(lastOutputCapture, motor, liveResult);
    if (lastOutputCapture.completedAtMs) return false;

    return Date.now() - lastOutputCapture.commandAcceptedAtMs <= jogMotionLockTimeoutMs(motor);
  }

  function actionMotionIsActive(motor) {
    if (
      !motor
      || !lastOutputCapture
      || lastOutputCapture.mode !== 'action'
      || Number(lastOutputCapture.axis) !== Number(selectedAxis)
      || !lastOutputCapture.commandAcceptedAtMs
      || lastOutputCapture.completedAtMs
    ) {
      return false;
    }

    const liveResult = positionSnapshot(motor, lastOutputCapture.gearRatio);
    updateOutputCompletion(lastOutputCapture, motor, liveResult);
    if (lastOutputCapture.completedAtMs) return false;

    return Date.now() - lastOutputCapture.commandAcceptedAtMs <= actionMotionLockTimeoutMs(lastOutputCapture);
  }

  function renderActualResultWaiting() {
    if (!el.motionTestActualState || !el.motionTestActualText) return;
    if (lastCommandResult) {
      el.motionTestActualState.textContent = lastCommandResult.success ? '요청 완료' : '요청 실패';
      el.motionTestActualText.textContent = lastCommandResult.message || '-';
      return;
    }
    if ((el.motionTestMode?.value || 'jog') === 'action') {
      const motor = selectedMotor();
      const label = actionMotorLabel(motor);
      el.motionTestActualState.textContent = '명령 전';
      el.motionTestActualText.textContent = [
        `${label} 절대 위치 동작 명령 전입니다.`,
        '동작 실행 버튼을 누르면 motion_supervisor에 절대 위치 요청을 보냅니다.',
        '실제 위치 결과는 위 출력값 표의 결과값(동작 후)에 표시됩니다.',
      ].join('\n');
      return;
    }
    const motor = selectedMotor();
    const jogMotorText = isDynamixelMotor(motor) ? '다이나믹셀' : 'AC 서보';
    el.motionTestActualState.textContent = '명령 전';
    el.motionTestActualText.textContent = [
      `${jogMotorText} 조그 명령 전입니다.`,
      '조그 버튼을 누르면 motion_supervisor에 조그 요청을 보냅니다.',
      '실제 위치 결과는 위 출력값 표의 결과값(동작 후)에 표시됩니다.',
    ].join('\n');
  }

  function renderCurrentState() {
    const motor = selectedMotor();
    const isActionMode = el.motionTestMode?.value === 'action';
    const isDynamixelSelected = isDynamixelMotor(motor);
    if (el.motionTestGearRatio) {
      if (isDynamixelSelected) {
        el.motionTestGearRatio.value = '1';
        el.motionTestGearRatio.disabled = true;
        el.motionTestGearRatio.title = '다이나믹셀 동작 모드는 감속비를 사용하지 않으며 1로 고정됩니다';
      } else {
        el.motionTestGearRatio.disabled = false;
        el.motionTestGearRatio.title = '';
      }
    }
    if (el.motionTestAxisInfo) el.motionTestAxisInfo.textContent = motorLabel(motor);
    if (el.motionTestCurrentPosition) {
      const currentMotorDeg = positionDeg(motor);
      const currentRaw = positionRaw(motor);
      const gearRatio = isActionMode ? actionGearRatio(el, motor) : 1;
      const currentOutputDeg = currentMotorDeg === null ? null : currentMotorDeg / gearRatio;
      el.motionTestCurrentPosition.innerHTML = motor
        ? valueTableHtml(
          ['기준', '각도', '원시값'],
          [
            ['출력축', degText(currentOutputDeg), '-'],
            ['모터축', degText(currentMotorDeg), rawText(currentRaw)],
          ],
        )
        : emptyValueHtml('-');
    }
    if (el.motionTestMotorState) {
      el.motionTestMotorState.innerHTML = motor
        ? statusTableHtml(motorReadyRows(motor))
        : emptyValueHtml('-');
    }
    if (el.motionTestJogDistance) {
      el.motionTestJogDistance.max = String(maxJogDeltaDeg(getLatestState()));
    }
    updateActionPositionInputConstraints(motor, isActionMode);
    const plan = commandPlan();
    const negativeJogPlan = commandPlan({ jogDirection: -1 });
    const positiveJogPlan = commandPlan({ jogDirection: 1 });
    const jogMotionActive = jogMotionIsActive(motor);
    const actionMotionActive = actionMotionIsActive(motor);
    const motionCommandActive = jogMotionActive || actionMotionActive;
    const commonJogDisabled = (
      el.motionTestMode?.value !== 'jog'
      || !motor
      || Boolean(jogBlockReason(motor))
      || jogRequestInFlight
      || motionStopInFlight
      || motionCommandActive
    );
    const negativeJogDisabled = (
      commonJogDisabled
      || negativeJogPlan === null
      || Boolean(positionLimitBlockReason(negativeJogPlan))
    );
    const positiveJogDisabled = (
      commonJogDisabled
      || positiveJogPlan === null
      || Boolean(positionLimitBlockReason(positiveJogPlan))
    );
    if (el.motionTestJogNegativeButton) {
      el.motionTestJogNegativeButton.disabled = negativeJogDisabled;
      const reason = (
        el.motionTestMode?.value !== 'jog' ? '조그 모드를 선택하세요'
          : jogRequestInFlight ? '동작 명령을 처리하고 있습니다'
            : motionStopInFlight ? '모터 정지 요청을 처리하고 있습니다'
              : motionCommandActive ? '현재 동작이 완료될 때까지 기다리거나 동작 정지를 누르세요'
                : jogBlockReason(motor)
                  || (negativeJogPlan === null ? '조그 이동량과 현재 위치를 확인하세요' : '')
                  || positionLimitBlockReason(negativeJogPlan)
      );
      el.motionTestJogNegativeButton.title = negativeJogDisabled ? reason : '- 방향으로 조그 이동';
    }
    if (el.motionTestJogPositiveButton) {
      el.motionTestJogPositiveButton.disabled = positiveJogDisabled;
      const reason = (
        el.motionTestMode?.value !== 'jog' ? '조그 모드를 선택하세요'
          : jogRequestInFlight ? '동작 명령을 처리하고 있습니다'
            : motionStopInFlight ? '모터 정지 요청을 처리하고 있습니다'
              : motionCommandActive ? '현재 동작이 완료될 때까지 기다리거나 동작 정지를 누르세요'
                : jogBlockReason(motor)
                  || (positiveJogPlan === null ? '조그 이동량과 현재 위치를 확인하세요' : '')
                  || positionLimitBlockReason(positiveJogPlan)
      );
      el.motionTestJogPositiveButton.title = positiveJogDisabled ? reason : '+ 방향으로 조그 이동';
    }
    const actionDisabled = (
      !isActionMode
      || !motor
      || plan === null
      || Boolean(actionBlockReason(motor))
      || Boolean(positionLimitBlockReason(plan))
      || jogRequestInFlight
      || motionStopInFlight
      || motionCommandActive
    );
    if (el.motionTestRunButton) {
      el.motionTestRunButton.disabled = actionDisabled;
      const reason = (
        !isActionMode ? '동작 모드를 선택하세요'
          : jogRequestInFlight ? '동작 명령을 처리하고 있습니다'
            : motionStopInFlight ? '모터 정지 요청을 처리하고 있습니다'
              : motionCommandActive ? '현재 동작이 완료될 때까지 기다리거나 동작 정지를 누르세요'
                : actionBlockReason(motor)
                  || (plan === null ? '목표 위치, 감속비, 동작 시간을 확인하세요' : '')
                  || positionLimitBlockReason(plan)
      );
      el.motionTestRunButton.title = actionDisabled ? reason : '설정한 목표 위치로 동작';
    }
    if (el.motionTestStopButton) {
      el.motionTestStopButton.disabled = (
        motionStopInFlight
        || document.body.classList.contains('emergency-latched')
      );
      el.motionTestStopButton.textContent = motionStopInFlight
        ? '정지 요청 중'
        : '모터 동작 정지';
      el.motionTestStopButton.title = motionStopInFlight
        ? '모터 정지 요청을 처리하고 있습니다'
        : '진행 중인 동작 테스트 명령을 취소하고 현재 위치를 유지합니다';
    }
    if (el.motionTestActionGuide) {
      let guideState = 'warning';
      let guideText = '다음 단계: 시험할 축을 선택하세요';
      if (motionStopInFlight) {
        guideState = 'active';
        guideText = '모터 동작 정지 요청을 처리하고 있습니다';
      } else if (jogRequestInFlight || motionCommandActive) {
        guideState = 'active';
        guideText = '현재 동작 중입니다 · 완료를 기다리거나 모터 동작 정지를 누르세요';
      } else if (motor && !isActionMode) {
        if (!negativeJogDisabled || !positiveJogDisabled) {
          guideState = 'ready';
          guideText = negativeJogDisabled || positiveJogDisabled
            ? '조그 준비 완료 · 위치 제한 안에서 활성화된 방향을 사용하세요'
            : '조그 준비 완료 · 이동량을 확인하고 방향 버튼을 누르세요';
        } else {
          const reason = jogBlockReason(motor)
            || positionLimitBlockReason(negativeJogPlan)
            || positionLimitBlockReason(positiveJogPlan)
            || '조그 이동량과 현재 위치를 확인하세요';
          guideText = `동작 불가: ${reason}`;
        }
      } else if (motor && isActionMode) {
        if (!actionDisabled) {
          guideState = 'ready';
          guideText = '동작 준비 완료 · 목표 위치와 동작 시간을 확인하고 실행하세요';
        } else {
          const reason = actionBlockReason(motor)
            || positionLimitBlockReason(plan)
            || '목표 위치, 감속비, 동작 시간을 확인하세요';
          guideText = `동작 불가: ${reason}`;
        }
      }
      el.motionTestActionGuide.dataset.state = guideState;
      el.motionTestActionGuide.textContent = guideText;
    }
    const selectedAcServoReady = (
      motor
      && isAcServoMotor(motor)
      && String(motor.state || '') === 'detected'
    );
    const anyAcServoReady = detectedAcServoMotors(getLatestState()).length > 0;
    [
      el.selectedAcServoOnButton,
      el.selectedAcServoOffButton,
      el.selectedAcServoFaultResetButton,
    ].forEach((button) => {
      if (button) button.disabled = !selectedAcServoReady || servoControlInFlight;
    });
    [
      el.allAcServoOnButton,
      el.allAcServoOffButton,
    ].forEach((button) => {
      if (button) button.disabled = !anyAcServoReady || servoControlInFlight;
    });
    renderModeButtons();
    renderCommandPositionSummary();
    renderOutputValues();
    renderResultPreview();
    renderActualResultWaiting();
  }

  function renderLatestState() {
    const motor = selectedMotor();
    if (selectedAxis !== null && !motor) selectedAxis = null;
    renderAxisOptions();
    renderCurrentState();
  }

  function selectAxis(axis) {
    const nextAxis = numericValue(axis, null);
    selectedAxis = nextAxis;
    renderAxisOptions();
    renderCurrentState();
  }

  async function sendJog(direction) {
    lastJogDirection = direction;
    const plan = commandPlan({ jogDirection: direction });
    const motor = plan?.motor;
    const isDynamixel = isDynamixelMotor(motor);
    const motorLabelText = isDynamixel ? '다이나믹셀' : 'AC 서보';
    const jogValue = numericValue(el.motionTestJogDistance?.value, null);
    const maxJog = maxJogDeltaDeg(getLatestState());
    const jogLimitReason = (
      jogValue !== null && Math.abs(jogValue) > maxJog
    )
      ? `조그 이동량은 최대 ${formatNumber(maxJog, 0)} deg까지 가능합니다`
      : '';
    const blockReason = (
      jogLimitReason
      || jogBlockReason(motor)
      || positionLimitBlockReason(plan)
    );
    if (!plan || blockReason) {
      lastCommandResult = {
        success: false,
        message: blockReason || '조그 명령값을 확인하세요',
      };
      renderCurrentState();
      return;
    }

    captureOutputStart(plan);
    jogRequestInFlight = true;
    lastCommandResult = {
      success: true,
      message: `${motorLabelText} 조그 요청 전송 중`,
    };
    renderCurrentState();

    try {
      const requestJog = isDynamixel ? requestDynamixelJog : requestAcServoJog;
      const response = await requestJog({
        axis: selectedAxis,
        relative_deg: plan.motorDeltaDeg,
      });
      if (lastOutputCapture && Boolean(response?.success)) {
        lastOutputCapture.commandAcceptedAtMs = Date.now();
      }
      lastCommandResult = {
        success: Boolean(response?.success),
        message: response?.message || `${motorLabelText} 조그 요청 결과 없음`,
      };
    } catch (error) {
      lastCommandResult = {
        success: false,
        message: `${motorLabelText} 조그 요청 실패: ${error?.message || error}`,
      };
    } finally {
      jogRequestInFlight = false;
      renderCurrentState();
    }
  }

  async function sendAction() {
    const plan = commandPlan();
    const motorLabelText = actionMotorLabel(plan?.motor);
    const blockReason = (
      actionBlockReason(plan?.motor)
      || positionLimitBlockReason(plan)
    );
    if (!plan || blockReason) {
      lastCommandResult = {
        success: false,
        message: blockReason || '동작 명령값을 확인하세요',
      };
      renderCurrentState();
      return;
    }

    captureOutputStart(plan);
    jogRequestInFlight = true;
    lastCommandResult = {
      success: true,
      message: `${motorLabelText} 동작 요청 전송 중`,
    };
    renderCurrentState();

    try {
      const requestAction = isDynamixelMotor(plan.motor)
        ? requestDynamixelAction
        : requestAcServoAction;
      const response = await requestAction({
        axis: selectedAxis,
        target_deg: plan.targetMotorDeg,
        duration_sec: plan.duration?.requestedSec,
      });
      if (lastOutputCapture && Boolean(response?.success)) {
        lastOutputCapture.commandAcceptedAtMs = Date.now();
      }
      lastCommandResult = {
        success: Boolean(response?.success),
        message: response?.message || `${motorLabelText} 동작 요청 결과 없음`,
      };
    } catch (error) {
      lastCommandResult = {
        success: false,
        message: `${motorLabelText} 동작 요청 실패: ${error?.message || error}`,
      };
    } finally {
      jogRequestInFlight = false;
      renderCurrentState();
    }
  }

  async function sendAcServoControl(action, scope = 'selected') {
    const motor = selectedMotor();
    if (scope === 'selected' && (!motor || !isAcServoMotor(motor))) {
      if (el.acServoControlMessage) {
        el.acServoControlMessage.textContent = '선택 축이 AC 서보가 아닙니다';
      }
      renderCurrentState();
      return;
    }
    if (scope === 'all' && detectedAcServoMotors(getLatestState()).length === 0) {
      if (el.acServoControlMessage) {
        el.acServoControlMessage.textContent = '감지된 AC 서보 축이 없습니다';
      }
      renderCurrentState();
      return;
    }
    if (action === 'servo_off') {
      const targetText = scope === 'all' ? '전체 AC 서보' : '선택 축 AC 서보';
      const confirmed = window.confirm(
        `${targetText} 서보 끄기 명령을 보냅니다.\n서보가 꺼지면 부하가 풀릴 수 있습니다. 계속할까요?`,
      );
      if (!confirmed) {
        if (el.acServoControlMessage) {
          el.acServoControlMessage.textContent = '서보 끄기 취소';
        }
        return;
      }
    }

    servoControlInFlight = true;
    if (el.acServoControlMessage) {
      const label = {
        servo_on: '서보 켜기',
        servo_off: '서보 끄기',
        fault_reset: '오류 초기화',
      }[action] || action;
      el.acServoControlMessage.textContent = `${label} 요청 전송 중`;
    }
    renderCurrentState();

    try {
      const response = await requestAcServoControl({
        action,
        scope,
        axis: selectedAxis,
      });
      if (el.acServoControlMessage) {
        el.acServoControlMessage.textContent = response?.message || 'AC 서보 제어 응답 없음';
      }
    } catch (error) {
      if (el.acServoControlMessage) {
        el.acServoControlMessage.textContent = `AC 서보 제어 요청 실패: ${error?.message || error}`;
      }
    } finally {
      servoControlInFlight = false;
      renderCurrentState();
    }
  }

  async function stopMotorMotion() {
    if (motionStopInFlight) return;
    motionStopInFlight = true;
    lastCommandResult = {
      success: true,
      message: '모터 동작 정지 요청 전송 중',
    };
    renderCurrentState();

    try {
      const response = await requestMotionSafetyStop();
      if (response?.success === false) {
        throw new Error(response.message || '모터 동작 정지 요청 실패');
      }
      const captureMotor = lastOutputCapture
        ? motorByAxis(getLatestState(), lastOutputCapture.axis)
        : null;
      if (lastOutputCapture && captureMotor) {
        lastOutputCapture.completedAtMs = Date.now();
        lastOutputCapture.completedResult = positionSnapshot(
          captureMotor,
          lastOutputCapture.gearRatio,
        );
      }
      lastCommandResult = {
        success: true,
        message: response?.message || '모터 동작을 정지했습니다. 서보 ON 상태는 유지됩니다.',
      };
    } catch (error) {
      lastCommandResult = {
        success: false,
        message: `모터 동작 정지 실패: ${error?.message || error}`,
      };
    } finally {
      motionStopInFlight = false;
      renderCurrentState();
    }
  }

  function bindEvents() {
    if (el.motionTestAxisSelect) {
      el.motionTestAxisSelect.addEventListener('change', () => {
        selectAxis(el.motionTestAxisSelect.value);
      });
    }
    [
      el.motionTestMode,
      el.motionTestJogDistance,
      el.motionTestPosition,
      el.motionTestGearRatio,
      el.motionTestDurationSec,
    ].forEach((input) => {
      if (!input) return;
      input.addEventListener('input', renderCurrentState);
      input.addEventListener('change', renderCurrentState);
    });
    if (el.motionTestModeButtons) {
      el.motionTestModeButtons.addEventListener('click', (event) => {
        const button = event.target.closest('[data-motion-test-mode]');
        if (!button || !el.motionTestMode) return;
        el.motionTestMode.value = button.dataset.motionTestMode || 'jog';
        renderCurrentState();
      });
    }
    if (el.motionTestJogNegativeButton) {
      el.motionTestJogNegativeButton.addEventListener('click', () => {
        sendJog(-1);
      });
    }
    if (el.motionTestJogPositiveButton) {
      el.motionTestJogPositiveButton.addEventListener('click', () => {
        sendJog(1);
      });
    }
    if (el.motionTestRunButton) {
      el.motionTestRunButton.addEventListener('click', sendAction);
    }
    if (el.motionTestStopButton) {
      el.motionTestStopButton.addEventListener('click', stopMotorMotion);
    }
    if (el.selectedAcServoOnButton) {
      el.selectedAcServoOnButton.addEventListener('click', () => {
        sendAcServoControl('servo_on', 'selected');
      });
    }
    if (el.selectedAcServoOffButton) {
      el.selectedAcServoOffButton.addEventListener('click', () => {
        sendAcServoControl('servo_off', 'selected');
      });
    }
    if (el.allAcServoOnButton) {
      el.allAcServoOnButton.addEventListener('click', () => {
        sendAcServoControl('servo_on', 'all');
      });
    }
    if (el.allAcServoOffButton) {
      el.allAcServoOffButton.addEventListener('click', () => {
        sendAcServoControl('servo_off', 'all');
      });
    }
    if (el.selectedAcServoFaultResetButton) {
      el.selectedAcServoFaultResetButton.addEventListener('click', () => {
        sendAcServoControl('fault_reset', 'selected');
      });
    }
  }

  function resetProjectState() {
    selectedAxis = null;
    lastAxisOptionsSignature = '';
    lastOutputCapture = null;
    lastCommandResult = null;
    jogRequestInFlight = false;
    servoControlInFlight = false;
    motionStopInFlight = false;
    renderLatestState();
  }

  return {
    bindEvents,
    resetProjectState,
    renderLatestState,
    selectAxis,
    getSelectedAxis: () => selectedAxis,
  };
}
