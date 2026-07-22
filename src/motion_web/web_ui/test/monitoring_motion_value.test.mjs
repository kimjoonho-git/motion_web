import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

import { motionValueText } from '../static/js/monitoring.js';

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
