import {
  motionStudioEditorValueBounds,
  motionStudioPointCurvePreview,
} from './motion_studio_calculations.js?v=20260731-studio-performance-2';

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"]/g, (character) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;',
  }[character]));
}

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

export function motionStudioLayerTracks(layer) {
  const tracks = new Map();
  for (const frame of layer?.frames || []) {
    const timeSec = Number(frame.time_sec || 0);
    for (const [motionId, rawValue] of Object.entries(frame.values || {})) {
      const value = Number(rawValue);
      if (!Number.isFinite(timeSec) || !Number.isFinite(value)) continue;
      if (!tracks.has(motionId)) tracks.set(motionId, []);
      tracks.get(motionId).push({ timeSec, value });
    }
  }
  for (const points of tracks.values()) {
    points.sort((left, right) => left.timeSec - right.timeSec);
  }
  return tracks;
}

export function motionStudioSampleTrack(points, timeSec) {
  if (!points?.length) return null;
  let low = 0; let high = points.length;
  while (low < high) {
    const middle = Math.floor((low + high) / 2);
    if (points[middle].timeSec < timeSec) low = middle + 1;
    else high = middle;
  }
  const point = points[low];
  if (point && Math.abs(point.timeSec - timeSec) < 1e-7) return point.value;
  const previous = points[low - 1];
  if (!point || !previous) return null;
  const span = point.timeSec - previous.timeSec;
  if (span > 0.031 || span <= 0) return null;
  const ratio = (timeSec - previous.timeSec) / span;
  return previous.value + ((point.value - previous.value) * ratio);
}

export function motionStudioCompositionTracks(layers, mappingRows = []) {
  const enabledLayers = layers.filter((layer) => layer.enabled !== false);
  const sources = enabledLayers.map((layer) => motionStudioLayerTracks(layer));
  const motionIds = new Set(sources.flatMap((tracks) => [...tracks.keys()]));
  const manualInitialValues = new Map(mappingRows.filter((row) => (
    String(row.initial_mode || 'first_frame') === 'manual'
  )).map((row) => [String(row.motion_id), Number(row.initial_motion_position_deg || 0)]));
  const firstPoints = new Map();
  for (const source of sources) {
    for (const [motionId, points] of source.entries()) {
      if (!points.length) continue;
      const current = firstPoints.get(motionId);
      if (!current || points[0].timeSec < current.timeSec) firstPoints.set(motionId, points[0]);
    }
  }
  const duration = Math.max(0, ...enabledLayers.flatMap((layer) => (
    (layer.frames || []).map((frame) => Number(frame.time_sec || 0))
  )));
  const sampleCount = Math.max(0, Math.ceil(duration / 0.02));
  const tracks = new Map([...motionIds].map((motionId) => [motionId, []]));
  const lastValues = new Map([...motionIds].map((motionId) => {
    const firstPoint = firstPoints.get(motionId);
    return [motionId, manualInitialValues.has(motionId)
      ? manualInitialValues.get(motionId) : Number(firstPoint?.value || 0)];
  }));
  for (let index = 1; index <= sampleCount; index += 1) {
    const timeSec = Number((index * 0.02).toFixed(9));
    for (const motionId of motionIds) {
      let value = null;
      for (const source of sources) {
        const candidate = motionStudioSampleTrack(source.get(motionId), timeSec);
        if (candidate !== null) value = candidate;
      }
      const firstPoint = firstPoints.get(motionId);
      if (value === null && firstPoint && timeSec < firstPoint.timeSec) {
        value = manualInitialValues.has(motionId)
          ? manualInitialValues.get(motionId)
          : firstPoint.value;
      }
      if (value === null) value = lastValues.get(motionId) ?? 0;
      lastValues.set(motionId, value);
      tracks.get(motionId).push({ timeSec, value });
    }
  }
  return { tracks, duration, sampleCount, enabledLayers };
}

export function motionStudioDisplaySegments(points, maximumPoints = 1200) {
  const source = Array.isArray(points) ? points : [];
  if (!source.length) return [];
  const segments = [];
  let current = [];
  for (const point of source) {
    const previous = current[current.length - 1];
    if (previous && Number(point.timeSec) - Number(previous.timeSec) > 0.031) {
      segments.push(current);
      current = [];
    }
    current.push(point);
  }
  if (current.length) segments.push(current);
  const total = source.length;
  return segments.map((segment) => {
    const budget = Math.max(
      4,
      Math.floor((Math.max(4, Number(maximumPoints) || 1200) * segment.length) / total),
    );
    if (segment.length <= budget) return segment;
    const bucketSize = Math.max(1, Math.ceil(segment.length / Math.max(1, budget / 4)));
    const selected = [];
    for (let start = 0; start < segment.length; start += bucketSize) {
      const end = Math.min(segment.length, start + bucketSize);
      let minimum = start;
      let maximum = start;
      for (let index = start + 1; index < end; index += 1) {
        if (segment[index].value < segment[minimum].value) minimum = index;
        if (segment[index].value > segment[maximum].value) maximum = index;
      }
      const indices = [...new Set([start, minimum, maximum, end - 1])]
        .sort((left, right) => left - right);
      selected.push(...indices.map((index) => segment[index]));
    }
    return selected;
  });
}

export function motionStudioVisiblePoints(points, startTime, endTime) {
  const source = Array.isArray(points) ? points : [];
  if (!source.length) return [];
  const start = Number(startTime);
  const end = Number(endTime);
  let low = 0;
  let high = source.length;
  while (low < high) {
    const middle = Math.floor((low + high) / 2);
    if (Number(source[middle].timeSec) < start) low = middle + 1;
    else high = middle;
  }
  const first = Math.max(0, low - 1);
  low = first;
  high = source.length;
  while (low < high) {
    const middle = Math.floor((low + high) / 2);
    if (Number(source[middle].timeSec) <= end) low = middle + 1;
    else high = middle;
  }
  return source.slice(first, Math.min(source.length, low + 1));
}

export function drawMotionStudioLayerGraph({
  canvas,
  playhead,
  tracks,
  warnings = [],
  playback,
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
  let maxTime = 0.02;
  let minValue = 0;
  let maxValue = 0;
  for (const points of tracks.values()) {
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
  [...tracks.entries()].forEach(([, points], index) => {
    context.strokeStyle = colors[index % colors.length];
    context.lineWidth = 2;
    motionStudioDisplaySegments(points, Math.max(400, plotWidth * 2)).forEach((segment) => {
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
  context.save();
  context.strokeStyle = '#d33b3b';
  context.fillStyle = '#d33b3b';
  context.setLineDash([4, 3]);
  warnings.forEach((warning) => {
    const timeSec = Number(warning.second_time_sec);
    if (!Number.isFinite(timeSec)) return;
    const x = padding.left + ((Math.min(maxTime, Math.max(0, timeSec)) / maxTime) * plotWidth);
    context.beginPath();
    context.moveTo(x, padding.top);
    context.lineTo(x, padding.top + plotHeight);
    context.stroke();
  });
  context.restore();
  updatePlayhead(playback);
  return true;
}

export function motionStudioEditorIssueTimes(validation = {}, selectedMotionIds = []) {
  const selected = new Set(selectedMotionIds.map(String));
  return [
    ...(validation?.conflicts || []).map((item) => Number(item.start_sec)),
    ...(validation?.transition_warnings || []).map((item) => (
      Number(item.second_time_sec)
    )),
    ...(validation?.range_warnings || [])
      .filter((item) => selected.has(String(item.motion_id || '')))
      .map((item) => Number(item.time_sec)),
  ].filter(Number.isFinite);
}

export function drawMotionStudioEditorGraph({
  editor,
  canvas,
  legend,
  selectedMotionIds = [],
  operation = '',
  selectionStartText = '',
  selectionEndText = '',
  devicePixelRatio = globalThis.devicePixelRatio || 1,
}) {
  if (!editor || !canvas) return false;
  const selected = new Set(selectedMotionIds);
  const originalTracks = motionStudioLayerTracks(editor.original);
  const displayedLayer = editor.preview || editor.working;
  const workingTracks = motionStudioLayerTracks(displayedLayer);
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
  const viewEnd = Math.max(viewStart + 0.02, Number(editor.viewEnd || 0.02));
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
  if (editor.selectionStage === 1 && Number.isFinite(editor.selectionAnchor)) {
    const anchor = Math.max(viewStart, Math.min(viewEnd, editor.selectionAnchor));
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
    (curve) => JSON.parse(JSON.stringify(curve)),
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
