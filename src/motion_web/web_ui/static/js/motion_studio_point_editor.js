import {
  motionStudioCopyPointRange,
  motionStudioDeletePointRange,
  motionStudioPointCurveOrder,
  motionStudioPointCurveViewEnd,
  motionStudioSnapFrameTime,
} from './motion_studio_calculations.js?v=20260803-studio-structure-9';
import {
  MOTION_STUDIO_PERIOD_SEC,
  MOTION_STUDIO_TIME_EPSILON,
} from './motion_studio_constants.js?v=20260803-studio-structure-4';
import {
  motionStudioRangeSelectionActive,
  motionStudioRangeSelectionBounds,
  motionStudioResetRangeSelection,
} from './motion_studio_editor_state.js?v=20260803-studio-structure-9';

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
  editor.pointDraft = structuredClone(curve);
  editor.pointDraft.points = [
    ...(editor.pointDraft.points || []),
    ...copiedPoints,
  ].sort((first, second) => Number(first.time_sec) - Number(second.time_sec));
  editor.selectedPointId = copiedPoints[0]?.point_id || '';
  return copiedPoints;
}

export function applyMotionStudioDeletedPointRange(editor, curve, result) {
  if (!editor || !curve || !result?.ok) return false;
  editor.pointDraft = structuredClone(curve);
  editor.pointDraft.points = result.points;
  editor.selectedPointId = result.points[0]?.point_id || '';
  return true;
}

export function bindMotionStudioPointEditorEvents(context) {
  const {
    state, el, selectedDraftPoint, discardEditorPreview, setEditorMessage,
    syncPointControls, editorDuration, clearEditorPointRange, renderEditor,
    editorSelectedMotionIds, clearPendingPointCandidate, pointCurveIsApplied,
    pointCurveCanBeCreated, editorId, selectedEditorPointRange,
    activatePointDraftMutation,
  } = context;
  const updateSelectedPointFromControls = () => {
    const editor = state.editor;
    const point = selectedDraftPoint(editor);
    if (!editor || !point) return;
    const timeSec = Number(el.studioEditorPointTime?.value);
    const valueDeg = Number(el.studioEditorPointValue?.value);
    const tangentMode = el.studioEditorPointMode?.value || 'auto';
    discardEditorPreview('포인트 값이 바뀌어 결과 미리보기를 취소했습니다.');
    const result = updateMotionStudioDraftPoint(editor, point, {
      timeSec, valueDeg, tangentMode,
    });
    if (!result.ok) {
      setEditorMessage(
        result.reason === 'time_conflict'
          ? '같은 시간에는 포인트를 하나만 만들 수 있습니다.'
          : '포인트를 변경할 수 없습니다.',
        true,
      );
      syncPointControls();
      return;
    }
    if (Number.isFinite(timeSec) && point.time_sec > editor.viewEnd) {
      editor.pointTimelineEnd = motionStudioPointCurveViewEnd(
        editorDuration(editor.working),
        editor.viewEnd,
        point.time_sec + Math.max(1, point.time_sec * 0.05),
      );
      editor.viewEnd = editor.pointTimelineEnd;
    }
    clearEditorPointRange(editor);
    setEditorMessage('포인트 변경 완료 · 결과 미리보기를 눌러 곡선을 다시 계산하세요.');
    renderEditor();
  };
  [el.studioEditorPointTime, el.studioEditorPointValue, el.studioEditorPointMode]
    .forEach((field) => field?.addEventListener('change', updateSelectedPointFromControls));
  el.studioEditorPointCurveOrder?.addEventListener('change', () => {
    const editor = state.editor;
    if (!editor) return;
    const interpolationOrder = motionStudioPointCurveOrder(
      el.studioEditorPointCurveOrder?.value,
      editor.pointCurveOrder,
    );
    editor.pointCurveOrder = interpolationOrder;
    if (editor.pointDraft) editor.pointDraft.interpolation_order = interpolationOrder;
    discardEditorPreview('곡선 방식이 바뀌어 결과 미리보기를 취소했습니다.');
    setEditorMessage(`${interpolationOrder === 1 ? '직선' : `${interpolationOrder}차 곡선`} 선택 · 결과 미리보기를 눌러 다시 계산하세요.`);
    renderEditor();
  });
  el.studioEditorPointTimelineEnd?.addEventListener('change', () => {
    const editor = state.editor;
    if (!editor) return;
    const requested = Number(el.studioEditorPointTimelineEnd.value);
    if (!Number.isFinite(requested) || requested <= 0) {
      setEditorMessage('포인트 작업 시간축 끝은 0보다 큰 시간을 입력하세요.', true);
      syncPointControls();
      return;
    }
    editor.pointTimelineEnd = Math.max(MOTION_STUDIO_PERIOD_SEC, requested);
    editor.viewStart = 0;
    editor.viewEnd = editor.pointTimelineEnd;
    setEditorMessage(
      `포인트 작업 시간축을 0초~${editor.pointTimelineEnd.toFixed(2)}초로 표시합니다.`,
    );
    renderEditor();
  });
  el.studioEditorPointAddButton?.addEventListener('click', () => {
    const editor = state.editor;
    const candidate = editor?.pendingPointCandidate;
    if (!editor || !candidate) {
      setEditorMessage('그래프에서 추가할 위치를 먼저 선택하세요.', true);
      return;
    }
    const selectedIds = editorSelectedMotionIds();
    if (selectedIds.length !== 1 || selectedIds[0] !== candidate.motionId) {
      clearPendingPointCandidate(editor);
      setEditorMessage('포인트를 추가할 Motion ID 하나를 다시 선택하세요.', true);
      renderEditor();
      return;
    }
    if (
      !pointCurveIsApplied(editor, editor.pointDraft?.curve_id)
      && !pointCurveCanBeCreated(editor)
    ) {
      setEditorMessage(
        editor.pointDraft
          ? '생성된 포인트를 먼저 작업본에 반영하세요.'
          : '선택 축 전체에 포인트를 생성하고 작업본에 반영한 뒤 편집하세요.',
        true,
      );
      return;
    }
    const result = addMotionStudioDraftPoint(editor, candidate, {
      curveId: editorId('curve'),
      pointId: editorId('point'),
      interpolationOrder: editor.pointCurveOrder,
    });
    if (!result.ok) {
      clearPendingPointCandidate(editor);
      setEditorMessage('같은 시간에는 포인트를 하나만 만들 수 있습니다.', true);
      renderEditor();
      return;
    }
    clearPendingPointCandidate(editor);
    clearEditorPointRange(editor);
    setEditorMessage(
      `${candidate.motionId} 포인트 추가 · `
      + `${result.point.time_sec.toFixed(2)}초 · ${result.point.value_deg.toFixed(3)}°`,
    );
    renderEditor();
  });
  el.studioEditorPointDeleteButton?.addEventListener('click', () => {
    const editor = state.editor;
    const point = selectedDraftPoint(editor);
    if (!editor?.pointDraft || !point) return;
    if (!deleteMotionStudioDraftPoint(editor, point.point_id).ok) {
      setEditorMessage(
        '곡선을 유지하려면 포인트가 최소 2개 필요하므로 더 삭제할 수 없습니다.',
        true,
      );
      return;
    }
    discardEditorPreview();
    clearEditorPointRange(editor);
    setEditorMessage('포인트를 작업본에서 제거했습니다 · 결과 계산 전에는 저장되지 않습니다.');
    renderEditor();
  });
  el.studioEditorRangeSelectButton?.addEventListener('click', () => {
    const editor = state.editor;
    if (!editor) return;
    const selecting = !motionStudioRangeSelectionActive(editor);
    const selectedIds = new Set(editorSelectedMotionIds());
    const selectedCurves = (editor.working?.point_curves || []).filter(
      (curve) => selectedIds.has(String(curve.motion_id || '')),
    );
    if (selecting && !selectedCurves.length) {
      setEditorMessage(
        '구간을 선택하려면 포인트 곡선이 표시된 Motion ID를 먼저 선택하세요.',
        true,
      );
      return;
    }
    clearEditorPointRange(editor);
    motionStudioResetRangeSelection(editor, selecting);
    setEditorMessage(
      selecting
        ? '구간 선택 · 같은 포인트 곡선에서 시작 포인트를 선택하세요.'
        : '구간 선택을 취소했습니다 · 포인트 하나를 선택하거나 드래그할 수 있습니다.',
    );
    renderEditor();
  });
  el.studioEditorRangeCopyButton?.addEventListener('click', () => {
    const editor = state.editor;
    const selectedRange = selectedEditorPointRange(editor);
    if (!editor || !selectedRange) {
      setEditorMessage('같은 포인트 곡선의 서로 다른 포인트 두 개를 선택하세요.', true);
      return;
    }
    const bounds = motionStudioRangeSelectionBounds(editor);
    const result = motionStudioCopyPointRange(
      selectedRange.curve,
      bounds.startSec,
      bounds.endSec,
      Number(el.studioEditorRangeCopyTarget?.value),
    );
    if (!result.ok) {
      const errors = {
        invalid_range: '복사할 포인트 구간을 다시 선택하세요.',
        invalid_target: '복사 시작 시간은 0초 이상의 20ms 단위 값이어야 합니다.',
        time_conflict: '복사 위치에 기존 포인트가 있습니다. 겹치지 않는 시간을 입력하세요.',
      };
      setEditorMessage(errors[result.reason] || '포인트 구간을 복사할 수 없습니다.', true);
      return;
    }
    discardEditorPreview();
    const copiedPoints = applyMotionStudioCopiedPointRange(
      editor, selectedRange.curve, result, () => editorId('point'),
    );
    activatePointDraftMutation(
      editor,
      `구간 복사 완료 · ${copiedPoints.length}개 포인트 · `
        + `${result.startSec.toFixed(2)}초 ~ ${result.endSec.toFixed(2)}초 · `
        + '결과 미리보기로 곡선을 확인하세요.',
    );
  });
  el.studioEditorRangeDeleteButton?.addEventListener('click', () => {
    const editor = state.editor;
    const selectedRange = selectedEditorPointRange(editor);
    if (!editor || !selectedRange) {
      setEditorMessage('삭제할 포인트 구간을 다시 선택하세요.', true);
      return;
    }
    const bounds = motionStudioRangeSelectionBounds(editor);
    const result = motionStudioDeletePointRange(
      selectedRange.curve,
      bounds.startSec,
      bounds.endSec,
    );
    if (!result.ok) {
      setEditorMessage(
        result.reason === 'minimum_points'
          ? '곡선을 유지하려면 삭제 후 포인트가 최소 2개 남아야 합니다.'
          : '삭제할 포인트 구간을 다시 선택하세요.',
        true,
      );
      return;
    }
    discardEditorPreview();
    applyMotionStudioDeletedPointRange(editor, selectedRange.curve, result);
    activatePointDraftMutation(
      editor,
      `구간 삭제 완료 · ${result.deletedCount}개 포인트 · `
        + '결과 미리보기로 곡선을 확인하세요.',
    );
  });
}
