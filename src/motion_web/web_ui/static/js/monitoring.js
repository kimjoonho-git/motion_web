import {
  aliasText,
  displayText,
  escapeHtml,
  formatCounts,
  formatHex,
  formatInt,
  formatNumber,
  formatTime,
  motorFilterLabel,
  normalizeMotorTypeKey,
  stateLabel,
  statusDisplayLabels,
} from './format.js?v=20260718-korean-ui';

let lastMonitoringHeaderSignature = '';
let lastMonitoringRowsSignature = '';
let lastMonitoringDetailSignature = '';

export function renderAccess(payload, el) {
  const url = payload && payload.web_access && payload.web_access.url
    ? payload.web_access.url
    : `${location.protocol}//${location.host}/`;
  if (el.accessUrl) el.accessUrl.textContent = url;
}

function statusText(motor, rawMode) {
  if (rawMode) return formatHex(motor.statusword);
  const status = motor.status_text || stateLabel(motor.connection_state || motor.state);
  const labels = {
    'Torque enabled': '토크 켜짐',
    'Torque disabled': '토크 꺼짐',
    'Communication unavailable': '통신 불가',
    'No runtime state': '실행 상태 없음',
  };
  return statusDisplayLabels[status] || labels[status] || status;
}

function errorText(motor, rawMode) {
  const rawError = motor.errorcode_raw ?? motor.errorcode;
  const provided = String(motor.error_text || '');
  const localized = provided === 'No error'
    ? '오류 없음'
    : provided === 'Communication unavailable'
      ? '통신 불가'
      : provided.replace(/^Error\s+/i, '오류 ');
  return rawMode
    ? formatHex(rawError)
    : (localized || (Number(motor.errorcode) === 0 ? '오류 없음' : `오류 ${formatNumber(motor.errorcode, 1)}`));
}

function statusClass(motor) {
  const status = motor.status_text || '';
  const statusword = Number(motor.statusword || 0);
  const dangerStatuses = new Set([
    'Fault',
    'Fault reaction active',
    'Quick stop active',
  ]);
  return motor.fault || dangerStatuses.has(status) || (statusword & 0x0008)
    ? 'status-danger'
    : '';
}

function errorClass(motor) {
  return Number(motor.errorcode || 0) !== 0 ? 'status-danger' : '';
}

function axisText(motor) {
  return displayText(formatInt(motor.controller_index));
}

function displayNameText(motor) {
  return displayText(motor.display_name || '-');
}

function displayMotorTypeText(motor) {
  const key = motorFilterKey(motor);
  return displayText(key === 'unknown'
    ? (motor.motor_type_label || '확인 불가')
    : motorFilterLabel(key));
}

function powerText(motor) {
  if (motor.rated_power_w === null || motor.rated_power_w === undefined || Number.isNaN(Number(motor.rated_power_w))) {
    return '-';
  }
  return `${formatInt(motor.rated_power_w)}W`;
}

function driverText(motor, rawMode) {
  const name = motor.driver_name || motor.driver_model || '-';
  const power = powerText(motor);
  return rawMode ? displayText(power) : displayText(name);
}

function ethercatAliasText(motor) {
  return displayText(aliasText(motor.alias));
}

function nodeIdText(motor) {
  const value = motor.bus_id ?? motor.node_id ?? motor.id ?? motor.device_id;
  return displayText(value === null || value === undefined ? '-' : formatInt(value));
}

function commonIdText(motor) {
  const type = motorFilterKey(motor);
  if (type === 'ac_servo') return ethercatAliasText(motor);
  if (type === 'dynamixel' || type === 'cubemars') return nodeIdText(motor);
  if (motor.alias !== null && motor.alias !== undefined) return ethercatAliasText(motor);
  return nodeIdText(motor);
}

function portText(motor) {
  return displayText(motor.port || motor.serial_port || motor.can_interface || '-');
}

function calculatedAcServoRaw(motor, engineeringValue) {
  if (motorFilterKey(motor) !== 'ac_servo') return null;
  const value = Number(engineeringValue);
  const pulsePerRevolution = Number(motor.pulse_per_revolution);
  if (!Number.isFinite(value)
    || !Number.isFinite(pulsePerRevolution)
    || pulsePerRevolution <= 0) return null;
  return Math.round((value / 360.0) * pulsePerRevolution);
}

function positionText(motor, rawMode) {
  if (rawMode) {
    const raw = motor.position_raw ?? calculatedAcServoRaw(
      motor,
      motor.position_deg ?? motor.position,
    );
    return raw !== null && raw !== undefined
      ? formatInt(raw)
      : '-';
  }
  return motor.position_deg !== null && motor.position_deg !== undefined
    ? formatNumber(motor.position_deg, 3)
    : formatNumber(motor.position, 3);
}

function positionTurnText(motor) {
  const positionDeg = Number(motor.position_deg ?? motor.position);
  if (Number.isFinite(positionDeg)) return formatNumber(positionDeg / 360.0, 2);
  const positionRaw = Number(motor.position_raw);
  const pulsePerRevolution = Number(motor.pulse_per_revolution);
  if (Number.isFinite(positionRaw)
    && Number.isFinite(pulsePerRevolution)
    && pulsePerRevolution > 0) {
    return formatNumber(positionRaw / pulsePerRevolution, 2);
  }
  return '-';
}

export function motionValueText(motor) {
  const value = Number(motor.motion_value_deg);
  if (motor.motion_value_status === 'received' && Number.isFinite(value)) {
    return formatNumber(value, 3);
  }
  const labels = {
    unmapped: '미설정',
    missing: '모션값 미수신',
  };
  return labels[motor.motion_value_status] || '-';
}

function motionValueClass() {
  return 'mono';
}

function velocityText(motor, rawMode) {
  if (rawMode) {
    const raw = motor.velocity_raw ?? calculatedAcServoRaw(
      motor,
      motor.velocity_deg_s ?? motor.velocity,
    );
    return raw !== null && raw !== undefined
      ? formatInt(raw)
      : '-';
  }
  return motor.velocity_deg_s !== null && motor.velocity_deg_s !== undefined
    ? formatNumber(motor.velocity_deg_s, 3)
    : formatNumber(motor.velocity, 3);
}

function torqueText(motor, rawMode) {
  if (rawMode) {
    return motor.torque_raw !== null && motor.torque_raw !== undefined
      ? formatInt(motor.torque_raw)
      : '-';
  }
  return motor.torque !== null && motor.torque !== undefined
    ? formatNumber(motor.torque, 4)
    : '-';
}

function currentText(motor, rawMode) {
  if (rawMode) {
    return motor.current_raw !== null && motor.current_raw !== undefined
      ? formatInt(motor.current_raw)
      : '-';
  }
  return motor.current !== null && motor.current !== undefined
    ? formatNumber(motor.current, 4)
    : '-';
}

function effortText(motor, rawMode) {
  const type = motorFilterKey(motor);
  if (type === 'dynamixel') {
    const value = currentText(motor, rawMode);
    return value === '-' || rawMode ? value : `${value} A`;
  }
  const value = torqueText(motor, rawMode);
  return value === '-' || rawMode ? value : `${value} Nm`;
}

function ageText(motor) {
  if (motor.age_sec === null || motor.age_sec === undefined || Number.isNaN(Number(motor.age_sec))) {
    return '-';
  }
  return formatNumber(Number(motor.age_sec) * 1000, 0);
}

function stateCell(motor) {
  const stateName = motor.connection_state || motor.state || 'unknown';
  const stateClassByName = {
    online: 'detected',
    offline: 'disconnected',
    bus_down: 'ethercat_down',
    initializing: 'stale',
    unknown: 'stale',
  };
  const stateClass = motor.fault ? 'fault' : (stateClassByName[stateName] || stateName);
  const detail = motor.connection_message || motor.state_detail || '';
  const runtimeLabel = stateName === 'online' ? '수신 중' : stateLabel(stateName);
  return `<span class="state ${escapeHtml(stateClass)}" title="${displayText(detail)}">${displayText(runtimeLabel)}</span>`;
}

function physicalConnectionCell(motor) {
  if (motorFilterKey(motor) !== 'ac_servo') return '-';
  const stateName = motor.physical_connection_state || 'not_scanned';
  const labels = {
    detected: '물리 확인',
    missing: '미검출',
    unknown: '확인 불가',
    not_scanned: '검색 전',
  };
  const classes = {
    detected: 'detected',
    missing: 'disconnected',
    unknown: 'stale',
    not_scanned: 'stale',
  };
  const detail = motor.physical_connection_message || '';
  return `<span class="state ${escapeHtml(classes[stateName] || 'stale')}" title="${displayText(detail)}">${displayText(labels[stateName] || '확인 불가')}</span>`;
}

function positionHeaderText(rawMode) {
  return rawMode ? '원시 위치 (cnt)' : '위치 (deg)';
}

function velocityHeaderText(rawMode) {
  return rawMode ? '원시 속도 (cnt/s)' : '속도 (deg/s)';
}

function torqueHeaderText(rawMode) {
  return rawMode ? '원시 토크' : '토크 (Nm)';
}

function currentHeaderText(rawMode) {
  return rawMode ? '원시 전류' : '전류 (A)';
}

export function motorFilterKey(motor) {
  const value = [
    motor.motor_type,
    motor.motor_type_label,
    motor.transport,
    motor.transport_label,
    motor.driver_model,
    motor.driver_name,
  ].join(' ');
  return normalizeMotorTypeKey(value, '');
}

function monitoringColumnsForFilter(filter, rawMode) {
  const identity = [
    { label: '축 번호', className: 'mono', cell: (motor) => axisText(motor) },
    { label: 'ID', className: 'mono', cell: (motor) => commonIdText(motor) },
    { label: '모터 종류', cell: (motor) => displayMotorTypeText(motor) },
    { label: '이름', cell: (motor) => displayNameText(motor) },
  ];

  const common = [
    ...identity,
    { label: '물리 연결 (최근 검색)', cell: (motor) => physicalConnectionCell(motor) },
    { label: '런타임 수신', cell: (motor) => stateCell(motor) },
    { label: '서보 상태', className: (motor) => statusClass(motor), cell: (motor) => displayText(statusText(motor, rawMode)) },
    { label: '오류', className: (motor) => errorClass(motor), cell: (motor) => displayText(errorText(motor, rawMode)) },
    { label: positionHeaderText(rawMode), className: 'mono', cell: (motor) => displayText(positionText(motor, rawMode)) },
    { label: '모션값 (deg)', className: (motor) => motionValueClass(motor), cell: (motor) => displayText(motionValueText(motor)) },
    { label: '회전수 (turn)', className: 'mono', cell: (motor) => displayText(positionTurnText(motor)) },
    { label: velocityHeaderText(rawMode), className: 'mono', cell: (motor) => displayText(velocityText(motor, rawMode)) },
    { label: rawMode ? '원시 토크/전류' : '토크/전류', className: 'mono', cell: (motor) => displayText(effortText(motor, rawMode)) },
    { label: '갱신 지연 (ms)', className: 'mono', cell: (motor) => displayText(ageText(motor)) },
  ];
  return common;
}

function renderMonitoringHeader(columns, el) {
  if (!el.motorHeaderRows) return;
  const signature = JSON.stringify(columns.map((column) => column.label));
  if (signature === lastMonitoringHeaderSignature) return;
  lastMonitoringHeaderSignature = signature;
  el.motorHeaderRows.innerHTML = `
    <tr>
      ${columns.map((column) => `<th>${displayText(column.label)}</th>`).join('')}
    </tr>
  `;
}

function renderMonitoringTabs(motors, activeMonitoringFilter, el) {
  if (!el.monitoringTabs) return;
  el.monitoringTabs.querySelectorAll('[data-monitoring-filter]').forEach((button) => {
    const key = button.dataset.monitoringFilter;
    const active = key === activeMonitoringFilter;
    const count = key === 'all'
      ? motors.length
      : motors.filter((motor) => motorFilterKey(motor) === key).length;
    const label = motorFilterLabel(key);
    button.textContent = `${label} ${formatInt(count)}`;
    button.classList.toggle('active', active);
    button.setAttribute('aria-selected', active ? 'true' : 'false');
  });
}

function filterMonitoringMotors(motors, activeMonitoringFilter) {
  if (activeMonitoringFilter === 'all') return motors;
  return motors.filter((motor) => motorFilterKey(motor) === activeMonitoringFilter);
}

function motorIdValue(motor) {
  const type = motorFilterKey(motor);
  const value = type === 'ac_servo'
    ? motor.alias
    : (motor.bus_id ?? motor.node_id ?? motor.id ?? motor.device_id);
  return value === null || value === undefined ? '-' : formatInt(value);
}

function motorTypeValue(motor) {
  const key = motorFilterKey(motor);
  return key === 'unknown'
    ? (motor.motor_type_label || '확인 불가')
    : motorFilterLabel(key);
}

function transportValue(motor) {
  const value = String(motor.transport_label || motor.transport || '').toLowerCase();
  const labels = {
    ethercat: 'EtherCAT',
    serial: '시리얼',
    canopen: 'CANopen',
    socketcan: 'SocketCAN',
    unknown: '확인 불가',
  };
  return labels[value] || motor.transport_label || motor.transport || '-';
}

function configurationValue(motor) {
  const labels = {
    configured: '프로젝트에 등록됨',
    unconfigured: '프로젝트에 등록되지 않음',
  };
  return labels[motor.configuration_state] || motor.configuration_state || '-';
}

function dateTimeValue(epochSeconds) {
  const value = Number(epochSeconds);
  if (!Number.isFinite(value) || value <= 0) return '-';
  return new Date(value * 1000).toLocaleString('ko-KR');
}

function numberUnit(value, digits, unit) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '-';
  return `${formatNumber(value, digits)}${unit ? ` ${unit}` : ''}`;
}

function integerUnit(value, unit) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '-';
  return `${formatInt(value)}${unit ? ` ${unit}` : ''}`;
}

function ageMillisecondsValue(motor) {
  if (motor.age_sec === null
    || motor.age_sec === undefined
    || Number.isNaN(Number(motor.age_sec))) return '-';
  return numberUnit(Number(motor.age_sec) * 1000, 0, 'ms');
}

function yesNoUnknown(value, yesText = '예', noText = '아니요') {
  if (value === null || value === undefined) return '확인 불가';
  return value ? yesText : noText;
}

function detailRowsHtml(rows) {
  return `
    <dl class="monitoring-detail-values">
      ${rows.map(([label, value, tone = '']) => `
        <div class="${escapeHtml(tone)}">
          <dt>${displayText(label)}</dt>
          <dd>${displayText(value)}</dd>
        </div>
      `).join('')}
    </dl>
  `;
}

function detailRowsForTab(motor, tab, rawMode) {
  const type = motorFilterKey(motor);
  const isDynamixel = type === 'dynamixel';
  if (tab === 'connection') {
    return [
      ['물리 연결 (최근 검색)', motor.physical_connection_state === 'detected'
        ? '물리 확인'
        : motor.physical_connection_state === 'missing'
          ? '미검출'
          : motor.physical_connection_state === 'unknown'
            ? '확인 불가'
            : '검색 전'],
      ['물리 연결 설명', motor.physical_connection_message || '-'],
      ['물리 검색 시각', dateTimeValue(motor.physical_connection_checked_at)],
      ['런타임 수신 상태', (motor.connection_state || motor.state) === 'online'
        ? '수신 중'
        : stateLabel(motor.connection_state || motor.state)],
      ['통신 방식', transportValue(motor)],
      ['마스터 ID', motor.master_id ?? '-'],
      ['통신 포트', motor.serial_port || motor.can_interface || '-'],
      ['통신 속도', integerUnit(motor.serial_baudrate, motor.serial_baudrate ? 'bps' : '')],
      ['마지막 수신 시각', dateTimeValue(motor.last_seen_at)],
      ['갱신 지연', ageMillisecondsValue(motor)],
      ['상태 판단 출처', motor.connection_source || '-'],
      ['상태 설명', motor.connection_message || motor.state_detail || '-'],
    ];
  }
  if (tab === 'operation') {
    const hasError = Boolean(motor.fault) || Number(motor.errorcode || 0) !== 0;
    return [
      ['서보 상태', yesNoUnknown(motor.servo_on, '켜짐', '꺼짐')],
      ['운전 상태', statusText(motor, false)],
      ['목표 도달', yesNoUnknown(motor.target_reached, '도달', '미도달')],
      ['상태워드', formatHex(motor.statusword)],
      ['오류 여부', hasError ? '오류 있음' : '오류 없음', hasError ? 'danger' : 'ok'],
      ['오류 코드', motor.errorcode_hex || formatHex(motor.errorcode_raw ?? motor.errorcode)],
      ['원본 오류 코드', formatHex(motor.errorcode_raw ?? motor.errorcode)],
      ['오류 설명', errorText(motor, false), hasError ? 'danger' : ''],
    ];
  }
  if (tab === 'realtime') {
    const positionRaw = motor.position_raw ?? calculatedAcServoRaw(
      motor, motor.position_deg ?? motor.position,
    );
    const velocityRaw = motor.velocity_raw ?? calculatedAcServoRaw(
      motor, motor.velocity_deg_s ?? motor.velocity,
    );
    const positionTurn = positionTurnText(motor);
    const motionValue = motionValueText(motor);
    return [
      ['표시 방식', rawMode ? '원시값' : '해석값'],
      ['현재 위치', rawMode ? integerUnit(positionRaw, 'count') : numberUnit(motor.position_deg ?? motor.position, 3, 'deg')],
      ['현재 모션값', motionValue === '-' || motionValue === '미설정' || motionValue === '모션값 미수신'
        ? motionValue : `${motionValue} deg`],
      ['모션 ID', motor.motion_id || '미설정'],
      ['모션값 상태', motor.motion_value_message || '-'],
      ['현재 회전수', positionTurn === '-' ? '-' : `${positionTurn} turn`],
      ['현재 속도', rawMode ? integerUnit(velocityRaw, 'count/s') : numberUnit(motor.velocity_deg_s ?? motor.velocity, 3, 'deg/s')],
      [isDynamixel ? '현재 전류' : '현재 토크', rawMode
        ? integerUnit(isDynamixel ? motor.current_raw : motor.torque_raw, 'raw')
        : numberUnit(isDynamixel ? motor.current : motor.torque, 4, isDynamixel ? 'A' : 'Nm')],
      ['측정 시각', dateTimeValue(motor.last_seen_at)],
      ['측정값 갱신 지연', ageMillisecondsValue(motor)],
    ];
  }
  if (tab === 'specification') {
    return [
      ['최소 위치', numberUnit(motor.lower, 3, 'deg')],
      ['최대 위치', numberUnit(motor.upper, 3, 'deg')],
      ['속도 설정값', numberUnit(motor.speed, 3, '')],
      ['가속도', numberUnit(motor.acceleration, 3, 'deg/s²')],
      ['감속도', numberUnit(motor.deceleration, 3, 'deg/s²')],
      ['프로파일 속도', numberUnit(motor.profile_velocity, 3, 'deg/s')],
      ['프로파일 가속도', numberUnit(motor.profile_acceleration, 3, 'deg/s²')],
      ['프로파일 감속도', numberUnit(motor.profile_deceleration, 3, 'deg/s²')],
      ['정격 토크', numberUnit(motor.rated_torque_nm, 4, 'Nm')],
      ['정격 전류', numberUnit(motor.rated_current_a, 4, 'A')],
      ['정격 출력', numberUnit(motor.rated_power_w, 0, 'W')],
      ['정격 속도', numberUnit(motor.rated_speed_rpm, 0, 'rpm')],
      ['회전당 펄스', integerUnit(motor.pulse_per_revolution, 'count/rev')],
    ];
  }
  return [
    ['축 번호', formatInt(motor.controller_index)],
    ['모터 ID', motorIdValue(motor)],
    ['축 이름', motor.display_name || '-'],
    ['모터 종류', motorTypeValue(motor)],
    ['드라이버 모델', motor.driver_model || motor.driver_name || '-'],
    ['드라이버 ID', motor.driver_id ?? '-'],
    ['설정 상태', configurationValue(motor)],
  ];
}

function renderMonitoringSummary(motors, el) {
  const online = motors.filter((motor) => motor.connection_connected === true
    || motor.connection_state === 'online').length;
  const faults = motors.filter((motor) => Boolean(motor.fault)
    || Number(motor.errorcode || 0) !== 0).length;
  const stale = motors.filter((motor) => motor.connection_state === 'stale').length;
  if (el.monitoringTotalCount) el.monitoringTotalCount.textContent = `${formatInt(motors.length)}축`;
  if (el.monitoringOnlineCount) el.monitoringOnlineCount.textContent = `${formatInt(online)}축`;
  if (el.monitoringFaultCount) {
    el.monitoringFaultCount.textContent = `${formatInt(faults)}축`;
    el.monitoringFaultCount.parentElement?.classList.toggle('alert', faults > 0);
  }
  if (el.monitoringStaleCount) {
    el.monitoringStaleCount.textContent = `${formatInt(stale)}축`;
    el.monitoringStaleCount.parentElement?.classList.toggle('warning', stale > 0);
  }
}

function renderMonitoringDetail(motors, selectedAxis, activeTab, rawMode, el) {
  const selected = motors.find((motor) => (
    selectedAxis !== null
    && selectedAxis !== undefined
    && Number(motor.controller_index) === Number(selectedAxis)
  )) || null;
  el.monitoringDetailTabs?.querySelectorAll('[data-monitoring-detail-tab]').forEach((button) => {
    const active = button.dataset.monitoringDetailTab === activeTab;
    button.classList.toggle('active', active);
    button.setAttribute('aria-selected', active ? 'true' : 'false');
    button.disabled = !selected;
  });
  if (el.monitoringDetailTitle) {
    el.monitoringDetailTitle.textContent = selected
      ? `축 ${formatInt(selected.controller_index)} · ${selected.display_name || '이름 없음'}`
      : '모터를 선택하세요';
  }
  if (el.monitoringDetailSubtitle) {
    el.monitoringDetailSubtitle.textContent = selected
      ? `${motorTypeValue(selected)} · ID ${motorIdValue(selected)} · ${stateLabel(selected.connection_state || selected.state)}`
      : '위 표의 모터 행을 누르면 상세 정보가 표시됩니다';
  }
  if (!el.monitoringDetailContent) return;
  const signature = JSON.stringify({ selected, activeTab, rawMode });
  if (signature === lastMonitoringDetailSignature) return;
  lastMonitoringDetailSignature = signature;
  el.monitoringDetailContent.innerHTML = selected
    ? detailRowsHtml(detailRowsForTab(selected, activeTab, rawMode))
    : '<div class="empty">모터를 선택하면 상세 정보가 표시됩니다</div>';
}

export function renderMonitoring(state, options) {
  const {
    el,
    rawMode,
    activeMonitoringFilter,
    shouldShowMonitoringMotor,
    registryCount,
    selectedMotionTestAxis,
    activeMonitoringDetailTab,
  } = options;
  const allMotors = Array.isArray(state.motors) ? state.motors : [];
  const registryFilteredMotors = allMotors.filter((motor) => shouldShowMonitoringMotor(motor));
  const motors = filterMonitoringMotors(registryFilteredMotors, activeMonitoringFilter);
  const columns = monitoringColumnsForFilter(activeMonitoringFilter, rawMode);
  const enabled = Boolean(state.monitoring_enabled);

  renderMonitoringSummary(registryFilteredMotors, el);
  renderMonitoringDetail(
    registryFilteredMotors,
    selectedMotionTestAxis,
    activeMonitoringDetailTab || 'basic',
    rawMode,
    el,
  );

  if (el.monitoringState) el.monitoringState.textContent = enabled ? '켜짐' : '꺼짐';
  if (el.monitorToggle) {
    el.monitorToggle.textContent = enabled ? '모니터링 끄기' : '모니터링 켜기';
    el.monitorToggle.classList.toggle('off', !enabled);
    el.monitorToggle.classList.toggle('primary', enabled);
  }
  if (el.displayModeToggle) el.displayModeToggle.textContent = rawMode ? '해석값 보기' : '원시값 보기';
  const connectionSummary = state.connection_summary || {};
  const onlineCount = Number.isFinite(Number(connectionSummary.online))
    ? Number(connectionSummary.online)
    : Number(state.online_motors_count || state.detected_count || 0);
  if (el.onlineMotorCount) el.onlineMotorCount.textContent = formatInt(onlineCount);
  if (el.knownMotorCount) {
    el.knownMotorCount.textContent = `${formatInt(state.known_motors_count || allMotors.length)} / ${formatInt(state.max_motors || 50)}`;
  }
  if (el.lastUpdate) el.lastUpdate.textContent = formatTime(state.generated_at);

  const registryFilteredCount = allMotors.length - registryFilteredMotors.length;
  const registryText = registryCount > 0
    ? `설정 축 표시 ${formatInt(registryFilteredMotors.length)}/${formatInt(registryCount)}축`
    : '설정 축 표시 0축';
  const filteredText = registryFilteredCount > 0
    ? `, 설정 외 runtime ${formatInt(registryFilteredCount)}축 숨김`
    : '';
  if (el.summaryText) {
    const offlineCount = Number(connectionSummary.offline || 0);
    const busDownCount = Number(connectionSummary.bus_down || 0);
    const pendingCount = Number(connectionSummary.stale || 0)
      + Number(connectionSummary.initializing || 0)
      + Number(connectionSummary.monitoring_off || 0)
      + Number(connectionSummary.unknown || 0);
    el.summaryText.textContent = `런타임 수신: 수신 중 ${formatInt(onlineCount)}축, 수신 끊김 ${formatInt(offlineCount)}축, 버스 끊김 ${formatInt(busDownCount)}축, 확인 중 ${formatInt(pendingCount)}축 · 물리 연결은 최근 AC Servo 검색 결과 기준 · ${registryText}${filteredText} · 모터 타입 ${formatCounts(state.motor_type_counts)}`;
  }
  if (el.monitoringViewSummary) {
    el.monitoringViewSummary.textContent = `${motorFilterLabel(activeMonitoringFilter)} · 설정 기준 ${formatInt(motors.length)}축 표시`;
  }
  renderMonitoringTabs(registryFilteredMotors, activeMonitoringFilter, el);
  renderMonitoringHeader(columns, el);

  if (!el.rows) return;
  if (motors.length === 0) {
    const emptyText = registryCount > 0
      ? `설정된 ${motorFilterLabel(activeMonitoringFilter)} 실행 상태를 아직 수신하지 못했습니다`
      : '설정된 모터가 없습니다. 모터 축 설정을 먼저 불러오세요.';
    const html = `<tr><td colspan="${columns.length}" class="empty">${displayText(emptyText)}</td></tr>`;
    if (el.rows.innerHTML !== html) el.rows.innerHTML = html;
    lastMonitoringRowsSignature = '';
    return;
  }

  const selectedForMotor = (motor) => {
    const axis = motor.controller_index;
    return axis !== null &&
      axis !== undefined &&
      selectedMotionTestAxis !== null &&
      selectedMotionTestAxis !== undefined &&
      Number(axis) === Number(selectedMotionTestAxis);
  };
  const structureSignature = JSON.stringify({
    columns: columns.map((column) => column.label),
    axes: motors.map((motor) => motor.controller_index),
  });

  if (structureSignature !== lastMonitoringRowsSignature) {
    lastMonitoringRowsSignature = structureSignature;
    el.rows.innerHTML = motors.map((motor) => {
      const axis = motor.controller_index;
      const selected = selectedForMotor(motor);
      return `
        <tr class="monitoring-row ${selected ? 'selected-row' : ''}" data-monitoring-axis="${escapeHtml(String(axis ?? ''))}">
        ${columns.map((column) => {
          const className = typeof column.className === 'function'
            ? column.className(motor)
            : column.className || '';
          return `<td class="${escapeHtml(className)}">${column.cell(motor)}</td>`;
        }).join('')}
        </tr>
      `;
    }).join('');
    return;
  }

  const rowElements = el.rows.querySelectorAll('tr.monitoring-row');
  motors.forEach((motor, rowIndex) => {
    const row = rowElements[rowIndex];
    if (!row) return;
    row.classList.toggle('selected-row', selectedForMotor(motor));
    const cells = row.querySelectorAll('td');
    columns.forEach((column, columnIndex) => {
      const cell = cells[columnIndex];
      if (!cell) return;
      const className = typeof column.className === 'function'
        ? column.className(motor)
        : column.className || '';
      if (cell.className !== className) cell.className = className;
      const html = column.cell(motor);
      if (cell.innerHTML !== html) cell.innerHTML = html;
    });
  });
}
