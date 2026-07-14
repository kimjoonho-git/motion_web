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
  if (!response.ok) {
    const detail = payload?.message || payload?.detail || `HTTP ${response.status}`;
    throw new Error(detail);
  }
  return payload;
}

export async function fetchStatusSnapshot() {
  const response = await fetch('/api/status');
  return readJson(response);
}

export async function fetchMotorEvents(category = 'all', limit = 300) {
  const query = new URLSearchParams({
    category: String(category || 'all'),
    limit: String(limit),
  });
  const response = await fetch(`/api/motor-events?${query.toString()}`);
  return readJson(response);
}

export async function clearMotorEvents() {
  const response = await fetch('/api/motor-events', { method: 'DELETE' });
  return readJson(response);
}

export async function setMonitoringEnabled(enabled) {
  const response = await fetch('/api/monitoring/enabled', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ enabled }),
  });
  return readJson(response);
}

export async function requestMotorScan() {
  const response = await fetch('/api/motors/scan', { method: 'POST' });
  return readJson(response);
}

export async function requestAcServoScan() {
  const response = await fetch('/api/motors/scan/ac-servo', { method: 'POST' });
  return readJson(response);
}

export async function requestDynamixelScan() {
  const response = await fetch('/api/motors/scan/dynamixel', { method: 'POST' });
  return readJson(response);
}

export async function fetchMotorConfig() {
  const response = await fetch('/api/motor-config');
  return readJson(response);
}

export async function saveMotorConfig(payload) {
  const response = await fetch('/api/motor-config', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  return readJson(response);
}

export async function applyMotorConfig() {
  const response = await fetch('/api/motor-config/apply', { method: 'POST' });
  return readJson(response);
}

export async function fetchMotionFiles() {
  const response = await fetch('/api/motion-files');
  return readJson(response);
}

export async function fetchMotionFile(fileId) {
  const response = await fetch(`/api/motion-files/${encodeURIComponent(fileId)}`);
  return readJson(response);
}

export async function uploadMotionFile(payload) {
  const response = await fetch('/api/motion-files/upload', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  return readJson(response);
}

export async function deleteMotionFile(fileId) {
  const response = await fetch(`/api/motion-files/${encodeURIComponent(fileId)}`, {
    method: 'DELETE',
  });
  return readJson(response);
}

export async function fetchMotionMappings() {
  const response = await fetch('/api/motion-mappings');
  return readJson(response);
}

export async function fetchMotionMapping(fileId) {
  const response = await fetch(`/api/motion-mappings/${encodeURIComponent(fileId)}`);
  return readJson(response);
}

export async function saveMotionMapping(payload) {
  const response = await fetch('/api/motion-mappings', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  return readJson(response);
}

export async function validateMotionMapping(payload) {
  const response = await fetch('/api/motion-mappings/validate', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  return readJson(response);
}

export async function deleteMotionMapping(fileId) {
  const response = await fetch(`/api/motion-mappings/${encodeURIComponent(fileId)}`, {
    method: 'DELETE',
  });
  return readJson(response);
}

export async function fetchMotionRunStatus() {
  const response = await fetch('/api/motion-run/status');
  return readJson(response);
}

export async function checkMotionRun(payload) {
  const response = await fetch('/api/motion-run/check', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  return readJson(response);
}

export async function initializeMotionRun(payload) {
  const response = await fetch('/api/motion-run/initialize', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  return readJson(response);
}

export async function startMotionRun(payload) {
  const response = await fetch('/api/motion-run/start', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  return readJson(response);
}

export async function stopMotionRun() {
  const response = await fetch('/api/motion-run/stop', { method: 'POST' });
  return readJson(response);
}

export async function fetchMidiMonitor() {
  const response = await fetch('/api/midi-monitor');
  return readJson(response);
}

export async function saveMidiMapping(payload) {
  const response = await fetch('/api/midi-monitor/mapping', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  return readJson(response);
}

export async function requestAcServoJog(payload) {
  const response = await fetch('/api/motion-test/ac-servo/jog', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  return readJson(response);
}

export async function requestDynamixelJog(payload) {
  const response = await fetch('/api/motion-test/dynamixel/jog', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  return readJson(response);
}

export async function requestAcServoAction(payload) {
  const response = await fetch('/api/motion-test/ac-servo/action', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  return readJson(response);
}

export async function requestDynamixelAction(payload) {
  const response = await fetch('/api/motion-test/dynamixel/action', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  return readJson(response);
}

export async function requestAcServoControl(payload) {
  const response = await fetch('/api/motion-test/ac-servo/control', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  return readJson(response);
}
