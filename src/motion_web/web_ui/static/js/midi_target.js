/**
 * MIDI 를 쓸 PC 고르기 · §6-94
 *
 * MIDI 장치는 한 대뿐이고 **한 번에 한 PC 만** 그것을 쓴다 · USB 를 옮겨 꽂는
 * 것과 같되 선을 뽑지 않는다.
 *
 * **정하는 것은 장치를 든 PC 다** · 넘겨받는 쪽에서 가로챌 수 있게 하면 같은
 * 페이더를 둘이 민다 · 판정은 조정 노드가 하고(`midi_relay`), 여기서는 그
 * 상태를 화면 모양으로 옮기기만 한다.
 */

/** 대상이 비어 있으면 장치를 든 PC 가 직접 쓰는 것이다. */
function currentUser(relay = {}) {
  return String(relay.target_pc_id || relay.device_pc_id || '');
}

export function midiTargetView({
  relay = {}, config = {}, peers = [], joined = false,
} = {}) {
  const me = String(config.pc_id || relay.pc_id || '');
  const deviceHolder = String(relay.device_pc_id || '');
  const holdsDevice = relay.holds_device === true;
  const current = currentUser(relay);

  // **연동 중인 PC 중에서만 고른다** · 통신이 끊긴 PC 로 넘기면 MIDI 가 아무
  // 데도 가지 않고, 그 PC 는 자기가 대상이 된 줄도 모른다
  const online = (Array.isArray(peers) ? peers : [])
    .filter((peer) => String(peer.state || '') === 'online')
    .map((peer) => ({
      pc_id: String(peer.pc_id || ''),
      label: String(peer.display_name || peer.pc_id || ''),
    }));
  const names = [
    { pc_id: me, label: `이 PC (${me || '이름 없음'})` },
    ...online,
  ].filter((item) => item.pc_id);

  let reason = '';
  if (!deviceHolder) {
    reason = '그룹에 MIDI 장치가 없습니다';
  } else if (!holdsDevice) {
    reason = `MIDI 장치는 ${deviceHolder} 에 있습니다 · 그 PC 에서 정하세요`;
  } else if (!joined) {
    reason = '그룹에 참가하지 않았습니다 · 참가해야 다른 PC 로 넘길 수 있습니다';
  } else if (online.length === 0) {
    reason = '연동 중인 다른 PC 가 없습니다';
  }

  return {
    me,
    deviceHolder,
    current,
    canChoose: holdsDevice,
    reason,
    state: deviceHolder
      ? (current === deviceHolder
        ? `${deviceHolder} 에서 직접 사용 중`
        : `${deviceHolder} → ${current} 로 넘김`)
      : 'MIDI 장치 없음',
    choices: names.map((item) => ({
      ...item,
      active: item.pc_id === current,
      // 장치를 든 PC 를 고르는 것이 곧 되돌리기다
      isDevice: item.pc_id === deviceHolder,
      disabled: !holdsDevice || item.pc_id === current
        || (item.pc_id !== deviceHolder && !joined),
    })),
  };
}
