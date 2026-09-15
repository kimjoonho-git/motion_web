import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';
import { indexHtml } from '../tools/index_html.mjs';

const index = indexHtml;
const navigation = fs.readFileSync(
  new URL('../static/js/workspace_navigation.js', import.meta.url),
  'utf8',
);
const controller = fs.readFileSync(
  new URL('../static/js/servo_alarm.js', import.meta.url),
  'utf8',
);
const main = fs.readFileSync(
  new URL('../static/js/main.js', import.meta.url),
  'utf8',
);

// 서보 에러 관리는 설정이 아니라 운영 중 대응이다 · [운영]으로 옮겼다 · §6-67
test('서보 에러 관리는 운영 그룹에 있다', () => {
  assert.match(index, /data-workspace-tab="config">모터 관리/);
  assert.match(index, /data-workspace-tab="servo-errors">서보 에러 관리/);
  assert.match(index, /data-workspace-panel="servo-errors"/);
  assert.match(
    navigation,
    // 연동은 모션 실행 화면으로 합쳐졌다 · 별도 탭이 아니다 · §6-66
    /operations: Object\.freeze\(\['monitoring', 'servo-errors', 'log', 'btop'/,
  );
});

test('프로젝트 등급 편집과 실제 안전상태 표시가 연결되어 있다', () => {
  assert.match(controller, /fetchServoAlarmPolicy/);
  assert.match(controller, /saveServoAlarmPolicy/);
  assert.match(controller, /servo_alarm_grade3_latched/);
  assert.match(controller, /프로그램 재시작 필요/);
  assert.match(controller, /전체 항목을 기본 등급으로 변경/);
  assert.match(controller, /servo_alarm_policy_revision/);
  assert.match(controller, /저장된 프로젝트 등급과 Supervisor 적용 등급이 일치하지 않습니다/);
});

test('통신 미수신 0xFFFF는 서보 에러 팝업으로 분류하지 않는다', () => {
  assert.match(main, /rawErrorCode === 0xFFFF/);
  assert.match(main, /if \(communicationUnavailable\) return false/);
});
