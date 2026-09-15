import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import { indexHtml } from '../tools/index_html.mjs';

const html = indexHtml;
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
  assert.match(controller, /midiConnectionState\.textContent = surfaceOwned/);
  assert.match(controller, /midiInputState\.textContent = inputActive/);
  assert.match(controller, /status\?\.last_received_at/);
  assert.match(controller, /status\?\.device_last_power_reconnected_at/);
});

test('being handed over is not shown as a disconnected device', () => {
  // 장치가 빠진 것과 다른 PC 가 쓰는 것은 다른 일이다 · §6-94 · 둘 다
  // '연결 대기' 라고 하면 무엇을 고쳐야 할지 알 수 없다
  assert.match(controller, /const surfaceOwned = status\?\.surface_owned !== false/);
  assert.match(controller, /다른 PC 가 사용 중/);
  // 눌러 봐야 거절당하는 버튼은 아예 못 누르게 한다
  assert.match(
    controller,
    /connectMidiDeviceButton\.disabled = loading \|\| !surfaceOwned/,
  );
  assert.match(controller, /resetMidiRuntimeButton\.disabled = loading \|\| !surfaceOwned/);
});

test('a verified MIDI bank save reports the new mapping file revision', () => {
  assert.match(controller, /createMidiMonitorController\(\{ el, onMappingFileSaved \}\)/);
  assert.match(controller, /onMappingFileSaved\?\.\(payload\.file\)/);
});

test('MIDI motion angle uses the current displayed value instead of stale command cache', () => {
  assert.match(controller, /const displayedMotionDeg = live\?\.displayed_motion_value_deg/);
  assert.match(controller, /displayedMotionDeg === null \|\| displayedMotionDeg === undefined/);
});

test('SELECT OFF reports zero command without claiming physical arrival', () => {
  assert.match(controller, /commandMessage\.includes\('물리 도착 피드백 없음'\)/);
  assert.match(controller, /비활성 · 0 명령 전송/);
  assert.match(controller, /const zeroReturnFailed/);
  assert.match(controller, /0 복귀 실패/);
});
