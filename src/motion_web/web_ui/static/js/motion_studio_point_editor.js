import {
  motionStudioPointCurveOrder,
  motionStudioSnapFrameTime,
} from './motion_studio_calculations.js?v=20260803-studio-structure-2';
import {
  MOTION_STUDIO_PERIOD_SEC,
  MOTION_STUDIO_TIME_EPSILON,
} from './motion_studio_constants.js?v=20260803-studio-structure-2';

const clone = (value) => JSON.parse(JSON.stringify(value));

export function updateMotionStudioDraftPoint(editor, point, {
  timeSec,
  valueDeg,
  tangentMode = 'auto',
}) {
  if (!editor?.pointDraft || !point) return { ok: false, reason: 'missing_point' };
  if (Number.isFinite(timeSec)) {
    const snappedTime = motionStudioSnapFrameTime(Math.max(0, timeSec));
    const collision = editor.pointDraft.points.some(
      (candidate) => candidate.point_id !== point.point_id
        && Math.abs(Number(candidate.time_sec) - snappedTime)
          < MOTION_STUDIO_PERIOD_SEC - MOTION_STUDIO_TIME_EPSILON,
    );
    if (collision) return { ok: false, reason: 'time_conflict' };
    point.time_sec = snappedTime;
  }
  if (Number.isFinite(valueDeg)) point.value_deg = valueDeg;
  point.tangent_mode = tangentMode;
  editor.pointDraft.points.sort((first, second) => first.time_sec - second.time_sec);
  return { ok: true, point };
}

export function addMotionStudioDraftPoint(editor, candidate, {
  curveId,
  pointId,
  interpolationOrder,
}) {
  if (!editor || !candidate) return { ok: false, reason: 'missing_candidate' };
  if (!editor.pointDraft || editor.pointDraft.motion_id !== candidate.motionId) {
    editor.pointDraft = {
      curve_id: curveId,
      motion_id: candidate.motionId,
      interpolation_order: motionStudioPointCurveOrder(interpolationOrder),
      points: [],
    };
  }
  const snappedTime = motionStudioSnapFrameTime(candidate.timeSec);
  const collision = editor.pointDraft.points.some(
    (point) => Math.abs(Number(point.time_sec) - snappedTime)
      < MOTION_STUDIO_PERIOD_SEC - MOTION_STUDIO_TIME_EPSILON,
  );
  if (collision) return { ok: false, reason: 'time_conflict' };
  const point = {
    point_id: pointId,
    time_sec: snappedTime,
    value_deg: Number(Number(candidate.valueDeg).toFixed(6)),
    tangent_mode: 'auto',
    in_handle: {},
    out_handle: {},
  };
  editor.pointDraft.points.push(point);
  editor.pointDraft.points.sort((first, second) => first.time_sec - second.time_sec);
  editor.selectedPointId = point.point_id;
  return { ok: true, point };
}

export function deleteMotionStudioDraftPoint(editor, pointId) {
  const points = editor?.pointDraft?.points || [];
  if (!points.some((point) => point.point_id === pointId)) {
    return { ok: false, reason: 'missing_point' };
  }
  if (points.length <= 2) return { ok: false, reason: 'minimum_points' };
  editor.pointDraft.points = points.filter((point) => point.point_id !== pointId);
  editor.selectedPointId = editor.pointDraft.points[0]?.point_id || '';
  return { ok: true, deletedCount: 1 };
}

export function applyMotionStudioCopiedPointRange(editor, curve, result, createPointId) {
  if (!editor || !curve || !result?.ok) return [];
  const copiedPoints = result.points.map((point) => ({
    ...point,
    point_id: createPointId(),
  }));
  editor.pointDraft = clone(curve);
  editor.pointDraft.points = [
    ...(editor.pointDraft.points || []),
    ...copiedPoints,
  ].sort((first, second) => Number(first.time_sec) - Number(second.time_sec));
  editor.selectedPointId = copiedPoints[0]?.point_id || '';
  return copiedPoints;
}

export function applyMotionStudioDeletedPointRange(editor, curve, result) {
  if (!editor || !curve || !result?.ok) return false;
  editor.pointDraft = clone(curve);
  editor.pointDraft.points = result.points;
  editor.selectedPointId = result.points[0]?.point_id || '';
  return true;
}
