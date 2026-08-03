import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

import {
  createMotionStudioRequestFence,
} from '../static/js/motion_studio_controller_events.js';
import {
  createMotionStudioAxisEditorController,
  motionStudioSelectedAxisIds,
  motionStudioValidMotionId,
  setMotionStudioAxisSelection,
} from '../static/js/motion_studio_axis_editor.js';
import {
  motionStudioEditorHistoryEntry,
} from '../static/js/motion_studio_editor_controller.js';
import {
  createMotionStudioLayerController,
  selectMotionStudioLayer,
  updateMotionStudioMergeSelection,
} from '../static/js/motion_studio_layer_controller.js';

class Target extends EventTarget {
  constructor(value = '') {
    super();
    this.value = value;
    this.checked = false;
    this.dataset = {};
  }
}

function axisList(...values) {
  const inputs = values.map((value) => new Target(value));
  return {
    inputs,
    querySelectorAll(selector) {
      return selector === 'input:checked'
        ? inputs.filter((input) => input.checked)
        : inputs;
    },
  };
}

test('axis controller applies selection and binds preview actions once', () => {
  const list = axisList('1-1', '1-2');
  const el = {
    studioEditorAxisList: list,
    studioEditorSelectAllButton: new Target(),
    studioEditorSelectNoneButton: new Target(),
    studioEditorAddAxisButton: new Target(),
    studioEditorCopyAxisButton: new Target(),
    studioEditorDeleteAxisButton: new Target(),
  };
  const calls = [];
  const controller = createMotionStudioAxisEditorController({
    el,
    onSelectionChange: (ids) => calls.push(['selection', ids]),
    onAddAxis: () => calls.push(['add']),
    onCopyAxis: () => calls.push(['copy']),
    onDeleteAxis: () => calls.push(['delete']),
  });
  controller.bind();
  controller.bind();
  el.studioEditorSelectAllButton.dispatchEvent(new Event('click'));
  el.studioEditorAddAxisButton.dispatchEvent(new Event('click'));
  el.studioEditorCopyAxisButton.dispatchEvent(new Event('click'));
  el.studioEditorDeleteAxisButton.dispatchEvent(new Event('click'));

  assert.deepEqual(motionStudioSelectedAxisIds(list), ['1-1', '1-2']);
  assert.deepEqual(calls, [
    ['selection', ['1-1', '1-2']], ['add'], ['copy'], ['delete'],
  ]);
  assert.deepEqual(setMotionStudioAxisSelection(list, false), []);
  controller.destroy();
  el.studioEditorAddAxisButton.dispatchEvent(new Event('click'));
  assert.equal(calls.length, 4);
});

test('axis motion IDs accept only positive integer pairs', () => {
  assert.equal(motionStudioValidMotionId('1-2'), true);
  assert.equal(motionStudioValidMotionId('10-30'), true);
  assert.equal(motionStudioValidMotionId('0-2'), false);
  assert.equal(motionStudioValidMotionId('1-a'), false);
});

test('layer controller keeps selected and append-layer state consistent', () => {
  const state = {
    project: { layers: [{ layer_id: 'a' }, { layer_id: 'b' }] },
    selectedLayerId: 'a',
    mergeLayerIds: new Set(['a', 'b']),
    mergeAppendLayerId: 'b',
  };
  assert.equal(selectMotionStudioLayer(state, 'b'), true);
  assert.equal(state.selectedLayerId, 'b');
  assert.equal(selectMotionStudioLayer(state, 'missing'), false);
  assert.equal(state.selectedLayerId, 'b');
  assert.deepEqual(updateMotionStudioMergeSelection(state, 'b', false), ['a']);
  assert.equal(state.mergeAppendLayerId, '');

  const copyButton = new Target();
  let copies = 0;
  const controller = createMotionStudioLayerController({
    el: { studioSelectedLayerCopyButton: copyButton },
    handlers: { onCopy: () => { copies += 1; } },
  });
  controller.bind();
  controller.bind();
  copyButton.dispatchEvent(new Event('click'));
  assert.equal(copies, 1);
  controller.destroy();
  copyButton.dispatchEvent(new Event('click'));
  assert.equal(copies, 1);
});

test('editor history preserves the applied layer, curve, and selected point', () => {
  const editor = {
    working: { frames: [{ time_sec: 0 }] },
    validation: { playable: true },
    pointDraft: { curve_id: 'curve-a' },
    selectedPointId: 'p1',
  };
  const entry = motionStudioEditorHistoryEntry(editor);
  editor.working.frames[0].time_sec = 9;
  assert.equal(entry.layer.frames[0].time_sec, 0);
  assert.equal(entry.curveId, 'curve-a');
  assert.equal(entry.selectedPointId, 'p1');
});

test('project request fence rejects asynchronous results after project replacement', async () => {
  const fence = createMotionStudioRequestFence();
  const firstProjectRequest = fence.capture();
  let resolveRequest;
  const result = new Promise((resolve) => { resolveRequest = resolve; });

  fence.invalidate();
  const secondProjectRequest = fence.capture();
  resolveRequest({ project_id: 'first-project' });
  await result;

  assert.equal(fence.isCurrent(firstProjectRequest), false);
  assert.equal(fence.isCurrent(secondProjectRequest), true);
});

test('editor workflow stays in the editor controller and the workspace controller stays compact', () => {
  const workspaceSource = readFileSync(
    new URL('../static/js/motion_studio.js', import.meta.url), 'utf8',
  );
  const editorSource = readFileSync(
    new URL('../static/js/motion_studio_editor_controller.js', import.meta.url), 'utf8',
  );
  assert.ok(workspaceSource.split('\n').length - 1 <= 1500);
  for (const functionName of [
    'openLayerEditor',
    'closeLayerEditor',
    'renderEditor',
    'applyEditorOperation',
    'updateEditorWorkingCopy',
  ]) {
    assert.doesNotMatch(workspaceSource, new RegExp(`function ${functionName}\\(`));
    assert.match(editorSource, new RegExp(`function ${functionName}\\(`));
  }
  assert.match(editorSource, /const onEditorSave = async \(\) =>/);
  assert.match(editorSource, /const onEditorUndo = \(\) =>/);
  assert.match(editorSource, /const onEditorRedo = \(\) =>/);
});

test('stage 6 separates project, point, math, track, and canvas responsibilities', () => {
  const source = (name) => readFileSync(
    new URL(`../static/js/${name}`, import.meta.url), 'utf8',
  );
  const calculationsSource = source('motion_studio_calculations.js');
  const projectSource = source('motion_studio_project_model.js');
  const pointSource = source('motion_studio_point_model.js');
  const mathSource = source('motion_studio_editor_math.js');
  const tracksSource = source('motion_studio_tracks.js');
  const graphSource = source('motion_studio_graph.js');

  assert.equal(calculationsSource.trim().split('\n').length, 3);
  assert.doesNotMatch(calculationsSource, /function\s+/);
  assert.match(projectSource, /export function applyMotionStudioProjectPatch\(/);
  assert.match(pointSource, /export function motionStudioCopyPointRange\(/);
  assert.match(mathSource, /export function motionStudioSnapFrameTime\(/);
  for (const functionName of [
    'motionStudioLayerTracks',
    'motionStudioCompositionTracks',
    'motionStudioDisplaySegments',
    'motionStudioVisiblePoints',
    'motionStudioEditorIssueTimes',
  ]) {
    assert.match(tracksSource, new RegExp(`export function ${functionName}\\(`));
    assert.doesNotMatch(graphSource, new RegExp(`function ${functionName}\\(`));
  }
  assert.match(graphSource, /export function drawMotionStudioLayerGraph\(/);
  assert.match(graphSource, /export function drawMotionStudioEditorGraph\(/);
});
