import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import { indexHtml, stylesCss } from '../tools/index_html.mjs';

const html = indexHtml;
const main = readFileSync(new URL('../static/js/main.js', import.meta.url), 'utf8');
const styles = stylesCss;

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
    'motion-mapping', 'motion-midi', 'studio',
    'manual', 'motion-run',
  ]) {
    assert.match(html, new RegExp(`data-workspace-tab=["']${route}["']`));
  }
  assert.match(main, /defaultWorkspaceForGroup/);
  assert.match(main, /workspaceForLegacyNavigation/);
  assert.doesNotMatch(main, /tab\?\.click\(\)/);
});

test('motion files are managed inside the execution screen, not a separate tab', () => {
  const creationPanel = html.match(
    /data-workspace-group-panel="creation"[\s\S]*?<\/div>/,
  )?.[0] || '';
  const executionPanel = html.match(
    /data-workspace-group-panel="execution"[\s\S]*?<\/div>/,
  )?.[0] || '';
  assert.doesNotMatch(creationPanel, /data-workspace-tab="motion-run"/);
  assert.match(executionPanel, /data-workspace-tab="motion-run"/);
  // 파일 관리가 실행 화면으로 합쳐졌으므로 별도 탭은 어디에도 없어야 한다
  assert.doesNotMatch(html, /data-workspace-tab="motion-files"/);
});

test('motion screens use workspace routes without obsolete internal tab controls', () => {
  assert.doesNotMatch(html, /id=["']motionTabs["']/);
  assert.doesNotMatch(html, /data-motion-tab=/);
  for (const panel of ['mapping', 'midi', 'run']) {
    assert.match(html, new RegExp(`data-motion-panel=["']${panel}["']`));
  }
  // 'files' 패널은 'run' 으로 합쳐졌다 · 목록과 실행이 같은 화면에 뜬다
  assert.doesNotMatch(html, /data-motion-panel=["']files["']/);
  assert.equal((html.match(/data-motion-panel="run"/g) || []).length, 1);
});

test('header status and controls share one compact row with separators', () => {
  const desktopTopbarOperations = styles.match(
    /\.topbar-operations\s*\{([^}]*)\}/,
  )?.[1] || '';
  assert.match(
    html,
    /class="topbar-operation-status"[\s\S]*class="topbar-operation-divider"[\s\S]*class="topbar-operation-buttons"/,
  );
  assert.equal((html.match(/class="topbar-operation-divider"/g) || []).length, 2);
  assert.match(desktopTopbarOperations, /align-items: center;/);
  assert.doesNotMatch(desktopTopbarOperations, /flex-direction: column;/);
});

test('motor activity uses a reserved title-row slot without vertical layout shift', () => {
  const title = html.match(/<div class="app-title">[\s\S]*?<\/div>/)?.[0] || '';
  const activityStyles = styles.match(
    /\.motor-activity-banner\s*\{([^}]*)\}/,
  )?.[1] || '';
  const hiddenActivityStyles = styles.match(
    /\.motor-activity-banner\[aria-hidden="true"\]\s*\{([^}]*)\}/,
  )?.[1] || '';

  assert.match(title, /id="motorActivityBanner"/);
  assert.match(activityStyles, /width: 190px;/);
  assert.match(activityStyles, /height: 30px;/);
  assert.match(hiddenActivityStyles, /visibility: hidden;/);
  assert.doesNotMatch(hiddenActivityStyles, /display: none;/);
});

test('settings workflows preserve action IDs and expose their defined steps', () => {
  for (const id of [
    'scanButton',
    'dynamixelScanButton',
    'saveAxisConfigButton',
    'applyAxisConfigButton',
    'addMotionIdButton',
    'generateMotionIdsButton',
    'saveMotionMappingButton',
    'resetMotionMappingButton',
  ]) {
    assert.equal(countId(id), 1, `${id} must remain unique`);
  }
  assert.match(html, /1\. 장비 검색/);
  assert.match(html, /2\. 검색 결과 확인 및 축 편집/);
  assert.match(html, /3\. 저장하고 설정 적용/);
  assert.doesNotMatch(html, /4\. 실제 시스템 적용/);
  assert.match(html, /1\. 모션축 설정 파일/);
  assert.match(html, /2\. 모션축 편집/);
  assert.match(html, /3\. 검증·미리보기·저장/);
  assert.doesNotMatch(html, /4\. 저장/);
  assert.match(styles, /\.motion-mapping-final-actions\s*\{[^}]*flex-wrap: nowrap;/s);
});
