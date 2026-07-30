import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const html = readFileSync(new URL('../static/index.html', import.meta.url), 'utf8');
const dom = readFileSync(new URL('../static/js/dom.js', import.meta.url), 'utf8');
const controller = readFileSync(new URL('../static/js/midi_monitor.js', import.meta.url), 'utf8');

test('MIDI device connection and recent input activity are displayed separately', () => {
  assert.match(html, /id="midiConnectionState"/);
  assert.match(html, /id="midiInputState"/);
  assert.match(html, /id="midiLastInputState"/);
  assert.match(html, /id="midiPowerReconnectState"/);
  assert.match(dom, /midiInputState: document\.getElementById\('midiInputState'\)/);
  assert.match(dom, /midiLastInputState: document\.getElementById\('midiLastInputState'\)/);
  assert.match(dom, /midiPowerReconnectState: document\.getElementById\('midiPowerReconnectState'\)/);
  assert.match(controller, /const deviceConnected = Boolean\(status\?\.device_connected\)/);
  assert.match(controller, /const inputActive = Boolean\(status\?\.connected\)/);
  assert.match(controller, /midiConnectionState\.textContent = deviceConnected/);
  assert.match(controller, /midiInputState\.textContent = inputActive/);
  assert.match(controller, /status\?\.last_received_at/);
  assert.match(controller, /status\?\.device_last_power_reconnected_at/);
});

test('a verified MIDI bank save reports the new mapping file revision', () => {
  assert.match(controller, /createMidiMonitorController\(\{ el, onMappingFileSaved \}\)/);
  assert.match(controller, /onMappingFileSaved\?\.\(payload\.file\)/);
});
