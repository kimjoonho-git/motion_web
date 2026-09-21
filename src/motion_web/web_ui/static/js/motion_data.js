import { createMotionFileManager } from './motion_file_manager.js';
import { motionScheduleResumeNote } from './schedule_scope.js';
import {
  checkMotionRun,
  configureMotionAutomation,
  deleteMotionFile,
  fetchMotionFile,
  fetchMotionFiles,
  fetchMotionMapping,
  fetchMotionMappings,
  fetchMotionRunStatus,
  initializeMotionRun,
  projectFileDownloadUrl,
  saveMotionMapping,
  saveRegisteredMotionFile,
  startMotionRun,
  fetchScheduleStatus,
  stopMotionRun,
  stopMotionRunAfterCycle,
  requestMotionSafetyStop,
  validateMotionMapping,
} from './api.js';
import {
  displayText,
  formatInt,
  formatMoment,
  formatNumber,
  normalizeMotorTypeKey,
  maxOf,
  minOf,
} from './format.js';
import {
  showAlert,
  showConfirm,
  showPrompt,
} from './ui_dialogs.js';

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

/**
 * 실행 대상을 한 곳에서 정한다 · §6-98
 *
 * 전에는 대상의 주인이 둘이었다 · 화면의 라디오(사용자가 고름)와 실제 연동
 * 상태(참가·마스터·PC 수)가 따로 놀았다 · 참가하지도 않은 채 "그룹 전체"를
 * 고를 수 있었고, 그러면 버튼이 전부 회색이 되는데 이유는 작은 힌트 글씨
 * 한 줄에만 나왔다.
 *
 * 이제 고를 수 없는 것은 **화면에 없다** · 참가하지 않았으면 그룹 칸이 아예
 * 없고 참가 버튼만 있다.
 *
 * 버튼 이름에 대상을 박는다 · 3대를 움직이는 버튼과 1대를 움직이는 버튼은
 * 글자가 달라야 한다 · 전에는 툴팁으로만 갈렸다.
 */
const PEER_STATE_LABEL = {
  online: '정상', warning: '경고', offline: '끊김', unknown: '확인 중',
};

/** 참가 PC 를 한 줄로 · 자세한 표는 연동 화면에 있다. */
function peerSummaryText(role = {}) {
  const here = `${role.master && role.isMaster ? role.master : '이 PC'}${role.isMaster ? '(마스터)' : ''}`;
  const others = (Array.isArray(role.peers) ? role.peers : []).map((peer) => {
    const name = peer.display_name || peer.pc_id || '이름 없음';
    const state = PEER_STATE_LABEL[peer.state] || peer.state || '확인 중';
    return `${name}${peer.is_master ? '(마스터)' : ''} ${state}`;
  });
  return [here, ...others].join(' · ');
}

/** 어느 모션축 설정 파일을 열 것인가 · §6-238
 *
 * 프로젝트는 이 파일을 **하나만** 물고 쓴다 · 그 하나를 서버가
 * `active_file_id` 로 알려준다 · 전에는 그 값이 없어서 화면이 목록의
 * **첫 번째**를 골랐다 · 파일이 하나뿐이면 우연히 맞았고, 그래서 오래
 * 들키지 않았다 · 여럿이면 프로젝트가 쓰는 것과 다른 것을 편집하게 된다.
 *
 * 등록된 것이 목록에 없으면(지워졌거나 아직 등록 전) 첫 번째로 물러선다 ·
 * 목록이 비면 빈 글자를 준다 · 그때는 새로 만들어 저장하는 자리다.
 *
 * **함수로 빼 둔다** · 화면 안에 묻어 두면 시험이 글자만 훑게 되고, 이
 * 판단이 틀려도 통과한다 · 실제로 그런 일이 있었다.
 */
export function mappingFileToOpen({ files = [], activeFileId = '' } = {}) {
  const ids = (Array.isArray(files) ? files : [])
    .map((file) => String(file?.id || '').trim())
    .filter(Boolean);
  const registered = String(activeFileId || '').trim();
  if (registered && ids.includes(registered)) return registered;
  return ids[0] || '';
}

export function motionRunTargetView({ role = {}, chosen = 'local' } = {}) {
  const joined = role.joined === true;
  const isMaster = role.isMaster === true;
  const peerCount = Math.max(1, Number(role.peerCount || 1));
  // **그룹 실행은 마스터만 한다** · §6-100
  //
  // 조정 노드가 이미 거부한다("이 PC 는 연동 슬레이브라 그룹 실행을 시작할 수
  // 없습니다") · 그런데 화면은 참가만 하면 `그룹 전체` 를 보여 줬다 · 골라서
  // 누른 뒤에야 거부당하니, 슬레이브 앞에 앉은 사람은 무엇이 잘못됐는지
  // 모른다 · 아예 고를 수 없게 한다.
  // 실행 화면에는 대상 선택이 없다 · §6-100 · 언제나 이 PC 다
  const groupSelectable = false;
  const scope = 'local';
  const group = scope === 'group';
  return {
    scope,
    joined,
    isMaster,
    peerCount,
    groupSelectable,
    // 연동 조작은 연동 화면 하나다 · 실행 화면에는 그리로 가는 길만 둔다
    needsCoordinationSetup: !joined,
    coordinationLinkLabel: joined ? 'PC 연동 설정 열기' : 'PC 연동 설정에서 참가하기',
    peerSummary: group ? peerSummaryText(role) : '',
    localLabel: '이 PC 만',
    groupLabel: `그룹 ${peerCount}대`,
    summary: group
      ? `참가 PC ${peerCount}대가 같은 시각에 움직입니다`
      : '이 PC 에 연결된 모터만 움직입니다',
    buttons: {
      initialize: group ? `그룹 초기 위치 이동 · ${peerCount}대` : '초기 위치 이동',
      start: group ? `그룹 1회 시작 · ${peerCount}대` : '1회 시작',
      continuous: group ? `그룹 연속 시작 · ${peerCount}대` : '연속 시작',
      stop: group ? `그룹 즉시 정지 · ${peerCount}대` : '즉시 정지',
      stopAfter: group ? `그룹 회차 후 정지 · ${peerCount}대` : '현재 회차 후 정지',
    },
  };
}

/**
 * 왜 지금 시작할 수 없는가 · 대상마다 판정하는 규칙이 다르다 · §6-98
 *
 * 로컬은 실행 설정과 파일이, 그룹은 연동 상태(참가·역할·통신·알람)가 정한다 ·
 * 사유는 **한 자리에** 늘 보인다 · 버튼을 회색으로만 두면 고장처럼 보인다 ·
 * 특히 슬레이브 PC 는 시작이 영원히 불가라 더 그렇다.
 */
export function motionRunBlockView({
  scope = 'local', availability = {}, localReady = true, localReason = '',
} = {}) {
  if (scope === 'group') {
    // 그룹이 이미 도는 중이면 막힌 것이 아니다 · 정지는 누구나 할 수 있다
    const blocked = availability.ok !== true && availability.active !== true;
    return { blocked, reason: blocked ? String(availability.reason || '') : '' };
  }
  return {
    blocked: !localReady,
    reason: localReady ? '' : String(localReason || ''),
  };
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

  const minTime = minOf(points.map((point) => Number(point.time_sec)));
  const maxTime = maxOf(points.map((point) => Number(point.time_sec)));
  const minValue = minOf(points.map((point) => Number(point.value)));
  const maxValue = maxOf(points.map((point) => Number(point.value)));
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

/** 프로젝트가 쓰는 모션축 설정 파일 이름 · §6-239
 *
 * 프로젝트마다 이 파일은 **하나**다 · 사람이 이름을 지을 일이 없으므로
 * 고정한다 · 이미 다른 이름으로 만들어 둔 프로젝트는 그 파일을 그대로
 * 쓴다(서버가 `active_file_id` 로 알려준다) · 이 이름은 **처음 만들 때만**
 * 쓰인다.
 *
 * **함수 안이 아니라 여기에 둔다** · 안에 두었더니 `createMotionDataController`
 * 가 도는 도중 `emptyMappingDraft()` 가 먼저 불려서 선언 전에 읽혔고,
 * 화면이 통째로 못 떴다(`Cannot access 'DEFAULT_MAPPING_NAME' before
 * initialization`) · 검사는 통과했고 **띄워 보고서야** 드러났다.
 */
const DEFAULT_MAPPING_NAME = 'motion_axis';

export function createMotionDataController({
  el,
  getLatestState = () => null,
  getConfiguredMotors = null,
  onProjectFilesChange,
  groupRun = null,
  onExportMotionFileToStudio = async () => null,
}) {
  let files = [];
  let selectedFileId = null;
  let selectedFile = null;
  // 파일로 저장할 때 쓰는 주소가 프로젝트 번호를 요구한다 · 목록 응답이 실어 온다.
  let motionProjectId = '';
  let mappingFiles = [];
  let selectedMappingId = null;
  let mappingDraft = emptyMappingDraft();
  let registeredMotionFileIdValue = '';
  let mappingValidation = null;
  let mappingMotionFileDetail = null;
  let loading = false;
  let mappingLoading = false;
  let mappingDirty = false;
  let fileLoadToken = 0;
  let mappingLoadToken = 0;
  let mappingRevision = '';
  let mappingRevisionConflict = false;
  let activeMotionPanel = 'run';
  let motionRunStatus = null;
  let motionRunLastResult = null;
  let motionRunLoading = false;
  let motionRunGraphAnimationId = null;
  let motionRunGraphFileId = '';
  let motionRunGraphToggleSignature = '';
  const motionRunGraphHiddenIds = new Set();

  function setMessage(message) {
    if (el.motionFileMessage) el.motionFileMessage.textContent = message;
  }

  function setMappingMessage(message) {
    if (el.motionMappingMessage) el.motionMappingMessage.textContent = message;
  }

  function markMappingDirty() {
    mappingDirty = true;
  }

  function isMappingRevisionConflict(message) {
    const text = String(message || '');
    return text.includes('모션축 설정이 화면을 불러온 뒤 변경')
      || text.includes('모션축 설정 버전 정보가 없습니다');
  }

  /** 저장된 내용이 이 화면과 달라졌을 때 사람에게 묻는다 · §6-243
   *
   * **말이 사람 말이어야 한다** · 전에는 이랬다.
   *
   *     「저장된 내용 불러오기」   ← 무엇을 잃는지 안 적혀 있다
   *     「편집 내용 유지」         ← 유지해서 어쩌라는 것인지 안 적혀 있다
   *
   * 게다가 「편집 내용 유지」는 **막다른 길**이었다 · `mappingRevisionConflict`
   * 가 참으로 남아서 저장을 다시 눌러도 같은 창만 뜨고 영영 저장되지 않았다 ·
   * 남는 길은 편집을 버리는 것뿐인데, 그걸 고르는 창이 두 갈래로 보였다.
   *
   * 이제 묻는 것은 하나다 — **지금 고친 것을 버릴 것인가.**
   */
  async function resolveMappingRevisionConflict(message) {
    mappingRevisionConflict = true;
    setMappingMessage(`저장하지 못했습니다: ${message}`);
    const reload = await showConfirm(
      '저장된 모션축 설정이 이 화면을 연 뒤에 바뀌었습니다.\n'
      + '지금 고친 내용은 저장되지 않았습니다.\n\n'
      + '저장된 내용을 다시 불러오면 지금 고친 것은 사라집니다.',
      {
        title: '저장하지 못했습니다',
        confirmLabel: '고친 것을 버리고 다시 불러오기',
        cancelLabel: '그대로 두기',
        tone: 'warning',
      },
    );
    if (reload && selectedMappingId) {
      await selectMapping(selectedMappingId);
      return;
    }
    // 「그대로 두기」를 골라도 **다시 저장은 해볼 수 있어야 한다** · 화면에
    // 남은 편집이 유일한 사본이다 · 막아두면 사람이 손으로 옮겨 적는 수밖에
    // 없다 · 그 사이 파일이 또 바뀌었으면 이 창이 다시 뜰 뿐이다.
    mappingRevisionConflict = false;
    setMappingMessage(
      '고친 내용을 화면에 두었습니다 · 다시 저장을 누르면 한 번 더 시도합니다',
    );
    renderMappingPanel();
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

  function setMotionRunMessage(message) {
    if (el.motionRunMessage) el.motionRunMessage.textContent = message;
  }

  async function showMotionRunFailure(message, title) {
    const detail = String(message || '모션 실행 요청이 실패했습니다');
    const blockedByCoordination = /DDS 그룹 실행이 로컬 모션 실행을 사용 중입니다/.test(detail);
    await showAlert(
      blockedByCoordination
        ? `${detail}\n\n`
          + 'PC 연동 화면에서 그룹 실행을 종료하거나 「연동 탈퇴」를 누른 뒤 다시 시도하세요.'
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
      name: DEFAULT_MAPPING_NAME,
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
    return numericOr(row.reference_position_deg, 0.0);
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

  function motionRunPayload() {
    const initialMoveTimeSec = motionRunInitialMoveTimeSec();
    const repeatMode = String(el.motionAutomationRepeatMode?.value || 'reinitialize');
    const dwellSec = Number(el.motionAutomationDwellSec?.value);
    const targetCycleCount = Math.max(0, parseInt(el.motionRunTargetCycle?.value || '0', 10));
    return {
      motion_file_id: registeredMotionFileId({ motion_file_id: registeredMotionFileIdValue }),
      mapping_file_id: selectedMappingId || '',
      initial_move_time_sec: initialMoveTimeSec,
      run_mode: 'once',
      repeat_mode: repeatMode,
      dwell_sec: Number.isFinite(dwellSec) ? dwellSec : 0.0,
      target_cycle_count: Number.isFinite(targetCycleCount) ? targetCycleCount : 0,
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
    const targetCycle = Number(status?.summary?.target_cycle_count) || 0;
    const currentCycle = Number(status?.current_cycle) || 0;
    const cycleText = targetCycle > 0 
      ? ` [${formatInt(currentCycle)} / ${formatInt(targetCycle)}회차]` 
      : (currentCycle > 0 ? ` [${formatInt(currentCycle)}회차]` : '');
    
    if (state === 'initializing') {
      return `초기 위치 이동 ${formatNumber(progress.elapsed_sec, 2)} / ${formatNumber(progress.duration_sec, 2)} s${cycleText}`;
    }
    if (state === 'running' || state === 'stopping') {
      return `모션 진행 ${formatNumber(progress.elapsed_sec, 2)} / ${formatNumber(progress.duration_sec, 2)} s${cycleText}`;
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

    const minTime = minOf(points.map((point) => Number(point.time_sec)));
    const maxTime = maxOf(points.map((point) => Number(point.time_sec)));
    const minValue = minOf(points.map((point) => Number(point.value)));
    const maxValue = maxOf(points.map((point) => Number(point.value)));
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
    // 아홉 칸을 같은 크기로 늘어놓으면 정작 알아야 할 "무엇이 재생되는가"가
    // 묻힌다. 실행 대상 셋을 크게 두고, 나머지 수치는 아래에 작게 붙인다.
    const targets = [
      { label: '재생 파일', value: runFile?.filename || payload.motion_file_id || '등록된 파일 없음', missing: !runFile && !payload.motion_file_id },
      { label: '모션축 설정', value: mappingFile?.filename || payload.mapping_file_id || '선택 안 됨', missing: !mappingFile && !payload.mapping_file_id },
      { label: '상태', value: motionRunStateText(status.state) },
    ].map((item) => (
      `<div class="motion-run-target-item${item.missing ? ' missing' : ''}">`
      + `<span>${displayText(item.label)}</span>`
      + `<strong title="${displayText(item.value)}">${displayText(item.value)}</strong>`
      + '</div>'
    )).join('');

    const metrics = [
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
    ].map((item) => (
      `<div class="motion-run-metric"><span>${displayText(item.label)}</span>`
      + `<strong>${displayText(item.value)}</strong></div>`
    )).join('');

    // 고른 파일과 등록된 파일이 다르면 실행 결과가 어긋난다 · 그 자리에서 알린다.
    const notice = mismatch
      ? '<div class="motion-run-notice">선택한 파일은 재생 등록되어 있지 않습니다 ·'
        + ' 재생 등록된 파일 기준으로 실행됩니다</div>'
      : '';

    el.motionRunSummary.innerHTML =
      `<div class="motion-run-targets">${targets}</div>`
      + `<div class="motion-run-metrics">${metrics}</div>`
      + notice;
  }

  function renderMotionRunStatus() {
    if (!el.motionRunStatus) return;
    const status = motionRunStatus || {};
    const progress = motionRunEffectiveProgress(status);
    const capabilities = status.capabilities || {};
    const warnings = Array.isArray(status.warnings) ? status.warnings : [];
    const ratio = Number(progress.ratio);
    const repeatMode = String(el.motionAutomationRepeatMode?.value || 'reinitialize');
    const continuousText = (() => {
      const cap = capabilities.continuous_run;
      if (!cap || typeof cap.available !== 'boolean') return '검사 전';
      if (cap.available === false && (repeatMode === 'reinitialize' || repeatMode === 'dwell_reinitialize')) {
        return '가능 — 초기 위치 이동 설정으로 안전 가드 통과';
      }
      return `${cap.available ? '가능' : '불가'} — ${cap.reason || '-'}`;
    })();
    const capabilityText = (capability) => {
      if (!capability || typeof capability.available !== 'boolean') return '검사 전';
      return `${capability.available ? '가능' : '불가'} — ${capability.reason || '-'}`;
    };
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
          <!-- 두 쌍씩 채워 다섯 줄로 · §6-100 · 한 쌍만 쓰고 가로를 비워 두면
               실행 화면에서 상태표가 그래프보다 높이를 더 먹는다 -->
          <tr>
            <th>초기 위치</th><td>${displayText(capabilityText(capabilities.initial_position))}</td>
            <th>1회 모션</th><td>${displayText(capabilityText(capabilities.single_run))}</td>
          </tr>
          <tr>
            <th>연속 모션</th><td>${displayText(continuousText)}</td>
            <th>범위 제한</th><td>${displayText(warnings.length ? warnings.join(' / ') : '제한 적용 없음')}</td>
          </tr>
          <tr>
            <th>메시지</th><td>${displayText(status.message || '-')}</td>
            <th>갱신</th><td>${displayText(timeText(status.updated_at))}</td>
          </tr>
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


  /** 한 회차가 끝나면 어떻게 잇는가 · 바로 · 대기 후 · 초기 위치 이동 후
   *
   * 부팅 때 스스로 시작하는 기능은 뺐다 · §6-134 · 여기 남은 것은 **반복
   * 방식**뿐이고, 스케줄도 이 값을 읽어 그대로 실어 보낸다.
   */
  function renderMotionAutomation() {
    const automation = motionRunStatus?.automation || {};
    // 안 적었으면 「초기 위치 이동 후 다음」이다 · §6-135 · 선택칸의
    // 기본값(`selected`)과 같아야 한다 · 전에는 여기만 `direct` 라서
    // 화면에 골라진 것과 실제로 나가는 값이 달랐다
    const repeatMode = String(automation.repeat_mode || 'reinitialize');
    const dwellSec = Number(automation.dwell_sec);
    const busy = motionRunLoading;

    // **저장된 값을 선택칸에 넣는다** · §6-268
    //
    // 전에는 `repeatMode` 를 읽어 놓고 선택칸에 넣지 않았다 · 그래서 화면은
    // 언제나 HTML 의 기본값(`초기 위치 이동 후 다음`)을 보여 줬고, 파일에
    // `바로 다음 모션` 이 적혀 있어도 그대로였다.
    //
    // 더 나쁜 것은 그다음이다 · 사람이 다른 값을 고르면 **화면에 보이던
    // 잘못된 값이 그대로 파일에 덮어써졌다** · 저장해 둔 설정이 화면을 한 번
    // 열었다는 이유로 바뀌었다.
    //
    // 사람이 그 칸을 만지는 중이면 건드리지 않는다 · 고르는 도중에 값이
    // 바뀌면 손이 미끄러진 것처럼 보인다.
    if (el.motionAutomationRepeatMode) {
      el.motionAutomationRepeatMode.disabled = busy;
      if (document.activeElement !== el.motionAutomationRepeatMode
        && el.motionAutomationRepeatMode.value !== repeatMode) {
        el.motionAutomationRepeatMode.value = repeatMode;
      }
    }
    const currentRepeatMode = el.motionAutomationRepeatMode?.value || repeatMode;
    const showDwell = currentRepeatMode === 'dwell' || currentRepeatMode === 'dwell_reinitialize';
    el.motionAutomationDwellWrap?.classList.toggle('hidden', !showDwell);
    if (el.motionAutomationDwellSec) {
      el.motionAutomationDwellSec.disabled = busy;
      if (document.activeElement !== el.motionAutomationDwellSec
        && Number.isFinite(dwellSec)
        && Number(el.motionAutomationDwellSec.value) !== dwellSec) {
        el.motionAutomationDwellSec.value = String(dwellSec);
      }
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
    const continuousUnavailable = (() => {
      if (continuousCapability?.available !== false) return false;
      const repeatMode = String(el.motionAutomationRepeatMode?.value || 'reinitialize');
      if (repeatMode === 'reinitialize' || repeatMode === 'dwell_reinitialize') return false;
      return true;
    })();
    // 범위에 따라 판정하는 규칙이 다르다 · 로컬은 실행 설정과 파일, 그룹은
    // 연동 상태(참가·통신·알람)가 정한다. 버튼은 한 벌이고 판정만 갈린다 · §6-65
    //
    // 대상과 사유는 순수 함수 둘이 정한다 · 화면 없이 시험할 수 있어야 한다 · §6-98
    const target = motionRunTargetView({
      role: groupRunRole(), chosen: 'local',
    });
    renderMotionRunTarget(target);
    const scope = target.scope;
    const group = scope === 'group' ? groupRunAvailability() : null;
    const groupActive = Boolean(group?.active);
    const { blocked, reason: blockReason } = motionRunBlockView({
      scope,
      availability: group || {},
      localReady: contextReady && hasRequiredFiles,
      localReason: contextMessage,
    });

    // 같은 이름의 자동 재생이 둘이었다 · 범위마다 자기 것만 보인다 · §6-66
    el.motionAutomationToggleWrap?.classList.toggle('hidden', scope === 'group');
    if (el.motionRunScopeGroupHint) {
      const availability = group || groupRunAvailability();
      el.motionRunScopeGroupHint.textContent = availability.ok
        ? '같은 시각에 동시 시작'
        : (availability.reason || '연동 상태 확인 중');
    }
    if (el.motionRunCheckButton) {
      // 실행 준비 검사는 이 PC 기준이다 · 그룹 범위에서는 쓰지 않는다
      // **준비 검사는 상태를 보는 일이다** · §6-203
      //
      // 「실행 컨텍스트가 준비 안 됐으면」 막았다 · 그런데 무엇이 모자란지
      // 보려고 누르는 버튼이다 · 안 되는 이유를 알려면 눌러야 하는데
      // 안 된다고 막으면 알 길이 없다.
      el.motionRunCheckButton.disabled = motionRunLoading
        || !hasRequiredFiles || running || scope === 'group';
      el.motionRunCheckButton.title = scope === 'group'
        ? '실행 준비 검사는 이 PC 단독 범위에서만 사용합니다'
        : (contextReady ? '' : contextMessage);
    }
    if (el.motionRunInitializeButton) {
      el.motionRunInitializeButton.disabled = motionRunLoading || running
        || (scope === 'group' ? !group.ok : (!contextReady || !hasMappingFile));
      el.motionRunInitializeButton.title = blocked ? blockReason : '';
    }
    if (el.motionRunStartButton) {
      el.motionRunStartButton.disabled = motionRunLoading || running || blocked;
      el.motionRunStartButton.title = blocked
        ? blockReason
        : (scope === 'group'
          ? `참가 PC ${group.peerCount}대를 같은 시각에 1회 실행합니다`
          : '전체 모션축 초기 위치 이동 완료 후 모션을 1회 실행합니다');
    }
    if (el.motionRunContinuousStartButton) {
      el.motionRunContinuousStartButton.disabled = motionRunLoading || running
        || blocked || continuousUnavailable;
      el.motionRunContinuousStartButton.title = continuousUnavailable
        ? (continuousCapability?.reason || '연속 모션 안전조건을 통과하지 못했습니다')
        : (blocked
          ? blockReason
          : '전체 모션축 초기 위치 이동 완료 후 정지할 때까지 모션을 반복합니다');
    }
    // 시작은 마스터만이지만 정지는 누구나 · 그룹이 도는 동안이면 슬레이브에서도
    // 세울 수 있어야 한다 · §6-70
    const stoppable = scope === 'group' ? groupActive : running;
    if (el.motionRunStopButton) {
      el.motionRunStopButton.disabled = motionRunLoading || !stoppable;
      el.motionRunStopButton.title = scope === 'group'
        ? '참가 PC 전체를 즉시 정지합니다'
        : '이 PC의 모션을 즉시 정지합니다';
    }
    if (el.motionRunStopAfterButton) {
      el.motionRunStopAfterButton.disabled = motionRunLoading || !stoppable;
      el.motionRunStopAfterButton.title = scope === 'group'
        ? '참가 PC 전체를 현재 회차까지 마친 뒤 정지합니다'
        : '이 PC의 모션을 현재 회차까지 마친 뒤 정지합니다';
    }
    if (el.motionRunRefreshButton) {
      el.motionRunRefreshButton.disabled = motionRunLoading;
    }
    // 버튼을 회색으로만 두면 고장처럼 보인다 · 슬레이브 PC 는 시작이 영원히
    // 불가라 더 그렇다 · 사유를 늘 같은 자리에 적는다 · §6-98
    if (el.motionRunBlockReason) {
      el.motionRunBlockReason.textContent = blockReason;
      el.motionRunBlockReason.classList.toggle('hidden', !blocked || !blockReason);
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
  }

  function renderMotionTabs(active = null) {
    // 'files'는 'run'에 합쳐졌다 · 옛 값이 들어와도 실행 화면을 연다
    const requested = String(active || activeMotionPanel || 'run');
    const next = requested === 'files' ? 'run' : requested;
    activeMotionPanel = ['mapping', 'midi', 'run'].includes(next) ? next : 'run';
    if (el.motionPanels) {
      el.motionPanels.forEach((panel) => {
        panel.classList.toggle('hidden', panel.dataset.motionPanel !== activeMotionPanel);
      });
    }
  }

  function renderFileRows() {
    if (el.motionFileCount) {
      el.motionFileCount.textContent = files.length ? `${files.length}개` : '';
    }
    if (!el.motionFileRows) return;
    if (!files.length) {
      el.motionFileRows.innerHTML = emptyRow(4, '저장된 모션 파일이 없습니다');
      return;
    }
    el.motionFileRows.innerHTML = files.map((file) => {
      const analysis = analysisOf(file);
      const selected = file.id === selectedFileId;
      // 재생 등록된 파일은 목록에서 바로 구분돼야 한다. 등록 여부를 알려면
      // 모션축 설정을 열어봐야 했던 것이 가장 흔한 혼란이었다.
      const registered = Boolean(registeredMotionFileIdValue)
        && file.id === registeredMotionFileIdValue;
      const rowClass = [selected ? 'selected' : '', registered ? 'registered' : '']
        .filter(Boolean).join(' ');
      const badge = registered
        ? '<span class="motion-registered-badge" title="이 파일이 재생 등록되어 있습니다">재생</span>'
        : '';
      return (
        `<tr class="${rowClass}" data-motion-file-id="${displayText(file.id)}">
          <td class="motion-file-name-cell"><div class="motion-file-name-inner">${badge}<button type="button" class="link-button" data-motion-file-id="${displayText(file.id)}">${displayText(file.filename)}</button></div></td>
          <td><span class="motion-state-pill ${statusClass(file)}">${statusText(file)}</span></td>
          <td>${formatNumber(analysis.time?.duration_sec, 3)} s</td>
          <td class="motion-file-moment" title="마지막으로 바뀐 시각">${displayText(formatMoment(file.updated_at))}</td>
        </tr>`
      );
    }).join('');
  }

  /** 모션 파일 버튼을 켜고 끈다 · §6-100
   *
   * **화면 조각에 딸려 있으면 안 된다** · 전에는 `선택 파일 상세`를 그리는
   * 함수 안에 끼어 있었다 · 그 화면을 걷어내자 함수째 사라져 재생 등록도
   * 삭제도 못 하는 상태가 됐다 · 버튼은 그 화면이 없어도 있다.
   */
  function renderMotionFileActions() {
    const file = selectedFile;
    const registered = Boolean(file && file.id === registeredMotionFileIdValue);
    if (el.deleteMotionFileButton) {
      el.deleteMotionFileButton.disabled = !file || loading;
      el.deleteMotionFileButton.title = registered
        ? '재생 등록을 해제한 뒤 삭제할 수 있습니다'
        : '';
    }
    if (el.exportMotionFileToStudioButton) {
      el.exportMotionFileToStudioButton.disabled = !file || loading;
      el.exportMotionFileToStudioButton.title = file
        ? '선택한 실행 파일을 독립된 스튜디오 레이어로 내보냅니다'
        : '모션 파일을 먼저 선택하세요';
    }
    if (el.downloadMotionFileButton) {
      el.downloadMotionFileButton.disabled = !file || !motionProjectId || loading;
      el.downloadMotionFileButton.title = file
        ? '이 파일을 지금 보고 있는 PC 에 저장합니다'
        : '모션 파일을 먼저 선택하세요';
    }
    // 재생 등록은 **모션축 설정 편집과 상관없다** · §6-160
    //
    // 전에는 두 버튼이 `mappingDirty` 로 꺼졌다 · 모션축 설정을 편집 중이면
    // 모션 파일도 못 바꿨고, 등록이 한 번 실패하면 프로그램이 제 손으로 세운
    // 그 표시 때문에 **되돌아갈 길까지 사라졌다**.
    //
    // 한 파일에 들어 있을 뿐 둘은 남남이다 · MIDI 뱅크가 이미 그렇게
    // 떨어져 있다.
    if (el.registerMotionFileButton) {
      el.registerMotionFileButton.disabled = (
        !file || !selectedMappingId || loading || mappingLoading || registered
      );
      el.registerMotionFileButton.textContent = registered ? '재생 등록됨' : '재생 등록';
      el.registerMotionFileButton.title = !selectedMappingId
        ? '저장된 모션축 설정을 먼저 선택하세요'
        : '이 파일을 재생 등록합니다 · 모션축 설정 편집과는 무관합니다';
    }
    if (el.unregisterMotionFileButton) {
      el.unregisterMotionFileButton.disabled = (
        !registered || !selectedMappingId || loading || mappingLoading
      );
      el.unregisterMotionFileButton.title = registered
        ? '현재 모션축 설정에서 이 파일의 재생 등록을 해제합니다'
        : '현재 재생 등록된 파일을 선택하세요';
    }
  }

  function renderMappingFileName() {
    // 고르는 자리가 아니라 **보여주는 자리**다 · §6-239
    //
    // 프로젝트는 모션축 설정 파일을 **하나만** 물고 쓴다 · 전에는 목록에서
    // 고르게 했는데, 고를 일이 없으니 고르는 상자와 「새 매칭 작성」·「목록
    // 새로고침」·「현재 설정 파일 삭제」가 다 쓸모없는 손잡이였다 · 그것들이
    // 만들 수 있는 어긋난 상태(등록된 파일과 다른 것을 편집하고 있다)만
    // 남았다.
    if (!el.motionMappingFileName) return;
    const file = mappingFiles.find((item) => item.id === selectedMappingId);
    el.motionMappingFileName.textContent = file?.filename || selectedMappingId || '아직 없음 · 저장하면 만들어집니다';
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
    // 대소문자를 무시하고 맞춘다 · §6-104
    //
    // 화면은 `encodeURIComponent` 로 값을 만드는데 그것은 **대문자**를 낸다
    // (`%2Fdev%2F...usb-FTDI_...`) · 서버는 저장할 때 **소문자**로 바꾼다
    // (`%2fdev%2f...usb-ftdi_...`). 그래서 저장을 누른 뒤 화면이 제 선택을
    // 못 찾아 「선택 안함」으로 되돌아갔다.
    //
    // AC 서보 값(`ac_servo:master:0:alias:103`)은 대문자가 없어 안 걸린다 ·
    // **다이나믹셀에서만** 났고, 저장 직후에만 났다.
    //
    // 짝을 찾는 다른 곳(`motorForRef`)은 이미 양쪽을 소문자로 낮춰 비교한다 ·
    // 여기만 빠져 있었다.
    const value = String(
      row.motor_ref || motorSelectionValue(mappedMotor) || ''
    ).trim().toLowerCase();
    return (
      `<select class="wide-select" data-motion-mapping-field="motor_ref" data-motion-id="${displayText(row.motion_id)}">
        <option value="">선택 안함</option>
        ${motors.map((motor) => {
          const selectionValue = motorSelectionValue(motor);
          const selected = selectionValue.trim().toLowerCase() === value ? ' selected' : '';
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
      const firstFrameInitial = initialMode === 'first_frame';
      const initialPositionDisabled = firstFrameInitial;
      const dynamixelGearFixed = isDynamixelMappingRow(row);
      const referencePositionValue = displayReferencePosition(row);
      const initialPositionValue = displayInitialPosition(row);
      const gearRatioValue = mappingGearRatioValue(row);
      const initialPositionDisabledAttr = initialPositionDisabled ? ' disabled' : '';
      const gearRatioDisabledAttr = dynamixelGearFixed ? ' disabled title="다이나믹셀은 감속비를 사용하지 않으며 1로 고정됩니다"' : '';
      return (
        `<tr data-mapping-index="${index}">
          <td class="motion-id-cell"><input class="motion-id-input mono" type="text" pattern="[1-9]\\d*-[1-9]\\d*" title="양의 정수-양의 정수 형식으로 입력하세요. 예: 1-1, 2-3" data-motion-mapping-field="motion_id" value="${displayText(row.motion_id)}" placeholder="예: 1-1" autocomplete="off" autocapitalize="off" spellcheck="false"></td>
          <td><input type="checkbox" data-motion-mapping-field="enabled" ${row.enabled ? 'checked' : ''}></td>
          <td class="mapping-motor-cell">${motorSelectHtml(row)}</td>
          <td class="mapping-number-cell ${dynamixelGearFixed ? 'mapping-disabled-cell' : ''}"><input class="numeric-input mapping-number-input" type="number" min="0.0001" step="0.0001" data-motion-mapping-field="gear_ratio" value="${displayText(gearRatioValue)}"${gearRatioDisabledAttr}></td>
          <td class="mapping-number-cell"><input class="numeric-input mapping-number-input" type="number" step="0.001" data-motion-mapping-field="reference_position_deg" value="${displayText(referencePositionValue)}"></td>
          <td>
            <button class="mapping-mini-button" type="button" data-motion-mapping-action="capture_reference">캡처</button>
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
          <td><input type="checkbox" data-motion-mapping-field="invert" ${row.invert ? 'checked' : ''}></td>
          <td><span class="motion-state-pill ${status.className}">${displayText(status.text)}</span> <button class="mapping-mini-button" type="button" data-motion-mapping-action="delete">삭제</button></td>
        </tr>`
      );
    }).join('');
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
    renderMappingFileName();
    if (el.saveMotionMappingButton) el.saveMotionMappingButton.disabled = mappingLoading;
    if (el.addMotionIdButton) el.addMotionIdButton.disabled = mappingLoading;
    if (el.generateMotionIdsButton) el.generateMotionIdsButton.disabled = mappingLoading;
    if (el.resetMotionMappingButton) el.resetMotionMappingButton.disabled = mappingLoading;
    el.motionMappingRows?.closest('table')?.classList.toggle('mapping-loading', mappingLoading);
    renderMappingRows();
    renderMappingValidation();
  }

  function renderRuntimeMappingState() {
    if (activeMotionPanel !== 'mapping') return;
    const activeElement = document.activeElement;
    if (
      activeElement &&
      activeElement.closest?.('#motionMappingRows')
    ) {
      return;
    }
    renderMappingRows();
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
    renderMotionFileActions();
    renderMappingPanel();
    renderMotionRunPanel();
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
      // **프로젝트가 물고 있는 파일**을 연다 · 판단은 밖에 있다 · §6-238
      const openId = mappingFileToOpen({
        files: mappingFiles,
        activeFileId: payload.active_file_id,
      });
      if (openId && openId !== selectedMappingId) {
        await selectMapping(openId);
        return;
      }
      if (selectedMappingId && !mappingFiles.some((file) => file.id === selectedMappingId)) {
        selectedMappingId = null;
        mappingRevision = '';
        mappingDraft = emptyMappingDraft();
        registeredMotionFileIdValue = '';
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
      if (payload.success === false || !payload.mapping) {
        // **실패한 응답으로 덮어쓰지 않는다** · §6-237
        //
        // 서버는 못 읽었을 때도 **HTTP 200** 에 `success: false` 만 실어
        // 보낸다 · 그래서 여기는 오류로 치지 않았고, 아래 한 줄이
        //
        //     const loadedDraft = payload.mapping || emptyMappingDraft();
        //
        // 들고 있던 설정을 **빈 것으로 갈아엎었다** · 「매핑 이름」이 비고
        // 저절로 돌아오지 않았다 · 파일은 서버에 멀쩡히 있는데도 그랬다.
        //
        // 「설정 적용 · 모터 재시작」 뒤에 늘 그랬다 · 웹 서버가 다시 뜨면
        // 화면이 곧바로 매핑을 다시 읽는데, 그 순간 모션 쪽 노드가 아직
        // 안 떠서 `success: false` 가 온다 · 실측 34밀리초 만에 지워졌다.
        //
        // 못 읽었으면 **아무것도 하지 않는 것이 맞다** · 화면이 들고 있던
        // 것이 마지막으로 확인된 값이다 · 노드가 뜨면 다시 읽는다.
        setMappingMessage('모션축 설정을 아직 못 읽었습니다 · 잠시 후 다시 읽습니다');
        window.setTimeout(() => {
          if (selectedMappingId === requestedMappingId) selectMapping(requestedMappingId);
        }, 3000);
        return;
      }
      const loadedDraft = payload.mapping;
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

  /** 파일을 **이 웹을 보고 있는 컴퓨터**에 저장한다 · 서버끼리 옮기지 않는다.
   *
   * blob 이 아니라 평범한 링크다. blob 은 헤드리스에서 확인이 안 됐고, 서버가
   * 한글 파일명을 이미 `filename*=utf-8''` 로 붙여 준다.
   */
  function downloadSelectedMotionFile() {
    const file = selectedFile;
    if (!file || !motionProjectId) {
      setMessage('내 PC로 저장할 모션 파일을 먼저 선택하세요');
      return;
    }
    const anchor = document.createElement('a');
    anchor.href = projectFileDownloadUrl(motionProjectId, 'motions', file.id);
    anchor.download = file.filename || file.id;
    anchor.click();
    setMessage(`내 PC로 저장: ${file.filename || file.id}`);
  }

  /** 재생 등록·해제를 걸고 **실패하면 되돌린다** · §6-159
   *
   * 전에는 이랬다 · 등록을 누르면 먼저 초안을 고치고 `markMappingDirty()` 를
   * 세운 다음 저장했다 · 저장이 실패하면 **그 「고쳐진 중」 표시가 그대로
   * 남았다**.
   *
   * 그러면 등록 버튼도 해제 버튼도 `mappingDirty` 때문에 꺼진다 · 버튼에는
   * 「설정 저장 필요」 라고 뜨는데, 사용자는 아무것도 편집한 적이 없다 ·
   * 프로그램이 제 손으로 세운 표시 때문에 **되돌아갈 길이 사라진다**.
   *
   * 실제로 그렇게 막혔다 · 모션축 설정 파일이 화면을 띄운 뒤 바뀌어
   * (`revision conflict`) 저장이 거부됐고, 그 뒤로는 등록도 해제도 안 됐다.
   *
   * 여기 들어올 때 `mappingDirty` 는 반드시 거짓이다(두 함수가 먼저 막는다) ·
   * 그러니 실패하면 우리가 세운 것만 지우면 된다.
   */
  /** 재생 등록·해제 · **모션축 설정은 건드리지 않는다** · §6-160
   *
   * 전에는 `saveCurrentMapping()` 을 불렀다 · 그것은 모션축 설정 **전체**를
   * 보내는 길이라 두 가지가 딸려 왔다.
   *
   *   하나 · 편집 중인 모션축 설정까지 같이 저장된다 (원하지 않은 저장)
   *   둘  · 설정 개정 검사에 걸려 「모션축 설정 저장 충돌」 창이 뜬다
   *
   * 모션 데이터만 건드린 사람에게 편집한 적도 없는 설정을 되돌릴지 묻는
   * 창이 떴다 · 셋(모션축 설정 · MIDI 뱅크 · 재생 등록)은 한 파일에 들어
   * 있을 뿐 서로 남남이다 · MIDI 가 이미 제 길로 다닌다.
   */
  async function applyMotionFileRegistration(fileId, detail, label) {
    setMappingMessage(label);
    mappingLoading = true;
    renderMappingPanel();
    try {
      const payload = await saveRegisteredMotionFile({
        file_id: selectedMappingId,
        motion_file_id: fileId,
      });
      if (payload.success === false) {
        setMappingMessage(`재생 등록 실패: ${payload.message || '저장하지 못했습니다'}`);
        return false;
      }
      // 편집 중인 모션축 설정은 **그대로 둔다** · 우리가 바꾼 칸만 반영한다
      mappingDraft.motion_file_id = fileId;
      mappingMotionFileDetail = detail;
      registeredMotionFileIdValue = registeredMotionFileId(mappingDraft);
      syncMappingFileRevision(payload.file);
      setMappingMessage(payload.message || label);
      await onProjectFilesChange?.();
      return true;
    } catch (error) {
      setMappingMessage(`재생 등록 실패: ${error?.message || error}`);
      return false;
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
    await applyMotionFileRegistration(
      selectedFile.id,
      selectedFile,
      `재생 등록 저장 중: ${selectedFile.filename}`,
    );
    render();
  }

  async function unregisterSelectedMotionFile() {
    if (!selectedFile || !selectedMappingId || selectedFile.id !== registeredMotionFileIdValue) {
      setMessage('현재 재생 등록된 모션 파일을 선택하세요');
      return;
    }
    const registeredFilename = selectedFile.filename;
    const confirmed = await showConfirm(
      `${registeredFilename} 파일의 재생 등록을 해제합니다.\n`
      + '파일은 삭제되지 않으며, 다시 등록하기 전까지 모션 실행은 차단됩니다.',
      { title: '모션 파일 재생 등록 해제', confirmLabel: '등록 해제', tone: 'danger' },
    );
    if (!confirmed) return;
    await applyMotionFileRegistration(
      '',
      null,
      `재생 등록 해제 저장 중: ${registeredFilename}`,
    );
    if (!registeredMotionFileIdValue) {
      motionRunStatus = null;
      motionRunLastResult = null;
      setMotionRunMessage('재생 등록된 모션 파일이 없습니다');
    }
    render();
  }

  /** 저장하고 **됐는지 알려준다** · §6-159
   *
   * 전에는 아무것도 안 돌려줬다 · 부르는 쪽은 실패를 알 길이 없어 성공한 셈
   * 치고 넘어갔고, 그 사이 「고쳐진 중」 표시만 남아 버튼이 죽었다.
   */
  async function saveCurrentMapping() {
    const draftError = validateMappingDraft();
    if (draftError) {
      mappingValidation = null;
      setMappingMessage(`매핑 저장 중단: ${draftError}`);
      renderMappingPanel();
      return false;
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
        return false;
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
          return false;
        }
        setMappingMessage(`매핑 저장 실패: ${payload.message || '검증 실패'}`);
        return false;
      }
      mappingFiles = Array.isArray(payload.files) ? payload.files : mappingFiles;
      mappingDraft = payload.mapping || mappingDraft;
      registeredMotionFileIdValue = registeredMotionFileId(mappingDraft);
      selectedMappingId = payload.file?.id || mappingDraft.file_id || selectedMappingId;
      mappingRevision = mappingFileRevision(payload.file);
      mappingDirty = false;
      mappingRevisionConflict = false;
      setMappingMessage(payload.message || (
        payload.runtime_applied
          ? `모션축 설정 저장 완료: ${selectedMappingId} · MIDI 적용 완료`
          : `모션축 설정 저장 완료: ${selectedMappingId}`
      ));
      await onProjectFilesChange?.();
      return true;
    } catch (error) {
      if (isMappingRevisionConflict(error?.message || error)) {
        await resolveMappingRevisionConflict(error?.message || error);
        return false;
      }
      setMappingMessage(`매핑 저장 실패: ${error?.message || error}`);
      return false;
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
    mappingValidation = null;
    mappingDirty = false;
    mappingRevisionConflict = false;
    setMappingMessage(`${label}의 저장하지 않은 편집 내용을 버렸습니다`);
    renderMappingPanel();
  }

  async function refreshMappingAfterReconnect() {
    if (!selectedMappingId) {
      // 고른 것이 없으면 **목록부터** 다시 읽는다 · §6-236
      //
      // 전에는 여기서 그냥 나갔다 · 「모터축 설정」을 적용하면 웹 서버가
      // 다시 뜨는데, 그때 고른 매핑이 없으면 아무것도 안 읽었다 · 「편집할
      // 매칭」과 「매핑 이름」이 **빈 채로 남았다** · 프로젝트에 파일이
      // 멀쩡히 있는데도 그랬다 · 「목록 새로고침」을 누르기 전까지 그대로다.
      //
      // 고른 것이 없다는 말은 **읽을 것이 없다는 말이 아니다** · 목록을
      // 읽으면 첫 파일이 자동으로 잡힌다.
      await loadMappings();
      return;
    }
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

  function updateMappingRow(rowIndex, field, value, checked = false) {
    const row = mappingDraft.mappings[Number(rowIndex)];
    if (!row) return;
    if (field === 'motion_id') {
      row.motion_id = String(value || '').trim();
    } else if (field === 'enabled' || field === 'invert') {
      row[field] = Boolean(checked);
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
      field === 'gear_ratio'
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
    mappingValidation = null;
    mappingMotionFileDetail = null;
    mappingDirty = false;
    mappingRevisionConflict = false;
    fileLoadToken += 1;
    mappingLoadToken += 1;
    motionRunStatus = null;
    motionRunLastResult = null;
    motionRunGraphFileId = '';
    motionRunGraphHiddenIds.clear();
    setMessage('현재 프로젝트 모션 파일을 불러오세요');
    setMappingMessage('현재 프로젝트 모션축 설정을 불러오세요');
    setMotionRunMessage('현재 프로젝트 모션을 선택하세요');
    render();
    renderMappingPanel();
    renderMotionRunPanel();
  }

  const fileManager = createMotionFileManager({
    onFilesChanged: (newFiles, projectId) => {
      files = newFiles;
      motionProjectId = String(projectId || '');
      render();
    },
    onFileSelected: (id, file) => { selectedFileId = id; selectedFile = file; render(); },
    onExportToStudio: async (id) => {
      const result = await onExportMotionFileToStudio(id);
      await onProjectFilesChange?.();
      return result;
    },
    onProjectFilesChange: () => onProjectFilesChange?.(),
    setMessage: setMessage,
    setLoading: (l) => { loading = l; render(); },
    checkIsFileRegistered: (id) => id === registeredMotionFileIdValue,
  });

  async function loadFiles(id) { return fileManager.loadFiles(id); }
  async function selectFile(id, token) { return fileManager.selectFile(id, token); }
  async function exportSelectedFileToStudio() { return fileManager.exportSelectedFileToStudio(); }
  async function deleteSelectedFile() { return fileManager.deleteSelectedFile(); }


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

  /** 실행 범위 · 'local'(이 PC) 또는 'group'(참가한 전체 PC).
   *
   * 둘은 같은 모터 경로를 쓰는 배타 관계다 · 그룹이 도는 동안 로컬 실행은
   * 브리지가 거절한다(`coordination_bridge.local_execution_blocker`). 그래서
   * 화면에서도 하나만 고르게 한다 · 같은 이름의 버튼을 두 벌 두지 않는다 · §6-65
   */


  /** 연동 역할 · 참가 여부와 PC 수의 주인은 조정 노드다. */
  function groupRunRole() {
    return groupRun?.role?.() || { joined: false, peerCount: 1, isMaster: false };
  }

  /** 이번 실행의 대상 · 고를 수 없는 것은 골라져 있어도 '이 PC 만' 이다. */
  function motionRunScope() {
    // 실행 화면에는 대상 선택이 없다 · §6-100 · 언제나 이 PC 다
    return 'local';
  }

  /** 버튼 이름을 판정대로 그린다 · 여기서 다시 판단하지 않는다. */
  function renderMotionRunTarget(target) {
    const labels = target.buttons;
    if (el.motionRunInitializeButton) {
      el.motionRunInitializeButton.textContent = labels.initialize;
    }
    if (el.motionRunStartButton) el.motionRunStartButton.textContent = labels.start;
    if (el.motionRunContinuousStartButton) {
      el.motionRunContinuousStartButton.textContent = labels.continuous;
    }
    if (el.motionRunStopButton) el.motionRunStopButton.textContent = labels.stop;
    if (el.motionRunStopAfterButton) {
      el.motionRunStopAfterButton.textContent = labels.stopAfter;
    }
  }

  function groupRunAvailability() {
    return groupRun?.availability?.() || { ok: false, reason: '연동 정보를 받지 못했습니다' };
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

  /** 멈췄는데 스케줄이 되돌릴 거라면 그 자리에서 말해 준다 · §6-149
   *
   * 누를 때 한 번만 묻는다 · 상시로 두드리면 보여주려던 안내가 서버를 느리게
   * 만든다 · 실제로 그것 때문에 그룹 실행이 통째로 멈춘 적이 있다(§6-146).
   */
  async function scheduleResumeNote() {
    try {
      return motionScheduleResumeNote(await fetchScheduleStatus());
    } catch (error) {
      return '';                 // 안내를 못 해도 정지 자체는 이미 됐다
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
      const note = await scheduleResumeNote();
      setMotionRunMessage(
        `${payload.message || '정지 요청 완료'}${note ? ` · ${note}` : ''}`,
      );
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
      const note = await scheduleResumeNote();
      setMotionRunMessage(
        `${payload.message || '회차 후 정지 대기 중'}${note ? ` · ${note}` : ''}`,
      );
    } catch (error) {
      setMotionRunMessage(`정지 요청 실패: ${error?.message || error}`);
    } finally {
      motionRunLoading = false;
      renderMotionRunPanel();
    }
  }

  function motionAutomationSettings() {
    const repeatMode = String(
      el.motionAutomationRepeatMode?.value
      || motionRunStatus?.automation?.repeat_mode
      || 'reinitialize',
    );
    const dwellSec = Number(el.motionAutomationDwellSec?.value);
    const motionFileId = motionRunSelectedMotionFile()?.filename
      || motionRunStatus?.automation?.motion_file_id
      || '';
    const mappingFileId = selectedMappingFile()?.filename
      || selectedMappingId
      || motionRunStatus?.automation?.mapping_file_id
      || '';
    return {
      repeat_mode: repeatMode,
      dwell_sec: Number.isFinite(dwellSec) && dwellSec >= 0 ? dwellSec : 0,
      motion_file_id: motionFileId,
      mapping_file_id: mappingFileId,
    };
  }

  /** 반복 방식·대기 시간을 저장한다 · 부팅 자동 재생은 없앴다 · §6-134 */
  async function saveMotionAutomation() {
    motionRunLoading = true;
    renderMotionRunPanel();
    try {
      const payload = await configureMotionAutomation(motionAutomationSettings());
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
    el.exportMotionFileToStudioButton?.addEventListener('click', exportSelectedFileToStudio);
    el.downloadMotionFileButton?.addEventListener('click', downloadSelectedMotionFile);
    if (el.deleteMotionFileButton) {
      el.deleteMotionFileButton.addEventListener('click', deleteSelectedFile);
    }
    if (el.motionRunCheckButton) {
      el.motionRunCheckButton.addEventListener('click', checkCurrentMotionRun);
    }
    // **이 버튼들은 언제나 이 PC 것이다** · §6-100
    //
    // 전에는 같은 버튼이 `실행 대상` 에 따라 이 PC 를 돌리기도, 참가한 PC
    // 전부에게 시작 신호를 보내기도 했다 · 모터가 실제로 움직이는 버튼에서
    // 그 애매함은 위험하다 · 그룹 실행은 `PC 연동 설정` 탭에 따로 있다.
    if (el.motionRunInitializeButton) {
      el.motionRunInitializeButton.addEventListener(
        'click', () => initializeCurrentMotionRun(),
      );
    }
    if (el.motionRunStartButton) {
      el.motionRunStartButton.addEventListener(
        'click', () => startCurrentMotionRun('once'),
      );
    }
    if (el.motionRunContinuousStartButton) {
      el.motionRunContinuousStartButton.addEventListener(
        'click', () => startCurrentMotionRun('continuous'),
      );
    }
    if (el.motionRunStopButton) {
      el.motionRunStopButton.addEventListener(
        'click', () => stopCurrentMotionRun(),
      );
    }
    if (el.motionRunStopAfterButton) {
      el.motionRunStopAfterButton.addEventListener(
        'click', () => stopCurrentMotionRunAfterCycle(),
      );
    }
    if (el.motionRunRefreshButton) {
      el.motionRunRefreshButton.addEventListener('click', refreshMotionRunStatus);
    }
    // 반복 방식은 고치는 즉시 저장한다 · 전에는 「부팅 시 자동 재생」이
    // 켜져 있을 때만 저장돼서, 꺼 두면 고친 값이 다음 실행에 안 갔다 · §6-134
    el.motionAutomationRepeatMode?.addEventListener('change', () => {
      renderMotionAutomation();
      void saveMotionAutomation();
    });
    el.motionAutomationDwellSec?.addEventListener('change', () => {
      void saveMotionAutomation();
    });
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
    el.addMotionIdButton?.addEventListener('click', addMotionId);
    el.generateMotionIdsButton?.addEventListener('click', generateMotionIdsFromMotors);
    if (el.saveMotionMappingButton) {
      el.saveMotionMappingButton.addEventListener('click', saveCurrentMapping);
    }
    el.resetMotionMappingButton?.addEventListener('click', resetCurrentMapping);
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
    render,
    renderRuntimeState,
    showTab: (tab) => {
      renderMotionTabs(tab);
      render();
    },
  };
}
