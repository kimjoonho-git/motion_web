import assert from 'node:assert/strict';
import test from 'node:test';

import {
  motionStudioEditorAxisLabel,
  motionStudioEditorInspectorState,
  motionStudioRangeWarningGroups,
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
      config: { controller_index: 12, bus_id: 7, serial_port: '/dev/ttyUSB0' },
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
    motionStudioEditorInspectorState({ appliedPointCurve: true, pointSelected: true }).key,
    'point',
  );
  assert.equal(
    motionStudioEditorInspectorState({
      appliedPointCurve: true,
      pointSelected: true,
      rangeSelected: true,
    }).key,
    'point-range',
  );
  assert.equal(
    motionStudioEditorInspectorState({ appliedPointCurve: true }).key,
    'applied-point',
  );
  assert.equal(
    motionStudioEditorInspectorState({ rangeSelected: true }).key,
    'point-range',
  );
  assert.equal(motionStudioEditorInspectorState().key, 'none');
});

test('range warnings are grouped by axis and adjacent 20ms periods', () => {
  const groups = motionStudioRangeWarningGroups([
    {
      motion_id: '1-3', time_sec: 4.56, value_deg: 10.1,
      lower_deg: -15, upper_deg: 10,
    },
    {
      motion_id: '1-3', time_sec: 4.58, value_deg: 10.6,
      lower_deg: -15, upper_deg: 10,
    },
    {
      motion_id: '1-3', time_sec: 6.12, value_deg: 10.3,
      lower_deg: -15, upper_deg: 10,
    },
    {
      motion_id: '1-2', time_sec: 3.1, value_deg: -10.2,
      lower_deg: -10, upper_deg: 15,
    },
  ]);

  assert.deepEqual(groups.map((group) => group.motionId), ['1-2', '1-3']);
  assert.equal(groups[0].belowLower, true);
  assert.equal(groups[0].minimumDeg, -10.2);
  assert.equal(groups[1].aboveUpper, true);
  assert.equal(groups[1].maximumDeg, 10.6);
  assert.deepEqual(groups[1].segments, [
    { startSec: 4.56, endSec: 4.58 },
    { startSec: 6.12, endSec: 6.12 },
  ]);
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
