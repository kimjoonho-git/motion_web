/** 긴급정지는 알리기만 한다 · 버튼을 끄지 않는다 · §6-189
 *
 * **막다른 길이 있었다.**
 *
 * 긴급정지가 걸리면 화면의 `<button>` 을 **전부** 껐다 · 197개 중 128개.
 *
 *     document.querySelectorAll('button').forEach((button) => {
 *       if (button === el.programRestartButton || ...) return;
 *       button.disabled = true;
 *     });
 *
 * 그 안에 **대화상자의 확인·취소**가 있었다.
 *
 *     알림이 뜬다            → 「확인」이 회색 → 닫을 수가 없다
 *     프로그램 재시작을 누른다 → 「재시작」도 「취소」도 회색 → 갇힌다
 *
 * 허락된 단 하나의 복구 동작이 그 동작의 **확인창 때문에** 막혔다 ·
 * `window.alert` 도 이 대화상자로 이어져 있어서 경고를 닫을 수조차 없었다 ·
 * 탭도 `<button>` 이라 함께 꺼져 상태를 보러 갈 수도, 기록을 읽을 수도 없었다 ·
 * 상태를 받을 때마다 다시 도니까 한 번 열린 버튼도 곧 다시 꺼졌다.
 *
 * **막는 일은 서버가 한다** · 슈퍼바이저가 여덟 자리에서 모터 명령을 거절한다
 * (`EMERGENCY_LATCHED_MESSAGE`) · 버튼마다의 판단도 따로 있다 ·
 * `studioMotorActionBlockReason()` 이 서버가 내려준 `motor_action_blocker` 를
 * 읽는다 · **같은 판단이 세 벌**이었고, 갈리는 날 사람이 갇혔다.
 */

import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
import test from 'node:test';

const JS_DIR = new URL('../static/js/', import.meta.url);
const PANELS = new URL('../static/panels/', import.meta.url);
const MAIN = readFileSync(new URL('main.js', JS_DIR), 'utf8');

function allPanels() {
  return readdirSync(PANELS)
    .filter((name) => name.endsWith('.html'))
    .map((name) => readFileSync(new URL(name, PANELS), 'utf8'))
    .join('\n');
}

/** 주석을 지운 코드 · 설명문에 적어둔 **옛 코드 예시**가 진짜로 보인다 */
function codeOnly(text) {
  return text
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .split('\n')
    .filter((line) => !line.trimStart().startsWith('//'))
    .join('\n');
}

test('화면 전체 버튼을 훑어 끄는 코드가 없다', () => {
  const offenders = [];
  for (const name of readdirSync(JS_DIR).filter((f) => f.endsWith('.js'))) {
    const text = codeOnly(readFileSync(new URL(name, JS_DIR), 'utf8'));
    for (const match of text.matchAll(/querySelectorAll\(\s*['"]button['"]\s*\)/g)) {
      offenders.push(`${name}:${text.slice(0, match.index).split('\n').length}`);
    }
  }

  assert.deepEqual(offenders, [], (
    '버튼을 통째로 훑습니다 · 대화상자와 탭까지 꺼져 사람이 갇힙니다:\n  '
    + offenders.join('\n  ')
  ));
});

test('긴급정지는 알리기만 한다', () => {
  const start = MAIN.indexOf('function showEmergencyLatched() {');
  assert.ok(start > 0, 'showEmergencyLatched() 가 없습니다');
  const body = MAIN.slice(start, MAIN.indexOf('\n}', start));

  // 띠를 띄우고 몸통에 표시를 남긴다 · 그 이상은 하지 않는다
  assert.match(body, /emergencyStopBanner/);
  assert.match(body, /classList\.toggle\('emergency-latched'/);
  assert.doesNotMatch(body, /disabled/);
});

test('끈 버튼을 되살리던 장치도 남지 않았다', () => {
  // 되살리기가 남아 있으면 「전에 꺼져 있었는지」를 기억하는 칸도 남는다
  for (const name of ['emergencyForcedDisabled', 'emergencyPreviousDisabled',
    'data-emergency-keep', 'staysUsableWhileLatched', 'enforceEmergencyUi']) {
    assert.ok(!MAIN.includes(name), `main.js 에 ${name} 이 남아 있습니다`);
  }
  assert.ok(!allPanels().includes('data-emergency-keep'));
});

test('모터 버튼은 서버가 내려준 답으로 끈다', () => {
  // 화면이 스스로 판단하면 서버와 갈린다 · 주인은 하나다
  const start = MAIN.indexOf('function studioMotorActionBlockReason() {');
  assert.ok(start > 0);
  const body = MAIN.slice(start, MAIN.indexOf('\n}', start));

  assert.match(body, /appState\.motorActionBlocker/);
  assert.match(body, /appState\.emergencyLatched/);
});

test('긴급정지를 알리는 띠가 화면에 있다', () => {
  // 버튼을 안 끄므로, 지금 어떤 상태인지는 이 띠가 유일하게 말해준다
  const html = allPanels();

  assert.match(html, /id="emergencyStopBanner"/);
  assert.match(html, /긴급정지 잠김/);
});

test('프로그램 재시작은 제 사정으로만 꺼진다', () => {
  // 서비스가 설치되지 않았을 때만 · 긴급정지와는 무관하다
  const start = MAIN.indexOf('const programRestartBlockedReason =');
  const body = MAIN.slice(start, start + 600);

  assert.match(body, /자동 실행 서비스가 설치되지 않았습니다/);
  assert.doesNotMatch(body, /emergencyLatched/);
});
