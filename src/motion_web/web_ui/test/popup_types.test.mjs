import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const html = readFileSync(new URL('../static/index.html', import.meta.url), 'utf8');
const styles = readFileSync(new URL('../static/styles.css', import.meta.url), 'utf8');
const scripts = [
  'motor_config.js',
  'motion_data.js',
  'motion_studio.js',
  'project_explorer.js',
  'event_log.js',
  'midi_monitor.js',
  'motion_test.js',
].map((name) => readFileSync(new URL(`../static/js/${name}`, import.meta.url), 'utf8')).join('\n');

test('popup UI remains grouped into four management types', () => {
  assert.match(html, /id="operationProgressModal"/);
  assert.match(html, /id="appDialogModal"/);
  assert.match(html, /id="motorErrorPopup"/);
  assert.match(html, /id="studioLayerManagerModal"/);
  assert.match(html, /id="studioLayerEditorModal"/);
  assert.match(html, /id="studioEditorSaveConfirmModal"/);
});

test('feature modules do not call native confirm or prompt dialogs', () => {
  assert.doesNotMatch(scripts, /window\.(?:confirm|prompt)\s*\(/);
  assert.match(scripts, /showConfirm/);
  assert.match(scripts, /showPrompt/);
});

test('motor error popup remains visible above every modal type', () => {
  assert.match(
    styles,
    /\.motor-error-popup\s*\{[\s\S]*?z-index:\s*1500;/,
  );
});
