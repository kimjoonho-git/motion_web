import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

import {
  motionFileOriginalText,
  registeredMotionFileId,
} from '../static/js/motion_data.js';

const controller = readFileSync(
  new URL('../static/js/motion_data.js', import.meta.url),
  'utf8',
);
const html = readFileSync(new URL('../static/index.html', import.meta.url), 'utf8');
const dom = readFileSync(new URL('../static/js/dom.js', import.meta.url), 'utf8');

test('motion execution only uses the explicitly registered mapping file', () => {
  assert.equal(registeredMotionFileId({ motion_file_id: 'wave.json' }), 'wave.json');
  assert.equal(registeredMotionFileId({ motion_file_id: '' }), '');
  assert.equal(registeredMotionFileId({}), '');
});

test('motion file list does not arbitrarily select the first file', () => {
  const loadStart = controller.indexOf('async function loadFiles(');
  const loadEnd = controller.indexOf('async function selectFile(', loadStart);
  const loadBody = controller.slice(loadStart, loadEnd);
  const payloadStart = controller.indexOf('function motionRunPayload()');
  const payloadEnd = controller.indexOf('function motionRunInitialMoveTimeSec()', payloadStart);
  const payloadBody = controller.slice(payloadStart, payloadEnd);

  assert.doesNotMatch(loadBody, /selectFile\(files\[0\]\.id\)/);
  assert.doesNotMatch(payloadBody, /selectedFileId/);
  assert.doesNotMatch(payloadBody, /mappingDraft/);
  assert.match(payloadBody, /registeredMotionFileIdValue/);
});

test('late motion file list responses are discarded by request token', () => {
  const loadStart = controller.indexOf('async function loadFiles(');
  const loadEnd = controller.indexOf('async function selectFile(', loadStart);
  const loadBody = controller.slice(loadStart, loadEnd);
  const selectEnd = controller.indexOf('async function uploadSelectedFile()', loadEnd);
  const selectBody = controller.slice(loadEnd, selectEnd);

  assert.match(loadBody, /const loadToken = \+\+fileLoadToken/);
  assert.match(loadBody, /if \(loadToken !== fileLoadToken\) return/);
  assert.match(selectBody, /requestToken \?\? \+\+fileLoadToken/);
  assert.match(selectBody, /if \(loadToken !== fileLoadToken\) return/);
});

test('file list registration is explicit and persists through mapping save', () => {
  assert.match(html, /id="registerMotionFileButton"/);
  assert.match(dom, /registerMotionFileButton: document\.getElementById\('registerMotionFileButton'\)/);
  assert.match(controller, /registerMotionFileButton\?\.addEventListener\('click', registerSelectedMotionFile\)/);

  const registerStart = controller.indexOf('async function registerSelectedMotionFile()');
  const registerEnd = controller.indexOf('async function saveCurrentMapping()', registerStart);
  const registerBody = controller.slice(registerStart, registerEnd);
  assert.match(registerBody, /mappingDraft\.motion_file_id = selectedFile\.id/);
  assert.match(registerBody, /await saveCurrentMapping\(\)/);
});

test('registered motion file can be explicitly unregistered without deleting the file', () => {
  assert.match(html, /id="unregisterMotionFileButton"/);
  assert.match(dom, /unregisterMotionFileButton: document\.getElementById\('unregisterMotionFileButton'\)/);
  assert.match(controller, /unregisterMotionFileButton\?\.addEventListener\('click', unregisterSelectedMotionFile\)/);

  const unregisterStart = controller.indexOf('async function unregisterSelectedMotionFile()');
  const unregisterEnd = controller.indexOf('async function saveCurrentMapping()', unregisterStart);
  const unregisterBody = controller.slice(unregisterStart, unregisterEnd);
  assert.match(unregisterBody, /mappingDraft\.motion_file_id = ''/);
  assert.match(unregisterBody, /await saveCurrentMapping\(\)/);
  assert.doesNotMatch(unregisterBody, /deleteMotionFile/);
});

test('original motion file view prefers the complete file content without truncation', () => {
  const complete = `header\n${'x'.repeat(15000)}\nlast-frame`;
  assert.equal(
    motionFileOriginalText(
      { content: complete, content_preview: complete.slice(0, 12000) },
      {},
    ),
    complete,
  );
});
