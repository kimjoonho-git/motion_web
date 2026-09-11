import { escapeHtml } from './format.js?v=20260911122020';
import { motionStudioEditorValueBounds } from './motion_studio_editor_math.js?v=20260911122020';
import { motionStudioPointCurvePreview } from './motion_studio_point_model.js?v=20260911122020';
import { MOTION_STUDIO_PERIOD_SEC } from './motion_studio_constants.js?v=20260911122020';
import {
  motionStudioDisplaySegments,
  motionStudioEditorIssueTimes,
  motionStudioLayerTracks,
  motionStudioVisiblePoints,
} from './motion_studio_tracks.js?v=20260911122020';

export {
  motionStudioCompositionTracks,
  motionStudioDisplaySegments,
  motionStudioEditorIssueTimes,
  motionStudioLayerTracks,
  motionStudioSampleTrack,
  motionStudioVisiblePoints,
} from './motion_studio_tracks.js?v=20260911122020';

function pointCurves(layer) {
  return Array.isArray(layer?.point_curves) ? layer.point_curves : [];
}

export function motionStudioZeroAxisY(minValue, maxValue, top, plotHeight) {
  const minimum = Number(minValue);
  const maximum = Number(maxValue);
  const plotTop = Number(top);
  const height = Number(plotHeight);
  if (
    !Number.isFinite(minimum)
    || !Number.isFinite(maximum)
    || !Number.isFinite(plotTop)
    || !Number.isFinite(height)
    || maximum <= minimum
    || height <= 0
    || minimum > 0
    || maximum < 0
  ) return null;
  return plotTop + ((maximum / (maximum - minimum)) * height);
}

function drawZeroValueAxis(context, padding, plotWidth, plotHeight, minValue, maxValue) {
  const y = motionStudioZeroAxisY(
    minValue,
    maxValue,
    padding.top,
    plotHeight,
  );
  if (!Number.isFinite(y)) return;
  context.save();
  context.strokeStyle = '#8fa0b1';
  context.lineWidth = 0.8;
  context.beginPath();
  context.moveTo(padding.left, y);
  context.lineTo(padding.left + plotWidth, y);
  context.stroke();
  context.fillStyle = '#65788a';
  context.font = '10px sans-serif';
  context.fillText('0°', Math.max(4, padding.left - 25), y - 4);
  context.restore();
}

/** 그래프가 덮어야 할 시간 · 녹화가 데이터 끝을 지나면 그만큼 늘어난다 · §6-79
 *
 * 추가 녹화는 **녹화된 것이 끝난 뒤**가 본무대다 · 시간축을 데이터 길이에
 * 묶어 두면 그 순간부터 플레이헤드가 오른쪽 끝에 붙어 버리고, 사용자는 지금이
 * 몇 초인지 알 수 없게 된다.
 */
/** 잠금 띠 · 재생이 쥔 구간을 축마다 한 줄로 올린다 · §6-79
 *
 * 곡선은 모든 축이 한 판에 겹쳐 그려진다 · 잠금까지 곡선 위에 칠하면 축이 둘만
 * 돼도 무엇이 잠긴 건지 읽을 수 없다. 축마다 얇은 줄을 따로 주고 곡선과 같은
 * 색을 쓴다 · 색으로 어느 축인지 잇는다.
 *
 * 띠가 있는 구간은 **그 축을 MIDI 로 만져도 기록되지 않는다** · 빈 구간이
 * 녹화할 수 있는 시간이다.
 */
function drawOwnedSpanLanes({
  context, padding, plotWidth, maxTime, colors, motionIds, ownedSpans,
}) {
  if (!ownedSpans || !motionIds.length || maxTime <= 0) return 0;
  const laneHeight = 5;
  const laneGap = 2;
  let drawn = 0;
  motionIds.forEach((motionId, index) => {
    const spans = ownedSpans[String(motionId)];
    if (!Array.isArray(spans) || !spans.length) return;
    const top = padding.top + (drawn * (laneHeight + laneGap)) + 2;
    context.save();
    context.fillStyle = colors[index % colors.length];
    context.globalAlpha = 0.3;
    spans.forEach((span) => {
      const start = Math.max(0, Number(span?.[0]) || 0);
      const end = Math.max(start, Number(span?.[1]) || 0);
      const left = padding.left + ((Math.min(start, maxTime) / maxTime) * plotWidth);
      const right = padding.left + ((Math.min(end, maxTime) / maxTime) * plotWidth);
      context.fillRect(left, top, Math.max(2, right - left), laneHeight);
    });
    context.restore();
    drawn += 1;
  });
  return drawn;
}


export function motionStudioGraphTimeSpan(dataDurationSec, playback = {}) {
  const data = Math.max(0, Number(dataDurationSec) || 0);
  if (!playback.showPlayhead) return data;
  const playheadTime = Math.max(0, Number(playback.playheadTime) || 0);
  if (playheadTime <= data) return data;
  // 딱 플레이헤드까지만 늘리면 플레이헤드는 늘 오른쪽 끝에 있다 · 시간이
  // 흐르는 것이 안 보인다. 한 칸씩 앞질러 늘려 플레이헤드가 칸 안을 지나가게
  // 한다 · 매 프레임 다시 그리지 않으니 곡선도 덜 흔들린다.
  const chunk = Math.max(5, data);
  return data + ((Math.floor((playheadTime - data) / chunk) + 1) * chunk);
}

export function drawMotionStudioLayerGraph({
  canvas,
  playhead,
  tracks,
  baseTracks = null,
  playback,
  ownedSpans = null,
  timeSpan = 0,
  sampleIntervalSec = 0,
  updatePlayhead = () => {},
  devicePixelRatio = globalThis.devicePixelRatio || 1,
}) {
  if (!canvas) return false;
  const width = Math.max(520, Math.floor(canvas.getBoundingClientRect().width || 760));
  const height = 320;
  const ratio = devicePixelRatio || 1;
  canvas.width = Math.floor(width * ratio);
  canvas.height = Math.floor(height * ratio);
  const context = canvas.getContext('2d');
  context.setTransform(ratio, 0, 0, ratio, 0, 0);
  context.clearRect(0, 0, width, height);
  let pointCount = 0;
  let maxTime = MOTION_STUDIO_PERIOD_SEC;
  let minValue = 0;
  let maxValue = 0;
  // 시간축은 **받아서 쓴다** · 여기서 따로 계산하면 플레이헤드와 어긋나
  // 재생 표시가 튄다 · 부르는 쪽이 한 번만 셈한다 · §6-83
  maxTime = Math.max(maxTime, Math.max(0, Number(timeSpan) || 0));
  for (const points of [...tracks.values(), ...(baseTracks?.values() || [])]) {
    pointCount += points.length;
    for (const point of points) {
      maxTime = Math.max(maxTime, Number(point.timeSec) || 0);
      minValue = Math.min(minValue, Number(point.value) || 0);
      maxValue = Math.max(maxValue, Number(point.value) || 0);
    }
  }
  if (!pointCount) {
    playhead?.classList.add('hidden');
    context.fillStyle = '#5d6b78';
    context.font = '13px sans-serif';
    context.fillText('그래프 데이터 없음', 16, 28);
    return true;
  }
  const padding = { left: 52, right: 18, top: 18, bottom: 34 };
  const plotWidth = width - padding.left - padding.right;
  const plotHeight = height - padding.top - padding.bottom;
  if (Math.abs(maxValue - minValue) < 1e-9) {
    minValue -= 1;
    maxValue += 1;
  }
  context.strokeStyle = '#d9e0e7';
  context.lineWidth = 1;
  context.strokeRect(padding.left, padding.top, plotWidth, plotHeight);
  drawZeroValueAxis(context, padding, plotWidth, plotHeight, minValue, maxValue);
  context.fillStyle = '#5d6b78';
  context.font = '11px sans-serif';
  context.fillText(`${maxValue.toFixed(2)}°`, 4, padding.top + 4);
  context.fillText(`${minValue.toFixed(2)}°`, 4, padding.top + plotHeight);
  context.fillText('0초', padding.left, height - 10);
  context.fillText(`${maxTime.toFixed(3)}초`, width - padding.right - 58, height - 10);
  const colors = ['#1f6feb', '#d97706', '#16803c', '#a23ab7', '#d33b3b', '#0f8b8d'];
  // 바탕 곡선 · 추가 녹화 중 **지금 재생되고 있는 기존 레이어** · §6-83
  //
  // 녹화가 시작되면 그래프가 새로 기록되는 것만 보여 주고 기존 모션이 사라졌다 ·
  // 그런데 추가 녹화는 그 기존 모션을 보면서 얹는 일이다 · 사라지면 언제
  // 얹어야 할지 알 수가 없다.
  drawTrackLines({
    context, padding, plotWidth, plotHeight, maxTime, minValue, maxValue,
    tracks: baseTracks, colors, dashed: true, alpha: 0.4,
  });
  drawOwnedSpanLanes({
    context, padding, plotWidth, maxTime, colors,
    motionIds: [...tracks.keys()],
    ownedSpans,
  });
  drawTrackLines({
    context, padding, plotWidth, plotHeight, maxTime, minValue, maxValue,
    tracks, colors, dashed: false, alpha: 1,
    // 솎아낸 미리보기는 점 간격이 20ms 가 아니다 · §6-84
    gapSec: Math.max(0.031, (Number(sampleIntervalSec) || 0) * 1.5),
  });
  updatePlayhead(playback);
  return true;
}


/** 곡선을 그린다 · 바탕(기존 레이어)은 점선으로 옅게, 새로 녹화되는 것은 실선. */
function drawTrackLines({
  context, padding, plotWidth, plotHeight, maxTime, minValue, maxValue,
  tracks, colors, dashed, alpha, gapSec = 0.031,
}) {
  if (!tracks || !tracks.size || maxTime <= 0) return;
  context.save();
  context.globalAlpha = alpha;
  if (dashed) context.setLineDash([5, 4]);
  [...tracks.entries()].forEach(([, points], index) => {
    context.strokeStyle = colors[index % colors.length];
    context.lineWidth = dashed ? 1.5 : 2;
    motionStudioDisplaySegments(
      points, Math.max(400, plotWidth * 2), gapSec,
    ).forEach((segment) => {
      context.beginPath();
      segment.forEach((point, pointIndex) => {
        const x = padding.left + ((point.timeSec / maxTime) * plotWidth);
        const y = padding.top + (((maxValue - point.value) / (maxValue - minValue)) * plotHeight);
        if (pointIndex === 0) context.moveTo(x, y);
        else context.lineTo(x, y);
      });
      context.stroke();
    });
  });
  context.restore();
}

export function drawMotionStudioEditorGraph({
  editor,
  canvas,
  legend,
  originalTrackMap = null,
  workingTrackMap = null,
  selectedMotionIds = [],
  operation = '',
  selectionStartText = '',
  selectionEndText = '',
  devicePixelRatio = globalThis.devicePixelRatio || 1,
}) {
  if (!editor || !canvas) return false;
  const selected = new Set(selectedMotionIds);
  const originalTracks = originalTrackMap || motionStudioLayerTracks(editor.original);
  const displayedLayer = editor.preview || editor.working;
  const workingTracks = workingTrackMap || motionStudioLayerTracks(displayedLayer);
  const ids = [...new Set([...originalTracks.keys(), ...workingTracks.keys()])]
    .filter((motionId) => selected.has(motionId));
  const draftPreview = (
    operation === 'point_curve'
    && editor.pointDraft && selected.has(editor.pointDraft.motion_id)
  ) ? motionStudioPointCurvePreview(
      editor.pointDraft.points,
      editor.pointDraft.interpolation_order || editor.pointCurveOrder,
    ) : [];
  const canvasRect = canvas.getBoundingClientRect();
  const width = Math.max(1, Math.floor(canvasRect.width || 900));
  const height = Math.max(1, Math.floor(canvasRect.height || 320));
  const ratio = devicePixelRatio || 1;
  canvas.width = Math.floor(width * ratio);
  canvas.height = Math.floor(height * ratio);
  const context = canvas.getContext('2d');
  context.setTransform(ratio, 0, 0, ratio, 0, 0);
  context.clearRect(0, 0, width, height);
  const padding = { left: 62, right: 22, top: 22, bottom: 42 };
  const plotWidth = width - padding.left - padding.right;
  const plotHeight = height - padding.top - padding.bottom;
  const viewStart = Math.max(0, Number(editor.viewStart || 0));
  const viewEnd = Math.max(
    viewStart + MOTION_STUDIO_PERIOD_SEC,
    Number(editor.viewEnd || MOTION_STUDIO_PERIOD_SEC),
  );
  const visiblePoints = ids.flatMap((motionId) => [
    ...(workingTracks.get(motionId) || []), ...(originalTracks.get(motionId) || []),
  ].filter((point) => point.timeSec >= viewStart - 1e-9 && point.timeSec <= viewEnd + 1e-9))
    .concat(draftPreview.filter(
      (point) => point.timeSec >= viewStart - 1e-9 && point.timeSec <= viewEnd + 1e-9,
    ));
  const valueSource = visiblePoints.length ? visiblePoints : ids.flatMap((id) => [
    ...(workingTracks.get(id) || []), ...(originalTracks.get(id) || []),
  ]);
  const automaticMinValue = valueSource.length
    ? Math.min(0, ...valueSource.map((point) => point.value)) : -1;
  const automaticMaxValue = valueSource.length
    ? Math.max(0, ...valueSource.map((point) => point.value)) : 1;
  const { minValue, maxValue } = motionStudioEditorValueBounds(
    automaticMinValue,
    automaticMaxValue,
    editor.valueScale,
    editor.valueOffset,
    editor.valueRangeLock || editor.valueView,
  );
  const xFor = (timeSec) => padding.left
    + (((timeSec - viewStart) / (viewEnd - viewStart)) * plotWidth);
  const yFor = (value) => padding.top
    + (((maxValue - value) / (maxValue - minValue)) * plotHeight);
  const timeFor = (x) => viewStart
    + (((x - padding.left) / plotWidth) * (viewEnd - viewStart));
  const valueFor = (y) => maxValue
    - (((y - padding.top) / plotHeight) * (maxValue - minValue));
  editor.graphMetrics = {
    padding, plotWidth, plotHeight, width, height,
    viewStart, viewEnd, minValue, maxValue, xFor, yFor, timeFor, valueFor,
  };
  context.fillStyle = '#fff';
  context.fillRect(0, 0, width, height);
  pointCurves(displayedLayer)
    .filter((curve) => selected.has(curve.motion_id))
    .forEach((curve, index) => {
      const points = curve.points || [];
      const startSec = Number(points[0]?.time_sec);
      const endSec = Number(points[points.length - 1]?.time_sec);
      if (!Number.isFinite(startSec) || !Number.isFinite(endSec)) return;
      const left = Math.max(viewStart, startSec);
      const right = Math.min(viewEnd, endSec);
      if (right < left) return;
      context.fillStyle = index % 2
        ? 'rgba(126, 87, 194, 0.08)'
        : 'rgba(31, 111, 235, 0.08)';
      context.fillRect(
        xFor(left),
        padding.top,
        Math.max(1, xFor(right) - xFor(left)),
        plotHeight,
      );
      context.fillStyle = '#526579';
      context.font = '11px sans-serif';
      context.fillText(`포인트 데이터 ${index + 1}`, xFor(left) + 5, padding.top + 14);
    });
  const selectionStart = Number(selectionStartText);
  const selectionEnd = Number(selectionEndText);
  if (Number.isFinite(editor.rangeSelection?.start?.timeSec)
    && editor.rangeSelection?.phase === 'awaiting_end') {
    const anchor = Math.max(
      viewStart,
      Math.min(viewEnd, editor.rangeSelection.start.timeSec),
    );
    context.strokeStyle = '#1f6feb';
    context.lineWidth = 2;
    context.beginPath();
    context.moveTo(xFor(anchor), padding.top);
    context.lineTo(xFor(anchor), padding.top + plotHeight);
    context.stroke();
  } else if (
    selectionStartText && selectionEndText
    && Number.isFinite(selectionStart) && Number.isFinite(selectionEnd)
  ) {
    const shadeStart = Math.max(viewStart, Math.min(selectionStart, selectionEnd));
    const shadeEnd = Math.min(viewEnd, Math.max(selectionStart, selectionEnd));
    if (shadeEnd >= shadeStart) {
      context.fillStyle = 'rgba(31, 111, 235, 0.10)';
      context.fillRect(
        xFor(shadeStart),
        padding.top,
        Math.max(1, xFor(shadeEnd) - xFor(shadeStart)),
        plotHeight,
      );
    }
  }
  context.strokeStyle = '#d9e0e7';
  context.strokeRect(padding.left, padding.top, plotWidth, plotHeight);
  drawZeroValueAxis(context, padding, plotWidth, plotHeight, minValue, maxValue);
  context.fillStyle = '#5d6b78';
  context.font = '11px sans-serif';
  context.fillText(`${maxValue.toFixed(2)}°`, 6, padding.top + 4);
  context.fillText(`${minValue.toFixed(2)}°`, 6, padding.top + plotHeight);
  context.fillText(`${viewStart.toFixed(3)}초`, padding.left, height - 12);
  context.fillText(`${viewEnd.toFixed(3)}초`, width - padding.right - 66, height - 12);
  const colors = ['#1f6feb', '#d97706', '#16803c', '#a23ab7', '#d33b3b', '#0f8b8d'];
  const drawTracks = (tracks, dashed, alpha) => {
    ids.forEach((motionId, colorIndex) => {
      const points = tracks.get(motionId) || [];
      context.strokeStyle = colors[colorIndex % colors.length];
      context.globalAlpha = alpha;
      context.lineWidth = dashed ? 1.3 : 2.2;
      context.setLineDash(dashed ? [5, 4] : []);
      const visible = motionStudioVisiblePoints(points, viewStart, viewEnd);
      motionStudioDisplaySegments(visible, Math.max(400, plotWidth * 2)).forEach((segment) => {
        context.beginPath();
        segment.forEach((point, pointIndex) => {
          const x = xFor(point.timeSec);
          const y = yFor(point.value);
          if (pointIndex === 0) context.moveTo(x, y);
          else context.lineTo(x, y);
        });
        context.stroke();
      });
    });
  };
  drawTracks(originalTracks, true, 0.4);
  drawTracks(workingTracks, false, 1);
  context.globalAlpha = 1;
  context.setLineDash([]);
  if (draftPreview.length) {
    const colorIndex = ids.indexOf(editor.pointDraft.motion_id);
    context.beginPath();
    context.strokeStyle = colors[(colorIndex < 0 ? 0 : colorIndex) % colors.length];
    context.lineWidth = 3;
    context.setLineDash([3, 2]);
    let started = false;
    draftPreview.forEach((point) => {
      if (point.timeSec < viewStart - 1e-9 || point.timeSec > viewEnd + 1e-9) return;
      const x = xFor(point.timeSec);
      const y = yFor(point.value);
      if (!started) {
        context.moveTo(x, y);
        started = true;
      } else {
        context.lineTo(x, y);
      }
    });
    if (started) context.stroke();
    context.setLineDash([]);
  }
  let displayedCurves = pointCurves(displayedLayer).map(
    (curve) => structuredClone(curve),
  );
  if (editor.pointDraft && operation === 'point_curve') {
    displayedCurves = displayedCurves.filter(
      (curve) => curve.curve_id !== editor.pointDraft.curve_id,
    );
    displayedCurves.push(editor.pointDraft);
  }
  editor.pointHitTargets = [];
  editor.handleHitTargets = [];
  displayedCurves.filter((curve) => selected.has(curve.motion_id)).forEach((curve) => {
    const colorIndex = ids.indexOf(curve.motion_id);
    const color = colors[(colorIndex < 0 ? 0 : colorIndex) % colors.length];
    (curve.points || []).forEach((point) => {
      const timeSec = Number(point.time_sec);
      const value = Number(point.value_deg);
      if (!Number.isFinite(timeSec) || !Number.isFinite(value)) return;
      if (timeSec < viewStart - 1e-9 || timeSec > viewEnd + 1e-9) return;
      const x = xFor(timeSec);
      const y = yFor(value);
      const isSelected = point.point_id === editor.selectedPointId
        && curve.curve_id === editor.pointDraft?.curve_id;
      context.beginPath();
      context.fillStyle = '#fff';
      context.strokeStyle = color;
      context.lineWidth = isSelected ? 3 : 2;
      context.arc(x, y, isSelected ? 6 : 4.5, 0, Math.PI * 2);
      context.fill();
      context.stroke();
      editor.pointHitTargets.push({ x, y, curve, point });
      if (!isSelected) return;
      [['in', point.in_handle], ['out', point.out_handle]].forEach(([side, handle]) => {
        const dt = Number(handle?.dt_sec);
        const dv = Number(handle?.dv_deg);
        if (!Number.isFinite(dt) || !Number.isFinite(dv) || Math.abs(dt) < 1e-9) return;
        const handleX = xFor(timeSec + dt);
        const handleY = yFor(value + dv);
        context.beginPath();
        context.strokeStyle = '#65788a';
        context.lineWidth = 1.4;
        context.moveTo(x, y);
        context.lineTo(handleX, handleY);
        context.stroke();
        context.beginPath();
        context.fillStyle = '#1f6feb';
        context.strokeStyle = '#fff';
        context.arc(handleX, handleY, 5, 0, Math.PI * 2);
        context.fill();
        context.stroke();
        editor.handleHitTargets.push({ x: handleX, y: handleY, side, point });
      });
    });
  });
  const displayedValidation = editor.previewValidation || editor.validation;
  const issueTimes = motionStudioEditorIssueTimes(displayedValidation, [...selected]);
  context.strokeStyle = '#d33b3b';
  context.setLineDash([4, 3]);
  issueTimes.forEach((timeSec) => {
    if (timeSec < viewStart || timeSec > viewEnd) return;
    const x = xFor(timeSec);
    context.beginPath();
    context.moveTo(x, padding.top);
    context.lineTo(x, padding.top + plotHeight);
    context.stroke();
  });
  context.setLineDash([]);
  const candidate = editor.pendingPointCandidate;
  if (
    candidate
    && selected.has(String(candidate.motionId || ''))
    && Number.isFinite(Number(candidate.timeSec))
    && Number.isFinite(Number(candidate.valueDeg))
    && candidate.timeSec >= viewStart - 1e-9
    && candidate.timeSec <= viewEnd + 1e-9
  ) {
    const candidateX = xFor(candidate.timeSec);
    const candidateY = yFor(candidate.valueDeg);
    context.beginPath();
    context.fillStyle = '#fff';
    context.strokeStyle = '#d97706';
    context.lineWidth = 2;
    context.setLineDash([3, 2]);
    context.arc(candidateX, candidateY, 7, 0, Math.PI * 2);
    context.fill();
    context.stroke();
    context.setLineDash([]);
    context.fillStyle = '#8a4b08';
    context.fillText(
      '추가 후보',
      Math.min(width - 60, candidateX + 10),
      Math.max(14, candidateY - 9),
    );
  }
  if (editor.cursor) {
    const { x, y, timeSec, value } = editor.cursor;
    context.strokeStyle = '#596775';
    context.lineWidth = 1;
    context.setLineDash([3, 3]);
    context.beginPath();
    context.moveTo(x, padding.top);
    context.lineTo(x, padding.top + plotHeight);
    context.moveTo(padding.left, y);
    context.lineTo(padding.left + plotWidth, y);
    context.stroke();
    context.setLineDash([]);
    context.fillStyle = '#263442';
    context.fillText(`${timeSec.toFixed(3)}초`, Math.min(width - 90, x + 5), height - 24);
    context.fillText(`${value.toFixed(3)}°`, 5, Math.max(12, Math.min(height - 8, y)));
    if (editor.cursor.nearest) {
      const nearest = editor.cursor.nearest;
      context.beginPath();
      context.fillStyle = '#d33b3b';
      context.arc(xFor(nearest.timeSec), yFor(nearest.value), 4, 0, Math.PI * 2);
      context.fill();
    }
  }
  if (legend) {
    legend.innerHTML = ids.map((motionId, index) => (
      `<span><i style="background:${colors[index % colors.length]}"></i>${escapeHtml(motionId)}</span>`
    )).join('') + (editor.preview
      ? '<span>점선: 저장 원본 · 실선: 결과 미리보기</span>'
      : '<span>점선: 저장 원본 · 실선: 현재 작업본</span>');
  }
  return true;
}
