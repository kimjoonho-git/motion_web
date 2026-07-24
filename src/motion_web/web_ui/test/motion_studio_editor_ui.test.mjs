import assert from 'node:assert/strict';
import test from 'node:test';

import {
  motionStudioEditorAxisLabel,
  motionStudioEditorInspectorState,
  renderMotionStudioEditorPresentation,
} from '../static/js/motion_studio_editor_ui.js';

test('editor axis labels join project mapping and configured motor names', () => {
  const mappings = [
    { motion_id: '2-2', motor_ref: '', motor_axis: 0 },
    { motion_id: '3-1', motor_ref: 'dynamixel:id:7', motor_axis: 9 },
  ];
  const motors = [
    { name: '목 상하', axis: 0, motor_type: 'ac_servo' },
    {
      name: '입',
      motor_type: 'dynamixel',
      config: { controller_index: 12, bus_id: 7 },
    },
  ];

  assert.equal(
    motionStudioEditorAxisLabel('2-2', mappings, motors),
    '2-2  목 상하',
  );
  assert.equal(
    motionStudioEditorAxisLabel('3-1', mappings, motors),
    '3-1  입',
  );
  assert.equal(motionStudioEditorAxisLabel('9-9', mappings, motors), '9-9');
});

test('inspector state follows preview, point, range, and empty selections', () => {
  assert.equal(motionStudioEditorInspectorState({ preview: true }).key, 'preview');
  assert.equal(
    motionStudioEditorInspectorState({ pointDraftUnsaved: true }).key,
    'unsaved-point',
  );
  assert.equal(
    motionStudioEditorInspectorState({ savedPointCurve: true, pointSelected: true }).key,
    'point',
  );
  assert.equal(
    motionStudioEditorInspectorState({ savedPointCurve: true }).key,
    'saved-point',
  );
  assert.equal(
    motionStudioEditorInspectorState({ rangeSelected: true }).key,
    'motion-range',
  );
  assert.equal(motionStudioEditorInspectorState().key, 'none');
});

test('save and inspector presentation render explicit state without moving graph', () => {
  const toggles = [];
  const el = {
    studioEditorSaveStatus: { textContent: '', className: '' },
    studioEditorInspectorState: { textContent: '', dataset: {} },
    studioEditorSelectionGuide: { textContent: '' },
    studioEditorDangerZone: {
      classList: { toggle: (name, enabled) => toggles.push([name, enabled]) },
    },
  };

  renderMotionStudioEditorPresentation(el, {
    saveState: 'failed',
    saveError: '충돌',
    inspector: motionStudioEditorInspectorState({ pointSelected: true }),
    showDangerZone: true,
  });

  assert.equal(el.studioEditorSaveStatus.textContent, '저장 실패 · 충돌');
  assert.equal(el.studioEditorInspectorState.textContent, '포인트 선택');
  assert.deepEqual(toggles, [['hidden', false]]);
});
