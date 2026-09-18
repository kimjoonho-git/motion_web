/** 화면이 서버로 가는 길은 하나다 · §6-181
 *
 * **세 모듈이 `api.js` 를 건너뛰고 있었다.**
 *
 *     schedule_manager.js   여섯 곳
 *     system_time.js        한 곳
 *     docs_viewer.js        한 곳
 *
 * `api.js` 는 그냥 `fetch` 가 아니다 · 프로젝트 세대 번호를 붙이고, 서버가
 * 앞섰으면 화면이 따라가게 하고, 오류 문구를 정리한다 · 건너뛴 셋은 그걸
 * 전부 놓쳤다.
 *
 * 특히 스케줄이 문제였다 · 스케줄은 **프로젝트에 매인다**
 * (`active_project_id` 를 갖는다) · 그런데 세대 검사를 안 거치니, 프로젝트를
 * 바꾼 직후에도 옛 프로젝트 기준으로 저장될 수 있었다.
 *
 * **다만 모두가 프로젝트에 매이지는 않는다** · 시스템 시각과 사용법 문서는
 * 어느 프로젝트를 고르든 같은 답이다 · 그것까지 검사하면 프로젝트를 바꾸는
 * 순간 아무 상관 없는 호출이 실패한다 · 그래서 관문이 「이 호출이 프로젝트에
 * 매이나」를 안다 (`projectScoped`).
 */

import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
import test from 'node:test';

const JS_DIR = new URL('../static/js/', import.meta.url);

function sources() {
  return readdirSync(JS_DIR)
    .filter((name) => name.endsWith('.js') && name !== 'api.js')
    .map((name) => [name, readFileSync(new URL(name, JS_DIR), 'utf8')]);
}

const API = readFileSync(new URL('api.js', JS_DIR), 'utf8');

test('api.js 말고는 아무도 fetch 를 직접 부르지 않는다', () => {
  const offenders = [];
  for (const [name, text] of sources()) {
    for (const match of text.matchAll(/(^|[^.\w])fetch\s*\(/g)) {
      const line = text.slice(0, match.index).split('\n').length;
      offenders.push(`${name}:${line}`);
    }
  }
  assert.deepEqual(offenders, [], (
    '서버를 부르는 길은 api.js 하나입니다 · 프로젝트 세대·늦은 응답 처리를 '
    + `건너뛰게 됩니다:\n  ${offenders.join('\n  ')}`
  ));
});

test('관문은 프로젝트에 매이지 않는 호출을 구별한다', () => {
  // 구별이 없으면 시스템 시각·문서가 프로젝트 전환 때 괜히 실패한다
  assert.match(API, /projectScoped = true/);
  assert.match(API, /if \(!projectScoped\) \{/);
});

test('프로젝트와 무관한 것만 검사를 건너뛴다', () => {
  const skipped = [...API.matchAll(/export const (\w+)[^;]*projectScoped: false/g)]
    .map((match) => match[1]);

  assert.deepEqual(skipped.sort(), ['fetchDocument', 'fetchDocumentList', 'fetchSystemTime']);
});

test('스케줄은 프로젝트에 매인 것으로 다룬다', () => {
  // `active_project_id` 를 갖는다 · 옛 프로젝트 기준으로 저장되면 안 된다
  for (const name of [
    'fetchScheduleStatus', 'fetchScheduleList', 'saveScheduleRunMode',
    'saveSchedule', 'setScheduleEnabled', 'deleteSchedule',
  ]) {
    const line = API.split('\n').find((row) => row.includes(`export const ${name} `));
    assert.ok(line, `${name} 이 api.js 에 없습니다`);
    assert.ok(!line.includes('projectScoped: false'), `${name} 이 세대 검사를 건너뜁니다`);
  }
});

test('같은 길을 두 번 만들지 않는다', () => {
  const names = [...API.matchAll(/^export const (\w+)/gm)].map((match) => match[1]);
  const twice = names.filter((name, index) => names.indexOf(name) !== index);

  assert.deepEqual(twice, [], `api.js 에 같은 이름이 두 번 있습니다: ${twice}`);
});

test('프로젝트가 바뀌는 중에는 사람을 부르지 않는다', () => {
  // 폴링이 매번 경고창을 띄우면 프로젝트를 못 바꾼다
  const text = readFileSync(new URL('schedule_manager.js', JS_DIR), 'utf8');
  const guards = (text.match(/staleProjectResponse/g) || []).length;

  assert.ok(guards >= 6, (
    `스케줄이 늦은 응답을 걸러내는 곳이 ${guards}곳뿐입니다 · `
    + '프로젝트를 바꿀 때마다 경고창이 뜹니다'
  ));
});
