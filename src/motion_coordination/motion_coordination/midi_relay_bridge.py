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
from motion_coordination_interfaces.msg import GroupMidiChannel
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

#: 권한을 이만큼마다 다시 말해 준다 · §6-94
#:
#: 권한 전달은 남지 않는다 · 받는 PC 가 그때 없었으면 못 듣는다 · 되살아나는
#: 길이 있어야 한다 · 0.1 초마다 도는 `tick()` 에서 이 간격으로만 보낸다.
GRANT_REPEAT_SEC = 1.0


#: 장치가 PC 로 보내던 통로 · 장치를 든 PC 가 대상 PC 로 나른다
#:
#: **중간에서 판단하지 않는다** · USB 선이 나르던 것을 그대로 나르는 것이
#: 전부다 · 장치가 붙어 있는지, 지금 눌렸는지, 녹화해도 되는지는 **받는 PC 가**
#: 제 것으로 정한다 · 전에는 원시 MIDI 만 날라서, 받는 PC 는 값이 들어오는데도
#: "장치가 없다" 고 알았다 · 그래서 녹화가 막히고 SELECT 가 이상했다.
DEVICE_CHANNELS = ('input_state', 'connection_state')

#: PC 가 장치로 보내던 통로 · 대상 PC 가 장치를 든 PC 로 되돌린다
CONTROL_CHANNELS = ('feedback', 'connection_command')

#: 건너온 것을 이 PC 의 통로로 그대로 되뿌리는 통로 · §6-94
#:
#: `connection_state` 는 빠져 있다 · 남의 장치가 살아 있는지는 **권한 사실**
#: (`xtouch/surface`)에 적는다 · 로컬 장치 통로에 섞으면 이 PC 의 중계가
#: 그것을 제 USB 이야기로 읽는다.
REPUBLISHED_CHANNELS = ('input_state', 'feedback', 'connection_command')


def _channel_topic(channel: str) -> str:
    return {
        'input_state': topics.XTOUCH_INPUT_STATE,
        'connection_state': topics.XTOUCH_CONNECTION_STATE,
        'feedback': topics.XTOUCH_FEEDBACK,
        'connection_command': topics.XTOUCH_CONNECTION_COMMAND,
    }[channel]


def _channel_qos(channel: str) -> QoSProfile:
    """그 통로가 원래 쓰던 것과 **같아야** 한다 · 다르면 아무 말 없이 안 간다."""
    if channel == 'connection_state':
        return _connection_state_qos()
    return QoSProfile(depth=10, reliability=ReliabilityPolicy.RELIABLE)


def _midi_qos() -> QoSProfile:
    """200Hz 최선형 · 깊이 1 · 놓친 것을 다시 보내느니 다음 것을 보낸다.

    입력 브리지(`midi_input_node`)와 `midi_control` 이 쓰는 것과 **같아야**
    한다 · 다르면 아무 말 없이 한 쪽도 안 받는다.
    """
    return QoSProfile(depth=1, reliability=ReliabilityPolicy.BEST_EFFORT)


def _reads_connected(data: Any) -> bool:
    """장치 상태 글에서 "붙었나" 만 읽는다 · §6-94"""
    try:
        payload = json.loads(str(data or ''))
    except (json.JSONDecodeError, TypeError):
        return False
    return isinstance(payload, dict) and bool(payload.get('connected'))


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
        self._sequence = 0
        self._channel_sequence = 0
        self._device_connected = False
        #: 남이 나에게 표면 권한을 주었나 · 값이 흐르는 것으로 짐작하지 않는다
        self._granted = False
        #: 내가 쓰는 남의 표면이 살아 있나
        self._remote_device_connected = False
        self._remote_source_pc_id = ''
        self._remote_seen_at: Optional[float] = None
        self._last_grant_at = 0.0
        self._local_midi_sub: Any = None
        self._counters: Dict[str, int] = {
            'midi_sent': 0,
            'midi_received': 0,
            'midi_dropped': 0,
            'channel_sent': 0,
            'channel_received': 0,
        }

        self._group_midi_pub = node.create_publisher(
            GroupMidiChannel, topics.GROUP_MIDI, _midi_qos()
        )
        self._local_midi_pub = node.create_publisher(
            Midi, topics.XTOUCH_MIDI, _midi_qos()
        )
        self._group_channel_pub = node.create_publisher(
            GroupMidiChannel, topics.GROUP_MIDI_FEEDBACK, 10
        )
        #: 이 PC 가 표면을 쓸 수 있는가 · 이 사실의 주인은 여기 하나다 · §6-94
        self._surface_pub = node.create_publisher(
            String, topics.XTOUCH_SURFACE, _connection_state_qos()
        )
        #: 통로마다 이 PC 쪽 발행구 하나 · 받은 것을 그대로 다시 내보낸다
        #: **되뿌리는 통로만** 발행구를 갖는다 · §6-94
        #:
        #: `connection_state` 는 여기 없다 · 그 통로의 주인은 이 PC 의 입력
        #: 브리지 하나뿐이고, 중계는 한 마디도 하지 않는다 · "안 한다" 를
        #: 규칙으로 두면 언젠가 누가 쓴다 · 쓸 수단을 없애 둔다.
        self._local_channel_pub = {
            channel: node.create_publisher(
                String, _channel_topic(channel), _channel_qos(channel)
            )
            for channel in REPUBLISHED_CHANNELS
        }

        node.create_subscription(
            GroupMidiChannel, topics.GROUP_MIDI, self._on_group_midi, _midi_qos()
        )
        node.create_subscription(
            GroupMidiChannel, topics.GROUP_MIDI_FEEDBACK,
            self._on_group_channel, 10,
        )
        # 이 PC 의 장치가 붙었는지는 여기서만 본다 · 중계할지 정하는 데 쓴다
        node.create_subscription(
            String, topics.XTOUCH_CONNECTION_STATE,
            self._on_connection_state, _connection_state_qos(),
        )
        for channel in DEVICE_CHANNELS + CONTROL_CHANNELS:
            node.create_subscription(
                String, _channel_topic(channel),
                self._forwarder(channel), _channel_qos(channel),
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
        previous = self.rules.target_pc_id
        self.rules.update(target_pc_id=wanted)
        # 권한은 **말로** 넘긴다 · 장치가 빠진 척하지 않는다 · §6-94
        #
        # 전에는 "장치가 빠졌다" 는 가짜 장치 신호로 넘긴 PC 를 멈춰 세웠다 ·
        # 그러자 진짜 장치가 재연결을 알리는 순간 그 말을 덮어써서, 넘긴 PC 가
        # 잠깐 장치를 되찾고 페이더를 0 으로 밀었다 · 그 움직임이 받은 PC 의
        # 모터까지 0 으로 끌고 갔다.
        if previous and previous != wanted:
            self._grant_to(previous, False)
        if wanted:
            self._grant_to(wanted, True)
        # 대상이 바뀌면 번호도 새로 센다 · 안 그러면 새 흐름의 첫 값이
        # 지난 흐름의 큰 번호에 막혀 통째로 버려진다
        self._midi_gate.reset()
        self._publish_surface()
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
        if connected != self._device_connected:
            self._device_connected = connected
            if not connected:
                # 장치가 빠졌으면 중계도 끝이다 · 대상만 남겨 두면 다시 꽂는
                # 순간 아무도 누르지 않았는데 남의 PC 로 흘러 나간다
                previous = self.rules.target_pc_id
                self.rules.update(target_pc_id='')
                if previous and previous != self.rules.pc_id:
                    self._grant_to(previous, False)
            self._refresh_device_owner()
            self._sync_local_midi_subscription()
        self._publish_surface()

    def _grant_to(self, target: str, owned: bool) -> None:
        """다른 PC 에게 표면 권한을 주거나 거둔다 · §6-94

        **권한은 장치 연결과 다른 사실이다** · 그래서 장치 통로를 쓰지 않는다 ·
        장치인 척하면 진짜 장치가 재연결을 알리는 순간 서로를 덮어쓴다.

        받는 PC 는 이 말을 듣고서야 제 표면을 연다 · 값이 흐르는 것을 보고
        짐작하게 두면, 되돌린 뒤에도 1 초쯤 제가 주인인 줄 안다.
        """
        if not target:
            return
        self._channel_sequence += 1
        out = GroupMidiChannel()
        out.group_id = self.rules.group_id
        out.source_pc_id = self.rules.pc_id
        out.target_pc_id = str(target)
        out.sequence = self._channel_sequence
        out.sent_at = self._now()
        out.channel = 'owner'
        out.payload = json.dumps(
            {
                'owned': bool(owned),
                'device_pc_id': self.rules.pc_id,
                'connected': bool(self._device_connected),
            },
            ensure_ascii=False,
        )
        self._group_channel_pub.publish(out)
        self._counters['channel_sent'] += 1
        # 방금 말했다 · 되풀이는 여기서부터 센다
        self._last_grant_at = self._clock()

    def _publish_surface(self) -> None:
        """이 PC 가 지금 표면을 쓸 수 있는가 · §6-94

        이 사실의 주인은 **여기 하나**다 · `midi_control` 은 이것만 보고 표면
        구독을 열고 닫는다 · 장치가 꽂혔는지(`connection/state`)는 입력
        브리지가 따로 말하며, 둘은 섞이지 않는다.
        """
        owned = self.owns_surface
        payload = {
            'owned': owned,
            'connected': owned and self.surface_connected,
            'owner_pc_id': self.rules.target_pc_id or self.rules.pc_id,
            'device_pc_id': self.rules.device_pc_id,
            'remote': owned and not self.rules.holds_device,
            'message': self._surface_message(),
        }
        self._surface_pub.publish(
            String(data=json.dumps(payload, ensure_ascii=False))
        )

    def _surface_message(self) -> str:
        if self.owns_surface:
            if self.rules.holds_device:
                return ''
            return f'{self.rules.device_pc_id} 의 MIDI 를 씁니다'
        if self.rules.holds_device and self.rules.relaying:
            return f'MIDI 를 {self.rules.target_pc_id} 가 쓰는 중입니다'
        return 'MIDI 장치가 이 PC 에 없습니다'

    @property
    def owns_surface(self) -> bool:
        """이 PC 가 표면을 쓸 수 있나.

        내 USB 에 꽂힌 장치를 아무에게도 넘기지 않았거나, 남이 나에게
        **권한을 준** 경우다 · 값이 흐르는 것만 보고 짐작하지 않는다.
        """
        if self.rules.holds_device:
            return not self.rules.relaying
        return self._granted

    @property
    def surface_connected(self) -> bool:
        """내가 쓰는 표면이 살아 있나."""
        if self.rules.holds_device:
            return self._device_connected
        return self._remote_device_connected

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
        out = GroupMidiChannel()
        out.group_id = self.rules.group_id
        out.source_pc_id = self.rules.pc_id
        out.target_pc_id = self.rules.target_pc_id
        out.sequence = self._sequence
        out.sent_at = self._now()
        out.channel = 'midi'
        # 장치 상태를 **그대로** 담는다 · 베껴 적으면 규격이 바뀔 때 갈린다
        out.midi = message
        self._group_midi_pub.publish(out)
        self._counters['midi_sent'] += 1

    # ----------------------------------------------------------------- #
    # 받기 · 대상 PC 에서
    # ----------------------------------------------------------------- #

    def _on_group_midi(self, message: GroupMidiChannel) -> None:
        if not self._granted:
            # **권한 없이는 남의 장치 값을 내 표면에 쓰지 않는다** · §6-94
            #
            # 이 통로에는 쓰는 쪽이 둘이다 · 내 입력 브리지와 중계 · 둘이
            # 동시에 쓰면 안 된다 · 내 장치를 남에게 넘겨준 PC 가 그 남이
            # 보낸 값을 받아 제 표면에 덧쓰면 두 값이 뒤섞인다.
            return
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
        if not self._remote_device_connected:
            # 값이 온다는 것은 **저쪽 장치가 살아 있다는 뜻**이다 · §6-94
            #
            # 보내는 쪽은 장치가 붙어 있을 때만 흘린다 · 잠깐 끊겨 죽은 것으로
            # 봤다가 값이 다시 오면 **첫 값에 되살린다** · 되풀이되는 권한
            # 알림(1초)을 기다리면 그 동안 SELECT 가 꺼진 채로 있는다.
            self._remote_device_connected = True
            self._publish_surface()
        self._local_midi_pub.publish(message.midi)
        self._counters['midi_received'] += 1

    # ----------------------------------------------------------------- #
    # 되돌아가는 페이더 명령
    # ----------------------------------------------------------------- #

    def _forwarder(self, channel: str):
        """이 통로로 들어온 것을 그대로 내보낸다 · 내용은 손대지 않는다."""
        def forward(message: String) -> None:
            self._on_local_channel(channel, message)
        return forward

    def _on_local_channel(self, channel: str, message: String) -> None:
        """장치 쪽 통로는 장치를 든 PC 가, 조작 쪽 통로는 쓰는 PC 가 보낸다.

        물리 장치는 한 대뿐이다 · **대상 PC 하나만** 조작을 되돌린다.
        """
        if channel in DEVICE_CHANNELS:
            if not self.rules.should_send_midi:
                return
            target = self.rules.target_pc_id
        else:
            if not self.rules.should_send_feedback:
                return
            target = self.rules.device_pc_id
        self._channel_sequence += 1
        out = GroupMidiChannel()
        out.group_id = self.rules.group_id
        out.source_pc_id = self.rules.pc_id
        out.target_pc_id = target
        out.sequence = self._channel_sequence
        out.sent_at = self._now()
        out.channel = channel
        out.payload = str(message.data or '')
        self._group_channel_pub.publish(out)
        self._counters['channel_sent'] += 1

    def _on_group_channel(self, message: GroupMidiChannel) -> None:
        channel = str(message.channel or '')
        if channel == 'owner':
            self._on_owner_grant(message)
            return
        if channel not in DEVICE_CHANNELS + CONTROL_CHANNELS:
            return
        if channel in DEVICE_CHANNELS:
            # 장치 쪽 통로도 같다 · 권한이 있어야 남의 장치 값을 받는다
            if not self._granted or not self.rules.accepts_midi(message):
                return
        elif not self.rules.accepts_feedback(message):
            return
        payload = str(message.payload or '')
        if channel == 'connection_state':
            # **남의 장치 사실은 로컬 장치 통로로 내보내지 않는다** · §6-94
            #
            # 그 통로의 주인은 이 PC 의 입력 브리지 하나다 · 남의 장치 사실을
            # 거기 섞으면 이 PC 의 중계가 그것을 제 USB 이야기로 읽는다 ·
            # 대신 **내가 쓰는 표면이 살아 있는가**로 바꿔 적는다.
            self._remote_device_connected = _reads_connected(payload)
            self._publish_surface()
            self._counters['channel_received'] += 1
            return
        out = String()
        out.data = payload
        self._local_channel_pub[channel].publish(out)
        self._counters['channel_received'] += 1

    def _on_owner_grant(self, message: GroupMidiChannel) -> None:
        """장치를 든 PC 가 나에게 표면 권한을 주거나 거뒀다 · §6-94"""
        if not self.rules.accepts_midi(message):
            return
        try:
            payload = json.loads(str(message.payload or ''))
        except (json.JSONDecodeError, TypeError):
            return
        if not isinstance(payload, dict):
            return
        self._granted = bool(payload.get('owned'))
        self._remote_device_connected = bool(payload.get('connected'))
        self._counters['channel_received'] += 1
        if self._granted:
            self._remote_source_pc_id = str(
                payload.get('device_pc_id') or message.source_pc_id or ''
            )
            self.rules.update(target_pc_id=self.rules.pc_id)
        else:
            # 권한을 거뒀다 · 값이 더 오지 않아도 **지금** 놓는다
            self._remote_source_pc_id = ''
            self._remote_seen_at = None
            self.rules.update(target_pc_id='')
            self._midi_gate.reset()
        self._refresh_device_owner()
        self._publish_surface()

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
        self._reassert_grant()

    def _reassert_grant(self) -> None:
        """권한을 주기적으로 다시 말해 준다 · §6-94

        권한 전달은 한 번만 나가고 남지 않는다(`VOLATILE`) · 넘기는 순간 받는
        PC 의 조정 노드가 떠 있지 않았거나 다시 시작했으면 그 말을 영영 못
        듣는다 · 그러면 그 PC 에서는 MIDI 가 아무것도 안 되고, 되살릴 길이
        장치를 든 PC 에서 다시 누르는 것뿐이다.

        값이 흐르는 것을 보고 짐작하는 것과는 다르다 · **주는 쪽이 계속 말하는**
        것이다 · 거두는 말도 같은 길로 간다.
        """
        if not self.rules.should_send_midi:
            return
        if self._clock() - self._last_grant_at < GRANT_REPEAT_SEC:
            return
        self._grant_to(self.rules.target_pc_id, True)

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
        if not self._device_connected:
            self.rules.update(target_pc_id='')
        # 값이 끊겼다고 권한이 사라지지는 않는다 · 다만 표면은 죽은 것이다
        self._remote_device_connected = False
        self._refresh_device_owner()
        self._publish_surface()

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
            # 권한은 장치 연결과 다른 사실이다 · 화면도 구분해서 보여야 한다
            'owns_surface': self.owns_surface,
            'surface_connected': self.surface_connected,
            'remote_age_sec': age,
            'counters': dict(self._counters),
        })
        return state
