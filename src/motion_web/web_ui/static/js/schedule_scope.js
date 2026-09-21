/** 스케줄이 지금 무엇을 하는가 · §6-133
 *
 * 스케줄은 한 벌이다 · 연동을 쓰면 그룹 전체가, 안 쓰면 이 PC 만 움직인다 ·
 * 그런데 화면에는 `is_master` 하나만 있었고, **연동을 쓰지 않는 PC 도
 * `is_master: true`** 로 돌아온다 (`resolve_master_role` 이 연동 비활성을
 * "단독 동작으로 간주" 한다). 그래서 연동을 켠 적 없는 사용자에게 "마스터"
 * 라고 떴다.
 *
 * 그리고 연동을 쓰지만 「연동 탈퇴」를 눌러 둔 동안에는, 스케줄이 발화해도
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
      text: '스케줄러: 이 PC 에서는 설정하지 않습니다',
      tone: 'muted',
      canEdit: false,
      warning: '',
      blockedReason:
        '이 PC 는 받는 쪽이라 스케줄도 실행 관리도 여기서는 '
        + '아무 일을 하지 않습니다 · 다른 PC 가 보내는 실행만 이 PC 를 '
        + '움직입니다 · 여기서 멈추려면 「전체 동작 정지」를 누르세요',
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
  // **스케줄은 묶였는지와 상관없이 돈다** · §6-266
  //
  // 전에는 묶이지 않았으면 「시각이 되어도 실행되지 않습니다」라고 했다 ·
  // 실제로 그랬고, 16~18시 구간 안에서 15분 동안 7번 거절당하며 한 번도
  // 돌지 않았다 · 이제 묶이지 않으면 이 PC 혼자 돈다 · 그러니 그 경고는
  // 사실이 아니고, 여기서 남의 화면 이야기를 할 이유도 없다.
  return {
    scope: 'group',
    text: `스케줄러: 동작 중 ${registered}`,
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
    return '시각이 되면 등록된 모션을 연속 시작하고, '
      + '종료 시각에 현재 회차 후 정지합니다.';
  }
  return '스케줄러 상태를 확인하고 있습니다.';
}


/** 지금 멈추면 스케줄이 되돌리는가 · §6-149
 *
 * 스케줄 모드에서는 사람이 정지를 눌러도 다음 점검에서 다시 시작한다 ·
 * **이건 버그가 아니라 설계다** · 「사람이 멈췄나」를 요청 내용으로 추측하다가
 * 그룹 정지·안전 정지까지 사람이 멈춘 것으로 읽는 오판이 나서, 추측을 없애고
 * 모드 스위치 하나로 만들었다(§6-143).
 *
 * 문제는 그걸 아는 사람만 안다는 것이다 · 무대에서 "잠깐 멈춰" 하고 눌렀는데
 * 얼마 뒤 저절로 도로 돌면 위험하다 · 그래서 멈출 때 말해 준다.
 *
 * 빈 문자열이면 되돌리지 않는다는 뜻이다.
 */
export function motionScheduleResumeNote(status) {
  const read = readStatus(status);
  if (!read) return '';
  if (read.manual) return '';                       // 스케줄이 손대지 않는다
  if (read.enabled && !read.isMaster) return '';    // 여기서는 스케줄이 안 돈다
  if (!status?.active_schedule_id) return '';       // 지금은 돌아야 할 구간이 아니다
  const seconds = Number(status?.reconcile_interval_sec);
  const within = Number.isFinite(seconds) && seconds > 0
    ? `최대 ${seconds.toFixed(0)}초 뒤` : '잠시 뒤';
  return `스케줄 모드입니다 · ${within} 다시 시작합니다 · `
    + '계속 멈춰 두려면 「📅 모션 스케줄」에서 수동 모드로 바꾸세요';
}


/** 스케줄이 태어난 시간대와 이 PC 의 시간대가 어긋났는가 · §6-150
 *
 * PC 를 들고 나가서 네트워크에 붙여도 시간대는 안 바뀐다 · NTP 는 절대
 * 시각(UTC)만 맞춘다 · 그래서 한국에서 만든 09:17 스케줄이 파리에서 현지
 * 02:17 에 돈다 · 시계는 맞는데 화면 어디에도 이상이 없다.
 *
 * 스케줄은 **저장될 때 그 PC 의 시간대를 지문으로 적어 둔다** · 판단에는 쓰지
 * 않는다 · 시각은 지금 이 PC 기준으로 해석한다 · 이 함수는 오직 지문과 지금이
 * 다른지만 본다.
 *
 * 빈 문자열이면 어긋나지 않았다는 뜻이다.
 */
export function motionScheduleTimezoneDrift(schedules, timezone) {
  const now = String(timezone || '').trim();
  if (!now) return '';
  const stamps = new Set(
    (Array.isArray(schedules) ? schedules : [])
      .filter((item) => item?.enabled !== false)
      .map((item) => String(item?.saved_timezone || '').trim())
      .filter(Boolean),
  );
  stamps.delete(now);
  if (stamps.size === 0) return '';
  const names = [...stamps].sort().join(', ');
  return `⚠️ 이 스케줄은 ${names} 에서 만들어졌는데 이 PC 는 지금 ${now} 입니다 · `
    + '시각을 다시 확인하세요 · 시간대를 바꾸려면 「터미널」 화면에서';
}
