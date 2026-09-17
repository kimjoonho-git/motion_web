const PROJECT_GENERATION_KEY = '__motionProjectGeneration';

/** 서버가 앞서 갔을 때 알릴 곳 · §6-140
 *
 * 모터축 설정을 적용하면 프로젝트 세대가 오른다 · 그 순간 열려 있던 화면은
 * 옛 세대를 들고 있어서, 그다음 요청의 응답이 전부 「이전 프로젝트의 늦은
 * 응답」으로 버려졌다 · 버리는 쪽은 조용히 `return` 만 해서 화면이 빈칸으로
 * 굳었다 · 「재생 등록된 파일 없음」이 그렇게 나왔다. 데이터는 멀쩡했다.
 *
 * 늦은 응답(세대가 **뒤진** 응답)은 버리는 게 맞다 · 그러나 세대가 **앞선**
 * 응답은 버릴 것이 아니라 따라가야 할 신호다.
 */
let projectAheadHandler = null;

export function setProjectAheadHandler(handler) {
  projectAheadHandler = typeof handler === 'function' ? handler : null;
}

export function setProjectGeneration(value) {
  const parsed = Number(value);
  if (Number.isInteger(parsed) && parsed >= 0) {
    window[PROJECT_GENERATION_KEY] = parsed;
  }
}

export function getProjectGeneration() {
  const value = Number(window[PROJECT_GENERATION_KEY]);
  return Number.isInteger(value) && value >= 0 ? value : null;
}

async function projectFetch(input, options = {}) {
  const expectedGeneration = getProjectGeneration();
  const { timeoutMs = 0, ...requestOptions } = options;
  const headers = new Headers(requestOptions.headers || {});
  if (expectedGeneration !== null) {
    headers.set('X-Project-Generation', String(expectedGeneration));
  }
  const timeout = Number(timeoutMs);
  const controller = Number.isFinite(timeout) && timeout > 0 && !requestOptions.signal
    ? new AbortController()
    : null;
  const timer = controller
    ? window.setTimeout(() => controller.abort(), timeout)
    : null;
  try {
    const response = await window.fetch(input, {
      ...requestOptions,
      headers,
      signal: controller?.signal || requestOptions.signal,
    });
    response.projectGenerationExpected = expectedGeneration;
    return response;
  } catch (error) {
    if (controller?.signal.aborted) {
      throw new Error(`상태 응답 시간 초과 · ${Math.round(timeout / 1000)}초`);
    }
    throw error;
  } finally {
    if (timer !== null) window.clearTimeout(timer);
  }
}

async function readJson(response) {
  let payload = null;
  try {
    payload = await response.json();
  } catch (error) {
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }
    throw error;
  }
  const expected = response.projectGenerationExpected;
  const headerGeneration = Number(response.headers.get('X-Project-Generation'));
  const payloadGeneration = Number(payload?.project_generation);
  const responseGeneration = Number.isInteger(payloadGeneration)
    ? payloadGeneration
    : (Number.isInteger(headerGeneration) ? headerGeneration : null);
  const previousGeneration = Number(payload?.previous_project_generation);
  const transition = Number.isInteger(expected)
    && Number.isInteger(previousGeneration)
    && previousGeneration === expected
    && Number.isInteger(payloadGeneration)
    && payloadGeneration > expected;
  const externalBoundary = response.status === 409
    && payload?.stale_project_generation === true
    && Number.isInteger(expected)
    && Number.isInteger(responseGeneration)
    && responseGeneration > expected;
  if (externalBoundary) {
    setProjectGeneration(responseGeneration);
    const error = new Error(payload?.message || '프로젝트가 다른 브라우저에서 변경되었습니다');
    error.projectBoundaryGeneration = responseGeneration;
    throw error;
  }
  if (
    !transition
    && Number.isInteger(expected)
    && Number.isInteger(responseGeneration)
    && responseGeneration > expected
  ) {
    // 서버가 앞서 갔다 · 버릴 것이 아니라 따라가야 한다 · §6-140
    setProjectGeneration(responseGeneration);
    if (projectAheadHandler) projectAheadHandler(responseGeneration);
    const error = new Error('프로젝트 설정이 바뀌어 다시 읽습니다');
    error.staleProjectResponse = true;
    error.projectMovedAhead = responseGeneration;
    throw error;
  }
  if (
    !transition
    && Number.isInteger(expected)
    && (
      (Number.isInteger(getProjectGeneration()) && expected !== getProjectGeneration())
      || (Number.isInteger(responseGeneration) && responseGeneration !== expected)
    )
  ) {
    const error = new Error('이전 프로젝트의 늦은 응답을 폐기했습니다');
    error.staleProjectResponse = true;
    throw error;
  }
  if (transition || getProjectGeneration() === null) {
    setProjectGeneration(responseGeneration);
  }
  if (!response.ok) {
    const detail = payload?.message || payload?.detail || `HTTP ${response.status}`;
    throw new Error(detail);
  }
  return payload;
}


export const fetchStatusSnapshot = (timeoutMs = 5000) =>
  request('GET', '/api/status', { timeoutMs });

export const fetchSystemVersion = () => request('GET', '/api/system/version');

export const fetchCoordinationStatus = () => request('GET', '/api/coordination');

export const saveCoordinationSettings = (payload) => request('PUT', '/api/coordination/settings', { body: payload });

export const sendCoordinationControl = (payload) =>
  request('POST', '/api/coordination/control', { body: payload, timeoutMs: 7000 });

export const fetchServoAlarmPolicy = () => request('GET', '/api/servo-alarm-policy');

export const saveServoAlarmPolicy = (overrides) => request('PUT', '/api/servo-alarm-policy', { body: { overrides } });

export const restartManagedProgram = () => request('POST', '/api/system/program/restart');

export const createDesktopShortcut = () => request('POST', '/api/system/desktop-shortcut');

export const restartMotorControlSystem = () => request('POST', '/api/system/motor-control/restart');

export const clearMotorRuntimeApplication = () => request('POST', '/api/system/motor-runtime/clear');

/** 경로와 메서드만 다른 요청을 한 곳으로 모은다.
 *
 * 74개 함수가 거의 같은 여섯 줄을 반복하고 있었다 · 봉투가 같으니 표로 쓰면
 * 어떤 화면이 어느 엔드포인트를 쓰는지 한눈에 보인다. 이 파일 안에 이미
 * `motionStudioRequest` 로 같은 꼴이 있었다 · 그 관례를 파일 전체로 넓힌다.
 */
async function request(method, path, { body, timeoutMs } = {}) {
  const options = { method };
  if (body !== undefined) {
    options.headers = { 'Content-Type': 'application/json' };
    options.body = JSON.stringify(body);
  }
  if (timeoutMs !== undefined) options.timeoutMs = timeoutMs;
  return readJson(await projectFetch(path, options));
}


async function motionStudioRequest(path = '', method = 'GET', payload = null) {
  const options = { method };
  if (payload !== null) {
    options.headers = { 'Content-Type': 'application/json' };
    options.body = JSON.stringify(payload);
  }
  const response = await projectFetch(`/api/motion-studio${path}`, options);
  return readJson(response);
}

export const fetchMotionStudio = () => motionStudioRequest();
export const importMotionStudioFile = (payload) => motionStudioRequest('/import', 'POST', payload);
export const saveMotionStudioProject = (payload) => motionStudioRequest('/project', 'PUT', payload);
export const createMotionStudioLayer = (payload = {}) => motionStudioRequest('/layers', 'POST', payload);
export const updateMotionStudioLayer = (payload) => motionStudioRequest('/layers', 'PUT', payload);
export const saveMotionStudioLayerData = (payload) => motionStudioRequest('/layers/data', 'PUT', payload);
export const deleteMotionStudioLayer = (layerId) => motionStudioRequest(`/layers/${encodeURIComponent(layerId)}`, 'DELETE');
export const duplicateMotionStudioLayer = (layerId) => motionStudioRequest(`/layers/${encodeURIComponent(layerId)}/duplicate`, 'POST');
export const editMotionStudioLayer = (payload) => motionStudioRequest('/editor/transform', 'POST', payload);
export const previewMotionStudioMerge = (payload) => motionStudioRequest('/editor/merge-preview', 'POST', payload);
export const commitMotionStudioMerge = (payload) => motionStudioRequest('/layers/merge', 'POST', payload);
export const startMotionStudioRecord = (payload) => motionStudioRequest('/record', 'POST', payload);
export const startMotionStudioInitialization = (payload) => motionStudioRequest('/initialize', 'POST', payload);
export const startMotionStudioPlayback = (payload) => motionStudioRequest('/play', 'POST', payload);
export const stopMotionStudio = () => motionStudioRequest('/stop', 'POST');
export const exportMotionStudio = (fileId) => motionStudioRequest('/export', 'POST', { file_id: fileId });

export async function fetchMotorEvents(category = 'all', limit = 300, fileName = 'all') {
  const query = new URLSearchParams({
    category: String(category || 'all'),
    limit: String(limit),
    file_name: String(fileName || 'all'),
  });
  const response = await projectFetch(`/api/motor-events?${query.toString()}`);
  return readJson(response);
}

export const clearMotorEvents = () => request('DELETE', '/api/motor-events');

export const deleteMotorEventLogFile = (fileName) =>
  request('DELETE', `/api/motor-events/files/${encodeURIComponent(fileName)}`);

export const setMonitoringEnabled = (enabled) => request('POST', '/api/monitoring/enabled', { body: { enabled } });

export const requestMotorScan = () => request('POST', '/api/motors/scan');

export const requestAcServoScan = () => request('POST', '/api/motors/scan/ac-servo');

export const requestDynamixelScan = () => request('POST', '/api/motors/scan/dynamixel');

export const fetchMotorScanProgress = () => request('GET', '/api/motors/scan/progress');

export const writeEthercatAlias = (payload) => request('POST', '/api/motors/ethercat-alias', { body: payload });

export const fetchMotorConfig = () => request('GET', '/api/motor-config');

export const saveMotorConfig = (payload) => request('PUT', '/api/motor-config', { body: payload });

export const deleteMotorConfig = () => request('DELETE', '/api/motor-config');

export const applyMotorConfig = () => request('POST', '/api/motor-config/apply');

export const fetchProjects = () => request('GET', '/api/projects');

export const createProject = (payload) => request('POST', '/api/projects', { body: payload });

export const deleteProject = (projectId) => request('DELETE', `/api/projects/${encodeURIComponent(projectId)}`);


export const fetchProject = (projectId) => request('GET', `/api/projects/${encodeURIComponent(projectId)}`);

export const selectProject = (projectId) => request('POST', `/api/projects/${encodeURIComponent(projectId)}/select`);

export const saveProjectMemo = (projectId, memo) =>
  request('PATCH', `/api/projects/${encodeURIComponent(projectId)}`, { body: { memo } });

function projectFileUrl(projectId, category, fileName) {
  return `/api/projects/${encodeURIComponent(projectId)}/files/${encodeURIComponent(category)}/${encodeURIComponent(fileName)}`;
}

export const importProjectFile = (projectId, payload) =>
  request('POST', `/api/projects/${encodeURIComponent(projectId)}/files`, { body: payload });

export const fetchProjectFile = (projectId, category, fileName) =>
  request('GET', projectFileUrl(projectId, category, fileName));

export async function fetchReadOnlyProjectFile(projectId, relativePath) {
  const query = new URLSearchParams({ relative_path: relativePath });
  const response = await projectFetch(
    `/api/projects/${encodeURIComponent(projectId)}/tree-file?${query.toString()}`,
  );
  return readJson(response);
}

export const renameProjectFile = (projectId, category, fileName, newName) =>
  request('POST', `${projectFileUrl(projectId, category, fileName)}/rename`, { body: { new_name: newName } });

export const activateProjectFile = (projectId, category, fileName) =>
  request('POST', `${projectFileUrl(projectId, category, fileName)}/active`);

export const openProjectFileEditor = (projectId, category, fileName) =>
  request('POST', `${projectFileUrl(projectId, category, fileName)}/open-editor`);

export const deleteProjectFile = (projectId, category, fileName) =>
  request('DELETE', projectFileUrl(projectId, category, fileName));

export function projectFileDownloadUrl(projectId, category, fileName) {
  return `${projectFileUrl(projectId, category, fileName)}/download`;
}

export const fetchMotionFiles = () => request('GET', '/api/motion-files');

export const fetchMotionFile = (fileId) => request('GET', `/api/motion-files/${encodeURIComponent(fileId)}`);

export const deleteMotionFile = (fileId) => request('DELETE', `/api/motion-files/${encodeURIComponent(fileId)}`);

export const fetchMotionMappings = () => request('GET', '/api/motion-mappings');

export const fetchMotionMapping = (fileId) => request('GET', `/api/motion-mappings/${encodeURIComponent(fileId)}`);

export const saveMotionMapping = (payload) => request('POST', '/api/motion-mappings', { body: payload });

export const validateMotionMapping = (payload) => request('POST', '/api/motion-mappings/validate', { body: payload });

export const deleteMotionMapping = (fileId) =>
  request('DELETE', `/api/motion-mappings/${encodeURIComponent(fileId)}`);

export const fetchMotionRunStatus = () => request('GET', '/api/motion-run/status');

export const checkMotionRun = (payload) => request('POST', '/api/motion-run/check', { body: payload });

export const initializeMotionRun = (payload) => request('POST', '/api/motion-run/initialize', { body: payload });

export const startMotionRun = (payload) => request('POST', '/api/motion-run/start', { body: payload });

export const configureMotionAutomation = (payload) => request('PUT', '/api/motion-run/automation', { body: payload });


export const stopMotionRun = () => request('POST', '/api/motion-run/stop');

export const stopMotionRunAfterCycle = () => request('POST', '/api/motion-run/stop-after-cycle');

export const requestMotionSafetyStop = () => request('POST', '/api/safety/motion-stop');

export const requestEmergencySafetyStop = () => request('POST', '/api/safety/emergency-stop');

export const fetchMidiMonitor = () => request('GET', '/api/midi-monitor');


export const createMidiBank = (payload = {}) => request('POST', '/api/midi-monitor/banks', { body: payload });

export const selectMidiBank = (bankId) =>
  request('POST', `/api/midi-monitor/banks/${encodeURIComponent(bankId)}/select`);

export const updateMidiBank = (bankId, payload) =>
  request('PUT', `/api/midi-monitor/banks/${encodeURIComponent(bankId)}`, { body: payload });

export const deleteMidiBank = (bankId) => request('DELETE', `/api/midi-monitor/banks/${encodeURIComponent(bankId)}`);

export const loadMidiBanksFromFile = () => request('POST', '/api/midi-monitor/banks/file/load');

export const resetMidiRuntimeValues = () => request('POST', '/api/midi-monitor/runtime/reset');

export const connectMidiDevice = () => request('POST', '/api/midi-monitor/device/connect');

export const disconnectMidiDevice = () => request('POST', '/api/midi-monitor/device/disconnect');

export const requestAcServoJog = (payload) => request('POST', '/api/motion-test/ac-servo/jog', { body: payload });

export const requestDynamixelJog = (payload) => request('POST', '/api/motion-test/dynamixel/jog', { body: payload });

export const requestAcServoAction = (payload) =>
  request('POST', '/api/motion-test/ac-servo/action', { body: payload });

export const requestDynamixelAction = (payload) =>
  request('POST', '/api/motion-test/dynamixel/action', { body: payload });

export const requestAcServoControl = (payload) =>
  request('POST', '/api/motion-test/ac-servo/control', { body: payload });
