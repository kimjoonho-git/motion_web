import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

import {
  applyMotionStudioProjectPatch,
  motionStudioCanCreatePointCurve,
  motionStudioCanSwitchPointDraftCurve,
  motionStudioCanvasEventPoint,
  motionStudioEditorGraphClickAction,
  motionStudioEditorValidationProject,
  motionStudioMergePreviewProject,
  motionStudioSetLayerEnabled,
  motionStudioValueViewAfterRangeUnlock,
  motionStudioMotionTargetAtTime,
  motionStudioNearestMotionTarget,
  motionStudioPointCurveAtTime,
  motionStudioPointCurveIsApplied,
  motionStudioPointCurveOrder,
  motionStudioPointCurvePreview,
  motionStudioPointCurveViewEnd,
  motionStudioCopyPointRange,
  motionStudioDeletePointRange,
  motionStudioPointDragStarted,
  motionStudioPointHitTarget,
  motionStudioPointRangePoints,
  motionStudioPointRangeReady,
  motionStudioPointRangeTargetsMatch,
  motionStudioRuntimeStatusMessage,
  motionStudioShouldProtectPointAxisSelection,
  motionStudioSnapFrameTime,
} from '../static/js/motion_studio.js';
import {
  createMotionStudioEditorSession,
  motionStudioPointDraftHasUnsavedChanges,
  motionStudioSelectRangePoint,
} from '../static/js/motion_studio_editor_state.js';
import {
  motionStudioPanEditorGraph,
} from '../static/js/motion_studio_graph_interactions.js';
const motionStudioRuntimeSource = () => [
  'motion_studio.js',
  'motion_studio_editor_controller.js',
  'motion_studio_graph_interactions.js',
  'motion_studio_point_editor.js',
].map((name) => readFileSync(
  new URL(`../static/js/${name}`, import.meta.url),
  'utf8',
)).join('\n');

test('project patches preserve unchanged layer objects and apply order changes', () => {
  const unchanged = { layer_id: 'unchanged', frames: [{ time_sec: 0.02, values: {} }] };
  const replaced = { layer_id: 'replaced', frames: [] };
  const updated = { layer_id: 'replaced', name: 'updated', frames: [] };

  const project = applyMotionStudioProjectPatch(
    {
      project_id: 'project',
      name: 'before',
      layers: [unchanged, replaced, { layer_id: 'deleted', frames: [] }],
    },
    {
      metadata: { project_id: 'project', name: 'after' },
      layer_order: ['replaced', 'unchanged'],
      upsert_layers: [updated],
      delete_layer_ids: ['deleted'],
    },
  );

  assert.equal(project.name, 'after');
  assert.deepEqual(project.layers.map((layer) => layer.layer_id), [
    'replaced', 'unchanged',
  ]);
  assert.equal(project.layers[0], updated);
  assert.equal(project.layers[1], unchanged);
});

test('project patches do not carry layers across different projects', () => {
  const previousLayer = { layer_id: 'project-a-layer', frames: [] };
  const nextLayer = { layer_id: 'project-b-layer', frames: [] };
  const project = applyMotionStudioProjectPatch(
    { project_id: 'project-a', layers: [previousLayer] },
    {
      metadata: { project_id: 'project-b', name: 'second project' },
      layer_order: ['project-b-layer'],
      upsert_layers: [nextLayer],
    },
  );

  assert.equal(project.project_id, 'project-b');
  assert.deepEqual(project.layers, [nextLayer]);
  assert.equal(project.layers.includes(previousLayer), false);
});

test('playback selection updates one layer without replacing unchanged layers', () => {
  const first = { layer_id: 'first', enabled: true, frames: [] };
  const second = { layer_id: 'second', enabled: true, frames: [] };
  const project = { project_id: 'project-a', layers: [first, second] };

  const updated = motionStudioSetLayerEnabled(project, 'second', false);

  assert.notEqual(updated, project);
  assert.equal(updated.layers[0], first);
  assert.notEqual(updated.layers[1], second);
  assert.equal(updated.layers[1].enabled, false);
  assert.equal(motionStudioSetLayerEnabled(updated, 'missing', true), updated);
});

test('editor validation payload keeps only affected axes across other layers', () => {
  const edited = {
    layer_id: 'edited',
    frames: [{ time_sec: 0.02, values: { '1-1': 1 } }],
  };
  const other = {
    layer_id: 'other',
    frames: [
      { time_sec: 0.02, values: { '1-1': 2, '9-9': 9 } },
      { time_sec: 0.04, values: { '9-9': 10 } },
    ],
  };
  const project = { project_id: 'project', layers: [edited, other] };

  const validation = motionStudioEditorValidationProject(project, edited);

  assert.deepEqual(validation.layers[0].frames, []);
  assert.deepEqual(validation.layers[1].frames, [
    { time_sec: 0.02, values: { '1-1': 2 } },
  ]);
  assert.deepEqual(
    motionStudioMergePreviewProject(project, ['other']).layers,
    [other],
  );
});

test('canvas pointer coordinates follow the internal graph size on scaled displays', () => {
  assert.deepEqual(
    motionStudioCanvasEventPoint(
      { left: 20, top: 10, width: 480, height: 240 },
      260,
      130,
      960,
      480,
    ),
    { x: 480, y: 240 },
  );
  assert.deepEqual(
    motionStudioCanvasEventPoint(
      { left: 20, top: 10, width: 0, height: 0 },
      120,
      60,
      960,
      480,
    ),
    { x: 100, y: 50 },
  );
});

test('runtime status feedback reports asynchronous failure and active completion', () => {
  assert.deepEqual(
    motionStudioRuntimeStatusMessage(
      { state: 'initializing', message: '초기 위치 이동 중' },
      { state: 'error', message: '서보가 꺼져 있습니다' },
    ),
    { message: '서보가 꺼져 있습니다', error: true },
  );
  assert.deepEqual(
    motionStudioRuntimeStatusMessage(
      { state: 'playing', message: '재생 중' },
      { state: 'idle', message: '재생 완료' },
    ),
    { message: '재생 완료', error: false },
  );
  assert.equal(
    motionStudioRuntimeStatusMessage(
      { state: 'idle', message: '대기' },
      { state: 'idle', message: '대기' },
    ),
    null,
  );
});

test('a newly added flat axis can start a point curve without converting motion', () => {
  const flatLayer = {
    frames: [
      { time_sec: 0.00, values: { '3-1': 5 } },
      { time_sec: 0.02, values: { '3-1': 5 } },
    ],
    point_curves: [],
  };

  assert.equal(motionStudioCanCreatePointCurve(flatLayer, '3-1'), true);
  assert.equal(motionStudioCanCreatePointCurve({
    ...flatLayer,
    frames: [
      { time_sec: 0.00, values: { '3-1': 5 } },
      { time_sec: 0.02, values: { '3-1': 6 } },
    ],
  }, '3-1'), false);
  assert.equal(motionStudioCanCreatePointCurve({
    ...flatLayer,
    point_curves: [{ curve_id: 'curve-a', motion_id: '3-1', points: [] }],
  }, '3-1'), false);
});

test('point curve editability follows the applied working layer, not the saved baseline', () => {
  const savedLayer = { point_curves: [] };
  const workingLayer = {
    point_curves: [{ curve_id: 'curve-copied', motion_id: '2-1', points: [] }],
  };

  assert.equal(motionStudioPointCurveIsApplied(savedLayer, 'curve-copied'), false);
  assert.equal(motionStudioPointCurveIsApplied(workingLayer, 'curve-copied'), true);
});

test('point edit ranges require two points from the same point curve', () => {
  assert.equal(
    motionStudioPointRangeTargetsMatch('1-1', '1-1', 'curve-a', 'curve-a'),
    true,
  );
  assert.equal(
    motionStudioPointRangeTargetsMatch('1-1', '1-1', 'curve-a', 'curve-b'),
    false,
  );
  assert.equal(
    motionStudioPointRangeTargetsMatch('1-1', '1-2', 'curve-a', 'curve-a'),
    false,
  );
  assert.equal(motionStudioPointRangeReady(1, 1, '1-1', 'curve-a'), false);
  assert.equal(motionStudioPointRangeReady(1, 1.02, '1-1', 'curve-a'), true);
  assert.equal(motionStudioPointRangeReady(1, 2, '1-1', ''), false);
  const curve = {
    curve_id: 'curve-a',
    motion_id: '1-1',
    points: [
      { point_id: 'a', time_sec: 1, value_deg: 10 },
      { point_id: 'b', time_sec: 1.02, value_deg: 11 },
    ],
  };
  assert.equal(motionStudioPointRangeReady(1, 1.02, '1-1', 'curve-a', curve), true);
  assert.equal(motionStudioPointRangeReady(1, 2, '1-1', 'curve-a', curve), false);
  assert.deepEqual(
    motionStudioPointRangePoints(curve, 1, 1.02, '1-1', 'curve-a')
      .map((point) => point.point_id),
    ['a', 'b'],
  );
});

test('point range copy preserves shape metadata and blocks time collisions', () => {
  const curve = {
    curve_id: 'curve-a',
    motion_id: '1-1',
    points: [
      {
        point_id: 'a',
        time_sec: 0,
        value_deg: 1,
        tangent_mode: 'smooth',
        out_handle: { dt_sec: 0.01, dv_deg: 0.5 },
      },
      {
        point_id: 'b',
        time_sec: 0.04,
        value_deg: 3,
        tangent_mode: 'broken',
        in_handle: { dt_sec: -0.01, dv_deg: -0.5 },
      },
      { point_id: 'c', time_sec: 0.10, value_deg: 2 },
    ],
  };
  const copied = motionStudioCopyPointRange(curve, 0, 0.04, 0.20);
  assert.equal(copied.ok, true);
  assert.deepEqual(copied.points.map((point) => point.time_sec), [0.2, 0.24]);
  assert.deepEqual(copied.points[0].out_handle, { dt_sec: 0.01, dv_deg: 0.5 });
  assert.equal(copied.points[1].tangent_mode, 'broken');
  assert.deepEqual(
    motionStudioCopyPointRange(curve, 0, 0.04, 0.10),
    { ok: false, reason: 'time_conflict' },
  );
});

test('point range deletion removes the inclusive range and keeps two curve points', () => {
  const curve = {
    curve_id: 'curve-a',
    motion_id: '1-1',
    points: [
      { point_id: 'a', time_sec: 0, value_deg: 1 },
      { point_id: 'b', time_sec: 0.02, value_deg: 2 },
      { point_id: 'c', time_sec: 0.04, value_deg: 3 },
      { point_id: 'd', time_sec: 0.06, value_deg: 4 },
    ],
  };
  const deleted = motionStudioDeletePointRange(curve, 0.02, 0.04);
  assert.equal(deleted.ok, true);
  assert.equal(deleted.deletedCount, 2);
  assert.deepEqual(deleted.points.map((point) => point.point_id), ['a', 'd']);
  assert.deepEqual(
    motionStudioDeletePointRange(curve, 0, 0.04),
    { ok: false, reason: 'minimum_points' },
  );
});

test('motion sample clicks resolve without a previous mousemove cursor state', () => {
  const tracks = new Map([
    ['1-1', [{ timeSec: 0.02, value: 1 }, { timeSec: 0.04, value: 2 }]],
    ['1-2', [{ timeSec: 0.02, value: 10 }]],
  ]);
  const metrics = {
    xFor: (timeSec) => timeSec * 1000,
    yFor: (value) => value * 10,
  };

  assert.deepEqual(
    motionStudioNearestMotionTarget(tracks, ['1-1'], metrics, 42, 21),
    { motionId: '1-1', timeSec: 0.04, value: 2 },
  );
  assert.equal(
    motionStudioNearestMotionTarget(tracks, ['1-1'], metrics, 200, 200),
    null,
  );
  assert.equal(
    motionStudioNearestMotionTarget(tracks, ['1-2'], metrics, 42, 21),
    null,
  );
});

test('graph cursor time and value resolve to the selected 20 ms motion sample', () => {
  assert.equal(motionStudioSnapFrameTime(6.341), 6.34);
  assert.equal(motionStudioSnapFrameTime(6.349), 6.34);
  assert.equal(motionStudioSnapFrameTime(6.351), 6.36);

  const tracks = new Map([
    ['1-1', [
      { timeSec: 6.32, value: 2 },
      { timeSec: 6.34, value: 4 },
    ]],
    ['1-2', [{ timeSec: 6.34, value: 10 }]],
  ]);
  assert.deepEqual(
    motionStudioMotionTargetAtTime(tracks, ['1-1', '1-2'], 6.34, 9),
    { motionId: '1-2', timeSec: 6.34, value: 10 },
  );
  assert.deepEqual(
    motionStudioMotionTargetAtTime(tracks, ['1-1'], 6.34, 9),
    { motionId: '1-1', timeSec: 6.34, value: 4 },
  );
  assert.equal(motionStudioMotionTargetAtTime(tracks, ['1-1'], 6.36, 4), null);
});

test('graph hover and point creation use the same 20 ms motion sample source', () => {
  const source = motionStudioRuntimeSource();
  const hoverFlow = source.match(
    /studioEditorGraph\?\.addEventListener\('mousemove'[\s\S]*?studioEditorGraph\?\.addEventListener\('mouseleave'/,
  )?.[0] || '';
  const addPointFlow = readFileSync(
    new URL('../static/js/motion_studio_graph_interactions.js', import.meta.url),
    'utf8',
  );
  const confirmPointFlow = source.match(
    /studioEditorPointAddButton\?\.addEventListener\('click'[\s\S]*?studioEditorPointDeleteButton/,
  )?.[0] || '';

  assert.match(hoverFlow, /motionStudioSnapFrameTime\(metrics\.timeFor\(x\)\)/);
  assert.match(hoverFlow, /motionStudioMotionTargetAtTime/);
  assert.match(hoverFlow, /value: nearest \? nearest\.value : rawValue/);
  assert.match(addPointFlow, /const timeSec = motionStudioSnapFrameTime/);
  assert.match(addPointFlow, /const graphSample = motionStudioMotionTargetAtTime/);
  assert.match(addPointFlow, /graphSample\?\.value \?\? metrics\.valueFor/);
  assert.match(addPointFlow, /editor\.pendingPointCandidate =/);
  assert.doesNotMatch(addPointFlow, /editor\.pointDraft\.points\.push/);
  assert.match(confirmPointFlow, /addMotionStudioDraftPoint\(editor, candidate/);
  assert.match(confirmPointFlow, /clearPendingPointCandidate\(editor\)/);
});

test('graph click intent keeps point creation separate from range selection', () => {
  const pointTarget = {
    curve: { curve_id: 'curve-active', motion_id: '1-2' },
    point: { point_id: 'point-a', time_sec: 3.0 },
  };
  const motionTarget = { motionId: '1-2', timeSec: 3.0, value: 4.0 };
  const activeRegion = { curve_id: 'curve-active', motion_id: '1-2' };
  const otherRegion = { curve_id: 'curve-other', motion_id: '1-2' };

  assert.equal(motionStudioEditorGraphClickAction({
    operation: 'point_curve',
    pointTarget,
  }), 'edit_point');
  assert.equal(motionStudioEditorGraphClickAction({
    operation: 'time_shift',
    pointTarget,
  }), 'edit_point');
  assert.equal(motionStudioEditorGraphClickAction({
    operation: 'point_curve',
    pointTarget,
    rangeSelection: true,
  }), 'select_point');
  assert.equal(motionStudioEditorGraphClickAction({
    operation: 'point_curve',
    motionTarget,
    pointRegion: activeRegion,
    activeCurveId: 'curve-active',
  }), 'add_point');
  assert.equal(motionStudioEditorGraphClickAction({
    operation: 'point_curve',
    motionTarget,
    pointRegion: otherRegion,
    activeCurveId: 'curve-active',
  }), 'select_curve');
  assert.equal(motionStudioEditorGraphClickAction({
    operation: 'point_curve',
    motionTarget,
    pointRegion: null,
    activeCurveId: 'curve-active',
  }), 'add_point');
  assert.equal(motionStudioEditorGraphClickAction({
    operation: 'point_curve',
    motionTarget: null,
    pointRegion: null,
    activeCurveId: 'curve-active',
  }), 'add_point');
  assert.equal(motionStudioEditorGraphClickAction({
    operation: 'time_shift',
    motionTarget,
    pointRegion: activeRegion,
  }), 'select_curve');
  assert.equal(motionStudioEditorGraphClickAction({
    operation: 'time_shift',
    motionTarget,
    pointRegion: null,
  }), 'select_motion');
});

test('point regions resolve by time for one selected axis without requiring a nearby sample', () => {
  const curves = [
    {
      curve_id: 'curve-a',
      motion_id: '1-1',
      points: [{ time_sec: 1.0 }, { time_sec: 2.0 }],
    },
    {
      curve_id: 'curve-b',
      motion_id: '1-2',
      points: [{ time_sec: 1.0 }, { time_sec: 2.0 }],
    },
  ];

  assert.equal(
    motionStudioPointCurveAtTime(curves, ['1-1'], 1.5)?.curve_id,
    'curve-a',
  );
  assert.equal(
    motionStudioPointCurveAtTime(curves, ['1-1', '1-2'], 1.5),
    null,
  );
  assert.equal(
    motionStudioPointCurveAtTime(
      curves,
      ['1-1', '1-2'],
      1.5,
      { motionId: '1-2' },
    )?.curve_id,
    'curve-b',
  );
  assert.equal(motionStudioPointCurveAtTime(curves, ['1-1'], 3.0), null);
});

test('unsaved point drafts block curve and axis switches without blocking same-curve edits', () => {
  assert.equal(
    motionStudioCanSwitchPointDraftCurve('curve-a', 'curve-a', true),
    true,
  );
  assert.equal(
    motionStudioCanSwitchPointDraftCurve('curve-a', 'curve-b', true),
    false,
  );
  assert.equal(
    motionStudioCanSwitchPointDraftCurve('curve-a', 'curve-b', false),
    true,
  );
  assert.equal(motionStudioCanSwitchPointDraftCurve('', 'curve-b', true), true);
  assert.equal(motionStudioShouldProtectPointAxisSelection(true, true, false), true);
  assert.equal(motionStudioShouldProtectPointAxisSelection(true, false, true), true);
  assert.equal(motionStudioShouldProtectPointAxisSelection(true, false, false), false);
  assert.equal(motionStudioShouldProtectPointAxisSelection(false, true, true), false);
});

test('each layer editor session starts without a general-motion range mode', () => {
  const source = motionStudioRuntimeSource();

  assert.match(
    source,
    /let preferredEditorEditOperation = 'time_scale'/,
  );
  assert.match(
    source,
    /function openLayerEditor\(layer\)[\s\S]*?const operation = preferredEditorEditOperation;/,
  );
  assert.match(
    source,
    /const graphAction = motionStudioEditorGraphClickAction\([\s\S]*?graphAction !== 'select_point'/,
  );
  assert.match(source, /function selectPointCurveFromGraph[\s\S]*?pointDraftHasUnsavedChanges/);
  assert.match(
    source,
    /function protectPointDraftAxisSelection[\s\S]*?selectOnlyEditorAxis\(editor\.pointDraft\.motion_id\)/,
  );
  assert.doesNotMatch(source, /function setMotionRangeMode/);
  assert.doesNotMatch(source, /cursorMatchesClick/);
});

test('primary edit workflow actions stay in the fixed top action area', () => {
  const html = readFileSync(
    new URL('../static/index.html', import.meta.url),
    'utf8',
  );
  const actionArea = html.match(
    /<div class="studio-editor-primary-actions"[\s\S]*?<\/div>\s*<div class="studio-editor-layout">/,
  )?.[0] || '';
  assert.match(actionArea, /id="studioEditorApplyButton"/);
  assert.match(actionArea, /id="studioEditorUpdateButton"/);
  assert.match(actionArea, /id="studioEditorSaveButton"/);
  assert.match(actionArea, /id="studioEditorUndoButton"/);
  assert.match(actionArea, /id="studioEditorRedoButton"/);
  assert.match(actionArea, />변경 미리보기</);
  assert.match(actionArea, />작업본 반영</);
  assert.match(actionArea, /id="studioEditorSaveButton"[^>]*>저장</);
  assert.match(actionArea, /id="studioEditorCloseButton"[^>]*>닫기</);
  for (const id of [
    'studioEditorApplyButton',
    'studioEditorUpdateButton',
    'studioEditorSaveButton',
    'studioEditorUndoButton',
    'studioEditorRedoButton',
  ]) {
    assert.equal((html.match(new RegExp(`id="${id}"`, 'g')) || []).length, 1);
  }
});

test('editor keeps the graph and compact range toolbar in one viewport layout', () => {
  const html = readFileSync(
    new URL('../static/index.html', import.meta.url),
    'utf8',
  );
  const styles = readFileSync(
    new URL('../static/styles.css', import.meta.url),
    'utf8',
  );
  assert.match(
    html,
    /studio-editor-layout[\s\S]*?studio-editor-sidebar[\s\S]*?studio-editor-main[\s\S]*?studioEditorGraph[\s\S]*?studio-editor-inspector/,
  );
  assert.match(html, /id="studioEditorSaveConfirmModal"/);
  assert.doesNotMatch(html, /id="studioEditorDangerZone"/);
  assert.match(html, /id="studioEditorTimeZoomInButton"/);
  assert.match(html, /id="studioEditorValueZoomInButton"/);
  assert.match(html, /id="studioEditorValueRangeLockButton"[^>]*>축 범위 고정</);
  assert.match(html, /id="studioEditorPointAddButton"[^>]*>포인트 추가</);
  assert.match(
    html,
    /class="studio-editor-feedback"[\s\S]*?id="studioEditorSelectedPointSummary"[\s\S]*?id="studioEditorSelectedPointStartTime"[\s\S]*?id="studioEditorSelectedPointStartValue"[\s\S]*?id="studioEditorSelectedPointEndTime"[\s\S]*?id="studioEditorSelectedPointEndValue"[\s\S]*?id="studioEditorCursorInfo"[\s\S]*?id="studioEditorMessage"/,
  );
  for (const operation of [
    'time_shift', 'time_scale', 'value_offset', 'value_scale', 'point_curve',
  ]) {
    assert.match(html, new RegExp(`data-studio-editor-operation="${operation}"`));
  }
  assert.equal((html.match(/id="studioEditorCloseButton"/g) || []).length, 1);
  assert.match(
    styles,
    /grid-template-columns:\s*230px minmax\(0,\s*1fr\)/,
  );
  assert.match(
    styles,
    /grid-template-rows:\s*auto minmax\(var\(--studio-editor-graph-min-height\), 1fr\) auto auto auto/,
  );
  assert.match(
    html,
    /studio-editor-inspector-content[\s\S]*?studio-editor-operation-panel[\s\S]*?id="studioEditorRangeActions"/,
  );
  assert.match(
    styles,
    /\.studio-editor-inspector-content \{[\s\S]*?grid-template-columns: minmax\(0, 1\.7fr\) minmax\(360px, 1fr\)/,
  );
  assert.match(
    styles,
    /\.studio-editor-operation-panel \{[\s\S]*?border: 1px solid var\(--line\)/,
  );
  assert.match(
    styles,
    /--studio-editor-graph-min-height:\s*240px/,
  );
  assert.match(
    styles,
    /\.studio-editor-toolbar \{[\s\S]*?justify-content: flex-end;/,
  );
  assert.match(
    styles,
    /\.studio-editor-canvas-wrap \{[\s\S]*?height: auto;/,
  );
  assert.match(
    styles,
    /\.studio-editor-main \{[\s\S]*?overflow: hidden;/,
  );
});

test('selected range start and end point values are rendered in the graph summary', () => {
  const source = motionStudioRuntimeSource();
  assert.match(
    source,
    /studioEditorSelectedPointSummary\?\.classList\.toggle\('hidden', !startPoint\)/,
  );
  assert.match(source, /studioEditorSelectedPointStartTime\.textContent/);
  assert.match(source, /studioEditorSelectedPointStartValue\.textContent/);
  assert.match(source, /studioEditorSelectedPointEndTime\.textContent/);
  assert.match(source, /studioEditorSelectedPointEndValue\.textContent/);
});

test('editor graph background pans both axes and axis selection resets a fixed value range', () => {
  const source = motionStudioRuntimeSource();
  const editor = {
    valueRangeLock: null,
    panningGraph: {
      startX: 40,
      startY: 30,
      startViewStart: 2,
      startViewEnd: 12,
      startMinValue: -10,
      startMaxValue: 30,
      timeSpan: 10,
      valueSpan: 40,
      moved: false,
    },
  };
  const panned = motionStudioPanEditorGraph(
    editor,
    { plotWidth: 200, plotHeight: 100 },
    60,
    50,
  );
  assert.deepEqual(panned, { viewStart: 1, viewEnd: 11 });
  assert.deepEqual(editor.valueView, { minValue: -2, maxValue: 38 });
  assert.match(source, /resetEditorValueView\(\{ unlock: true \}\)/);
  assert.match(source, /motionStudioMotionAxisRange\(activeMapping\(\)\?\.rows \|\| \[\]/);
});

test('unlocking an axis range preserves the visible fixed bounds', () => {
  assert.deepEqual(
    motionStudioValueViewAfterRangeUnlock({
      motionId: '1-1',
      minValue: -35,
      maxValue: 45,
    }),
    { minValue: -35, maxValue: 45 },
  );
  assert.equal(
    motionStudioValueViewAfterRangeUnlock({ minValue: 10, maxValue: 10 }),
    null,
  );

});

test('editor exposes whole-axis point creation without motion-section conversions', () => {
  const html = readFileSync(
    new URL('../static/index.html', import.meta.url),
    'utf8',
  );
  const source = motionStudioRuntimeSource();
  assert.match(
    html,
    /id="studioEditorCreatePointsButton"[^>]*>전체 포인트 생성</,
  );
  assert.match(
    html,
    /id="studioEditorApproximationOrder">[\s\S]*?<option value="1">[\s\S]*?<option value="3" selected>[\s\S]*?<option value="5">/,
  );
  assert.doesNotMatch(html, /studioEditorScopeControls/);
  assert.doesNotMatch(html, /studioEditorCurveDetachButton/);
  assert.doesNotMatch(html, /studioEditorCurveDeleteButton/);
  assert.match(source, /applyEditorOperation\('create_axis_point_curve'\)/);
  assert.doesNotMatch(source, /convert_motion_to_point_curve/);
  assert.doesNotMatch(source, /convert_point_curve_to_motion/);
  assert.match(source, /deleteMotionStudioDraftPoint\(editor, point\.point_id\)/);
  assert.doesNotMatch(source, /selection_kind:/);
  assert.doesNotMatch(source, /replace_overlapping_point_curves:/);
  assert.match(source, /approximation_interpolation_order: Number\(/);
  assert.match(source, /pointCurveIsApplied/);
  assert.match(source, /전체에 포인트를 생성하고 작업본에 반영한 뒤 편집/);
  assert.match(
    source,
    /const activeCurveId = appliedOperation === 'create_axis_point_curve'/,
  );
  assert.match(source, /const workingPointCurve = Boolean\(storedCurveForDraft\(editor\)\)/);
  assert.match(
    source,
    /studio-editor-conversion-controls select, \.studio-editor-conversion-controls input'[\s\S]*?control\.disabled = Boolean\(editor\?\.preview\) \|\| pointMode \|\| selectedAxisPointBacked/,
  );
});

test('applied point curves can be edited again before the layer is saved', () => {
  const source = motionStudioRuntimeSource();
  const appliedGuard = source.match(
    /function pointCurveIsApplied[\s\S]*?^\s*}/m,
  )?.[0] || '';
  assert.match(appliedGuard, /editor\?\.working/);
  assert.doesNotMatch(appliedGuard, /editor\?\.original/);
  assert.match(
    source,
    /Boolean\(editor\?\.preview\)[\s\S]*?\(!appliedPointCurve && !creatablePointCurve\)[\s\S]*?\(!pointMode && !pointRangeReady\)/,
  );
});

test('motion export identifies the playback-selected layer and ignores the blue detail row', () => {
  const html = readFileSync(
    new URL('../static/index.html', import.meta.url),
    'utf8',
  );
  const source = motionStudioRuntimeSource();
  assert.match(html, /id="studioExportTarget"/);
  assert.match(html, /재생 선택 체크 기준 · 연한 파란색 행과 무관/);
  assert.match(source, /motionStudioExportSelection\(state\.project\?\.layers\)/);
  assert.match(source, /선택 기준 · 재생 선택 체크/);
  assert.match(source, /연한 파란색 행 · 상세보기 대상이며 내보내기와 무관/);
});

test('range editing accepts two distinct times from different axes', () => {
  const editor = { rangeSelection: { phase: 'awaiting_start', start: null, end: null } };
  const target = (pointId, timeSec, curveId = 'curve-a', motionId = '1-1') => ({
    curve: { curve_id: curveId, motion_id: motionId },
    point: { point_id: pointId, time_sec: timeSec, value_deg: timeSec },
  });

  assert.equal(motionStudioSelectRangePoint(editor, target('p1', 1)).phase, 'awaiting_end');
  assert.equal(motionStudioSelectRangePoint(editor, target('p1', 1)).reason, 'same_point');
  assert.equal(
    motionStudioSelectRangePoint(editor, target('same-time', 1, 'curve-b', '2-1')).reason,
    'same_time',
  );
  assert.equal(editor.rangeSelection.phase, 'awaiting_end');
  assert.equal(
    motionStudioSelectRangePoint(editor, target('p2', 2, 'curve-b', '2-1')).phase,
    'complete',
  );
});

test('point range actions reset stale selection and use the point-curve apply path', () => {
  const html = readFileSync(
    new URL('../static/index.html', import.meta.url),
    'utf8',
  );
  const source = motionStudioRuntimeSource();
  const styles = readFileSync(
    new URL('../static/styles.css', import.meta.url),
    'utf8',
  );
  const addFlow = source.match(
    /studioEditorPointAddButton\?\.addEventListener\('click'[\s\S]*?studioEditorPointDeleteButton/,
  )?.[0] || '';
  const deleteFlow = source.match(
    /studioEditorPointDeleteButton\?\.addEventListener\('click'[\s\S]*?studioEditorRangeCopyButton/,
  )?.[0] || '';
  const rangeSelectControlFlow = source.match(
    /if \(el\.studioEditorRangeSelectButton\) \{[\s\S]*?classList\.toggle\('on', rangeSelecting\);/,
  )?.[0] || '';
  const rangeFlow = source;

  assert.match(html, /id="studioEditorRangeActions" class="studio-editor-range-actions"/);
  assert.match(
    html,
    /id="studioEditorRangeSelectButton"[^>]*aria-pressed="false">구간 선택</,
  );
  assert.doesNotMatch(html, /studio-editor-range-actions hidden/);
  assert.match(
    html,
    /id="studioEditorRangeActions"[\s\S]*?class="studio-editor-point-action-row"[\s\S]*?id="studioEditorPointAddButton"[\s\S]*?id="studioEditorPointDeleteButton"[\s\S]*?class="studio-editor-range-action-row"[\s\S]*?id="studioEditorRangeCopyButton"[\s\S]*?id="studioEditorRangeDeleteButton"[\s\S]*?<\/section>/,
  );
  assert.match(html, /id="studioEditorRangeStatus"[^>]*>선택된 축에서 시작·종료 포인트/);
  assert.match(html, /id="studioEditorRangeCopyTarget"[^>]*step="0\.02"[^>]*disabled/);
  assert.match(html, /id="studioEditorRangeCopyButton"[^>]*disabled>구간 복사</);
  assert.match(html, /id="studioEditorRangeDeleteButton"[^>]*disabled>구간 삭제</);
  assert.match(addFlow, /clearEditorPointRange\(editor\)/);
  assert.match(deleteFlow, /clearEditorPointRange\(editor\)/);
  assert.match(rangeFlow, /motionStudioCopyPointRange/);
  assert.match(rangeFlow, /motionStudioDeletePointRange/);
  assert.match(rangeFlow, /activatePointDraftMutation/);
  assert.match(source, /rangeSelection: rangeSelecting/);
  assert.match(source, /void applyDraggedPoint\(\)/);
  assert.match(
    rangeSelectControlFlow,
    /studioEditorRangeSelectButton\.disabled = Boolean\(editor\?\.preview\)/,
  );
  assert.doesNotMatch(
    rangeSelectControlFlow,
    /selectedIds\.length !== 1/,
  );
  assert.match(
    styles,
    /studio-editor-range-actions button\[aria-pressed="true"\]/,
  );
  assert.match(
    styles,
    /studio-editor-range-actions button \{[\s\S]*?white-space: nowrap/,
  );
  assert.match(source, /if \(editor\.suppressGraphClick && !rangeSelecting\)/);
  assert.match(source, /rangeSelecting \? 22 : 14/);
  assert.match(source, /구간을 선택하려면 포인트 곡선이 표시된 Motion ID/);
  assert.doesNotMatch(source, /studioEditorRangeActions\.classList\.toggle\('hidden'/);
  assert.match(
    source,
    /studioEditorRangeStatus\.textContent = rangeReady[\s\S]*?rangePointTargets\.length/,
  );
  assert.match(
    source,
    /studioEditorRangeCopyTarget\.disabled = !selectedRange \|\| Boolean\(editor\?\.preview\)/,
  );
});

test('temporary point editing restores the last selected range edit operation', () => {
  const source = motionStudioRuntimeSource();
  const openFlow = source.match(
    /function openLayerEditor[\s\S]*?function closeLayerEditor/,
  )?.[0] || '';
  const pointModeFlow = source.match(
    /function enterEditorPointMode[\s\S]*?function activatePointDraftMutation/,
  )?.[0] || '';
  const applyFlow = source.match(
    /function updateEditorWorkingCopy[\s\S]*?async function previewEditorAxisAddition/,
  )?.[0] || '';
  const curveSelectionFlow = source.match(
    /if \(graphAction === 'select_curve'\)[\s\S]*?discardEditorPreview/,
  )?.[0] || '';

  const editor = createMotionStudioEditorSession({
    layer: { layer_id: 'layer-a', frames: [], point_curves: [] },
    operation: 'time_scale',
    duration: 1,
    pointTimelineEnd: 2,
  });
  assert.equal(editor.preferredEditOperation, 'time_scale');
  assert.equal(editor.pointModeReturnOperation, '');
  assert.match(openFlow, /const operation = preferredEditorEditOperation/);
  assert.match(source, /preferredEditorEditOperation = operation/);
  assert.match(pointModeFlow, /editor\.pointModeReturnOperation = currentOperation/);
  assert.match(pointModeFlow, /function restoreEditorEditOperation/);
  assert.match(applyFlow, /editor\.previewOperation \|\| editor\.operationReport/);
  assert.match(applyFlow, /appliedOperation === 'point_curve'/);
  assert.match(applyFlow, /restoreEditorEditOperation\(editor\)/);
  assert.match(curveSelectionFlow, /activatePointMode/);
  assert.match(
    curveSelectionFlow,
    /pointRegion\.points\?\.\[0\]\?\.point_id \|\| '',\s+activatePointMode/,
  );
});

test('axis range violations remain visible warnings without blocking edit apply', () => {
  const source = motionStudioRuntimeSource();
  const graphSource = readFileSync(
    new URL('../static/js/motion_studio_graph.js', import.meta.url),
    'utf8',
  );
  assert.match(source, /previewValidation\.range_warnings/);
  assert.match(source, /축 설정 범위 초과 경고/);
  assert.match(source, /계속 진행 가능/);
  assert.match(
    graphSource,
    /motionStudioEditorIssueTimes\(displayedValidation, \[\.\.\.selected\]\)/,
  );
  assert.match(source, /motionStudioRangeWarningGroups/);
  assert.match(source, /그래프에서 숨김/);
});

test('layer axis deletion uses preview, apply, and save workflow', () => {
  const source = motionStudioRuntimeSource();
  const html = readFileSync(
    new URL('../static/index.html', import.meta.url),
    'utf8',
  );
  const deletionFlow = source.match(
    /async function previewEditorAxisDeletion\(\)[\s\S]*?async function applyEditorOperation/,
  )?.[0] || '';

  assert.match(html, /id="studioEditorDeleteAxisButton"[\s\S]*?>선택 축 삭제</);
  assert.match(deletionFlow, /operation: 'delete_axis'/);
  assert.match(deletionFlow, /confirmLabel: '삭제 미리보기'/);
  assert.match(deletionFlow, /editor\.preview = clone\(result\.layer\)/);
  assert.doesNotMatch(deletionFlow, /saveMotionStudioLayerData/);
});

test('saving keeps the layer editor open and refreshes its saved baseline', () => {
  const source = motionStudioRuntimeSource();
  const saveFlow = source.match(
    /const acceptSavedEditorLayer =[\s\S]*?editorViewport\.bind\(\)/,
  )?.[0] || '';
  assert.match(saveFlow, /editor\.original = clone\(savedLayer\)/);
  assert.match(saveFlow, /editor\.undo = \[\]/);
  assert.match(saveFlow, /editor\.redo = \[\]/);
  assert.match(saveFlow, /if \(validation\) editor\.validation = clone\(validation\)/);
  assert.match(saveFlow, /저장 완료 · 창을 닫지 않고 편집을 계속할 수 있습니다/);
  assert.doesNotMatch(saveFlow, /if \(result\) \{\s*closeLayerEditor\(\)/);
});

test('undo also cancels point drafts and previews before edit apply', () => {
  const source = motionStudioRuntimeSource();
  assert.match(source, /if \(editor\.preview\) \{[\s\S]*?discardEditorPreview/);
  assert.match(source, /if \(pointDraftHasUnsavedChanges\(editor\)\)/);
  assert.match(source, /편집 반영 전 포인트 변경을 취소했습니다/);
  assert.match(source, /hasTransientChange/);
});

test('linear point curves do not become dirty only from legacy tangent naming', () => {
  const stored = {
    curve_id: 'curve-a',
    motion_id: '1-1',
    interpolation_order: 1,
    points: [
      { point_id: 'p1', time_sec: 0, value_deg: 0, tangent_mode: 'linear' },
      { point_id: 'p2', time_sec: 1, value_deg: 1, tangent_mode: 'linear' },
    ],
  };
  const pointDraft = structuredClone(stored);
  pointDraft.points.forEach((point) => { point.tangent_mode = 'auto'; });

  assert.equal(motionStudioPointDraftHasUnsavedChanges({
    working: { point_curves: [stored] },
    pointDraft,
  }), false);
});

test('general-motion range controls are absent from the simplified editor', () => {
  const html = readFileSync(
    new URL('../static/index.html', import.meta.url),
    'utf8',
  );
  const sidebar = html.match(
    /<aside class="studio-editor-sidebar">[\s\S]*?<\/aside>/,
  )?.[0] || '';
  assert.doesNotMatch(sidebar, /studioEditorSelectWholeRangeButton/);
  assert.doesNotMatch(html, /studioEditorScopeControls/);
  assert.doesNotMatch(html, /studioEditorStart/);
  assert.doesNotMatch(html, /studioEditorEnd/);
  assert.doesNotMatch(html, /studioEditorSelectWholeRangeButton/);
});

test('point hit target is forgiving and a click does not become a drag', () => {
  const target = { x: 100, y: 50, point: { point_id: 'point_1' } };
  assert.equal(motionStudioPointHitTarget([target], 113, 50), target);
  assert.equal(motionStudioPointHitTarget([target], 115, 50), null);
  assert.equal(motionStudioPointHitTarget([target], 120, 50, 22), target);
  const nearer = { x: 112, y: 50, point: { point_id: 'point_2' } };
  assert.equal(motionStudioPointHitTarget([target, nearer], 111, 50, 22), nearer);
  assert.equal(
    motionStudioPointDragStarted({ startX: 100, startY: 50, moved: false }, 102, 51),
    false,
  );
  assert.equal(
    motionStudioPointDragStarted({ startX: 100, startY: 50, moved: false }, 104, 50),
    true,
  );
});

test('point-curve workspace is not limited by the existing axis duration', () => {
  assert.equal(motionStudioPointCurveViewEnd(0.02), 10);
  assert.equal(motionStudioPointCurveViewEnd(7.86, 20), 20);
  assert.equal(motionStudioPointCurveViewEnd(7.86, 20, 35), 35);
});

test('point draft renders a visible curve before server apply', () => {
  const points = [
    { point_id: 'start', time_sec: 0, value_deg: 0, tangent_mode: 'auto' },
    { point_id: 'end', time_sec: 5, value_deg: 10, tangent_mode: 'auto' },
  ];
  for (const order of [1, 3, 5]) {
    const preview = motionStudioPointCurvePreview(points, order);
    assert.equal(preview.length > 2, true);
    assert.deepEqual(preview[0], { timeSec: 0, value: 0 });
    assert.deepEqual(preview.at(-1), { timeSec: 5, value: 10 });
    assert.equal(Math.abs(preview[Math.floor(preview.length / 2)].value - 5) < 1e-9, true);
  }
});

test('point curve order uses one validated value for display, draft, and preview', () => {
  assert.equal(motionStudioPointCurveOrder('3', 1), 3);
  assert.equal(motionStudioPointCurveOrder(undefined, 5), 5);
  assert.equal(motionStudioPointCurveOrder(2, 1), 1);
  assert.equal(motionStudioPointCurveOrder(2, 2), 3);

  const source = motionStudioRuntimeSource();
  const loadDraft = source.match(
    /function loadPointDraft[\s\S]*?\n  }\n\n  function selectOnlyEditorAxis/,
  )?.[0] || '';
  const syncControls = source.match(
    /function syncPointControls[\s\S]*?\n  }\n\n  function editorSelectedMotionIds/,
  )?.[0] || '';
  assert.match(loadDraft, /studioEditorPointCurveOrder\.value = String\(editor\.pointCurveOrder\)/);
  assert.doesNotMatch(syncControls, /activeElement !== el\.studioEditorPointCurveOrder/);
});

test('adding a point keeps a cubic preview geometrically different from straight lines', () => {
  const points = [
    { point_id: 'start', time_sec: 0, value_deg: 0, tangent_mode: 'auto' },
    { point_id: 'added', time_sec: 1, value_deg: 10, tangent_mode: 'auto' },
    { point_id: 'end', time_sec: 2, value_deg: 0, tangent_mode: 'auto' },
  ];
  const straight = motionStudioPointCurvePreview(points, 1);
  const cubic = motionStudioPointCurvePreview(points, 3);
  const straightByTime = new Map(straight.map((point) => [point.timeSec.toFixed(6), point.value]));
  const maximumDifference = Math.max(...cubic.map((point) => (
    Math.abs(point.value - straightByTime.get(point.timeSec.toFixed(6)))
  )));

  assert.equal(cubic.length, straight.length);
  assert.equal(maximumDifference > 0.5, true);
  assert.deepEqual(cubic[0], { timeSec: 0, value: 0 });
  assert.deepEqual(cubic.at(-1), { timeSec: 2, value: 0 });
});
