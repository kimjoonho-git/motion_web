import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import { indexHtml } from '../tools/index_html.mjs';
import {
  motionScheduleBadgeState,
  motionScheduleScopeNote,
} from '../static/js/schedule_scope.js';

/**
 * 스케줄은 한 벌이고, 지금 무엇을 움직이는지는 연동 상태가 정한다 · §6-133
 *
 * 전에는 `is_master` 하나만 봤다 · 그런데 **연동을 쓰지 않는 PC 도
 * `is_master: true`** 로 돌아온다 (`resolve_master_role` 이 연동 비활성을
 * "단독 동작으로 간주" 한다) · 그래서 연동을 켠 적 없는 사용자에게 "마스터"
 * 라고 떴다.
 */

const status = (overrides = {}) => ({
  status: 'ok', is_master: true, schedule_count: 2, ...overrides,
});

test('연동을 쓰지 않으면 마스터라는 말을 쓰지 않는다', () => {
  const state = motionScheduleBadgeState(status());
  assert.equal(state.scope, 'local');
  assert.equal(state.text, '스케줄러: 동작 중 (2개 등록)');
  assert.equal(state.canEdit, true);
  assert.equal(state.warning, '');
  assert.match(motionScheduleScopeNote(status()), /이 PC 의 등록된 모션/);
});

test('연동 마스터로 참가 중이면 그룹 전체가 움직인다고 말한다', () => {
  const now = status({ coordination_enabled: true, coordination_joined: true });
  const state = motionScheduleBadgeState(now);
  assert.equal(state.scope, 'group');
  assert.equal(state.text, '스케줄러: 마스터 (2개 등록)');
  assert.equal(state.warning, '');
  assert.match(motionScheduleScopeNote(now), /그룹에 참가한 모든 PC/);
});

test('빠져 있으면 발화해도 실행되지 않는다고 띄운다', () => {
  // 조정 노드가 `먼저 DDS 그룹에 참가하세요` 로 거부한다 · 전에는 로그에만
  // 남아서 "스케줄이 발화했는데 아무 일도 안 났다" 가 됐다 · §6-68
  const now = status({
    coordination_enabled: true,
    coordination_joined: false,
    coordination_node_connected: true,
  });
  const state = motionScheduleBadgeState(now);
  assert.equal(state.tone, 'warn');
  assert.match(state.text, /그룹에서 빠져 있음/);
  assert.match(state.warning, /시각이 되어도 실행되지 않습니다/);
  assert.match(state.warning, /「다시 참가」/);
  assert.equal(state.canEdit, true, '빠져 있어도 스케줄은 고칠 수 있다');
});

test('슬레이브는 고칠 수 없고 왜인지 말한다', () => {
  const now = status({
    is_master: false, coordination_enabled: true, coordination_joined: true,
  });
  const state = motionScheduleBadgeState(now);
  assert.equal(state.scope, 'slave');
  assert.equal(state.canEdit, false);
  assert.match(state.blockedReason, /아무 일을 하지 않습니다/);
  assert.match(state.blockedReason, /「지금 빠지기」/);
});

test('연동 노드가 안 붙으면 빠졌다고 단정하지 않는다', () => {
  // 브리지만 재시작한 직후에는 조정 노드 상태를 아직 못 받는다 · 그때
  // "빠져 있음" 이라 띄우면 없는 문제를 만든다
  const now = status({
    coordination_enabled: true,
    coordination_joined: false,
    coordination_node_connected: false,
  });
  const state = motionScheduleBadgeState(now);
  assert.match(state.text, /연동 상태 확인 중/);
  assert.equal(state.warning, '', '확인 중에는 경고하지 않는다');
});

test('수동 모드면 스케줄이 손대지 않는다고 말한다', () => {
  // 전에는 「사람이 멈췄나」를 요청 내용으로 추측했다 · 그룹 정지나 안전
  // 정지까지 사람이 멈춘 것으로 읽어 오판했다 · 스위치 하나로 바꿨다 · §6-143
  const now = status({ run_mode: 'manual' });
  const state = motionScheduleBadgeState(now);
  assert.equal(state.scope, 'manual');
  assert.match(state.text, /수동 모드/);
  assert.match(state.blockedReason, /시작·정지시키지 않습니다/);
  assert.match(motionScheduleScopeNote(now), /스케줄 모드로 바꾸면/);
});

test('슬레이브에서는 수동 모드가 앞에 나서지 않는다', () => {
  // 슬레이브는 스케줄 자체가 안 돈다 · 거기서 「수동 모드」라고 띄우면
  // 마스터가 보내는 그룹 실행까지 안 도는 것처럼 읽힌다 · §6-143
  const state = motionScheduleBadgeState(status({
    is_master: false,
    coordination_enabled: true,
    coordination_joined: true,
    coordination_node_connected: true,
    run_mode: 'manual',
  }));
  assert.equal(state.scope, 'slave');
  assert.equal(state.canEdit, false, '슬레이브에서는 실행 관리도 잠겨야 한다');
  assert.match(state.blockedReason, /「지금 빠지기」/);
});

test('슬레이브에서는 실행 관리 칸이 잠긴다', () => {
  // 여기서 수동으로 바꿔 두면 막힌 줄 알게 된다 · 실제로 막으려면
  // 그룹에서 나가야 한다 · §6-143
  const manager = readFileSync(
    new URL('../static/js/schedule_manager.js', import.meta.url), 'utf8',
  );
  assert.match(manager, /modeSelect\.disabled = !state\.canEdit/);
});

test('단독 PC 에서도 수동 모드가 보인다', () => {
  const state = motionScheduleBadgeState(status({ run_mode: 'manual' }));
  assert.equal(state.scope, 'manual');
});

test('없어진 「사람이 멈춤」 걸쇠가 되살아나지 않는다', () => {
  const source = readFileSync(
    new URL('../static/js/schedule_scope.js', import.meta.url), 'utf8',
  );
  assert.doesNotMatch(source, /schedule_hold_reason/);
  assert.match(indexHtml, /id="scheduleRunMode"/);
});

test('상태를 못 받았으면 아무것도 단정하지 않는다', () => {
  const state = motionScheduleBadgeState(null);
  assert.equal(state.scope, 'unknown');
  assert.equal(state.canEdit, false, '모르는 채로 누르게 두지 않는다');
});

test('옛 브리지가 새 값을 안 보내도 단독으로 읽힌다', () => {
  // 갱신 중인 PC 는 `coordination_enabled` 없이 답한다 · 그때 "마스터" 라고
  // 뜨면 예전 문제로 되돌아간다
  const state = motionScheduleBadgeState(status());
  assert.equal(state.scope, 'local');
});

test('화면이 이 판단을 실제로 쓴다', () => {
  const manager = readFileSync(
    new URL('../static/js/schedule_manager.js', import.meta.url), 'utf8',
  );
  assert.match(manager, /import \{[\s\S]*motionScheduleBadgeState[\s\S]*\} from '\.\/schedule_scope\.js'/);
  assert.match(manager, /motionScheduleBadgeState\(this\.status\)/);
  assert.match(manager, /getElementById\('scheduleScopeNotice'\)/);
  // 고정 문구였다 · 연동을 켠 적 없는 PC 에도 "마스터 PC 전용" 이라고 떴다
  assert.doesNotMatch(indexHtml, /마스터 PC 전용 스케줄러/);
  assert.match(indexHtml, /id="scheduleScopeNotice"/);
});
