export const WORKSPACE_GROUPS = Object.freeze({
  operations: Object.freeze(['monitoring', 'servo-errors', 'log', 'btop']),
  setup: Object.freeze(['system', 'project', 'config']),
  creation: Object.freeze([
    'motion-mapping',
    'motion-midi',
    'studio',
  ]),
  execution: Object.freeze(['manual', 'motion-run', 'coordination']),
});

const WORKSPACE_DEFAULTS = Object.freeze({
  operations: 'monitoring',
  setup: 'system',
  creation: 'studio',
  execution: 'manual',
});

const MOTION_WORKSPACE_TABS = Object.freeze({
  'motion-mapping': 'mapping',
  'motion-midi': 'midi',
  'motion-run': 'run',
});

export const MOTION_WORKSPACE_DETAILS = Object.freeze({
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
    '모션 파일을 고르고 재생 등록한 뒤 초기 위치 이동과 재생을 제어합니다',
  ]),
});

const WORKSPACE_ROUTES = new Set(Object.values(WORKSPACE_GROUPS).flat());
const PROJECT_SELECTION_WORKSPACE = 'system';

export function normalizeWorkspaceRoute(route) {
  const value = String(route || '').trim();
  return WORKSPACE_ROUTES.has(value) ? value : WORKSPACE_DEFAULTS.operations;
}

export function canChangeProjectInWorkspace(route) {
  return normalizeWorkspaceRoute(route) === PROJECT_SELECTION_WORKSPACE;
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
  if (!['motion', 'project'].includes(workspace)) return normalizeWorkspaceRoute(workspace);
  // 파일 관리는 모션 실행 화면으로 합쳐졌다 · 옛 'files' 요청도 그리로 보낸다
  const tab = String(motionTab || 'run') === 'files' ? 'run' : String(motionTab || 'run');
  return Object.entries(MOTION_WORKSPACE_TABS)
    .find(([, value]) => value === tab)?.[0] || 'motion-run';
}

export function workspaceForProjectCategory(
  category,
  fallbackWorkspace = 'monitoring',
  motionTab = '',
) {
  const routes = {
    motor_axes: 'config',
    motion_axis_matching: 'motion-mapping',
    motions: 'motion-run',
    layers: 'studio',
    logs: 'log',
  };
  return routes[String(category || '')]
    || workspaceForLegacyNavigation(fallbackWorkspace, motionTab);
}

export function createWorkspaceRouteState(initialRoute = WORKSPACE_DEFAULTS.operations) {
  let activeRoute = normalizeWorkspaceRoute(initialRoute);
  const lastByGroup = { ...WORKSPACE_DEFAULTS };
  lastByGroup[workspaceGroupFor(activeRoute)] = activeRoute;
  return {
    current: () => activeRoute,
    select: (route) => {
      activeRoute = normalizeWorkspaceRoute(route);
      lastByGroup[workspaceGroupFor(activeRoute)] = activeRoute;
      return activeRoute;
    },
    forGroup: (group) => (
      lastByGroup[String(group || '')] || defaultWorkspaceForGroup(group)
    ),
  };
}
