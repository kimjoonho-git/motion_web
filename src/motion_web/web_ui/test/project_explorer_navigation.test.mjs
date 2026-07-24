import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const main = readFileSync(new URL('../static/js/main.js', import.meta.url), 'utf8');
const motionData = readFileSync(new URL('../static/js/motion_data.js', import.meta.url), 'utf8');
const projectExplorer = readFileSync(
  new URL('../static/js/project_explorer.js', import.meta.url),
  'utf8',
);

test('project explorer results use category routes instead of backend workspace names', () => {
  assert.match(main, /workspaceForProjectCategory\(/);
  assert.match(main, /\['motions', 'motion_axis_matching'\]\.includes\(result\.category\)/);
  assert.match(main, /motionData\.openProjectFile\(result\.category, result\.file_name\)/);
});

test('MIDI bank entry opens its mapping and then navigates to MIDI input', () => {
  assert.match(projectExplorer, /data-project-open-midi/);
  assert.match(projectExplorer, /onNavigate\('motion-midi'\)/);
  assert.match(projectExplorer, /openInFeature\(category, fileName, 'motion-midi'\)/);
  assert.match(projectExplorer, /onOpenEditor\(result, targetWorkspace\)/);
  assert.match(main, /requestedWorkspace \|\| workspaceForProjectCategory/);
});

test('motion controller owns active panel state without hidden DOM tabs', () => {
  assert.match(motionData, /let activeMotionPanel = 'files'/);
  assert.match(motionData, /panel\.dataset\.motionPanel !== activeMotionPanel/);
  assert.doesNotMatch(motionData, /el\.motionTabs/);
});
