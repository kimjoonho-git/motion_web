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
 * **언제든 같은 모양이다** · `2026-09-10 09:18`
 *
 * 처음에는 오늘이면 시각만, 올해면 날짜까지, 그 밖이면 연도까지 줄여 썼다 ·
 * 목록에 섞여 나오니 `09. 10. 09:18` 과 `13:09` 가 나란히 서서 무엇과 무엇을
 * 견주는지 알 수 없었다 · 짧은 것보다 **같은 것**이 낫다.
 *
 * 자리 수를 직접 맞춘다 · `toLocaleDateString` 은 지역 설정에 따라 모양이
 * 달라져서 "일괄되게" 를 지킬 수 없다.
 */
export function formatMoment(epochSeconds) {
  const seconds = Number(epochSeconds);
  if (!Number.isFinite(seconds) || seconds <= 0) return '-';
  const at = new Date(seconds * 1000);
  const pad = (value) => String(value).padStart(2, '0');
  return `${at.getFullYear()}-${pad(at.getMonth() + 1)}-${pad(at.getDate())}`
    + ` ${pad(at.getHours())}:${pad(at.getMinutes())}`;
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

/** 가장 작은 값 · **펼치기(...)를 쓰지 않는다** · §6-261
 *
 * `Math.min(0, ...values)` 는 값을 전부 **인자로** 넘긴다 · 인자 수에는
 * 한계가 있어서 10분짜리 모션(27,473프레임 × 3축 = 8만 값)에서 터졌다.
 *
 *     RangeError: Maximum call stack size exceeded
 *
 * 그래프 그리는 중에 터지므로 **편집 창이 통째로 비어 보였다** · 사람에게는
 * 「용량이 커서 안 나오나」로 보였다 · 값 개수는 한계가 없다.
 */
export function minOf(values, seed = Infinity) {
  let smallest = seed;
  for (const value of values) {
    // `Number(null)` 은 0 이다 · 빈 칸을 0 으로 읽어 축 범위를 망가뜨린다
    if (typeof value !== 'number' || !Number.isFinite(value)) continue;
    if (value < smallest) smallest = value;
  }
  return smallest;
}

/** 가장 큰 값 · 같은 이유로 펼치기를 쓰지 않는다 · §6-261 */
export function maxOf(values, seed = -Infinity) {
  let largest = seed;
  for (const value of values) {
    if (typeof value !== 'number' || !Number.isFinite(value)) continue;
    if (value > largest) largest = value;
  }
  return largest;
}
