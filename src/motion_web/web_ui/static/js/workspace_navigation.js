export const WORKSPACE_GROUPS = Object.freeze({
  operations: Object.freeze(['monitoring', 'log']),
  setup: Object.freeze(['system', 'config']),
  creation: Object.freeze([
    'motion-files',
    'motion-mapping',
    'motion-midi',
    'studio',
  ]),
  execution: Object.freeze(['manual', 'motion-run']),
});

export const WORKSPACE_DEFAULTS = Object.freeze({
  operations: 'monitoring',
  setup: 'system',
  creation: 'motion-files',
  execution: 'manual',
});

export const MOTION_WORKSPACE_TABS = Object.freeze({
  'motion-files': 'files',
  'motion-mapping': 'mapping',
  'motion-midi': 'midi',
  'motion-run': 'run',
});

export const MOTION_WORKSPACE_DETAILS = Object.freeze({
  'motion-files': Object.freeze([
    '모션 파일',
    '현재 프로젝트의 모션 데이터를 확인하고 분석합니다',
  ]),
  'motion-mapping': Object.freeze([
    '모션축 설정',
    '모션 ID를 프로젝트 모터축에 연결하고 실행 변환값을 설정합니다',
  ]),
  'motion-midi': Object.freeze([
    'MIDI 입력 설정',
    'MIDI 연결, 뱅크 및 채널별 모션 입력을 설정합니다',
  ]),
  'motion-run': Object.freeze([
    '모션 실행',
    '초기 위치 이동과 모션 재생 상태를 확인하고 제어합니다',
  ]),
});

const WORKSPACE_ROUTES = new Set(Object.values(WORKSPACE_GROUPS).flat());

export function normalizeWorkspaceRoute(route) {
  const value = String(route || '').trim();
  return WORKSPACE_ROUTES.has(value) ? value : WORKSPACE_DEFAULTS.operations;
}

export function workspaceGroupFor(route) {
  const target = normalizeWorkspaceRoute(route);
  return Object.entries(WORKSPACE_GROUPS)
    .find(([, routes]) => routes.includes(target))?.[0] || 'operations';
}

export function workspacePanelFor(route) {
  const target = normalizeWorkspaceRoute(route);
  return MOTION_WORKSPACE_TABS[target] ? 'motion' : target;
}

export function motionTabForWorkspace(route) {
  return MOTION_WORKSPACE_TABS[normalizeWorkspaceRoute(route)] || '';
}

export function defaultWorkspaceForGroup(group) {
  return WORKSPACE_DEFAULTS[String(group || '')] || WORKSPACE_DEFAULTS.operations;
}

export function workspaceForLegacyNavigation(workspace, motionTab = '') {
  if (workspace !== 'motion') return normalizeWorkspaceRoute(workspace);
  const tab = String(motionTab || 'files');
  return Object.entries(MOTION_WORKSPACE_TABS)
    .find(([, value]) => value === tab)?.[0] || 'motion-files';
}
