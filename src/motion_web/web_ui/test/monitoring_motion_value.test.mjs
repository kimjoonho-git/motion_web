import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

import { motionValueText, renderMonitoring } from '../static/js/monitoring.js';

const here = path.dirname(fileURLToPath(import.meta.url));
const source = fs.readFileSync(
  path.join(here, '../static/js/monitoring.js'),
  'utf8',
);

test('monitoring table contains the motion value column', () => {
  assert.match(source, /label: '모션값 \(deg\)'/);
  assert.match(source, /\['현재 모션값'/);
});

test('motion value formatter distinguishes received, unmapped, and missing states', () => {
  assert.equal(motionValueText({
    motion_value_status: 'received',
    motion_value_deg: 2.125,
  }), '2.125');
  assert.equal(motionValueText({ motion_value_status: 'unmapped' }), '미설정');
  assert.equal(motionValueText({ motion_value_status: 'missing' }), '모션값 미수신');
});

test('monitoring summary renders runtime connection counts without an exception', () => {
  const summaryText = { textContent: '' };
  const el = new Proxy(
    { summaryText, rows: null },
    { get: (target, property) => target[property] ?? null },
  );

  renderMonitoring({
    monitoring_enabled: true,
    motors: [{ controller_index: 0, connection_state: 'online' }],
    connection_summary: {
      online: 1,
      offline: 0,
      bus_down: 0,
      stale: 0,
      initializing: 0,
      monitoring_off: 0,
      unknown: 0,
    },
    motor_type_counts: { 'AC Servo': 1 },
  }, {
    el,
    rawMode: false,
    activeMonitoringFilter: 'all',
    shouldShowMonitoringMotor: () => true,
    registryCount: 1,
    selectedMotionTestAxis: null,
    activeMonitoringDetailTab: 'basic',
  });

  assert.match(summaryText.textContent, /수신 중 1축/);
});
