import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const html = readFileSync(new URL('../static/index.html', import.meta.url), 'utf8');
const controller = readFileSync(new URL('../static/js/motor_config.js', import.meta.url), 'utf8');
const motionTest = readFileSync(new URL('../static/js/motion_test.js', import.meta.url), 'utf8');
const styles = readFileSync(new URL('../static/styles.css', import.meta.url), 'utf8');

test('motor management exposes one seven-stage preparation flow', () => {
  for (const step of [
    'service',
    'connection',
    'configuration',
    'application',
    'mapping',
    'drive',
    'verification',
  ]) {
    assert.match(html, new RegExp(`data-motor-readiness-step="${step}"`));
    assert.match(controller, new RegExp(`key: '${step}'`));
  }
  assert.match(controller, /renderMotorReadiness\(rows, rowViews, changed\)/);
  assert.match(styles, /\.motor-readiness-steps/);
});

test('axis readiness table keeps runtime facts distinct', () => {
  for (const heading of [
    '실제 장치 식별',
    '설정·실행 적용',
    '모션 매칭',
    '서보·토크',
    '조그·동작',
    '모션 실행',
    '최종 상태',
  ]) {
    assert.match(html, new RegExp(`<th>${heading}</th>`));
  }
  assert.match(controller, /motion_axis_configured === true/);
  assert.match(controller, /runtime\.servo_on === true/);
  assert.match(controller, /축별 실행 이력은 미지원/);
  assert.match(controller, /실물 검증 미확인/);
});

test('unsupported Dynamixel torque controls are not presented as working actions', () => {
  assert.match(html, /현재 명시적 토크 제어 버튼은 지원하지 않으며/);
  assert.doesNotMatch(html, /id="[^"]*Dynamixel[^"]*Torque/);
});

test('existing AC servo API is reachable per axis from motor management', () => {
  assert.match(controller, /data-axis-servo-action="servo_on"/);
  assert.match(controller, /data-axis-servo-action="servo_off"/);
  assert.match(controller, /data-axis-servo-action="fault_reset"/);
  assert.match(controller, /onAcServoControl\(button\.dataset\.axisServoAction/);
  assert.match(motionTest, /controlAcServo: async \(action, axis\)/);
  assert.match(motionTest, /sendAcServoControl\(action, 'selected'\)/);
});

test('current project name is visually emphasized without emphasizing an empty state', () => {
  assert.match(styles, /\.header-project-name\s*\{[\s\S]*?background: #1f5fca;[\s\S]*?color: #fff;/);
  assert.match(styles, /\.header-project-name\.empty\s*\{[\s\S]*?background: #f5f9fd;/);
  assert.match(styles, /\.project-current-display\s*\{[\s\S]*?background: #1f5fca;[\s\S]*?color: #fff;/);
  assert.match(styles, /\.project-current-display\.empty\s*\{[\s\S]*?background: #f5f9fd;/);
});

test('motor setting tabs use concise names', () => {
  assert.match(html, /data-axis-settings-tab="table">상세 설정 표</);
  assert.match(html, /data-axis-settings-tab="raw">원본 보기</);
  assert.doesNotMatch(html, />설정 표</);
  assert.doesNotMatch(html, />고급 원본 보기</);
});

test('project selection lives in the left project sidebar only', () => {
  const sidebar = html.match(/<aside class="project-sidebar"[\s\S]*?<\/aside>\s*<section id="projectSetupProgress"/)?.[0] || '';
  assert.match(sidebar, /id="projectExplorerCurrentName"/);
  assert.match(sidebar, /id="projectExplorerSelect"/);
  assert.doesNotMatch(html, /class="project-system-manager system-project-card"/);
});

test('system status cards use compact exact desktop columns', () => {
  assert.match(styles, /\.system-status-primary\s*\{[\s\S]*?grid-template-columns: repeat\(5,/);
  assert.match(styles, /\.system-status-primary > div\s*\{[\s\S]*?min-height: 56px;/);
  assert.match(styles, /\.system-status-secondary\s*\{[\s\S]*?grid-template-columns: repeat\(4,/);
  assert.match(styles, /\.system-status-secondary > div\s*\{[\s\S]*?min-height: 46px;/);
});

test('motor type summary separates configuration, physical and runtime facts', () => {
  const headings = [
    '프로젝트 설정',
    '물리 감지',
    '실행 적용',
    '런타임 보고 축',
    '오류',
    '서보·토크 ON',
    '제어 가능',
  ];
  for (const heading of headings) {
    assert.match(html, new RegExp(`<th[^>]*[^>]*>${heading}</th>`));
  }
  assert.match(
    html,
    new RegExp(headings.map((heading) => `>${heading}</th>`).join('[\\s\\S]*')),
  );
  assert.doesNotMatch(html, /전체 \/ 온라인/);
  assert.match(
    html,
    /title="motor_manager 상태 메시지에 포함된 축 수이며 물리 연결 수가 아닙니다">런타임 보고 축<\/th>/,
  );
  assert.match(controller, /function physicalScanStatus\(typeKey\)/);
  assert.match(controller, /if \(!scan \|\| scan\.skipped\) return \{ code: 'unknown', text: '미확인'/);
  assert.match(controller, /motor\.connection_connected === true/);
  assert.match(controller, /motor\.servo_on === true/);
  assert.match(controller, /Boolean\(motor\.fault\) \|\| Number\(motor\.errorcode \|\| 0\) !== 0/);
  assert.match(controller, /return \{ text: '물리 확인 필요', detail: '장비 검색 후 판정'/);
  assert.match(controller, /view\.row\.runtimeMotor\?\.state === 'detected'[\s\S]*?Boolean\(view\.row\.scanRow\)/);
});
