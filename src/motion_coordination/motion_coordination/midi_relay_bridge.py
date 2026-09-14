"""중계 규칙을 ROS 에 잇는다 · §6-94

규칙은 `midi_relay` 에 있다 · 보낼 것인가, 받을 것인가, 이 값이 내 것인가 ·
여기서는 그 판단을 **보기만 하고** 구독·발행만 한다 · 여기서 다시 판단하면
ROS 없이 시험하던 규칙과 조용히 갈린다.

    장치 ─USB─▶ PC1 입력브리지 ─[/pc1/xtouch/midi]─▶ PC1 조정
                                                       ║ /motion_group/midi
       PC2 midi_control ◀─[/pc2/xtouch/midi]─── PC2 조정 ◀╝

    되돌아가는 페이더 명령(`/motion_group/midi_feedback`)은 정확히 반대 길이다.

받는 PC 의 `midi_control` 이 보기에 **USB 를 직접 꽂은 것과 구별되지 않는다** ·
그래서 SELECT·Pickup·한계값·중재기가 전부 기존대로 돈다.

**평소에는 로컬 MIDI 를 구독조차 하지 않는다** · 대상이 정해지는 순간 열고,
풀리는 순간 버린다 · 아무 데도 안 가는 200Hz 를 받아 내는 일이 없어야 한다.
"""

from __future__ import annotations

import json
import time
from typing import Any, Callable, Dict, Optional

from midi_msgs.msg import Midi
from motion_coordination_interfaces.msg import GroupMidi, GroupMidiFeedback
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
from std_msgs.msg import String

from motion_common import topics

from .midi_relay import MidiRelayRules, SequenceGate


#: 이만큼 원격 MIDI 가 없으면 중계가 끝난 것으로 본다
#:
#: 보내는 쪽은 움직임이 없어도 200Hz 로 흘린다 · 1초면 200개를 놓친 것이다 ·
#: 대상 PC 의 `midi_control` 은 제 시간(0.5초)으로 더 빨리 판정한다 · 여기 값은
#: "기억을 지울 때"만 쓰므로 넉넉히 둔다 · 짧게 잡으면 잠깐 끊길 때마다
#: 장치 주인이 없어졌다 생겼다 한다.
REMOTE_STREAM_TIMEOUT_SEC = 1.0


def _midi_qos() -> QoSProfile:
    """200Hz 최선형 · 깊이 1 · 놓친 것을 다시 보내느니 다음 것을 보낸다.

    입력 브리지(`midi_input_node`)와 `midi_control` 이 쓰는 것과 **같아야**
    한다 · 다르면 아무 말 없이 한 쪽도 안 받는다.
    """
    return QoSProfile(depth=1, reliability=ReliabilityPolicy.BEST_EFFORT)


def _connection_state_qos() -> QoSProfile:
    """장치 연결 상태 · 늦게 붙어도 마지막 값을 받아야 한다."""
    return QoSProfile(
        depth=1,
        reliability=ReliabilityPolicy.RELIABLE,
        durability=DurabilityPolicy.TRANSIENT_LOCAL,
    )


class MidiRelayBridge:
    """조정 노드에 중계를 붙인다.

    장치 주인과 대상은 **장치가 꽂힌 PC 가 정한다** · 나머지 PC 는 오는
    메시지에 실린 값을 받아 쓴다 · 양쪽에서 정하면 같은 페이더를 둘이 민다.
    """

    def __init__(
        self,
        node: Any,
        *,
        pc_id: Any,
        group_id: Any,
        clock: Callable[[], float] = time.monotonic,
        stale_sec: float = REMOTE_STREAM_TIMEOUT_SEC,
    ) -> None:
        self._node = node
        self._clock = clock
        self._stale_sec = max(0.1, float(stale_sec))
        self.rules = MidiRelayRules(pc_id)
        self.rules.update(group_id=group_id)
        self._midi_gate = SequenceGate()
        self._feedback_gate = SequenceGate()
        self._sequence = 0
        self._feedback_sequence = 0
        self._device_connected = False
        self._remote_source_pc_id = ''
        self._remote_seen_at: Optional[float] = None
        self._local_midi_sub: Any = None
        self._counters: Dict[str, int] = {
            'midi_sent': 0,
            'midi_received': 0,
            'midi_dropped': 0,
            'feedback_sent': 0,
            'feedback_received': 0,
        }

        self._group_midi_pub = node.create_publisher(
            GroupMidi, topics.GROUP_MIDI, _midi_qos()
        )
        self._local_midi_pub = node.create_publisher(
            Midi, topics.XTOUCH_MIDI, _midi_qos()
        )
        self._group_feedback_pub = node.create_publisher(
            GroupMidiFeedback, topics.GROUP_MIDI_FEEDBACK, 10
        )
        self._local_feedback_pub = node.create_publisher(
            String, topics.XTOUCH_FEEDBACK, 10
        )

        node.create_subscription(
            GroupMidi, topics.GROUP_MIDI, self._on_group_midi, _midi_qos()
        )
        node.create_subscription(
            GroupMidiFeedback, topics.GROUP_MIDI_FEEDBACK,
            self._on_group_feedback, 10,
        )
        node.create_subscription(
            String, topics.XTOUCH_CONNECTION_STATE,
            self._on_connection_state, _connection_state_qos(),
        )
        node.create_subscription(
            String, topics.XTOUCH_FEEDBACK, self._on_local_feedback, 10
        )

    # ----------------------------------------------------------------- #
    # 대상 선택 · 주인은 장치를 든 PC 하나다
    # ----------------------------------------------------------------- #

    def set_target(self, pc_id: Any) -> Dict[str, Any]:
        """이 MIDI 를 누가 쓸지 정한다 · **장치를 든 PC 에서만** 정한다.

        USB 를 옮겨 꽂는 것과 같다 · 선을 뽑지 않을 뿐이다 · 넘겨받는 쪽에서
        가로챌 수 있게 하면 같은 페이더를 둘이 밀게 된다.

        빈 값은 **되돌리기**다 · 장치가 빠진 뒤에도 언제나 된다.
        """
        wanted = str(pc_id or '').strip()
        if wanted:
            if not self.rules.group_id:
                raise ValueError('그룹 설정이 없어 MIDI 를 넘길 수 없습니다')
            if not self._device_connected:
                raise ValueError(
                    'MIDI 장치가 이 PC 에 없습니다 · 장치가 꽂힌 PC 에서 정하세요'
                )
        self.rules.update(target_pc_id=wanted)
        # 대상이 바뀌면 번호도 새로 센다 · 안 그러면 새 흐름의 첫 값이
        # 지난 흐름의 큰 번호에 막혀 통째로 버려진다
        self._midi_gate.reset()
        self._feedback_gate.reset()
        # 구독은 여기서 열지 않는다 · 이 함수는 **로컬 API 스레드**에서 불리고,
        # 구독을 만들고 지우는 일은 노드를 도는 쪽이 해야 한다 · 다음 `tick()`
        # 이 맞춰 준다(0.1초) · 넘겨주는 일에 그 정도 늦음은 보이지 않는다.
        return {
            'success': True,
            'message': (
                f'MIDI 를 {wanted} 에서 씁니다' if wanted else 'MIDI 를 되돌렸습니다'
            ),
            'midi_relay': self.snapshot(),
        }

    # ----------------------------------------------------------------- #
    # 이 PC 의 장치
    # ----------------------------------------------------------------- #

    def _on_connection_state(self, message: String) -> None:
        try:
            payload = json.loads(message.data)
        except (json.JSONDecodeError, TypeError):
            return
        if not isinstance(payload, dict):
            return
        connected = bool(payload.get('connected'))
        if connected == self._device_connected:
            return
        self._device_connected = connected
        if not connected:
            # 장치가 빠졌으면 중계도 끝이다 · 대상만 남겨 두면 다시 꽂는
            # 순간 아무도 누르지 않았는데 남의 PC 로 흘러 나간다
            self.rules.update(target_pc_id='')
        self._refresh_device_owner()
        self._sync_local_midi_subscription()

    def _refresh_device_owner(self) -> None:
        """장치 주인은 하나다 · 내 장치가 붙어 있으면 나, 아니면 나에게 값을
        보내 주는 PC · 둘 다 아니면 없다."""
        if self._device_connected:
            self.rules.update(device_pc_id=self.rules.pc_id)
        else:
            self.rules.update(device_pc_id=self._remote_source_pc_id)

    def _sync_local_midi_subscription(self) -> None:
        """중계할 때만 로컬 200Hz 를 연다."""
        needed = self.rules.should_send_midi
        if needed and self._local_midi_sub is None:
            self._local_midi_sub = self._node.create_subscription(
                Midi, topics.XTOUCH_MIDI, self._on_local_midi, _midi_qos()
            )
        elif not needed and self._local_midi_sub is not None:
            self._node.destroy_subscription(self._local_midi_sub)
            self._local_midi_sub = None

    # ----------------------------------------------------------------- #
    # 보내기 · 장치를 든 PC 에서
    # ----------------------------------------------------------------- #

    def _on_local_midi(self, message: Midi) -> None:
        if not self.rules.should_send_midi:
            return
        self._sequence += 1
        out = GroupMidi()
        out.group_id = self.rules.group_id
        out.source_pc_id = self.rules.pc_id
        out.target_pc_id = self.rules.target_pc_id
        out.sequence = self._sequence
        out.sent_at = self._now()
        # 장치 상태를 **그대로** 담는다 · 베껴 적으면 규격이 바뀔 때 갈린다
        out.midi = message
        self._group_midi_pub.publish(out)
        self._counters['midi_sent'] += 1

    # ----------------------------------------------------------------- #
    # 받기 · 대상 PC 에서
    # ----------------------------------------------------------------- #

    def _on_group_midi(self, message: GroupMidi) -> None:
        if not self.rules.accepts_midi(message):
            return
        if not self._midi_gate.accepts(message.sequence):
            # 200Hz 최선형이라 순서가 뒤집힌다 · 옛 값을 그대로 쓰면 모터가
            # 잠깐 뒤로 갔다 온다
            self._counters['midi_dropped'] += 1
            return
        # 장치가 어디 있는지는 **오는 값이 알려 준다** · 이 PC 에는 없다
        self._remote_source_pc_id = str(message.source_pc_id or '')
        self._remote_seen_at = self._clock()
        self.rules.update(target_pc_id=message.target_pc_id)
        self._refresh_device_owner()
        self._local_midi_pub.publish(message.midi)
        self._counters['midi_received'] += 1

    # ----------------------------------------------------------------- #
    # 되돌아가는 페이더 명령
    # ----------------------------------------------------------------- #

    def _on_local_feedback(self, message: String) -> None:
        """물리 페이더는 한 대뿐이다 · **대상 PC 하나만** 되돌린다."""
        if not self.rules.should_send_feedback:
            return
        self._feedback_sequence += 1
        out = GroupMidiFeedback()
        out.group_id = self.rules.group_id
        out.source_pc_id = self.rules.pc_id
        out.target_pc_id = self.rules.device_pc_id
        out.sequence = self._feedback_sequence
        out.sent_at = self._now()
        out.payload = str(message.data or '')
        self._group_feedback_pub.publish(out)
        self._counters['feedback_sent'] += 1

    def _on_group_feedback(self, message: GroupMidiFeedback) -> None:
        if not self.rules.accepts_feedback(message):
            return
        if not self._feedback_gate.accepts(message.sequence):
            return
        out = String()
        out.data = str(message.payload or '')
        self._local_feedback_pub.publish(out)
        self._counters['feedback_received'] += 1

    # ----------------------------------------------------------------- #
    # 시간
    # ----------------------------------------------------------------- #

    def tick(self) -> None:
        """노드가 주기적으로 부른다 · 끊김을 정리하고 구독을 맞춘다.

        구독을 여닫는 일이 **여기 한 곳**에 있다 · 로컬 API 스레드가 대상을
        바꾸고 같은 일을 하면 노드를 도는 쪽과 부딪힌다.
        """
        self._expire_remote_stream()
        self._sync_local_midi_subscription()

    def _expire_remote_stream(self) -> None:
        """원격 값이 끊기면 기억을 지운다.

        안 지우면 장치가 아직 남에게 있다고 믿어, 아무도 못 받는 페이더 명령을
        계속 내보낸다.
        """
        if self._remote_seen_at is None:
            return
        if self._clock() - self._remote_seen_at <= self._stale_sec:
            return
        self._remote_seen_at = None
        self._remote_source_pc_id = ''
        self._midi_gate.reset()
        self._feedback_gate.reset()
        if not self._device_connected:
            self.rules.update(target_pc_id='')
        self._refresh_device_owner()

    def _now(self) -> Any:
        return self._node.get_clock().now().to_msg()

    # ----------------------------------------------------------------- #
    # 조회
    # ----------------------------------------------------------------- #

    def snapshot(self) -> Dict[str, Any]:
        """화면이 읽는 모양 · 왜 안 움직이는지 사용자가 알아야 한다."""
        age = (
            None if self._remote_seen_at is None
            else round(max(0.0, self._clock() - self._remote_seen_at), 3)
        )
        state = self.rules.snapshot()
        state.update({
            'device_connected': self._device_connected,
            'remote_age_sec': age,
            'counters': dict(self._counters),
        })
        return state
