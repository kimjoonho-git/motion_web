import { MOTION_STUDIO_PERIOD_SEC } from './motion_studio_constants.js?v=20260803-studio-structure-4';
import { motionStudioLayerDuration } from './motion_studio_project_model.js?v=20260803-studio-structure-12';

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

export function motionStudioSnapFrameTime(
  timeSec,
  periodSec = MOTION_STUDIO_PERIOD_SEC,
) {
  const time = Number(timeSec);
  const period = Number(periodSec);
  if (!Number.isFinite(time) || !Number.isFinite(period) || period <= 0) return 0;
  return Math.max(0, Math.round(time / period) * period);
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
