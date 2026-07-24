export function motionStudioLayerDuration(layer) {
  return Math.max(
    0,
    ...(layer?.frames || [])
      .map((frame) => Number(frame.time_sec))
      .filter((timeSec) => Number.isFinite(timeSec) && timeSec >= 0),
  );
}

export function motionStudioEditorValueBounds(minimum, maximum, scale = 1) {
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
  return { minValue: center - halfSpan, maxValue: center + halfSpan };
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
  editor.viewEnd = Math.max(0.02, duration);
  editor.selectionStage = 0;
  editor.selectionAnchor = null;
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

export function resolveMotionStudioSelectedLayerId(layers, selectedLayerId = '') {
  const available = Array.isArray(layers) ? layers : [];
  if (available.some((layer) => layer.layer_id === selectedLayerId)) return selectedLayerId;
  return String(available[0]?.layer_id || '');
}

export function motionStudioShouldEditPoint(operation, pointTarget) {
  return operation === 'point_curve' && Boolean(pointTarget);
}

export function motionStudioSelectionKindsMatch(firstKind, secondKind) {
  return ['point', 'motion'].includes(firstKind) && firstKind === secondKind;
}

export function motionStudioPointHitTarget(targets, x, y, radius = 14) {
  return (Array.isArray(targets) ? targets : []).find(
    (target) => Math.hypot(Number(target.x) - x, Number(target.y) - y) <= radius,
  ) || null;
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
  const order = [1, 3, 5].includes(Number(interpolationOrder))
    ? Number(interpolationOrder) : 3;
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
    const steps = Math.max(8, Math.min(80, Math.ceil(span / 0.02)));
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
