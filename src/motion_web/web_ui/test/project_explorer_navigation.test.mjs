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

test('project transitions reset feature state only after a successful change', () => {
  assert.match(projectExplorer, /canChangeProject = \(\) => true/);
  assert.match(
    projectExplorer,
    /if \(!canChangeProject\(\)\)[\s\S]*?프로젝트 변경은 프로젝트·장비 > 시스템 정보에서만 가능합니다/,
  );
  assert.match(
    projectExplorer,
    /const changed = await run\([\s\S]*?selectProject\(projectId\)[\s\S]*?if \(changed\) await onProjectChange/,
  );
  assert.match(
    projectExplorer,
    /const created = await run\([\s\S]*?createProject\([\s\S]*?if \(created\) await onProjectChange/,
  );
});

test('failed file loads cannot reuse a previous selection or race feature navigation', () => {
  assert.match(
    projectExplorer,
    /async function openFile[\s\S]*?state\.selectedFile = null;[\s\S]*?return false;/,
  );
  assert.match(
    projectExplorer,
    /const opened = await openFile\(category, fileName\);[\s\S]*?if \(!opened \|\| !state\.selectedFile\) return false;/,
  );
  assert.match(projectExplorer, /await onOpenEditor\(result, targetWorkspace\)/);
  assert.match(main, /onOpenEditor: async \(result, requestedWorkspace = ''\)/);
  assert.match(main, /await motionData\.openProjectFile\(result\.category, result\.file_name\)/);
  assert.match(main, /if \(target === 'studio'\) await motionStudio\.refresh\(false\)/);
});

test('project.json loads automatically and remains read-only', () => {
  assert.match(
    projectExplorer,
    /async function loadProjectInfoFile\(projectId, relativePath = 'project\.json'\)/,
  );
  assert.match(
    projectExplorer,
    /async function loadProject[\s\S]*?await loadProjectInfoFile\(state\.project\?\.project_id\)/,
  );
  assert.match(projectExplorer, /el\.projectFileEditor\.readOnly = true/);
});

test('managed file actions open from the project tree popup', () => {
  assert.match(
    projectExplorer,
    /if \(event\.target\.closest\('\[data-project-manage\]'\)\)[\s\S]*?openFileActionMenu\(anchorRect\)/,
  );
  assert.match(projectExplorer, /function closeFileActionMenu\(\)/);
  assert.match(projectExplorer, /document\.addEventListener\('pointerdown'/);
  assert.doesNotMatch(projectExplorer, /projectFileSaveButton/);
});

test('project file management is visible and executable only from system information', () => {
  assert.match(main, /canManageProjectFiles: \(\) => canChangeProjectInWorkspace\(workspaceRouteState\.current\(\)\)/);
  assert.match(main, /projectExplorer\?\.syncWorkspacePermissions\(\)/);
  assert.match(
    projectExplorer,
    /isLogFile \|\| managedInFeature \|\| !fileManagementAllowed \? ''/,
  );
  assert.match(
    projectExplorer,
    /function requireFileManagementPermission\(\)[\s\S]*?프로젝트 파일 관리는 프로젝트·장비 > 시스템 정보에서만 가능합니다/,
  );
  assert.match(
    projectExplorer,
    /const disabled = !canManageProjectFiles\(\)[\s\S]*?!file \|\| state\.busy/,
  );
  assert.match(
    projectExplorer,
    /projectFileRenameButton\?\.addEventListener[\s\S]*?!requireFileManagementPermission\(\)/,
  );
  assert.match(
    projectExplorer,
    /projectFileActivateButton\?\.addEventListener[\s\S]*?!requireFileManagementPermission\(\)/,
  );
  assert.match(
    projectExplorer,
    /projectFileDeleteButton\?\.addEventListener[\s\S]*?!requireFileManagementPermission\(\)/,
  );
});

test('motion file transfer is not exposed from the project tree', () => {
  assert.doesNotMatch(projectExplorer, /data-project-add-layer/);
  assert.doesNotMatch(projectExplorer, /onAddMotionLayer/);
  assert.doesNotMatch(projectExplorer, /스튜디오 레이어로 추가/);
  assert.match(projectExplorer, /const managedInFeature = file\.category === 'motions'/);
  assert.match(
    projectExplorer,
    /isLogFile \|\| managedInFeature \|\| !fileManagementAllowed \? ''/,
  );
});

test('project explorer hides recoverable trash without deleting backend data', () => {
  assert.match(
    projectExplorer,
    /const visibleTree = state\.tree\.filter\(\(folder\) => folder\.category !== 'trash'\)/,
  );
  assert.match(projectExplorer, /const totalFiles = visibleTree\.reduce/);
  assert.match(projectExplorer, /const folders = visibleTree\.map/);
  assert.doesNotMatch(projectExplorer, /trash: \{ icon:/);
});

test('runtime clear button and delete-blocked popup guide the stop-clear-delete path', () => {
  const html = readFileSync(new URL('../static/index.html', import.meta.url), 'utf8');
  const api = readFileSync(new URL('../static/js/api.js', import.meta.url), 'utf8');
  assert.match(html, /id="clearMotorRuntimeButton"[^>]*>실행 적용 해제</);
  assert.match(api, /clearMotorRuntimeApplication[\s\S]*?\/api\/system\/motor-runtime\/clear/);
  assert.match(projectExplorer, /clearMotorRuntimeApplication/);
  assert.match(projectExplorer, /clearMotorRuntimeButton\.disabled = state\.busy \|\| !hasRuntime/);
  assert.match(
    projectExplorer,
    /1\. 「전체 동작 정지」를 실행합니다\.[\s\S]*2\. 「실행 적용 해제」를 실행합니다\.[\s\S]*3\. 이 프로젝트를 다시 삭제합니다\./,
  );
});
