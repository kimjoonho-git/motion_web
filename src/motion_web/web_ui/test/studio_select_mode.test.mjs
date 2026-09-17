import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

import { motionStudioEditorGraphClickAction } from '../static/js/motion_studio_point_model.js';

// 선택 방식은 처음에 고른다 · §6-121
//
// 전에는 아래 기능 탭(시간 배율·모션값 이동…)이 클릭의 뜻까지 정했다 ·
// 포인트 하나를 고치려면 반드시 「포인트 곡선」을 먼저 골라야 했고, 다른
// 기능에서 포인트를 누르면 뜻하지 않게 구간 선택이 시작됐다.

const OPERATIONS = [
  'time_shift', 'time_scale', 'value_offset', 'value_scale', 'point_curve', '',
];
const pointTarget = {
  curve: { curve_id: 'c1', motion_id: '1-1' },
  point: { point_id: 'p1', time_sec: 1.0 },
};

test('a point click picks that point whatever the operation is', () => {
  for (const operation of OPERATIONS) {
    assert.equal(
      motionStudioEditorGraphClickAction({ operation, pointTarget }),
      'edit_point',
      `${operation || '(없음)'} 에서 포인트를 못 고른다`,
    );
  }
});

test('with range selection on, the same click builds the range', () => {
  for (const operation of OPERATIONS) {
    assert.equal(
      motionStudioEditorGraphClickAction({
        operation, pointTarget, rangeSelection: true,
      }),
      'select_point',
      `${operation || '(없음)'} 에서 구간을 못 잡는다`,
    );
  }
});

// 기능을 바꿔도 보고 있던 자리는 그대로 · §6-121

const CONTROLLER = readFileSync(
  fileURLToPath(new URL('../static/js/motion_studio_editor_controller.js', import.meta.url)),
  'utf8',
);

test('switching the operation does not reset the graph view', () => {
  const start = CONTROLLER.indexOf('const onEditorOperationChange');
  assert.ok(start > 0, '기능 변경 처리부를 찾지 못했다');
  const block = CONTROLLER.slice(
    start,
    CONTROLLER.indexOf('bindMotionStudioPointEditorEvents(', start),
  );
  assert.ok(block.length > 200, '처리부를 제대로 잘라내지 못했다');
  assert.match(block, /editor\.operation = nextOperation;/, '엉뚱한 곳을 보고 있다');
  assert.doesNotMatch(block, /editor\.viewStart = 0;/, '보기 범위를 0으로 되돌린다');
  assert.doesNotMatch(block, /editor\.viewEnd = editor\.pointTimelineEnd;/);
});

// 선택 방식 버튼 두 개

const PANEL = readFileSync(
  fileURLToPath(new URL('../static/panels/09-panel-studio.html', import.meta.url)),
  'utf8',
);
const POINT_EDITOR = readFileSync(
  fileURLToPath(new URL('../static/js/motion_studio_point_editor.js', import.meta.url)),
  'utf8',
);

test('both selection modes are offered as buttons', () => {
  assert.match(PANEL, /id="studioEditorPointSelectButton"[^>]*>포인트 선택</);
  assert.match(PANEL, /id="studioEditorRangeSelectButton"[^>]*>구간 선택</);
});

test('choosing point selection turns range selection off', () => {
  const block = POINT_EDITOR.slice(
    POINT_EDITOR.indexOf("el.studioEditorPointSelectButton?.addEventListener"),
  ).slice(0, 700);
  assert.match(block, /setSelectionMode\(editor, 'point'\)/);
});

test('exactly one mode shows as pressed', () => {
  assert.match(
    CONTROLLER,
    /studioEditorPointSelectButton\.setAttribute\(\s*'aria-pressed', rangeChosen \? 'false' : 'true'/,
  );
  assert.match(
    CONTROLLER,
    /studioEditorRangeSelectButton\.setAttribute\(\s*'aria-pressed', rangeChosen \? 'true' : 'false'/,
  );
});

// 편집하고 반영해도 「구간 선택」에 그대로 서 있는다 · §6-123
//
// 구간을 다 잡으면 단계가 `complete` 가 된다 · 「고르는 중」이 아니라고 해서
// 포인트 선택으로 돌아간 것처럼 보이면, 이어서 구간 작업을 할 수 없다.

import {
  motionStudioRangeSelectionActive,
  motionStudioRangeSelectionChosen,
} from '../static/js/motion_studio_editor_state.js';
import {
  synchronizeMotionStudioEditorTimeline,
} from '../static/js/motion_studio_editor_math.js';

const withPhase = (phase) => ({ rangeSelection: { phase, start: null, end: null } });

test('a finished range still counts as range mode', () => {
  assert.equal(motionStudioRangeSelectionChosen(withPhase('complete')), true);
  assert.equal(motionStudioRangeSelectionActive(withPhase('complete')), false);
});

test('picking and finishing both count as range mode', () => {
  for (const phase of ['awaiting_start', 'awaiting_end', 'complete']) {
    assert.equal(motionStudioRangeSelectionChosen(withPhase(phase)), true, phase);
  }
  assert.equal(motionStudioRangeSelectionChosen(withPhase('inactive')), false);
  assert.equal(motionStudioRangeSelectionChosen(null), false);
});

test('a same-length layer refresh keeps the chosen range', () => {
  const layer = { frames: [{ time_sec: 0.02 }, { time_sec: 1.0 }] };
  const editor = {
    viewStart: 0.2, viewEnd: 0.8,
    rangeSelection: { phase: 'complete', start: { timeSec: 0.2 }, end: { timeSec: 0.6 } },
  };
  const changed = synchronizeMotionStudioEditorTimeline(editor, layer, layer);
  assert.equal(changed, false);
  assert.equal(editor.rangeSelection.phase, 'complete', '반영했더니 구간이 풀렸다');
  assert.equal(editor.viewStart, 0.2, '보던 자리가 바뀌었다');
});

test('a layer whose length changed does release the range', () => {
  const editor = {
    viewStart: 0.2, viewEnd: 0.8,
    rangeSelection: { phase: 'complete', start: { timeSec: 0.2 }, end: { timeSec: 0.6 } },
  };
  const changed = synchronizeMotionStudioEditorTimeline(
    editor,
    { frames: [{ time_sec: 0.02 }, { time_sec: 3.0 }] },
    { frames: [{ time_sec: 0.02 }, { time_sec: 1.0 }] },
  );
  assert.equal(changed, true);
  assert.equal(editor.rangeSelection.phase, 'inactive', '시간이 어긋나는데 구간이 남았다');
});

// 구간 선택 중에는 포인트 끌기로 새지 않는다 · §6-123
//
// 전에는 「고르는 중」일 때만 막았다 · 구간을 한 번 잡고 나면(`complete`)
// 가드가 풀려, 다음에 포인트를 누르는 순간 끌기가 시작되고 그 끌기가 포인트
// 곡선 모드로 갈아탔다 · 그 바람에 구간이 풀리고 축도 하나만 남았다.

const INTERACTIONS = readFileSync(
  fileURLToPath(new URL('../static/js/motion_studio_graph_interactions.js', import.meta.url)),
  'utf8',
);

test('pressing a point never starts a drag while in range mode', () => {
  const mousedown = INTERACTIONS.slice(
    INTERACTIONS.indexOf("addEventListener('mousedown'"),
  ).slice(0, 900);
  assert.match(
    mousedown,
    /if \(motionStudioRangeSelectionChosen\(editor\)\)/,
    '구간을 다 잡은 뒤에도 끌기가 시작된다',
  );
  assert.doesNotMatch(
    mousedown,
    /if \(motionStudioRangeSelectionActive\(editor\)\) \{\s*\n\s*event\.preventDefault/,
  );
});

test('every range judgement in the graph uses the same state', () => {
  // 한 곳만 옛 판정을 쓰면 그 자리에서 다시 샌다
  for (const pattern of [
    /suppressGraphClick && !rangeChosen/,
    /rangeChosen \? 22 : 14/,
    /rangeSelection: rangeChosen/,
  ]) {
    assert.match(INTERACTIONS, pattern, String(pattern));
  }
});

// 잘못 고른 것도 「실행 취소」로 물린다 · §6-124
//
// 전에는 결과 미리보기나 포인트 변경이 있을 때만 취소가 켜졌다 · 구간을
// 잘못 잡으면 물릴 방법이 없어, 그대로 두거나 편집기를 닫아야 했다.

test('undo is offered when only a pick is pending', () => {
  assert.match(CONTROLLER, /const hasPickToCancel = Boolean\(/);
  assert.match(
    CONTROLLER,
    /studioEditorUndoButton\.disabled = \(\s*\n\s*!hasTransientChange && !hasPickToCancel && !editor\?\.undo\.length/,
  );
});

test('undo cancels the pick before touching applied edits', () => {
  const undoFlow = CONTROLLER.slice(
    CONTROLLER.indexOf('const onEditorUndo'),
  ).slice(0, 1600);
  const pickAt = undoFlow.indexOf('motionStudioRangePicked(editor) || editor.selectedPointId');
  const historyAt = undoFlow.indexOf('editor.redo.push(');
  assert.ok(pickAt > 0, '고른 것을 물리는 자리가 없다');
  assert.ok(historyAt > pickAt, '반영한 편집을 먼저 되돌린다');
});

test('the undo button says what it will cancel', () => {
  assert.match(CONTROLLER, /고른 구간·포인트를 취소합니다/);
});

// 편집·저장해도 선택 방식은 그대로 · §6-125
//
// 고른 구간과 선택 방식이 한 값에 섞여 있었다 · 레이어 길이가 바뀌면 잡아 둔
// 시간이 안 맞아 구간을 풀어야 하는데, 그때 「구간 선택으로 일하는 중」이라는
// 사실까지 함께 풀려 저장할 때마다 포인트 선택으로 되돌아갔다.

import {
  motionStudioRangePicked,
  motionStudioSetSelectionMode,
} from '../static/js/motion_studio_editor_state.js';

test('the chosen mode survives a layer whose length changed', () => {
  const editor = { selectionMode: 'range', viewStart: 0, viewEnd: 1 };
  motionStudioSetSelectionMode(editor, 'range');
  synchronizeMotionStudioEditorTimeline(
    editor,
    { frames: [{ time_sec: 0.02 }, { time_sec: 5.0 }] },
    { frames: [{ time_sec: 0.02 }, { time_sec: 1.0 }] },
  );
  assert.equal(editor.selectionMode, 'range', '저장했더니 방식이 바뀌었다');
  assert.equal(
    motionStudioRangeSelectionChosen(editor), true,
    '구간 선택 쪽에 서 있어야 한다',
  );
  // 잡아 둔 구간은 시간이 안 맞으므로 다시 고르게 한다
  assert.equal(editor.rangeSelection.phase, 'awaiting_start');
  assert.equal(motionStudioRangePicked(editor), false);
});

test('point mode also survives', () => {
  const editor = { viewStart: 0, viewEnd: 1 };
  motionStudioSetSelectionMode(editor, 'point');
  synchronizeMotionStudioEditorTimeline(
    editor,
    { frames: [{ time_sec: 0.02 }, { time_sec: 5.0 }] },
    { frames: [{ time_sec: 0.02 }, { time_sec: 1.0 }] },
  );
  assert.equal(editor.selectionMode, 'point');
  assert.equal(motionStudioRangeSelectionChosen(editor), false);
  assert.equal(editor.rangeSelection.phase, 'inactive');
});

test('mode and pick are different questions', () => {
  const editor = {};
  motionStudioSetSelectionMode(editor, 'range');
  // 방식은 구간이지만 아직 아무것도 고르지 않았다
  assert.equal(motionStudioRangeSelectionChosen(editor), true);
  assert.equal(motionStudioRangePicked(editor), false);
});

// 두 버튼은 토글이 아니다 · 누르면 그 방식이 된다 · §6-125
//
// 전에는 「고르는 중인가」를 뒤집어 방식을 정했다 · 그래서
//   · 구간을 고르는 중에 「구간 선택」을 누르면 포인트 선택으로 넘어갔고
//   · 다 고른 뒤에는 「포인트 선택」이 아예 먹지 않았다
// 어느 쪽이든 사용자는 "버튼이 안 먹는다" 로 겪는다.

test('the point button switches back from any range phase', () => {
  const block = POINT_EDITOR.slice(
    POINT_EDITOR.indexOf("el.studioEditorPointSelectButton?.addEventListener"),
  ).slice(0, 900);
  // 방식으로 판정해야 한다 · 「고르는 중」으로 보면 완료 상태에서 막힌다
  assert.match(block, /motionStudioRangeSelectionChosen\(editor\)/);
  assert.doesNotMatch(block, /motionStudioRangeSelectionActive\(editor\)/);
  assert.match(block, /setSelectionMode\(editor, 'point'\)/);
});

test('the range button always chooses range mode', () => {
  const block = POINT_EDITOR.slice(
    POINT_EDITOR.indexOf("el.studioEditorRangeSelectButton?.addEventListener"),
  ).slice(0, 1200);
  assert.match(block, /setSelectionMode\(editor, 'range'\)/);
  assert.doesNotMatch(block, /selecting \? 'range' : 'point'/, '아직 토글이다');
  assert.doesNotMatch(block, /motionStudioRangeSelectionActive\(editor\)/);
});

test('each mode setting really lands', () => {
  const editor = {};
  motionStudioSetSelectionMode(editor, 'range');
  assert.equal(motionStudioRangeSelectionChosen(editor), true);
  motionStudioSetSelectionMode(editor, 'point');
  assert.equal(motionStudioRangeSelectionChosen(editor), false);
  // 구간을 다 잡은 뒤에도 포인트로 돌아갈 수 있어야 한다
  motionStudioSetSelectionMode(editor, 'range');
  editor.rangeSelection = { phase: 'complete', start: {}, end: {} };
  motionStudioSetSelectionMode(editor, 'point');
  assert.equal(motionStudioRangeSelectionChosen(editor), false, '완료 상태에서 못 돌아간다');
  assert.equal(editor.rangeSelection.phase, 'inactive');
});
