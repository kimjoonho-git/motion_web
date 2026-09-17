/** 레이어 편집기 · 지금 되는 동작을 그대로 붙잡아 둔다 · §6-126
 *
 * 정리하기 **전에** 거는 그물이다 · 새 기능을 확인하는 검사가 아니라,
 * **원래 되던 것이 계속 되는지**만 본다.
 *
 * 이번 주에 되던 것이 세 번 깨졌다 · 그래프가 통째로 안 그려졌고, 「포인트
 * 선택」 버튼이 먹지 않았고, 저장하면 선택 방식이 풀렸다 · 셋 다 새로 넣은
 * 것에는 검사가 있었지만 **원래 되던 것에는 없었다** · 그래서 내 화면에서는
 * 전부 초록이었고 깨진 것은 사용자 화면에서 드러났다.
 *
 * 여기 있는 검사가 깨지면 "고쳤다" 가 아니라 "뭔가 부쉈다" 는 뜻이다.
 */
import assert from 'node:assert/strict';
import test from 'node:test';

import {
  createMotionStudioEditorSession,
  motionStudioRangePicked,
  motionStudioRangeSelectionActive,
  motionStudioRangeSelectionBounds,
  motionStudioRangeSelectionChosen,
  motionStudioSelectRangePoint,
  motionStudioSelectedPointRange,
  motionStudioSelectedTimeRange,
  motionStudioSetSelectionMode,
  motionStudioPointDraftHasUnsavedChanges,
  motionStudioEditorLayerIsDirty,
} from '../static/js/motion_studio_editor_state.js';
import {
  motionStudioCopyPointRange,
  motionStudioDeletePointRange,
  motionStudioEditorGraphClickAction,
  motionStudioPointHitTarget,
} from '../static/js/motion_studio_point_model.js';
import {
  addMotionStudioDraftPoint,
  applyMotionStudioCopiedPointRange,
  deleteMotionStudioDraftPoint,
  updateMotionStudioDraftPoint,
} from '../static/js/motion_studio_point_editor.js';
import {
  synchronizeMotionStudioEditorTimeline,
} from '../static/js/motion_studio_editor_math.js';

// ---------------------------------------------------------------- 시험 자료

function curveOf(motionId, count = 8, step = 0.1) {
  return {
    curve_id: `curve-${motionId}`,
    motion_id: motionId,
    interpolation_order: 3,
    points: Array.from({ length: count }, (_unused, index) => ({
      point_id: `${motionId}-p${index}`,
      time_sec: Number((0.02 + index * step).toFixed(3)),
      value_deg: index * 3,
      tangent_mode: 'auto',
    })),
  };
}

function layerOf(motionIds = ['1-1', '1-2']) {
  const curves = motionIds.map((motionId) => curveOf(motionId));
  return {
    layer_id: 'L',
    name: '시험 레이어',
    point_curves: curves,
    frames: Array.from({ length: 40 }, (_unused, index) => ({
      time_sec: Number((0.02 + index * 0.02).toFixed(3)),
      values: Object.fromEntries(motionIds.map((motionId) => [motionId, index])),
    })),
  };
}

function sessionOf(motionIds = ['1-1', '1-2']) {
  const layer = layerOf(motionIds);
  return createMotionStudioEditorSession({
    layerId: 'L', layer, validation: { conflicts: [], playable: true },
  });
}

const targetOf = (curve, index) => ({ curve, point: curve.points[index] });

// ------------------------------------------------------- 편집기를 열었을 때

test('LOCK 편집기는 포인트 선택으로 시작한다', () => {
  const editor = sessionOf();
  assert.equal(motionStudioRangeSelectionChosen(editor), false);
  assert.equal(motionStudioRangePicked(editor), false);
  assert.equal(editor.preview, null);
  assert.equal(editor.undo.length, 0);
});

test('LOCK 포인트 선택에서 포인트를 누르면 그 포인트를 고친다', () => {
  const editor = sessionOf();
  const action = motionStudioEditorGraphClickAction({
    operation: 'time_scale',
    pointTarget: targetOf(editor.working.point_curves[0], 2),
    rangeSelection: motionStudioRangeSelectionChosen(editor),
  });
  assert.equal(action, 'edit_point');
});

// ------------------------------------------------------------- 구간 잡기

test('LOCK 구간 선택 · 시작 → 종료 → 완료', () => {
  const editor = sessionOf();
  motionStudioSetSelectionMode(editor, 'range');
  assert.equal(motionStudioRangeSelectionActive(editor), true);

  const curve = editor.working.point_curves[0];
  const first = motionStudioSelectRangePoint(editor, targetOf(curve, 1));
  assert.equal(first.ok, true);
  assert.equal(first.phase, 'awaiting_end');

  const second = motionStudioSelectRangePoint(editor, targetOf(curve, 4));
  assert.equal(second.ok, true);
  assert.equal(second.phase, 'complete');

  const bounds = motionStudioRangeSelectionBounds(editor);
  assert.ok(bounds.endSec > bounds.startSec);
  assert.equal(motionStudioRangePicked(editor), true);
  assert.equal(motionStudioRangeSelectionChosen(editor), true, '완료해도 구간 방식이다');
});

test('LOCK 구간은 축이 달라도 잡힌다 · 시간 범위로 쓴다', () => {
  const editor = sessionOf();
  motionStudioSetSelectionMode(editor, 'range');
  const [first, second] = editor.working.point_curves;
  motionStudioSelectRangePoint(editor, targetOf(first, 1));
  const done = motionStudioSelectRangePoint(editor, targetOf(second, 5));
  assert.equal(done.phase, 'complete');
  assert.ok(motionStudioSelectedTimeRange(editor), '시간 범위를 못 만든다');
  // 같은 곡선만 보는 판정은 축이 다르면 비어야 한다 · 구간 편집은 시간으로 쓴다
  assert.equal(motionStudioSelectedPointRange(editor), null);
});

test('LOCK 같은 포인트·같은 시간은 구간이 되지 않는다', () => {
  const editor = sessionOf();
  motionStudioSetSelectionMode(editor, 'range');
  const curve = editor.working.point_curves[0];
  motionStudioSelectRangePoint(editor, targetOf(curve, 2));
  assert.equal(
    motionStudioSelectRangePoint(editor, targetOf(curve, 2)).reason, 'same_point',
  );
});

test('LOCK 구간 선택 중 포인트 클릭은 구간을 잡는다', () => {
  const editor = sessionOf();
  motionStudioSetSelectionMode(editor, 'range');
  for (const operation of ['time_shift', 'value_scale', 'point_curve']) {
    assert.equal(motionStudioEditorGraphClickAction({
      operation,
      pointTarget: targetOf(editor.working.point_curves[0], 1),
      rangeSelection: motionStudioRangeSelectionChosen(editor),
    }), 'select_point', operation);
  }
});

// --------------------------------------------------------- 선택 방식 오가기

test('LOCK 두 방식은 언제든 서로 오간다', () => {
  const editor = sessionOf();
  for (const phase of ['awaiting_start', 'awaiting_end', 'complete']) {
    motionStudioSetSelectionMode(editor, 'range');
    editor.rangeSelection = { ...editor.rangeSelection, phase };
    motionStudioSetSelectionMode(editor, 'point');
    assert.equal(motionStudioRangeSelectionChosen(editor), false, `${phase} 에서 못 빠져나온다`);
    motionStudioSetSelectionMode(editor, 'range');
    assert.equal(motionStudioRangeSelectionChosen(editor), true);
  }
});

test('LOCK 레이어 길이가 바뀌어도 방식은 유지된다', () => {
  const editor = sessionOf();
  motionStudioSetSelectionMode(editor, 'range');
  synchronizeMotionStudioEditorTimeline(
    editor,
    { frames: [{ time_sec: 0.02 }, { time_sec: 9.0 }] },
    { frames: [{ time_sec: 0.02 }, { time_sec: 1.0 }] },
  );
  assert.equal(motionStudioRangeSelectionChosen(editor), true);
  assert.equal(motionStudioRangePicked(editor), false, '잡아 둔 구간은 풀려야 한다');
});

// ------------------------------------------------------------ 포인트 편집

test('LOCK 포인트 추가·이동·삭제', () => {
  const editor = { pointDraft: null, selectedPointId: '' };
  const curve = curveOf('1-1');
  editor.pointDraft = structuredClone(curve);

  const added = addMotionStudioDraftPoint(
    editor, { motionId: '1-1', timeSec: 0.051, valueDeg: 9 },
    { curveId: curve.curve_id, pointId: 'new-1', interpolationOrder: 3 },
  );
  assert.equal(added.ok, true);
  assert.equal(added.point.time_sec, 0.06, '20ms 격자에 붙어야 한다');

  const moved = updateMotionStudioDraftPoint(
    editor, editor.pointDraft.points[2], { timeSec: 0.151, valueDeg: 12 },
  );
  assert.equal(moved.ok, true, '빈 자리로는 옮겨진다');
  // 이미 포인트가 있는 20ms 칸으로는 못 옮긴다
  const blocked = updateMotionStudioDraftPoint(
    editor, editor.pointDraft.points[0],
    { timeSec: editor.pointDraft.points[1].time_sec, valueDeg: 1 },
  );
  assert.equal(blocked.ok, false);
  assert.equal(blocked.reason, 'time_conflict');

  const before = editor.pointDraft.points.length;
  assert.equal(deleteMotionStudioDraftPoint(editor, 'new-1').ok, true);
  assert.equal(editor.pointDraft.points.length, before - 1);
});

test('LOCK 포인트는 두 개 아래로 못 줄인다', () => {
  const editor = {
    pointDraft: { curve_id: 'c', motion_id: '1-1', points: [
      { point_id: 'a', time_sec: 0.02, value_deg: 0, tangent_mode: 'auto' },
      { point_id: 'b', time_sec: 0.04, value_deg: 1, tangent_mode: 'auto' },
    ] },
    selectedPointId: 'a',
  };
  assert.equal(deleteMotionStudioDraftPoint(editor, 'a').ok, false);
});

// ------------------------------------------------------------- 구간 복사

test('LOCK 구간 복사는 붙일 자리 포인트를 대체한다', () => {
  const curve = curveOf('1-1', 10);
  const result = motionStudioCopyPointRange(curve, 0.02, 0.22, 0.52);
  assert.equal(result.ok, true);
  assert.ok(result.replacedPointIds.length > 0, '대체 대상을 못 찾는다');

  const editor = {};
  let serial = 0;
  applyMotionStudioCopiedPointRange(editor, curve, result, () => `n${serial++}`);
  const times = editor.pointDraft.points.map((point) => point.time_sec);
  assert.deepEqual(times, [...times].sort((a, b) => a - b), '시간 순서가 깨졌다');
  assert.equal(new Set(times.map((t) => t.toFixed(3))).size, times.length, '같은 시간에 둘');
});

test('LOCK 구간 삭제는 포인트 두 개를 남긴다', () => {
  const curve = curveOf('1-1', 6);
  const result = motionStudioDeletePointRange(curve, 0.02, 0.52);
  assert.equal(result.ok, false, '전부 지우는 것은 막아야 한다');
});

// ----------------------------------------------------- 저장·되돌리기 상태

test('LOCK 작업본이 원본과 같으면 저장할 것이 없다', () => {
  // 같은지 보는 방법은 부르는 쪽이 준다 · 편집기는 그 답만 쓴다
  const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
  const editor = sessionOf();
  assert.equal(motionStudioEditorLayerIsDirty(editor, same), false);
  editor.working = structuredClone(editor.working);
  editor.working.point_curves[0].points[0].value_deg += 5;
  assert.equal(motionStudioEditorLayerIsDirty(editor, same), true);
});

test('LOCK 반영 전 포인트 변경은 따로 알아본다', () => {
  const editor = sessionOf();
  assert.equal(motionStudioPointDraftHasUnsavedChanges(editor), false);
  editor.pointDraft = structuredClone(editor.working.point_curves[0]);
  assert.equal(motionStudioPointDraftHasUnsavedChanges(editor), false);
  editor.pointDraft.points[1].value_deg += 7;
  assert.equal(motionStudioPointDraftHasUnsavedChanges(editor), true);
});

// ------------------------------------------------------------ 그래프 집기

test('LOCK 포인트 집기는 가까운 것을 고르고 멀면 안 고른다', () => {
  const targets = [
    { x: 10, y: 10, point: { point_id: 'a' } },
    { x: 60, y: 60, point: { point_id: 'b' } },
  ];
  assert.equal(motionStudioPointHitTarget(targets, 12, 12, 14).point.point_id, 'a');
  assert.equal(motionStudioPointHitTarget(targets, 300, 300, 14), null);
});

// ------------------------------------------------- 화면에 묶인 규칙 잠그기
//
// 아래는 브라우저 없이 돌릴 수 없는 자리다 · 대신 **그 규칙이 코드에 남아
// 있는지**를 본다 · 이번 주에 깨진 셋이 전부 여기 있었다.

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const read = (name) => readFileSync(
  fileURLToPath(new URL(`../static/js/${name}`, import.meta.url)), 'utf8',
);
const CONTROLLER = read('motion_studio_editor_controller.js');
const INTERACTIONS = read('motion_studio_graph_interactions.js');
const POINT_EDITOR = read('motion_studio_point_editor.js');
const GRAPH = read('motion_studio_graph.js');

test('LOCK 그래프 그리기는 끝까지 간다 · 눈금까지 그린다', () => {
  const draw = GRAPH.slice(GRAPH.indexOf('export function drawMotionStudioEditorGraph'));
  const colorsAt = draw.indexOf('const colors =');
  const limitsAt = draw.indexOf('drawMotionStudioAxisLimits(');
  const ticksAt = draw.indexOf('maxValue.toFixed(2)');
  assert.ok(colorsAt > 0 && limitsAt > 0 && ticksAt > 0);
  assert.ok(limitsAt > colorsAt, 'colors 보다 먼저 쓰면 그리기가 통째로 멈춘다');
});

test('LOCK 선택 방식 버튼 둘은 누르면 그 방식이 된다', () => {
  const pointButton = POINT_EDITOR.slice(
    POINT_EDITOR.indexOf("el.studioEditorPointSelectButton?.addEventListener"),
  ).slice(0, 900);
  const rangeButton = POINT_EDITOR.slice(
    POINT_EDITOR.indexOf("el.studioEditorRangeSelectButton?.addEventListener"),
  ).slice(0, 1200);
  assert.match(pointButton, /setSelectionMode\(editor, 'point'\)/);
  assert.match(rangeButton, /setSelectionMode\(editor, 'range'\)/);
  for (const block of [pointButton, rangeButton]) {
    assert.doesNotMatch(
      block, /motionStudioRangeSelectionActive\(editor\)/,
      '「고르는 중」으로 방식을 정하면 버튼이 안 먹는 상태가 생긴다',
    );
  }
});

test('LOCK 구간 선택 중에는 포인트 끌기가 시작되지 않는다', () => {
  const mousedown = INTERACTIONS.slice(
    INTERACTIONS.indexOf("addEventListener('mousedown'"),
  ).slice(0, 900);
  assert.match(mousedown, /motionStudioRangeSelectionChosen\(editor\)/);
});

test('LOCK 모터가 도는 동작과 저장은 단축키로 못 한다', () => {
  const shortcuts = read('motion_studio_editor_shortcuts.js');
  for (const forbidden of [
    'studioRecordButton', 'studioOverdubButton', 'studioPlayButton',
    'studioInitializeButton', 'studioStopButton', 'studioEditorSaveButton',
    'studioEditorUpdateButton',
  ]) {
    assert.doesNotMatch(shortcuts, new RegExp(forbidden), forbidden);
  }
});

test('LOCK 실행 취소는 고른 것부터 물린다', () => {
  const undoFlow = CONTROLLER.slice(CONTROLLER.indexOf('const onEditorUndo')).slice(0, 1600);
  const previewAt = undoFlow.indexOf('editor.preview');
  const pickAt = undoFlow.indexOf('motionStudioRangePicked(editor)');
  const historyAt = undoFlow.indexOf('editor.redo.push(');
  assert.ok(previewAt >= 0 && pickAt > previewAt && historyAt > pickAt, '순서가 뒤집혔다');
});

test('LOCK 구간 복사·삭제는 시간 범위로 켜진다', () => {
  assert.match(CONTROLLER, /const rangeUsable = Boolean\(selectedTimeRange\)/);
  assert.match(CONTROLLER, /studioEditorRangeCopyButton\.disabled = !rangeUsable/);
  assert.match(CONTROLLER, /studioEditorRangeDeleteButton\.disabled = !rangeUsable/);
});

test('LOCK 기능 탭을 바꿔도 보던 자리가 그대로다', () => {
  const start = CONTROLLER.indexOf('const onEditorOperationChange');
  const block = CONTROLLER.slice(
    start, CONTROLLER.indexOf('bindMotionStudioPointEditorEvents(', start),
  );
  assert.ok(block.length > 200);
  assert.doesNotMatch(block, /editor\.viewStart = 0;/);
});

// ------------------------------------------------- 고른 것은 주인이 하나 · §6-127
//
// 여섯 파일이 `rangeSelection` / `selectedPointId` / `selectionMode` 를 직접
// 썼다 · 한 곳의 뜻을 바꾸면 나머지가 옛 뜻으로 읽어, 이번 주 버그 네 건이
// 거기서 났다 · 이제 바꾸는 일은 `motion_studio_editor_selection.js` 만 한다.

test('LOCK 고른 것을 바꾸는 곳은 주인 모듈뿐이다', () => {
  const owned = /editor\.(rangeSelection|selectedPointId|selectionMode|pendingPointCandidate)\s*=/;
  for (const name of [
    'motion_studio_editor_controller.js',
    'motion_studio_graph_interactions.js',
    'motion_studio_point_editor.js',
    'motion_studio_editor_math.js',
    'motion_studio_graph.js',
  ]) {
    assert.doesNotMatch(read(name), owned, `${name} 이 고른 것을 직접 쓴다`);
  }
});

test('LOCK 주인 모듈은 방식과 고른 것을 따로 다룬다', () => {
  const owner = read('motion_studio_editor_selection.js');
  for (const fn of [
    'setSelectionMode', 'releaseRange', 'restartRange',
    'selectPoint', 'setPendingPoint', 'clearPendingPoint', 'clearPicks',
  ]) {
    assert.ok(owner.includes(`export function ${fn}(`), fn);
  }
  // 구간만 푸는 함수는 방식을 건드리면 안 된다
  const release = owner.slice(owner.indexOf('export function releaseRange')).slice(0, 300);
  // 읽는 것(`=== 'range'`)은 되고, 바꾸는 것(`= ...`)은 안 된다
  assert.doesNotMatch(release, /editor\.selectionMode = (?!=)/);
});
