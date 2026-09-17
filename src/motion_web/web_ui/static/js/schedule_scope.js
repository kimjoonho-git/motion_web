/** 스케줄이 지금 무엇을 하는가 · §6-133
 *
 * 스케줄은 한 벌이다 · 연동을 쓰면 그룹 전체가, 안 쓰면 이 PC 만 움직인다 ·
 * 그런데 화면에는 `is_master` 하나만 있었고, **연동을 쓰지 않는 PC 도
 * `is_master: true`** 로 돌아온다 (`resolve_master_role` 이 연동 비활성을
 * "단독 동작으로 간주" 한다). 그래서 연동을 켠 적 없는 사용자에게 "마스터"
 * 라고 떴다.
 *
 * 그리고 연동을 쓰지만 「지금 빠지기」를 눌러 둔 동안에는, 스케줄이 발화해도
 * 조정 노드가 `먼저 DDS 그룹에 참가하세요` 로 거부한다 · 로그에만 남았다.
 */

function readStatus(status) {
  if (!status) return null;
  return {
    enabled: Boolean(status.coordination_enabled),
    joined: Boolean(status.coordination_joined),
    isMaster: status.is_master !== false,
    count: Number(status.schedule_count) || 0,
    // 조정 노드가 안 붙으면 `joined` 는 마지막으로 받아 둔 값이거나 빈 값이다 ·
    // 그것으로 "빠져 있음" 이라 단정하면 없는 문제를 만든다 · §6-133
    nodeConnected: status.coordination_node_connected !== false,
    hold: String(status.schedule_hold_reason || ''),
  };
}

export function motionScheduleBadgeState(status) {
  const read = readStatus(status);
  if (!read) {
    return {
      scope: 'unknown', text: '스케줄러: 확인 중', tone: 'muted',
      canEdit: false, warning: '', blockedReason: '',
    };
  }
  const { enabled, joined, isMaster, count, nodeConnected, hold } = read;
  const registered = `(${count}개 등록)`;

  // 사람이 멈춰 두면 스케줄이 손대지 않는다 · §6-138
  //
  // 점검은 1분마다 "구간 안인데 안 돈다" 를 보고 다시 시작시킨다 · 사람이
  // 「즉시 정지」를 눌렀는데 1분 뒤 되살아나면 정지가 무의미하고 정비 중에는
  // 위험하다 · 왜 안 도는지 화면에 없으면 "스케줄이 고장났다" 가 된다.
  if (hold) {
    return {
      scope: 'held',
      text: '스케줄러: 사람이 정지시킴',
      tone: 'warn',
      canEdit: true,
      warning: hold,
      blockedReason: '',
    };
  }

  if (!enabled) {
    return {
      scope: 'local',
      text: `스케줄러: 동작 중 ${registered}`,
      tone: 'ok',
      canEdit: true,
      warning: '',
      blockedReason: '',
    };
  }
  if (!isMaster) {
    return {
      scope: 'slave',
      text: '스케줄러: 슬레이브 · 마스터 PC 에서 설정',
      tone: 'muted',
      canEdit: false,
      warning: '',
      blockedReason:
        '이 PC 는 연동 슬레이브입니다 · 스케줄은 마스터 PC 에서 설정하며, '
        + '발화하면 이 PC 도 함께 실행됩니다',
    };
  }
  if (!nodeConnected) {
    return {
      scope: 'group',
      text: '스케줄러: 마스터 · 연동 상태 확인 중',
      tone: 'muted',
      canEdit: true,
      warning: '',
      blockedReason: '',
    };
  }
  if (!joined) {
    return {
      scope: 'group',
      text: '스케줄러: 마스터 · 그룹에서 빠져 있음',
      tone: 'warn',
      canEdit: true,
      warning:
        '이 PC 가 그룹에서 빠져 있어, 시각이 되어도 실행되지 않습니다 · '
        + 'PC 연동 화면에서 「다시 참가」를 누르세요.',
      blockedReason: '',
    };
  }
  return {
    scope: 'group',
    text: `스케줄러: 마스터 ${registered}`,
    tone: 'ok',
    canEdit: true,
    warning: '',
    blockedReason: '',
  };
}

/** 모달 설명 · 무엇이 함께 움직이는지 먼저 말한다 */
export function motionScheduleScopeNote(status) {
  const state = motionScheduleBadgeState(status);
  if (state.scope === 'held') {
    return `${state.warning} · 모션 실행 화면에서 시작하면 스케줄이 다시 관리합니다.`;
  }
  if (state.scope === 'local') {
    return '시각이 되면 이 PC 의 등록된 모션을 연속 시작하고, '
      + '종료 시각에 현재 회차 후 정지합니다.';
  }
  if (state.scope === 'slave') {
    return state.blockedReason + '.';
  }
  if (state.scope === 'group') {
    return '시각이 되면 그룹에 참가한 모든 PC 가 각자의 등록된 모션을 '
      + '연속 시작하고, 종료 시각에 현재 회차 후 정지합니다.';
  }
  return '스케줄러 상태를 확인하고 있습니다.';
}
