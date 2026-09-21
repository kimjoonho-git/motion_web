import {
  MOTION_STUDIO_SHORTCUT_BUTTONS,
  MOTION_STUDIO_SHORTCUT_LABELS,
  motionStudioEditorShortcut,
  motionStudioNeighbourPointId,
  motionStudioShortcutAllowed,
  motionStudioTypingTarget,
} from './motion_studio_editor_shortcuts.js';
import { motionStudioAxisEndpoints } from './motion_studio_tracks.js';
import { displayText, maxOf, minOf } from './format.js';
import {
  editMotionStudioLayer,
  saveMotionStudioLayerData,
} from './api.js';
import {
  motionStudioCanCreatePointCurve,
  motionStudioEditorValidationProject,
  motionStudioLayerDataEqual,
  motionStudioLayerDuration,
  motionStudioLayerMotionIds,
  motionStudioPointCurveIsApplied,
} from './motion_studio_project_model.js';
import {
  motionStudioEditorNextValueScale,
  motionStudioEditorValueBounds,
  motionStudioMotionAxisRange,
  motionStudioValueViewAfterRangeUnlock,
  synchronizeMotionStudioEditorTimeline,
} from './motion_studio_editor_math.js';
import {
  motionStudioCanSwitchPointDraftCurve,
  motionStudioEditorGraphClickAction,
  motionStudioPointCurveOrder,
  motionStudioPointCurvePreview,
  motionStudioPointCurveViewEnd,
  motionStudioShouldProtectPointAxisSelection,
} from './motion_studio_point_model.js';
import {
  drawMotionStudioEditorGraph,
} from './motion_studio_graph.js';
import {
  motionStudioEditorInspectorState,
  motionStudioRangeWarningGroups,
  renderMotionStudioEditorPresentation,
  requestMotionStudioEditorSave,
} from './motion_studio_editor_ui.js';
import {
  createMotionStudioEditorSession,
  motionStudioEditorFailureFingerprint,
  motionStudioEditorLayerIsDirty,
  motionStudioEditorPointCurves,
  motionStudioPointDraftHasUnsavedChanges,
  motionStudioRangeSelectionActive,
  motionStudioRangeSelectionBounds,
  motionStudioRangePicked,
  motionStudioRangeSelectionChosen,
  motionStudioResetRangeSelection,
  motionStudioSelectedDraftPoint,
  motionStudioSelectedPointRange,
  motionStudioSelectedTimeRange,
  motionStudioStoredCurveForDraft,
} from './motion_studio_editor_state.js';
import {
  createMotionStudioGraphScheduler,
} from './motion_studio_graph_scheduler.js';
import {
  createMotionStudioEditorViewportController,
} from './motion_studio_editor_viewport.js';
import {
  MOTION_STUDIO_PERIOD_SEC,
} from './motion_studio_constants.js';
import {
  bindMotionStudioGraphEvents,
} from './motion_studio_graph_interactions.js';
import {
  bindMotionStudioPointEditorEvents,
} from './motion_studio_point_editor.js';
import {
  createMotionStudioAxisEditorController,
  motionStudioValidMotionId,
} from './motion_studio_axis_editor.js';
import {
  clearPendingPoint,
  clearPicks,
  releaseRange,
  selectPoint,
} from './motion_studio_editor_selection.js';
import { showConfirm } from './ui_dialogs.js';

const POINT_RANGE_EDIT_OPERATIONS = new Set([
  'time_shift',
  'time_scale',
  'value_offset',
  'value_scale',
]);

function editorId(prefix) {
  if (globalThis.crypto?.randomUUID) {
    return `${prefix}_${globalThis.crypto.randomUUID().slice(0, 8)}`;
  }
  return `${prefix}_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 7)}`;
}

export function motionStudioEditorHistoryEntry(editor, clone = structuredClone) {
  return {
    layer: clone(editor.working),
    validation: clone(editor.validation),
    curveId: String(editor.pointDraft?.curve_id || ''),
    selectedPointId: String(editor.selectedPointId || ''),
  };
}

export function createMotionStudioEditorController({
  state,
  el,
  clone = structuredClone,
  escapeHtml,
  activeMapping,
  configuredMotors,
  editorAxisLabel,
  layerPointCoverageIssues,
  editorValidationProject,
  cachedLayerTracks,
  run,
}) {
  let preferredEditorEditOperation = 'time_scale';
  let bound = false;
  function editorMotionIds(layer) {
    return motionStudioLayerMotionIds(layer);
  }

  function editorPointCurves(layer) {
    return motionStudioEditorPointCurves(layer);
  }

  function selectedDraftPoint(editor = state.editor) {
    return motionStudioSelectedDraftPoint(editor);
  }

  function storedCurveForDraft(editor = state.editor) {
    return motionStudioStoredCurveForDraft(editor);
  }

  function selectedEditorPointRange(editor = state.editor) {
    return motionStudioSelectedPointRange(editor);
  }

  function selectedEditorTimeRange(editor = state.editor) {
    return motionStudioSelectedTimeRange(editor);
  }

  function selectedEditorTimeRangePoints(editor = state.editor) {
    const range = selectedEditorTimeRange(editor);
    if (!range) return [];
    const selectedIds = new Set(editorSelectedMotionIds());
    return editorPointCurves(editor?.working).flatMap((curve) => {
      if (!selectedIds.has(String(curve.motion_id || ''))) return [];
      return (curve.points || []).filter((point) => {
        const timeSec = Number(point.time_sec);
        return Number.isFinite(timeSec)
          && timeSec >= range.startSec - 1e-9
          && timeSec <= range.endSec + 1e-9;
      }).map((point) => ({ curve, point }));
    });
  }

  function pointCurveIsApplied(editor, curveId) {
    return motionStudioPointCurveIsApplied(editor?.working, curveId);
  }

  function pointCurveCanBeCreated(editor = state.editor) {
    const selectedIds = editorSelectedMotionIds();
    const motionId = String(
      editor?.pointDraft?.motion_id
      || (selectedIds.length === 1 ? selectedIds[0] : ''),
    );
    return motionStudioCanCreatePointCurve(editor?.working, motionId);
  }

  function pointDraftHasUnsavedChanges(editor = state.editor) {
    return motionStudioPointDraftHasUnsavedChanges(editor);
  }

  function loadPointDraft(curve, pointId = '') {
    const editor = state.editor;
    if (!editor || !curve) return;
    editor.pointDraft = clone(curve);
    clearPendingPoint(editor);
    const derivedOrder = (editor.pointDraft.points || []).every(
      (point) => point.tangent_mode === 'linear',
    ) ? 1 : 3;
    editor.pointDraft.interpolation_order = motionStudioPointCurveOrder(
      editor.pointDraft.interpolation_order,
      derivedOrder,
    );
    editor.pointCurveOrder = editor.pointDraft.interpolation_order;
    if (el.studioEditorPointCurveOrder) {
      el.studioEditorPointCurveOrder.value = String(editor.pointCurveOrder);
    }
    (editor.pointDraft.points || []).forEach((point) => {
      if (point.tangent_mode === 'linear') point.tangent_mode = 'auto';
    });
    selectPoint(editor, pointId || curve.points?.[0]?.point_id || '');
    editor.draggingHandle = null;
    editor.draggingPoint = null;
  }

  function selectOnlyEditorAxis(motionId) {
    el.studioEditorAxisList?.querySelectorAll('input').forEach((input) => {
      input.checked = input.value === motionId;
    });
  }

  // 고른 것을 바꾸는 일은 `motion_studio_editor_selection.js` 가 맡는다 · §6-127
  function clearEditorPointRange(editor) {
    releaseRange(editor);
  }

  function clearPendingPointCandidate(editor = state.editor) {
    clearPendingPoint(editor);
  }

  function rememberEditorEditOperation(editor, operation) {
    if (!editor || !POINT_RANGE_EDIT_OPERATIONS.has(operation)) return;
    editor.preferredEditOperation = operation;
    preferredEditorEditOperation = operation;
  }

  function enterEditorPointMode(editor) {
    if (!editor) return;
    const currentOperation = String(
      el.studioEditorOperation?.value || editor.operation || '',
    );
    if (POINT_RANGE_EDIT_OPERATIONS.has(currentOperation)) {
      rememberEditorEditOperation(editor, currentOperation);
      editor.pointModeReturnOperation = currentOperation;
    }
    editor.operation = 'point_curve';
    if (el.studioEditorOperation) el.studioEditorOperation.value = 'point_curve';
  }

  function restoreEditorEditOperation(editor) {
    if (!editor) return '';
    const operation = POINT_RANGE_EDIT_OPERATIONS.has(editor.pointModeReturnOperation)
      ? editor.pointModeReturnOperation
      : (
        POINT_RANGE_EDIT_OPERATIONS.has(editor.preferredEditOperation)
          ? editor.preferredEditOperation
          : preferredEditorEditOperation
      );
    editor.operation = operation;
    editor.preferredEditOperation = operation;
    editor.pointModeReturnOperation = '';
    if (el.studioEditorOperation) el.studioEditorOperation.value = operation;
    clearPendingPointCandidate(editor);
    clearEditorPointRange(editor);
    return operation;
  }

  function activatePointDraftMutation(editor, message) {
    if (!editor?.pointDraft) return;
    clearPendingPointCandidate(editor);
    clearEditorPointRange(editor);
    enterEditorPointMode(editor);
    const lastTime = maxOf(
      (editor.pointDraft.points || []).map(
        (point) => Number(point.time_sec) || 0,
      ),
      0,
    );
    if (lastTime > editor.pointTimelineEnd) editor.pointTimelineEnd = lastTime;
    if (lastTime > editor.viewEnd) editor.viewEnd = lastTime;
    setEditorMessage(message);
    renderEditor();
  }

  function setPointCurveMode(curve = null, pointId = '') {
    enterEditorPointMode(state.editor);
    if (curve) {
      selectOnlyEditorAxis(curve.motion_id);
      loadPointDraft(curve, pointId);
      clearEditorPointRange(state.editor);
    }
    renderEditor();
  }

  function selectPointCurveFromGraph(
    curve,
    pointId = '',
    activatePointMode = true,
    preserveAxisSelection = false,
  ) {
    const editor = state.editor;
    if (!editor || !curve) return false;
    const activeCurveId = String(editor.pointDraft?.curve_id || '');
    const targetCurveId = String(curve.curve_id || '');
    if (!motionStudioCanSwitchPointDraftCurve(
      activeCurveId,
      targetCurveId,
      pointDraftHasUnsavedChanges(editor),
    )) {
      setEditorMessage(
        '현재 포인트 변경을 먼저 결과 미리보기하고 작업본에 반영한 뒤 다른 곡선을 선택하세요.',
        true,
      );
      renderEditor();
      return false;
    }
    if (activatePointMode) {
      setPointCurveMode(curve, pointId);
    } else {
      if (!preserveAxisSelection) selectOnlyEditorAxis(curve.motion_id);
      loadPointDraft(curve, pointId);
      renderEditorControls();
      drawEditorGraph();
    }
    return true;
  }

  function protectPointDraftAxisSelection() {
    const editor = state.editor;
    if (!editor?.pointDraft) return false;
    const pointMode = el.studioEditorOperation?.value === 'point_curve';
    const unsaved = pointDraftHasUnsavedChanges(editor);
    if (!motionStudioShouldProtectPointAxisSelection(
      true,
      pointMode,
      unsaved,
    )) return false;
    selectOnlyEditorAxis(editor.pointDraft.motion_id);
    setEditorMessage(
      unsaved
        ? '현재 포인트 변경을 먼저 결과 미리보기하고 작업본에 반영한 뒤 Motion ID 선택을 바꾸세요.'
        : '포인트 곡선 편집 중에는 해당 Motion ID만 사용합니다. 다른 곡선은 그래프에서 선택하세요.',
      true,
    );
    renderEditor();
    return true;
  }

  function syncPointControls() {
    const editor = state.editor;
    const point = selectedDraftPoint(editor);
    const pointMode = el.studioEditorOperation?.value === 'point_curve';
    const appliedPointCurve = pointCurveIsApplied(editor, editor?.pointDraft?.curve_id);
    const editablePointCurve = appliedPointCurve || pointCurveCanBeCreated(editor);
    const selectedRange = selectedEditorPointRange(editor);
    const selectedTimeRange = selectedEditorTimeRange(editor);
    const rangeReady = Boolean(selectedTimeRange);
    const rangeSelecting = motionStudioRangeSelectionActive(editor);
    // 구간을 다 잡은 뒤에도 「구간 선택」쪽에 서 있어야 한다 · §6-123
    const rangeChosen = motionStudioRangeSelectionChosen(editor);
    const rangeBounds = selectedTimeRange || motionStudioRangeSelectionBounds(editor);
    const selectedIds = editorSelectedMotionIds();
    const rangePointTargets = selectedEditorTimeRangePoints(editor);
    const selectedAppliedPointCurve = editorPointCurves(editor?.working).some(
      (curve) => selectedIds.includes(String(curve.motion_id || '')),
    );
    const startPoint = rangeReady ? selectedTimeRange.start : point;
    const endPoint = rangeReady ? selectedTimeRange.end : null;
    el.studioEditorSelectedPointSummary?.classList.toggle('hidden', !startPoint);
    if (el.studioEditorSelectedPointTitle) {
      el.studioEditorSelectedPointTitle.textContent = rangeReady
        ? '선택 범위'
        : '선택 포인트';
    }
    if (el.studioEditorSelectedPointStartTime) {
      el.studioEditorSelectedPointStartTime.textContent = startPoint
        ? `${Number(startPoint.timeSec ?? startPoint.time_sec).toFixed(2)}초`
        : '-';
    }
    if (el.studioEditorSelectedPointStartValue) {
      el.studioEditorSelectedPointStartValue.textContent = startPoint
        ? `${Number(startPoint.valueDeg ?? startPoint.value_deg).toFixed(3)}°`
        : '-';
    }
    el.studioEditorSelectedPointEnd?.classList.toggle('pending', !endPoint);
    if (el.studioEditorSelectedPointEndTime) {
      el.studioEditorSelectedPointEndTime.textContent = endPoint
        ? `${Number(endPoint.timeSec ?? endPoint.time_sec).toFixed(2)}초`
        : '선택 필요';
    }
    if (el.studioEditorSelectedPointEndValue) {
      el.studioEditorSelectedPointEndValue.textContent = endPoint
        ? `${Number(endPoint.valueDeg ?? endPoint.value_deg).toFixed(3)}°`
        : '-';
    }
    if (el.studioEditorPointAddButton) {
      // **포인트 선택 모드면 언제든 된다** · §6-254
      //
      // 전에는 편집 방식이 「포인트 곡선」일 때만 켜졌다(`pointMode`) ·
      // 그래프에서 포인트를 골라 놓고도 방식을 먼저 바꾸지 않으면 추가·삭제가
      // 회색이라, 왜 안 되는지 알 수 없었다.
      //
      // 가르는 것은 편집 방식이 아니라 **무엇을 고르는 중인가**다 ·
      // 「구간 선택」 쪽에 서 있으면 포인트를 건드리지 않고, 그 외에는
      // 포인트를 고른 그대로 다룰 수 있어야 한다.
      const candidate = editor?.pendingPointCandidate;
      const canAddPoint = !rangeChosen
        && Boolean(candidate)
        && !editor?.preview
        && editorSelectedMotionIds().length === 1;
      el.studioEditorPointAddButton.disabled = !canAddPoint;
      el.studioEditorPointAddButton.title = candidate
        ? `${candidate.timeSec.toFixed(2)}초 · ${candidate.valueDeg.toFixed(3)}°`
        : '그래프에서 추가할 위치를 먼저 선택하세요';
    }
    [el.studioEditorPointTime, el.studioEditorPointValue, el.studioEditorPointMode].forEach((field) => {
      // 고른 포인트의 값을 고치는 칸도 같은 기준을 쓴다 · §6-254
      if (field) field.disabled = rangeChosen || !point || !editablePointCurve;
    });
    if (el.studioEditorPointDeleteButton) {
      // 포인트 선택 모드면 언제든 된다 · §6-254
      const canDeletePoint = !rangeChosen
        && Boolean(point)
        && editablePointCurve
        && (editor?.pointDraft?.points?.length || 0) > 2;
      el.studioEditorPointDeleteButton.disabled = !canDeletePoint;
      el.studioEditorPointDeleteButton.title = canDeletePoint
        ? '선택한 포인트만 삭제하고 남은 포인트로 곡선을 다시 계산합니다'
        : '곡선을 유지하려면 포인트가 최소 2개 필요합니다';
    }
    if (el.studioEditorRangeStatus) {
      el.studioEditorRangeStatus.textContent = rangeReady
        ? `${selectedIds.length}개 축 · `
          + `${rangeBounds.startSec.toFixed(2)}초 ~ `
          + `${rangeBounds.endSec.toFixed(2)}초 · `
          + `${rangePointTargets.length}개 포인트 · `
          + `${selectedTimeRange.start.motionId} → ${selectedTimeRange.end.motionId}`
        : rangeSelecting
          ? editor?.rangeSelection?.phase === 'awaiting_end'
            ? '선택된 축에서 종료 포인트를 선택하세요. 다른 축도 가능합니다.'
            : '선택된 축에서 시작 포인트를 선택하세요.'
          : '구간 선택을 누른 뒤 시작·종료 포인트를 선택하세요.';
      el.studioEditorRangeStatus.classList.toggle('ready', rangeReady);
    }
    if (el.studioEditorPointSelectButton) {
      // 선택 방식 두 개는 함께 갱신한다 · 하나만 눌린 것처럼 보여야 한다 · §6-121
      el.studioEditorPointSelectButton.disabled = Boolean(editor?.preview);
      el.studioEditorPointSelectButton.setAttribute(
        'aria-pressed', rangeChosen ? 'false' : 'true',
      );
      el.studioEditorPointSelectButton.classList.toggle('on', !rangeChosen);
      el.studioEditorPointSelectButton.title = editor?.preview
        ? '현재 결과 미리보기를 먼저 편집 반영하거나 취소하세요'
        : '그래프의 포인트 하나를 눌러 고릅니다';
    }
    if (el.studioEditorRangeSelectButton) {
      el.studioEditorRangeSelectButton.disabled = Boolean(editor?.preview);
      el.studioEditorRangeSelectButton.textContent = '구간 선택';
      el.studioEditorRangeSelectButton.setAttribute(
        'aria-pressed', rangeChosen ? 'true' : 'false',
      );
      el.studioEditorRangeSelectButton.classList.toggle('on', rangeChosen);
      el.studioEditorRangeSelectButton.title = editor?.preview
        ? '현재 결과 미리보기를 먼저 편집 반영하거나 취소하세요'
        : '구간의 시작 포인트와 종료 포인트를 차례로 선택합니다';
    }
    // 구간 복사·삭제는 **고른 축 전부**를 다룬다 · 켜지는 조건도 시간 범위로
    // 본다 · 시작·종료가 서로 다른 축이어도 된다 · §6-122
    const rangeUsable = Boolean(selectedTimeRange) && rangePointTargets.length > 0;
    if (el.studioEditorRangeCopyTarget) {
      el.studioEditorRangeCopyTarget.disabled = !rangeUsable || Boolean(editor?.preview);
    }
    if (el.studioEditorRangeCopyButton) {
      el.studioEditorRangeCopyButton.disabled = !rangeUsable || Boolean(editor?.preview);
      el.studioEditorRangeCopyButton.title = rangeUsable
        ? '선택한 축 전부에서 이 구간을 복사해 「복사 시작(초)」 자리에 붙입니다'
        : '「구간 선택」을 누르고 시작·종료 포인트를 선택하세요';
    }
    if (el.studioEditorRangeDeleteButton) {
      el.studioEditorRangeDeleteButton.disabled = !rangeUsable || Boolean(editor?.preview);
      el.studioEditorRangeDeleteButton.title = rangeUsable
        ? '선택한 축 전부에서 이 구간의 포인트를 지웁니다'
        : '「구간 선택」을 누르고 시작·종료 포인트를 선택하세요';
    }
    if (el.studioEditorPointCurveOrder) {
      el.studioEditorPointCurveOrder.disabled = !pointMode || !editablePointCurve;
      el.studioEditorPointCurveOrder.value = String(motionStudioPointCurveOrder(
        editor?.pointDraft?.interpolation_order,
        editor?.pointCurveOrder,
      ));
    }
    if (el.studioEditorPointTimelineEnd) {
      el.studioEditorPointTimelineEnd.disabled = !pointMode;
      if (document.activeElement !== el.studioEditorPointTimelineEnd) {
        el.studioEditorPointTimelineEnd.value = Number(
          editor?.pointTimelineEnd
          || motionStudioPointCurveViewEnd(editorDuration(editor?.working)),
        ).toFixed(2);
      }
    }
    if (!point) {
      if (el.studioEditorPointTime) el.studioEditorPointTime.value = '';
      if (el.studioEditorPointValue) el.studioEditorPointValue.value = '';
      return;
    }
    if (el.studioEditorPointTime && document.activeElement !== el.studioEditorPointTime) {
      el.studioEditorPointTime.value = Number(point.time_sec).toFixed(2);
    }
    if (el.studioEditorPointValue && document.activeElement !== el.studioEditorPointValue) {
      el.studioEditorPointValue.value = Number(point.value_deg).toFixed(3);
    }
    if (el.studioEditorPointMode) el.studioEditorPointMode.value = point.tangent_mode || 'auto';
  }

  function editorSelectedMotionIds() {
    return [...(el.studioEditorAxisList?.querySelectorAll('input:checked') || [])]
      .map((input) => input.value);
  }

  function selectedEditorMotionAxisRange() {
    const selectedIds = editorSelectedMotionIds();
    if (selectedIds.length !== 1) return null;
    return motionStudioMotionAxisRange(activeMapping()?.rows || [], selectedIds[0]);
  }

  function resetEditorValueView({
    unlock = false,
    preserveLockedRange = false,
  } = {}) {
    const editor = state.editor;
    if (!editor) return;
    const unlockedView = preserveLockedRange
      ? motionStudioValueViewAfterRangeUnlock(editor.valueRangeLock)
      : null;
    editor.valueScale = 1;
    editor.valueOffset = 0;
    editor.valueView = unlockedView;
    if (unlock) editor.valueRangeLock = null;
  }

  function refreshEditorAxisControls(preferredSelection = null, layerOverride = null) {
    const editor = state.editor;
    if (!editor) return;
    const previousSelection = preferredSelection || new Set(editorSelectedMotionIds());
    const displayedLayer = layerOverride || editor.preview || editor.working;
    const ids = editorMotionIds(displayedLayer);
    if (el.studioEditorAxisList) {
      el.studioEditorAxisList.innerHTML = ids.map((motionId) => (
        `<label><input type="checkbox" value="${escapeHtml(motionId)}"${
          previousSelection.has(motionId) ? ' checked' : ''
        }><span>${escapeHtml(editorAxisLabel(motionId))}</span></label>`
      )).join('');
    }

    if (el.studioEditorAddAxisButton) {
      el.studioEditorAddAxisButton.disabled = !el.studioEditorAddAxisSelect?.value.trim()
        || Boolean(editor.preview);
    }
    if (el.studioEditorCopyAxisSource) {
      const previousSource = el.studioEditorCopyAxisSource.value;
      el.studioEditorCopyAxisSource.innerHTML = ids.length
        ? ids.map((motionId) => (
          `<option value="${escapeHtml(motionId)}">${escapeHtml(editorAxisLabel(motionId))}</option>`
        )).join('')
        : '<option value="">원본 축 없음</option>';
      if (ids.includes(previousSource)) el.studioEditorCopyAxisSource.value = previousSource;
    }
    if (el.studioEditorCopyAxisTarget) {
      const targetId = el.studioEditorCopyAxisTarget.value.trim();
      if (targetId && ids.includes(targetId)) el.studioEditorCopyAxisTarget.value = '';
    }
    if (el.studioEditorCopyAxisButton) {
      el.studioEditorCopyAxisButton.disabled = !ids.length
        || !el.studioEditorCopyAxisTarget?.value.trim()
        || Boolean(editor.preview);
    }
    if (el.studioEditorDeleteAxisButton) {
      el.studioEditorDeleteAxisButton.disabled = !previousSelection.size
        || Boolean(editor.preview);
    }
  }

  function setEditorMessage(message, error = false) {
    if (!el.studioEditorMessage) return;
    el.studioEditorMessage.textContent = message || '';
    el.studioEditorMessage.classList.toggle('error-text', error);
  }

  function renderEditorValidationDetails() {
    if (!el.studioEditorValidationDetails) return;
    const editor = state.editor;
    const validation = editor?.previewValidation || editor?.validation;
    const groups = motionStudioRangeWarningGroups(validation?.range_warnings);
    el.studioEditorValidationDetails.classList.toggle('hidden', !groups.length);
    if (!groups.length) {
      el.studioEditorValidationDetails.replaceChildren();
      return;
    }
    const selectedIds = new Set(editorSelectedMotionIds());
    el.studioEditorValidationDetails.innerHTML = groups.map((group) => {
      const actual = group.belowLower && group.aboveUpper
        ? `실제 ${group.minimumDeg.toFixed(3)}° ~ ${group.maximumDeg.toFixed(3)}°`
        : group.belowLower
          ? `실제 최소 ${group.minimumDeg.toFixed(3)}°`
          : `실제 최대 ${group.maximumDeg.toFixed(3)}°`;
      const periods = group.segments.map((segment) => (
        Math.abs(segment.endSec - segment.startSec) < 0.0005
          ? `${segment.startSec.toFixed(3)}초`
          : `${segment.startSec.toFixed(3)}~${segment.endSec.toFixed(3)}초`
      )).join(', ');
      const visibility = selectedIds.has(group.motionId)
        ? '그래프 표시 중'
        : '그래프에서 숨김';
      return `<div><strong>${escapeHtml(editorAxisLabel(group.motionId))}</strong>`
        + ` · ${visibility}`
        + ` · 허용 ${group.lowerDeg.toFixed(3)}° ~ ${group.upperDeg.toFixed(3)}°`
        + ` · ${actual}`
        + ` · 초과 ${group.count}점`
        + ` · ${escapeHtml(periods)}</div>`;
    }).join('');
  }

  function editorDuration(layer) {
    return motionStudioLayerDuration(layer);
  }

  function editorTimeBounds(layer) {
    const times = (layer?.frames || [])
      .map((frame) => Number(frame.time_sec))
      .filter((timeSec) => Number.isFinite(timeSec) && timeSec >= 0);
    return {
      start: times.length ? minOf(times) : 0,
      end: times.length ? maxOf(times) : MOTION_STUDIO_PERIOD_SEC,
    };
  }

  function openLayerEditor(layer) {
    if (!layer || layer.locked) return;
    const duration = editorDuration(layer);
    const operation = preferredEditorEditOperation;
    if (el.studioEditorOperation) el.studioEditorOperation.value = operation;
    const pointTimelineEnd = motionStudioPointCurveViewEnd(duration);
    state.editor = createMotionStudioEditorSession({
      layer,
      operation,
      duration,
      pointTimelineEnd,
      rangeWarnings: (state.composition?.range_warnings || []).filter(
        (warning) => String(warning.layer_id || '') === String(layer.layer_id || ''),
      ),
    });
    if (el.studioEditorTitle) el.studioEditorTitle.textContent = `레이어 편집 · ${layer.name}`;
    if (el.studioEditorSubtitle) el.studioEditorSubtitle.textContent = '편집 반영 0회';
    refreshEditorAxisControls(new Set(editorMotionIds(layer)), layer);
    el.studioLayerEditorModal?.classList.remove('hidden');
    document.body.classList.add('modal-open');
    const appliedCurveCount = editorPointCurves(layer).length;
    setEditorMessage(
      appliedCurveCount
        ? `반영된 포인트 곡선 ${appliedCurveCount}개 · 편집할 포인트를 선택하세요.`
        : '일반 모션은 Motion ID를 하나 선택해 전체 포인트를 생성한 뒤 편집하세요.',
    );
    renderEditor();
  }

  function closeLayerEditor() {
    editorGraphScheduler.cancel();
    state.editor = null;
    el.studioLayerEditorModal?.classList.add('hidden');
    el.studioEditorSaveConfirmModal?.classList.add('hidden');
    document.body.classList.remove('modal-open');
  }

  function renderEditorControls() {
    const editor = state.editor;
    const operation = el.studioEditorOperation?.value || 'value_offset';
    const pointMode = operation === 'point_curve';
    const selectedIds = editorSelectedMotionIds();
    const selectedAxisPointBacked = selectedIds.length === 1
      && !layerPointCoverageIssues(editor?.working).includes(selectedIds[0]);
    const appliedPointCurve = pointCurveIsApplied(editor, editor?.pointDraft?.curve_id);
    const selectedAppliedPointCurve = editorPointCurves(editor?.working).some(
      (curve) => selectedIds.includes(String(curve.motion_id || '')),
    );
    const creatablePointCurve = pointMode && pointCurveCanBeCreated(editor);
    const workingPointCurve = Boolean(storedCurveForDraft(editor));
    const pointRangeReady = Boolean(selectedEditorTimeRange(editor));
    const pointDraftDirty = pointDraftHasUnsavedChanges(editor);
    const hasTransientChange = Boolean(editor?.preview) || pointDraftDirty;
    // 되돌릴 것이 있는가 · §6-124
    //
    // 잘못 고른 구간이나 포인트도 되돌릴 거리다 · 전에는 결과 미리보기나
    // 포인트 변경이 있을 때만 「실행 취소」가 켜져서, 구간을 잘못 잡으면
    // 물릴 방법이 없었다.
    const hasPickToCancel = Boolean(
      motionStudioRangePicked(editor) || editor?.selectedPointId,
    );
    const layerDirty = motionStudioEditorLayerIsDirty(editor, motionStudioLayerDataEqual);
    if (editor?.saveState === 'failed'
      && editor.saveFailureFingerprint !== motionStudioEditorFailureFingerprint(editor)) {
      editor.saveState = 'dirty';
      editor.saveError = '';
    }
    let saveState = editor?.saveState || 'saved';
    if (editor?.preview) saveState = 'preview';
    else if (!['saving', 'failed'].includes(saveState)) {
      saveState = layerDirty || pointDraftDirty ? 'dirty' : 'saved';
    }
    if (el.studioEditorUndoButton) {
      el.studioEditorUndoButton.disabled = (
        !hasTransientChange && !hasPickToCancel && !editor?.undo.length
      );
      el.studioEditorUndoButton.title = editor?.preview
        ? '결과 미리보기를 취소합니다'
        : (pointDraftDirty
          ? '반영 전 포인트 변경을 취소합니다'
          : (hasPickToCancel
            ? '고른 구간·포인트를 취소합니다'
            : '마지막으로 반영한 편집을 되돌립니다'));
    }
    if (el.studioEditorRedoButton) {
      el.studioEditorRedoButton.disabled = hasTransientChange || !editor?.redo.length;
    }
    if (el.studioEditorUpdateButton) el.studioEditorUpdateButton.disabled = !editor?.preview;
    if (el.studioEditorApplyButton) {
      // 무엇이 있어야 미리보기를 할 수 있는가 · §6-131
      //
      // 포인트 곡선 편집은 **편집 중인 초안**이 있어야 한다 · 그런데 구간
      // 편집(시간·모션값 이동·배율)은 초안과 아무 상관이 없다 · 고른 축의
      // 고른 구간에 포인트가 있으면 된다.
      //
      // 전에는 둘 다 `appliedPointCurve` 하나로 막았다 · 포인트 곡선을 먼저
      // 골라야만 구간 편집을 할 수 있던 시절의 규칙이다 · 이제는 포인트를
      // 누르지 않고 구간만 잡을 수 있어서, 전체 포인트를 생성해 두고도
      // 「변경 미리보기」가 끝까지 눌리지 않았다.
      //
      // 구간 복사·삭제는 이미 이 규칙을 쓴다 (`rangeUsable`) · 같은 질문에
      // 같은 답을 주도록 맞춘다.
      const rangeEditReady = pointRangeReady
        && selectedEditorTimeRangePoints(editor).length > 0;
      el.studioEditorApplyButton.disabled = Boolean(editor?.preview) || (pointMode
        ? (!appliedPointCurve && !creatablePointCurve)
        : !rangeEditReady);
      el.studioEditorApplyButton.title = pointMode || rangeEditReady
        ? '고른 구간·포인트에 편집을 적용해 결과를 미리 봅니다'
        : '먼저 「구간 선택」으로 시작·끝을 찍으세요 · 그 구간에 포인트가 있어야 합니다';
    }
    if (el.studioEditorSaveButton) {
      el.studioEditorSaveButton.disabled = (
        !['dirty', 'failed'].includes(saveState)
        || pointDraftDirty
      );
    }
    if (el.studioEditorOperationTitle) el.studioEditorOperationTitle.textContent = '포인트 편집';
    document.querySelectorAll(
      '.studio-editor-conversion-controls select, .studio-editor-conversion-controls input',
    ).forEach((control) => {
      control.disabled = Boolean(editor?.preview) || pointMode || selectedAxisPointBacked;
    });
    if (el.studioEditorValueRangeLockButton) {
      const availableRange = selectedEditorMotionAxisRange();
      const locked = Boolean(editor?.valueRangeLock);
      el.studioEditorValueRangeLockButton.disabled = !locked && !availableRange;
      el.studioEditorValueRangeLockButton.textContent = locked
        ? '축 범위 해제'
        : '축 범위 고정';
      el.studioEditorValueRangeLockButton.setAttribute(
        'aria-pressed',
        locked ? 'true' : 'false',
      );
      el.studioEditorValueRangeLockButton.classList.toggle('on', locked);
      el.studioEditorValueRangeLockButton.title = locked
        ? `${editor.valueRangeLock.motionId} · `
          + `${editor.valueRangeLock.minValue}° ~ ${editor.valueRangeLock.maxValue}°`
        : availableRange
          ? `${availableRange.motionId} · `
            + `${availableRange.minValue}° ~ ${availableRange.maxValue}°`
          : '모션축 하나와 유효한 축 범위가 필요합니다';
    }
    if (el.studioEditorValueZoomInButton) {
      el.studioEditorValueZoomInButton.disabled = Boolean(editor?.valueRangeLock);
    }
    if (el.studioEditorValueZoomOutButton) {
      el.studioEditorValueZoomOutButton.disabled = Boolean(editor?.valueRangeLock);
    }
    if (el.studioEditorAddAxisButton) {
      el.studioEditorAddAxisButton.disabled = !el.studioEditorAddAxisSelect?.value
        || Boolean(editor?.preview);
    }
    if (el.studioEditorCopyAxisButton) {
      el.studioEditorCopyAxisButton.disabled = !el.studioEditorCopyAxisSource?.value
        || !el.studioEditorCopyAxisTarget?.value
        || Boolean(editor?.preview);
    }
    if (el.studioEditorDeleteAxisButton) {
      el.studioEditorDeleteAxisButton.disabled = !selectedIds.length
        || Boolean(editor?.preview);
    }
    if (el.studioEditorOperation) {
      el.studioEditorOperation.disabled = Boolean(editor?.preview);
    }
    el.studioEditorOperationButtons?.forEach((button) => {
      const active = button.dataset.studioEditorOperation === operation;
      button.setAttribute('aria-pressed', active ? 'true' : 'false');
      button.disabled = Boolean(editor?.preview) || !workingPointCurve;
    });
    if (el.studioEditorCreatePointsButton) {
      const selectedId = selectedIds.length === 1 ? selectedIds[0] : '';
      const alreadyPointBacked = selectedId
        && !layerPointCoverageIssues(editor?.working).includes(selectedId);
      el.studioEditorCreatePointsButton.disabled = (
        Boolean(editor?.preview)
        || pointMode
        || !selectedId
        || alreadyPointBacked
      );
    }
    document.querySelectorAll('[data-studio-editor-value]').forEach((field) => {
      const kind = field.dataset.studioEditorValue;
      field.classList.toggle('hidden', !(
        (operation === 'value_offset' && kind === 'offset')
        || ((operation === 'value_scale' || operation === 'time_scale') && kind === 'factor')
        || (operation === 'time_shift' && kind === 'delta')
        || (operation === 'point_curve' && kind === 'points')
      ));
    });
    if (el.studioEditorFactorLabel) {
      el.studioEditorFactorLabel.textContent = operation === 'time_scale'
        ? '시간 배율'
        : '동작 크기 배율';
    }
    if (el.studioEditorFactor) {
      el.studioEditorFactor.min = operation === 'value_scale' ? '-100' : '0.01';
      el.studioEditorFactor.max = '100';
    }
    if (el.studioEditorOperationHelp) {
      const help = {
        time_scale: '포인트 한 개는 0초, 두 개 이상은 첫 포인트를 기준으로 시간을 조절합니다.',
        value_scale: '포인트 한 개는 0°, 두 개 이상은 첫 포인트를 기준으로 모션값을 조절합니다. 음수는 곡선을 상하 반전합니다.',
        time_shift: '선택한 포인트 한 개 또는 포인트 범위를 시간축으로 이동합니다.',
        value_offset: '선택한 포인트 한 개 또는 포인트 범위의 모션값을 이동합니다.',
        point_curve: appliedPointCurve
          ? '작업본에 반영된 포인트 모션입니다. 포인트 추가·이동과 탄젠트 편집이 가능합니다.'
          : creatablePointCurve
            ? '새로 추가한 축입니다. 그래프를 클릭해 포인트를 두 개 이상 만드세요.'
            : '선택 축 전체에 포인트를 생성하고 작업본에 반영한 뒤 편집할 수 있습니다.',
      };
      el.studioEditorOperationHelp.textContent = help[operation] || '';
    }
    renderMotionStudioEditorPresentation(el, {
      saveState,
      savedAt: editor?.savedAt || '',
      saveError: editor?.saveError || '',
      inspector: motionStudioEditorInspectorState({
        preview: Boolean(editor?.preview),
        pointDraftUnsaved: pointDraftDirty
          || (workingPointCurve && !appliedPointCurve),
        appliedPointCurve: appliedPointCurve || selectedAppliedPointCurve,
        pointSelected: Boolean(selectedDraftPoint(editor)),
        rangeSelected: pointRangeReady,
      }),
    });
    syncPointControls();
  }

  function drawEditorGraph() {
    const editor = state.editor;
    if (!editor) return;
    const displayedLayer = editor.preview || editor.working;
    drawMotionStudioEditorGraph({
      editor,
      canvas: el.studioEditorGraph,
      legend: el.studioEditorLegend,
      originalTrackMap: cachedLayerTracks(editor.original),
      workingTrackMap: cachedLayerTracks(displayedLayer),
      selectedMotionIds: editorSelectedMotionIds(),
      operation: el.studioEditorOperation?.value || '',
      selectionStartText: Number.isFinite(editor.rangeSelection?.start?.timeSec)
        ? String(editor.rangeSelection.start.timeSec) : '',
      selectionEndText: Number.isFinite(editor.rangeSelection?.end?.timeSec)
        ? String(editor.rangeSelection.end.timeSec) : '',
      // 모션축 한계 · 그래프에 점선으로 · §6-120
      axisLimits: editorSelectedMotionIds()
        .map((motionId) => motionStudioMotionAxisRange(
          activeMapping()?.rows || [], motionId,
        ))
        .filter(Boolean),
      devicePixelRatio: window.devicePixelRatio || 1,
    });
    renderAxisEndpoints();
  }

  /** 축 하나만 골랐을 때 시작·끝을 표로 적는다 · §6-249
   *
   * 그래프를 눈으로 훑어 「어디서 시작해 어디서 끝나나」를 읽던 것을 숫자로
   * 적어 준다 · 편집본과 원본을 나란히 두어 얼마나 바꿨는지 함께 보인다.
   *
   * **하나만 골랐을 때만** 보여준다 · 여럿이면 어느 줄이 어느 축인지
   * 헷갈리고, 그건 이미 그래프가 색으로 말하고 있다.
   */
  /** 고른 축 하나의 시작·끝 · **네 가지만** · §6-249
   *
   * 시작 시간 · 시작 각도 · 끝 시간 · 끝 각도 · 그뿐이다 · 길이도 변화량도
   * 원본과의 비교도 넣었다가 뺐다 · 표가 커지면 옆 단추들이 밀린다.
   *
   * 자리는 왼쪽 사이드바의 「선택 축 삭제」 아래 빈 곳이다 · 그래프 밑에
   * 두었더니 아래 편집 단추들이 밀렸다.
   */
  function renderAxisEndpoints() {
    const box = el.studioEditorAxisEndpoints;
    const rows = el.studioEditorAxisEndpointRows;
    if (!box || !rows) return;
    const editor = state.editor;
    const selectedIds = editorSelectedMotionIds();
    const hide = () => { box.classList.add('hidden'); rows.innerHTML = ''; };
    if (!editor || selectedIds.length !== 1) return hide();
    const motionId = String(selectedIds[0]);
    const ends = motionStudioAxisEndpoints(
      cachedLayerTracks(editor.preview || editor.working).get(motionId),
    );
    if (!ends) return hide();
    if (el.studioEditorAxisEndpointsTitle) {
      el.studioEditorAxisEndpointsTitle.textContent = `모션 ID ${motionId}`;
    }
    const line = (label, timeSec, valueDeg) => (
      `<tr>
        <th scope="row">${displayText(label)}</th>
        <td class="mono">${displayText(Number(timeSec).toFixed(3))}s</td>
        <td class="mono">${displayText(Number(valueDeg).toFixed(2))}°</td>
      </tr>`
    );
    rows.innerHTML = (
      line('시작', ends.startTimeSec, ends.startValueDeg)
      + line('끝', ends.endTimeSec, ends.endValueDeg)
    );
    box.classList.remove('hidden');
  }

  const editorGraphScheduler = createMotionStudioGraphScheduler({
    draw: drawEditorGraph,
    requestFrame: (callback) => window.requestAnimationFrame(callback),
    cancelFrame: (frameId) => window.cancelAnimationFrame(frameId),
  });
  const editorViewport = createMotionStudioEditorViewportController({
    el,
    getEditor: () => state.editor,
    drawGraph: drawEditorGraph,
    scheduleGraph: editorGraphScheduler.schedule,
    renderEditor,
    setMessage: setEditorMessage,
    resetValueView: resetEditorValueView,
    selectedMotionAxisRange: selectedEditorMotionAxisRange,
    selectedPointRange: selectedEditorPointRange,
    editorDuration,
  });

  function renderEditor() {
    if (!state.editor) return;
    renderEditorControls();
    renderEditorValidationDetails();
    drawEditorGraph();
  }

  function refreshEditorTimeline(layer, previousLayer = null) {
    const editor = state.editor;
    if (!synchronizeMotionStudioEditorTimeline(editor, layer, previousLayer)) return false;
    return true;
  }

  function discardEditorPreview(message = '') {
    const editor = state.editor;
    if (!editor) return false;
    if (!editor.preview) {
      if (!editor.operationReport) return false;
      editor.operationReport = null;
      editor.previewOperation = '';
      if (message) setEditorMessage(message);
      renderEditor();
      return true;
    }
    const preview = editor.preview;
    const discardedOperation = String(
      editor.previewOperation || editor.operationReport?.operation || '',
    );
    editor.preview = null;
    editor.previewValidation = null;
    editor.operationReport = null;
    editor.previewOperation = '';
    editor.pendingCurveId = '';
    refreshEditorTimeline(editor.working, preview);
    refreshEditorAxisControls(null, editor.working);
    if (discardedOperation === 'point_curve') {
      const stored = storedCurveForDraft(editor);
      if (stored) {
        loadPointDraft(stored, editor.selectedPointId);
        restoreEditorEditOperation(editor);
      }
    }
    if (el.studioEditorSubtitle) {
      el.studioEditorSubtitle.textContent = `편집 반영 ${editor.undo.length}회`;
    }
    if (message) setEditorMessage(message);
    renderEditor();
    return true;
  }

  function updateEditorWorkingCopy() {
    const editor = state.editor;
    if (!editor?.preview) {
      setEditorMessage('먼저 결과 미리보기 버튼으로 편집 결과를 확인하세요.', true);
      return;
    }
    const appliedOperation = String(
      editor.previewOperation || editor.operationReport?.operation || '',
    );
    const activeCurveId = appliedOperation === 'create_axis_point_curve'
      ? (editor.pendingCurveId || '')
      : (editor.pointDraft?.curve_id || editor.pendingCurveId || '');
    const selectedPointId = editor.selectedPointId;
    const previousIds = new Set(editorMotionIds(editor.working));
    const selectedIds = new Set(editorSelectedMotionIds());
    const previewIds = editorMotionIds(editor.preview);
    previewIds.forEach((motionId) => {
      if (!previousIds.has(motionId)) selectedIds.add(motionId);
    });
    editor.undo.push({
      layer: clone(editor.working),
      validation: clone(editor.validation),
      curveId: String(editor.pointDraft?.curve_id || ''),
      selectedPointId: String(editor.selectedPointId || ''),
    });
    editor.redo = [];
    editor.working = clone(editor.preview);
    editor.validation = clone(
      editor.previewValidation || { conflicts: [], playable: true },
    );
    editor.preview = null;
    editor.previewValidation = null;
    editor.operationReport = null;
    editor.previewOperation = '';
    editor.pendingCurveId = '';
    if (appliedOperation === 'create_axis_point_curve') {
      editor.pointDraft = null;
      selectPoint(editor, '');
    }
    if (
      appliedOperation === 'delete_axis'
      && editor.pointDraft
      && !editorMotionIds(editor.working).includes(String(editor.pointDraft.motion_id || ''))
    ) {
      editor.pointDraft = null;
      selectPoint(editor, '');
    }
    if (activeCurveId) {
      const updatedCurve = editorPointCurves(editor.working).find(
        (curve) => curve.curve_id === activeCurveId,
      );
      if (updatedCurve) loadPointDraft(
        updatedCurve,
        appliedOperation === 'create_axis_point_curve' ? '' : selectedPointId,
      );
    }
    if (appliedOperation === 'point_curve') {
      restoreEditorEditOperation(editor);
    }
    refreshEditorAxisControls(selectedIds, editor.working);
    if (el.studioEditorSubtitle) {
      el.studioEditorSubtitle.textContent = `편집 반영 ${editor.undo.length}회`;
    }
    const appliedRangeWarningCount = editor.validation?.range_warnings?.length || 0;
    const appliedMessage = appliedOperation === 'create_axis_point_curve'
      ? '선택 축 전체 포인트 생성을 작업본에 반영했습니다. 바로 포인트를 편집할 수 있습니다.'
      : `편집 반영 ${editor.undo.length}회 완료 · 다음 편집을 계속할 수 있습니다.`;
    setEditorMessage(
      appliedMessage + (appliedRangeWarningCount
        ? ` · 축 설정 범위 초과 경고 ${appliedRangeWarningCount}건`
        : ''),
      appliedRangeWarningCount > 0,
    );
    renderEditor();
  }

  async function previewEditorAxisAddition() {
    const editor = state.editor;
    if (!editor) return;
    if (editor.preview) {
      setEditorMessage('현재 결과 미리보기를 먼저 편집 반영하거나 취소하세요.', true);
      return;
    }
    const motionId = String(el.studioEditorAddAxisSelect?.value || '').trim();
    const initialValue = Number(el.studioEditorAddAxisValue?.value);
    if (!motionId) {
      setEditorMessage('추가할 Motion ID를 입력하세요.', true);
      return;
    }
    if (!motionStudioValidMotionId(motionId)) {
      setEditorMessage('Motion ID는 양의 정수-양의 정수 형식으로 입력하세요. 예: 1-2', true);
      return;
    }
    if (!Number.isFinite(initialValue)) {
      setEditorMessage('축 초기 모션값을 숫자로 입력하세요.', true);
      return;
    }
    el.studioEditorAddAxisButton.disabled = true;
    try {
      const result = await editMotionStudioLayer({
        layer: editor.working,
        project: editorValidationProject(editor.working, [motionId]),
        operation: 'add_axis',
        motion_ids: [motionId],
        initial_value_deg: initialValue,
        mapping_rows: activeMapping()?.rows || [],
      });
      if (result.success === false) throw new Error(result.message || '축 추가 실패');
      editor.previewOperation = 'add_axis';
      editor.preview = clone(result.layer);
      refreshEditorTimeline(editor.preview, editor.working);
      editor.previewValidation = result.validation
        || { conflicts: [], playable: true };
      refreshEditorAxisControls(new Set([motionId]), editor.preview);
      if (el.studioEditorSubtitle) {
        el.studioEditorSubtitle.textContent = '축 추가 미리보기 · 편집 반영 전';
      }
      setEditorMessage(
        `${motionId} 축 추가 미리보기 완료 · 초기값 ${initialValue.toFixed(3)}° · 확인 후 편집 반영하세요.`,
      );
      renderEditor();
    } catch (error) {
      setEditorMessage(error.message || String(error), true);
    } finally {
      renderEditorControls();
    }
  }

  async function previewEditorAxisCopy() {
    const editor = state.editor;
    if (!editor) return;
    if (editor.preview) {
      setEditorMessage('현재 결과 미리보기를 먼저 편집 반영하거나 취소하세요.', true);
      return;
    }
    const sourceMotionId = String(el.studioEditorCopyAxisSource?.value || '').trim();
    const targetMotionId = String(el.studioEditorCopyAxisTarget?.value || '').trim();
    if (!sourceMotionId || !targetMotionId) {
      setEditorMessage('복사할 원본 축을 선택하고 대상 Motion ID를 입력하세요.', true);
      return;
    }
    if (!motionStudioValidMotionId(targetMotionId)) {
      setEditorMessage('대상 Motion ID는 양의 정수-양의 정수 형식으로 입력하세요. 예: 1-2', true);
      return;
    }
    el.studioEditorCopyAxisButton.disabled = true;
    try {
      const result = await editMotionStudioLayer({
        layer: editor.working,
        project: editorValidationProject(
          editor.working, [sourceMotionId, targetMotionId],
        ),
        operation: 'copy_axis',
        source_motion_id: sourceMotionId,
        motion_ids: [targetMotionId],
        mapping_rows: activeMapping()?.rows || [],
      });
      if (result.success === false) throw new Error(result.message || '축 복사 실패');
      editor.previewOperation = 'copy_axis';
      editor.preview = clone(result.layer);
      refreshEditorTimeline(editor.preview, editor.working);
      editor.previewValidation = result.validation
        || { conflicts: [], playable: true };
      refreshEditorAxisControls(new Set([targetMotionId]), editor.preview);
      if (el.studioEditorSubtitle) {
        el.studioEditorSubtitle.textContent = '축 복사 미리보기 · 편집 반영 전';
      }
      setEditorMessage(
        `${sourceMotionId} → ${targetMotionId} 축 복사 미리보기 완료 · 확인 후 편집 반영하세요.`,
      );
      renderEditor();
    } catch (error) {
      setEditorMessage(error.message || String(error), true);
    } finally {
      renderEditorControls();
    }
  }

  async function previewEditorAxisDeletion() {
    const editor = state.editor;
    if (!editor) return;
    if (editor.preview) {
      setEditorMessage('현재 결과 미리보기를 먼저 편집 반영하거나 취소하세요.', true);
      return;
    }
    if (pointDraftHasUnsavedChanges(editor)) {
      setEditorMessage('포인트 변경을 먼저 미리보기하고 작업본에 반영하세요.', true);
      return;
    }
    const motionIds = editorSelectedMotionIds();
    if (!motionIds.length) {
      setEditorMessage('삭제할 Motion ID를 선택하세요.', true);
      return;
    }
    const confirmed = await showConfirm(
      `현재 레이어에서 다음 축 데이터를 삭제할까요?\n${motionIds.join(', ')}`,
      {
        title: '선택 축 삭제',
        confirmLabel: '삭제 미리보기',
        tone: 'danger',
      },
    );
    if (!confirmed || state.editor !== editor) return;
    el.studioEditorDeleteAxisButton.disabled = true;
    try {
      const result = await editMotionStudioLayer({
        layer: editor.working,
        project: editorValidationProject(editor.working, motionIds),
        operation: 'delete_axis',
        motion_ids: motionIds,
        mapping_rows: activeMapping()?.rows || [],
      });
      if (result.success === false) throw new Error(result.message || '축 삭제 실패');
      editor.previewOperation = 'delete_axis';
      editor.preview = clone(result.layer);
      refreshEditorTimeline(editor.preview, editor.working);
      editor.previewValidation = result.validation
        || { conflicts: [], range_warnings: [], playable: true };
      const remainingIds = new Set(editorMotionIds(editor.preview));
      refreshEditorAxisControls(remainingIds, editor.preview);
      if (el.studioEditorSubtitle) {
        el.studioEditorSubtitle.textContent = '축 삭제 미리보기 · 편집 반영 전';
      }
      setEditorMessage(
        `${motionIds.join(', ')} 축 삭제 미리보기 완료 · 확인 후 작업본에 반영하세요.`,
      );
      renderEditor();
    } catch (error) {
      setEditorMessage(error.message || String(error), true);
    } finally {
      renderEditorControls();
    }
  }

  async function applyEditorOperation(operationOverride = '') {
    const editor = state.editor;
    if (!editor) return;
    const operation = typeof operationOverride === 'string' && operationOverride
      ? operationOverride
      : (el.studioEditorOperation?.value || 'value_offset');
    const pointMetadataOperation = [
      'point_curve', 'create_axis_point_curve',
    ].includes(operation);
    const motionIds = editorSelectedMotionIds();
    if (!motionIds.length) { setEditorMessage('편집할 Motion ID를 선택하세요', true); return; }
    if (
      !pointMetadataOperation
      && !selectedEditorTimeRange(editor)
    ) {
      setEditorMessage('선택된 축에서 시간이 다른 포인트 두 개를 선택하세요.', true);
      return;
    }
    if (operation === 'point_curve' && (!editor.pointDraft || editor.pointDraft.points.length < 2)) {
      setEditorMessage('그래프에서 같은 Motion ID의 포인트를 두 개 이상 만드세요.', true);
      return;
    }
    const rangeBounds = motionStudioRangeSelectionBounds(editor);
    const payload = {
      layer: editor.working,
      project: editorValidationProject(editor.working, motionIds),
      operation,
      motion_ids: motionIds,
      start_sec: Number(rangeBounds?.startSec || 0),
      end_sec: Number(rangeBounds?.endSec || 0),
      offset_deg: Number(el.studioEditorOffset?.value || 0),
      factor: Number(el.studioEditorFactor?.value || 1),
      delta_sec: Number(el.studioEditorDelta?.value || 0) / 1000,
      // 구간 복사가 붙일 자리 · 고른 축 전부에 같은 시간으로 붙는다 · §6-122
      target_start_sec: Number(el.studioEditorRangeCopyTarget?.value || 0),
      interpolation_order: operation === 'point_curve'
        ? motionStudioPointCurveOrder(
          editor.pointDraft?.interpolation_order,
          editor.pointCurveOrder,
        )
        : 1,
      curve_id: editor.pendingCurveId || editor.pointDraft?.curve_id || '',
      points: operation === 'point_curve' ? clone(editor.pointDraft?.points || []) : [],
      approximation_tolerance_deg: Number(
        el.studioEditorApproximationTolerance?.value || 0.1,
      ),
      approximation_interpolation_order: Number(
        el.studioEditorApproximationOrder?.value || 3,
      ),
      approximation_maximum_points: Number(
        el.studioEditorApproximationMaximumPoints?.value || 50,
      ),
      mapping_rows: activeMapping()?.rows || [],
    };
    el.studioEditorApplyButton.disabled = true;
    if (el.studioEditorUpdateButton) el.studioEditorUpdateButton.disabled = true;
    try {
      const result = await editMotionStudioLayer(payload);
      if (result.success === false) throw new Error(result.message || '편집 실패');
      editor.operationReport = result.operation_report || null;
      editor.previewOperation = operation;
      editor.preview = clone(result.layer);
      refreshEditorTimeline(editor.preview, editor.working);
      if (operation === 'point_curve' && editor.pointDraft) {
        const calculated = editorPointCurves(editor.preview).find(
          (curve) => curve.curve_id === editor.pointDraft.curve_id,
        );
        if (calculated) loadPointDraft(calculated, editor.selectedPointId);
      }
      editor.previewValidation = result.validation
        || { conflicts: [], playable: true };
      if (el.studioEditorSubtitle) {
        el.studioEditorSubtitle.textContent = '결과 미리보기 · 편집 반영 전';
      }
      const issueCount = editor.previewValidation.conflicts?.length || 0;
      const rangeWarningCount = editor.previewValidation.range_warnings?.length || 0;
      const rangeWarningText = rangeWarningCount
        ? ` · 축 설정 범위 초과 경고 ${rangeWarningCount}건 (계속 진행 가능)`
        : '';
      if (operation === 'create_axis_point_curve') {
        const report = editor.operationReport || {};
        setEditorMessage(
          `선택 축 전체 포인트 생성 미리보기 · ${Number(report.interpolation_order || 1)}차 곡선 · `
          + `${Number(report.source_sample_count || 0)}개 모션점 → `
          + `${Number(report.point_count || 0)}개 포인트 · 최대 오차 `
          + `${Number(report.maximum_error_deg || 0).toFixed(4)}° · `
          + `편집 반영 후 바로 포인트를 편집할 수 있습니다.${rangeWarningText}`,
          rangeWarningCount > 0,
        );
      } else {
        const previewMessage = issueCount
          ? `결과 미리보기 · 충돌 ${issueCount}건 · 확인 후 값을 바꾸거나 편집 반영하세요${rangeWarningText}`
          : `결과 미리보기 완료 · 결과가 맞으면 편집 반영을 누르세요.${rangeWarningText}`;
        setEditorMessage(
          previewMessage,
          issueCount > 0 || rangeWarningCount > 0,
        );
      }
      renderEditor();
    } catch (error) {
      if (operation === 'create_axis_point_curve') editor.pendingCurveId = '';
      setEditorMessage(error.message || String(error), true);
    } finally {
      el.studioEditorApplyButton.disabled = false;
      renderEditorControls();
    }
  }

  function bind() {
    if (bound) return;
    bound = true;
    const onEditorAxisSelectionChange = () => {
      if (protectPointDraftAxisSelection()) return;
      // 더는 고르지 않은 축의 임시 작업본은 놓는다 · 그 축 그래프가 사라지는데
      // 편집 대상으로 남아 있으면 앞뒤가 안 맞는다 · §6-129
      const editor = state.editor;
      const stillSelected = new Set(editorSelectedMotionIds());
      if (
        editor?.pointDraft
        && !stillSelected.has(String(editor.pointDraft.motion_id || ''))
      ) {
        editor.pointDraft = null;
        selectPoint(editor, '');
      }
      resetEditorValueView({ unlock: true });
      clearPendingPointCandidate();
      clearEditorPointRange(state.editor);
      if (!discardEditorPreview('축 선택이 바뀌어 결과 미리보기를 취소했습니다.')) renderEditor();
    };
    const axisEditorController = createMotionStudioAxisEditorController({
      el,
      canChangeSelection: () => !protectPointDraftAxisSelection(),
      onSelectionChange: onEditorAxisSelectionChange,
      onAddAxis: previewEditorAxisAddition,
      onCopyAxis: previewEditorAxisCopy,
      onDeleteAxis: previewEditorAxisDeletion,
      onControlChange: renderEditorControls,
    });
    axisEditorController.bind();
    const onEditorOperationButton = (button) => {
      const operation = button.dataset.studioEditorOperation || '';
      if (!operation || !el.studioEditorOperation || button.disabled) return;
      el.studioEditorOperation.value = operation;
      el.studioEditorOperation.dispatchEvent(new Event('change', { bubbles: true }));
      const inputByOperation = {
        time_shift: el.studioEditorDelta,
        time_scale: el.studioEditorFactor,
        value_offset: el.studioEditorOffset,
        value_scale: el.studioEditorFactor,
      };
      window.requestAnimationFrame(() => inputByOperation[operation]?.focus());
    };
    const onEditorOperationChange = () => {
      const editor = state.editor;
      if (!editor) return;
      const nextOperation = el.studioEditorOperation.value;
      const previousOperation = editor.operation;
      if (editor.preview) {
        el.studioEditorOperation.value = editor.operation || 'time_scale';
        setEditorMessage(
          '현재 결과 미리보기를 먼저 편집 반영한 뒤 편집 기능을 바꾸세요.',
          true,
        );
        renderEditor();
        return;
      }
      if (
        editor.operation === 'point_curve'
        && nextOperation !== 'point_curve'
        && pointDraftHasUnsavedChanges(editor)
      ) {
        el.studioEditorOperation.value = 'point_curve';
        setEditorMessage(
          '변경한 포인트를 먼저 결과 미리보기하고 편집 반영한 뒤 다른 기능으로 이동하세요.',
          true,
        );
        renderEditor();
        return;
      }
      const leavingPointMode = (
        previousOperation === 'point_curve' && nextOperation !== 'point_curve'
      );
      if (
        nextOperation === 'point_curve'
        && POINT_RANGE_EDIT_OPERATIONS.has(previousOperation)
      ) {
        rememberEditorEditOperation(editor, previousOperation);
        editor.pointModeReturnOperation = previousOperation;
      } else if (POINT_RANGE_EDIT_OPERATIONS.has(nextOperation)) {
        rememberEditorEditOperation(editor, nextOperation);
        editor.pointModeReturnOperation = '';
      }
      editor.operation = nextOperation;
      clearPendingPointCandidate(editor);
      if (editor.operation === 'point_curve') {
        // 보고 있던 자리를 그대로 둔다 · §6-121
        //
        // 전에는 여기서 `viewStart = 0` 으로 되돌려, 확대해서 보던 자리가
        // 기능을 바꿀 때마다 날아갔다 · 「끝(초)」 입력칸이 쓰는
        // `pointTimelineEnd` 는 계산해 두되 보기 범위는 건드리지 않는다.
        editor.pointTimelineEnd = motionStudioPointCurveViewEnd(
          editorDuration(editor.working),
          editor.viewEnd,
          editor.pointTimelineEnd,
        );
      }
      renderEditor();
      if (leavingPointMode && !selectedEditorPointRange(editor)) {
        setEditorMessage(
          '구간 편집을 적용하려면 구간 선택을 누르고 시작·종료 포인트를 선택하세요.',
        );
      }
    };
    bindMotionStudioPointEditorEvents({
      state, el, selectedDraftPoint, discardEditorPreview, setEditorMessage,
      syncPointControls, editorDuration, clearEditorPointRange, renderEditor,
      editorSelectedMotionIds, clearPendingPointCandidate, pointCurveIsApplied,
      pointCurveCanBeCreated, editorId, selectedEditorPointRange,
      activatePointDraftMutation, selectedEditorTimeRange, applyEditorOperation,
    });
    [el.studioEditorOffset, el.studioEditorFactor, el.studioEditorDelta].forEach((input) => {
      input?.addEventListener('input', () => {
        discardEditorPreview('편집값이 바뀌어 결과 미리보기를 취소했습니다. 다시 계산하세요.');
      });
    });
    const onEditorCreatePoints = () => {
      const editor = state.editor;
      if (!editor) return;
      if (editorSelectedMotionIds().length !== 1) {
        setEditorMessage('전체 포인트를 생성할 Motion ID를 하나만 선택하세요.', true);
        return;
      }
      editor.pendingCurveId = editorId('curve');
      applyEditorOperation('create_axis_point_curve');
    };
    const discardEditor = async () => {
      if (
        (
          state.editor?.preview
          || (
            state.editor
            && !motionStudioLayerDataEqual(
              state.editor.original,
              state.editor.working,
            )
          )
          || pointDraftHasUnsavedChanges(state.editor)
        )
        && !await showConfirm(
          '저장하지 않은 편집 내용과 결과 미리보기를 버리고 닫을까요?',
          { title: '레이어 편집 닫기', confirmLabel: '변경 버리기', tone: 'warning' },
        )
      ) return;
      closeLayerEditor();
    };
    const onEditorUndo = () => {
      const editor = state.editor;
      if (!editor) return;
      if (editor.preview) {
        discardEditorPreview('결과 미리보기를 취소하고 현재 작업본으로 돌아왔습니다.');
        return;
      }
      if (pointDraftHasUnsavedChanges(editor)) {
        const stored = storedCurveForDraft(editor);
        if (stored) {
          loadPointDraft(stored, editor.selectedPointId);
          setEditorMessage('편집 반영 전 포인트 변경을 취소했습니다.');
        } else {
          editor.pointDraft = null;
          selectPoint(editor, '');
          setEditorMessage('편집 반영 전 포인트 작업을 취소했습니다.');
        }
        restoreEditorEditOperation(editor);
        renderEditor();
        return;
      }
      // 잘못 고른 구간·포인트부터 물린다 · 반영한 편집보다 가까운 실수다 · §6-124
      if (motionStudioRangePicked(editor) || editor.selectedPointId) {
        clearPicks(editor);
        setEditorMessage('고른 구간·포인트를 취소했습니다 · 다시 고르세요.');
        renderEditor();
        return;
      }
      if (!editor.undo.length) return;
      editor.redo.push(motionStudioEditorHistoryEntry(editor, clone));
      const previous = editor.undo.pop();
      const replacedLayer = editor.working;
      editor.working = previous.layer;
      editor.validation = previous.validation;
      const previousCurve = editorPointCurves(editor.working).find(
        (curve) => String(curve.curve_id || '') === String(previous.curveId || ''),
      );
      if (previousCurve) {
        loadPointDraft(previousCurve, previous.selectedPointId);
      } else {
        editor.pointDraft = null;
        selectPoint(editor, '');
      }
      refreshEditorTimeline(editor.working, replacedLayer);
      refreshEditorAxisControls(null, editor.working);
      if (el.studioEditorSubtitle) {
        el.studioEditorSubtitle.textContent = `편집 반영 ${editor.undo.length}회`;
      }
      setEditorMessage('직전 편집을 취소했습니다'); renderEditor();
    };
    const onEditorRedo = () => {
      const editor = state.editor;
      if (!editor?.redo.length) return;
      discardEditorPreview();
      editor.undo.push(motionStudioEditorHistoryEntry(editor, clone));
      const following = editor.redo.pop();
      const replacedLayer = editor.working;
      editor.working = following.layer;
      editor.validation = following.validation;
      const followingCurve = editorPointCurves(editor.working).find(
        (curve) => String(curve.curve_id || '') === String(following.curveId || ''),
      );
      if (followingCurve) {
        loadPointDraft(followingCurve, following.selectedPointId);
      } else {
        editor.pointDraft = null;
        selectPoint(editor, '');
      }
      refreshEditorTimeline(editor.working, replacedLayer);
      refreshEditorAxisControls(null, editor.working);
      if (el.studioEditorSubtitle) {
        el.studioEditorSubtitle.textContent = `편집 반영 ${editor.undo.length}회`;
      }
      setEditorMessage('취소한 편집을 다시 반영했습니다'); renderEditor();
    };
    const acceptSavedEditorLayer = (editor, savedLayer, message, validation = null) => {
      const previousWorking = editor.working;
      const selectedPointId = editor.selectedPointId;
      const activeCurveId = editor.pointDraft?.curve_id;
      editor.original = clone(savedLayer);
      editor.working = clone(savedLayer);
      editor.preview = null;
      editor.previewValidation = null;
      if (validation) editor.validation = clone(validation);
      editor.operationReport = null;
      editor.previewOperation = '';
      editor.undo = [];
      editor.redo = [];
      editor.saveState = 'saved';
      editor.savedAt = new Date().toLocaleTimeString('ko-KR', {
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit',
      });
      editor.saveError = '';
      editor.saveFailureFingerprint = '';
      refreshEditorTimeline(editor.working, previousWorking);
      refreshEditorAxisControls(null, editor.working);
      if (activeCurveId && el.studioEditorOperation?.value === 'point_curve') {
        const savedCurve = editorPointCurves(editor.working).find(
          (curve) => curve.curve_id === activeCurveId,
        );
        if (savedCurve) loadPointDraft(savedCurve, selectedPointId);
        else {
          editor.pointDraft = null;
          selectPoint(editor, '');
        }
      }
      if (el.studioEditorSubtitle) {
        el.studioEditorSubtitle.textContent = '편집 반영 0회';
      }
      setEditorMessage(message);
      renderEditor();
    };
    const onEditorSave = async () => {
      const editor = state.editor;
      if (!editor) return;
      if (editor.preview) {
        setEditorMessage('결과 미리보기를 먼저 편집 반영한 뒤 저장하세요.', true);
        return;
      }
      if (pointDraftHasUnsavedChanges(editor)) {
        setEditorMessage(
          '포인트 변경을 먼저 결과 미리보기하고 편집 반영한 뒤 저장하세요.',
          true,
        );
        return;
      }
      if (motionStudioLayerDataEqual(editor.original, editor.working)) {
        setEditorMessage('저장할 변경사항이 없습니다.');
        renderEditor();
        return;
      }
      const confirmed = await requestMotionStudioEditorSave(el, {
        layerName: editor.working?.name || editor.original?.name || editor.layerId,
        editCount: editor.undo.length,
        pointCurvesChanged: JSON.stringify(editor.original?.point_curves || [])
          !== JSON.stringify(editor.working?.point_curves || []),
        warningCount: editor.validation?.range_warnings?.length || 0,
      });
      if (!confirmed || state.editor !== editor) return;
      editor.saveState = 'saving';
      editor.saveError = '';
      renderEditor();
      const result = await run(() => saveMotionStudioLayerData({
        layer_id: editor.layerId,
        original_revision: Number(editor.original.edit_revision || 0),
        layer: editor.working,
      }), {
        onError: (error) => {
          if (state.editor !== editor) return;
          editor.saveState = 'failed';
          editor.saveError = error.message || String(error);
          editor.saveFailureFingerprint = motionStudioEditorFailureFingerprint(editor);
          setEditorMessage(`저장 실패 · ${editor.saveError}`, true);
          renderEditor();
        },
      });
      if (result) {
        const savedLayer = state.project?.layers?.find(
          (layer) => layer.layer_id === editor.layerId,
        ) || editor.working;
        acceptSavedEditorLayer(
          editor,
          savedLayer,
          result.range_warnings?.length
            ? `저장 완료 · 축 설정 범위 초과 경고 ${result.range_warnings.length}건 · 계속 편집할 수 있습니다.`
            : '저장 완료 · 창을 닫지 않고 편집을 계속할 수 있습니다.',
          {
            ...(editor.validation || {}),
            range_warnings: result.range_warnings || [],
          },
        );
        return;
      }
      const currentLayer = state.project?.layers?.find(
        (layer) => layer.layer_id === editor.layerId,
      );
      const originalRevision = Number(editor.original.edit_revision || 0);
      const currentRevision = Number(currentLayer?.edit_revision || 0);
      if (
        currentLayer
        && currentRevision !== originalRevision
        && motionStudioLayerDataEqual(currentLayer, editor.working)
      ) {
        acceptSavedEditorLayer(
          editor,
          currentLayer,
          '같은 편집 결과가 이미 저장되어 현재 작업본을 동기화했습니다.',
        );
        return;
      }
      if (currentLayer && currentRevision !== originalRevision) {
        editor.saveState = 'failed';
        editor.saveError = '저장 중 원본 레이어가 변경되었습니다.';
        setEditorMessage(
          '저장 중 원본 레이어가 변경되었습니다. 현재 작업은 저장되지 않았습니다. '
          + '편집 창을 닫고 최신 레이어를 다시 열어 작업하세요.',
          true,
        );
        renderEditor();
        return;
      }
      if (editor.saveState !== 'failed') {
        editor.saveState = 'failed';
        editor.saveError = '레이어를 저장하지 못했습니다.';
        editor.saveFailureFingerprint = motionStudioEditorFailureFingerprint(editor);
        setEditorMessage('저장 실패 · 현재 작업본은 유지됩니다.', true);
        renderEditor();
      }
    };
    function editorModalOpen() {
      const modal = el.studioLayerEditorModal;
      return Boolean(state.editor) && Boolean(modal)
        && !modal.classList.contains('hidden');
    }

    function saveConfirmOpen() {
      const modal = el.studioEditorSaveConfirmModal;
      return Boolean(modal) && !modal.classList.contains('hidden');
    }

    function moveSelectedPoint(step) {
      const editor = state.editor;
      const points = editor?.pointDraft?.points;
      const next = motionStudioNeighbourPointId(
        points, editor?.selectedPointId, step,
      );
      if (!next || next === editor.selectedPointId) return false;
      selectPoint(editor, next);
      renderEditor();
      return true;
    }

    /** 버튼 설명에 키를 같이 적는다 · 키를 만들어도 모르면 없는 것과 같다 */
    function labelEditorShortcuts() {
      Object.entries(MOTION_STUDIO_SHORTCUT_LABELS).forEach(([name, keyText]) => {
        const button = el[name];
        if (!button || button.dataset.shortcutLabelled === '1') return;
        const existing = button.getAttribute('title') || button.textContent || '';
        button.setAttribute('title', `${existing.trim()} (${keyText})`.trim());
        button.dataset.shortcutLabelled = '1';
      });
    }

    function bindEditorShortcuts() {
      labelEditorShortcuts();
      document.addEventListener('keydown', (event) => {
        // 편집 동작만 받는다 · 모터가 도는 것과 저장은 마우스로만 · §6-118
        if (!motionStudioShortcutAllowed({
          editorOpen: editorModalOpen(),
          saveConfirmOpen: saveConfirmOpen(),
          typing: motionStudioTypingTarget(event.target),
        })) return;
        const action = motionStudioEditorShortcut(event);
        if (!action) return;
        if (action === 'previousPoint' || action === 'nextPoint') {
          if (moveSelectedPoint(action === 'nextPoint' ? 1 : -1)) {
            event.preventDefault();
          }
          return;
        }
        const button = el[MOTION_STUDIO_SHORTCUT_BUTTONS[action]];
        // 버튼이 막혀 있으면 키도 막힌다 · 조건은 한 곳에서만 정한다
        if (!button || button.disabled) return;
        event.preventDefault();
        button.click();
      });
    }

    (el.studioEditorOperationButtons || []).forEach((button) => {
      button.addEventListener('click', () => onEditorOperationButton(button));
    });
    el.studioEditorOperation?.addEventListener('change', onEditorOperationChange);
    el.studioEditorCreatePointsButton?.addEventListener('click', onEditorCreatePoints);
    el.studioEditorApplyButton?.addEventListener('click', () => applyEditorOperation());
    el.studioEditorUpdateButton?.addEventListener('click', updateEditorWorkingCopy);
    el.studioEditorCloseButton?.addEventListener('click', discardEditor);
    el.studioEditorUndoButton?.addEventListener('click', onEditorUndo);
    el.studioEditorRedoButton?.addEventListener('click', onEditorRedo);
    el.studioEditorSaveButton?.addEventListener('click', onEditorSave);
    bindEditorShortcuts();
    editorViewport.bind();
    const applyDraggedPoint = async () => {
      const editor = state.editor;
      if (!editor || editor.autoApplyingPointDrag) return;
      editor.autoApplyingPointDrag = true;
      renderEditorControls();
      try {
        await applyEditorOperation('point_curve');
        if (state.editor !== editor || !editor.preview) return;
        updateEditorWorkingCopy();
        setEditorMessage('포인트 이동 자동 반영 완료 · 결과가 맞으면 저장하세요.');
        renderEditor();
      } finally {
        if (state.editor === editor) {
          editor.autoApplyingPointDrag = false;
          renderEditorControls();
        }
      }
    };
    bindMotionStudioGraphEvents({
      state, el, cachedLayerTracks, editorSelectedMotionIds, selectedDraftPoint,
      clearEditorPointRange, selectPointCurveFromGraph, syncPointControls,
      editorGraphScheduler, editorViewport, editorPointCurves, pointCurveIsApplied,
      pointCurveCanBeCreated, setEditorMessage, discardEditorPreview,
      clearPendingPointCandidate, renderEditorControls, drawEditorGraph,
      loadPointDraft, renderEditor, applyDraggedPoint,
    });
  }

  return {
    bind,
    openLayerEditor,
    closeLayerEditor,
    renderEditor,
  };
}
