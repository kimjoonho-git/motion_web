import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const html = readFileSync(new URL('../static/index.html', import.meta.url), 'utf8');
const main = readFileSync(new URL('../static/js/main.js', import.meta.url), 'utf8');

function countId(id) {
  return [...html.matchAll(new RegExp(`id=["']${id}["']`, 'g'))].length;
}

test('two-level workspace navigation exposes every defined group and route', () => {
  for (const group of ['operations', 'setup', 'creation', 'execution']) {
    assert.match(html, new RegExp(`data-workspace-group=["']${group}["']`));
    assert.match(html, new RegExp(`data-workspace-group-panel=["']${group}["']`));
  }
  for (const route of [
    'monitoring', 'log', 'system', 'config',
    'motion-files', 'motion-mapping', 'motion-midi', 'studio',
    'manual', 'motion-run',
  ]) {
    assert.match(html, new RegExp(`data-workspace-tab=["']${route}["']`));
  }
  assert.match(main, /defaultWorkspaceForGroup/);
  assert.match(main, /workspaceForLegacyNavigation/);
  assert.doesNotMatch(main, /tab\?\.click\(\)/);
});

test('motion screens use workspace routes without obsolete internal tab controls', () => {
  assert.doesNotMatch(html, /id=["']motionTabs["']/);
  assert.doesNotMatch(html, /data-motion-tab=/);
  for (const panel of ['files', 'mapping', 'midi', 'run']) {
    assert.match(html, new RegExp(`data-motion-panel=["']${panel}["']`));
  }
});

test('settings workflows preserve action IDs and expose their defined steps', () => {
  for (const id of [
    'scanButton',
    'dynamixelScanButton',
    'saveAxisConfigButton',
    'applyAxisConfigButton',
    'refreshMotionMappingsButton',
    'newMotionMappingButton',
    'addMotionIdButton',
    'generateMotionIdsButton',
    'validateMotionMappingButton',
    'saveMotionMappingButton',
    'resetMotionMappingButton',
    'deleteMotionMappingButton',
  ]) {
    assert.equal(countId(id), 1, `${id} must remain unique`);
  }
  assert.match(html, /1\. 장비 검색/);
  assert.match(html, /2\. 검색 결과 확인 및 축 편집/);
  assert.match(html, /3\. 저장·실행 적용/);
  assert.doesNotMatch(html, /4\. 실제 시스템 적용/);
  assert.match(html, /1\. 설정 파일 선택/);
  assert.match(html, /2\. 모션축 편집/);
  assert.match(html, /3\. 검증 및 계산 미리보기/);
  assert.match(html, /4\. 저장/);
});
