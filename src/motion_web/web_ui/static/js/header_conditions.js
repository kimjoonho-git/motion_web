/** 상단의 두 칸 · **아는 쪽이 바로 고쳐 준다** · §6-286 §6-288
 *
 * 칸은 둘이다 ·
 *
 *     연동 / 단독      이 PC 가 그룹으로 도나 혼자 도나
 *     시간 안 / 시간 밖  지금이 스케줄 구간 안인가
 *
 * 두 사실은 **주인이 다르다** · 연동 여부는 연동 화면이 1초마다 받고, 구간
 * 여부는 스케줄 상태가 5초마다 받는다 · 전에는 둘 다 스케줄 상태에만
 * 묶여 있어서, 「연동 탈퇴」를 눌러도 상단이 최대 5초를 옛 값으로 버텼다 ·
 * 사람은 눌렸는지 안 눌렸는지를 모른다.
 *
 * 그래서 아는 쪽이 아는 것만 밀어 넣는다 · 나머지는 직전 값을 그대로 둔다.
 */

const state = {
  enabled: null,
  joined: null,
  inWindow: null,
};

/** 순수 계산 · 화면을 모른다 · 시험은 이것만 본다 */
export function motionHeaderConditionCells({ enabled, joined, inWindow }) {
  if (enabled === null || joined === null) {
    return [
      { key: 'scope', text: '연동?', on: false, title: '연동 상태 확인 중' },
      { key: 'window', text: '시간?', on: false, title: '스케줄 상태 확인 중' },
    ];
  }
  const grouped = Boolean(enabled) && Boolean(joined);
  return [
    {
      key: 'scope',
      text: grouped ? '연동' : '단독',
      on: grouped,
      title: grouped
        ? '연동 참가 중 · 그룹으로 함께 돕니다'
        : (enabled
          ? '연동을 쓰지만 그룹에서 나가 있습니다 · 이 PC 혼자 돕니다'
          : '연동을 쓰지 않습니다 · 이 PC 혼자 돕니다'),
    },
    {
      key: 'window',
      // 시간만 본다 · 돌지 말지는 옆의 스케줄러 배지가 말한다
      text: inWindow ? '시간 안' : '시간 밖',
      on: Boolean(inWindow),
      title: inWindow
        ? '지금은 스케줄 구간 안입니다'
        : '지금은 스케줄 구간 밖입니다 · 시간이 되면 스스로 켭니다',
    },
  ];
}

function draw() {
  // 화면이 없는 곳(시험)에서도 불린다 · 계산만 하고 조용히 돌아간다
  if (typeof document === 'undefined') return;
  const host = document.getElementById('headerConditionBadges');
  if (!host) return;
  const cells = motionHeaderConditionCells(state);
  const key = cells.map((cell) => `${cell.text}:${cell.on}`).join('|');
  if (host.dataset.key === key) return;
  host.dataset.key = key;
  host.replaceChildren(...cells.map((cell) => {
    const node = document.createElement('span');
    node.className = cell.on ? 'on' : 'off';
    node.textContent = cell.text;
    node.title = cell.title || '';
    return node;
  }));
}

/** 아는 것만 밀어 넣는다 · 안 준 값은 직전 것을 그대로 둔다 */
export function motionHeaderConditionsUpdate(part) {
  let changed = false;
  for (const [key, value] of Object.entries(part || {})) {
    if (!(key in state) || value === undefined) continue;
    if (state[key] !== value) {
      state[key] = value;
      changed = true;
    }
  }
  if (changed) draw();
}
