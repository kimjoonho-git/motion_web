import assert from 'node:assert/strict';
import test from 'node:test';

import {
  bindMotionStudioGraphEvents,
  motionStudioGraphPointInside,
  motionStudioMoveDraftPoint,
  motionStudioMoveTangentHandle,
  motionStudioPanEditorGraph,
} from '../static/js/motion_studio_graph_interactions.js';

class FakeEventTarget {
  constructor(rect = null) {
    this.listeners = new Map();
    this.rect = rect;
  }

  addEventListener(type, listener) {
    const listeners = this.listeners.get(type) || [];
    listeners.push(listener);
    this.listeners.set(type, listeners);
  }

  dispatch(type, event = {}) {
    for (const listener of this.listeners.get(type) || []) listener(event);
  }

  getBoundingClientRect() {
    return this.rect;
  }
}

const metrics = {
  padding: { left: 10, top: 20 },
  plotWidth: 100,
  plotHeight: 200,
  timeFor: (x) => x / 100,
  valueFor: (y) => 100 - y,
};

test('graph interaction boundary includes only the plot area', () => {
  assert.equal(motionStudioGraphPointInside(metrics, 10, 20), true);
  assert.equal(motionStudioGraphPointInside(metrics, 110, 220), true);
  assert.equal(motionStudioGraphPointInside(metrics, 9, 20), false);
});

test('point drag snaps to 20 ms and preserves an occupied point time', () => {
  const point = { point_id: 'moving', time_sec: 0, value_deg: 0 };
  const editor = {
    pointDraft: {
      points: [
        point,
        { point_id: 'occupied', time_sec: 0.06, value_deg: 3 },
      ],
    },
  };
  let result = motionStudioMoveDraftPoint(editor, point, 4.9, 90, metrics);
  assert.equal(point.time_sec, 0.04);
  assert.equal(point.value_deg, 10);
  assert.equal(result.collides, false);

  result = motionStudioMoveDraftPoint(editor, point, 6.1, 80, metrics);
  assert.equal(result.collides, true);
  assert.equal(point.time_sec, 0.04);
});

test('graph pan and tangent movement update only editor view state', () => {
  const editor = {
    valueRangeLock: null,
    panningGraph: {
      startX: 10,
      startY: 20,
      startViewStart: 1,
      startViewEnd: 3,
      startMinValue: -10,
      startMaxValue: 10,
      timeSpan: 2,
      valueSpan: 20,
      moved: false,
    },
  };
  const nextView = motionStudioPanEditorGraph(editor, metrics, 20, 40);
  assert.deepEqual(nextView, { viewStart: 0.8, viewEnd: 2.8 });
  assert.deepEqual(editor.valueView, { minValue: -8, maxValue: 12 });

  const point = { time_sec: 0.5, value_deg: 10, tangent_mode: 'auto' };
  motionStudioMoveTangentHandle(point, 'out', 60, 80, metrics);
  assert.equal(point.tangent_mode, 'smooth');
  assert.equal(point.out_handle.dt_sec > 0, true);
  assert.equal(point.in_handle.dt_sec < 0, true);
});

test('range selection has one click path and keeps one canonical selection state', () => {
  const curve = {
    curve_id: 'curve_1',
    motion_id: '1-1',
    points: [
      { point_id: 'start', time_sec: 18.16, value_deg: -1.624 },
      { point_id: 'end', time_sec: 31.82, value_deg: -0.954 },
    ],
  };
  const graph = new FakeEventTarget({ left: 0, top: 0, width: 400, height: 300 });
  const fakeWindow = new FakeEventTarget();
  const previousWindow = globalThis.window;
  globalThis.window = fakeWindow;
  const editor = {
    working: { point_curves: [curve] },
    preview: null,
    graphMetrics: {
      width: 400,
      height: 300,
      padding: { left: 10, top: 10 },
      plotWidth: 380,
      plotHeight: 280,
      timeFor: (x) => x / 10,
      valueFor: (y) => y,
      xFor: (timeSec) => timeSec * 10,
      yFor: (value) => value,
    },
    pointHitTargets: [
      { x: 181.6, y: 50, curve, point: curve.points[0] },
      { x: 318.2, y: 50, curve, point: curve.points[1] },
    ],
    handleHitTargets: [],
    rangeSelection: { phase: 'awaiting_start', start: null, end: null },
    suppressGraphClick: true,
    pointDraft: null,
  };
  try {
    const context = {
      state: { editor },
      el: {
        studioEditorGraph: graph,
        studioEditorOperation: { value: 'time_shift' },
        studioEditorRangeCopyTarget: { value: '' },
      },
      cachedLayerTracks: () => new Map(),
      editorSelectedMotionIds: () => ['1-1'],
      selectedDraftPoint: () => null,
      clearEditorPointRange: () => {},
      selectPointCurveFromGraph: (targetCurve, pointId) => {
        editor.pointDraft = structuredClone(targetCurve);
        editor.selectedPointId = pointId;
        return true;
      },
      syncPointControls: () => {},
      editorGraphScheduler: { schedule: () => {} },
      editorViewport: { setView: () => {} },
      editorPointCurves: () => [curve],
      pointCurveIsApplied: () => true,
      pointCurveCanBeCreated: () => false,
      setEditorMessage: () => {},
      discardEditorPreview: () => {},
      clearPendingPointCandidate: () => {},
      renderEditorControls: () => {},
      drawEditorGraph: () => {},
      loadPointDraft: (targetCurve, pointId) => {
        editor.pointDraft = structuredClone(targetCurve);
        editor.selectedPointId = pointId;
      },
      renderEditor: () => {},
      applyDraggedPoint: async () => {},
    };
    bindMotionStudioGraphEvents(context);
    bindMotionStudioGraphEvents(context);
    graph.dispatch('mousedown', {
      button: 0,
      clientX: 181.6,
      clientY: 50,
      preventDefault: () => {},
    });
    assert.equal(editor.rangeSelection.phase, 'awaiting_start');
    graph.dispatch('click', { clientX: 181.6, clientY: 50, timeStamp: 1000 });
    assert.deepEqual(editor.rangeSelection, {
      phase: 'awaiting_end',
      start: {
        pointId: 'start', motionId: '1-1', curveId: 'curve_1', timeSec: 18.16,
      },
      end: null,
    });
    assert.equal(editor.suppressGraphClick, false);

    graph.dispatch('mousedown', {
      button: 0,
      clientX: 318.2,
      clientY: 50,
      preventDefault: () => {},
    });
    graph.dispatch('click', { clientX: 318.2, clientY: 50, timeStamp: 1200 });
    assert.deepEqual(editor.rangeSelection, {
      phase: 'complete',
      start: {
        pointId: 'start', motionId: '1-1', curveId: 'curve_1', timeSec: 18.16,
      },
      end: {
        pointId: 'end', motionId: '1-1', curveId: 'curve_1', timeSec: 31.82,
      },
    });
    assert.equal(context.el.studioEditorRangeCopyTarget.value, '31.84');
  } finally {
    globalThis.window = previousWindow;
  }
});
