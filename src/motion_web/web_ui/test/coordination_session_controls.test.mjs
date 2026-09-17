import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import { indexHtml } from '../tools/index_html.mjs';

const html = indexHtml;
const coordination = readFileSync(
  new URL('../static/js/coordination.js', import.meta.url), 'utf8',
);
const dom = readFileSync(new URL('../static/js/dom.js', import.meta.url), 'utf8');

/**
 * 「연동 사용」과 「지금 빠지기」는 다른 것을 바꾼다 · §6-132
 *
 *   연동 사용   `enabled` · 설정 파일에 영구히 남는다
 *   지금 빠지기 `_joined` · 노드 메모리만 · 재시작하면 「연동 사용」을 따라 되돌아간다
 *
 * 화면에서는 둘 다 그냥 선택지와 버튼이라 어느 쪽이 남는지 알 길이 없었다 ·
 * 게다가 영구적인 쪽이 접힌 구역 안에 숨어 있고 휘발성인 쪽이 밖에 있었다.
 */

test('연동 사용 선택이 접힌 채로 숨지 않는다', () => {
  assert.match(html, /<details class="coordination-settings-details" open>/);
});

test('연동 사용이 설정 칸 맨 앞에 온다', () => {
  const fields = html.match(
    /<div class="coordination-settings-fields">[\s\S]*?<\/div>\s*<div class="coordination-settings-actions">/,
  )?.[0] || '';
  assert.ok(fields, '설정 칸 묶음을 읽지 못했다');
  const order = ['coordinationEnabled', 'coordinationIsMaster', 'coordinationPcId']
    .map((id) => fields.indexOf(id));
  assert.ok(order.every((at) => at >= 0), '세 칸이 모두 있어야 한다');
  assert.deepEqual([...order].sort((a, b) => a - b), order, '연동 사용 · 역할 · 그다음이 순서다');
});

test('「연동 설정」이라는 겹치는 이름을 쓰지 않는다', () => {
  assert.match(html, /<span>연동 사용<\/span>/);
  assert.doesNotMatch(html, /<span>연동 설정<\/span>/);
});

test('세션 버튼은 둘뿐이고 한 번에 하나만 보인다', () => {
  const actions = html.match(
    /<div class="coord-badge coord-badge-actions">[\s\S]*?<\/div>/,
  )?.[0] || '';
  assert.ok(actions, '버튼 묶음을 읽지 못했다');
  const buttons = [...actions.matchAll(/<button id="(\w+)"[^>]*>([^<]+)</g)]
    .map((match) => [match[1], match[2]]);
  assert.deepEqual(buttons, [
    ['coordinationJoinButton', '다시 참가'],
    ['coordinationTemporaryDisableButton', '지금 빠지기'],
  ]);
  // 참가 초기값이 「연동 사용」이라 평소에는 이미 참가 상태로 뜬다
  assert.match(coordination, /coordinationJoinButton\.hidden = joined/);
  assert.match(coordination, /coordinationTemporaryDisableButton\.hidden = !joined/);
});

test('없앤 「그룹 나가기」가 코드에 남아 조용히 죽지 않는다', () => {
  // 요소가 사라졌는데 등록부와 쓰는 코드가 남으면 `if (el.X)` 안에서
  // 아무 일도 하지 않는다 · §6-61
  assert.doesNotMatch(html, /coordinationLeaveButton/);
  assert.doesNotMatch(dom, /coordinationLeaveButton/);
  assert.doesNotMatch(coordination, /coordinationLeaveButton/);
  assert.doesNotMatch(coordination, /control\('leave'\)/);
});

test('무엇이 남고 무엇이 풀리는지 화면이 말한다', () => {
  assert.match(html, /프로그램을 다시 켜면 아래 「연동 사용」 설정을 따라 자동으로 다시 참가합니다/);
  assert.match(html, /저장하면 계속 유지됩니다/);
});

test('빠지기 확인창이 그룹 실행 중일 때를 따로 묻는다', () => {
  const body = coordination.match(
    /async function temporarilyDisable\(\)[\s\S]*?\n  \}/,
  )?.[0] || '';
  assert.ok(body, '확인창 코드를 읽지 못했다');
  assert.match(body, /참가한 모든 PC 의 모션이 즉시 정지된 뒤/);
  assert.match(body, /회차가 끝나기를 기다리지 않습니다/);
  assert.match(body, /프로그램을 다시 켜면 「연동 사용」 설정을 따라 자동으로 다시 참가합니다/);
  // 확인창은 글자 그대로 나온다 · 꾸밈 기호는 그대로 보인다
  assert.doesNotMatch(body, /\*\*/);
});
