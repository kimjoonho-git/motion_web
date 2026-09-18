import assert from 'node:assert/strict';
import test from 'node:test';
import { indexHtml } from '../tools/index_html.mjs';
import { timezoneCommand } from '../static/js/system_time.js';

/**
 * 해외 설치에서 사람이 직접 하는 유일한 일 · §6-150
 *
 * NTP 는 절대 시각(UTC)만 맞추고 시간대는 안 바꾼다 · PC 를 파리에 들고 가서
 * 네트워크에 붙여도 `Asia/Seoul` 그대로고, 09:17 스케줄이 현지 02:17 에 돈다.
 *
 * 화면은 **칠 명령만 만들어 준다** · `timedatectl` 은 root 권한이 필요하고,
 * 그 권한을 웹 서비스에 주는 것이 더 위험하다.
 */

const ZONES = ['Asia/Seoul', 'Europe/Paris', 'America/New_York'];

test('고르면 그대로 칠 수 있는 명령이 나온다', () => {
  const { command } = timezoneCommand({
    zones: ZONES, chosen: 'Europe/Paris', current: 'Asia/Seoul',
  });
  assert.equal(command, 'sudo timedatectl set-timezone Europe/Paris');
});

test('안 골랐으면 명령을 만들지 않는다', () => {
  assert.deepEqual(timezoneCommand({ zones: ZONES }), { command: '', note: '' });
});

test('목록에 없는 이름이면 명령을 안 준다 · 오타를 치게 두지 않는다', () => {
  // `Europe/Pari` 는 `timedatectl` 이 거부한다 · 거부 이유는 터미널에만 남아서
  // 사람은 왜 안 되는지 모른 채 시간을 쓴다
  const { command, note } = timezoneCommand({ zones: ZONES, chosen: 'Europe/Pari' });
  assert.equal(command, '');
  assert.match(note, /목록에 없는/);
});

test('이미 그 시간대면 그렇다고 말해 준다', () => {
  const { command, note } = timezoneCommand({
    zones: ZONES, chosen: 'Asia/Seoul', current: 'Asia/Seoul',
  });
  assert.ok(command, '이미 맞아도 명령 자체는 보여 준다');
  assert.match(note, /이미/);
});

test('앞뒤 공백은 흘려보낸다', () => {
  const { command } = timezoneCommand({ zones: ZONES, chosen: '  Europe/Paris  ' });
  assert.equal(command, 'sudo timedatectl set-timezone Europe/Paris');
});

test('목록을 못 받았어도 막히지 않는다', () => {
  // 서버가 목록을 못 읽는 배포도 있다 · 그때까지 사람을 막으면 설치를 못 한다
  const { command } = timezoneCommand({ zones: [], chosen: 'Europe/Paris' });
  assert.equal(command, 'sudo timedatectl set-timezone Europe/Paris');
});

test('화면에 자리가 있다', () => {
  for (const id of [
    'systemTimezoneNow', 'systemTimezonePick', 'systemTimezoneList',
    'systemTimezoneCommand', 'btnCopyTimezoneCommand', 'systemTimezoneMismatch',
  ]) {
    assert.ok(indexHtml.includes(`id="${id}"`), `${id} 자리가 없다`);
  }
});

test('시간대를 바꾼 뒤 다시 빌드하라고 알려준다', () => {
  // 돌고 있던 노드가 옛 시간대를 들고 있다 · 이 말이 없으면 바꿔 놓고도
  // 왜 그대로인지 모른다
  assert.match(indexHtml, /다시 빌드 명령.{0,40}한 번 더/s);
});
