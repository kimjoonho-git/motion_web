export function createMotionStudioEditorViewportController({
  el,
  getEditor,
  drawGraph,
  scheduleGraph,
  renderEditor,
  setMessage,
  resetValueView,
  selectedMotionAxisRange,
  selectedPointRange,
  editorDuration,
}) {
  function setView(start, end, scheduleDraw = false) {
    const editor = getEditor();
    if (!editor) return false;
    const span = Math.max(0.04, end - start);
    editor.viewStart = Math.max(0, start);
    editor.viewEnd = editor.viewStart + span;
    if (scheduleDraw) scheduleGraph();
    else drawGraph();
    return true;
  }

  function scaleValues(factor) {
    const editor = getEditor();
    if (!editor || editor.valueRangeLock) return false;
    const minValue = Number(editor.graphMetrics?.minValue);
    const maxValue = Number(editor.graphMetrics?.maxValue);
    const multiplier = Number(factor);
    if (
      !Number.isFinite(minValue)
      || !Number.isFinite(maxValue)
      || !Number.isFinite(multiplier)
      || multiplier <= 0
    ) return false;
    const center = (minValue + maxValue) / 2;
    const halfSpan = ((maxValue - minValue) / 2) * multiplier;
    if (!Number.isFinite(center) || !Number.isFinite(halfSpan) || halfSpan <= 0) return false;
    editor.valueScale = 1;
    editor.valueOffset = 0;
    editor.valueView = {
      minValue: center - halfSpan,
      maxValue: center + halfSpan,
    };
    return true;
  }

  function bind() {
    el.studioEditorTimeZoomInButton?.addEventListener('click', () => {
      const editor = getEditor(); if (!editor) return;
      const center = (editor.viewStart + editor.viewEnd) / 2;
      const span = (editor.viewEnd - editor.viewStart) * 0.6;
      setView(center - span / 2, center + span / 2);
    });
    el.studioEditorTimeZoomOutButton?.addEventListener('click', () => {
      const editor = getEditor(); if (!editor) return;
      const center = (editor.viewStart + editor.viewEnd) / 2;
      const span = (editor.viewEnd - editor.viewStart) * 1.7;
      setView(center - span / 2, center + span / 2);
    });
    el.studioEditorValueZoomInButton?.addEventListener('click', () => {
      if (scaleValues(0.6)) drawGraph();
    });
    el.studioEditorValueZoomOutButton?.addEventListener('click', () => {
      if (scaleValues(1.7)) drawGraph();
    });
    el.studioEditorValueRangeLockButton?.addEventListener('click', () => {
      const editor = getEditor();
      if (!editor) return;
      if (editor.valueRangeLock) {
        resetValueView({ unlock: true, preserveLockedRange: true });
        setMessage('세로축 고정 해제 · 그래프 배경을 상하로 이동할 수 있습니다.');
        renderEditor();
        return;
      }
      const range = selectedMotionAxisRange();
      if (!range) {
        setMessage('모션축 하나를 선택하고 모션 매핑의 축 범위를 확인하세요.', true);
        return;
      }
      resetValueView();
      editor.valueRangeLock = range;
      setMessage(`세로축 고정 · ${range.motionId} · ${range.minValue}° ~ ${range.maxValue}°`);
      renderEditor();
    });
    el.studioEditorFitAllButton?.addEventListener('click', () => {
      const editor = getEditor(); if (!editor) return;
      resetValueView();
      setView(0, Math.max(0.04, editorDuration(editor.working)));
    });
    el.studioEditorFitSelectionButton?.addEventListener('click', () => {
      const editor = getEditor();
      if (!selectedPointRange(editor)) {
        setMessage('먼저 같은 포인트 곡선의 서로 다른 포인트 두 개를 선택하세요.', true);
        return;
      }
      resetValueView();
      setView(
        Math.min(editor.selectionStartSec, editor.selectionEndSec),
        Math.max(editor.selectionStartSec, editor.selectionEndSec),
      );
    });
  }

  return { bind, scaleValues, setView };
}
