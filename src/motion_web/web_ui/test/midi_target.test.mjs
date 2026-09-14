import assert from 'node:assert/strict';
import test from 'node:test';

import { midiTargetView } from '../static/js/midi_target.js';

const PEERS = [{ pc_id: 'pc-a', display_name: 'pc-a' }];

function view({ relay, peers = PEERS } = {}) {
  return midiTargetView({ relay, config: { pc_id: 'joonhoTest' }, peers });
}

/**
 * MIDI 장치는 한 대뿐이고 한 번에 한 PC 만 쓴다 · 정하는 것은 **장치를 든 PC** 다 ·
 * 넘겨받는 쪽에서 가로챌 수 있으면 같은 페이더를 둘이 민다 · §6-94
 */

test('장치를 든 PC 에서는 넘길 상대를 고를 수 있다', () => {
  const result = view({
    relay: {
      device_pc_id: 'joonhoTest', holds_device: true, target_pc_id: '',
      device_connected: true,
    },
  });

  assert.equal(result.canChoose, true);
  assert.equal(result.reason, '');
  assert.equal(result.state, 'joonhoTest 에서 직접 사용 중');
  const peer = result.choices.find((choice) => choice.pc_id === 'pc-a');
  assert.equal(peer.disabled, false, '상대를 고를 수 없다');
});

test('지금 쓰는 PC 는 눌러도 소용없으므로 막는다', () => {
  const result = view({
    relay: { device_pc_id: 'joonhoTest', holds_device: true, target_pc_id: '' },
  });

  const me = result.choices.find((choice) => choice.pc_id === 'joonhoTest');
  assert.equal(me.active, true);
  assert.equal(me.disabled, true);
});

test('넘긴 뒤에는 누가 쓰는지 그대로 보인다', () => {
  const result = view({
    relay: { device_pc_id: 'joonhoTest', holds_device: true, target_pc_id: 'pc-a' },
  });

  assert.equal(result.current, 'pc-a');
  assert.equal(result.state, 'joonhoTest → pc-a 로 넘김');
  assert.equal(result.choices.find((c) => c.pc_id === 'pc-a').active, true);
  // 장치를 든 PC 를 다시 고르는 것이 되돌리기다
  assert.equal(result.choices.find((c) => c.pc_id === 'joonhoTest').isDevice, true);
});

test('장치가 남에게 있으면 이 화면에서는 못 정한다', () => {
  const result = midiTargetView({
    relay: { device_pc_id: 'pc-a', holds_device: false, target_pc_id: '' },
    config: { pc_id: 'joonhoTest' },
    peers: PEERS,
  });

  assert.equal(result.canChoose, false);
  assert.match(result.reason, /pc-a 에 있습니다/);
  assert.ok(result.choices.every((choice) => choice.disabled), '남의 장치를 가로챌 수 있다');
});

test('그룹에 장치가 하나도 없으면 그렇게 말한다', () => {
  const result = view({ relay: { device_pc_id: '', holds_device: false } });

  assert.equal(result.state, 'MIDI 장치 없음');
  assert.match(result.reason, /장치가 없습니다/);
});
