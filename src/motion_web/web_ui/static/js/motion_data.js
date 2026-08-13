import {
  checkMotionRun,
  configureMotionAutomation,
  deleteMotionMapping,
  deleteMotionFile,
  disableMotionAutomation,
  fetchMotionFile,
  fetchMotionFiles,
  fetchMotionMapping,
  fetchMotionMappings,
  fetchMotionRunStatus,
  initializeMotionRun,
  saveMotionMapping,
  startMotionAutomation,
  startMotionRun,
  stopMotionRun,
  stopMotionRunAfterCycle,
  requestMotionSafetyStop,
  validateMotionMapping,
} from './api.js?v=20260810-dds-release-1';
import {
  displayText,
  formatInt,
  formatNumber,
  normalizeMotorTypeKey,
} from './format.js?v=20260718-korean-ui';
import {
  showAlert,
  showConfirm,
  showPrompt,
} from './ui_dialogs.js?v=20260727-popup-common-3';

const MOTOR_AXIS_ANGLE_ALERT_DEG = 360.0;
const MOTION_ID_PATTERN = /^[1-9]\d*-[1-9]\d*$/;
const MOTION_RUN_STAGES = [
  { key: 'idle', label: '모션 전' },
  { key: 'ready', label: '준비 완료' },
  { key: 'initializing', label: '초기 위치 이동중' },
  { key: 'initialized', label: '초기 위치 완료' },
  { key: 'running', label: '모션중' },
  { key: 'verifying', label: '위치 확인중' },
  { key: 'completed', label: '모션 완료' },
];

function bytesText(value) {
  const bytes = Number(value);
  if (!Number.isFinite(bytes)) return '-';
  if (bytes < 1024) return `${formatInt(bytes)} B`;
  if (bytes < 1024 * 1024) return `${formatNumber(bytes / 1024, 1)} KB`;
  return `${formatNumber(bytes / (1024 * 1024), 2)} MB`;
}

function numericOr(value, fallback) {
  const number = Number(value);
  return Number.isFinite(number) ? number : fallback;
}

function targetText(value) {
  const number = Number(value);
  return Number.isFinite(number) ? `${formatNumber(number, 3)} deg` : '-';
}

function degValue(value) {
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

export function registeredMotionFileId(mapping = {}) {
  return String(mapping?.motion_file_id || '').trim();
}

export function effectiveMotionRunProgress(
  status = {},
  {
    nowSec = Date.now() / 1000,
    initialMoveTimeSec = null,
  } = {},
) {
  const state = String(status?.state || 'idle');
  const progress = status?.progress || {};
  const summaryDuration = Number(status?.summary?.duration_sec);
  let elapsed = Number(progress.elapsed_sec);
  let duration = Number(progress.duration_sec);

  if (!Number.isFinite(elapsed)) elapsed = 0.0;
  if (!Number.isFinite(duration) || duration < 0) duration = 0.0;

  if (state === 'initializing') {
    if (Number.isFinite(initialMoveTimeSec) && initialMoveTimeSec >= 0) {
      duration = initialMoveTimeSec;
    }
  } else if (
    state === 'running'
    || state === 'waiting'
    || state === 'verifying'
    || state === 'completed'
  ) {
    duration = duration > 0
      ? duration
      : (Number.isFinite(summaryDuration) ? summaryDuration : 0.0);
  }

  if (state === 'running' || state === 'initializing') {
    const updatedAt = Number(status?.updated_at);
    if (Number.isFinite(updatedAt) && updatedAt > 0 && nowSec >= updatedAt) {
      elapsed += nowSec - updatedAt;
    }
  } else if (
    state === 'waiting'
    || state === 'verifying'
    || state === 'completed'
  ) {
    elapsed = duration;
  }

  elapsed = Math.max(0.0, elapsed);
  if (duration > 0) elapsed = Math.min(elapsed, duration);
  return {
    elapsed_sec: elapsed,
    duration_sec: duration,
    ratio: duration > 0 ? Math.min(Math.max(elapsed / duration, 0.0), 1.0) : 0.0,
    sample_index: progress.sample_index,
    active_axis_count: progress.active_axis_count,
  };
}

export function motionMotorRef(motor) {
  if (!motor) return '';
  const motorType = normalizeMotorTypeKey(motor.motor_type, motor.motor_type_label);
  if (motorType === 'ac_servo') {
    const alias = motor.alias ?? motor.ethercat_alias;
    const numericAlias = Number(alias);
    const masterIndex = Number(motor.ethercat_master_index ?? 0);
    if (!Number.isInteger(masterIndex) || masterIndex < 0) return '';
    if (
      alias !== null && alias !== undefined && alias !== ''
      && Number.isFinite(numericAlias) && numericAlias > 0
    ) {
      return `ac_servo:master:${masterIndex}:alias:${numericAlias}`;
    }
    const slavePosition = Number(motor.slave_position);
    return Number.isInteger(slavePosition) && slavePosition >= 0
      ? `ac_servo:master:${masterIndex}:slave:${slavePosition}`
      : '';
  }
  if (motorType === 'dynamixel') {
    const busId = motor.bus_id ?? motor.node_id;
    const numericBusId = Number(busId);
    const serialPort = String(motor.serial_port ?? '').trim();
    return busId === null || busId === undefined || busId === ''
      || !Number.isInteger(numericBusId) || numericBusId < 0 || !serialPort
      ? ''
      : `dynamixel:port:${encodeURIComponent(serialPort)}:id:${numericBusId}`;
  }
  return '';
}

export function motionMotorRefs(motor) {
  const canonical = motionMotorRef(motor);
  const motorType = normalizeMotorTypeKey(motor?.motor_type, motor?.motor_type_label);
  if (motorType === 'ac_servo') {
    const alias = Number(motor?.alias ?? motor?.ethercat_alias);
    const legacy = Number.isFinite(alias) && alias > 0 ? `ac_servo:alias:${alias}` : '';
    return [canonical, legacy].filter(Boolean);
  }
  if (motorType === 'dynamixel') {
    const busId = Number(motor?.bus_id ?? motor?.node_id);
    const legacy = Number.isInteger(busId) && busId >= 0 ? `dynamixel:id:${busId}` : '';
    return [canonical, legacy].filter(Boolean);
  }
  return canonical ? [canonical] : [];
}

export function motionMotorSelectionValue(motor) {
  const motorRef = motionMotorRef(motor);
  if (motorRef) return motorRef;
  const axis = Number(motor?.controller_index);
  return Number.isFinite(axis) ? `axis:${axis}` : '';
}

export function motionMotorIdentityLabel(motor) {
  const motorType = normalizeMotorTypeKey(motor?.motor_type, motor?.motor_type_label);
  if (motorType === 'ac_servo') {
    const alias = Number(motor?.alias ?? motor?.ethercat_alias);
    const masterIndex = formatInt(motor?.ethercat_master_index ?? 0);
    return Number.isFinite(alias) && alias > 0
      ? `AC Master ${masterIndex} · EEPROM Alias ${formatInt(alias)}`
      : (
        `AC Master ${masterIndex}`
        + ` · EEPROM Alias 미설정 · Slave Position ${formatInt(motor?.slave_position)}`
      );
  }
  if (motorType === 'dynamixel') {
    const serialPort = String(motor?.serial_port ?? '').trim() || '직렬 포트 미설정';
    const busId = motor?.bus_id ?? motor?.node_id ?? motor?.id ?? motor?.device_id;
    return `Dynamixel ${serialPort} · ID ${formatInt(busId)}`;
  }
  const motorId = motor?.id ?? motor?.device_id;
  return `ID ${formatInt(motorId)}`;
}

export function motionMappingTargetKey(row) {
  const motorRef = String(row?.motor_ref || '').trim();
  if (motorRef) return `ref:${motorRef.toLowerCase()}`;
  if (row?.motor_axis !== null && row?.motor_axis !== undefined && row?.motor_axis !== '') {
    return `axis:${Number(row.motor_axis)}`;
  }
  return '';
}

export function motionMotorTargetKey(motor) {
  const motorRef = motionMotorRef(motor);
  if (motorRef) return `ref:${motorRef.toLowerCase()}`;
  const axis = Number(motor?.controller_index);
  return Number.isFinite(axis) ? `axis:${axis}` : '';
}

function defaultMotionAxisRow(motionId, motorAxis = null) {
  return {
    motion_id: String(motionId), enabled: motorAxis !== null,
    motor_ref: '', motor_axis: motorAxis,
    reference_enabled: true, reference_position_deg: 0.0,
    motion_lower_deg: -180.0, motion_upper_deg: 180.0,
    initial_mode: 'manual', initial_motion_position_deg: 0.0,
    initial_move_time_sec: 5.0, invert: false, offset_deg: 0.0,
    scale: 1.0, gear_ratio: 1.0,
  };
}

export function buildGeneratedMotionAxisRows(motors = [], previousRows = []) {
  const normalizedPrevious = (Array.isArray(previousRows) ? previousRows : []).map((row) => ({
    ...row,
    motor_ref: String(row?.motor_ref || '').trim().toLowerCase() === 'ac_servo:alias:0'
      ? ''
      : String(row?.motor_ref || '').trim(),
  })).map((row) => {
    const target = String(row.motor_ref || '').trim().toLowerCase();
    const matches = motors.filter((motor) => target
      ? motionMotorRefs(motor).some((ref) => ref.toLowerCase() === target)
      : Number(motor?.controller_index) === Number(row.motor_axis));
    return matches.length === 1
      ? { ...row, motor_ref: motionMotorRef(matches[0]) }
      : row;
  });
  const targetCounts = normalizedPrevious.reduce((counts, row) => {
    const key = motionMappingTargetKey(row);
    if (key) counts.set(key, (counts.get(key) || 0) + 1);
    return counts;
  }, new Map());
  const previousByTarget = new Map(normalizedPrevious
    .filter((row) => targetCounts.get(motionMappingTargetKey(row)) === 1)
    .map((row) => [motionMappingTargetKey(row), row]));
  const preservedRows = motors.map((motor) => (
    previousByTarget.get(motionMotorTargetKey(motor)) || null
  ));
  const usedMotionIds = new Set(preservedRows
    .map((row) => String(row?.motion_id || '').trim())
    .filter(Boolean));
  let nextMotionNumber = 1;
  const nextSuggestedMotionId = () => {
    let candidate = `1-${nextMotionNumber}`;
    while (usedMotionIds.has(candidate)) {
      nextMotionNumber += 1;
      candidate = `1-${nextMotionNumber}`;
    }
    usedMotionIds.add(candidate);
    nextMotionNumber += 1;
    return candidate;
  };
  return motors.map((motor, index) => {
    const motorRef = motionMotorRef(motor);
    const motorAxis = Number(motor?.controller_index);
    const existing = preservedRows[index];
    return {
      ...(existing || defaultMotionAxisRow(nextSuggestedMotionId(), motorAxis)),
      motor_ref: motorRef,
      motor_axis: motorAxis,
    };
  });
}

export function mergeConfiguredMotionMotors(runtimeMotors = [], configuredMotors = []) {
  if (!Array.isArray(configuredMotors) || !configuredMotors.length) {
    return Array.isArray(runtimeMotors) ? runtimeMotors : [];
  }
  const runtime = Array.isArray(runtimeMotors) ? runtimeMotors : [];
  return configuredMotors.map((configured) => {
    const controllerIndex = configured?.config?.controller_index ?? configured?.axis;
    const current = runtime.find((item) => (
      Number(item?.controller_index) === Number(controllerIndex)
    )) || {};
    return {
      ...current,
      controller_index: controllerIndex,
      display_name: configured?.name || current?.display_name || '-',
      motor_type: configured?.motor_type || current?.motor_type,
      motor_type_label: configured?.motor_type_label || current?.motor_type_label,
      alias: configured?.config?.alias
        ?? configured?.identity?.ethercat_alias
        ?? current?.alias,
      ethercat_master_index: configured?.config?.ethercat_master_index
        ?? configured?.identity?.ethercat_master_index
        ?? current?.ethercat_master_index
        ?? 0,
      ethercat_alias: configured?.identity?.ethercat_alias ?? current?.ethercat_alias,
      slave_position: configured?.identity?.slave_position
        ?? configured?.config?.position
        ?? current?.slave_position,
      bus_id: configured?.identity?.bus_id
        ?? configured?.config?.bus_id
        ?? current?.bus_id,
      node_id: configured?.identity?.node_id ?? current?.node_id,
      serial_port: configured?.identity?.serial_port
        ?? configured?.config?.serial_port
        ?? current?.serial_port,
    };
  });
}

function exceedsMotorAxisAngleAlert(value) {
  const number = degValue(value);
  return number !== null && Math.abs(number) >= MOTOR_AXIS_ANGLE_ALERT_DEG;
}

function timeText(epochSeconds) {
  if (!epochSeconds) return '-';
  return new Date(epochSeconds * 1000).toLocaleString();
}

function analysisOf(file) {
  return file?.analysis || {};
}

function statusText(file) {
  const analysis = analysisOf(file);
  if (!analysis.format_valid) return '형식 오류';
  if (!analysis.json_valid && analysis.valid) return '호환 형식';
  return analysis.valid ? '정상' : '검사 오류';
}

function statusClass(file) {
  const analysis = analysisOf(file);
  if (!analysis.format_valid || !analysis.valid) return 'bad';
  if (Array.isArray(analysis.warnings) && analysis.warnings.length) return 'warn';
  return 'ok';
}

function emptyRow(colspan, message) {
  return `<tr><td colspan="${colspan}" class="empty">${displayText(message)}</td></tr>`;
}

function valueGridHtml(items) {
  return items
    .map((item) => (
      `<div class="motion-summary-item"><span>${displayText(item.label)}</span><strong>${displayText(item.value)}</strong></div>`
    ))
    .join('');
}

function validationHtml(analysis) {
  if (!analysis || !analysis.format_valid) {
    return '<div class="empty">해석 가능한 모션 파일을 선택하세요</div>';
  }
  const interpolation = analysis.interpolation || {};
  const rows = [
    ['파일 형식', analysis.json_valid ? 'JSON' : '헤더 + 대괄호 행'],
    ['데이터 검사', analysis.valid ? '통과' : '오류 있음'],
    ['데이터 위치', analysis.source || '-'],
    ['기준 주기', `${formatNumber(interpolation.period_sec, 3)} s`],
    ['보간 필요', interpolation.required ? '필요' : '불필요'],
  ];
  const issueRows = [
    ...(analysis.errors || []).map((message) => ['오류', message]),
    ...(analysis.warnings || []).map((message) => ['주의', message]),
  ];
  const tableRows = [...rows, ...issueRows]
    .map(([label, value]) => `<tr><th>${displayText(label)}</th><td>${displayText(value)}</td></tr>`)
    .join('');
  return `<table class="motion-state-table"><tbody>${tableRows}</tbody></table>`;
}

function motionIdRowsHtml(analysis) {
  const motionIds = Array.isArray(analysis?.motion_ids) ? analysis.motion_ids : [];
  if (!motionIds.length) return emptyRow(8, 'motion ID가 없습니다');
  return motionIds.map((item) => (
    `<tr>
      <td>${displayText(item.motion_id)}</td>
      <td>${formatInt(item.count)}</td>
      <td>${formatNumber(item.first_value, 3)}</td>
      <td>${formatNumber(item.last_value, 3)}</td>
      <td>${formatNumber(item.min_value, 3)}</td>
      <td>${formatNumber(item.max_value, 3)}</td>
      <td>${formatNumber(item.first_time_sec, 3)} - ${formatNumber(item.last_time_sec, 3)}</td>
      <td>${item.requires_interpolation ? '필요' : '불필요'}</td>
    </tr>`
  )).join('');
}

export function motionFileOriginalText(file, analysis) {
  const content = String(file?.content || file?.content_preview || '');
  if (content.trim()) return content;
  const records = Array.isArray(analysis?.preview_records) ? analysis.preview_records : [];
  if (!records.length) return '원본 데이터가 없습니다';
  return records
    .map((record) => `[${formatInt(record.frame)}, ${formatNumber(record.time_sec, 3)}, "${record.motion_id}", ${formatNumber(record.value, 3)}]`)
    .join('\n');
}

function drawGraph(canvas, messageEl, analysis, hiddenIds = new Set()) {
  if (!canvas) return;
  const context = canvas.getContext('2d');
  if (!context) return;

  const width = Math.max(canvas.clientWidth || canvas.width, 320);
  const height = Math.max(canvas.clientHeight || canvas.height, 220);
  if (canvas.width !== width) canvas.width = width;
  if (canvas.height !== height) canvas.height = height;

  context.clearRect(0, 0, width, height);
  context.fillStyle = '#ffffff';
  context.fillRect(0, 0, width, height);

  const allSeries = Array.isArray(analysis?.graph_series) ? analysis.graph_series : [];
  const series = allSeries
    .map((item, index) => ({ ...item, colorIndex: index }))
    .filter((item) => !hiddenIds.has(String(item.motion_id)));
  const points = series.flatMap((item) => item.points || []);
  if (!allSeries.length) {
    if (messageEl) messageEl.textContent = '그래프 데이터가 없습니다';
    context.fillStyle = '#5d6b78';
    context.fillText('그래프 데이터 없음', 16, 28);
    return;
  }
  if (!series.length || !points.length) {
    if (messageEl) messageEl.textContent = '표시할 축이 없습니다';
    context.fillStyle = '#5d6b78';
    context.fillText('축 버튼을 눌러 그래프를 표시하세요', 16, 28);
    return;
  }

  const minTime = Math.min(...points.map((point) => Number(point.time_sec)));
  const maxTime = Math.max(...points.map((point) => Number(point.time_sec)));
  const minValue = Math.min(...points.map((point) => Number(point.value)));
  const maxValue = Math.max(...points.map((point) => Number(point.value)));
  const timeRange = Math.max(maxTime - minTime, 1e-9);
  const valueRange = Math.max(maxValue - minValue, 1e-9);
  const padLeft = 46;
  const padRight = 16;
  const padTop = 18;
  const padBottom = 34;
  const graphWidth = width - padLeft - padRight;
  const graphHeight = height - padTop - padBottom;
  const colors = ['#1f6feb', '#16834a', '#c62828', '#a05d00', '#7b3ff2', '#00838f', '#6d4c41', '#455a64'];

  context.strokeStyle = '#d6dee6';
  context.lineWidth = 1;
  context.beginPath();
  context.moveTo(padLeft, padTop);
  context.lineTo(padLeft, padTop + graphHeight);
  context.lineTo(padLeft + graphWidth, padTop + graphHeight);
  context.stroke();

  context.fillStyle = '#5d6b78';
  context.font = '12px Arial';
  context.fillText(`${formatNumber(maxValue, 1)} deg`, 6, padTop + 8);
  context.fillText(`${formatNumber(minValue, 1)} deg`, 6, padTop + graphHeight);
  context.fillText(`${formatNumber(minTime, 2)}s`, padLeft, height - 10);
  context.fillText(`${formatNumber(maxTime, 2)}s`, Math.max(padLeft, width - 68), height - 10);

  series.forEach((item, index) => {
    const color = colors[item.colorIndex % colors.length];
    const itemPoints = Array.isArray(item.points) ? item.points : [];
    if (!itemPoints.length) return;
    context.strokeStyle = color;
    context.lineWidth = 1.8;
    context.beginPath();
    itemPoints.forEach((point, pointIndex) => {
      const x = padLeft + (((Number(point.time_sec) - minTime) / timeRange) * graphWidth);
      const y = padTop + graphHeight - (((Number(point.value) - minValue) / valueRange) * graphHeight);
      if (pointIndex === 0) context.moveTo(x, y);
      else context.lineTo(x, y);
    });
    context.stroke();
    context.fillStyle = color;
    context.fillText(`ID ${item.motion_id}`, padLeft + 8 + ((index % 4) * 78), padTop + 14 + (Math.floor(index / 4) * 14));
  });

  if (messageEl) {
    messageEl.textContent = `표시 ${formatInt(series.length)}/${formatInt(allSeries.length)}`;
  }
}

export function createMotionDataController({
  el,
  getLatestState = () => null,
  getConfiguredMotors = null,
  onWorkContextChange,
  onProjectFilesChange,
  onExportMotionFileToStudio = async () => null,
}) {
  let files = [];
  let selectedFileId = null;
  let selectedFile = null;
  let mappingFiles = [];
  let selectedMappingId = null;
  let mappingDraft = emptyMappingDraft();
  let registeredMotionFileIdValue = '';
  let mappingRawText = '';
  let mappingValidation = null;
  let mappingMotionFileDetail = null;
  let loading = false;
  let mappingLoading = false;
  let mappingDirty = false;
  let fileLoadToken = 0;
  let mappingLoadToken = 0;
  let mappingRevision = '';
  let mappingRevisionConflict = false;
  let activeMotionPanel = 'files';
  let motionRunStatus = null;
  let motionRunLastResult = null;
  let motionRunLoading = false;
  let motionRunGraphAnimationId = null;
  let motionFileGraphFileId = '';
  let motionFileGraphToggleSignature = '';
  const motionFileGraphHiddenIds = new Set();
  let motionRunGraphFileId = '';
  let motionRunGraphToggleSignature = '';
  const motionRunGraphHiddenIds = new Set();
  let automationResumeModalHidden = false;

  function setMessage(message) {
    if (el.motionFileMessage) el.motionFileMessage.textContent = message;
  }

  function setMappingMessage(message) {
    if (el.motionMappingMessage) el.motionMappingMessage.textContent = message;
  }

  function markMappingDirty() {
    mappingDirty = true;
    onWorkContextChange?.();
  }

  function isMappingRevisionConflict(message) {
    const text = String(message || '');
    return text.includes('모션축 설정이 화면을 불러온 뒤 변경')
      || text.includes('모션축 설정 버전 정보가 없습니다');
  }

  async function resolveMappingRevisionConflict(message) {
    mappingRevisionConflict = true;
    setMappingMessage(`매핑 저장 실패: ${message}`);
    const reload = await showConfirm(
      '저장된 모션축 설정과 이 화면이 기준으로 삼은 설정이 다릅니다.\n'
      + '현재 편집 내용은 저장되지 않았습니다.\n\n'
      + '저장된 내용으로 되돌린 후 다시 변경·저장하시겠습니까?',
      {
        title: '모션축 설정 저장 충돌',
        confirmLabel: '저장된 내용 불러오기',
        cancelLabel: '편집 내용 유지',
        tone: 'warning',
      },
    );
    if (reload && selectedMappingId) await selectMapping(selectedMappingId);
  }

  function mappingFileRevision(file) {
    return String(file?.mapping_revision || file?.revision || '').trim();
  }

  function syncMappingFileRevision(file) {
    if (!file || typeof file !== 'object') return false;
    const fileId = String(file.id || file.filename || '').trim();
    const revision = mappingFileRevision(file);
    if (!fileId || fileId !== selectedMappingId || !revision) return false;
    mappingRevision = revision;
    mappingFiles = mappingFiles.map((item) => (
      item?.id === fileId ? { ...item, ...file } : item
    ));
    setMappingMessage(
      mappingDirty
        ? 'MIDI Bank 저장을 반영했습니다 · 편집 중인 모션축 설정은 유지됩니다'
        : 'MIDI Bank 저장을 반영했습니다 · 모션축 설정을 계속 편집할 수 있습니다',
    );
    return true;
  }

  async function confirmDiscardMappingChanges(action) {
    if (!mappingDirty) return true;
    return showConfirm(
      `저장하지 않은 모션축 설정 변경이 있습니다.\n변경 내용을 버리고 ${action}하시겠습니까?`,
      { title: '저장하지 않은 변경', confirmLabel: '변경 버리기', tone: 'warning' },
    );
  }

  function forceMappingNameInput(value = '') {
    if (!el.motionMappingName) return;
    el.motionMappingName.value = String(value || '');
  }

  function setMotionRunMessage(message) {
    if (el.motionRunMessage) el.motionRunMessage.textContent = message;
  }

  async function showMotionRunFailure(message, title) {
    const detail = String(message || '모션 실행 요청이 실패했습니다');
    const blockedByCoordination = /DDS 그룹 실행이 로컬 모션 실행을 사용 중입니다/.test(detail);
    await showAlert(
      blockedByCoordination
        ? `${detail}\n\n`
          + 'DDS 그룹 연동에서 그룹 실행을 종료하거나 「연동 일시 해제」를 실행한 뒤 다시 시도하세요.'
        : detail,
      {
        title: blockedByCoordination ? 'DDS 그룹 실행 중' : title,
        confirmLabel: '확인',
        tone: 'danger',
      },
    );
  }

  function emptyMappingDraft() {
    return {
      file_id: '',
      name: '',
      motion_file_id: '',
      created_at: null,
      updated_at: null,
      mappings: [],
    };
  }

  function sortedRuntimeMotors() {
    const latestState = getLatestState();
    const runtimeMotors = Array.isArray(latestState?.motors) ? latestState.motors : [];
    const hasConfiguredSource = typeof getConfiguredMotors === 'function';
    const configuredValue = hasConfiguredSource ? getConfiguredMotors() : null;
    const configuredMotors = Array.isArray(configuredValue) ? configuredValue : [];
    const runtimeMatchesConfiguration = latestState?.project_scope?.runtime_matches_selected === true
      && latestState?.project_scope?.motor_config_applied === true;
    const motors = hasConfiguredSource
      ? (configuredMotors.length
        ? mergeConfiguredMotionMotors(
          runtimeMatchesConfiguration ? runtimeMotors : [],
          configuredMotors,
        )
        : [])
      : runtimeMotors;
    return motors
      .filter((motor) => Number.isFinite(Number(motor?.controller_index)))
      .sort((a, b) => Number(a.controller_index) - Number(b.controller_index));
  }

  function motorIdText(motor) {
    const value = motor?.alias ?? motor?.bus_id ?? motor?.node_id ?? motor?.id ?? motor?.device_id;
    if (value === null || value === undefined || value === '') return '-';
    const numeric = Number(value);
    return Number.isFinite(numeric) ? formatInt(numeric) : String(value);
  }

  function motorOptionLabel(motor) {
    const identity = motionMotorIdentityLabel(motor);
    return `${identity} / 현재 축 번호 ${formatInt(motor.controller_index)} / ${motor.display_name || '-'}`;
  }

  function motorForAxis(axis) {
    if (axis === null || axis === undefined || axis === '') return null;
    return sortedRuntimeMotors().find((motor) => Number(motor.controller_index) === Number(axis)) || null;
  }

  function motorRefForMotor(motor) {
    return motionMotorRef(motor);
  }

  function motorForRef(motorRef) {
    const target = String(motorRef || '').trim().toLowerCase();
    if (!target) return null;
    const matches = sortedRuntimeMotors().filter((motor) => (
      motionMotorRefs(motor).some((ref) => ref.toLowerCase() === target)
    ));
    return matches.length === 1 ? matches[0] : null;
  }

  function motorSelectionValue(motor) {
    return motionMotorSelectionValue(motor);
  }

  function motorForSelectionValue(value) {
    const selection = String(value || '').trim();
    const axisMatch = /^axis:(\d+)$/.exec(selection.toLowerCase());
    return axisMatch ? motorForAxis(Number(axisMatch[1])) : motorForRef(selection);
  }

  function motorForMapping(row) {
    const byRef = motorForRef(row?.motor_ref);
    return byRef || (!row?.motor_ref ? motorForAxis(row?.motor_axis) : null);
  }

  function mappingTargetKey(row) {
    return motionMappingTargetKey(row);
  }

  function upgradeLegacyMappingRefs() {
    const rows = Array.isArray(mappingDraft.mappings) ? mappingDraft.mappings : [];
    rows.forEach((row) => {
      if (String(row.motor_ref || '').trim().toLowerCase() === 'ac_servo:alias:0') {
        row.motor_ref = '';
      }
      const current = motorForRef(row.motor_ref);
      if (current) {
        row.motor_axis = Number(current.controller_index);
        row.motor_ref = motorRefForMotor(current);
        return;
      }
      if (String(row.motor_ref || '').trim()) return;
      const motor = motorForAxis(row.motor_axis);
      const motorRef = motorRefForMotor(motor);
      if (motorRef) row.motor_ref = motorRef;
    });
  }

  function motorLimitInfo(row, detail) {
    const motor = motorForMapping(row);
    if (!motor) {
      return { className: 'warn', rangeText: '리미트 확인 불가' };
    }
    const lower = degValue(motor.lower);
    const upper = degValue(motor.upper);
    if (lower === null && upper === null) {
      return { className: 'warn', rangeText: '리미트 정보 없음' };
    }
    const targetMin = degValue(detail.motion_motor_target_min_deg);
    const targetMax = degValue(detail.motion_motor_target_max_deg);
    if (targetMin === null || targetMax === null) {
      return { className: 'warn', rangeText: limitRangeText(lower, upper) };
    }
    const below = lower !== null && targetMin < lower;
    const above = upper !== null && targetMax > upper;
    if (below || above) {
      return { className: 'bad', rangeText: limitRangeText(lower, upper) };
    }
    return { className: 'ok', rangeText: limitRangeText(lower, upper) };
  }

  function limitRangeText(lower, upper) {
    const lowerText = lower === null ? '-∞' : targetText(lower);
    const upperText = upper === null ? '+∞' : targetText(upper);
    return `${lowerText} ~ ${upperText}`;
  }

  function isDynamixelMotor(motor) {
    return normalizeMotorTypeKey(motor?.motor_type, motor?.motor_type_label) === 'dynamixel';
  }

  function isDynamixelMappingRow(row) {
    return isDynamixelMotor(motorForMapping(row));
  }

  function mappingGearRatioValue(row) {
    return isDynamixelMappingRow(row) ? 1.0 : numericOr(row?.gear_ratio, 1.0);
  }

  function normalizeDynamixelGearRatios() {
    const rows = Array.isArray(mappingDraft.mappings) ? mappingDraft.mappings : [];
    rows.forEach((row) => {
      if (isDynamixelMappingRow(row)) {
        row.gear_ratio = 1.0;
      }
    });
  }

  function firstMotionValueFor(motionId) {
    const motionIds = Array.isArray(mappingMotionFileDetail?.analysis?.motion_ids)
      ? mappingMotionFileDetail.analysis.motion_ids
      : [];
    const found = motionIds.find((item) => String(item.motion_id) === String(motionId));
    const value = Number(found?.first_value);
    return Number.isFinite(value) ? value : null;
  }

  function displayInitialPosition(row) {
    if ((row.initial_mode || 'first_frame') !== 'first_frame') {
      return numericOr(row.initial_motion_position_deg, 0.0);
    }
    const firstValue = firstMotionValueFor(row.motion_id);
    return firstValue === null ? numericOr(row.initial_motion_position_deg, 0.0) : firstValue;
  }

  function displayReferencePosition(row) {
    if (row.reference_enabled === false) {
      return 0.0;
    }
    return numericOr(row.reference_position_deg, 0.0);
  }

  function displayInitialMoveTime(row) {
    return numericOr(row.initial_move_time_sec, 5.0);
  }

  function motorPositionDeg(motor) {
    const candidates = [
      motor?.position_deg,
      motor?.position_actual_deg,
      motor?.output_position_deg,
      motor?.present_position_deg,
      motor?.position_actual,
    ];
    for (const value of candidates) {
      const number = Number(value);
      if (Number.isFinite(number)) return number;
    }
    return null;
  }

  function selectedMappingFile() {
    return mappingFiles.find((file) => file.id === selectedMappingId) || null;
  }

  function getWorkContext() {
    const mappingFile = selectedMappingFile();
    return {
      motionFile: selectedFile?.filename || '',
      motionFileSelected: Boolean(selectedFile),
      motionFileValid: Boolean(selectedFile && analysisOf(selectedFile).valid),
      mappingFile: mappingFile?.filename || selectedMappingId || '',
      mappingFileSelected: Boolean(selectedMappingId),
      mappingValid: Boolean(mappingValidation?.valid),
      mappingValidated: Boolean(mappingValidation),
      mappingChanged: mappingDirty,
    };
  }

  function motionRunPayload() {
    const initialMoveTimeSec = motionRunInitialMoveTimeSec();
    return {
      motion_file_id: registeredMotionFileId({ motion_file_id: registeredMotionFileIdValue }),
      mapping_file_id: selectedMappingId || '',
      initial_move_time_sec: initialMoveTimeSec,
      run_mode: 'once',
    };
  }

  function motionRunInitialMoveTimeSec() {
    const rawValue = String(el.motionRunInitialMoveTime?.value || 'mapping');
    if (rawValue === 'mapping') return null;
    const value = Number(rawValue);
    return [5, 7, 10].includes(value) ? value : 5;
  }

  function motionRunSelectedMotionFile() {
    const fileId = motionRunPayload().motion_file_id;
    if (mappingMotionFileDetail?.id === fileId) return mappingMotionFileDetail;
    if (selectedFile?.id === fileId) return selectedFile;
    return files.find((file) => file.id === fileId) || null;
  }

  async function ensureMotionRunMotionFileDetail() {
    const fileId = motionRunPayload().motion_file_id;
    if (!fileId) return null;
    return ensureMappingMotionFileDetail(fileId);
  }

  function motionRunStateText(state) {
    const key = String(state || 'idle');
    const labels = {
      idle: '모션 전',
      ready: '실행 준비 완료',
      initializing: '초기 위치 이동 중',
      initialized: '초기 위치 완료',
      running: '모션 중',
      waiting: '반복 대기 중',
      verifying: '위치 확인 중',
      stopping: '정지 중',
      stopped: '정지',
      completed: '모션 완료',
      error: '오류',
    };
    return labels[key] || key;
  }

  function motionRunStateClass(state) {
    const key = String(state || 'idle');
    if (key === 'error') return 'bad';
    if (
      key === 'running'
      || key === 'waiting'
      || key === 'initializing'
      || key === 'verifying'
      || key === 'stopping'
    ) return 'warn';
    if (key === 'ready' || key === 'initialized' || key === 'completed') return 'ok';
    return 'warn';
  }

  function motionRunStageKey(status) {
    const state = String(status?.state || 'idle');
    if (
      state === 'waiting'
      || state === 'stopping'
      || state === 'stopped'
      || state === 'error'
    ) return state;
    if (MOTION_RUN_STAGES.some((stage) => stage.key === state)) return state;
    return 'idle';
  }

  function motionRunStageIndex(key) {
    return MOTION_RUN_STAGES.findIndex((stage) => stage.key === key);
  }

  function motionRunEffectiveProgress(status = motionRunStatus || {}) {
    return effectiveMotionRunProgress(status, {
      initialMoveTimeSec: motionRunInitialMoveTimeSec(),
    });
  }

  function renderMotionRunStages() {
    if (!el.motionRunStageStrip) return;
    const status = motionRunStatus || {};
    const currentKey = motionRunStageKey(status);
    const currentIndex = motionRunStageIndex(currentKey);
    const stageHtml = MOTION_RUN_STAGES.map((stage, index) => {
      let className = 'motion-run-stage';
      if (currentIndex >= 0 && index < currentIndex) className += ' done';
      if (stage.key === currentKey) className += ' active';
      const label = stage.key === currentKey ? `현재: ${stage.label}` : stage.label;
      return `<span class="${className}">${displayText(label)}</span>`;
    }).join('');
    const extraState = (
      currentKey === 'waiting'
      || currentKey === 'stopping'
      || currentKey === 'stopped'
      || currentKey === 'error'
    )
      ? `<span class="motion-run-stage ${motionRunStateClass(currentKey)} active">${displayText(`현재: ${motionRunStateText(currentKey)}`)}</span>`
      : '';
    el.motionRunStageStrip.innerHTML = `${stageHtml}${extraState}`;
  }

  function motionRunProgressRatio(status = motionRunStatus || {}) {
    const state = String(status?.state || 'idle');
    if (state === 'completed' || state === 'initialized') return 1.0;
    if (state === 'ready' || state === 'idle') return 0.0;
    return motionRunEffectiveProgress(status).ratio;
  }

  function motionRunProgressText(status = motionRunStatus || {}) {
    const state = String(status?.state || 'idle');
    const progress = motionRunEffectiveProgress(status);
    if (state === 'initializing') {
      return `초기 위치 이동 ${formatNumber(progress.elapsed_sec, 2)} / ${formatNumber(progress.duration_sec, 2)} s`;
    }
    if (state === 'running' || state === 'stopping') {
      return `모션 진행 ${formatNumber(progress.elapsed_sec, 2)} / ${formatNumber(progress.duration_sec, 2)} s`;
    }
    if (state === 'waiting') return String(status?.message || '반복 대기 중');
    if (state === 'verifying') return '최종 위치 확인 중';
    if (state === 'initialized') return '초기 위치 이동 완료';
    if (state === 'completed') return `모션 완료 ${formatNumber(progress.duration_sec, 2)} s`;
    if (state === 'ready') return '실행 준비 완료';
    if (state === 'stopped') return '정지됨';
    if (state === 'error') return '오류';
    return '모션 전';
  }

  function renderMotionRunProgressBar() {
    const status = motionRunStatus || {};
    const state = String(status.state || 'idle');
    const ratio = motionRunProgressRatio(status);
    const percent = Math.min(Math.max(ratio, 0.0), 1.0) * 100;
    if (el.motionRunCurrentPhase) {
      el.motionRunCurrentPhase.textContent = `현재 단계: ${motionRunStateText(state)}`;
    }
    if (el.motionRunProgressText) {
      el.motionRunProgressText.textContent = `${motionRunProgressText(status)} · ${formatNumber(percent, 1)} %`;
    }
    if (el.motionRunProgressFill) {
      el.motionRunProgressFill.className = `motion-run-progress-fill ${state}`;
      el.motionRunProgressFill.style.width = `${percent}%`;
    }
  }

  function drawMotionRunGraph(canvas, messageEl, file, status = {}, hiddenIds = new Set()) {
    if (!canvas) return;
    const context = canvas.getContext('2d');
    if (!context) return;

    const width = Math.max(canvas.clientWidth || canvas.width, 360);
    const height = Math.max(canvas.clientHeight || canvas.height, 240);
    if (canvas.width !== width) canvas.width = width;
    if (canvas.height !== height) canvas.height = height;

    context.clearRect(0, 0, width, height);
    context.fillStyle = '#ffffff';
    context.fillRect(0, 0, width, height);

    const analysis = analysisOf(file);
    const allSeries = Array.isArray(analysis?.graph_series) ? analysis.graph_series : [];
    const series = allSeries
      .map((item, index) => ({ ...item, colorIndex: index }))
      .filter((item) => !hiddenIds.has(String(item.motion_id)));
    const points = series.flatMap((item) => item.points || []);
    if (!allSeries.length) {
      if (messageEl) messageEl.textContent = '모션 그래프 데이터가 없습니다';
      context.fillStyle = '#5d6b78';
      context.font = '13px Arial';
      context.fillText('모션 그래프 데이터 없음', 16, 28);
      return;
    }
    if (!series.length || !points.length) {
      if (messageEl) messageEl.textContent = '표시할 축이 없습니다';
      context.fillStyle = '#5d6b78';
      context.font = '13px Arial';
      context.fillText('축 버튼을 눌러 그래프를 표시하세요', 16, 28);
      return;
    }

    const minTime = Math.min(...points.map((point) => Number(point.time_sec)));
    const maxTime = Math.max(...points.map((point) => Number(point.time_sec)));
    const minValue = Math.min(...points.map((point) => Number(point.value)));
    const maxValue = Math.max(...points.map((point) => Number(point.value)));
    const timeRange = Math.max(maxTime - minTime, 1e-9);
    const valueRange = Math.max(maxValue - minValue, 1e-9);
    const padLeft = 54;
    const padRight = 18;
    const padTop = 20;
    const padBottom = 38;
    const graphWidth = width - padLeft - padRight;
    const graphHeight = height - padTop - padBottom;
    const colors = ['#1f6feb', '#16834a', '#c62828', '#a05d00', '#7b3ff2', '#00838f', '#6d4c41', '#455a64'];

    context.strokeStyle = '#d6dee6';
    context.lineWidth = 1;
    context.beginPath();
    context.moveTo(padLeft, padTop);
    context.lineTo(padLeft, padTop + graphHeight);
    context.lineTo(padLeft + graphWidth, padTop + graphHeight);
    context.stroke();

    context.fillStyle = '#5d6b78';
    context.font = '12px Arial';
    context.fillText(`${formatNumber(maxValue, 1)} deg`, 6, padTop + 8);
    context.fillText(`${formatNumber(minValue, 1)} deg`, 6, padTop + graphHeight);
    context.fillText(`${formatNumber(minTime, 2)}s`, padLeft, height - 10);
    context.fillText(`${formatNumber(maxTime, 2)}s`, Math.max(padLeft, width - 70), height - 10);

    series.forEach((item, index) => {
      const color = colors[item.colorIndex % colors.length];
      const itemPoints = Array.isArray(item.points) ? item.points : [];
      if (!itemPoints.length) return;
      context.strokeStyle = color;
      context.lineWidth = 1.8;
      context.beginPath();
      itemPoints.forEach((point, pointIndex) => {
        const x = padLeft + (((Number(point.time_sec) - minTime) / timeRange) * graphWidth);
        const y = padTop + graphHeight - (((Number(point.value) - minValue) / valueRange) * graphHeight);
        if (pointIndex === 0) context.moveTo(x, y);
        else context.lineTo(x, y);
      });
      context.stroke();
      context.fillStyle = color;
      context.fillText(`ID ${item.motion_id}`, padLeft + 8 + ((index % 5) * 82), padTop + 14 + (Math.floor(index / 5) * 14));
    });

    const state = String(status?.state || 'idle');
    const effective = motionRunEffectiveProgress(status);
    let cursorTime = minTime;
    if (
      state === 'running'
      || state === 'waiting'
      || state === 'verifying'
      || state === 'completed'
    ) {
      cursorTime = Math.min(Math.max(effective.elapsed_sec, minTime), maxTime);
    } else if (state === 'initialized') {
      cursorTime = minTime;
    }
    const cursorX = padLeft + (((cursorTime - minTime) / timeRange) * graphWidth);
    context.strokeStyle = state === 'running' ? '#111827' : '#64748b';
    context.lineWidth = 2;
    context.beginPath();
    context.moveTo(cursorX, padTop);
    context.lineTo(cursorX, padTop + graphHeight);
    context.stroke();

    context.fillStyle = state === 'running' ? '#111827' : '#64748b';
    context.font = '12px Arial';
    const cursorLabel = (
      state === 'running'
      || state === 'waiting'
      || state === 'verifying'
      || state === 'completed'
    )
      ? `${formatNumber(cursorTime, 2)}s`
      : motionRunStateText(state);
    context.fillText(cursorLabel, Math.min(cursorX + 6, width - 80), padTop + graphHeight - 8);

    if (messageEl) {
      const visibleText = `표시 ${series.length}/${allSeries.length}`;
      if (state === 'initializing') {
        messageEl.textContent = `초기 위치 이동 중 ${formatNumber(effective.elapsed_sec, 2)} / ${formatNumber(effective.duration_sec, 2)} s · ${visibleText}`;
      } else if (state === 'running') {
        messageEl.textContent = `모션 중 ${formatNumber(effective.elapsed_sec, 2)} / ${formatNumber(effective.duration_sec, 2)} s · ${visibleText}`;
      } else if (state === 'waiting') {
        messageEl.textContent = `${String(status?.message || '반복 대기 중')} · ${visibleText}`;
      } else if (state === 'verifying') {
        messageEl.textContent = `최종 위치 확인 중 · ${visibleText}`;
      } else if (state === 'completed') {
        messageEl.textContent = `모션 완료 ${formatNumber(effective.duration_sec, 2)} s · ${visibleText}`;
      } else {
        messageEl.textContent = visibleText;
      }
    }
  }

  function renderMotionRunGraph() {
    drawMotionRunGraph(
      el.motionRunGraphCanvas,
      el.motionRunGraphMessage,
      motionRunSelectedMotionFile(),
      motionRunStatus || {},
      motionRunGraphHiddenIds,
    );
  }

  function renderMotionRunGraphAxisToggles() {
    if (!el.motionRunGraphAxisToggles) return;
    const file = motionRunSelectedMotionFile();
    const fileId = String(motionRunPayload().motion_file_id || file?.id || '');
    if (fileId !== motionRunGraphFileId) {
      motionRunGraphFileId = fileId;
      motionRunGraphToggleSignature = '';
      motionRunGraphHiddenIds.clear();
    }
    const series = Array.isArray(analysisOf(file)?.graph_series)
      ? analysisOf(file).graph_series
      : [];
    if (!series.length) {
      if (motionRunGraphToggleSignature !== `${fileId}|empty`) {
        el.motionRunGraphAxisToggles.innerHTML = '';
        motionRunGraphToggleSignature = `${fileId}|empty`;
      }
      return;
    }
    const allVisible = series.every((item) => !motionRunGraphHiddenIds.has(String(item.motion_id)));
    const noneVisible = series.every((item) => motionRunGraphHiddenIds.has(String(item.motion_id)));
    const allStateText = allVisible ? '표시' : (noneVisible ? '숨김' : '일부');
    const signature = `${fileId}|${series.map((item) => {
      const motionId = String(item.motion_id);
      return `${motionId}:${motionRunGraphHiddenIds.has(motionId) ? '0' : '1'}`;
    }).join(',')}`;
    if (signature === motionRunGraphToggleSignature) return;
    const allButton = `<button type="button" class="motion-run-graph-toggle ${allVisible ? 'active' : ''}" data-motion-run-graph-all="true" aria-pressed="${allVisible}">전체 · ${allStateText}</button>`;
    const axisButtons = series.map((item) => {
      const motionId = String(item.motion_id);
      const visible = !motionRunGraphHiddenIds.has(motionId);
      return `<button type="button" class="motion-run-graph-toggle ${visible ? 'active' : ''}" data-motion-run-graph-id="${displayText(motionId)}" aria-pressed="${visible}">${displayText(motionId)} · ${visible ? '표시' : '숨김'}</button>`;
    }).join('');
    el.motionRunGraphAxisToggles.innerHTML = `${allButton}${axisButtons}`;
    motionRunGraphToggleSignature = signature;
  }

  function motionRunNeedsGraphAnimation() {
    const state = String(motionRunStatus?.state || 'idle');
    return state === 'initializing' || state === 'running' || state === 'verifying';
  }

  function updateMotionRunGraphAnimation() {
    if (motionRunGraphAnimationId !== null || !motionRunNeedsGraphAnimation()) return;
    const step = () => {
      motionRunGraphAnimationId = null;
      renderMotionRunProgressBar();
      renderMotionRunGraph();
      if (motionRunNeedsGraphAnimation()) {
        motionRunGraphAnimationId = window.requestAnimationFrame(step);
      }
    };
    motionRunGraphAnimationId = window.requestAnimationFrame(step);
  }

  function stopMotionRunGraphAnimationIfIdle() {
    if (motionRunNeedsGraphAnimation() || motionRunGraphAnimationId === null) return;
    window.cancelAnimationFrame(motionRunGraphAnimationId);
    motionRunGraphAnimationId = null;
  }

  function renderMotionRunSummary() {
    if (!el.motionRunSummary) return;
    const payload = motionRunPayload();
    const runFile = motionRunSelectedMotionFile();
    const mappingFile = selectedMappingFile();
    const status = motionRunStatus || {};
    const summary = status.summary || motionRunLastResult?.summary || {};
    const mismatch = selectedFileId
      && mappingDraft.motion_file_id
      && selectedFileId !== mappingDraft.motion_file_id;
    el.motionRunSummary.innerHTML = valueGridHtml([
      { label: '모션 파일', value: runFile?.filename || payload.motion_file_id || '-' },
      { label: '매핑 파일', value: mappingFile?.filename || payload.mapping_file_id || '-' },
      { label: '상태', value: motionRunStateText(status.state) },
      {
        label: '초기 이동',
        value: payload.initial_move_time_sec === null
          ? '매핑 축별 설정'
          : `${formatNumber(payload.initial_move_time_sec, 0)} s 일괄`,
      },
      { label: '실행 축', value: formatInt(summary.axis_count) },
      { label: '총 시간', value: `${formatNumber(summary.duration_sec, 3)} s` },
      {
        label: '연속 동작',
        value: typeof summary.continuous_available === 'boolean'
          ? (summary.continuous_available ? '가능' : '불가')
          : '검사 전',
      },
      { label: '주기', value: `${formatNumber(summary.period_sec, 3)} s` },
      { label: '주의', value: mismatch ? '매핑 파일의 모션 파일 기준으로 실행' : '-' },
    ]);
  }

  function renderMotionRunStatus() {
    if (!el.motionRunStatus) return;
    const status = motionRunStatus || {};
    const progress = motionRunEffectiveProgress(status);
    const capabilities = status.capabilities || {};
    const warnings = Array.isArray(status.warnings) ? status.warnings : [];
    const capabilityText = (capability) => {
      if (!capability || typeof capability.available !== 'boolean') return '검사 전';
      return `${capability.available ? '가능' : '불가'} — ${capability.reason || '-'}`;
    };
    const ratio = Number(progress.ratio);
    el.motionRunStatus.innerHTML = `
      <table class="motion-state-table motion-run-status-table">
        <tbody>
          <tr>
            <th>상태</th><td>${displayText(motionRunStateText(status.state))}</td>
            <th>모드 / 완료</th><td>${displayText(`${status.run_mode === 'continuous' ? '연속' : '1회'} / ${formatInt(status.cycle_count)}회`)}</td>
          </tr>
          <tr>
            <th>진행</th><td>${displayText(Number.isFinite(ratio) ? `${formatNumber(ratio * 100, 1)} %` : '-')}</td>
            <th>경과 / 전체</th><td>${displayText(`${formatNumber(progress.elapsed_sec, 2)} / ${formatNumber(progress.duration_sec, 2)} s`)}</td>
          </tr>
          <tr><th>초기 위치</th><td colspan="3">${displayText(capabilityText(capabilities.initial_position))}</td></tr>
          <tr><th>1회 모션</th><td colspan="3">${displayText(capabilityText(capabilities.single_run))}</td></tr>
          <tr><th>연속 모션</th><td colspan="3">${displayText(capabilityText(capabilities.continuous_run))}</td></tr>
          <tr><th>범위 제한</th><td colspan="3">${displayText(warnings.length ? warnings.join(' / ') : '제한 적용 없음')}</td></tr>
          <tr><th>메시지</th><td colspan="3">${displayText(status.message || '-')}</td></tr>
          <tr><th>갱신</th><td colspan="3">${displayText(timeText(status.updated_at))}</td></tr>
        </tbody>
      </table>
    `;
  }

  function renderMotionRunAxes() {
    if (!el.motionRunAxisRows) return;
    const axes = Array.isArray(motionRunStatus?.axes) ? motionRunStatus.axes : [];
    if (!axes.length) {
      el.motionRunAxisRows.innerHTML = emptyRow(10, '실행 준비 검사를 누르면 표시됩니다');
      return;
    }
    el.motionRunAxisRows.innerHTML = axes.map((axis) => (
      `<tr>
        <td class="mono">${displayText(axis.motion_id)}</td>
        <td class="mono">${formatInt(axis.motor_axis)}</td>
        <td>${displayText(axis.motor_type || '-')}</td>
        <td>${targetText(axis.motion_limit_lower_deg)}</td>
        <td>${targetText(axis.motion_limit_upper_deg)}</td>
        <td>${targetText(axis.initial_motor_target_deg)}</td>
        <td>${targetText(axis.target_min_deg)} ~ ${targetText(axis.target_max_deg)}</td>
        <td>${targetText(axis.loop_start_motion_deg)} / ${targetText(axis.loop_end_motion_deg)}</td>
        <td>${Number(axis.loop_delta_deg) <= Number(axis.loop_tolerance_deg)
          ? `가능 (차이 ${targetText(axis.loop_delta_deg)})`
          : `불가 (차이 ${targetText(axis.loop_delta_deg)}, 허용 ${targetText(axis.loop_tolerance_deg)})`}</td>
        <td>${axis.motion_clamped
          ? `${targetText(axis.source_motion_min_deg)} ~ ${targetText(axis.source_motion_max_deg)} → ${targetText(axis.command_motion_min_deg)} ~ ${targetText(axis.command_motion_max_deg)}`
          : '제한 없음'}</td>
      </tr>`
    )).join('');
  }

  function motionAutomationStateText(state) {
    const labels = {
      off: '사용 안 함',
      ready: '시작 대기',
      checking: '검사 중',
      starting: '시작 중',
      initializing: '초기 위치 이동 중',
      initialized: '초기 위치 이동 완료',
      running: '반복 중',
      waiting: '회차 사이 대기',
      stop_requested: '현재 단계 후 정지',
      stopped: '정지',
      blocked: '실행 차단',
    };
    const key = String(state || 'off');
    return labels[key] || key;
  }

  function renderMotionAutomation() {
    const automation = motionRunStatus?.automation || {};
    const enabled = automation.enabled === true;
    const armed = automation.armed === true;
    const repeatMode = String(automation.repeat_mode || 'direct');
    const dwellSec = Number(automation.dwell_sec);
    const busy = motionRunLoading || armed;
    const hasFiles = Boolean(
      motionRunPayload().motion_file_id && motionRunPayload().mapping_file_id,
    );
    const contextReady = getLatestState()?.execution_context?.ready === true;

    if (el.motionAutomationEnabled) {
      el.motionAutomationEnabled.checked = enabled;
      el.motionAutomationEnabled.disabled = motionRunLoading;
    }
    if (el.motionAutomationRepeatMode) {
      if (document.activeElement !== el.motionAutomationRepeatMode) {
        el.motionAutomationRepeatMode.value = repeatMode;
      }
      el.motionAutomationRepeatMode.disabled = !enabled || busy;
    }
    const showDwell = repeatMode === 'dwell' || repeatMode === 'dwell_reinitialize';
    el.motionAutomationDwellWrap?.classList.toggle('hidden', !showDwell);
    if (el.motionAutomationDwellSec) {
      if (document.activeElement !== el.motionAutomationDwellSec) {
        el.motionAutomationDwellSec.value = String(
          Number.isFinite(dwellSec) ? dwellSec : 0,
        );
      }
      el.motionAutomationDwellSec.disabled = !enabled || busy;
    }
    if (el.motionAutomationStartButton) {
      el.motionAutomationStartButton.disabled = (
        motionRunLoading || !enabled || armed || !hasFiles || !contextReady
      );
    }
    if (el.motionAutomationReserveButton) {
      el.motionAutomationReserveButton.disabled = (
        motionRunLoading || !enabled || !hasFiles || !contextReady
      );
    }
    if (el.motionAutomationStatus) {
      el.motionAutomationStatus.textContent = motionAutomationStateText(
        automation.state,
      );
      el.motionAutomationStatus.className = automation.state === 'blocked'
        ? 'bad-text'
        : (armed ? 'warning-text' : '');
    }
    if (el.motionAutomationDetail) {
      const fileName = motionRunSelectedMotionFile()?.filename
        || automation.motion_file_id
        || '재생 등록 파일 없음';
      el.motionAutomationDetail.textContent = automation.message
        || `${fileName} · ${repeatMode === 'direct'
          ? '바로 반복'
          : repeatMode === 'dwell'
            ? `${Number.isFinite(dwellSec) ? dwellSec : 0}초 정지 후 반복`
            : '초기 위치 이동 후 반복'}`;
    }
  }

  function renderAutomationResumeModal() {
    const modal = document.getElementById('automationResumeModal');
    if (!modal) return;
    
    const status = motionRunStatus || {};
    const automation = status.automation || {};
    const pending = Boolean(automation.resume_pending);
    
    // Automatically reset hidden state when pending becomes false (recovery completed or cancelled)
    if (!pending) {
      automationResumeModalHidden = false;
    }
    
    if (pending && !automationResumeModalHidden) {
      modal.classList.remove('hidden');
      
      const stageEl = document.getElementById('automationResumeStage');
      if (stageEl) {
        const stage = automation.stage || 'waiting';
        let stageText = '상태 확인 중';
        if (stage === 'waiting') stageText = '하드웨어 준비 대기 중';
        else if (stage === 'starting') stageText = '초기 위치 이동 중';
        else if (stage === 'running') stageText = '모션 실행 중';
        stageEl.textContent = stageText;
      }
      
      const messageEl = document.getElementById('automationResumeMessage');
      if (messageEl) {
        messageEl.textContent = automation.message || '하드웨어 연결 상태를 점검하고 있습니다.';
      }
    } else {
      modal.classList.add('hidden');
    }
  }

  function renderMotionRunPanel() {
    const payload = motionRunPayload();
    const status = motionRunStatus || {};
    const state = String(status.state || 'idle');
    const running = state === 'running' || state === 'initializing'
      || state === 'verifying' || state === 'stopping' || state === 'waiting';
    const hasMappingFile = Boolean(payload.mapping_file_id);
    const hasMotionFile = Boolean(payload.motion_file_id);
    const hasRequiredFiles = hasMappingFile && hasMotionFile;
    const context = getLatestState()?.execution_context || {};
    const contextReady = context.ready === true;
    const contextMessage = context.message || '현재 프로젝트 실행 설정 적용 대기 중입니다';
    const statusMatchesFiles = status.motion_file_id === payload.motion_file_id
      && status.mapping_file_id === payload.mapping_file_id;
    const continuousCapability = statusMatchesFiles
      ? status.capabilities?.continuous_run
      : null;
    const continuousUnavailable = continuousCapability?.available === false;
    if (el.motionRunCheckButton) {
      el.motionRunCheckButton.disabled = motionRunLoading || !contextReady || !hasRequiredFiles || running;
      el.motionRunCheckButton.title = contextReady ? '' : contextMessage;
    }
    if (el.motionRunInitializeButton) {
      el.motionRunInitializeButton.disabled = motionRunLoading || !contextReady || !hasMappingFile || running;
      el.motionRunInitializeButton.title = contextReady ? '' : contextMessage;
    }
    if (el.motionRunStartButton) {
      el.motionRunStartButton.disabled = motionRunLoading || !contextReady || !hasRequiredFiles || running;
      el.motionRunStartButton.title = contextReady
        ? '전체 모션축 초기 위치 이동 완료 후 모션을 1회 실행합니다'
        : contextMessage;
    }
    if (el.motionRunContinuousStartButton) {
      el.motionRunContinuousStartButton.disabled = motionRunLoading
        || !contextReady || !hasRequiredFiles || running || continuousUnavailable;
      el.motionRunContinuousStartButton.title = continuousUnavailable
        ? (continuousCapability?.reason || '연속 모션 안전조건을 통과하지 못했습니다')
        : (contextReady
          ? '전체 모션축 초기 위치 이동 완료 후 정지할 때까지 모션을 반복합니다'
          : contextMessage);
    }
    if (el.motionRunStopButton) {
      el.motionRunStopButton.disabled = motionRunLoading || !running;
    }
    if (el.motionRunStopAfterButton) {
      el.motionRunStopAfterButton.disabled = motionRunLoading || !running;
    }
    if (el.motionRunRefreshButton) {
      el.motionRunRefreshButton.disabled = motionRunLoading;
    }
    if (el.motionRunInitialMoveTime) {
      el.motionRunInitialMoveTime.disabled = motionRunLoading || running;
    }
    if (el.motionRunMessage) {
      const message = motionRunLoading
        ? '모션 동작 요청 처리 중'
        : !hasMappingFile
          ? '모션축 설정 파일을 선택하세요'
          : !hasMotionFile
            ? '모션 파일 없음 · 첫 프레임 축은 모션 0°로 초기 위치 이동할 수 있습니다'
          : status.message || '실행 준비 가능';
      el.motionRunMessage.textContent = message;
    }
    renderMotionRunSummary();
    renderMotionRunStatus();
    renderMotionRunStages();
    renderMotionRunProgressBar();
    renderMotionRunGraphAxisToggles();
    renderMotionRunGraph();
    updateMotionRunGraphAnimation();
    stopMotionRunGraphAnimationIfIdle();
    renderMotionRunAxes();
    renderMotionAutomation();
    renderAutomationResumeModal();
  }

  function renderMotionTabs(active = null) {
    const next = String(active || activeMotionPanel || 'files');
    activeMotionPanel = ['files', 'mapping', 'midi', 'run'].includes(next)
      ? next
      : 'files';
    if (el.motionPanels) {
      el.motionPanels.forEach((panel) => {
        panel.classList.toggle('hidden', panel.dataset.motionPanel !== activeMotionPanel);
      });
    }
  }

  function renderFileRows() {
    if (!el.motionFileRows) return;
    if (!files.length) {
      el.motionFileRows.innerHTML = emptyRow(5, '저장된 모션 파일이 없습니다');
      return;
    }
    el.motionFileRows.innerHTML = files.map((file) => {
      const analysis = analysisOf(file);
      const selected = file.id === selectedFileId;
      const duration = analysis.time?.duration_sec;
      return (
        `<tr class="${selected ? 'selected' : ''}" data-motion-file-id="${displayText(file.id)}">
          <td><button type="button" class="link-button" data-motion-file-id="${displayText(file.id)}">${displayText(file.filename)}</button></td>
          <td><span class="motion-state-pill ${statusClass(file)}">${statusText(file)}</span></td>
          <td>${formatNumber(duration, 3)} s</td>
          <td>${formatInt(analysis.motion_id_count)}</td>
          <td>${bytesText(file.size_bytes)}</td>
        </tr>`
      );
    }).join('');
  }

  function renderMotionFileGraphAxisToggles(file) {
    if (!el.motionFileGraphAxisToggles) return;
    const fileId = String(file?.id || '');
    if (fileId !== motionFileGraphFileId) {
      motionFileGraphFileId = fileId;
      motionFileGraphToggleSignature = '';
      motionFileGraphHiddenIds.clear();
    }
    const series = Array.isArray(analysisOf(file)?.graph_series)
      ? analysisOf(file).graph_series
      : [];
    if (!series.length) {
      if (motionFileGraphToggleSignature !== `${fileId}|empty`) {
        el.motionFileGraphAxisToggles.innerHTML = '';
        motionFileGraphToggleSignature = `${fileId}|empty`;
      }
      return;
    }
    const allVisible = series.every((item) => !motionFileGraphHiddenIds.has(String(item.motion_id)));
    const noneVisible = series.every((item) => motionFileGraphHiddenIds.has(String(item.motion_id)));
    const allStateText = allVisible ? '표시' : (noneVisible ? '숨김' : '일부');
    const signature = `${fileId}|${series.map((item) => {
      const motionId = String(item.motion_id);
      return `${motionId}:${motionFileGraphHiddenIds.has(motionId) ? '0' : '1'}`;
    }).join(',')}`;
    if (signature === motionFileGraphToggleSignature) return;
    const allButton = `<button type="button" class="motion-run-graph-toggle ${allVisible ? 'active' : ''}" data-motion-file-graph-all="true" aria-pressed="${allVisible}">전체 · ${allStateText}</button>`;
    const axisButtons = series.map((item) => {
      const motionId = String(item.motion_id);
      const visible = !motionFileGraphHiddenIds.has(motionId);
      return `<button type="button" class="motion-run-graph-toggle ${visible ? 'active' : ''}" data-motion-file-graph-id="${displayText(motionId)}" aria-pressed="${visible}">${displayText(motionId)} · ${visible ? '표시' : '숨김'}</button>`;
    }).join('');
    el.motionFileGraphAxisToggles.innerHTML = `${allButton}${axisButtons}`;
    motionFileGraphToggleSignature = signature;
  }

  function renderMotionFileGraph(file) {
    renderMotionFileGraphAxisToggles(file);
    drawGraph(
      el.motionFileGraphCanvas,
      el.motionFileGraphMessage,
      analysisOf(file),
      motionFileGraphHiddenIds,
    );
  }

  function renderSelectedFile() {
    const file = selectedFile;
    const analysis = analysisOf(file);
    if (el.deleteMotionFileButton) {
      el.deleteMotionFileButton.disabled = !file || loading;
      el.deleteMotionFileButton.title = (
        file && file.id === registeredMotionFileIdValue
          ? '재생 등록을 해제한 뒤 삭제할 수 있습니다'
          : ''
      );
    }
    if (el.exportMotionFileToStudioButton) {
      el.exportMotionFileToStudioButton.disabled = !file || loading;
      el.exportMotionFileToStudioButton.title = file
        ? '선택한 실행 파일을 독립된 스튜디오 레이어로 내보냅니다'
        : '모션 파일을 먼저 선택하세요';
    }
    if (el.registerMotionFileButton) {
      const registered = Boolean(file && file.id === registeredMotionFileIdValue);
      el.registerMotionFileButton.disabled = (
        !file || !selectedMappingId || loading || mappingLoading || mappingDirty || registered
      );
      el.registerMotionFileButton.textContent = registered
        ? '재생 등록됨'
        : (mappingDirty ? '설정 저장 필요' : '재생 등록');
      el.registerMotionFileButton.title = !selectedMappingId
        ? '저장된 모션축 설정을 먼저 선택하세요'
        : (mappingDirty ? '모션축 설정의 편집 내용을 먼저 저장하거나 되돌리세요' : '');
    }
    if (el.unregisterMotionFileButton) {
      const registered = Boolean(file && file.id === registeredMotionFileIdValue);
      el.unregisterMotionFileButton.disabled = (
        !registered || !selectedMappingId || loading || mappingLoading || mappingDirty
      );
      el.unregisterMotionFileButton.title = registered
        ? '현재 모션축 설정에서 이 파일의 재생 등록을 해제합니다'
        : '현재 재생 등록된 파일을 선택하세요';
    }
    if (!file) {
      if (el.motionFileSummary) el.motionFileSummary.innerHTML = '<div class="empty">파일을 선택하세요</div>';
      if (el.motionFileValidation) el.motionFileValidation.innerHTML = '파일을 선택하세요';
      if (el.motionFileMotionIdRows) el.motionFileMotionIdRows.innerHTML = emptyRow(8, '파일을 선택하세요');
      if (el.motionFilePreviewRows) el.motionFilePreviewRows.textContent = '파일을 선택하세요';
      renderMotionFileGraph(null);
      return;
    }

    if (el.motionFileSummary) {
      const interpolation = analysis.interpolation || {};
      el.motionFileSummary.innerHTML = valueGridHtml([
        { label: '파일명', value: file.filename },
        { label: '상태', value: statusText(file) },
        { label: '레코드', value: `${formatInt(analysis.valid_records)} / ${formatInt(analysis.total_records)}` },
        { label: '총 시간', value: `${formatNumber(analysis.time?.duration_sec, 3)} s` },
        { label: '모션 ID', value: formatInt(analysis.motion_id_count) },
        { label: '보간', value: interpolation.required ? '20ms 선형보간 필요' : '20ms 기준 통과' },
      ]);
    }
    if (el.motionFileValidation) el.motionFileValidation.innerHTML = validationHtml(analysis);
    if (el.motionFileMotionIdRows) el.motionFileMotionIdRows.innerHTML = motionIdRowsHtml(analysis);
    if (el.motionFilePreviewRows) el.motionFilePreviewRows.textContent = motionFileOriginalText(file, analysis);
    renderMotionFileGraph(file);
  }

  function renderMappingSelect() {
    if (!el.motionMappingSelect) return;
    const options = [
      '<option value="">새 매핑 또는 파일 선택</option>',
      ...mappingFiles.map((file) => {
        const label = `${file.name || file.filename} (${formatInt(file.enabled_count)} / ${formatInt(file.mapping_count)})`;
        const selected = file.id === selectedMappingId ? ' selected' : '';
        return `<option value="${displayText(file.id)}"${selected}>${displayText(label)}</option>`;
      }),
    ];
    el.motionMappingSelect.innerHTML = options.join('');
    el.motionMappingSelect.value = selectedMappingId || '';
  }

  function renderMappingMotionFileSelect() {
    if (!el.motionMappingFileSelect) return;
    const options = [
      '<option value="">모션 파일 선택</option>',
      ...files.map((file) => {
        const selected = file.id === mappingDraft.motion_file_id ? ' selected' : '';
        const analysis = analysisOf(file);
        const label = `${file.filename} · ID ${formatInt(analysis.motion_id_count)}`;
        return `<option value="${displayText(file.id)}"${selected}>${displayText(label)}</option>`;
      }),
    ];
    el.motionMappingFileSelect.innerHTML = options.join('');
    el.motionMappingFileSelect.value = mappingDraft.motion_file_id || '';
  }

  function mappingDuplicateAxisCounts() {
    return mappingDraft.mappings.reduce((counts, row) => {
      const key = mappingTargetKey(row);
      if (!row.enabled || !key) return counts;
      counts[key] = (counts[key] || 0) + 1;
      return counts;
    }, {});
  }

  function mappingRowStatus(row, duplicateCounts) {
    if (!row.enabled) return { text: '비활성', className: 'warn' };
    const targetKey = mappingTargetKey(row);
    if (!targetKey) {
      return { text: '모터축 미선택', className: 'bad' };
    }
    if ((duplicateCounts[targetKey] || 0) > 1) {
      return { text: '중복 매칭', className: 'bad' };
    }
    if (!motorForMapping(row)) {
      return { text: '현재 모터 없음', className: 'warn' };
    }
    return { text: '사용 가능', className: 'ok' };
  }

  function mappingValidationRowStatus(row, fallbackStatus) {
    const validationRow = mappingValidation?.rows?.[String(row.motion_id)];
    if (!validationRow) return fallbackStatus;
    if (validationRow.status === 'error') return { text: '검증 오류', className: 'bad' };
    if (validationRow.status === 'warning') return { text: '검증 주의', className: 'warn' };
    return { text: '검증 통과', className: 'ok' };
  }

  function motorSelectHtml(row) {
    const motors = sortedRuntimeMotors();
    const mappedMotor = motorForMapping(row);
    const value = String(row.motor_ref || motorSelectionValue(mappedMotor) || '');
    return (
      `<select class="wide-select" data-motion-mapping-field="motor_ref" data-motion-id="${displayText(row.motion_id)}">
        <option value="">선택 안함</option>
        ${motors.map((motor) => {
          const selectionValue = motorSelectionValue(motor);
          const selected = selectionValue === value ? ' selected' : '';
          return selectionValue
            ? `<option value="${displayText(selectionValue)}"${selected}>${displayText(motorOptionLabel(motor))}</option>`
            : '';
        }).join('')}
      </select>`
    );
  }

  function renderMappingRows() {
    if (!el.motionMappingRows) return;
    const rows = Array.isArray(mappingDraft.mappings) ? mappingDraft.mappings : [];
    if (!rows.length) {
      el.motionMappingRows.innerHTML = emptyRow(15, '모션 ID를 직접 추가하거나 모터축에서 자동 생성하세요');
      return;
    }
    const duplicateCounts = mappingDuplicateAxisCounts();
    el.motionMappingRows.innerHTML = rows.map((row, index) => {
      const status = mappingValidationRowStatus(row, mappingRowStatus(row, duplicateCounts));
      const initialMode = row.initial_mode || 'first_frame';
      const referenceDisabled = row.reference_enabled === false;
      const initialTimeOverridden = motionRunInitialMoveTimeSec() !== null;
      const initialMoveTimeDisabled = initialTimeOverridden;
      const firstFrameInitial = initialMode === 'first_frame';
      const initialPositionDisabled = firstFrameInitial;
      const dynamixelGearFixed = isDynamixelMappingRow(row);
      const referencePositionValue = displayReferencePosition(row);
      const initialPositionValue = displayInitialPosition(row);
      const initialMoveTimeValue = displayInitialMoveTime(row);
      const gearRatioValue = mappingGearRatioValue(row);
      const referenceDisabledAttr = referenceDisabled ? ' disabled' : '';
      const initialMoveTimeDisabledAttr = initialMoveTimeDisabled
        ? ' disabled title="모션 동작 탭의 초기 이동 시간이 일괄 적용됩니다"'
        : '';
      const initialPositionDisabledAttr = initialPositionDisabled ? ' disabled' : '';
      const gearRatioDisabledAttr = dynamixelGearFixed ? ' disabled title="다이나믹셀은 감속비를 사용하지 않으며 1로 고정됩니다"' : '';
      return (
        `<tr data-mapping-index="${index}">
          <td><input class="motion-id-input mono" type="text" pattern="[1-9]\\d*-[1-9]\\d*" title="양의 정수-양의 정수 형식으로 입력하세요. 예: 1-1, 2-3" data-motion-mapping-field="motion_id" value="${displayText(row.motion_id)}" placeholder="예: 1-1" autocomplete="off" autocapitalize="off" spellcheck="false"></td>
          <td><input type="checkbox" data-motion-mapping-field="enabled" ${row.enabled ? 'checked' : ''}></td>
          <td>${motorSelectHtml(row)}</td>
          <td class="mapping-number-cell ${dynamixelGearFixed ? 'mapping-disabled-cell' : ''}"><input class="numeric-input mapping-number-input" type="number" min="0.0001" step="0.0001" data-motion-mapping-field="gear_ratio" value="${displayText(gearRatioValue)}"${gearRatioDisabledAttr}></td>
          <td class="mapping-number-cell ${referenceDisabled ? 'mapping-disabled-cell' : ''}"><input class="numeric-input mapping-number-input" type="number" step="0.001" data-motion-mapping-field="reference_position_deg" value="${displayText(referencePositionValue)}"${referenceDisabledAttr}></td>
          <td class="${referenceDisabled ? 'mapping-disabled-cell' : ''}">
            <label class="mapping-reference-toggle"><input type="checkbox" data-motion-mapping-field="reference_enabled"${row.reference_enabled !== false ? ' checked' : ''}>사용</label>
            <button class="mapping-mini-button" type="button" data-motion-mapping-action="capture_reference"${referenceDisabledAttr}>캡처</button>
          </td>
          <td class="mapping-number-cell"><input class="numeric-input mapping-number-input" type="number" step="0.001" data-motion-mapping-field="motion_lower_deg" value="${displayText(row.motion_lower_deg)}"></td>
          <td class="mapping-number-cell"><input class="numeric-input mapping-number-input" type="number" step="0.001" data-motion-mapping-field="motion_upper_deg" value="${displayText(row.motion_upper_deg)}"></td>
          <td>
            <select class="compact-select" data-motion-mapping-field="initial_mode">
              <option value="first_frame"${initialMode === 'first_frame' ? ' selected' : ''}>첫 프레임</option>
              <option value="manual"${initialMode === 'manual' ? ' selected' : ''}>수동</option>
            </select>
          </td>
          <td class="mapping-number-cell ${initialPositionDisabled ? 'mapping-disabled-cell' : ''}"><input class="numeric-input mapping-number-input" type="number" step="0.001" data-motion-mapping-field="initial_motion_position_deg" value="${displayText(initialPositionValue)}"${initialPositionDisabledAttr}></td>
          <td class="mapping-number-cell ${initialMoveTimeDisabled ? 'mapping-disabled-cell' : ''}"><input class="numeric-input mapping-number-input" type="number" min="0.001" step="0.001" data-motion-mapping-field="initial_move_time_sec" value="${displayText(initialMoveTimeValue)}"${initialMoveTimeDisabledAttr}></td>
          <td><input type="checkbox" data-motion-mapping-field="invert" ${row.invert ? 'checked' : ''}></td>
          <td class="mapping-number-cell"><input class="numeric-input mapping-number-input" type="number" step="0.001" data-motion-mapping-field="offset_deg" value="${displayText(row.offset_deg)}"></td>
          <td class="mapping-number-cell"><input class="numeric-input mapping-number-input" type="number" step="0.0001" data-motion-mapping-field="scale" value="${displayText(row.scale)}"></td>
          <td><span class="motion-state-pill ${status.className}">${displayText(status.text)}</span> <button class="mapping-mini-button" type="button" data-motion-mapping-action="delete">삭제</button></td>
        </tr>`
      );
    }).join('');
  }

  function renderUnusedMotors() {
    if (!el.motionMappingUnusedMotorRows) return;
    const usedAxes = new Set(
      mappingDraft.mappings
        .filter((row) => row.enabled && mappingTargetKey(row))
        .map((row) => mappingTargetKey(row)),
    );
    const unused = sortedRuntimeMotors().filter((motor) => (
      !usedAxes.has(motionMotorTargetKey(motor))
    ));
    if (!unused.length) {
      el.motionMappingUnusedMotorRows.innerHTML = emptyRow(5, '미사용 모터축이 없습니다');
      return;
    }
    el.motionMappingUnusedMotorRows.innerHTML = unused.map((motor) => (
      `<tr>
        <td class="mono">${formatInt(motor.controller_index)}</td>
        <td class="mono">${displayText(motorIdText(motor))}</td>
        <td>${displayText(({
          ac_servo: 'AC 서보', dynamixel: '다이나믹셀', cubemars: '큐브마스',
        })[normalizeMotorTypeKey(motor.motor_type, motor.motor_type_label)] || '확인 불가')}</td>
        <td>${displayText(motor.display_name || '-')}</td>
        <td>${displayText(motor.status_text || motor.state || '-')}</td>
      </tr>`
    )).join('');
  }

  function renderMappingValidation() {
    if (!el.motionMappingValidation) return;
    if (!mappingValidation) {
      el.motionMappingValidation.innerHTML = '<div class="empty">설정 검증을 누르면 결과가 표시됩니다</div>';
      return;
    }

    const errors = Array.isArray(mappingValidation.errors) ? mappingValidation.errors : [];
    const warnings = Array.isArray(mappingValidation.warnings) ? mappingValidation.warnings : [];
    const valid = Boolean(mappingValidation.valid);
    const issueItems = [
      ...errors.map((message) => `<li>오류: ${displayText(message)}</li>`),
      ...warnings.map((message) => `<li>주의: ${displayText(message)}</li>`),
    ].join('');
    const rows = Array.isArray(mappingDraft.mappings) ? mappingDraft.mappings : [];
    const tableRows = rows.map((row) => {
      const detail = mappingValidation.rows?.[String(row.motion_id)] || {};
      const status = detail.status === 'error'
        ? { text: '오류', className: 'bad' }
        : detail.status === 'warning'
          ? { text: '주의', className: 'warn' }
          : { text: '통과', className: 'ok' };
      const messages = Array.isArray(detail.messages) && detail.messages.length
        ? detail.messages.join(', ')
        : '-';
      const rangeText = (
        `${targetText(detail.motion_motor_target_min_deg)} ~ ${targetText(detail.motion_motor_target_max_deg)}`
      );
      const outputRangeText = (
        `${targetText(detail.motion_output_min_deg)} ~ ${targetText(detail.motion_output_max_deg)}`
      );
      const limitInfo = motorLimitInfo(row, detail);
      const referenceClass = exceedsMotorAxisAngleAlert(detail.reference_position_deg)
        ? 'validation-angle-alert'
        : '';
      const targetRangeClass = limitInfo.className === 'bad' ? 'validation-angle-alert' : '';
      const firstFrameText = Number.isFinite(Number(detail.first_frame_motor_target_deg))
        ? `${targetText(detail.first_frame_motion_position_deg)} -> ${targetText(detail.first_frame_output_deg)} -> ${targetText(detail.first_frame_motor_target_deg)}`
        : '첫 프레임 실행 시 계산';
      const initialText = row.initial_mode === 'manual'
        ? `${targetText(detail.initial_motion_position_deg)} -> ${targetText(detail.manual_initial_output_deg)} -> ${targetText(detail.manual_initial_motor_target_deg)}`
        : firstFrameText;
      return (
        `<tr>
          <td class="mono">${displayText(row.motion_id)}</td>
          <td><span class="motion-state-pill ${status.className}">${status.text}</span></td>
          <td>${displayText(messages)}</td>
          <td class="${referenceClass}">${targetText(detail.reference_position_deg)}</td>
          <td>${displayText(outputRangeText)}</td>
          <td>
            <div class="validation-range-cell">
              <span class="${targetRangeClass}">${displayText(rangeText)}</span>
              <span class="validation-limit-text">모터 리미트: ${displayText(limitInfo.rangeText)}</span>
            </div>
          </td>
          <td>${displayText(initialText)}</td>
        </tr>`
      );
    }).join('');

    el.motionMappingValidation.innerHTML = `
      <div class="motion-mapping-validation-summary">
        <span class="motion-state-pill ${valid ? 'ok' : 'bad'}">${valid ? '검증 통과' : '검증 오류'}</span>
        <span>오류 ${formatInt(errors.length)}</span>
        <span>주의 ${formatInt(warnings.length)}</span>
      </div>
      ${issueItems ? `<ul class="motion-mapping-validation-issues">${issueItems}</ul>` : ''}
      <table class="motion-mapping-validation-table">
        <thead>
          <tr>
            <th><span class="validation-head-label">모션 ID</span><span class="validation-head-unit">ID</span></th>
            <th><span class="validation-head-label">상태</span><span class="validation-head-unit">검증</span></th>
            <th><span class="validation-head-label">메시지</span><span class="validation-head-unit">-</span></th>
            <th><span class="validation-head-label">기준점</span><span class="validation-head-unit">모터 deg</span></th>
            <th><span class="validation-head-label">출력축 범위</span><span class="validation-head-unit">출력축 deg</span></th>
            <th><span class="validation-head-label">모터 목표 범위</span><span class="validation-head-unit">모터 deg / limit</span></th>
            <th><span class="validation-head-label">초기 위치 변환</span><span class="validation-head-unit">모션 → 출력축 → 모터 deg</span></th>
          </tr>
        </thead>
        <tbody>${tableRows || emptyRow(7, '검증할 모션축이 없습니다')}</tbody>
      </table>
    `;
  }

  function renderMappingPanel() {
    renderMappingSelect();
    renderMappingMotionFileSelect();
    if (el.motionMappingName && document.activeElement !== el.motionMappingName) {
      el.motionMappingName.value = mappingDraft.name || '';
    }
    if (el.deleteMotionMappingButton) el.deleteMotionMappingButton.disabled = !selectedMappingId || mappingLoading;
    if (el.saveMotionMappingButton) el.saveMotionMappingButton.disabled = mappingLoading;
    if (el.importMotionIdsButton) el.importMotionIdsButton.disabled = !mappingDraft.motion_file_id || mappingLoading;
    if (el.motionMappingSelect) el.motionMappingSelect.disabled = mappingLoading;
    if (el.refreshMotionMappingsButton) el.refreshMotionMappingsButton.disabled = mappingLoading;
    if (el.newMotionMappingButton) el.newMotionMappingButton.disabled = mappingLoading;
    if (el.addMotionIdButton) el.addMotionIdButton.disabled = mappingLoading;
    if (el.generateMotionIdsButton) el.generateMotionIdsButton.disabled = mappingLoading;
    if (el.resetMotionMappingButton) el.resetMotionMappingButton.disabled = mappingLoading;
    if (el.motionMappingName) el.motionMappingName.disabled = mappingLoading;
    if (el.motionMappingFileSelect) el.motionMappingFileSelect.disabled = mappingLoading;
    el.motionMappingRows?.closest('table')?.classList.toggle('mapping-loading', mappingLoading);
    renderMappingRows();
    renderMappingValidation();
    renderUnusedMotors();
    if (el.motionMappingRawText) {
      el.motionMappingRawText.textContent = mappingRawText || '매핑 파일을 선택하거나 저장하면 YAML 원본이 표시됩니다';
    }
    onWorkContextChange?.();
  }

  function renderRuntimeMappingState() {
    if (activeMotionPanel !== 'mapping') return;
    const activeElement = document.activeElement;
    if (
      activeElement &&
      (
        activeElement.closest?.('#motionMappingRows') ||
        activeElement === el.motionMappingName ||
        activeElement === el.motionMappingFileSelect ||
        activeElement === el.motionMappingSelect
      )
    ) {
      return;
    }
    renderMappingRows();
    renderUnusedMotors();
  }

  function renderRuntimeState() {
    const status = getLatestState()?.motion_run_status;
    if (status && Object.keys(status).length) {
      motionRunStatus = status;
      renderMotionRunPanel();
    }
    renderRuntimeMappingState();
  }

  function render() {
    renderMotionTabs();
    renderFileRows();
    renderSelectedFile();
    renderMappingPanel();
    renderMotionRunPanel();
    onWorkContextChange?.();
  }

  function mappingRowsFromMotionFile(file, previousRows = []) {
    const previousById = new Map(previousRows.map((row) => [String(row.motion_id), row]));
    const motionIds = Array.isArray(file?.analysis?.motion_ids) ? file.analysis.motion_ids : [];
    return motionIds.map((item) => {
      const motionId = String(item.motion_id);
      const previous = previousById.get(motionId);
      const previousMode = previous?.initial_mode || 'first_frame';
      const firstValue = numericOr(item.first_value, 0.0);
      return {
        motion_id: motionId,
        enabled: previous?.enabled ?? true,
        motor_ref: previous?.motor_ref ?? '',
        motor_axis: previous?.motor_axis ?? null,
        reference_enabled: previous?.reference_enabled ?? true,
        reference_position_deg: numericOr(previous?.reference_position_deg, 0.0),
        motion_lower_deg: numericOr(previous?.motion_lower_deg, -180.0),
        motion_upper_deg: numericOr(previous?.motion_upper_deg, 180.0),
        initial_mode: previousMode,
        initial_motion_position_deg: previousMode === 'first_frame'
          ? firstValue
          : numericOr(previous?.initial_motion_position_deg, firstValue),
        initial_move_time_sec: numericOr(previous?.initial_move_time_sec, 5.0),
        invert: previous?.invert ?? false,
        offset_deg: numericOr(previous?.offset_deg, 0.0),
        scale: numericOr(previous?.scale, 1.0),
        gear_ratio: numericOr(previous?.gear_ratio, 1.0),
      };
    });
  }

  function newMotionAxisRow(motionId, motorAxis = null) {
    return defaultMotionAxisRow(motionId, motorAxis);
  }

  async function addMotionId() {
    const entered = await showPrompt('추가할 모션 ID를 입력하세요', {
      title: 'Motion ID 추가',
      defaultValue: '1-1',
      confirmLabel: '추가',
    });
    const motionId = String(entered || '').trim();
    if (!motionId) return;
    if (mappingDraft.mappings.some((row) => String(row.motion_id) === motionId)) {
      setMappingMessage(`이미 존재하는 모션 ID입니다: ${motionId}`);
      return;
    }
    mappingDraft.mappings.push(newMotionAxisRow(motionId));
    mappingRawText = '';
    mappingValidation = null;
    markMappingDirty();
    setMappingMessage(`모션 ID ${motionId} 추가 완료`);
    renderMappingPanel();
  }

  function generateMotionIdsFromMotors() {
    const motors = sortedRuntimeMotors();
    if (!motors.length) {
      setMappingMessage('현재 프로젝트에 등록된 모터축이 없습니다. 모터축 설정을 먼저 저장하세요');
      return;
    }
    upgradeLegacyMappingRefs();
    mappingDraft.mappings = buildGeneratedMotionAxisRows(motors, mappingDraft.mappings);
    mappingRawText = '';
    mappingValidation = null;
    markMappingDirty();
    setMappingMessage(`${motors.length}개 모터축 행을 만들었습니다. 모션 ID를 직접 확인·수정하세요`);
    renderMappingPanel();
  }

  async function ensureMappingMotionFileDetail(fileId) {
    if (!fileId) return null;
    if (mappingMotionFileDetail?.id === fileId) return mappingMotionFileDetail;
    if (selectedFile?.id === fileId) {
      mappingMotionFileDetail = selectedFile;
      return mappingMotionFileDetail;
    }
    const payload = await fetchMotionFile(fileId);
    mappingMotionFileDetail = payload.file || null;
    files = Array.isArray(payload.files) ? payload.files : files;
    return mappingMotionFileDetail;
  }

  function validateMappingDraft() {
    upgradeLegacyMappingRefs();
    if (!mappingDraft.name?.trim()) return '매핑 이름이 필요합니다';
    const rows = Array.isArray(mappingDraft.mappings) ? mappingDraft.mappings : [];
    if (!rows.length) return '모션 ID를 먼저 추가하세요';
    const invalidMotionId = rows.find((row) => !MOTION_ID_PATTERN.test(String(row.motion_id || '').trim()));
    if (invalidMotionId) return `모션 ID는 양의 정수-양의 정수 형식이어야 합니다: ${invalidMotionId.motion_id || '(비어 있음)'}`;
    const motionIdCounts = rows.reduce((counts, row) => {
      const motionId = String(row.motion_id || '').trim();
      counts[motionId] = (counts[motionId] || 0) + 1;
      return counts;
    }, {});
    const duplicateMotionId = Object.entries(motionIdCounts).find(([, count]) => count > 1);
    if (duplicateMotionId) return `모션 ID가 중복되었습니다: ${duplicateMotionId[0]}`;
    const duplicateCounts = mappingDuplicateAxisCounts();
    const duplicateAxis = Object.entries(duplicateCounts).find(([, count]) => count > 1);
    if (duplicateAxis) return `동일한 모터 ID가 중복 사용되었습니다: ${duplicateAxis[0]}`;
    const enabledWithoutMotor = rows.find((row) => row.enabled && !mappingTargetKey(row));
    if (enabledWithoutMotor) return `활성화된 모션 ID에 모터 ID가 없습니다: ${enabledWithoutMotor.motion_id}`;
    return '';
  }

  async function loadMappings(selectMappingId = selectedMappingId) {
    const loadToken = ++mappingLoadToken;
    mappingLoading = true;
    setMappingMessage('매핑 목록 불러오는 중');
    renderMappingPanel();
    try {
      const payload = await fetchMotionMappings();
      if (loadToken !== mappingLoadToken) return;
      mappingFiles = Array.isArray(payload.files) ? payload.files : [];
      if (selectMappingId && mappingFiles.some((file) => file.id === selectMappingId)) {
        await selectMapping(selectMappingId);
        return;
      }
      if (!selectedMappingId && mappingFiles.length) {
        await selectMapping(mappingFiles[0].id);
        return;
      }
      if (selectedMappingId && !mappingFiles.some((file) => file.id === selectedMappingId)) {
        selectedMappingId = null;
        mappingRevision = '';
        mappingDraft = emptyMappingDraft();
        registeredMotionFileIdValue = '';
        mappingRawText = '';
        mappingValidation = null;
        mappingDirty = false;
        mappingRevisionConflict = false;
      }
      setMappingMessage(payload.message || '매핑 목록 갱신 완료');
    } catch (error) {
      if (loadToken !== mappingLoadToken || error?.staleProjectResponse) return;
      setMappingMessage(`매핑 목록 실패: ${error?.message || error}`);
    } finally {
      if (loadToken === mappingLoadToken) {
        mappingLoading = false;
        renderMappingPanel();
      }
    }
  }

  async function selectMapping(fileId) {
    const requestedMappingId = fileId || null;
    const loadToken = ++mappingLoadToken;
    if (!requestedMappingId) {
      selectedMappingId = null;
      mappingDraft = emptyMappingDraft();
      registeredMotionFileIdValue = '';
      mappingRawText = '';
      mappingValidation = null;
      mappingMotionFileDetail = null;
      mappingDirty = false;
      mappingRevisionConflict = false;
      mappingRevision = '';
      renderMappingPanel();
      return;
    }
    mappingLoading = true;
    setMappingMessage('매핑 파일 불러오는 중');
    renderMappingPanel();
    try {
      const payload = await fetchMotionMapping(requestedMappingId);
      if (loadToken !== mappingLoadToken) return;
      const loadedDraft = payload.mapping || emptyMappingDraft();
      let loadedMotionFileDetail = null;
      let loadedMotionFiles = files;
      if (loadedDraft.motion_file_id) {
        if (selectedFile?.id === loadedDraft.motion_file_id) {
          loadedMotionFileDetail = selectedFile;
        } else {
          const motionPayload = await fetchMotionFile(loadedDraft.motion_file_id);
          if (loadToken !== mappingLoadToken) return;
          loadedMotionFileDetail = motionPayload.file || null;
          loadedMotionFiles = Array.isArray(motionPayload.files)
            ? motionPayload.files
            : loadedMotionFiles;
        }
      }
      files = loadedMotionFiles;
      mappingFiles = Array.isArray(payload.files) ? payload.files : mappingFiles;
      mappingDraft = loadedDraft;
      registeredMotionFileIdValue = registeredMotionFileId(loadedDraft);
      upgradeLegacyMappingRefs();
      selectedMappingId = payload.file?.id || mappingDraft.file_id || requestedMappingId;
      mappingRevision = mappingFileRevision(payload.file);
      mappingRawText = payload.content || '';
      mappingValidation = payload.validation || null;
      mappingMotionFileDetail = loadedMotionFileDetail;
      normalizeDynamixelGearRatios();
      mappingDirty = false;
      mappingRevisionConflict = false;
      const midiWarning = String(payload.midi_banks_warning || '').trim();
      const mappingFileName = payload.file?.filename || payload.file?.id || selectedMappingId || '-';
      const motionFileName = mappingDraft.motion_file_id || '-';
      setMappingMessage(midiWarning
        ? `모션축 설정: ${mappingFileName} · 모션 데이터: ${motionFileName} · MIDI 뱅크: ${midiWarning}`
        : `모션축 설정: ${mappingFileName} · 모션 데이터: ${motionFileName} · MIDI 뱅크 적용 완료`);
    } catch (error) {
      if (loadToken !== mappingLoadToken || error?.staleProjectResponse) return;
      setMappingMessage(`매핑 파일 실패: ${error?.message || error}`);
    } finally {
      if (loadToken === mappingLoadToken) {
        mappingLoading = false;
        renderMappingPanel();
      }
    }
  }

  async function newMappingDraft() {
    const hasExistingDraft = Boolean(
      selectedMappingId
      || mappingDraft.motion_file_id
      || mappingDraft.mappings?.length,
    );
    if (hasExistingDraft && !await confirmDiscardMappingChanges('새 매칭을 작성')) return;
    const enteredName = String(el.motionMappingName?.value || '').trim();
    if (!enteredName) {
      setMappingMessage('매핑 이름을 먼저 입력한 뒤 새 매칭 작성을 누르세요');
      el.motionMappingName?.focus();
      return;
    }
    const currentFile = selectedMappingFile();
    const currentName = String(currentFile?.name || '').trim();
    if (selectedMappingId && currentName && enteredName === currentName) {
      setMappingMessage('기존 매핑과 다른 새 매핑 이름을 입력하세요');
      el.motionMappingName?.focus();
      el.motionMappingName?.select();
      return;
    }
    const baseFile = selectedFile || null;
    selectedMappingId = null;
    registeredMotionFileIdValue = '';
    mappingRevision = '';
    mappingRawText = '';
    mappingValidation = null;
    mappingMotionFileDetail = baseFile;
    mappingDraft = {
      ...emptyMappingDraft(),
      name: enteredName,
      motion_file_id: baseFile?.id || '',
      mappings: baseFile ? mappingRowsFromMotionFile(baseFile) : [],
    };
    mappingDirty = true;
    mappingRevisionConflict = false;
    forceMappingNameInput(enteredName);
    setMappingMessage(`새 매핑 작성 중: ${enteredName} · 아직 파일로 저장되지 않음`);
    renderMappingPanel();
  }

  async function importMotionIds() {
    if (!mappingDraft.motion_file_id) {
      setMappingMessage('모션 파일을 먼저 선택하세요');
      return;
    }
    mappingLoading = true;
    setMappingMessage('모션 ID 반영 중');
    renderMappingPanel();
    try {
      const detail = await ensureMappingMotionFileDetail(mappingDraft.motion_file_id);
      if (!detail?.analysis?.motion_ids?.length) {
        setMappingMessage('선택 파일에 모션 ID가 없습니다');
        return;
      }
      mappingDraft.mappings = mappingRowsFromMotionFile(detail, mappingDraft.mappings);
      normalizeDynamixelGearRatios();
      if (!mappingDraft.name) {
        mappingDraft.name = `${detail.filename.replace(/\.json$/i, '')}_mapping`;
      }
      mappingRawText = '';
      mappingValidation = null;
      markMappingDirty();
      setMappingMessage(`모션 ID ${formatInt(mappingDraft.mappings.length)}개 반영 완료`);
    } catch (error) {
      setMappingMessage(`모션 ID 반영 실패: ${error?.message || error}`);
    } finally {
      mappingLoading = false;
      renderMappingPanel();
    }
  }

  async function registerSelectedMotionFile() {
    if (!selectedFile || !selectedMappingId) {
      setMessage('재생 등록할 모션 파일과 저장된 모션축 설정을 먼저 선택하세요');
      return;
    }
    if (mappingDirty) {
      setMessage('모션축 설정의 편집 내용을 먼저 저장하거나 되돌린 뒤 재생 등록하세요');
      return;
    }
    const analysis = analysisOf(selectedFile);
    if (analysis.valid === false) {
      setMessage('검증에 실패한 모션 파일은 재생 등록할 수 없습니다');
      return;
    }
    const confirmed = await showConfirm(
      `${selectedFile.filename} 파일을 현재 모션축 설정의 재생 파일로 등록합니다.\n`
      + `${selectedMappingId}`,
      { title: '모션 파일 재생 등록', confirmLabel: '재생 등록', tone: 'primary' },
    );
    if (!confirmed) return;
    mappingDraft.motion_file_id = selectedFile.id;
    mappingMotionFileDetail = selectedFile;
    mappingRawText = '';
    mappingValidation = null;
    markMappingDirty();
    setMappingMessage(`재생 등록 저장 중: ${selectedFile.filename}`);
    await saveCurrentMapping();
    render();
  }

  async function unregisterSelectedMotionFile() {
    if (!selectedFile || !selectedMappingId || selectedFile.id !== registeredMotionFileIdValue) {
      setMessage('현재 재생 등록된 모션 파일을 선택하세요');
      return;
    }
    if (mappingDirty) {
      setMessage('모션축 설정의 편집 내용을 먼저 저장하거나 되돌린 뒤 재생 등록을 해제하세요');
      return;
    }
    const registeredFilename = selectedFile.filename;
    const confirmed = await showConfirm(
      `${registeredFilename} 파일의 재생 등록을 해제합니다.\n`
      + '파일은 삭제되지 않으며, 다시 등록하기 전까지 모션 실행은 차단됩니다.',
      { title: '모션 파일 재생 등록 해제', confirmLabel: '등록 해제', tone: 'danger' },
    );
    if (!confirmed) return;
    mappingDraft.motion_file_id = '';
    mappingMotionFileDetail = null;
    mappingRawText = '';
    mappingValidation = null;
    markMappingDirty();
    setMappingMessage(`재생 등록 해제 저장 중: ${registeredFilename}`);
    await saveCurrentMapping();
    if (!registeredMotionFileIdValue) {
      motionRunStatus = null;
      motionRunLastResult = null;
      setMotionRunMessage('재생 등록된 모션 파일이 없습니다');
    }
    render();
  }

  async function saveCurrentMapping() {
    if (mappingRevisionConflict) {
      await resolveMappingRevisionConflict(
        '최신 저장 내용을 다시 불러와야 저장할 수 있습니다.',
      );
      return;
    }
    const draftError = validateMappingDraft();
    if (draftError) {
      mappingValidation = null;
      setMappingMessage(`매핑 저장 중단: ${draftError}`);
      renderMappingPanel();
      return;
    }
    mappingLoading = true;
    setMappingMessage('매핑 저장 중');
    normalizeDynamixelGearRatios();
    upgradeLegacyMappingRefs();
    renderMappingPanel();
    try {
      const validated = await validateMotionMapping({
        file_id: selectedMappingId || '',
        mapping: mappingDraft,
      });
      mappingDraft = validated.mapping || mappingDraft;
      mappingValidation = validated.validation || null;
      if (validated.success === false || mappingValidation?.valid === false) {
        setMappingMessage(
          `검증 실패 · 저장하지 않음: ${validated.message || '설정 오류를 확인하세요'}`,
        );
        return;
      }
      const payload = await saveMotionMapping({
        file_id: selectedMappingId || '',
        base_mapping_revision: mappingRevision,
        mapping: mappingDraft,
      });
      mappingValidation = payload.validation || null;
      if (payload.success === false) {
        mappingDraft = payload.mapping || mappingDraft;
        if (isMappingRevisionConflict(payload.message)) {
          await resolveMappingRevisionConflict(payload.message);
          return;
        }
        setMappingMessage(`매핑 저장 실패: ${payload.message || '검증 실패'}`);
        return;
      }
      mappingFiles = Array.isArray(payload.files) ? payload.files : mappingFiles;
      mappingDraft = payload.mapping || mappingDraft;
      registeredMotionFileIdValue = registeredMotionFileId(mappingDraft);
      selectedMappingId = payload.file?.id || mappingDraft.file_id || selectedMappingId;
      mappingRevision = mappingFileRevision(payload.file);
      mappingRawText = payload.content || '';
      mappingDirty = false;
      mappingRevisionConflict = false;
      setMappingMessage(payload.message || (
        payload.runtime_applied
          ? `모션축 설정 저장 완료: ${selectedMappingId} · MIDI 적용 완료`
          : `모션축 설정 저장 완료: ${selectedMappingId}`
      ));
      await onProjectFilesChange?.();
    } catch (error) {
      if (isMappingRevisionConflict(error?.message || error)) {
        await resolveMappingRevisionConflict(error?.message || error);
        return;
      }
      setMappingMessage(`매핑 저장 실패: ${error?.message || error}`);
    } finally {
      mappingLoading = false;
      renderMappingPanel();
    }
  }

  async function resetCurrentMapping() {
    const label = selectedMappingId || mappingDraft.name || '현재 모션축 설정';
    const confirmed = await showConfirm(
      `${label}의 저장하지 않은 편집 내용을 버립니다.\n`
      + '저장된 파일이 있으면 디스크에서 다시 불러옵니다.',
      { title: '모션축 설정 되돌리기', confirmLabel: '편집 내용 버리기', tone: 'warning' },
    );
    if (!confirmed) return;
    if (selectedMappingId) {
      await selectMapping(selectedMappingId);
      return;
    }
    mappingDraft = emptyMappingDraft();
    mappingMotionFileDetail = null;
    mappingRawText = '';
    mappingValidation = null;
    mappingDirty = false;
    mappingRevisionConflict = false;
    forceMappingNameInput('');
    setMappingMessage(`${label}의 저장하지 않은 편집 내용을 버렸습니다`);
    renderMappingPanel();
  }

  async function refreshMappingAfterReconnect() {
    if (!selectedMappingId) return;
    if (!mappingDirty) {
      await selectMapping(selectedMappingId);
      return;
    }
    try {
      const payload = await fetchMotionMapping(selectedMappingId);
      const currentRevision = mappingFileRevision(payload.file);
      if (currentRevision && currentRevision !== mappingRevision) {
        mappingRevisionConflict = true;
        setMappingMessage(
          '프로그램 재연결 후 저장된 설정 변경을 확인했습니다 · '
          + '편집 내용은 유지 중이며 저장 전 저장된 내용을 다시 불러와야 합니다',
        );
      }
    } catch (error) {
      if (!error?.staleProjectResponse) {
        setMappingMessage(`재연결 후 모션축 설정 확인 실패: ${error?.message || error}`);
      }
    }
  }

  async function deleteCurrentMapping() {
    if (!selectedMappingId) return;
    const confirmed = await showConfirm(
      `선택한 모션축 설정 파일을 현재 프로젝트 휴지통으로 이동합니다.\n${selectedMappingId}`,
      { title: '모션축 설정 파일 삭제', confirmLabel: '휴지통으로 이동', tone: 'danger' },
    );
    if (!confirmed) return;
    mappingLoading = true;
    setMappingMessage('매핑 삭제 중');
    renderMappingPanel();
    try {
      const payload = await deleteMotionMapping(selectedMappingId);
      mappingFiles = Array.isArray(payload.files) ? payload.files : [];
      selectedMappingId = null;
      mappingRevision = '';
      mappingDraft = emptyMappingDraft();
      registeredMotionFileIdValue = '';
      mappingRawText = '';
      mappingValidation = null;
      mappingDirty = false;
      mappingRevisionConflict = false;
      setMappingMessage(payload.message || '매핑 삭제 완료');
      await onProjectFilesChange?.();
    } catch (error) {
      setMappingMessage(`매핑 삭제 실패: ${error?.message || error}`);
    } finally {
      mappingLoading = false;
      renderMappingPanel();
    }
  }

  function updateMappingRow(rowIndex, field, value, checked = false) {
    const row = mappingDraft.mappings[Number(rowIndex)];
    if (!row) return;
    if (field === 'motion_id') {
      row.motion_id = String(value || '').trim();
    } else if (field === 'enabled' || field === 'invert' || field === 'reference_enabled') {
      row[field] = Boolean(checked);
      if (field === 'reference_enabled' && !row.reference_enabled) {
        row.reference_position_deg = 0.0;
      }
    } else if (field === 'motor_ref') {
      const selectionValue = String(value || '');
      const motor = motorForSelectionValue(selectionValue);
      row.motor_ref = selectionValue.toLowerCase().startsWith('axis:')
        ? ''
        : selectionValue;
      row.motor_axis = motor ? Number(motor.controller_index) : null;
      if (isDynamixelMappingRow(row)) {
        row.gear_ratio = 1.0;
      }
    } else if (field === 'initial_mode') {
      row.initial_mode = value === 'manual' ? 'manual' : 'first_frame';
      if (row.initial_mode === 'first_frame') {
        const firstValue = firstMotionValueFor(row.motion_id);
        if (firstValue !== null) row.initial_motion_position_deg = firstValue;
      }
    } else if (
      field === 'offset_deg'
      || field === 'scale'
      || field === 'gear_ratio'
      || field === 'reference_position_deg'
      || field === 'motion_lower_deg'
      || field === 'motion_upper_deg'
      || field === 'initial_motion_position_deg'
      || field === 'initial_move_time_sec'
    ) {
      const number = Number(value);
      const fallback = field === 'scale' || field === 'gear_ratio' ? 1.0 : field === 'initial_move_time_sec' ? 5.0 : 0.0;
      row[field] = Number.isFinite(number) ? number : fallback;
      if (field === 'gear_ratio' && isDynamixelMappingRow(row)) {
        row.gear_ratio = 1.0;
      }
    }
    mappingRawText = '';
    mappingValidation = null;
    markMappingDirty();
    renderMappingPanel();
  }

  function captureReferencePosition(rowIndex) {
    const row = mappingDraft.mappings[Number(rowIndex)];
    if (!row) return;
    const motionId = String(row.motion_id || '');
    const scope = getLatestState()?.project_scope || {};
    if (scope.runtime_matches_selected !== true || scope.motor_config_applied !== true) {
      setMappingMessage(`현재 프로젝트 모터축 설정을 적용·재시작한 뒤 기준점을 캡처하세요: 모션 ID ${motionId}`);
      return;
    }
    const motor = motorForMapping(row);
    const position = motorPositionDeg(motor);
    if (position === null) {
      setMappingMessage(`현재 위치를 읽을 수 없습니다: 모션 ID ${motionId}`);
      return;
    }
    row.reference_position_deg = position;
    row.reference_enabled = true;
    mappingRawText = '';
    mappingValidation = null;
    markMappingDirty();
    setMappingMessage(`모션 ID ${motionId} 기준점 캡처: ${formatNumber(position, 3)} deg`);
    renderMappingPanel();
  }

  function resetProjectState() {
    files = [];
    selectedFileId = null;
    selectedFile = null;
    mappingFiles = [];
    selectedMappingId = null;
    mappingRevision = '';
    mappingDraft = emptyMappingDraft();
    registeredMotionFileIdValue = '';
    mappingRawText = '';
    mappingValidation = null;
    mappingMotionFileDetail = null;
    mappingDirty = false;
    mappingRevisionConflict = false;
    fileLoadToken += 1;
    mappingLoadToken += 1;
    forceMappingNameInput('');
    motionRunStatus = null;
    motionRunLastResult = null;
    motionFileGraphFileId = '';
    motionRunGraphFileId = '';
    motionFileGraphHiddenIds.clear();
    motionRunGraphHiddenIds.clear();
    setMessage('현재 프로젝트 모션 파일을 불러오세요');
    setMappingMessage('현재 프로젝트 모션축 설정을 불러오세요');
    setMotionRunMessage('현재 프로젝트 모션을 선택하세요');
    render();
    renderMappingPanel();
    renderMotionRunPanel();
  }

  async function loadFiles(selectFileId = selectedFileId) {
    const loadToken = ++fileLoadToken;
    loading = true;
    setMessage('파일 목록 불러오는 중');
    render();
    try {
      const payload = await fetchMotionFiles();
      if (loadToken !== fileLoadToken) return;
      files = Array.isArray(payload.files) ? payload.files : [];
      if (selectFileId && files.some((file) => file.id === selectFileId)) {
        await selectFile(selectFileId, loadToken);
        return;
      }
      if (selectedFileId && !files.some((file) => file.id === selectedFileId)) {
        selectedFileId = null;
        selectedFile = null;
      }
      setMessage(payload.message || '파일 목록 갱신 완료');
    } catch (error) {
      if (loadToken !== fileLoadToken || error?.staleProjectResponse) return;
      setMessage(`파일 목록 실패: ${error?.message || error}`);
    } finally {
      if (loadToken !== fileLoadToken) return;
      loading = false;
      render();
    }
  }

  async function selectFile(fileId, requestToken = null) {
    const loadToken = requestToken ?? ++fileLoadToken;
    if (loadToken !== fileLoadToken) return;
    selectedFileId = fileId;
    loading = true;
    setMessage('파일 상세 불러오는 중');
    render();
    try {
      const payload = await fetchMotionFile(fileId);
      if (loadToken !== fileLoadToken) return;
      selectedFile = payload.file || null;
      files = Array.isArray(payload.files) ? payload.files : files;
      setMessage(payload.message || '파일 상세 갱신 완료');
    } catch (error) {
      if (loadToken !== fileLoadToken || error?.staleProjectResponse) return;
      selectedFile = null;
      setMessage(`파일 상세 실패: ${error?.message || error}`);
    } finally {
      if (loadToken !== fileLoadToken) return;
      loading = false;
      render();
    }
  }

  async function exportSelectedFileToStudio() {
    const file = selectedFile;
    if (!file) {
      await showAlert(
        '스튜디오로 내보낼 모션 파일을 먼저 선택하세요.',
        { title: '스튜디오 내보내기', confirmLabel: '확인', tone: 'warning' },
      );
      return;
    }
    loading = true;
    setMessage(`${file.filename} 스튜디오 내보내기 중`);
    render();
    try {
      const result = await onExportMotionFileToStudio(file.id);
      if (!result || result.success === false) {
        throw new Error(result?.message || '스튜디오가 모션 파일을 받지 못했습니다');
      }
      const layers = Array.isArray(result.project?.layers)
        ? result.project.layers
        : (result.project_patch?.upsert_layers || []);
      const exportedLayer = [...layers].reverse().find(
        (layer) => layer?.source_motion_file_id === file.id,
      );
      const layerName = String(
        exportedLayer?.name || file.filename.replace(/\.json$/i, ''),
      );
      setMessage(`스튜디오 내보내기 완료: ${file.filename} → ${layerName}`);
      await onProjectFilesChange?.();
      await showAlert(
        `모션 파일을 스튜디오의 독립 레이어로 내보냈습니다.\n`
        + `파일 · ${file.filename}\n레이어 · ${layerName}`,
        { title: '스튜디오 내보내기 완료', confirmLabel: '확인', tone: 'info' },
      );
    } catch (error) {
      const message = error?.message || String(error);
      setMessage(`스튜디오 내보내기 실패: ${message}`);
      await showAlert(
        `모션 파일을 스튜디오로 내보내지 못했습니다.\n원인 · ${message}`,
        { title: '스튜디오 내보내기 실패', confirmLabel: '확인', tone: 'danger' },
      );
    } finally {
      loading = false;
      render();
    }
  }

  async function showMotionFileDeleteFailure(message) {
    await showAlert(
      String(message || '모션 파일을 삭제할 수 없습니다'),
      { title: '모션 파일 삭제 불가', confirmLabel: '확인', tone: 'warning' },
    );
  }

  async function deleteSelectedFile() {
    if (!selectedFileId) return;
    if (selectedFileId === registeredMotionFileIdValue) {
      await showMotionFileDeleteFailure(
        '재생 등록된 모션 파일은 삭제할 수 없습니다.\n'
        + '먼저 재생 등록을 해제한 뒤 다시 삭제하세요.',
      );
      return;
    }
    const confirmed = await showConfirm(
      `선택한 모션 파일을 삭제합니다.\n${selectedFileId}`,
      { title: '모션 파일 삭제', confirmLabel: '삭제', tone: 'danger' },
    );
    if (!confirmed) return;
    loading = true;
    setMessage('파일 삭제 중');
    render();
    try {
      const payload = await deleteMotionFile(selectedFileId);
      files = Array.isArray(payload.files) ? payload.files : [];
      if (payload.success === false) {
        const message = payload.message || '모션 파일을 삭제할 수 없습니다';
        setMessage(`삭제 실패: ${message}`);
        await showMotionFileDeleteFailure(message);
        return;
      }
      selectedFileId = null;
      selectedFile = null;
      setMessage(payload.message || '파일 삭제 완료');
      try {
        await onProjectFilesChange?.();
      } catch (refreshError) {
        const refreshMessage = refreshError?.message || String(refreshError);
        setMessage(`파일 삭제 완료 · 프로젝트 목록 갱신 실패: ${refreshMessage}`);
        await showAlert(
          `모션 파일은 삭제됐지만 프로젝트 목록을 갱신하지 못했습니다.\n${refreshMessage}`,
          { title: '프로젝트 목록 갱신 필요', confirmLabel: '확인', tone: 'warning' },
        );
      }
    } catch (error) {
      const message = error?.message || String(error);
      setMessage(`삭제 실패: ${message}`);
      await showMotionFileDeleteFailure(message);
    } finally {
      loading = false;
      render();
    }
  }

  async function refreshMotionRunStatus() {
    motionRunLoading = true;
    renderMotionRunPanel();
    try {
      const payload = await fetchMotionRunStatus();
      motionRunStatus = payload.status || motionRunStatus || null;
      motionRunLastResult = payload;
      setMotionRunMessage(payload.message || '모션 동작 상태 갱신 완료');
    } catch (error) {
      setMotionRunMessage(`상태 갱신 실패: ${error?.message || error}`);
    } finally {
      motionRunLoading = false;
      renderMotionRunPanel();
    }
  }

  async function checkCurrentMotionRun() {
    motionRunLoading = true;
    setMotionRunMessage('실행 준비 검사 중');
    renderMotionRunPanel();
    try {
      await ensureMotionRunMotionFileDetail();
      const payload = await checkMotionRun(motionRunPayload());
      motionRunStatus = payload.status || motionRunStatus || null;
      motionRunLastResult = payload;
      setMotionRunMessage(payload.message || (payload.success ? '실행 준비 검사 완료' : '실행 준비 검사 실패'));
    } catch (error) {
      setMotionRunMessage(`실행 준비 검사 실패: ${error?.message || error}`);
    } finally {
      motionRunLoading = false;
      renderMotionRunPanel();
    }
  }

  async function initializeCurrentMotionRun() {
    const hasMotionFile = Boolean(motionRunPayload().motion_file_id);
    const confirmed = await showConfirm(
      hasMotionFile
        ? '매핑된 축을 초기 위치로 이동합니다.'
        : '모션 파일이 없습니다.\n\n첫 프레임 방식 축은 모션 0°로 이동합니다.\n수동 방식 축은 설정한 초기위치로 이동합니다.\n계속할까요?',
      { title: '초기 위치 이동', confirmLabel: '이동 시작', tone: 'warning' },
    );
    if (!confirmed) return;
    motionRunLoading = true;
    setMotionRunMessage('초기 위치 이동 요청 중');
    renderMotionRunPanel();
    try {
      await ensureMotionRunMotionFileDetail();
      const payload = await initializeMotionRun(motionRunPayload());
      motionRunStatus = payload.status || motionRunStatus || null;
      motionRunLastResult = payload;
      setMotionRunMessage(payload.message || (payload.success ? '초기 위치 이동 시작' : '초기 위치 이동 실패'));
      if (payload.success === false) {
        await showMotionRunFailure(payload.message, '초기 위치 이동 실패');
      }
    } catch (error) {
      const message = error?.message || String(error);
      setMotionRunMessage(`초기 위치 이동 실패: ${message}`);
      await showMotionRunFailure(message, '초기 위치 이동 실패');
    } finally {
      motionRunLoading = false;
      renderMotionRunPanel();
    }
  }

  async function startCurrentMotionRun(runMode = 'once') {
    const continuous = runMode === 'continuous';
    const confirmed = await showConfirm(
      continuous
        ? '모션축 설정의 전체 활성 축을 초기 위치로 이동한 뒤 연속 모션을 시작합니다.\n정지 버튼을 누를 때까지 모션 파일을 반복합니다.'
        : '모션축 설정의 전체 활성 축을 초기 위치로 이동한 뒤 현재 모션 파일을 1회 실행합니다.',
      {
        title: continuous ? '연속 모션 시작' : '모션 1회 시작',
        confirmLabel: '모션 시작',
        tone: 'warning',
      },
    );
    if (!confirmed) return;
    motionRunLoading = true;
    setMotionRunMessage('모션 시작 요청 중');
    renderMotionRunPanel();
    try {
      await ensureMotionRunMotionFileDetail();
      const payload = await startMotionRun({ ...motionRunPayload(), run_mode: runMode });
      motionRunStatus = payload.status || motionRunStatus || null;
      motionRunLastResult = payload;
      setMotionRunMessage(payload.message || (payload.success ? '모션 실행 시작' : '모션 실행 실패'));
      if (payload.success === false) {
        await showMotionRunFailure(payload.message, '모션 실행 실패');
      }
    } catch (error) {
      const message = error?.message || String(error);
      setMotionRunMessage(`모션 실행 실패: ${message}`);
      await showMotionRunFailure(message, '모션 실행 실패');
    } finally {
      motionRunLoading = false;
      renderMotionRunPanel();
    }
  }

  async function stopCurrentMotionRun() {
    motionRunLoading = true;
    setMotionRunMessage('정지 요청 중');
    renderMotionRunPanel();
    try {
      const payload = await stopMotionRun();
      motionRunStatus = payload.status || motionRunStatus || null;
      motionRunLastResult = payload;
      setMotionRunMessage(payload.message || '정지 요청 완료');
    } catch (error) {
      setMotionRunMessage(`정지 요청 실패: ${error?.message || error}`);
    } finally {
      motionRunLoading = false;
      renderMotionRunPanel();
    }
  }

  async function stopCurrentMotionRunAfterCycle() {
    motionRunLoading = true;
    setMotionRunMessage('현재 회차 후 정지 대기 중');
    renderMotionRunPanel();
    try {
      const payload = await stopMotionRunAfterCycle();
      motionRunStatus = payload.status || motionRunStatus || null;
      motionRunLastResult = payload;
      setMotionRunMessage(payload.message || '회차 후 정지 대기 중');
    } catch (error) {
      setMotionRunMessage(`정지 요청 실패: ${error?.message || error}`);
    } finally {
      motionRunLoading = false;
      renderMotionRunPanel();
    }
  }

  function motionAutomationSettings(enabled = true) {
    const repeatMode = String(
      el.motionAutomationRepeatMode?.value
      || motionRunStatus?.automation?.repeat_mode
      || 'direct',
    );
    const dwellSec = Number(el.motionAutomationDwellSec?.value);
    return {
      enabled,
      repeat_mode: repeatMode,
      dwell_sec: Number.isFinite(dwellSec) && dwellSec >= 0 ? dwellSec : 0,
    };
  }

  async function saveMotionAutomation(enabled = true) {
    motionRunLoading = true;
    renderMotionRunPanel();
    try {
      const payload = enabled
        ? await configureMotionAutomation(motionAutomationSettings(true))
        : await disableMotionAutomation();
      motionRunStatus = payload.status || motionRunStatus || null;
      motionRunLastResult = payload;
      if (payload.success === false) {
        await showAlert(payload.message || '자동 반복 설정 실패', {
          title: '자동 반복 설정',
          tone: 'danger',
        });
      }
    } catch (error) {
      await showAlert(error?.message || String(error), {
        title: '자동 반복 설정 실패',
        tone: 'danger',
      });
    } finally {
      motionRunLoading = false;
      renderMotionRunPanel();
    }
  }

  async function startCurrentMotionAutomation() {
    const confirmed = await showConfirm(
      '등록된 모션 파일을 검사하고 전체 활성 축을 초기 위치로 이동한 뒤 자동 반복을 시작합니다.',
      {
        title: '자동 반복 시작',
        confirmLabel: '시작',
        tone: 'warning',
      },
    );
    if (!confirmed) return;
    motionRunLoading = true;
    renderMotionRunPanel();
    try {
      await ensureMotionRunMotionFileDetail();
      const payload = await startMotionAutomation(motionRunPayload());
      motionRunStatus = payload.status || motionRunStatus || null;
      motionRunLastResult = payload;
      if (payload.success === false) {
        await showMotionRunFailure(payload.message, '자동 반복 시작 실패');
      }
    } catch (error) {
      await showMotionRunFailure(error?.message || String(error), '자동 반복 시작 실패');
    } finally {
      motionRunLoading = false;
      renderMotionRunPanel();
    }
  }

  async function reserveCurrentMotionAutomation() {
    const confirmed = await showConfirm(
      '현재 모션을 출발시키지 않고 다음 번 부팅(또는 재시작) 시에 자동으로 시작하도록 예약합니다.\n'
      + '재생 등록된 모션 파일과 매핑 설정을 사용합니다.',
      {
        title: '부팅 시 자동 반복 예약',
        confirmLabel: '예약',
        tone: 'info',
      },
    );
    if (!confirmed) return;
    motionRunLoading = true;
    renderMotionRunPanel();
    try {
      await ensureMotionRunMotionFileDetail();
      const payload = await reserveMotionAutomation(motionRunPayload());
      motionRunStatus = payload.status || motionRunStatus || null;
      motionRunLastResult = payload;
      if (payload.success === false) {
        await showMotionRunFailure(payload.message, '자동 반복 예약 실패');
      } else {
        await showAlert('자동 반복 예약이 완료되었습니다.\n다음 번 부팅 시 지정된 모션이 자동으로 시작됩니다.', {
          title: '자동 반복 예약 완료',
          tone: 'info',
        });
      }
    } catch (error) {
      await showMotionRunFailure(error?.message || String(error), '자동 반복 예약 실패');
    } finally {
      motionRunLoading = false;
      renderMotionRunPanel();
    }
  }

  function bindEvents() {
    if (el.motionFileRows) {
      el.motionFileRows.addEventListener('click', (event) => {
        const target = event.target.closest('[data-motion-file-id]');
        if (!target) return;
        selectFile(target.dataset.motionFileId);
      });
    }
    el.registerMotionFileButton?.addEventListener('click', registerSelectedMotionFile);
    el.unregisterMotionFileButton?.addEventListener('click', unregisterSelectedMotionFile);
    if (el.motionFileGraphAxisToggles) {
      el.motionFileGraphAxisToggles.addEventListener('click', (event) => {
        const button = event.target.closest('button');
        if (!button) return;
        event.preventDefault();
        const series = Array.isArray(analysisOf(selectedFile)?.graph_series)
          ? analysisOf(selectedFile).graph_series
          : [];
        if (button.dataset.motionFileGraphAll === 'true') {
          const allVisible = series.every((item) => !motionFileGraphHiddenIds.has(String(item.motion_id)));
          motionFileGraphHiddenIds.clear();
          if (allVisible) {
            series.forEach((item) => motionFileGraphHiddenIds.add(String(item.motion_id)));
          }
        } else if (button.dataset.motionFileGraphId !== undefined) {
          const motionId = String(button.dataset.motionFileGraphId);
          if (motionFileGraphHiddenIds.has(motionId)) motionFileGraphHiddenIds.delete(motionId);
          else motionFileGraphHiddenIds.add(motionId);
        }
        renderMotionFileGraph(selectedFile);
      });
    }
    el.exportMotionFileToStudioButton?.addEventListener('click', exportSelectedFileToStudio);
    if (el.deleteMotionFileButton) {
      el.deleteMotionFileButton.addEventListener('click', deleteSelectedFile);
    }
    if (el.motionRunCheckButton) {
      el.motionRunCheckButton.addEventListener('click', checkCurrentMotionRun);
    }
    if (el.motionRunInitializeButton) {
      el.motionRunInitializeButton.addEventListener('click', initializeCurrentMotionRun);
    }
    if (el.motionRunStartButton) {
      el.motionRunStartButton.addEventListener('click', () => startCurrentMotionRun('once'));
    }
    if (el.motionRunContinuousStartButton) {
      el.motionRunContinuousStartButton.addEventListener('click', () => startCurrentMotionRun('continuous'));
    }
    if (el.motionRunStopButton) {
      el.motionRunStopButton.addEventListener('click', stopCurrentMotionRun);
    }
    if (el.motionRunStopAfterButton) {
      el.motionRunStopAfterButton.addEventListener('click', stopCurrentMotionRunAfterCycle);
    }
    if (el.motionRunRefreshButton) {
      el.motionRunRefreshButton.addEventListener('click', refreshMotionRunStatus);
    }
    el.motionAutomationEnabled?.addEventListener('change', () => {
      void saveMotionAutomation(el.motionAutomationEnabled.checked);
    });
    el.motionAutomationRepeatMode?.addEventListener('change', () => {
      renderMotionAutomation();
      void saveMotionAutomation(true);
    });
    el.motionAutomationDwellSec?.addEventListener('change', () => {
      void saveMotionAutomation(true);
    });
    el.motionAutomationStartButton?.addEventListener(
      'click',
      startCurrentMotionAutomation,
    );
    el.motionAutomationReserveButton?.addEventListener(
      'click',
      reserveCurrentMotionAutomation,
    );
    if (el.motionRunGraphAxisToggles) {
      el.motionRunGraphAxisToggles.addEventListener('click', (event) => {
        const button = event.target.closest('button');
        if (!button) return;
        event.preventDefault();
        const file = motionRunSelectedMotionFile();
        const series = Array.isArray(analysisOf(file)?.graph_series)
          ? analysisOf(file).graph_series
          : [];
        if (button.dataset.motionRunGraphAll === 'true') {
          const allVisible = series.every((item) => !motionRunGraphHiddenIds.has(String(item.motion_id)));
          motionRunGraphHiddenIds.clear();
          if (allVisible) {
            series.forEach((item) => motionRunGraphHiddenIds.add(String(item.motion_id)));
          }
        } else if (button.dataset.motionRunGraphId !== undefined) {
          const motionId = String(button.dataset.motionRunGraphId);
          if (motionRunGraphHiddenIds.has(motionId)) motionRunGraphHiddenIds.delete(motionId);
          else motionRunGraphHiddenIds.add(motionId);
        }
        renderMotionRunGraphAxisToggles();
        renderMotionRunGraph();
      });
    }
    if (el.motionRunInitialMoveTime) {
      el.motionRunInitialMoveTime.addEventListener('change', () => {
        renderMotionRunPanel();
        renderMappingPanel();
      });
    }
    if (el.motionMappingSelect) {
      el.motionMappingSelect.addEventListener('change', async () => {
        if (!await confirmDiscardMappingChanges('다른 매칭 파일을 불러오기')) {
          renderMappingSelect();
          return;
        }
        selectMapping(el.motionMappingSelect.value);
      });
    }
    if (el.refreshMotionMappingsButton) {
      el.refreshMotionMappingsButton.addEventListener('click', async () => {
        if (await confirmDiscardMappingChanges('목록을 새로고침')) loadMappings();
      });
    }
    if (el.newMotionMappingButton) {
      el.newMotionMappingButton.addEventListener('click', newMappingDraft);
    }
    el.addMotionIdButton?.addEventListener('click', addMotionId);
    el.generateMotionIdsButton?.addEventListener('click', generateMotionIdsFromMotors);
    if (el.saveMotionMappingButton) {
      el.saveMotionMappingButton.addEventListener('click', saveCurrentMapping);
    }
    el.resetMotionMappingButton?.addEventListener('click', resetCurrentMapping);
    if (el.deleteMotionMappingButton) {
      el.deleteMotionMappingButton.addEventListener('click', deleteCurrentMapping);
    }
    if (el.importMotionIdsButton) {
      el.importMotionIdsButton.addEventListener('click', importMotionIds);
    }
    if (el.motionMappingName) {
      el.motionMappingName.addEventListener('input', () => {
        mappingDraft.name = el.motionMappingName.value;
        mappingRawText = '';
        mappingValidation = null;
        markMappingDirty();
      });
    }
    if (el.motionMappingFileSelect) {
      el.motionMappingFileSelect.addEventListener('change', () => {
        mappingDraft.motion_file_id = el.motionMappingFileSelect.value;
        mappingMotionFileDetail = null;
        mappingRawText = '';
        mappingValidation = null;
        markMappingDirty();
        setMappingMessage('모션 파일이 변경되었습니다. 모션 ID 반영을 눌러 목록을 갱신하세요');
        renderMappingPanel();
      });
    }
    if (el.motionMappingRows) {
      el.motionMappingRows.addEventListener('click', (event) => {
        const action = event.target?.dataset?.motionMappingAction;
        if (!action) return;
        const row = event.target.closest('tr[data-mapping-index]');
        if (!row) return;
        const rowIndex = Number(row.dataset.mappingIndex);
        const mappingRow = mappingDraft.mappings[rowIndex];
        if (!mappingRow) return;
        if (action === 'capture_reference') {
          captureReferencePosition(rowIndex);
        } else if (action === 'delete') {
          const deletedMotionId = String(mappingRow.motion_id || '');
          mappingDraft.mappings.splice(rowIndex, 1);
          mappingRawText = '';
          mappingValidation = null;
          markMappingDirty();
          setMappingMessage(`모션 ID ${deletedMotionId} 삭제 완료`);
          renderMappingPanel();
        }
      });
      el.motionMappingRows.addEventListener('change', (event) => {
        const field = event.target?.dataset?.motionMappingField;
        if (!field) return;
        const row = event.target.closest('tr[data-mapping-index]');
        if (!row) return;
        updateMappingRow(row.dataset.mappingIndex, field, event.target.value, event.target.checked);
      });
    }

    const automationHideBtn = document.getElementById('automationResumeHideButton');
    if (automationHideBtn) {
      automationHideBtn.addEventListener('click', () => {
        automationResumeModalHidden = true;
        renderAutomationResumeModal();
      });
    }

    const automationCancelBtn = document.getElementById('automationResumeCancelButton');
    if (automationCancelBtn) {
      automationCancelBtn.addEventListener('click', async () => {
        automationResumeModalHidden = true;
        renderAutomationResumeModal();
        await saveMotionAutomation(false);
      });
    }
  }

  return {
    bindEvents,
    resetProjectState,
    fetchFiles: async () => {
      await loadFiles();
      await loadMappings();
    },
    refreshMotionFiles: () => loadFiles(),
    openProjectFile: async (category, fileName) => {
      if (category === 'motions') await loadFiles(fileName);
      if (category === 'motion_axis_matching') await loadMappings(fileName);
    },
    refreshMappingAfterReconnect,
    syncMappingFileRevision,
    getWorkContext,
    render,
    renderRuntimeState,
    showTab: (tab) => {
      renderMotionTabs(tab);
      render();
    },
  };
}
