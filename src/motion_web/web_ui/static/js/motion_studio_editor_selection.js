/** 편집기에서 **고른 것**을 책임지는 한 곳 · §6-127
 *
 * 「고른 것」은 네 가지다 · 선택 방식, 잡아 둔 구간, 고른 포인트, 찍어 둔
 * 후보 자리 · 전에는 여섯 파일이 이 값들을 직접 써서, 한 곳의 뜻을 바꾸면
 * 나머지가 옛 뜻으로 읽었다 · 이번 주 버그 네 건이 전부 거기서 났다.
 *
 * **바꾸는 일은 여기서만 한다** · 다른 파일은 여기 있는 함수를 부른다 ·
 * 그래야 뜻을 바꿀 때 고칠 자리가 한 곳이다.
 *
 * 읽기는 `motion_studio_editor_state.js` 가 맡는다 · 거기 있는 판정
 * (`...RangeSelectionChosen` / `...RangePicked`)과 짝이다.
 */
import {
  motionStudioRangeSelectionActive,
  motionStudioResetRangeSelection,
} from './motion_studio_editor_state.js';

/** 선택 방식을 정한다 · 잡아 둔 구간도 그 방식에 맞춰 놓는다 */
export function setSelectionMode(editor, mode) {
  if (!editor) return;
  editor.selectionMode = mode === 'range' ? 'range' : 'point';
  motionStudioResetRangeSelection(editor, editor.selectionMode === 'range');
}

/** 잡아 둔 구간만 푼다 · **선택 방식은 그대로** */
export function releaseRange(editor) {
  if (!editor) return;
  motionStudioResetRangeSelection(editor, editor.selectionMode === 'range');
}

/** 구간을 처음부터 다시 잡는다 · 구간 방식일 때만 뜻이 있다 */
export function restartRange(editor) {
  if (!editor) return;
  if (!motionStudioRangeSelectionActive(editor)) {
    motionStudioResetRangeSelection(editor, true);
  }
}

/** 고른 포인트를 정한다 · 빈 값이면 아무것도 안 고른 상태 */
export function selectPoint(editor, pointId = '') {
  if (!editor) return;
  editor.selectedPointId = String(pointId || '');
}

/** 찍어 둔 후보 자리를 기억한다 · 아직 포인트가 된 것은 아니다 */
export function setPendingPoint(editor, candidate) {
  if (!editor) return;
  editor.pendingPointCandidate = candidate || null;
}

/** 찍어 둔 후보 자리를 지운다 */
export function clearPendingPoint(editor) {
  if (!editor) return;
  editor.pendingPointCandidate = null;
}

/** 고른 것을 모두 비운다 · 방식은 건드리지 않는다 */
export function clearPicks(editor) {
  if (!editor) return;
  releaseRange(editor);
  selectPoint(editor, '');
  clearPendingPoint(editor);
}

/** 레이어 길이가 바뀌었다 · 잡아 둔 시간이 더는 맞지 않는다 */
export function releaseAfterTimelineChange(editor) {
  releaseRange(editor);
}
