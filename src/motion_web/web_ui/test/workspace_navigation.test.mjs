import assert from 'node:assert/strict';
import test from 'node:test';

import {
  canChangeProjectInWorkspace,
  createWorkspaceRouteState,
  defaultWorkspaceForGroup,
  motionTabForWorkspace,
  normalizeWorkspaceRoute,
  workspaceForLegacyNavigation,
  workspaceForProjectCategory,
  workspaceGroupFor,
  workspacePanelFor,
  WORKSPACE_GROUPS,
} from '../static/js/workspace_navigation.js';

test('project selection is allowed only in project equipment system information', () => {
  const routes = Object.values(WORKSPACE_GROUPS).flat();
  for (const route of routes) {
    assert.equal(
      canChangeProjectInWorkspace(route),
      route === 'system',
      `${route} 프로젝트 변경 허용 여부`,
    );
  }
});

test('workspace routes resolve their group and shared motion panel', () => {
  assert.equal(workspaceGroupFor('monitoring'), 'operations');
  assert.equal(workspaceGroupFor('config'), 'setup');
  assert.equal(workspaceGroupFor('servo-errors'), 'operations');
  assert.equal(workspaceGroupFor('motion-midi'), 'creation');
  assert.equal(workspaceGroupFor('motion-run'), 'execution');
  assert.equal(workspacePanelFor('motion-run'), 'motion');
  // 파일 관리는 모션 실행 화면으로 합쳐졌다 · 옛 경로는 더 이상 없다
  assert.equal(normalizeWorkspaceRoute('motion-files'), 'monitoring');
  assert.equal(workspacePanelFor('studio'), 'studio');
  assert.equal(motionTabForWorkspace('motion-mapping'), 'mapping');
});

test('workspace defaults and legacy motion navigation are deterministic', () => {
  assert.equal(defaultWorkspaceForGroup('creation'), 'studio');
  assert.equal(defaultWorkspaceForGroup('unknown'), 'monitoring');
  assert.equal(normalizeWorkspaceRoute('unknown'), 'monitoring');
  assert.equal(workspaceForLegacyNavigation('motion', 'midi'), 'motion-midi');
  assert.equal(workspaceForLegacyNavigation('motion', 'unknown'), 'motion-run');
  // 옛 'files' 탭 요청도 통합된 실행 화면으로 보낸다 · 북마크·탐색기 대비
  assert.equal(workspaceForLegacyNavigation('motion', 'files'), 'motion-run');
  assert.equal(workspaceForLegacyNavigation('config'), 'config');
});

test('every real workspace route survives navigation unchanged', () => {
  // `'project'`가 옛 이름 목록에 남아 있어서, 프로젝트 관리 탭을 누를 때마다
  // `motion-run`으로 튕겨 나갔다 · `onManageFile`의 편집기 스크롤도 빗나갔다.
  // 옛 이름으로 다룰 수 있는 것은 `'motion'` 하나뿐이다.
  for (const route of Object.values(WORKSPACE_GROUPS).flat()) {
    assert.equal(
      workspaceForLegacyNavigation(route),
      route,
      `${route} 탭이 제자리로 가야 한다`,
    );
  }
});

test('project categories navigate directly to their feature screen', () => {
  assert.equal(workspaceForProjectCategory('motor_axes'), 'config');
  assert.equal(workspaceForProjectCategory('motion_axis_matching'), 'motion-mapping');
  assert.equal(workspaceForProjectCategory('motions'), 'motion-run');
  assert.equal(workspaceForProjectCategory('layers'), 'studio');
  assert.equal(workspaceForProjectCategory('logs'), 'log');
});

test('workspace route state remembers the last screen in each group', () => {
  const state = createWorkspaceRouteState();
  state.select('motion-mapping');
  state.select('log');
  assert.equal(state.current(), 'log');
  assert.equal(state.forGroup('creation'), 'motion-mapping');
  assert.equal(state.forGroup('operations'), 'log');
  assert.equal(state.forGroup('execution'), 'manual');
});
