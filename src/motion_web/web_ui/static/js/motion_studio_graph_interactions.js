import {
  MOTION_STUDIO_PERIOD_SEC,
  MOTION_STUDIO_TIME_EPSILON,
} from './motion_studio_constants.js?v=20260803-studio-structure-4';
import {
  motionStudioCanvasEventPoint,
  motionStudioEditorGraphClickAction,
  motionStudioMotionTargetAtTime,
  motionStudioNearestMotionTarget,
  motionStudioPointCurveAtTime,
  motionStudioPointDragStarted,
  motionStudioPointHitTarget,
  motionStudioPointRangeTargetsMatch,
  motionStudioSnapFrameTime,
} from './motion_studio_calculations.js?v=20260803-studio-structure-9';
import {
  motionStudioRangeSelectionActive,
} from './motion_studio_editor_state.js?v=20260803-studio-structure-9';

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

export function bindMotionStudioGraphEvents(context) {
  const {
    state, el, cachedLayerTracks, editorSelectedMotionIds, selectedDraftPoint,
    clearEditorPointRange, selectPointCurveFromGraph, syncPointControls,
    editorGraphScheduler, editorViewport, editorPointCurves, pointCurveIsApplied,
    pointCurveCanBeCreated, setEditorMessage, discardEditorPreview,
    clearPendingPointCandidate, renderEditorControls, drawEditorGraph,
    loadPointDraft, renderEditor, applyDraggedPoint,
  } = context;
  const canvasPoint = (event, metrics) => motionStudioCanvasEventPoint(
    el.studioEditorGraph.getBoundingClientRect(),
    event.clientX,
    event.clientY,
    metrics.width,
    metrics.height,
  );
  const selectRangePoint = (pointTarget) => {
    const editor = state.editor;
    if (!editor || !pointTarget) return false;
    const targetMotionId = String(pointTarget.curve.motion_id);
    const targetCurveId = String(pointTarget.curve.curve_id);
    const snapped = motionStudioSnapFrameTime(pointTarget.point.time_sec);
    const rangeStart = editor.rangeSelection?.start;
    if (editor.rangeSelection?.phase === 'awaiting_start') {
      if (!selectPointCurveFromGraph(
        pointTarget.curve,
        pointTarget.point.point_id,
        false,
      )) return false;
      editor.rangeSelection = {
        phase: 'awaiting_end',
        start: {
        pointId: String(pointTarget.point.point_id || ''),
        motionId: targetMotionId,
        curveId: targetCurveId,
        timeSec: snapped,
        },
        end: null,
      };
      setEditorMessage(
        `포인트 한 개 선택 · ${snapped.toFixed(2)}초 · `
        + '같은 포인트 곡선의 다른 포인트를 선택하세요.',
      );
    } else if (editor.rangeSelection?.phase === 'awaiting_end' && rangeStart) {
      if (!motionStudioPointRangeTargetsMatch(
        rangeStart.motionId,
        targetMotionId,
        rangeStart.curveId,
        targetCurveId,
      )) {
        setEditorMessage(
          `같은 포인트 곡선의 포인트를 선택하세요. 현재 선택: ${rangeStart.motionId}`,
          true,
        );
        return false;
      }
      const endPointId = String(pointTarget.point.point_id || '');
      const samePoint = rangeStart.pointId && endPointId
        ? String(rangeStart.pointId) === endPointId
        : Math.abs(Number(rangeStart.timeSec) - snapped) < MOTION_STUDIO_TIME_EPSILON;
      if (samePoint) {
        setEditorMessage('범위를 만들려면 서로 다른 포인트를 선택하세요.', true);
        return false;
      }
      const first = Number(rangeStart.timeSec);
      const rangeEnd = {
        pointId: endPointId,
        motionId: targetMotionId,
        curveId: targetCurveId,
        timeSec: snapped,
      };
      const [start, end] = first <= snapped
        ? [rangeStart, rangeEnd] : [rangeEnd, rangeStart];
      loadPointDraft(pointTarget.curve, pointTarget.point.point_id);
      editor.rangeSelection = { phase: 'complete', start, end };
      if (el.studioEditorRangeCopyTarget) {
        const curveEnd = Math.max(
          0,
          ...(pointTarget.curve.points || []).map(
            (point) => Number(point.time_sec) || 0,
          ),
        );
        el.studioEditorRangeCopyTarget.value = motionStudioSnapFrameTime(
          Math.max(end.timeSec, curveEnd) + MOTION_STUDIO_PERIOD_SEC,
        ).toFixed(2);
      }
      setEditorMessage(
        '포인트 범위 선택 완료 · '
        + `${start.timeSec.toFixed(2)}초 ~ ${end.timeSec.toFixed(2)}초`,
      );
    } else {
      return false;
    }
    renderEditorControls();
    drawEditorGraph();
    return true;
  };

  el.studioEditorGraph?.addEventListener('mousemove', (event) => {
    const editor = state.editor;
    const metrics = editor?.graphMetrics;
    if (!editor || !metrics) return;
    const { x, y } = canvasPoint(event, metrics);
    if (editor.draggingPoint) {
      if (!motionStudioPointDragStarted(editor.draggingPoint, x, y)) return;
      if (!editor.draggingPoint.activated) {
        const pendingDrag = { ...editor.draggingPoint, activated: true };
        if (!selectPointCurveFromGraph(pendingDrag.curve, pendingDrag.pointId)) {
          editor.draggingPoint = null;
          return;
        }
        editor.draggingPoint = pendingDrag;
      }
      const point = selectedDraftPoint(editor);
      if (!point) return;
      motionStudioMoveDraftPoint(editor, point, x, y, editor.graphMetrics || metrics);
      clearEditorPointRange(editor);
      editor.draggingPoint.moved = true;
      editor.suppressGraphClick = true;
      syncPointControls();
      editorGraphScheduler.schedule();
      return;
    }
    if (editor.panningGraph) {
      const nextView = motionStudioPanEditorGraph(editor, metrics, x, y);
      if (nextView) {
        editor.suppressGraphClick = true;
        editorViewport.setView(nextView.viewStart, nextView.viewEnd, true);
      }
      return;
    }
    if (editor.draggingHandle) {
      const point = selectedDraftPoint(editor);
      if (!point) return;
      motionStudioMoveTangentHandle(point, editor.draggingHandle.side, x, y, metrics);
      clearEditorPointRange(editor);
      editor.suppressGraphClick = true;
      syncPointControls();
      editorGraphScheduler.schedule();
      return;
    }
    if (!motionStudioGraphPointInside(metrics, x, y)) {
      editor.cursor = null;
      if (el.studioEditorCursorInfo) {
        el.studioEditorCursorInfo.textContent = '그래프 안쪽에서 지점을 선택하세요';
      }
      editorGraphScheduler.schedule();
      return;
    }
    const rawValue = metrics.valueFor(y);
    const timeSec = motionStudioSnapFrameTime(metrics.timeFor(x));
    const nearest = motionStudioMotionTargetAtTime(
      cachedLayerTracks(editor.preview || editor.working),
      editorSelectedMotionIds(),
      timeSec,
      rawValue,
    );
    editor.cursor = {
      x: metrics.xFor(timeSec),
      y: nearest ? metrics.yFor(nearest.value) : y,
      timeSec,
      value: nearest ? nearest.value : rawValue,
      nearest,
    };
    if (el.studioEditorCursorInfo) {
      el.studioEditorCursorInfo.textContent = nearest
        ? `${nearest.motionId} · ${nearest.timeSec.toFixed(3)}초 · ${nearest.value.toFixed(3)}°`
        : `${timeSec.toFixed(3)}초 · ${rawValue.toFixed(3)}°`;
    }
    editorGraphScheduler.schedule();
  });

  el.studioEditorGraph?.addEventListener('mouseleave', () => {
    if (state.editor) state.editor.cursor = null;
    if (el.studioEditorCursorInfo) {
      el.studioEditorCursorInfo.textContent = '그래프 위에 마우스를 올리세요';
    }
    editorGraphScheduler.schedule();
  });

  el.studioEditorGraph?.addEventListener('click', (event) => {
    const editor = state.editor;
    const metrics = editor?.graphMetrics;
    if (!editor || !metrics) return;
    const rangeSelecting = motionStudioRangeSelectionActive(editor);
    if (editor.suppressGraphClick && !rangeSelecting) {
      editor.suppressGraphClick = false;
      return;
    }
    if (rangeSelecting) editor.suppressGraphClick = false;
    if (event.motionStudioRangeHandled) return;
    event.motionStudioRangeHandled = true;
    const clickPoint = { ...canvasPoint(event, metrics), timeStamp: event.timeStamp };
    if (!motionStudioGraphPointInside(metrics, clickPoint.x, clickPoint.y)) return;
    const pointTarget = motionStudioPointHitTarget(
      editor.pointHitTargets,
      clickPoint.x,
      clickPoint.y,
      rangeSelecting ? 22 : 14,
    );
    const selectedMotionIds = editorSelectedMotionIds();
    const motionTarget = motionStudioNearestMotionTarget(
      cachedLayerTracks(editor.preview || editor.working),
      selectedMotionIds,
      metrics,
      clickPoint.x,
      clickPoint.y,
    );
    const pointRegion = pointTarget?.curve || motionStudioPointCurveAtTime(
      editorPointCurves(editor.preview || editor.working),
      selectedMotionIds,
      Math.max(0, metrics.timeFor(clickPoint.x)),
      motionTarget,
    );
    const graphAction = motionStudioEditorGraphClickAction({
      operation: el.studioEditorOperation?.value || '',
      pointTarget,
      motionTarget,
      pointRegion,
      activeCurveId: editor.pointDraft?.curve_id,
      rangeSelection: rangeSelecting,
    });
    if (graphAction === 'edit_point') {
      if (
        !pointCurveIsApplied(editor, pointTarget.curve.curve_id)
        && !pointCurveCanBeCreated(editor)
      ) {
        if (!selectPointCurveFromGraph(
          pointTarget.curve,
          pointTarget.point.point_id,
        )) return;
        setEditorMessage('생성된 포인트를 먼저 작업본에 반영하세요.', true);
        return;
      }
      if (editor.preview) {
        setEditorMessage(
          '현재 결과 미리보기를 먼저 편집 반영한 뒤 포인트를 수정하세요.',
          true,
        );
        return;
      }
      if (!selectPointCurveFromGraph(
        pointTarget.curve,
        pointTarget.point.point_id,
      )) return;
      setEditorMessage(
        `${pointTarget.curve.motion_id} 포인트 선택 · 포인트를 드래그하거나 시간·모션값을 수정하세요.`,
      );
      return;
    }
    if (graphAction === 'select_curve') {
      const activatePointMode = el.studioEditorOperation?.value === 'point_curve';
      if (!selectPointCurveFromGraph(
        pointRegion,
        pointRegion.points?.[0]?.point_id || '',
        activatePointMode,
      )) return;
      const applied = pointCurveIsApplied(editor, pointRegion.curve_id);
      setEditorMessage(
        applied
          ? (activatePointMode
            ? '포인트 데이터입니다. 동그란 포인트를 선택해 편집하세요.'
            : '현재 편집 항목을 유지합니다. 동그란 포인트 두 개를 선택하세요.')
          : '생성된 포인트를 먼저 작업본에 반영하세요.',
        !applied,
      );
      return;
    }
    discardEditorPreview('편집할 포인트를 다시 선택하여 결과 미리보기를 취소했습니다.');
    if (graphAction === 'add_point') {
      const selectedIds = editorSelectedMotionIds();
      if (selectedIds.length !== 1) {
        clearPendingPointCandidate(editor);
        setEditorMessage('포인트를 추가할 Motion ID를 하나만 선택하세요.', true);
        return;
      }
      const motionId = selectedIds[0];
      const timeSec = motionStudioSnapFrameTime(metrics.timeFor(clickPoint.x));
      if ((editor.pointDraft?.points || []).some(
        (point) => Math.abs(Number(point.time_sec) - timeSec)
          < MOTION_STUDIO_PERIOD_SEC - MOTION_STUDIO_TIME_EPSILON,
      )) {
        clearPendingPointCandidate(editor);
        setEditorMessage('같은 시간에는 포인트를 하나만 만들 수 있습니다.', true);
        renderEditorControls();
        drawEditorGraph();
        return;
      }
      const graphSample = motionStudioMotionTargetAtTime(
        cachedLayerTracks(editor.preview || editor.working),
        [motionId],
        timeSec,
        metrics.valueFor(clickPoint.y),
      );
      editor.pendingPointCandidate = {
        motionId,
        timeSec: Number(timeSec.toFixed(2)),
        valueDeg: Number(
          (graphSample?.value ?? metrics.valueFor(clickPoint.y)).toFixed(6),
        ),
      };
      setEditorMessage(
        `${motionId} 추가 위치 선택 · ${editor.pendingPointCandidate.timeSec.toFixed(2)}초 · `
        + `${editor.pendingPointCandidate.valueDeg.toFixed(3)}° · 포인트 추가를 누르세요.`,
      );
      renderEditorControls();
      drawEditorGraph();
      return;
    }
    if (graphAction === 'select_motion') {
      setEditorMessage(
        '일반 모션점은 편집할 수 없습니다. Motion ID를 하나 선택해 전체 포인트를 생성하세요.',
        true,
      );
      return;
    }
    if (graphAction !== 'select_point') {
      setEditorMessage('편집할 포인트 가까이를 클릭하세요.', true);
      return;
    }
    selectRangePoint(pointTarget);
  });

  el.studioEditorGraph?.addEventListener('mousedown', (event) => {
    const editor = state.editor;
    const metrics = editor?.graphMetrics;
    if (!editor || !metrics || event.button !== 0) return;
    if (editor.autoApplyingPointDrag) {
      setEditorMessage('이전 포인트 이동을 계산하고 있습니다.', true);
      return;
    }
    const { x, y } = canvasPoint(event, metrics);
    if (motionStudioRangeSelectionActive(editor)) {
      event.preventDefault();
      editor.draggingPoint = null;
      editor.draggingHandle = null;
      editor.panningGraph = null;
      editor.suppressGraphClick = false;
      return;
    }
    const handle = (editor.handleHitTargets || []).find(
      (target) => Math.hypot(target.x - x, target.y - y) <= 9,
    );
    if (handle) {
      event.preventDefault();
      editor.draggingHandle = { side: handle.side };
      editor.suppressGraphClick = true;
      discardEditorPreview('탄젠트를 바꾸어 결과 미리보기를 취소했습니다.');
      setEditorMessage('탄젠트 핸들 조절 중 · 놓은 뒤 결과 미리보기로 곡선을 계산하세요.');
      return;
    }
    const pointTarget = motionStudioPointHitTarget(editor.pointHitTargets, x, y);
    if (pointTarget) {
      if (
        !pointCurveIsApplied(editor, pointTarget.curve.curve_id)
        && !pointCurveCanBeCreated(editor)
      ) {
        if (!selectPointCurveFromGraph(
          pointTarget.curve,
          pointTarget.point.point_id,
        )) return;
        setEditorMessage('생성된 포인트를 먼저 작업본에 반영하세요.', true);
        return;
      }
      if (editor.preview) {
        setEditorMessage(
          '현재 결과 미리보기를 먼저 편집 반영한 뒤 포인트를 이동하세요.',
          true,
        );
        return;
      }
      event.preventDefault();
      const pointMode = el.studioEditorOperation?.value === 'point_curve';
      if (pointMode && !selectPointCurveFromGraph(
        pointTarget.curve,
        pointTarget.point.point_id,
      )) return;
      editor.draggingPoint = {
        pointId: pointTarget.point.point_id,
        curve: pointTarget.curve,
        startX: x,
        startY: y,
        moved: false,
        activated: pointMode,
      };
      clearEditorPointRange(editor);
      if (pointMode) {
        syncPointControls();
        drawEditorGraph();
      }
      setEditorMessage(
        pointMode
          ? '포인트 선택 · 그대로 드래그하면 좌우는 시간, 상하는 모션값을 바꿉니다.'
          : '한 번 클릭하면 편집할 포인트로 선택하고, 드래그하면 포인트를 이동합니다.',
      );
      return;
    }
    if (motionStudioGraphPointInside(metrics, x, y)) {
      editor.panningGraph = {
        startX: x,
        startY: y,
        startViewStart: editor.viewStart,
        startViewEnd: editor.viewEnd,
        startMinValue: metrics.minValue,
        startMaxValue: metrics.maxValue,
        timeSpan: editor.viewEnd - editor.viewStart,
        valueSpan: metrics.maxValue - metrics.minValue,
        moved: false,
      };
    }
  });

  window.addEventListener('mouseup', () => {
    const editor = state.editor;
    if (!editor) return;
    if (editor.draggingHandle) {
      editor.draggingHandle = null;
      setEditorMessage('탄젠트 핸들 변경 완료 · 결과 미리보기를 눌러 20ms 곡선을 계산하세요.');
      renderEditor();
      return;
    }
    if (editor.draggingPoint) {
      const moved = editor.draggingPoint.moved;
      editor.draggingPoint = null;
      if (moved) {
        setEditorMessage('포인트 이동 완료 · 곡선을 계산하고 작업본에 자동 반영합니다.');
        renderEditor();
        void applyDraggedPoint();
      } else {
        syncPointControls();
        drawEditorGraph();
      }
      return;
    }
    if (editor.panningGraph) {
      const moved = editor.panningGraph.moved;
      editor.panningGraph = null;
      if (moved) {
        setEditorMessage(
          editor.valueRangeLock
            ? '그래프 시간축을 이동했습니다. 세로축은 모션축 범위로 고정되어 있습니다.'
            : '그래프 표시 구간을 좌우·상하로 이동했습니다.',
        );
        drawEditorGraph();
      }
    }
  });

  el.studioEditorGraph?.addEventListener('wheel', (event) => {
    const editor = state.editor;
    if (!editor) return;
    event.preventDefault();
    const span = editor.viewEnd - editor.viewStart;
    if (event.shiftKey) {
      const delta = Math.sign(event.deltaY) * span * 0.12;
      editorViewport.setView(editor.viewStart + delta, editor.viewEnd + delta);
      return;
    }
    const center = editor.cursor?.timeSec ?? ((editor.viewStart + editor.viewEnd) / 2);
    const zoomFactor = event.deltaY < 0 ? 0.8 : 1.25;
    const newSpan = span * zoomFactor;
    const ratio = (center - editor.viewStart) / span;
    editorViewport.setView(
      center - (newSpan * ratio),
      center + (newSpan * (1 - ratio)),
    );
  }, { passive: false });
}
