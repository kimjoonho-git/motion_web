import {
  checkMotionRun,
  deleteMotionMapping,
  deleteMotionFile,
  fetchMotionFile,
  fetchMotionFiles,
  fetchMotionMapping,
  fetchMotionMappings,
  fetchMotionRunStatus,
  initializeMotionRun,
  saveMotionMapping,
  startMotionRun,
  stopMotionRun,
  uploadMotionFile,
  validateMotionMapping,
} from './api.js?v=20260706-motion-safety';
import {
  displayText,
  formatInt,
  formatNumber,
  normalizeMotorTypeKey,
} from './format.js';

const MOTION_FILE_SIZE_LIMIT_BYTES = 10 * 1024 * 1024;
const MOTOR_AXIS_ANGLE_ALERT_DEG = 360.0;
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

function previewText(file, analysis) {
  const content = String(file?.content_preview || '');
  if (content.trim()) return content;
  const records = Array.isArray(analysis?.preview_records) ? analysis.preview_records : [];
  if (!records.length) return '미리보기 데이터가 없습니다';
  return records
    .slice(0, 40)
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
    context.fillText('No graph data', 16, 28);
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
  onWorkContextChange,
}) {
  let files = [];
  let selectedFileId = null;
  let selectedFile = null;
  let mappingFiles = [];
  let selectedMappingId = null;
  let mappingDraft = emptyMappingDraft();
  let mappingRawText = '';
  let mappingValidation = null;
  let mappingMotionFileDetail = null;
  let loading = false;
  let mappingLoading = false;
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

  function setMessage(message) {
    if (el.motionFileMessage) el.motionFileMessage.textContent = message;
  }

  function setMappingMessage(message) {
    if (el.motionMappingMessage) el.motionMappingMessage.textContent = message;
  }

  function setMotionRunMessage(message) {
    if (el.motionRunMessage) el.motionRunMessage.textContent = message;
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
    const motors = Array.isArray(getLatestState()?.motors) ? getLatestState().motors : [];
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
    return `Axis ${formatInt(motor.controller_index)} / ID ${motorIdText(motor)} / ${motor.motor_type_label || 'Unknown'} / ${motor.display_name || '-'}`;
  }

  function motorForAxis(axis) {
    if (axis === null || axis === undefined || axis === '') return null;
    return sortedRuntimeMotors().find((motor) => Number(motor.controller_index) === Number(axis)) || null;
  }

  function motorLimitInfo(row, detail) {
    const motor = motorForAxis(row?.motor_axis);
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
    return isDynamixelMotor(motorForAxis(row?.motor_axis));
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
    if (row.initial_enabled === false) {
      return 0.0;
    }
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
    if (row.initial_enabled === false) {
      return 0.0;
    }
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
    };
  }

  function motionRunPayload() {
    const initialMoveTimeSec = motionRunInitialMoveTimeSec();
    return {
      motion_file_id: mappingDraft.motion_file_id || selectedFileId || '',
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
    if (key === 'running' || key === 'initializing' || key === 'verifying' || key === 'stopping') return 'warn';
    if (key === 'ready' || key === 'initialized' || key === 'completed') return 'ok';
    return 'warn';
  }

  function motionRunStageKey(status) {
    const state = String(status?.state || 'idle');
    if (state === 'stopping' || state === 'stopped' || state === 'error') return state;
    if (MOTION_RUN_STAGES.some((stage) => stage.key === state)) return state;
    return 'idle';
  }

  function motionRunStageIndex(key) {
    return MOTION_RUN_STAGES.findIndex((stage) => stage.key === key);
  }

  function motionRunEffectiveProgress(status = motionRunStatus || {}) {
    const state = String(status?.state || 'idle');
    const progress = status?.progress || {};
    const lifecycle = status?.lifecycle || {};
    const nowSec = Date.now() / 1000;
    let elapsed = Number(progress.elapsed_sec);
    let duration = Number(progress.duration_sec);

    if (!Number.isFinite(elapsed)) elapsed = 0.0;
    if (!Number.isFinite(duration) || duration < 0) duration = 0.0;

    if (state === 'initializing') {
      const startedAt = Number(lifecycle.initial_started_at || status.phase_started_at || status.updated_at);
      const overrideDuration = motionRunInitialMoveTimeSec();
      if (overrideDuration !== null) duration = overrideDuration;
      if (Number.isFinite(startedAt) && startedAt > 0) elapsed = nowSec - startedAt;
    } else if (state === 'running') {
      const startedAt = Number(lifecycle.motion_started_at || status.phase_started_at || status.updated_at);
      const summaryDuration = Number(status?.summary?.duration_sec);
      duration = duration > 0 ? duration : (Number.isFinite(summaryDuration) ? summaryDuration : 0.0);
      if (Number.isFinite(startedAt) && startedAt > 0) {
        const runningElapsed = nowSec - startedAt;
        elapsed = status?.run_mode === 'continuous' && duration > 0
          ? runningElapsed % duration
          : runningElapsed;
      }
    } else if (state === 'verifying') {
      const summaryDuration = Number(status?.summary?.duration_sec);
      duration = duration > 0 ? duration : (Number.isFinite(summaryDuration) ? summaryDuration : 0.0);
      elapsed = duration;
    } else if (state === 'completed') {
      const summaryDuration = Number(status?.summary?.duration_sec);
      duration = duration > 0 ? duration : (Number.isFinite(summaryDuration) ? summaryDuration : 0.0);
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
    const extraState = currentKey === 'stopping' || currentKey === 'stopped' || currentKey === 'error'
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
      context.fillText('No motion graph data', 16, 28);
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
    if (state === 'running' || state === 'verifying' || state === 'completed') {
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
    const cursorLabel = state === 'running' || state === 'verifying' || state === 'completed'
      ? `${formatNumber(cursorTime, 2)}s`
      : motionRunStateText(state);
    context.fillText(cursorLabel, Math.min(cursorX + 6, width - 80), padTop + graphHeight - 8);

    if (messageEl) {
      const visibleText = `표시 ${series.length}/${allSeries.length}`;
      if (state === 'initializing') {
        messageEl.textContent = `초기 위치 이동 중 ${formatNumber(effective.elapsed_sec, 2)} / ${formatNumber(effective.duration_sec, 2)} s · ${visibleText}`;
      } else if (state === 'running') {
        messageEl.textContent = `모션 중 ${formatNumber(effective.elapsed_sec, 2)} / ${formatNumber(effective.duration_sec, 2)} s · ${visibleText}`;
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
    const allStateText = allVisible ? 'ON' : (noneVisible ? 'OFF' : '일부');
    const signature = `${fileId}|${series.map((item) => {
      const motionId = String(item.motion_id);
      return `${motionId}:${motionRunGraphHiddenIds.has(motionId) ? '0' : '1'}`;
    }).join(',')}`;
    if (signature === motionRunGraphToggleSignature) return;
    const allButton = `<button type="button" class="motion-run-graph-toggle ${allVisible ? 'active' : ''}" data-motion-run-graph-all="true" aria-pressed="${allVisible}">전체 · ${allStateText}</button>`;
    const axisButtons = series.map((item) => {
      const motionId = String(item.motion_id);
      const visible = !motionRunGraphHiddenIds.has(motionId);
      return `<button type="button" class="motion-run-graph-toggle ${visible ? 'active' : ''}" data-motion-run-graph-id="${displayText(motionId)}" aria-pressed="${visible}">${displayText(motionId)} · ${visible ? 'ON' : 'OFF'}</button>`;
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
        <td>${axis.initial_enabled === false ? '미사용' : targetText(axis.initial_motor_target_deg)}</td>
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

  function renderMotionRunPanel() {
    const payload = motionRunPayload();
    const status = motionRunStatus || {};
    const state = String(status.state || 'idle');
    const running = state === 'running' || state === 'initializing' || state === 'verifying' || state === 'stopping';
    const hasRequiredFiles = Boolean(payload.motion_file_id && payload.mapping_file_id);
    const startReady = state === 'initialized'
      && status.motion_file_id === payload.motion_file_id
      && status.mapping_file_id === payload.mapping_file_id;
    const continuousAvailable = status.capabilities?.continuous_run?.available === true;
    if (el.motionRunCheckButton) {
      el.motionRunCheckButton.disabled = motionRunLoading || !hasRequiredFiles || running;
    }
    if (el.motionRunInitializeButton) {
      el.motionRunInitializeButton.disabled = motionRunLoading || !hasRequiredFiles || running;
    }
    if (el.motionRunStartButton) {
      el.motionRunStartButton.disabled = motionRunLoading || !hasRequiredFiles || running || !startReady;
    }
    if (el.motionRunContinuousStartButton) {
      el.motionRunContinuousStartButton.disabled = motionRunLoading
        || !hasRequiredFiles || running || !startReady || !continuousAvailable;
      el.motionRunContinuousStartButton.title = continuousAvailable
        ? '정지 버튼을 누를 때까지 모션을 반복합니다'
        : (status.capabilities?.continuous_run?.reason || '실행 준비 검사가 필요합니다');
    }
    if (el.motionRunStopButton) {
      el.motionRunStopButton.disabled = motionRunLoading || !running;
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
        : !hasRequiredFiles
          ? '모션 파일과 매핑 파일을 선택하세요'
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
  }

  function renderMotionTabs(active = null) {
    if (!el.motionTabs) return;
    const current = active || el.motionTabs.querySelector('.active')?.dataset.motionTab || 'files';
    el.motionTabs.querySelectorAll('[data-motion-tab]').forEach((button) => {
      const isActive = button.dataset.motionTab === current;
      button.classList.toggle('active', isActive);
      button.setAttribute('aria-selected', isActive ? 'true' : 'false');
    });
    if (el.motionPanels) {
      el.motionPanels.forEach((panel) => {
        panel.classList.toggle('hidden', panel.dataset.motionPanel !== current);
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
    const allStateText = allVisible ? 'ON' : (noneVisible ? 'OFF' : '일부');
    const signature = `${fileId}|${series.map((item) => {
      const motionId = String(item.motion_id);
      return `${motionId}:${motionFileGraphHiddenIds.has(motionId) ? '0' : '1'}`;
    }).join(',')}`;
    if (signature === motionFileGraphToggleSignature) return;
    const allButton = `<button type="button" class="motion-run-graph-toggle ${allVisible ? 'active' : ''}" data-motion-file-graph-all="true" aria-pressed="${allVisible}">전체 · ${allStateText}</button>`;
    const axisButtons = series.map((item) => {
      const motionId = String(item.motion_id);
      const visible = !motionFileGraphHiddenIds.has(motionId);
      return `<button type="button" class="motion-run-graph-toggle ${visible ? 'active' : ''}" data-motion-file-graph-id="${displayText(motionId)}" aria-pressed="${visible}">${displayText(motionId)} · ${visible ? 'ON' : 'OFF'}</button>`;
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
        { label: 'Motion ID', value: formatInt(analysis.motion_id_count) },
        { label: '보간', value: interpolation.required ? '20ms 선형보간 필요' : '20ms 기준 통과' },
      ]);
    }
    if (el.motionFileValidation) el.motionFileValidation.innerHTML = validationHtml(analysis);
    if (el.motionFileMotionIdRows) el.motionFileMotionIdRows.innerHTML = motionIdRowsHtml(analysis);
    if (el.motionFilePreviewRows) el.motionFilePreviewRows.textContent = previewText(file, analysis);
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
      if (!row.enabled || row.motor_axis === null || row.motor_axis === undefined || row.motor_axis === '') {
        return counts;
      }
      const key = String(row.motor_axis);
      counts[key] = (counts[key] || 0) + 1;
      return counts;
    }, {});
  }

  function mappingRowStatus(row, duplicateCounts) {
    if (!row.enabled) return { text: '비활성', className: 'warn' };
    if (row.motor_axis === null || row.motor_axis === undefined || row.motor_axis === '') {
      return { text: '모터축 미선택', className: 'bad' };
    }
    if ((duplicateCounts[String(row.motor_axis)] || 0) > 1) {
      return { text: '중복 매칭', className: 'bad' };
    }
    if (!motorForAxis(row.motor_axis)) {
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
    const value = row.motor_axis === null || row.motor_axis === undefined ? '' : String(row.motor_axis);
    return (
      `<select class="wide-select" data-motion-mapping-field="motor_axis" data-motion-id="${displayText(row.motion_id)}">
        <option value="">선택 안함</option>
        ${motors.map((motor) => {
          const axis = String(motor.controller_index);
          const selected = axis === value ? ' selected' : '';
          return `<option value="${displayText(axis)}"${selected}>${displayText(motorOptionLabel(motor))}</option>`;
        }).join('')}
      </select>`
    );
  }

  function renderMappingRows() {
    if (!el.motionMappingRows) return;
    const rows = Array.isArray(mappingDraft.mappings) ? mappingDraft.mappings : [];
    if (!rows.length) {
      el.motionMappingRows.innerHTML = emptyRow(16, '모션 파일을 선택하고 Motion ID를 반영하세요');
      return;
    }
    const duplicateCounts = mappingDuplicateAxisCounts();
    el.motionMappingRows.innerHTML = rows.map((row) => {
      const status = mappingValidationRowStatus(row, mappingRowStatus(row, duplicateCounts));
      const initialMode = row.initial_mode || 'first_frame';
      row.reference_enabled = true;
      const referenceDisabled = false;
      const initialDisabled = row.initial_enabled === false;
      const initialTimeOverridden = motionRunInitialMoveTimeSec() !== null;
      const initialMoveTimeDisabled = initialDisabled || initialTimeOverridden;
      const firstFrameInitial = initialMode === 'first_frame';
      const initialPositionDisabled = initialDisabled || firstFrameInitial;
      const dynamixelGearFixed = isDynamixelMappingRow(row);
      const referencePositionValue = displayReferencePosition(row);
      const initialPositionValue = displayInitialPosition(row);
      const initialMoveTimeValue = displayInitialMoveTime(row);
      const gearRatioValue = mappingGearRatioValue(row);
      const referenceDisabledAttr = referenceDisabled ? ' disabled' : '';
      const initialDisabledAttr = initialDisabled ? ' disabled' : '';
      const initialMoveTimeDisabledAttr = initialMoveTimeDisabled
        ? ` disabled title="${initialTimeOverridden ? '모션 동작 탭의 초기 이동 시간이 일괄 적용됩니다' : '초기 위치 이동이 비활성화되어 있습니다'}"`
        : '';
      const initialPositionDisabledAttr = initialPositionDisabled ? ' disabled' : '';
      const gearRatioDisabledAttr = dynamixelGearFixed ? ' disabled title="Dynamixel은 감속비를 사용하지 않으며 1로 고정됩니다"' : '';
      return (
        `<tr data-motion-id="${displayText(row.motion_id)}">
          <td class="mono">${displayText(row.motion_id)}</td>
          <td><input type="checkbox" data-motion-mapping-field="enabled" ${row.enabled ? 'checked' : ''}></td>
          <td>${motorSelectHtml(row)}</td>
          <td class="mapping-number-cell ${dynamixelGearFixed ? 'mapping-disabled-cell' : ''}"><input class="numeric-input mapping-number-input" type="number" min="0.0001" step="0.0001" data-motion-mapping-field="gear_ratio" value="${displayText(gearRatioValue)}"${gearRatioDisabledAttr}></td>
          <td class="mapping-number-cell ${referenceDisabled ? 'mapping-disabled-cell' : ''}"><input class="numeric-input mapping-number-input" type="number" step="0.001" data-motion-mapping-field="reference_position_deg" value="${displayText(referencePositionValue)}"${referenceDisabledAttr}></td>
          <td class="${referenceDisabled ? 'mapping-disabled-cell' : ''}"><button class="mapping-mini-button" type="button" data-motion-mapping-action="capture_reference"${referenceDisabledAttr}>캡처</button></td>
          <td class="mapping-number-cell"><input class="numeric-input mapping-number-input" type="number" step="0.001" data-motion-mapping-field="motion_lower_deg" value="${displayText(row.motion_lower_deg)}"></td>
          <td class="mapping-number-cell"><input class="numeric-input mapping-number-input" type="number" step="0.001" data-motion-mapping-field="motion_upper_deg" value="${displayText(row.motion_upper_deg)}"></td>
          <td><input type="checkbox" data-motion-mapping-field="initial_enabled" ${row.initial_enabled !== false ? 'checked' : ''}></td>
          <td class="${initialDisabled ? 'mapping-disabled-cell' : ''}">
            <select class="compact-select" data-motion-mapping-field="initial_mode"${initialDisabledAttr}>
              <option value="first_frame"${initialMode === 'first_frame' ? ' selected' : ''}>첫 프레임</option>
              <option value="manual"${initialMode === 'manual' ? ' selected' : ''}>수동</option>
            </select>
          </td>
          <td class="mapping-number-cell ${initialPositionDisabled ? 'mapping-disabled-cell' : ''}"><input class="numeric-input mapping-number-input" type="number" step="0.001" data-motion-mapping-field="initial_motion_position_deg" value="${displayText(initialPositionValue)}"${initialPositionDisabledAttr}></td>
          <td class="mapping-number-cell ${initialMoveTimeDisabled ? 'mapping-disabled-cell' : ''}"><input class="numeric-input mapping-number-input" type="number" min="0.001" step="0.001" data-motion-mapping-field="initial_move_time_sec" value="${displayText(initialMoveTimeValue)}"${initialMoveTimeDisabledAttr}></td>
          <td><input type="checkbox" data-motion-mapping-field="invert" ${row.invert ? 'checked' : ''}></td>
          <td class="mapping-number-cell"><input class="numeric-input mapping-number-input" type="number" step="0.001" data-motion-mapping-field="offset_deg" value="${displayText(row.offset_deg)}"></td>
          <td class="mapping-number-cell"><input class="numeric-input mapping-number-input" type="number" step="0.0001" data-motion-mapping-field="scale" value="${displayText(row.scale)}"></td>
          <td><span class="motion-state-pill ${status.className}">${displayText(status.text)}</span></td>
        </tr>`
      );
    }).join('');
  }

  function renderUnusedMotors() {
    if (!el.motionMappingUnusedMotorRows) return;
    const usedAxes = new Set(
      mappingDraft.mappings
        .filter((row) => row.enabled && row.motor_axis !== null && row.motor_axis !== undefined && row.motor_axis !== '')
        .map((row) => String(row.motor_axis)),
    );
    const unused = sortedRuntimeMotors().filter((motor) => !usedAxes.has(String(motor.controller_index)));
    if (!unused.length) {
      el.motionMappingUnusedMotorRows.innerHTML = emptyRow(5, '미사용 모터축이 없습니다');
      return;
    }
    el.motionMappingUnusedMotorRows.innerHTML = unused.map((motor) => (
      `<tr>
        <td class="mono">${formatInt(motor.controller_index)}</td>
        <td class="mono">${displayText(motorIdText(motor))}</td>
        <td>${displayText(motor.motor_type_label || 'Unknown')}</td>
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
      const initialText = detail.initial_enabled === false
        ? '미사용'
        : row.initial_mode === 'manual'
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
            <th><span class="validation-head-label">Motion ID</span><span class="validation-head-unit">id</span></th>
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
    if (el.validateMotionMappingButton) el.validateMotionMappingButton.disabled = mappingLoading || !mappingDraft.mappings?.length;
    if (el.importMotionIdsButton) el.importMotionIdsButton.disabled = !mappingDraft.motion_file_id || mappingLoading;
    renderMappingRows();
    renderMappingValidation();
    renderUnusedMotors();
    if (el.motionMappingRawText) {
      el.motionMappingRawText.textContent = mappingRawText || '매핑 파일을 선택하거나 저장하면 YAML 원본이 표시됩니다';
    }
  }

  function renderRuntimeMappingState() {
    const activePanel = el.motionTabs?.querySelector('.active')?.dataset.motionTab;
    if (activePanel !== 'mapping') return;
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
        motor_axis: previous?.motor_axis ?? null,
        reference_enabled: previous?.reference_enabled ?? true,
        reference_position_deg: numericOr(previous?.reference_position_deg, 0.0),
        motion_lower_deg: numericOr(previous?.motion_lower_deg, -180.0),
        motion_upper_deg: numericOr(previous?.motion_upper_deg, 180.0),
        initial_enabled: previous?.initial_enabled ?? true,
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
    if (!mappingDraft.name?.trim()) return '매핑 이름이 필요합니다';
    if (!mappingDraft.motion_file_id?.trim()) return '모션 파일 선택이 필요합니다';
    const rows = Array.isArray(mappingDraft.mappings) ? mappingDraft.mappings : [];
    if (!rows.length) return 'Motion ID를 먼저 반영하세요';
    const duplicateCounts = mappingDuplicateAxisCounts();
    const duplicateAxis = Object.entries(duplicateCounts).find(([, count]) => count > 1);
    if (duplicateAxis) return `동일한 Motor Axis가 중복 사용되었습니다: Axis ${duplicateAxis[0]}`;
    const enabledWithoutAxis = rows.find((row) => row.enabled && (row.motor_axis === null || row.motor_axis === undefined || row.motor_axis === ''));
    if (enabledWithoutAxis) return `활성화된 Motion ID에 Motor Axis가 없습니다: ${enabledWithoutAxis.motion_id}`;
    return '';
  }

  async function loadMappings(selectMappingId = selectedMappingId) {
    mappingLoading = true;
    setMappingMessage('매핑 목록 불러오는 중');
    renderMappingPanel();
    try {
      const payload = await fetchMotionMappings();
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
        mappingDraft = emptyMappingDraft();
        mappingRawText = '';
        mappingValidation = null;
      }
      setMappingMessage(payload.message || '매핑 목록 갱신 완료');
    } catch (error) {
      setMappingMessage(`매핑 목록 실패: ${error?.message || error}`);
    } finally {
      mappingLoading = false;
      renderMappingPanel();
    }
  }

  async function selectMapping(fileId) {
    selectedMappingId = fileId || null;
    if (!selectedMappingId) {
      mappingDraft = emptyMappingDraft();
      mappingRawText = '';
      mappingValidation = null;
      mappingMotionFileDetail = null;
      renderMappingPanel();
      return;
    }
    mappingLoading = true;
    setMappingMessage('매핑 파일 불러오는 중');
    renderMappingPanel();
    try {
      const payload = await fetchMotionMapping(selectedMappingId);
      mappingFiles = Array.isArray(payload.files) ? payload.files : mappingFiles;
      mappingDraft = payload.mapping || emptyMappingDraft();
      selectedMappingId = payload.file?.id || mappingDraft.file_id || selectedMappingId;
      mappingRawText = payload.content || '';
      mappingValidation = payload.validation || null;
      mappingMotionFileDetail = null;
      if (mappingDraft.motion_file_id) {
        await ensureMappingMotionFileDetail(mappingDraft.motion_file_id);
      }
      normalizeDynamixelGearRatios();
      setMappingMessage(payload.message || '매핑 파일 로드 완료');
    } catch (error) {
      setMappingMessage(`매핑 파일 실패: ${error?.message || error}`);
    } finally {
      mappingLoading = false;
      renderMappingPanel();
    }
  }

  function newMappingDraft() {
    const baseFile = selectedFile || files[0] || null;
    selectedMappingId = null;
    mappingRawText = '';
    mappingValidation = null;
    mappingMotionFileDetail = baseFile;
    mappingDraft = {
      ...emptyMappingDraft(),
      name: baseFile ? `${baseFile.filename.replace(/\.json$/i, '')}_mapping` : 'motion_mapping',
      motion_file_id: baseFile?.id || '',
      mappings: baseFile ? mappingRowsFromMotionFile(baseFile) : [],
    };
    setMappingMessage('새 매핑 작성 중');
    renderMappingPanel();
  }

  async function importMotionIds() {
    if (!mappingDraft.motion_file_id) {
      setMappingMessage('모션 파일을 먼저 선택하세요');
      return;
    }
    mappingLoading = true;
    setMappingMessage('Motion ID 반영 중');
    renderMappingPanel();
    try {
      const detail = await ensureMappingMotionFileDetail(mappingDraft.motion_file_id);
      if (!detail?.analysis?.motion_ids?.length) {
        setMappingMessage('선택 파일에 Motion ID가 없습니다');
        return;
      }
      mappingDraft.mappings = mappingRowsFromMotionFile(detail, mappingDraft.mappings);
      normalizeDynamixelGearRatios();
      if (!mappingDraft.name) {
        mappingDraft.name = `${detail.filename.replace(/\.json$/i, '')}_mapping`;
      }
      mappingRawText = '';
      mappingValidation = null;
      setMappingMessage(`${formatInt(mappingDraft.mappings.length)}개 Motion ID 반영 완료`);
    } catch (error) {
      setMappingMessage(`Motion ID 반영 실패: ${error?.message || error}`);
    } finally {
      mappingLoading = false;
      renderMappingPanel();
    }
  }

  async function saveCurrentMapping() {
    mappingLoading = true;
    setMappingMessage('매핑 저장 중');
    normalizeDynamixelGearRatios();
    renderMappingPanel();
    try {
      const payload = await saveMotionMapping({
        file_id: selectedMappingId || '',
        mapping: mappingDraft,
      });
      mappingValidation = payload.validation || null;
      if (payload.success === false) {
        mappingDraft = payload.mapping || mappingDraft;
        setMappingMessage(`매핑 저장 실패: ${payload.message || '검증 실패'}`);
        return;
      }
      mappingFiles = Array.isArray(payload.files) ? payload.files : mappingFiles;
      mappingDraft = payload.mapping || mappingDraft;
      selectedMappingId = payload.file?.id || mappingDraft.file_id || selectedMappingId;
      mappingRawText = payload.content || '';
      setMappingMessage(payload.message || '매핑 저장 완료');
    } catch (error) {
      setMappingMessage(`매핑 저장 실패: ${error?.message || error}`);
    } finally {
      mappingLoading = false;
      renderMappingPanel();
    }
  }

  async function deleteCurrentMapping() {
    if (!selectedMappingId) return;
    const confirmed = window.confirm(`선택한 모션축 매핑을 삭제합니다.\n${selectedMappingId}`);
    if (!confirmed) return;
    mappingLoading = true;
    setMappingMessage('매핑 삭제 중');
    renderMappingPanel();
    try {
      const payload = await deleteMotionMapping(selectedMappingId);
      mappingFiles = Array.isArray(payload.files) ? payload.files : [];
      selectedMappingId = null;
      mappingDraft = emptyMappingDraft();
      mappingRawText = '';
      mappingValidation = null;
      setMappingMessage(payload.message || '매핑 삭제 완료');
    } catch (error) {
      setMappingMessage(`매핑 삭제 실패: ${error?.message || error}`);
    } finally {
      mappingLoading = false;
      renderMappingPanel();
    }
  }

  async function validateCurrentMapping() {
    mappingLoading = true;
    setMappingMessage('매핑 설정 검증 중');
    normalizeDynamixelGearRatios();
    renderMappingPanel();
    try {
      const payload = await validateMotionMapping({
        file_id: selectedMappingId || '',
        mapping: mappingDraft,
      });
      mappingDraft = payload.mapping || mappingDraft;
      mappingValidation = payload.validation || null;
      setMappingMessage(payload.message || (payload.success ? '매핑 검증 완료' : '매핑 검증 실패'));
    } catch (error) {
      setMappingMessage(`매핑 검증 실패: ${error?.message || error}`);
    } finally {
      mappingLoading = false;
      renderMappingPanel();
    }
  }

  function updateMappingRow(motionId, field, value, checked = false) {
    const row = mappingDraft.mappings.find((item) => String(item.motion_id) === String(motionId));
    if (!row) return;
    if (field === 'enabled' || field === 'invert' || field === 'reference_enabled' || field === 'initial_enabled') {
      row[field] = Boolean(checked);
      if (field === 'reference_enabled' && !row.reference_enabled) {
        row.reference_position_deg = 0.0;
      }
      if (field === 'initial_enabled' && !row.initial_enabled) {
        row.initial_motion_position_deg = 0.0;
        row.initial_move_time_sec = 0.0;
      }
      if (field === 'initial_enabled' && row.initial_enabled) {
        if (!Number.isFinite(Number(row.initial_move_time_sec)) || Number(row.initial_move_time_sec) <= 0) {
          row.initial_move_time_sec = 5.0;
        }
        if ((row.initial_mode || 'first_frame') === 'first_frame') {
          const firstValue = firstMotionValueFor(row.motion_id);
          if (firstValue !== null) row.initial_motion_position_deg = firstValue;
        }
      }
    } else if (field === 'motor_axis') {
      row.motor_axis = value === '' ? null : Number(value);
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
    renderMappingPanel();
  }

  function captureReferencePosition(motionId) {
    const row = mappingDraft.mappings.find((item) => String(item.motion_id) === String(motionId));
    if (!row) return;
    const motor = motorForAxis(row.motor_axis);
    const position = motorPositionDeg(motor);
    if (position === null) {
      setMappingMessage(`현재 위치를 읽을 수 없습니다: Motion ID ${motionId}`);
      return;
    }
    row.reference_position_deg = position;
    row.reference_enabled = true;
    mappingRawText = '';
    mappingValidation = null;
    setMappingMessage(`Motion ID ${motionId} 기준점 캡처: ${formatNumber(position, 3)} deg`);
    renderMappingPanel();
  }

  async function loadFiles(selectFileId = selectedFileId) {
    loading = true;
    setMessage('파일 목록 불러오는 중');
    render();
    try {
      const payload = await fetchMotionFiles();
      files = Array.isArray(payload.files) ? payload.files : [];
      if (selectFileId && files.some((file) => file.id === selectFileId)) {
        await selectFile(selectFileId);
        return;
      }
      if (!selectedFileId && files.length) {
        await selectFile(files[0].id);
        return;
      }
      if (selectedFileId && !files.some((file) => file.id === selectedFileId)) {
        selectedFileId = null;
        selectedFile = null;
      }
      setMessage(payload.message || '파일 목록 갱신 완료');
    } catch (error) {
      setMessage(`파일 목록 실패: ${error?.message || error}`);
    } finally {
      loading = false;
      render();
    }
  }

  async function selectFile(fileId) {
    selectedFileId = fileId;
    loading = true;
    setMessage('파일 상세 불러오는 중');
    render();
    try {
      const payload = await fetchMotionFile(fileId);
      selectedFile = payload.file || null;
      files = Array.isArray(payload.files) ? payload.files : files;
      setMessage(payload.message || '파일 상세 갱신 완료');
    } catch (error) {
      selectedFile = null;
      setMessage(`파일 상세 실패: ${error?.message || error}`);
    } finally {
      loading = false;
      render();
    }
  }

  async function uploadSelectedFile() {
    const file = el.motionFileInput?.files?.[0];
    if (!file) {
      setMessage('업로드할 JSON 파일을 선택하세요');
      return;
    }
    if (file.size > MOTION_FILE_SIZE_LIMIT_BYTES) {
      setMessage(`업로드 실패: 파일 크기가 ${bytesText(MOTION_FILE_SIZE_LIMIT_BYTES)}를 초과합니다`);
      return;
    }
    loading = true;
    setMessage(`${file.name} 업로드 중`);
    render();
    try {
      const content = await file.text();
      const payload = await uploadMotionFile({
        filename: file.name,
        content,
      });
      if (payload.success === false) {
        files = Array.isArray(payload.files) ? payload.files : files;
        setMessage(`업로드 실패: ${payload.message || '서버가 파일을 저장하지 못했습니다'}`);
        return;
      }
      files = Array.isArray(payload.files) ? payload.files : [];
      selectedFile = payload.file || null;
      selectedFileId = selectedFile?.id || selectedFileId;
      if (payload.file) {
        setMessage(payload.message || '업로드 완료');
      } else {
        setMessage('업로드 응답에 저장 파일 정보가 없습니다. 목록 새로고침을 눌러 확인하세요');
      }
    } catch (error) {
      setMessage(`업로드 실패: ${error?.message || error}`);
    } finally {
      loading = false;
      render();
    }
  }

  async function deleteSelectedFile() {
    if (!selectedFileId) return;
    const confirmed = window.confirm(`선택한 모션 파일을 삭제합니다.\n${selectedFileId}`);
    if (!confirmed) return;
    loading = true;
    setMessage('파일 삭제 중');
    render();
    try {
      const payload = await deleteMotionFile(selectedFileId);
      files = Array.isArray(payload.files) ? payload.files : [];
      selectedFileId = files[0]?.id || null;
      selectedFile = null;
      if (selectedFileId) await selectFile(selectedFileId);
      setMessage(payload.message || '파일 삭제 완료');
    } catch (error) {
      setMessage(`삭제 실패: ${error?.message || error}`);
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
    const confirmed = window.confirm('매핑된 축을 초기 위치로 이동합니다.');
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
    } catch (error) {
      setMotionRunMessage(`초기 위치 이동 실패: ${error?.message || error}`);
    } finally {
      motionRunLoading = false;
      renderMotionRunPanel();
    }
  }

  async function startCurrentMotionRun(runMode = 'once') {
    const continuous = runMode === 'continuous';
    const confirmed = window.confirm(continuous
      ? '초기 위치 이동이 완료된 상태에서 연속 모션을 시작합니다. 정지 버튼을 누를 때까지 반복합니다.'
      : '현재 선택된 모션 파일과 매핑 파일로 모션을 1회 시작합니다.');
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
    } catch (error) {
      setMotionRunMessage(`모션 실행 실패: ${error?.message || error}`);
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

  function bindEvents() {
    if (el.motionTabs) {
      el.motionTabs.addEventListener('click', (event) => {
        const button = event.target.closest('[data-motion-tab]');
        if (!button) return;
        renderMotionTabs(button.dataset.motionTab || 'files');
        render();
      });
    }
    if (el.motionFileRows) {
      el.motionFileRows.addEventListener('click', (event) => {
        const target = event.target.closest('[data-motion-file-id]');
        if (!target) return;
        selectFile(target.dataset.motionFileId);
      });
    }
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
    if (el.uploadMotionFileButton) {
      el.uploadMotionFileButton.addEventListener('click', uploadSelectedFile);
    }
    if (el.refreshMotionFilesButton) {
      el.refreshMotionFilesButton.addEventListener('click', () => loadFiles());
    }
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
    if (el.motionRunRefreshButton) {
      el.motionRunRefreshButton.addEventListener('click', refreshMotionRunStatus);
    }
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
    if (el.motionFileInput) {
      el.motionFileInput.addEventListener('change', () => {
        const file = el.motionFileInput?.files?.[0];
        if (!file) return;
        setMessage(`${file.name} 선택됨 (${bytesText(file.size)})`);
      });
    }
    if (el.motionMappingSelect) {
      el.motionMappingSelect.addEventListener('change', () => {
        selectMapping(el.motionMappingSelect.value);
      });
    }
    if (el.refreshMotionMappingsButton) {
      el.refreshMotionMappingsButton.addEventListener('click', () => loadMappings());
    }
    if (el.newMotionMappingButton) {
      el.newMotionMappingButton.addEventListener('click', newMappingDraft);
    }
    if (el.validateMotionMappingButton) {
      el.validateMotionMappingButton.addEventListener('click', validateCurrentMapping);
    }
    if (el.saveMotionMappingButton) {
      el.saveMotionMappingButton.addEventListener('click', saveCurrentMapping);
    }
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
      });
    }
    if (el.motionMappingFileSelect) {
      el.motionMappingFileSelect.addEventListener('change', () => {
        mappingDraft.motion_file_id = el.motionMappingFileSelect.value;
        mappingMotionFileDetail = null;
        mappingRawText = '';
        mappingValidation = null;
        setMappingMessage('모션 파일이 변경되었습니다. Motion ID 반영을 눌러 목록을 갱신하세요');
        renderMappingPanel();
      });
    }
    if (el.motionMappingRows) {
      el.motionMappingRows.addEventListener('click', (event) => {
        const action = event.target?.dataset?.motionMappingAction;
        if (!action) return;
        const row = event.target.closest('tr[data-motion-id]');
        if (!row) return;
        if (action === 'capture_reference') {
          captureReferencePosition(row.dataset.motionId);
        }
      });
      el.motionMappingRows.addEventListener('change', (event) => {
        const field = event.target?.dataset?.motionMappingField;
        if (!field) return;
        const row = event.target.closest('tr[data-motion-id]');
        if (!row) return;
        updateMappingRow(row.dataset.motionId, field, event.target.value, event.target.checked);
      });
    }
  }

  return {
    bindEvents,
    fetchFiles: async () => {
      await loadFiles();
      await loadMappings();
    },
    getWorkContext,
    render,
    renderRuntimeState,
  };
}
