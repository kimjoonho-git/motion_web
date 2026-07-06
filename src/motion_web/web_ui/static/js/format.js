export const motorTypeFilters = [
  { key: 'all', label: 'All' },
  { key: 'ac_servo', label: 'AC Servo' },
  { key: 'dynamixel', label: 'Dynamixel' },
  { key: 'cubemars', label: 'CubeMars' },
  { key: 'unknown', label: 'Unknown' },
];

export const statusDisplayLabels = {
  'Not ready to switch on': 'Not Ready',
  'Switch on disabled': 'Servo OFF',
  'Ready to switch on': 'Ready',
  'Switched on': 'Power ON',
  'Operation enabled': 'Servo ON',
  'Quick stop active': 'Quick Stop',
  'Fault reaction active': 'Error',
  Fault: 'Error',
  'Unknown status': 'Unknown',
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

export function formatHexByte(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '-';
  return `0x${(Number(value) & 0xFF).toString(16).toUpperCase().padStart(2, '0')}`;
}

export function formatYamlHex(value) {
  if (value === null || value === undefined || value === '' || Number.isNaN(Number(value))) return '';
  return `0x${Number(value).toString(16).toUpperCase().padStart(8, '0')}`;
}

export function formatRotarySwitch(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '-';
  const byte = Number(value) & 0xFF;
  const high = byte >> 4;
  const low = byte & 0x0F;
  return `${high}-${low}`;
}

export function parseIntegerValue(value, fallback = null) {
  const text = String(value ?? '').trim();
  if (!text) return fallback;
  const parsed = Number(text);
  return Number.isFinite(parsed) ? parsed : fallback;
}

export function parseIntegerField(field, fallback = null) {
  return parseIntegerValue(field?.value, fallback);
}

export function formatTime(epochSeconds) {
  if (!epochSeconds) return '-';
  return new Date(epochSeconds * 1000).toLocaleTimeString();
}

export function stateLabel(state) {
  const labels = {
    detected: '정상',
    stale: '갱신 지연',
    disconnected: '연결 끊김',
    monitoring_off: '모니터링 OFF',
    ethercat_down: '전원 OFF / 통신 끊김',
  };
  return labels[state] || state || 'Unknown';
}

export function formatCounts(counts) {
  if (!counts || Object.keys(counts).length === 0) return 'Unknown';
  return Object.entries(counts)
    .map(([name, count]) => `${name} ${formatInt(count)}`)
    .join(', ');
}

export function countBy(items, key) {
  return items.reduce((counts, item) => {
    const value = item[key] || 'Unknown';
    counts[value] = (counts[value] || 0) + 1;
    return counts;
  }, {});
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
  return item ? item.label : 'Unknown';
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
