/**
 * 탭은 "무엇을 하느냐" 로 나눈다 · §6-100
 *
 * 모드(단독/연동)로 나누면 **같은 일(재생)이 두 곳에** 생긴다 · 탭을 열기
 * 전에 "지금 연동 중이던가?" 를 먼저 기억해야 한다.
 *
 *   PC 연동 설정 · 그룹 · 참가 · 명단 · 그룹 실행 · 각 PC 진행 · 예약 · MIDI
 *   모션 실행    · 이 PC · 모션 고르기 · 실행 · 진행 · 그래프
 *
 * **그룹 실행은 모션 파일을 들고 가지 않는다** · 참가한 PC 들에게 시작·정지
 * 신호만 보내고 각 PC 는 제 모션을 돌린다 · "이 파일을 이 PC 에서 돌린다"
 * 와는 다른 일이라 화면도 버튼도 따로 둔다.
 */

import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import { indexHtml } from '../tools/index_html.mjs';

const html = indexHtml;
const coordination = readFileSync(
  new URL('../static/js/coordination.js', import.meta.url), 'utf8');
const motionData = readFileSync(
  new URL('../static/js/motion_data.js', import.meta.url), 'utf8');
const scheduleManager = readFileSync(
  new URL('../static/js/schedule_manager.js', import.meta.url), 'utf8');

/** 이 요소가 어느 탭 안에 있나. */
function panelOf(id) {
  const at = html.indexOf(`id="${id}"`);
  assert.notEqual(at, -1, `${id} 가 화면에 없다`);
  const seen = [...html.slice(0, at).matchAll(/data-workspace-panel="([a-z-]+)"/g)];
  return seen.length ? seen[seen.length - 1][1] : null;
}

test('이 PC 의 모션은 모션 실행 탭에 있다', () => {
  assert.equal(panelOf('motionRunStartButton'), 'motion', '1회 시작');
  assert.equal(panelOf('motionRunStatus'), 'motion', '실행 상태');
  assert.equal(panelOf('motionRunGraphCanvas'), 'motion', '모션 그래프');
  assert.equal(panelOf('motionRunBlockReason'), 'motion', '못 누르는 이유');
});

test('그룹에 관한 것은 전부 PC 연동 설정 탭에 있다', () => {
  assert.equal(panelOf('coordinationJoinButton'), 'coordination', '그룹 참가');
  assert.equal(panelOf('coordinationPeerRows'), 'coordination', '참가 PC 명단');
  assert.equal(panelOf('coordinationConfirmRosterButton'), 'coordination', '명단 확정');
  assert.equal(panelOf('coordinationSaveButton'), 'coordination', '이 PC 연동 설정');
  assert.equal(panelOf('coordinationStartOnceButton'), 'coordination', '그룹 1회 시작');
  assert.equal(panelOf('coordinationStopNowButton'), 'coordination', '그룹 즉시 정지');
  assert.equal(panelOf('motionRunPeerRows'), 'coordination', '각 PC 진행');
  assert.equal(panelOf('coordinationExecutionState'), 'coordination', '그룹 실행 상태');
  assert.equal(panelOf('coordinationAcknowledgeErrorButton'), 'coordination', '그룹 오류 확인');
  assert.equal(panelOf('coordinationAutoPlayToggle'), 'coordination', '부팅 시 자동 재생');
  assert.equal(panelOf('midiTargetChoices'), 'coordination', 'MIDI 사용 PC');
});

test('스케줄 진입점은 맨 위 한 곳뿐이다', () => {
  // 스케줄은 연동 전용이 아니다 · 연동을 쓰면 그룹이 함께 움직이고 안 쓰면
  // 이 PC 만 움직일 뿐, 같은 스케줄 한 벌이다 · 진입점이 둘이면 "연동
  // 스케줄과 그냥 스케줄이 따로 있나" 로 읽힌다 · §6-133
  assert.equal(panelOf('btnScheduleModal'), null, '맨 위 상단 바');
  assert.doesNotMatch(html, /btnScheduleModalCoord/);
  assert.doesNotMatch(html, /scheduleStatusBadgeCoord/);
  assert.doesNotMatch(scheduleManager, /Coord'/);
});

test('탭 이름이 "설정" 이라고 말해 준다', () => {
  // 눌러 보기 전에 "여긴 준비하는 곳, 실행은 저기" 가 전달되어야 한다
  assert.match(html, /data-workspace-tab="coordination">PC 연동 설정/);
});

test('명단은 참가하면, 그룹 실행은 마스터에서만 보인다', () => {
  assert.match(
    coordination,
    /coordinationRosterSection\?\.classList\.toggle\('hidden', !joined\)/,
  );
  assert.match(
    coordination,
    /coordinationGroupRunSection\?\.classList\.toggle\(/,
  );
  // 실행 화면은 대상을 고르지 않으므로 숨길 것도 없다
  assert.doesNotMatch(motionData, /motionRunGroupProgress/);
});

test('PC 한 대가 두 표에 다른 열로 나온다', () => {
  // 구성 표와 진행 표가 같은 행 만들기를 쓰되 열이 다르다 · 두 벌로 베끼면
  // 한쪽만 고쳐져 갈린다
  assert.match(coordination, /return \{\n\s+setup: `<tr>/);
  assert.match(coordination, /progress: `<tr>/);
  assert.match(coordination, /rows\.map\(\(row\) => row\.setup\)/);
  assert.match(coordination, /rows\.map\(\(row\) => row\.progress\)/);
});

test('어느 PC 의 화면인지 상단바에 늘 보인다', () => {
  // 여러 PC 를 한 사람이 띄워 놓고 쓴다 · 탭을 옮겨야 알 수 있으면 엉뚱한
  // PC 에 명령을 내린다 · §6-100
  assert.match(html, /id="globalPcName"/);
  const main = readFileSync(new URL('../static/js/main.js', import.meta.url), 'utf8');
  assert.match(main, /globalPcName\.textContent = hostname/);
});

test('같은 사실을 두 곳에 적지 않는다', () => {
  // `이 PC ID` 뱃지는 상단바로 갔다 · 연동 화면 설정 칸에는 입력이 따로 있다
  assert.doesNotMatch(html, /id="coordinationMachineId"/);
  assert.match(html, /id="coordinationPcId"/);
  // 내용이 3번 칸으로 간 뒤 껍데기만 남아 있던 줄
  assert.doesNotMatch(html, /coord-group-label">그룹 실행/);
});
