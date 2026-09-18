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
    manual: String(status.run_mode || 'schedule') === 'manual',
    // 시각이 되어 시도했는데 거부당했나 · §6-147
    failure: status.last_failure || {},
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
  const { enabled, joined, isMaster, count, nodeConnected, manual } = read;
  const registered = `(${count}개 등록)`;

  // 슬레이브가 맨 앞이다 · 여기서는 스케줄도 모드도 아무 일을 하지 않는다
  // · 마스터가 보내는 그룹 실행만 이 PC 를 움직인다 · §6-143
  if (enabled && !isMaster) {
    return {
      scope: 'slave',
      text: '스케줄러: 슬레이브 · 마스터 PC 에서 설정',
      tone: 'muted',
      canEdit: false,
      warning: '',
      blockedReason:
        '이 PC 는 연동 슬레이브입니다 · 스케줄도 실행 관리도 여기서는 '
        + '아무 일을 하지 않습니다 · 마스터가 보내는 그룹 실행만 이 PC 를 '
        + '움직입니다 · 정비하려면 PC 연동 화면에서 「지금 빠지기」를 누르세요',
    };
  }
  // 수동 모드면 스케줄은 아무것도 하지 않는다 · §6-143
  //
  // 슬레이브 판정 **뒤**에 본다 · 슬레이브에서는 스케줄 자체가 안 돌아서
  // 모드가 아무 일도 하지 않는다 · 거기서 「수동 모드」라고 띄우면
  // 마스터가 보내는 그룹 실행까지 안 도는 것처럼 읽힌다.
  //
  // 전에는 「사람이 멈췄나」를 요청 내용으로 추측했다 · 그룹 정지나 안전
  // 정지까지 사람이 멈춘 것으로 읽어서, 1회 연동 실행만 해도 "사람이 모션을
  // 정지했습니다" 가 떴다 · 추측을 없애고 스위치 하나로 만들었다.
  if (manual) {
    return {
      scope: 'manual',
      text: '스케줄러: 수동 모드',
      tone: 'muted',
      canEdit: true,
      warning: '',
      blockedReason: '수동 모드입니다 · 스케줄이 시작·정지시키지 않습니다',
    };
  }

  // 시각이 됐는데 거부당했다 · §6-147
  //
  // 여기까지 왔다는 건 스케줄이 실제로 일하는 상태라는 뜻이다(수동도 슬레이브도
  // 아니다) · 그런데도 시작이 거부되고 있으면 **화면이 말해야 한다**.
  //
  // 전에는 아무 데도 안 남았다 · 스케줄 노드가 60초마다 시도하고 연동이
  // 「정상 연결된 PC 가 2대 이상 필요합니다」로 거부해도, 배지는 초록불이었다 ·
  // 한 시간을 그러고 있었는데 아무도 몰랐다.
  //
  // 한 번은 경합일 수 있다(그룹 정리 중 등) · 노랑으로 두고, 세 번 연달아
  // 거부당하면(3분) 빨강으로 올린다 · 처음부터 빨강이면 곧 아무도 안 읽는다.
  const failCount = Number(read.failure.count) || 0;
  if (failCount > 0) {
    const reason = String(read.failure.message || '이유를 알려주지 않았습니다');
    return {
      scope: enabled ? 'group' : 'local',
      text: `스케줄러: 시작 거부됨 (${failCount}회)`,
      tone: failCount >= 3 ? 'bad' : 'warn',
      canEdit: true,
      warning: `시각이 되어 시작을 시도했지만 거부되었습니다 · ${reason}`,
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
  if (state.scope === 'manual') {
    return '수동 모드입니다 · 스케줄이 시작·정지시키지 않습니다 · '
      + '스케줄 모드로 바꾸면 시각에 맞춰 관리합니다.';
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
