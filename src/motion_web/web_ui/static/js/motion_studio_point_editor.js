import {
  selectPoint,
  setSelectionMode,
} from './motion_studio_editor_selection.js';
import {
  motionStudioCopyPointRange,
  motionStudioDeletePointRange,
  motionStudioPointCurveOrder,
  motionStudioPointCurveViewEnd,
} from './motion_studio_point_model.js';
import {
  motionStudioSnapFrameTime,
} from './motion_studio_editor_math.js';
import {
  MOTION_STUDIO_PERIOD_SEC,
  MOTION_STUDIO_TIME_EPSILON,
} from './motion_studio_constants.js';
import {
  motionStudioRangeSelectionChosen,
  motionStudioRangeSelectionBounds,
  motionStudioResetRangeSelection,
} from './motion_studio_editor_state.js';

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
  selectPoint(editor, point.point_id);
  return { ok: true, point };
}

export function deleteMotionStudioDraftPoint(editor, pointId) {
  const points = editor?.pointDraft?.points || [];
  if (!points.some((point) => point.point_id === pointId)) {
    return { ok: false, reason: 'missing_point' };
  }
  if (points.length <= 2) return { ok: false, reason: 'minimum_points' };
  editor.pointDraft.points = points.filter((point) => point.point_id !== pointId);
  selectPoint(editor, editor.pointDraft.points[0]?.point_id);
  return { ok: true, deletedCount: 1 };
}

export function applyMotionStudioCopiedPointRange(editor, curve, result, createPointId) {
  if (!editor || !curve || !result?.ok) return [];
  const copiedPoints = result.points.map((point) => ({
    ...point,
    point_id: createPointId(),
  }));
  editor.pointDraft = structuredClone(curve);
  // 붙이는 자리에 있던 포인트는 빼고 넣는다 · 같은 시간에 둘이 있을 수 없다
  const replaced = new Set(result.replacedPointIds || []);
  editor.pointDraft.points = [
    ...(editor.pointDraft.points || []).filter(
      (point) => !replaced.has(String(point.point_id || '')),
    ),
    ...copiedPoints,
  ].sort((first, second) => Number(first.time_sec) - Number(second.time_sec));
  selectPoint(editor, copiedPoints[0]?.point_id);
  return copiedPoints;
}

export function applyMotionStudioDeletedPointRange(editor, curve, result) {
  if (!editor || !curve || !result?.ok) return false;
  editor.pointDraft = structuredClone(curve);
  editor.pointDraft.points = result.points;
  selectPoint(editor, result.points[0]?.point_id);
  return true;
}

export function bindMotionStudioPointEditorEvents(context) {
  const {
    state, el, selectedDraftPoint, discardEditorPreview, setEditorMessage,
    adoptCurveAtCandidate, syncPointControls, editorDuration,
    clearEditorPointRange, renderEditor,
    editorSelectedMotionIds, clearPendingPointCandidate, pointCurveIsApplied,
    pointCurveCanBeCreated, editorId, selectedEditorPointRange,
    activatePointDraftMutation, selectedEditorTimeRange, applyEditorOperation,
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
    // 그 자리에 곡선이 있으면 **그 곡선에 넣는다** · 빈 묶음을 새로 만들지
    // 않는다 · §6-294
    adoptCurveAtCandidate?.(editor, candidate);
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
    // 포인트를 건드렸으면 **편집 방식도 「포인트 곡선」으로 옮긴다** · §6-295
    //
    // 전에는 포인트만 더해 놓고 편집 방식은 그대로였다 · 그래서 「변경
    // 미리보기」와 「작업본 반영」이 계속 꺼져 있었고, 실행 취소만 켜졌다 ·
    // 사람 눈에는 「포인트 추가를 눌러도 아무 일도 안 난다」로 보였다.
    //
    // 이 일을 하는 `activatePointDraftMutation` 이 이미 있었는데 **넘겨만 주고
    // 아무도 부르지 않았다** · 후보 지우기·구간 풀기·시간축 늘리기까지 한다.
    activatePointDraftMutation(
      editor,
      `${candidate.motionId} 포인트 추가 · `
      + `${result.point.time_sec.toFixed(2)}초 · ${result.point.value_deg.toFixed(3)}°`,
      result.point.time_sec,
    );
  });
  el.studioEditorPointDeleteButton?.addEventListener('click', () => {
    const editor = state.editor;
    const point = selectedDraftPoint(editor);
    // 왜 안 되는지는 **누른 뒤에** 말한다 · 회색으로 막지 않는다 · §6-263
    if (!editor?.pointDraft) {
      setEditorMessage('그래프에서 지울 포인트를 먼저 누르세요.', true);
      return;
    }
    if (!point) {
      setEditorMessage(
        '지울 포인트가 없습니다 · 그래프에서 동그란 포인트를 눌러 고르세요.',
        true,
      );
      return;
    }
    const pointCount = (editor.pointDraft.points || []).length;
    if (!deleteMotionStudioDraftPoint(editor, point.point_id).ok) {
      setEditorMessage(
        `이 곡선은 포인트가 ${pointCount}개뿐이라 지울 수 없습니다 · `
        + '곡선에는 최소 2개가 남아야 합니다 · 먼저 「포인트 추가」로 포인트를 만드세요.',
        true,
      );
      return;
    }
    discardEditorPreview();
    // 지운 것도 같다 · 편집 방식을 「포인트 곡선」으로 옮긴다 · §6-295
    activatePointDraftMutation(
      editor,
      '포인트를 작업본에서 제거했습니다 · 결과 계산 전에는 저장되지 않습니다.',
      point.time_sec,
    );
  });
  // 선택 방식은 둘 중 하나 · §6-121
  //
  // 「포인트 선택」이면 클릭이 포인트 하나를 고르고, 「구간 선택」이면
  // 시작·종료를 잡는다 · 아래 기능 탭은 클릭의 뜻에 관여하지 않는다.
  el.studioEditorPointSelectButton?.addEventListener('click', () => {
    const editor = state.editor;
    if (!editor) return;
    // **방식**을 본다 · 「고르는 중」이 아니어도 구간 선택 쪽에 서 있을 수
    // 있다 · 여기서 고르는 중만 보면, 구간을 다 잡았거나 아직 안 잡은
    // 상태에서 이 버튼이 먹지 않는다 · §6-125
    if (!motionStudioRangeSelectionChosen(editor)) {
      setEditorMessage('이미 포인트 선택입니다 · 그래프의 포인트를 누르세요.');
      return;
    }
    setSelectionMode(editor, 'point');
    setEditorMessage('포인트 선택 · 그래프의 포인트를 눌러 고르거나 끌어 옮기세요.');
    renderEditor();
  });
  el.studioEditorRangeSelectButton?.addEventListener('click', () => {
    const editor = state.editor;
    if (!editor) return;
    // 두 버튼은 **토글이 아니다** · 누르면 그 방식이 된다 · §6-125
    //
    // 전에는 「고르는 중인가」를 뒤집어 방식을 정했다 · 그래서 구간을 고르는
    // 중에 「구간 선택」을 누르면 오히려 포인트 선택으로 넘어갔고, 다 고른
    // 뒤에는 「포인트 선택」이 먹지 않았다.
    const selectedIds = new Set(editorSelectedMotionIds());
    const selectedCurves = (editor.working?.point_curves || []).filter(
      (curve) => selectedIds.has(String(curve.motion_id || '')),
    );
    if (!selectedCurves.length) {
      setEditorMessage(
        '구간을 선택하려면 포인트 곡선이 표시된 Motion ID를 먼저 선택하세요.',
        true,
      );
      return;
    }
    setSelectionMode(editor, 'range');
    setEditorMessage('구간 선택 · 선택된 축에서 시작 포인트를 선택하세요.');
    renderEditor();
  });
  // 구간 복사·삭제는 **고른 축 전부**를 서버가 한 번에 바꾼다 · §6-122
  //
  // 전에는 화면에서 곡선 하나의 임시 작업본만 고쳤다 · 그래서 축을 셋 골라도
  // 한 축만 바뀌었다 · 시간 이동·배율이 이미 쓰던 길(서버 편집 → 결과
  // 미리보기 → 작업본 반영)로 맞춘다.
  const runRangeOperation = (operation, missingMessage) => {
    const editor = state.editor;
    if (!editor) return;
    if (!selectedEditorTimeRange(editor)) {
      setEditorMessage(missingMessage, true);
      return;
    }
    applyEditorOperation(operation);
  };
  el.studioEditorRangeCopyButton?.addEventListener('click', () => {
    runRangeOperation(
      'copy_point_range',
      '복사할 구간을 먼저 선택하세요 · 「구간 선택」을 누르고 포인트 두 개를 선택합니다.',
    );
  });
  el.studioEditorRangeDeleteButton?.addEventListener('click', () => {
    runRangeOperation(
      'delete_point_range',
      '삭제할 구간을 먼저 선택하세요 · 「구간 선택」을 누르고 포인트 두 개를 선택합니다.',
    );
  });
}
