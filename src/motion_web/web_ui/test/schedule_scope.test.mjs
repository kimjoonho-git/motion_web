import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import { indexHtml } from '../tools/index_html.mjs';
import {
  motionScheduleBadgeState,
  motionScheduleResumeNote,
  motionScheduleScopeNote,
  motionScheduleTimezoneDrift,
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

test('묶여 있어도 같은 말을 한다 · 스케줄은 그것과 상관없다 · §6-266', () => {
  const now = status({ coordination_enabled: true, coordination_joined: true });
  const state = motionScheduleBadgeState(now);
  assert.equal(state.text, '스케줄러: 동작 중 (2개 등록)');
  assert.equal(state.warning, '');
  assert.doesNotMatch(motionScheduleScopeNote(now), /연동|그룹|마스터/);
});

test('묶이지 않았어도 경고하지 않는다 · 혼자 돈다 · §6-266', () => {
  // 전에는 「시각이 되어도 실행되지 않습니다」라고 했고 실제로 그랬다 ·
  // 16~18시 구간 안에서 15분 동안 7번 거절당하며 한 번도 돌지 않았다 ·
  // 이제 묶이지 않으면 이 PC 혼자 돈다 · 그 경고는 사실이 아니다.
  const now = status({
    coordination_enabled: true,
    coordination_joined: false,
    coordination_node_connected: true,
  });
  const state = motionScheduleBadgeState(now);
  assert.equal(state.tone, 'ok');
  assert.equal(state.warning, '');
  assert.equal(state.canEdit, true);
  assert.doesNotMatch(state.text, /빠져 있음/);
});

test('슬레이브는 고칠 수 없고 왜인지 말한다', () => {
  const now = status({
    is_master: false, coordination_enabled: true, coordination_joined: true,
  });
  const state = motionScheduleBadgeState(now);
  assert.equal(state.scope, 'slave');
  assert.equal(state.canEdit, false);
  assert.match(state.blockedReason, /아무 일을 하지 않습니다/);
  assert.match(state.blockedReason, /「전체 동작 정지」/);
});

test('다른 노드 상태를 못 받아도 스케줄은 그대로다 · §6-266', () => {
  const now = status({
    coordination_enabled: true,
    coordination_joined: false,
    coordination_node_connected: false,
  });
  const state = motionScheduleBadgeState(now);
  assert.equal(state.text, '스케줄러: 동작 중 (2개 등록)');
  assert.equal(state.warning, '');
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
  assert.match(state.blockedReason, /「전체 동작 정지」/);
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

/**
 * 시각이 됐는데 거부당했으면 화면이 말해야 한다 · §6-147
 *
 * 실제로 겪었다 · 연동 PC 가 한 대뿐이라 스케줄이 60초마다 시도하고 매번
 * 「정상 연결된 PC 가 2대 이상 필요합니다」로 거부당했는데, 배지는 한 시간
 * 내내 초록불이었다 · 로그에도 `success` 라고 찍혀서 아무 단서가 없었다.
 */

const rejected = (count, extra = {}) => status({
  coordination_enabled: true,
  coordination_joined: true,
  last_failure: {
    count,
    message: '그룹 연동에는 정상 연결된 PC가 2대 이상 필요합니다',
  },
  ...extra,
});

test('한 번 거부는 경합일 수 있다 · 노랑으로 말한다', () => {
  const state = motionScheduleBadgeState(rejected(1));
  assert.equal(state.tone, 'warn');
  assert.equal(state.text, '스케줄러: 시작 거부됨 (1회)');
  assert.match(state.warning, /2대 이상 필요/);
  assert.equal(state.canEdit, true, '거부당했다고 설정을 못 고치게 하면 안 된다');
});

test('세 번 연달아 거부당하면 빨강으로 올린다', () => {
  const state = motionScheduleBadgeState(rejected(3));
  assert.equal(state.tone, 'bad');
  assert.match(state.text, /3회/);
});

test('거부가 없으면 예전 그대로 초록이다', () => {
  const state = motionScheduleBadgeState(rejected(0));
  assert.equal(state.tone, 'ok');
  assert.match(state.text, /동작 중/);
});

test('수동 모드에서는 묵은 거부를 떠들지 않는다', () => {
  // 스케줄이 손대지 않는 상태다 · 지난 거부는 지금 사실이 아니다
  const state = motionScheduleBadgeState(rejected(5, { run_mode: 'manual' }));
  assert.equal(state.scope, 'manual');
  assert.equal(state.tone, 'muted');
});

test('슬레이브에서도 거부를 떠들지 않는다', () => {
  // 여기서는 스케줄 자체가 돌지 않는다 · 마스터 화면에서 볼 일이다
  const state = motionScheduleBadgeState(rejected(5, { is_master: false }));
  assert.equal(state.scope, 'slave');
});

test('연동을 안 쓰는 단독 PC 의 거부도 말한다', () => {
  const state = motionScheduleBadgeState(status({
    last_failure: { count: 2, message: '모션 파일이 없습니다' },
  }));
  assert.equal(state.scope, 'local');
  assert.equal(state.tone, 'warn');
  assert.match(state.warning, /모션 파일이 없습니다/);
});

/**
 * 멈춰도 스케줄이 되돌린다면 그 자리에서 말해야 한다 · §6-149
 *
 * 스케줄 모드에서는 사람이 정지를 눌러도 다음 점검에 다시 시작한다 · 설계가
 * 그렇다(§6-143) · 문제는 아는 사람만 안다는 것이고, 무대에서 「잠깐 멈춰」
 * 하고 눌렀는데 저절로 도로 도는 건 위험하다.
 */

const inWindow = (extra = {}) => status({
  run_mode: 'schedule',
  active_schedule_id: 'sched-1',
  reconcile_interval_sec: 10,
  ...extra,
});

test('스케줄이 되돌릴 상황이면 몇 초 뒤인지까지 말한다', () => {
  const note = motionScheduleResumeNote(inWindow());
  assert.match(note, /최대 10초 뒤 다시 시작/);
  assert.match(note, /수동 모드/, '어떻게 해야 안 돌아오는지도 말해야 한다');
});

test('수동 모드면 되돌리지 않는다 · 겁줄 필요 없다', () => {
  assert.equal(motionScheduleResumeNote(inWindow({ run_mode: 'manual' })), '');
});

test('구간 밖이면 되돌리지 않는다', () => {
  assert.equal(motionScheduleResumeNote(inWindow({ active_schedule_id: '' })), '');
});

test('슬레이브에서는 스케줄이 손대지 않는다', () => {
  assert.equal(motionScheduleResumeNote(inWindow({
    coordination_enabled: true, is_master: false,
  })), '');
});

test('주기를 모르면 숫자를 지어내지 않는다', () => {
  const note = motionScheduleResumeNote(inWindow({ reconcile_interval_sec: null }));
  assert.match(note, /잠시 뒤 다시 시작/);
  assert.doesNotMatch(note, /\d+초/);
});

/**
 * 들고 나갔는데 시간대만 안 바뀐 경우 · §6-150
 *
 * NTP 는 절대 시각(UTC)만 맞춘다 · 시간대는 사람이 정하는 값이라 네트워크에
 * 붙여도 안 바뀐다 · 한국에서 만든 09:17 스케줄이 파리에서 현지 02:17 에 돈다.
 */

const stamped = (zone) => ({ enabled: true, saved_timezone: zone });

test('스케줄이 태어난 곳과 지금이 다르면 말한다', () => {
  const note = motionScheduleTimezoneDrift([stamped('Asia/Seoul')], 'Europe/Paris');
  assert.match(note, /Asia\/Seoul/);
  assert.match(note, /Europe\/Paris/);
});

test('같으면 아무 말도 하지 않는다', () => {
  assert.equal(motionScheduleTimezoneDrift([stamped('Asia/Seoul')], 'Asia/Seoul'), '');
});

test('꺼 둔 스케줄은 따지지 않는다', () => {
  // 안 도는 스케줄 때문에 경고가 떠 있으면 곧 아무도 안 읽는다
  const off = { enabled: false, saved_timezone: 'Asia/Seoul' };
  assert.equal(motionScheduleTimezoneDrift([off], 'Europe/Paris'), '');
});

test('지문이 없는 옛 스케줄은 따지지 않는다', () => {
  // 이 값이 생기기 전에 만든 스케줄이 있다 · 그걸로 겁주면 안 된다
  assert.equal(motionScheduleTimezoneDrift([{ enabled: true }], 'Europe/Paris'), '');
});

test('지금 시간대를 모르면 따지지 않는다', () => {
  assert.equal(motionScheduleTimezoneDrift([stamped('Asia/Seoul')], ''), '');
});

test('여러 곳에서 만들어졌으면 전부 적는다', () => {
  const note = motionScheduleTimezoneDrift(
    [stamped('Asia/Seoul'), stamped('America/New_York'), stamped('Europe/Paris')],
    'Europe/Paris',
  );
  assert.match(note, /America\/New_York, Asia\/Seoul/);
  assert.doesNotMatch(note, /Europe\/Paris 에서/, '지금과 같은 것은 빼야 한다');
});
