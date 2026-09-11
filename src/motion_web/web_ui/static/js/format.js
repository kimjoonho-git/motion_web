const motorTypeFilters = [
  { key: 'all', label: '전체' },
  { key: 'ac_servo', label: 'AC 서보' },
  { key: 'dynamixel', label: '다이나믹셀' },
  { key: 'cubemars', label: '큐브마스' },
  { key: 'unknown', label: '확인 불가' },
];

export const statusDisplayLabels = {
  'Not ready to switch on': '서보 준비 전',
  'Switch on disabled': '서보 꺼짐',
  'Ready to switch on': '서보 준비',
  'Switched on': '전원 켜짐',
  'Operation enabled': '서보 켜짐',
  'Quick stop active': '비상 정지',
  'Fault reaction active': '오류',
  Fault: '오류',
  'Unknown status': '상태 확인 불가',
};

export function formatNumber(value, digits = 4) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '-';
  return Number(value).toLocaleString('ko-KR', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

export function formatInt(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '-';
  return Number(value).toLocaleString('ko-KR', {
    maximumFractionDigits: 0,
  });
}

export function formatHex(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '-';
  return `0x${Number(value).toString(16).toUpperCase().padStart(4, '0')}`;
}

function parseIntegerValue(value, fallback = null) {
  const text = String(value ?? '').trim();
  if (!text) return fallback;
  const parsed = Number(text);
  return Number.isFinite(parsed) ? parsed : fallback;
}

/** 언제였는지 · 레이어·모션 파일이 마지막으로 바뀐 시각 · §6-91
 *
 * 레이어가 쌓이면 어느 것이 방금 만든 것인지 알 수 없다 · 목록 한 줄에 들어가야
 * 하므로 짧게 쓴다 · 오늘이면 시각만, 올해면 날짜까지, 그 밖이면 연도까지.
 */
export function formatMoment(epochSeconds) {
  const seconds = Number(epochSeconds);
  if (!Number.isFinite(seconds) || seconds <= 0) return '-';
  const at = new Date(seconds * 1000);
  const now = new Date();
  const clock = at.toLocaleTimeString('ko-KR', {
    hour: '2-digit', minute: '2-digit', hour12: false,
  });
  const sameDay = at.toDateString() === now.toDateString();
  if (sameDay) return clock;
  const day = at.toLocaleDateString('ko-KR', { month: '2-digit', day: '2-digit' });
  if (at.getFullYear() === now.getFullYear()) return `${day} ${clock}`;
  return `${at.getFullYear()}. ${day} ${clock}`;
}

/** 얼마나 됐는지 · "방금", "3분 전" · 시각과 함께 쓰면 가장 알아보기 쉽다. */
export function formatSince(epochSeconds) {
  const seconds = Number(epochSeconds);
  if (!Number.isFinite(seconds) || seconds <= 0) return '';
  const elapsed = (Date.now() / 1000) - seconds;
  if (elapsed < 0) return '';
  if (elapsed < 60) return '방금';
  if (elapsed < 3600) return `${Math.floor(elapsed / 60)}분 전`;
  if (elapsed < 86400) return `${Math.floor(elapsed / 3600)}시간 전`;
  return `${Math.floor(elapsed / 86400)}일 전`;
}

export function formatTime(epochSeconds) {
  if (!epochSeconds) return '-';
  return new Date(epochSeconds * 1000).toLocaleTimeString();
}

export function stateLabel(state) {
  const labels = {
    online: '정상',
    offline: '연결 끊김',
    bus_down: '버스 / 링크 끊김',
    initializing: '연결 확인 중',
    unknown: '확인 불가',
    detected: '정상',
    stale: '갱신 지연',
    disconnected: '연결 끊김',
    monitoring_off: '모니터링 꺼짐',
    ethercat_down: '전원 꺼짐 / 통신 끊김',
  };
  return labels[state] || state || '확인 불가';
}

export function formatCounts(counts) {
  if (!counts || Object.keys(counts).length === 0) return '확인 불가';
  return Object.entries(counts)
    .map(([name, count]) => `${name} ${formatInt(count)}`)
    .join(', ');
}

export function normalizeMotorTypeKey(type, label) {
  const value = `${type || ''} ${label || ''}`.toLowerCase();
  if (
    value.includes('minas') ||
    value.includes('madln') ||
    value.includes('panasonic') ||
    value.includes('ac_servo') ||
    value.includes('ac servo')
  ) {
    return 'ac_servo';
  }
  if (value.includes('dynamixel')) return 'dynamixel';
  if (value.includes('cubemars') || value.includes('cube mars')) return 'cubemars';
  return 'unknown';
}

export function motorFilterLabel(key) {
  const item = motorTypeFilters.find((filter) => filter.key === key);
  return item ? item.label : '확인 불가';
}

export function escapeHtml(value) {
  return String(value)
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');
}

export function displayText(value) {
  if (value === null || value === undefined || value === '') return '-';
  return escapeHtml(value);
}

export function aliasText(value) {
  return value === null || value === undefined || Number.isNaN(Number(value))
    ? '-'
    : formatInt(value);
}

export function clone(value) {
  return JSON.parse(JSON.stringify(value));
}
