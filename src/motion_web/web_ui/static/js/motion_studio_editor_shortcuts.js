/** 레이어 편집기 단축키 · §6-118
 *
 * 편집 동작만 키로 받는다 · **모터가 도는 것**(녹화·추가 녹화·레이어 재생·
 * 초기 위치 이동)과 **저장**은 키로 하지 않는다 · 키 한 번에 모터가 돌면
 * 안 되고, 저장은 사용자가 마우스로 확인하고 눌러야 한다.
 *
 * 판정을 순수 함수로 떼어 둔다 · 브라우저 없이도 시험할 수 있다.
 */

/** 글자를 치는 중인가 · 입력칸에서는 Del 이 글자 지우기여야 한다 */
export function motionStudioTypingTarget(target) {
  if (!target) return false;
  if (target.isContentEditable) return true;
  const tag = String(target.tagName || '').toLowerCase();
  return tag === 'input' || tag === 'select' || tag === 'textarea';
}

/** 이 키가 어떤 편집 동작인가 · 아니면 빈 문자열 */
export function motionStudioEditorShortcut(event) {
  if (!event || event.altKey || event.metaKey) return '';
  const key = String(event.key || '');
  if (event.ctrlKey) {
    if (key.toLowerCase() !== 'z') return '';
    return event.shiftKey ? 'redo' : 'undo';
  }
  if (event.shiftKey && key !== '+') return '';
  switch (key) {
    case 'Delete': return 'deletePoint';
    case 'Insert': return 'addPoint';
    case 'a': case 'A': return 'addPoint';
    case 'Enter': return 'preview';
    case '+': case '=': return 'zoomIn';
    case '-': case '_': return 'zoomOut';
    case 'f': case 'F': return 'fitAll';
    case 'Escape': return 'close';
    case 'ArrowLeft': return 'previousPoint';
    case 'ArrowRight': return 'nextPoint';
    default: return '';
  }
}

/** 동작 → 그 일을 하는 버튼 이름 · 포인트 이동은 버튼이 없다 */
export const MOTION_STUDIO_SHORTCUT_BUTTONS = {
  deletePoint: 'studioEditorPointDeleteButton',
  addPoint: 'studioEditorPointAddButton',
  undo: 'studioEditorUndoButton',
  redo: 'studioEditorRedoButton',
  preview: 'studioEditorApplyButton',
  zoomIn: 'studioEditorTimeZoomInButton',
  zoomOut: 'studioEditorTimeZoomOutButton',
  fitAll: 'studioEditorFitAllButton',
  close: 'studioEditorCloseButton',
};

/** 버튼에 붙일 키 표기 · 키를 만들어도 모르면 없는 것과 같다 */
export const MOTION_STUDIO_SHORTCUT_LABELS = {
  studioEditorPointDeleteButton: 'Del',
  studioEditorPointAddButton: 'Ins',
  studioEditorUndoButton: 'Ctrl+Z',
  studioEditorRedoButton: 'Ctrl+Shift+Z',
  studioEditorApplyButton: 'Enter',
  studioEditorTimeZoomInButton: '+',
  studioEditorTimeZoomOutButton: '-',
  studioEditorFitAllButton: 'F',
  studioEditorCloseButton: 'Esc',
};

/** 지금 이 키를 받아도 되는가 · 하나라도 어긋나면 받지 않는다 */
export function motionStudioShortcutAllowed({
  editorOpen, saveConfirmOpen, typing,
}) {
  return Boolean(editorOpen) && !saveConfirmOpen && !typing;
}

/** 고른 포인트의 앞/뒤 포인트 · 없으면 빈 문자열 */
export function motionStudioNeighbourPointId(points, selectedPointId, step) {
  const list = Array.isArray(points) ? points : [];
  if (!list.length) return '';
  const index = list.findIndex(
    (point) => String(point?.point_id || '') === String(selectedPointId || ''),
  );
  if (index < 0) return String(list[0]?.point_id || '');
  const next = index + (Number(step) || 0);
  if (next < 0 || next >= list.length) return '';
  return String(list[next]?.point_id || '');
}
