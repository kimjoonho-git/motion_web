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
} from '../static/js/workspace_navigation.js';

test('project selection is allowed only in project equipment system information', () => {
  assert.equal(canChangeProjectInWorkspace('system'), true);
  assert.equal(canChangeProjectInWorkspace('config'), false);
  assert.equal(canChangeProjectInWorkspace('servo-errors'), false);
  assert.equal(canChangeProjectInWorkspace('monitoring'), false);
  assert.equal(canChangeProjectInWorkspace('motion-files'), false);
  assert.equal(canChangeProjectInWorkspace('manual'), false);
});

test('workspace routes resolve their group and shared motion panel', () => {
  assert.equal(workspaceGroupFor('monitoring'), 'operations');
  assert.equal(workspaceGroupFor('config'), 'setup');
  assert.equal(workspaceGroupFor('servo-errors'), 'setup');
  assert.equal(workspaceGroupFor('motion-midi'), 'creation');
  assert.equal(workspaceGroupFor('motion-run'), 'execution');
  assert.equal(workspacePanelFor('motion-files'), 'motion');
  assert.equal(workspacePanelFor('studio'), 'studio');
  assert.equal(motionTabForWorkspace('motion-mapping'), 'mapping');
});

test('workspace defaults and legacy motion navigation are deterministic', () => {
  assert.equal(defaultWorkspaceForGroup('creation'), 'motion-files');
  assert.equal(defaultWorkspaceForGroup('unknown'), 'monitoring');
  assert.equal(normalizeWorkspaceRoute('unknown'), 'monitoring');
  assert.equal(workspaceForLegacyNavigation('motion', 'midi'), 'motion-midi');
  assert.equal(workspaceForLegacyNavigation('project', 'mapping'), 'motion-mapping');
  assert.equal(workspaceForLegacyNavigation('motion', 'unknown'), 'motion-files');
  assert.equal(workspaceForLegacyNavigation('config'), 'config');
});

test('project categories navigate directly to their feature screen', () => {
  assert.equal(workspaceForProjectCategory('motor_axes'), 'config');
  assert.equal(workspaceForProjectCategory('motion_axis_matching'), 'motion-mapping');
  assert.equal(workspaceForProjectCategory('motions'), 'motion-files');
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
