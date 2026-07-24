import assert from 'node:assert/strict';
import test from 'node:test';

import {
  defaultWorkspaceForGroup,
  motionTabForWorkspace,
  normalizeWorkspaceRoute,
  workspaceForLegacyNavigation,
  workspaceGroupFor,
  workspacePanelFor,
} from '../static/js/workspace_navigation.js';

test('workspace routes resolve their group and shared motion panel', () => {
  assert.equal(workspaceGroupFor('monitoring'), 'operations');
  assert.equal(workspaceGroupFor('config'), 'setup');
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
  assert.equal(workspaceForLegacyNavigation('motion', 'unknown'), 'motion-files');
  assert.equal(workspaceForLegacyNavigation('config'), 'config');
});
