import { motionStudioPointRangePoints } from './motion_studio_point_model.js';
import { MOTION_STUDIO_PERIOD_SEC } from './motion_studio_constants.js';
import { MOTION_STUDIO_TIME_EPSILON } from './motion_studio_constants.js';

const clone = structuredClone;
const layerDirtyCache = new WeakMap();

export function createMotionStudioEditorSession({
  layer,
  operation,
  duration,
  pointTimelineEnd,
  rangeWarnings = [],
}) {
  return {
    layerId: layer.layer_id,
    original: clone(layer),
    working: clone(layer),
    preview: null,
    previewValidation: null,
    undo: [],
    redo: [],
    viewStart: 0,
    viewEnd: operation === 'point_curve'
      ? pointTimelineEnd : Math.max(MOTION_STUDIO_PERIOD_SEC, duration),
    valueScale: 1,
    valueOffset: 0,
    valueView: null,
    valueRangeLock: null,
    // 선택 방식은 사용자가 고른 것 · 편집·저장으로 풀리지 않는다 · §6-125
    //
    // 고른 구간(`rangeSelection`)과 **따로** 둔다 · 레이어 길이가 바뀌면
    // 잡아 둔 시간이 더는 맞지 않아 구간은 풀어야 하지만, 「구간 선택으로
    // 일하는 중」이라는 사실까지 풀리면 저장할 때마다 포인트 선택으로
    // 되돌아간다.
    selectionMode: 'point',
    rangeSelection: {
      phase: 'inactive',
      start: null,
      end: null,
    },
    pendingPointCandidate: null,
    cursor: null,
    graphMetrics: null,
    pointDraft: null,
    pointCurveOrder: 3,
    pointTimelineEnd,
    selectedPointId: '',
    pointHitTargets: [],
    handleHitTargets: [],
    draggingHandle: null,
    draggingPoint: null,
    panningGraph: null,
    operationReport: null,
    previewOperation: '',
    operation,
    preferredEditOperation: operation,
    pointModeReturnOperation: '',
    validation: {
      conflicts: [],
      range_warnings: clone(rangeWarnings),
      playable: true,
    },
    saveState: 'saved',
    savedAt: '',
    saveError: '',
    saveFailureFingerprint: '',
  };
}

export function motionStudioEditorPointCurves(layer) {
  return Array.isArray(layer?.point_curves) ? layer.point_curves : [];
}

export function motionStudioSelectedDraftPoint(editor) {
  return editor?.pointDraft?.points?.find(
    (point) => point.point_id === editor.selectedPointId,
  ) || null;
}

export function motionStudioStoredCurveForDraft(editor) {
  const curveId = String(editor?.pointDraft?.curve_id || '');
  if (!curveId) return null;
  return motionStudioEditorPointCurves(editor?.working).find(
    (curve) => String(curve.curve_id || '') === curveId,
  ) || null;
}

function motionStudioSelectedRangeCurve(editor) {
  const curveId = String(editor?.rangeSelection?.start?.curveId || '');
  const motionId = String(editor?.rangeSelection?.start?.motionId || '');
  if (!curveId || !motionId) return null;
  if (
    String(editor?.pointDraft?.curve_id || '') === curveId
    && String(editor?.pointDraft?.motion_id || '') === motionId
  ) return editor.pointDraft;
  return motionStudioEditorPointCurves(editor?.working).find(
    (curve) => String(curve.curve_id || '') === curveId
      && String(curve.motion_id || '') === motionId,
  ) || null;
}

export function motionStudioSelectedPointRange(editor) {
  const curve = motionStudioSelectedRangeCurve(editor);
  const start = editor?.rangeSelection?.start;
  const end = editor?.rangeSelection?.end;
  if (
    !start || !end
    || String(start.motionId || '') !== String(end.motionId || '')
    || String(start.curveId || '') !== String(end.curveId || '')
  ) return null;
  const points = motionStudioPointRangePoints(
    curve,
    start?.timeSec,
    end?.timeSec,
    start?.motionId,
    start?.curveId,
  );
  return points.length >= 2 ? { curve, points } : null;
}

export function motionStudioSelectedTimeRange(editor) {
  if (editor?.rangeSelection?.phase !== 'complete') return null;
  const start = editor.rangeSelection.start;
  const end = editor.rangeSelection.end;
  const startSec = Number(start?.timeSec);
  const endSec = Number(end?.timeSec);
  if (
    !Number.isFinite(startSec)
    || !Number.isFinite(endSec)
    || Math.abs(endSec - startSec)
      < MOTION_STUDIO_PERIOD_SEC - MOTION_STUDIO_TIME_EPSILON
  ) return null;
  return {
    startSec: Math.min(startSec, endSec),
    endSec: Math.max(startSec, endSec),
    start,
    end,
  };
}

export function motionStudioResetRangeSelection(editor, active = false) {
  if (!editor) return;
  editor.rangeSelection = {
    phase: active ? 'awaiting_start' : 'inactive',
    start: null,
    end: null,
  };
}

export function motionStudioRangeSelectionActive(editor) {
  return ['awaiting_start', 'awaiting_end'].includes(editor?.rangeSelection?.phase);
}

/** 지금 「구간 선택」 쪽에 서 있는가 · §6-123
 *
 * 구간을 다 잡으면 단계가 `complete` 가 된다 · 고르는 중이 아니라고 해서
 * 「포인트 선택」으로 돌아간 것처럼 보이면 안 된다 · 편집하고 반영한 뒤에도
 * 잡아 둔 구간 그대로 이어서 일할 수 있어야 한다.
 */
export function motionStudioRangeSelectionChosen(editor) {
  if (editor?.selectionMode === 'range') return true;
  if (editor?.selectionMode === 'point') return false;
  // 옛 편집 상태에는 `selectionMode` 가 없다 · 단계로 미루어 본다
  return motionStudioRangeSelectionActive(editor)
    || editor?.rangeSelection?.phase === 'complete';
}

/** 실제로 **고른 것**이 있는가 · 방식이 아니라 고른 결과를 본다 · §6-125 */
export function motionStudioRangePicked(editor) {
  return ['awaiting_end', 'complete'].includes(editor?.rangeSelection?.phase);
}

/** 선택 방식을 정한다 · §6-125
 *
 * 바꾸는 일의 주인은 `motion_studio_editor_selection.js` 다 · 여기 있는 것은
 * 그 모듈이 부르는 알맹이이고, 옛 호출부를 위해 이름을 남겨 둔다 · §6-127
 */
export function motionStudioSetSelectionMode(editor, mode) {
  if (!editor) return;
  editor.selectionMode = mode === 'range' ? 'range' : 'point';
  motionStudioResetRangeSelection(editor, editor.selectionMode === 'range');
}

export function motionStudioRangeSelectionBounds(editor) {
  const start = Number(editor?.rangeSelection?.start?.timeSec);
  const end = Number(editor?.rangeSelection?.end?.timeSec);
  if (!Number.isFinite(start) || !Number.isFinite(end)) return null;
  return { startSec: Math.min(start, end), endSec: Math.max(start, end) };
}

export function motionStudioSelectRangePoint(editor, pointTarget) {
  if (!editor || !pointTarget?.curve || !pointTarget?.point) {
    return { ok: false, reason: 'missing_target' };
  }
  const phase = editor.rangeSelection?.phase;
  if (!['awaiting_start', 'awaiting_end'].includes(phase)) {
    return { ok: false, reason: 'inactive' };
  }
  const target = {
    pointId: String(pointTarget.point.point_id || ''),
    motionId: String(pointTarget.curve.motion_id || ''),
    curveId: String(pointTarget.curve.curve_id || ''),
    timeSec: Number(Number(pointTarget.point.time_sec || 0).toFixed(2)),
    valueDeg: Number(Number(pointTarget.point.value_deg || 0).toFixed(6)),
  };
  if (phase === 'awaiting_start') {
    editor.rangeSelection = {
      phase: 'awaiting_end',
      start: target,
      end: null,
    };
    return { ok: true, phase: 'awaiting_end', target };
  }
  const start = editor.rangeSelection?.start;
  if (!start) return { ok: false, reason: 'missing_start' };
  const samePoint = start.pointId && target.pointId
    && String(start.motionId || '') === target.motionId
    && String(start.curveId || '') === target.curveId
    ? String(start.pointId) === target.pointId
    : false;
  if (samePoint) return { ok: false, reason: 'same_point', start, target };
  if (Math.abs(Number(start.timeSec) - target.timeSec) < MOTION_STUDIO_TIME_EPSILON) {
    return { ok: false, reason: 'same_time', start, target };
  }
  const [rangeStart, rangeEnd] = Number(start.timeSec) <= target.timeSec
    ? [start, target] : [target, start];
  editor.rangeSelection = {
    phase: 'complete',
    start: rangeStart,
    end: rangeEnd,
  };
  return {
    ok: true,
    phase: 'complete',
    start: rangeStart,
    end: rangeEnd,
  };
}

export function motionStudioBeginPointDrag(editor, pointTarget, x, y, pointMode) {
  if (!editor || !pointTarget?.curve || !pointTarget?.point) return false;
  editor.draggingHandle = null;
  editor.panningGraph = null;
  editor.draggingPoint = {
    pointId: pointTarget.point.point_id,
    curve: pointTarget.curve,
    startX: x,
    startY: y,
    moved: false,
    activated: Boolean(pointMode),
  };
  motionStudioResetRangeSelection(editor, false);
  return true;
}

export function motionStudioBeginTangentDrag(editor, side) {
  if (!editor || !['in', 'out'].includes(side)) return false;
  editor.draggingPoint = null;
  editor.panningGraph = null;
  editor.draggingHandle = { side };
  motionStudioResetRangeSelection(editor, false);
  return true;
}

function comparablePointCurve(curve) {
  const normalized = clone(curve);
  if (Number(normalized?.interpolation_order) === 1) {
    (normalized.points || []).forEach((point) => {
      if (point.tangent_mode === 'linear') point.tangent_mode = 'auto';
    });
  }
  return normalized;
}

export function motionStudioPointDraftHasUnsavedChanges(editor) {
  if (!editor?.pointDraft) return false;
  const stored = motionStudioStoredCurveForDraft(editor);
  if (!stored) return true;
  return JSON.stringify(comparablePointCurve(stored))
    !== JSON.stringify(comparablePointCurve(editor.pointDraft));
}

export function motionStudioEditorFailureFingerprint(editor) {
  return JSON.stringify({
    frames: editor?.working?.frames || [],
    point_curves: editor?.working?.point_curves || [],
    pointDraft: editor?.pointDraft || null,
  });
}

export function motionStudioEditorLayerIsDirty(editor, layersEqual) {
  if (!editor || typeof layersEqual !== 'function') return false;
  const cached = layerDirtyCache.get(editor);
  if (cached?.original === editor.original && cached?.working === editor.working) {
    return cached.value;
  }
  const value = !layersEqual(editor.original, editor.working);
  layerDirtyCache.set(editor, {
    original: editor.original,
    working: editor.working,
    value,
  });
  return value;
}
