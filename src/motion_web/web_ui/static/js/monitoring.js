import {
  aliasText,
  countBy,
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
} from './format.js?v=20260714-generic-connection-state';

let lastMonitoringHeaderSignature = '';
let lastMonitoringRowsSignature = '';

export function renderAccess(payload, el) {
  const url = payload && payload.web_access && payload.web_access.url
    ? payload.web_access.url
    : `${location.protocol}//${location.host}/`;
  if (el.accessUrl) el.accessUrl.textContent = url;
}

function statusText(motor, rawMode) {
  if (rawMode) return formatHex(motor.statusword);
  const status = motor.status_text || stateLabel(motor.connection_state || motor.state);
  return statusDisplayLabels[status] || status;
}

function errorText(motor, rawMode) {
  const rawError = motor.errorcode_raw ?? motor.errorcode;
  return rawMode
    ? formatHex(rawError)
    : (motor.error_text || (Number(motor.errorcode) === 0 ? 'No error' : `Error ${formatNumber(motor.errorcode, 1)}`));
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
  return displayText(motor.motor_type_label || 'Unknown');
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
  return `<span class="state ${escapeHtml(stateClass)}" title="${displayText(detail)}">${displayText(stateLabel(stateName))}</span>`;
}

function positionHeaderText(rawMode) {
  return rawMode ? 'Position (cnt)' : 'Position (deg)';
}

function velocityHeaderText(rawMode) {
  return rawMode ? 'Velocity (cnt/s)' : 'Velocity (deg/s)';
}

function torqueHeaderText(rawMode) {
  return rawMode ? 'Torque (raw)' : 'Torque (Nm)';
}

function currentHeaderText(rawMode) {
  return rawMode ? 'Current (raw)' : 'Current (A)';
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
    { label: 'Axis', className: 'mono', cell: (motor) => axisText(motor) },
    { label: 'ID', className: 'mono', cell: (motor) => commonIdText(motor) },
    { label: 'Motor Type', cell: (motor) => displayMotorTypeText(motor) },
    { label: 'Name', cell: (motor) => displayNameText(motor) },
  ];

  const common = [
    ...identity,
    { label: 'State', cell: (motor) => stateCell(motor) },
    { label: 'Status', className: (motor) => statusClass(motor), cell: (motor) => displayText(statusText(motor, rawMode)) },
    { label: 'Error', className: (motor) => errorClass(motor), cell: (motor) => displayText(errorText(motor, rawMode)) },
    { label: positionHeaderText(rawMode), className: 'mono', cell: (motor) => displayText(positionText(motor, rawMode)) },
    { label: velocityHeaderText(rawMode), className: 'mono', cell: (motor) => displayText(velocityText(motor, rawMode)) },
    { label: 'Age (ms)', className: 'mono', cell: (motor) => displayText(ageText(motor)) },
  ];

  const acServo = [
    ...identity,
    { label: 'Driver', cell: (motor) => driverText(motor, rawMode) },
    { label: 'State', cell: (motor) => stateCell(motor) },
    { label: rawMode ? 'Statusword' : 'Status', className: (motor) => statusClass(motor), cell: (motor) => displayText(statusText(motor, rawMode)) },
    { label: rawMode ? 'Error Code' : 'Error', className: (motor) => errorClass(motor), cell: (motor) => displayText(errorText(motor, rawMode)) },
    { label: rawMode ? 'Position Raw (cnt·역산)' : positionHeaderText(false), className: 'mono', cell: (motor) => displayText(positionText(motor, rawMode)) },
    { label: rawMode ? 'Velocity Raw (cnt/s·역산)' : velocityHeaderText(false), className: 'mono', cell: (motor) => displayText(velocityText(motor, rawMode)) },
    { label: torqueHeaderText(rawMode), className: 'mono', cell: (motor) => displayText(torqueText(motor, rawMode)) },
    { label: currentHeaderText(rawMode), className: 'mono', cell: (motor) => displayText(currentText(motor, rawMode)) },
    { label: 'Age (ms)', className: 'mono', cell: (motor) => displayText(ageText(motor)) },
  ];

  const dynamixel = [
    ...identity,
    { label: 'Port', cell: (motor) => portText(motor) },
    { label: 'Model', cell: (motor) => driverText(motor, rawMode) },
    { label: 'State', cell: (motor) => stateCell(motor) },
    { label: 'Error', className: (motor) => errorClass(motor), cell: (motor) => displayText(errorText(motor, rawMode)) },
    { label: positionHeaderText(rawMode), className: 'mono', cell: (motor) => displayText(positionText(motor, rawMode)) },
    { label: velocityHeaderText(rawMode), className: 'mono', cell: (motor) => displayText(velocityText(motor, rawMode)) },
    { label: currentHeaderText(rawMode), className: 'mono', cell: (motor) => displayText(currentText(motor, rawMode)) },
    { label: 'Voltage', className: 'mono', cell: () => '-' },
    { label: 'Temperature', className: 'mono', cell: () => '-' },
    { label: 'Age (ms)', className: 'mono', cell: (motor) => displayText(ageText(motor)) },
  ];

  const cubemars = [
    ...identity,
    { label: 'CAN Bus', cell: (motor) => portText(motor) },
    { label: 'Mode', cell: () => '-' },
    { label: 'State', cell: (motor) => stateCell(motor) },
    { label: 'Error', className: (motor) => errorClass(motor), cell: (motor) => displayText(errorText(motor, rawMode)) },
    { label: positionHeaderText(rawMode), className: 'mono', cell: (motor) => displayText(positionText(motor, rawMode)) },
    { label: velocityHeaderText(rawMode), className: 'mono', cell: (motor) => displayText(velocityText(motor, rawMode)) },
    { label: torqueHeaderText(rawMode), className: 'mono', cell: (motor) => displayText(torqueText(motor, rawMode)) },
    { label: 'Temperature', className: 'mono', cell: () => '-' },
    { label: 'Age (ms)', className: 'mono', cell: (motor) => displayText(ageText(motor)) },
  ];

  const unknown = [
    ...identity,
    { label: 'Transport', cell: (motor) => displayText(motor.transport_label || motor.transport || '-') },
    { label: 'State', cell: (motor) => stateCell(motor) },
    { label: 'Status', className: (motor) => statusClass(motor), cell: (motor) => displayText(statusText(motor, rawMode)) },
    { label: 'Error', className: (motor) => errorClass(motor), cell: (motor) => displayText(errorText(motor, rawMode)) },
    { label: 'Age (ms)', className: 'mono', cell: (motor) => displayText(ageText(motor)) },
  ];

  const sets = {
    all: common,
    ac_servo: acServo,
    dynamixel,
    cubemars,
    unknown,
  };
  return sets[filter] || common;
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

function renderMotorTypeSummary(state, motors, el) {
  if (!el.motorTypeRows) return;
  const knownCounts = countBy(motors, 'motor_type_label');
  const onlineCounts = countBy(
    motors.filter((motor) => motor.connection_connected === true
      || (!('connection_connected' in motor) && motor.state === 'detected')),
    'motor_type_label',
  );
  const catalog = Array.isArray(state.motor_type_catalog) && state.motor_type_catalog.length > 0
    ? state.motor_type_catalog.map((item) => item.label || item.type || 'Unknown')
    : Object.keys(knownCounts);
  const labels = Array.from(new Set([...catalog, ...Object.keys(knownCounts), 'Unknown']));

  const html = labels.map((label) => {
    const known = knownCounts[label] || 0;
    const online = onlineCounts[label] || 0;
    const activeClass = online > 0 ? 'online' : 'offline';
    return `
      <div class="type-row ${activeClass}">
        <span>${label}</span>
        <strong>${formatInt(known)} / ${formatInt(online)}</strong>
      </div>
    `;
  }).join('');
  if (el.motorTypeRows.innerHTML !== html) el.motorTypeRows.innerHTML = html;
}

export function renderMonitoring(state, options) {
  const {
    el,
    rawMode,
    activeMonitoringFilter,
    shouldShowMonitoringMotor,
    registryCount,
    selectedMotionTestAxis,
  } = options;
  const allMotors = Array.isArray(state.motors) ? state.motors : [];
  const registryFilteredMotors = allMotors.filter((motor) => shouldShowMonitoringMotor(motor));
  const motors = filterMonitoringMotors(registryFilteredMotors, activeMonitoringFilter);
  const columns = monitoringColumnsForFilter(activeMonitoringFilter, rawMode);
  const enabled = Boolean(state.monitoring_enabled);

  if (el.monitoringState) el.monitoringState.textContent = enabled ? 'ON' : 'OFF';
  if (el.monitorToggle) {
    el.monitorToggle.textContent = enabled ? 'Turn Monitoring OFF' : 'Turn Monitoring ON';
    el.monitorToggle.classList.toggle('off', !enabled);
    el.monitorToggle.classList.toggle('primary', enabled);
  }
  if (el.displayModeToggle) el.displayModeToggle.textContent = rawMode ? '해석값 보기' : 'Raw 보기';
  const connectionSummary = state.connection_summary || {};
  const onlineCount = Number.isFinite(Number(connectionSummary.online))
    ? Number(connectionSummary.online)
    : Number(state.online_motors_count || state.detected_count || 0);
  if (el.onlineMotorCount) el.onlineMotorCount.textContent = formatInt(onlineCount);
  if (el.knownMotorCount) {
    el.knownMotorCount.textContent = `${formatInt(state.known_motors_count || allMotors.length)} / ${formatInt(state.max_motors || 50)}`;
  }
  renderMotorTypeSummary(state, allMotors, el);
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
    el.summaryText.textContent = `연결 상태: 온라인 ${formatInt(onlineCount)}축, 연결 끊김 ${formatInt(offlineCount)}축, 버스 끊김 ${formatInt(busDownCount)}축, 확인 중 ${formatInt(pendingCount)}축 · ${registryText}${filteredText} · 모터 타입 ${formatCounts(state.motor_type_counts)}`;
  }
  if (el.monitoringViewSummary) {
    el.monitoringViewSummary.textContent = `${motorFilterLabel(activeMonitoringFilter)} · 설정 기준 ${formatInt(motors.length)}축 표시`;
  }
  renderMonitoringTabs(registryFilteredMotors, activeMonitoringFilter, el);
  renderMonitoringHeader(columns, el);

  if (!el.rows) return;
  if (motors.length === 0) {
    const emptyText = registryCount > 0
      ? `No configured ${motorFilterLabel(activeMonitoringFilter)} runtime state received`
      : 'No configured motor. Load motor settings first.';
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
