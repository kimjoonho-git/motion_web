import {
  MOTION_STUDIO_PERIOD_SEC,
  MOTION_STUDIO_TIME_EPSILON,
} from './motion_studio_constants.js';
import { motionStudioSnapFrameTime } from './motion_studio_editor_math.js';

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
  // 붙이는 자리에 있던 포인트는 **대체한다** · §6-119
  //
  // 전에는 거기 포인트가 하나라도 있으면 거부했다 · 그런데 그래프 중간은
  // 거의 언제나 포인트가 있으므로, 사실상 빈 자리에만 붙일 수 있었다.
  //
  // 이음매에서 값이 튀는지는 **검사하지 않는다** · 사용자가 보고 고칠 일이다.
  const pasteStart = Number(copiedPoints[0].time_sec);
  const pasteEnd = Number(copiedPoints[copiedPoints.length - 1].time_sec);
  const replacedPointIds = (curve?.points || [])
    .filter((point) => {
      const timeSec = Number(point.time_sec);
      return timeSec >= pasteStart - 0.01 && timeSec <= pasteEnd + 0.01;
    })
    .map((point) => String(point.point_id || ''));
  return {
    ok: true,
    points: copiedPoints,
    replacedPointIds,
    startSec: pasteStart,
    endSec: pasteEnd,
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
  // 포인트를 누르면 **지금 고른 기능이 정한다** · §6-101
  //
  // 전에는 어떤 기능이 골라져 있든 포인트 편집으로 넘어갔다 · `모션값 배율`
  // 을 골라 두고 포인트를 찍으면 `포인트 곡선` 으로 바뀌어 구간을 못 잡았다 ·
  // 화면은 "동그란 포인트 두 개를 선택하세요" 라고 안내하면서 실제로는 첫
  // 클릭에 모드를 갈아 버려, 안내와 동작이 어긋났다.
  //
  // 포인트 하나를 고쳐 쓰는 일은 `포인트 곡선` 기능의 몫이다.
  if (pointTarget) {
    return rangeSelection || !pointMode ? 'select_point' : 'edit_point';
  }
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
