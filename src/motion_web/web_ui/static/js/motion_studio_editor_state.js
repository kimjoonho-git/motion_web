import { motionStudioPointRangePoints } from './motion_studio_calculations.js?v=20260731-studio-performance-2';

const clone = (value) => JSON.parse(JSON.stringify(value));
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
      ? pointTimelineEnd : Math.max(0.02, duration),
    valueScale: 1,
    valueOffset: 0,
    valueView: null,
    valueRangeLock: null,
    selectionStage: 0,
    selectionAnchor: null,
    selectionStartSec: null,
    selectionEndSec: null,
    selectionMotionId: '',
    selectionCurveId: '',
    lastGraphClick: null,
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
      transition_warnings: [],
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

export function motionStudioSelectedRangeCurve(editor) {
  const curveId = String(editor?.selectionCurveId || '');
  const motionId = String(editor?.selectionMotionId || '');
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
  const points = motionStudioPointRangePoints(
    curve,
    editor?.selectionStartSec,
    editor?.selectionEndSec,
    editor?.selectionMotionId,
    editor?.selectionCurveId,
  );
  return points.length >= 2 ? { curve, points } : null;
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
