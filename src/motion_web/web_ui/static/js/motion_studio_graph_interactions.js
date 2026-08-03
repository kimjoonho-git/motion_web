import {
  MOTION_STUDIO_PERIOD_SEC,
  MOTION_STUDIO_TIME_EPSILON,
} from './motion_studio_constants.js?v=20260803-studio-structure-2';

export function motionStudioGraphPointInside(metrics, x, y) {
  const { padding } = metrics;
  return x >= padding.left
    && x <= padding.left + metrics.plotWidth
    && y >= padding.top
    && y <= padding.top + metrics.plotHeight;
}

export function motionStudioPanEditorGraph(editor, metrics, x, y) {
  const pan = editor?.panningGraph;
  if (!pan) return null;
  const pixelDeltaX = x - pan.startX;
  const pixelDeltaY = y - pan.startY;
  if (Math.hypot(pixelDeltaX, pixelDeltaY) >= 3) pan.moved = true;
  if (!pan.moved) return null;
  if (!editor.valueRangeLock) {
    const valueDelta = (pixelDeltaY / metrics.plotHeight) * pan.valueSpan;
    editor.valueView = {
      minValue: pan.startMinValue + valueDelta,
      maxValue: pan.startMaxValue + valueDelta,
    };
  }
  const timeDelta = -(pixelDeltaX / metrics.plotWidth) * pan.timeSpan;
  return {
    viewStart: pan.startViewStart + timeDelta,
    viewEnd: pan.startViewEnd + timeDelta,
  };
}

export function motionStudioMoveDraftPoint(editor, point, x, y, metrics) {
  const snappedTime = Math.max(
    0,
    Math.round(metrics.timeFor(x) / MOTION_STUDIO_PERIOD_SEC)
      * MOTION_STUDIO_PERIOD_SEC,
  );
  const collides = (editor.pointDraft?.points || []).some(
    (candidate) => candidate.point_id !== point.point_id
      && Math.abs(Number(candidate.time_sec) - snappedTime)
        < MOTION_STUDIO_PERIOD_SEC - MOTION_STUDIO_TIME_EPSILON,
  );
  if (!collides) point.time_sec = Number(snappedTime.toFixed(2));
  point.value_deg = Number(metrics.valueFor(y).toFixed(6));
  editor.pointDraft.points.sort((first, second) => first.time_sec - second.time_sec);
  return { collides, snappedTime };
}

export function motionStudioMoveTangentHandle(point, side, x, y, metrics) {
  let dtSec = metrics.timeFor(x) - Number(point.time_sec);
  if (side === 'in') dtSec = Math.min(-0.001, dtSec);
  else dtSec = Math.max(0.001, dtSec);
  const dvDeg = metrics.valueFor(y) - Number(point.value_deg);
  point[`${side}_handle`] = { dt_sec: dtSec, dv_deg: dvDeg };
  if ((point.tangent_mode || 'auto') !== 'smooth') point.tangent_mode = 'smooth';
  const opposite = side === 'in' ? 'out' : 'in';
  const oppositeHandle = point[`${opposite}_handle`] || {};
  const oppositeDt = Number(oppositeHandle.dt_sec || (side === 'in' ? 0.1 : -0.1));
  const slope = dvDeg / dtSec;
  point[`${opposite}_handle`] = { dt_sec: oppositeDt, dv_deg: slope * oppositeDt };
  return point[`${side}_handle`];
}
