import {
  MOTION_STUDIO_PERIOD_SEC,
  MOTION_STUDIO_TIME_EPSILON,
} from './motion_studio_constants.js?v=20260803-studio-structure-4';

export function applyMotionStudioProjectPatch(project, patch) {
  if (!patch || typeof patch !== 'object') return project || null;
  const metadata = (
    patch.metadata && typeof patch.metadata === 'object'
      ? patch.metadata : {}
  );
  const sameProject = (
    project
    && String(project.project_id || '') === String(metadata.project_id || '')
  );
  const existingLayers = sameProject && Array.isArray(project.layers)
    ? project.layers : [];
  const deleted = new Set(
    (patch.delete_layer_ids || []).map((value) => String(value)),
  );
  const byId = new Map(
    existingLayers
      .filter((layer) => layer && !deleted.has(String(layer.layer_id || '')))
      .map((layer) => [String(layer.layer_id || ''), layer]),
  );
  for (const layer of patch.upsert_layers || []) {
    if (!layer || typeof layer !== 'object') continue;
    byId.set(String(layer.layer_id || ''), layer);
  }
  const layers = [];
  for (const value of patch.layer_order || []) {
    const layerId = String(value || '');
    if (!byId.has(layerId)) continue;
    layers.push(byId.get(layerId));
    byId.delete(layerId);
  }
  layers.push(...byId.values());
  return {
    ...(sameProject ? project : {}),
    ...metadata,
    layers,
  };
}

export function motionStudioSetLayerEnabled(project, layerId, enabled) {
  if (!project || !Array.isArray(project.layers)) return project || null;
  const targetId = String(layerId || '');
  let changed = false;
  const layers = project.layers.map((layer) => {
    if (String(layer?.layer_id || '') !== targetId) return layer;
    const nextEnabled = Boolean(enabled);
    if ((layer.enabled !== false) === nextEnabled) return layer;
    changed = true;
    return { ...layer, enabled: nextEnabled };
  });
  return changed ? { ...project, layers } : project;
}

export function motionStudioEditorValidationProject(
  project,
  layer,
  extraMotionIds = [],
) {
  const selected = new Set([
    ...motionStudioLayerMotionIds(layer),
    ...extraMotionIds.map((value) => String(value || '')).filter(Boolean),
  ]);
  const source = project || {};
  return {
    ...source,
    layers: (source.layers || []).map((item) => {
      if (item.layer_id === layer?.layer_id) {
        return {
          layer_id: item.layer_id,
          name: item.name,
          enabled: item.enabled,
          locked: item.locked,
          frames: [],
        };
      }
      return {
        layer_id: item.layer_id,
        name: item.name,
        enabled: item.enabled,
        locked: item.locked,
        frames: (item.frames || []).flatMap((frame) => {
          const values = Object.fromEntries(
            Object.entries(frame.values || {}).filter(
              ([motionId]) => selected.has(motionId),
            ),
          );
          return Object.keys(values).length ? [{ ...frame, values }] : [];
        }),
      };
    }),
  };
}

export function motionStudioMergePreviewProject(project, layerIds) {
  const selected = new Set(layerIds.map((value) => String(value || '')));
  return {
    ...(project || {}),
    layers: (project?.layers || []).filter(
      (layer) => selected.has(String(layer.layer_id || '')),
    ),
  };
}

export function motionStudioLayerDuration(layer) {
  return Math.max(
    0,
    ...(layer?.frames || [])
      .map((frame) => Number(frame.time_sec))
      .filter((timeSec) => Number.isFinite(timeSec) && timeSec >= 0),
  );
}

export function motionStudioEditorValueBounds(
  minimum,
  maximum,
  scale = 1,
  offset = 0,
  fixedBounds = null,
) {
  const fixedMinimum = Number(fixedBounds?.minValue);
  const fixedMaximum = Number(fixedBounds?.maxValue);
  if (
    Number.isFinite(fixedMinimum)
    && Number.isFinite(fixedMaximum)
    && fixedMaximum > fixedMinimum
  ) {
    return { minValue: fixedMinimum, maxValue: fixedMaximum };
  }
  let minValue = Number.isFinite(Number(minimum)) ? Number(minimum) : -1;
  let maxValue = Number.isFinite(Number(maximum)) ? Number(maximum) : 1;
  if (maxValue < minValue) [minValue, maxValue] = [maxValue, minValue];
  if (Math.abs(maxValue - minValue) < 1e-9) {
    minValue -= 1;
    maxValue += 1;
  }
  const numericScale = Number(scale);
  const valueScale = Number.isFinite(numericScale) && numericScale > 0 ? numericScale : 1;
  const center = (minValue + maxValue) / 2;
  const halfSpan = ((maxValue - minValue) / 2) * valueScale;
  const numericOffset = Number(offset);
  const valueOffset = Number.isFinite(numericOffset) ? numericOffset : 0;
  return {
    minValue: center - halfSpan + valueOffset,
    maxValue: center + halfSpan + valueOffset,
  };
}

export function motionStudioMotionAxisRange(rows, motionId) {
  const targetId = String(motionId || '').trim();
  if (!targetId) return null;
  const row = (Array.isArray(rows) ? rows : []).find(
    (candidate) => String(candidate?.motion_id || '').trim() === targetId,
  );
  const minValue = Number(row?.motion_lower_deg);
  const maxValue = Number(row?.motion_upper_deg);
  if (!Number.isFinite(minValue) || !Number.isFinite(maxValue) || maxValue <= minValue) {
    return null;
  }
  return { motionId: targetId, minValue, maxValue };
}

export function motionStudioValueViewAfterRangeUnlock(fixedBounds) {
  const minValue = Number(fixedBounds?.minValue);
  const maxValue = Number(fixedBounds?.maxValue);
  if (!Number.isFinite(minValue) || !Number.isFinite(maxValue) || maxValue <= minValue) {
    return null;
  }
  return { minValue, maxValue };
}

export function motionStudioEditorNextValueScale(currentScale, factor) {
  const current = Number.isFinite(Number(currentScale)) && Number(currentScale) > 0
    ? Number(currentScale) : 1;
  const multiplier = Number(factor);
  if (!Number.isFinite(multiplier) || multiplier <= 0) return current;
  const next = current * multiplier;
  return Number.isFinite(next) && next > 0 ? next : current;
}

export function synchronizeMotionStudioEditorTimeline(editor, layer, previousLayer = null) {
  if (!editor) return false;
  const duration = motionStudioLayerDuration(layer);
  const previousDuration = previousLayer === null
    ? Number.NaN
    : motionStudioLayerDuration(previousLayer);
  if (Number.isFinite(previousDuration) && Math.abs(duration - previousDuration) <= 1e-9) {
    return false;
  }
  editor.viewStart = 0;
  editor.viewEnd = Math.max(MOTION_STUDIO_PERIOD_SEC, duration);
  editor.rangeSelection = { phase: 'inactive', start: null, end: null };
  return true;
}

export function motionStudioLayerDataEqual(first, second) {
  return JSON.stringify({
    frames: first?.frames || [],
    point_curves: first?.point_curves || [],
  }) === JSON.stringify({
    frames: second?.frames || [],
    point_curves: second?.point_curves || [],
  });
}

export function motionStudioLayerMotionIds(layer) {
  return [...new Set((layer?.frames || []).flatMap(
    (frame) => Object.keys(frame?.values || {}),
  ))];
}

export function motionStudioCanCreatePointCurve(layer, motionId) {
  const targetId = String(motionId || '').trim();
  if (!targetId) return false;
  if ((layer?.point_curves || []).some(
    (curve) => String(curve?.motion_id || '') === targetId,
  )) return false;
  const values = (layer?.frames || [])
    .filter((frame) => Object.hasOwn(frame?.values || {}, targetId))
    .map((frame) => Number(frame.values[targetId]));
  if (!values.length || values.some((value) => !Number.isFinite(value))) return false;
  return Math.max(...values) - Math.min(...values) < 1e-9;
}

export function motionStudioPointCurveIsApplied(layer, curveId) {
  const targetId = String(curveId || '');
  return Boolean(targetId) && (layer?.point_curves || []).some(
    (curve) => String(curve?.curve_id || '') === targetId,
  );
}

export function resolveMotionStudioSelectedLayerId(layers, selectedLayerId = '') {
  const available = Array.isArray(layers) ? layers : [];
  if (available.some((layer) => layer.layer_id === selectedLayerId)) return selectedLayerId;
  return String(available[0]?.layer_id || '');
}

export function motionStudioPointRangeTargetsMatch(
  firstMotionId,
  secondMotionId,
  firstCurveId,
  secondCurveId,
) {
  const first = String(firstMotionId || '');
  const second = String(secondMotionId || '');
  const firstCurve = String(firstCurveId || '');
  const secondCurve = String(secondCurveId || '');
  return Boolean(first)
    && first === second
    && Boolean(firstCurve)
    && firstCurve === secondCurve;
}

export function motionStudioPointRangeReady(
  startSec,
  endSec,
  motionId,
  curveId,
  curve = null,
) {
  const start = Number(startSec);
  const end = Number(endSec);
  const basicRangeReady = Number.isFinite(start)
    && Number.isFinite(end)
    && Math.abs(end - start) >= MOTION_STUDIO_PERIOD_SEC - MOTION_STUDIO_TIME_EPSILON
    && Boolean(String(motionId || ''))
    && Boolean(String(curveId || ''));
  if (!basicRangeReady || !curve) return basicRangeReady;
  if (
    String(curve.motion_id || '') !== String(motionId || '')
    || String(curve.curve_id || '') !== String(curveId || '')
  ) return false;
  const points = Array.isArray(curve.points) ? curve.points : [];
  return points.some(
    (point) => Math.abs(Number(point.time_sec) - start) < 1e-7,
  ) && points.some(
    (point) => Math.abs(Number(point.time_sec) - end) < 1e-7,
  );
}

export function motionStudioPointRangePoints(
  curve,
  startSec,
  endSec,
  motionId,
  curveId,
) {
  if (!motionStudioPointRangeReady(
    startSec,
    endSec,
    motionId,
    curveId,
    curve,
  )) return [];
  const start = Math.min(Number(startSec), Number(endSec));
  const end = Math.max(Number(startSec), Number(endSec));
  return (curve.points || [])
    .filter((point) => {
      const timeSec = Number(point.time_sec);
      return Number.isFinite(timeSec)
        && timeSec >= start - 1e-9
        && timeSec <= end + 1e-9;
    })
    .sort((first, second) => Number(first.time_sec) - Number(second.time_sec));
}

export function motionStudioCopyPointRange(
  curve,
  startSec,
  endSec,
  targetStartSec,
) {
  const sourcePoints = motionStudioPointRangePoints(
    curve,
    startSec,
    endSec,
    curve?.motion_id,
    curve?.curve_id,
  );
  if (sourcePoints.length < 2) return { ok: false, reason: 'invalid_range' };
  const requestedTargetStart = Number(targetStartSec);
  if (!Number.isFinite(requestedTargetStart) || requestedTargetStart < 0) {
    return { ok: false, reason: 'invalid_target' };
  }
  const targetStart = motionStudioSnapFrameTime(requestedTargetStart);
  const sourceStart = Number(sourcePoints[0].time_sec);
  const copiedPoints = sourcePoints.map((point) => ({
    ...structuredClone(point),
    time_sec: motionStudioSnapFrameTime(
      targetStart + (Number(point.time_sec) - sourceStart),
    ),
  }));
  if (copiedPoints.some(
    (point, index) => copiedPoints.slice(index + 1).some(
      (candidate) => Math.abs(
        Number(candidate.time_sec) - Number(point.time_sec),
      ) < 0.01,
    ),
  )) {
    return { ok: false, reason: 'time_conflict' };
  }
  const existingTimes = (curve?.points || []).map((point) => Number(point.time_sec));
  if (copiedPoints.some(
    (point) => existingTimes.some(
      (timeSec) => Math.abs(timeSec - Number(point.time_sec)) < 0.01,
    ),
  )) {
    return { ok: false, reason: 'time_conflict' };
  }
  return {
    ok: true,
    points: copiedPoints,
    startSec: Number(copiedPoints[0].time_sec),
    endSec: Number(copiedPoints[copiedPoints.length - 1].time_sec),
  };
}

export function motionStudioDeletePointRange(
  curve,
  startSec,
  endSec,
) {
  const selectedPoints = motionStudioPointRangePoints(
    curve,
    startSec,
    endSec,
    curve?.motion_id,
    curve?.curve_id,
  );
  if (selectedPoints.length < 2) return { ok: false, reason: 'invalid_range' };
  const selectedIds = new Set(selectedPoints.map((point) => String(point.point_id || '')));
  const remainingPoints = (curve?.points || []).filter(
    (point) => !selectedIds.has(String(point.point_id || '')),
  );
  if (remainingPoints.length < 2) return { ok: false, reason: 'minimum_points' };
  return {
    ok: true,
    points: structuredClone(remainingPoints),
    deletedCount: selectedPoints.length,
  };
}

export function motionStudioCanSwitchPointDraftCurve(
  activeCurveId,
  targetCurveId,
  hasUnsavedChanges,
) {
  const active = String(activeCurveId || '');
  const target = String(targetCurveId || '');
  return !active || active === target || !hasUnsavedChanges;
}

export function motionStudioShouldProtectPointAxisSelection(
  hasPointDraft,
  pointMode,
  hasUnsavedChanges,
) {
  return Boolean(hasPointDraft) && (Boolean(pointMode) || Boolean(hasUnsavedChanges));
}

export function motionStudioPointCurveAtTime(
  curves,
  selectedMotionIds,
  timeSec,
  motionTarget = null,
) {
  const selected = new Set(selectedMotionIds || []);
  const targetMotionId = String(motionTarget?.motionId || '');
  const candidateMotionIds = targetMotionId
    ? new Set([targetMotionId])
    : (selected.size === 1 ? selected : new Set());
  if (!candidateMotionIds.size || !Number.isFinite(Number(timeSec))) return null;
  return (Array.isArray(curves) ? curves : []).find((curve) => {
    if (!candidateMotionIds.has(String(curve?.motion_id || ''))) return false;
    const points = curve?.points || [];
    const startSec = Number(points[0]?.time_sec);
    const endSec = Number(points[points.length - 1]?.time_sec);
    return Number.isFinite(startSec)
      && Number.isFinite(endSec)
      && Number(timeSec) >= startSec - 1e-9
      && Number(timeSec) <= endSec + 1e-9;
  }) || null;
}

export function motionStudioEditorGraphClickAction({
  operation = '',
  pointTarget = null,
  motionTarget = null,
  pointRegion = null,
  activeCurveId = '',
  rangeSelection = false,
} = {}) {
  const pointMode = operation === 'point_curve';
  if (pointTarget) return rangeSelection ? 'select_point' : 'edit_point';
  const regionCurveId = String(pointRegion?.curve_id || '');
  const activeId = String(activeCurveId || '');
  if (pointMode) {
    if (pointRegion && (!activeId || regionCurveId !== activeId)) {
      return 'select_curve';
    }
    return 'add_point';
  }
  if (pointRegion) return 'select_curve';
  if (motionTarget) return 'select_motion';
  return 'none';
}

export function motionStudioPointHitTarget(targets, x, y, radius = 14) {
  let nearest = null;
  let nearestDistance = Number(radius);
  for (const target of Array.isArray(targets) ? targets : []) {
    const distance = Math.hypot(Number(target.x) - x, Number(target.y) - y);
    if (distance <= nearestDistance) {
      nearest = target;
      nearestDistance = distance;
    }
  }
  return nearest;
}

export function motionStudioNearestMotionTarget(
  tracks,
  selectedMotionIds,
  metrics,
  x,
  y,
  radius = 18,
) {
  if (!(tracks instanceof Map) || !metrics) return null;
  const selected = new Set(selectedMotionIds || []);
  let nearest = null;
  let nearestDistance = Number(radius);
  for (const [motionId, points] of tracks.entries()) {
    if (!selected.has(motionId)) continue;
    for (const point of points || []) {
      const pointX = Number(metrics.xFor?.(point.timeSec));
      const pointY = Number(metrics.yFor?.(point.value));
      if (!Number.isFinite(pointX) || !Number.isFinite(pointY)) continue;
      const distance = Math.hypot(pointX - Number(x), pointY - Number(y));
      if (distance >= nearestDistance) continue;
      nearestDistance = distance;
      nearest = { motionId, ...point };
    }
  }
  return nearest;
}

export function motionStudioSnapFrameTime(
  timeSec,
  periodSec = MOTION_STUDIO_PERIOD_SEC,
) {
  const time = Number(timeSec);
  const period = Number(periodSec);
  if (!Number.isFinite(time) || !Number.isFinite(period) || period <= 0) return 0;
  return Math.max(0, Math.round(time / period) * period);
}

export function motionStudioMotionTargetAtTime(
  tracks,
  selectedMotionIds,
  timeSec,
  preferredValue = Number.NaN,
  toleranceSec = 1e-7,
) {
  if (!(tracks instanceof Map)) return null;
  const selected = new Set(selectedMotionIds || []);
  const targetTime = Number(timeSec);
  const preferred = Number(preferredValue);
  if (!Number.isFinite(targetTime)) return null;
  let nearest = null;
  let nearestValueDistance = Number.POSITIVE_INFINITY;
  for (const [motionId, points] of tracks.entries()) {
    if (!selected.has(motionId)) continue;
    for (const point of points || []) {
      if (Math.abs(Number(point.timeSec) - targetTime) > toleranceSec) continue;
      const valueDistance = Number.isFinite(preferred)
        ? Math.abs(Number(point.value) - preferred)
        : 0;
      if (nearest && valueDistance >= nearestValueDistance) continue;
      nearestValueDistance = valueDistance;
      nearest = { motionId, ...point };
    }
  }
  return nearest;
}

export function motionStudioCanvasEventPoint(rect, clientX, clientY, width, height) {
  const displayWidth = Number(rect?.width);
  const displayHeight = Number(rect?.height);
  const graphWidth = Number(width);
  const graphHeight = Number(height);
  const left = Number(rect?.left) || 0;
  const top = Number(rect?.top) || 0;
  return {
    x: (Number(clientX) - left) * (
      displayWidth > 0 && graphWidth > 0 ? graphWidth / displayWidth : 1
    ),
    y: (Number(clientY) - top) * (
      displayHeight > 0 && graphHeight > 0 ? graphHeight / displayHeight : 1
    ),
  };
}

export function motionStudioRuntimeStatusMessage(previousStatus, nextStatus) {
  const previousState = String(previousStatus?.state || '');
  const nextState = String(nextStatus?.state || '');
  const previousMessage = String(previousStatus?.message || '');
  const nextMessage = String(nextStatus?.message || '');
  const activeStates = new Set(['initializing', 'playing', 'recording', 'stopping']);
  if (nextState === 'error' && (
    previousState !== nextState || previousMessage !== nextMessage
  )) {
    return { message: nextMessage || '모션 스튜디오 작업에 실패했습니다.', error: true };
  }
  if (nextState === 'idle' && activeStates.has(previousState)) {
    return { message: nextMessage || '모션 스튜디오 작업이 완료되었습니다.', error: false };
  }
  return null;
}

export function motionStudioPointDragStarted(draggingPoint, x, y) {
  if (!draggingPoint) return false;
  if (draggingPoint.moved) return true;
  return Math.hypot(
    x - Number(draggingPoint.startX),
    y - Number(draggingPoint.startY),
  ) >= 3;
}

export function motionStudioPointCurveViewEnd(
  layerDuration,
  currentViewEnd = 0,
  requestedViewEnd = 0,
) {
  return Math.max(
    10,
    Number(layerDuration) || 0,
    Number(currentViewEnd) || 0,
    Number(requestedViewEnd) || 0,
  );
}

export function motionStudioPointCurveOrder(value, fallback = 3) {
  const order = Number(value);
  if ([1, 3, 5].includes(order)) return order;
  const fallbackOrder = Number(fallback);
  return [1, 3, 5].includes(fallbackOrder) ? fallbackOrder : 3;
}

export function motionStudioPointCurvePreview(rawPoints, interpolationOrder = 3) {
  const points = (Array.isArray(rawPoints) ? rawPoints : [])
    .map((point) => ({
      ...point,
      time_sec: Number(point?.time_sec),
      value_deg: Number(point?.value_deg),
    }))
    .filter((point) => Number.isFinite(point.time_sec) && Number.isFinite(point.value_deg))
    .sort((first, second) => first.time_sec - second.time_sec);
  if (points.length < 2) return [];
  const order = motionStudioPointCurveOrder(interpolationOrder);
  const automaticSlope = (index) => {
    const before = points[Math.max(0, index - 1)];
    const after = points[Math.min(points.length - 1, index + 1)];
    const span = after.time_sec - before.time_sec;
    return span > 1e-9 ? (after.value_deg - before.value_deg) / span : 0;
  };
  const pointSlope = (index) => {
    if (index === 0 || index === points.length - 1) return 0;
    const point = points[index];
    if (point.tangent_mode === 'broken') return 0;
    if (point.tangent_mode === 'smooth') {
      const handle = point.out_handle || point.in_handle || {};
      const dt = Number(handle.dt_sec);
      const dv = Number(handle.dv_deg);
      if (Number.isFinite(dt) && Number.isFinite(dv) && Math.abs(dt) > 1e-9) {
        return dv / dt;
      }
    }
    return automaticSlope(index);
  };
  const acceleration = (index) => {
    if (index <= 0 || index >= points.length - 1) return 0;
    if (points[index].tangent_mode === 'broken') return 0;
    const before = points[index - 1];
    const point = points[index];
    const after = points[index + 1];
    const previousSpan = point.time_sec - before.time_sec;
    const followingSpan = after.time_sec - point.time_sec;
    if (previousSpan <= 1e-9 || followingSpan <= 1e-9) return 0;
    const previousSlope = (point.value_deg - before.value_deg) / previousSpan;
    const followingSlope = (after.value_deg - point.value_deg) / followingSpan;
    return 2 * (followingSlope - previousSlope) / (previousSpan + followingSpan);
  };
  const result = [];
  points.slice(0, -1).forEach((first, index) => {
    const second = points[index + 1];
    const span = second.time_sec - first.time_sec;
    if (span <= 1e-9) return;
    const steps = Math.max(
      8,
      Math.min(80, Math.ceil(span / MOTION_STUDIO_PERIOD_SEC)),
    );
    for (let step = 0; step <= steps; step += 1) {
      if (index > 0 && step === 0) continue;
      const ratio = step / steps;
      let value;
      if (order === 1) {
        value = first.value_deg + ((second.value_deg - first.value_deg) * ratio);
      } else if (order === 3) {
        const ratio2 = ratio * ratio;
        const ratio3 = ratio2 * ratio;
        value = (
          (((2 * ratio3) - (3 * ratio2) + 1) * first.value_deg)
          + ((ratio3 - (2 * ratio2) + ratio) * span * pointSlope(index))
          + (((-2 * ratio3) + (3 * ratio2)) * second.value_deg)
          + ((ratio3 - ratio2) * span * pointSlope(index + 1))
        );
      } else {
        const firstSlope = pointSlope(index);
        const secondSlope = pointSlope(index + 1);
        const firstAcceleration = acceleration(index);
        const secondAcceleration = acceleration(index + 1);
        const delta = second.value_deg - first.value_deg;
        const c0 = first.value_deg;
        const c1 = firstSlope * span;
        const c2 = 0.5 * firstAcceleration * span * span;
        const remainingValue = delta - c1 - c2;
        const remainingSlope = (secondSlope * span) - c1 - (2 * c2);
        const remainingAcceleration = (secondAcceleration * span * span) - (2 * c2);
        const c3 = (10 * remainingValue) - (4 * remainingSlope)
          + (0.5 * remainingAcceleration);
        const c4 = (-15 * remainingValue) + (7 * remainingSlope)
          - remainingAcceleration;
        const c5 = (6 * remainingValue) - (3 * remainingSlope)
          + (0.5 * remainingAcceleration);
        value = c0 + (c1 * ratio) + (c2 * ratio ** 2) + (c3 * ratio ** 3)
          + (c4 * ratio ** 4) + (c5 * ratio ** 5);
      }
      result.push({ timeSec: first.time_sec + (span * ratio), value });
    }
  });
  return result;
}
