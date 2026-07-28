const PROJECT_GENERATION_KEY = '__motionProjectGeneration';

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

export async function fetchStatusSnapshot(timeoutMs = 5000) {
  const response = await projectFetch('/api/status', { timeoutMs });
  return readJson(response);
}

export async function fetchServoAlarmPolicy() {
  const response = await projectFetch('/api/servo-alarm-policy');
  return readJson(response);
}

export async function saveServoAlarmPolicy(overrides) {
  const response = await projectFetch('/api/servo-alarm-policy', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ overrides }),
  });
  return readJson(response);
}

export async function restartManagedProgram() {
  const response = await projectFetch('/api/system/program/restart', { method: 'POST' });
  return readJson(response);
}

export async function createDesktopShortcut() {
  const response = await projectFetch('/api/system/desktop-shortcut', { method: 'POST' });
  return readJson(response);
}

export async function restartMotorControlSystem() {
  const response = await projectFetch('/api/system/motor-control/restart', { method: 'POST' });
  return readJson(response);
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
export const createMotionStudioProject = (payload) => motionStudioRequest('/projects', 'POST', payload);
export const loadMotionStudioProject = (projectId) => motionStudioRequest('/projects/load', 'POST', { project_id: projectId });
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

export async function clearMotorEvents() {
  const response = await projectFetch('/api/motor-events', { method: 'DELETE' });
  return readJson(response);
}

export async function deleteMotorEventLogFile(fileName) {
  const response = await projectFetch(
    `/api/motor-events/files/${encodeURIComponent(fileName)}`,
    { method: 'DELETE' },
  );
  return readJson(response);
}

export async function setMonitoringEnabled(enabled) {
  const response = await projectFetch('/api/monitoring/enabled', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ enabled }),
  });
  return readJson(response);
}

export async function requestMotorScan() {
  const response = await projectFetch('/api/motors/scan', { method: 'POST' });
  return readJson(response);
}

export async function requestAcServoScan() {
  const response = await projectFetch('/api/motors/scan/ac-servo', { method: 'POST' });
  return readJson(response);
}

export async function requestDynamixelScan() {
  const response = await projectFetch('/api/motors/scan/dynamixel', { method: 'POST' });
  return readJson(response);
}

export async function fetchMotorScanProgress() {
  const response = await projectFetch('/api/motors/scan/progress');
  return readJson(response);
}

export async function writeEthercatAlias(payload) {
  const response = await projectFetch('/api/motors/ethercat-alias', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  return readJson(response);
}

export async function fetchMotorConfig() {
  const response = await projectFetch('/api/motor-config');
  return readJson(response);
}

export async function saveMotorConfig(payload) {
  const response = await projectFetch('/api/motor-config', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  return readJson(response);
}

export async function deleteMotorConfig() {
  const response = await projectFetch('/api/motor-config', { method: 'DELETE' });
  return readJson(response);
}

export async function applyMotorConfig() {
  const response = await projectFetch('/api/motor-config/apply', { method: 'POST' });
  return readJson(response);
}

export async function fetchProjects() {
  const response = await projectFetch('/api/projects');
  return readJson(response);
}

export async function createProject(payload) {
  const response = await projectFetch('/api/projects', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  return readJson(response);
}

export async function deleteProject(projectId) {
  const response = await projectFetch(`/api/projects/${encodeURIComponent(projectId)}`, {
    method: 'DELETE',
  });
  return readJson(response);
}

export async function copyProjectFile(projectId, payload) {
  const response = await projectFetch(`/api/projects/${encodeURIComponent(projectId)}/copy-file`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  return readJson(response);
}

export async function fetchProject(projectId) {
  const response = await projectFetch(`/api/projects/${encodeURIComponent(projectId)}`);
  return readJson(response);
}

export async function selectProject(projectId) {
  const response = await projectFetch(`/api/projects/${encodeURIComponent(projectId)}/select`, {
    method: 'POST',
  });
  return readJson(response);
}

export async function saveProjectMemo(projectId, memo) {
  const response = await projectFetch(`/api/projects/${encodeURIComponent(projectId)}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ memo }),
  });
  return readJson(response);
}

function projectFileUrl(projectId, category, fileName) {
  return `/api/projects/${encodeURIComponent(projectId)}/files/${encodeURIComponent(category)}/${encodeURIComponent(fileName)}`;
}

export async function importProjectFile(projectId, payload) {
  const response = await projectFetch(`/api/projects/${encodeURIComponent(projectId)}/files`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  return readJson(response);
}

export async function fetchProjectFile(projectId, category, fileName) {
  const response = await projectFetch(projectFileUrl(projectId, category, fileName));
  return readJson(response);
}

export async function fetchReadOnlyProjectFile(projectId, relativePath) {
  const query = new URLSearchParams({ relative_path: relativePath });
  const response = await projectFetch(
    `/api/projects/${encodeURIComponent(projectId)}/tree-file?${query.toString()}`,
  );
  return readJson(response);
}

export async function saveProjectFile(projectId, category, fileName, content) {
  const response = await projectFetch(projectFileUrl(projectId, category, fileName), {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ content }),
  });
  return readJson(response);
}

export async function renameProjectFile(projectId, category, fileName, newName) {
  const response = await projectFetch(`${projectFileUrl(projectId, category, fileName)}/rename`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ new_name: newName }),
  });
  return readJson(response);
}

export async function activateProjectFile(projectId, category, fileName) {
  const response = await projectFetch(`${projectFileUrl(projectId, category, fileName)}/active`, {
    method: 'POST',
  });
  return readJson(response);
}

export async function openProjectFileEditor(projectId, category, fileName) {
  const response = await projectFetch(`${projectFileUrl(projectId, category, fileName)}/open-editor`, {
    method: 'POST',
  });
  return readJson(response);
}

export async function deleteProjectFile(projectId, category, fileName) {
  const response = await projectFetch(projectFileUrl(projectId, category, fileName), {
    method: 'DELETE',
  });
  return readJson(response);
}

export function projectFileDownloadUrl(projectId, category, fileName) {
  return `${projectFileUrl(projectId, category, fileName)}/download`;
}

export async function fetchMotionFiles() {
  const response = await projectFetch('/api/motion-files');
  return readJson(response);
}

export async function fetchMotionFile(fileId) {
  const response = await projectFetch(`/api/motion-files/${encodeURIComponent(fileId)}`);
  return readJson(response);
}

export async function uploadMotionFile(payload) {
  const response = await projectFetch('/api/motion-files/upload', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  return readJson(response);
}

export async function deleteMotionFile(fileId) {
  const response = await projectFetch(`/api/motion-files/${encodeURIComponent(fileId)}`, {
    method: 'DELETE',
  });
  return readJson(response);
}

export async function fetchMotionMappings() {
  const response = await projectFetch('/api/motion-mappings');
  return readJson(response);
}

export async function fetchMotionMapping(fileId) {
  const response = await projectFetch(`/api/motion-mappings/${encodeURIComponent(fileId)}`);
  return readJson(response);
}

export async function saveMotionMapping(payload) {
  const response = await projectFetch('/api/motion-mappings', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  return readJson(response);
}

export async function validateMotionMapping(payload) {
  const response = await projectFetch('/api/motion-mappings/validate', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  return readJson(response);
}

export async function deleteMotionMapping(fileId) {
  const response = await projectFetch(`/api/motion-mappings/${encodeURIComponent(fileId)}`, {
    method: 'DELETE',
  });
  return readJson(response);
}

export async function fetchMotionRunStatus() {
  const response = await projectFetch('/api/motion-run/status');
  return readJson(response);
}

export async function checkMotionRun(payload) {
  const response = await projectFetch('/api/motion-run/check', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  return readJson(response);
}

export async function initializeMotionRun(payload) {
  const response = await projectFetch('/api/motion-run/initialize', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  return readJson(response);
}

export async function startMotionRun(payload) {
  const response = await projectFetch('/api/motion-run/start', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  return readJson(response);
}

export async function stopMotionRun() {
  const response = await projectFetch('/api/motion-run/stop', { method: 'POST' });
  return readJson(response);
}

export async function requestMotionSafetyStop() {
  const response = await projectFetch('/api/safety/motion-stop', { method: 'POST' });
  return readJson(response);
}

export async function requestEmergencySafetyStop() {
  const response = await projectFetch('/api/safety/emergency-stop', { method: 'POST' });
  return readJson(response);
}

export async function fetchMidiMonitor() {
  const response = await projectFetch('/api/midi-monitor');
  return readJson(response);
}

export async function saveMidiMapping(payload) {
  const response = await projectFetch('/api/midi-monitor/mapping', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  return readJson(response);
}

export async function createMidiBank(payload = {}) {
  const response = await projectFetch('/api/midi-monitor/banks', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  return readJson(response);
}

export async function selectMidiBank(bankId) {
  const response = await projectFetch(`/api/midi-monitor/banks/${encodeURIComponent(bankId)}/select`, {
    method: 'POST',
  });
  return readJson(response);
}

export async function updateMidiBank(bankId, payload) {
  const response = await projectFetch(`/api/midi-monitor/banks/${encodeURIComponent(bankId)}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  return readJson(response);
}

export async function deleteMidiBank(bankId) {
  const response = await projectFetch(`/api/midi-monitor/banks/${encodeURIComponent(bankId)}`, {
    method: 'DELETE',
  });
  return readJson(response);
}

export async function loadMidiBanksFromFile() {
  const response = await projectFetch('/api/midi-monitor/banks/file/load', { method: 'POST' });
  return readJson(response);
}

export async function resetMidiRuntimeValues() {
  const response = await projectFetch('/api/midi-monitor/runtime/reset', { method: 'POST' });
  return readJson(response);
}

export async function connectMidiDevice() {
  const response = await projectFetch('/api/midi-monitor/device/connect', { method: 'POST' });
  return readJson(response);
}

export async function disconnectMidiDevice() {
  const response = await projectFetch('/api/midi-monitor/device/disconnect', { method: 'POST' });
  return readJson(response);
}

export async function requestAcServoJog(payload) {
  const response = await projectFetch('/api/motion-test/ac-servo/jog', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  return readJson(response);
}

export async function requestDynamixelJog(payload) {
  const response = await projectFetch('/api/motion-test/dynamixel/jog', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  return readJson(response);
}

export async function requestAcServoAction(payload) {
  const response = await projectFetch('/api/motion-test/ac-servo/action', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  return readJson(response);
}

export async function requestDynamixelAction(payload) {
  const response = await projectFetch('/api/motion-test/dynamixel/action', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  return readJson(response);
}

export async function requestAcServoControl(payload) {
  const response = await projectFetch('/api/motion-test/ac-servo/control', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  return readJson(response);
}
