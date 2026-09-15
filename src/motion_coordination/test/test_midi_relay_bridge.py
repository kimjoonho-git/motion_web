"""중계 배선 · §6-94

규칙(`midi_relay`)이 맞는지는 옆 파일이 본다 · 여기서는 **배선**만 본다 ·
그 판단대로 구독하고 발행하는가, 평소에 200Hz 를 헛돌리지 않는가, 장치가
빠지거나 값이 끊겼을 때 기억을 지우는가.

가짜 노드를 쓴다 · ROS 를 띄우면 이 검사들은 타이밍에 흔들린다.
"""

import json

import pytest
from midi_msgs.msg import Midi
from motion_coordination_interfaces.msg import GroupMidiChannel
from std_msgs.msg import String

from motion_common import topics
from motion_coordination.midi_relay_bridge import (
    MidiRelayBridge,
    _channel_topic,
)


# --------------------------------------------------------------------- #
# 가짜 노드
# --------------------------------------------------------------------- #

class _Publisher:
    def __init__(self, topic):
        self.topic = topic
        self.published = []

    def publish(self, message):
        self.published.append(message)


class _Subscription:
    def __init__(self, topic, callback):
        self.topic = topic
        self.callback = callback


class _Time:
    def to_msg(self):
        from builtin_interfaces.msg import Time
        return Time()


class _Clock:
    def now(self):
        return _Time()


class FakeNode:
    def __init__(self):
        self.publishers = {}
        self.subscriptions = {}

    def create_publisher(self, _type, topic, _qos):
        publisher = _Publisher(topic)
        self.publishers[topic] = publisher
        return publisher

    def create_subscription(self, _type, topic, callback, _qos):
        subscription = _Subscription(topic, callback)
        self.subscriptions.setdefault(topic, []).append(subscription)
        return subscription

    def destroy_subscription(self, subscription):
        self.subscriptions[subscription.topic].remove(subscription)

    def get_clock(self):
        return _Clock()

    def deliver(self, topic, message):
        for subscription in list(self.subscriptions.get(topic, ())):
            subscription.callback(message)

    def sent(self, topic):
        publisher = self.publishers.get(topic)
        return list(publisher.published) if publisher else []

    def subscribes(self, topic):
        return bool(self.subscriptions.get(topic))


class FakeClock:
    def __init__(self):
        self.value = 100.0

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


@pytest.fixture
def node():
    return FakeNode()


@pytest.fixture
def clock():
    return FakeClock()


def _bridge(node, clock, pc_id='pc1', group_id='test1'):
    return MidiRelayBridge(node, pc_id=pc_id, group_id=group_id, clock=clock)


def _connect(node, connected=True):
    node.deliver(
        topics.XTOUCH_CONNECTION_STATE,
        String(data=json.dumps({'connected': connected})),
    )


def _midi(channel=1):
    """장치 상태 하나 · `midi_msgs/Midi` 는 채널마다 배열이다."""
    message = Midi()
    message.channel = [int(channel)]
    return message


def _channels(messages):
    return [list(message.channel) for message in messages]


def _incoming(sequence=1, source='pc1', target='pc2', group='test1', channel=1):
    message = GroupMidiChannel()
    message.channel = 'midi'
    message.group_id = group
    message.source_pc_id = source
    message.target_pc_id = target
    message.sequence = sequence
    message.midi = _midi(channel)
    return message


# --------------------------------------------------------------------- #
# 평소 · 아무것도 안 한다
# --------------------------------------------------------------------- #

def test_the_local_midi_is_not_even_subscribed_at_rest(node, clock):
    """200Hz 다 · 갈 곳이 없으면 받아 내지도 않는다."""
    bridge = _bridge(node, clock)
    _connect(node)
    bridge.tick()

    assert not node.subscribes(topics.XTOUCH_MIDI)


def test_using_your_own_device_sends_nothing_to_the_network(node, clock):
    """USB 가 꽂힌 PC 가 직접 쓰는 평소 상태다."""
    bridge = _bridge(node, clock)
    _connect(node)
    bridge.set_target('pc1')
    bridge.tick()

    assert not node.subscribes(topics.XTOUCH_MIDI)
    assert node.sent(topics.GROUP_MIDI) == []


# --------------------------------------------------------------------- #
# 넘겨주기 · 주인은 장치를 든 PC 하나다
# --------------------------------------------------------------------- #

def test_only_the_pc_holding_the_device_can_hand_it_over(node, clock):
    """넘겨받는 쪽에서 가로챌 수 있으면 같은 페이더를 둘이 민다."""
    bridge = _bridge(node, clock, pc_id='pc2')
    node.deliver(topics.GROUP_MIDI_FEEDBACK, _grant(owned=True))

    with pytest.raises(ValueError):
        bridge.set_target('pc2')


def test_giving_it_away_opens_the_local_midi(node, clock):
    bridge = _bridge(node, clock)
    _connect(node)
    bridge.set_target('pc2')
    bridge.tick()

    assert node.subscribes(topics.XTOUCH_MIDI)
    assert bridge.rules.should_send_midi


def test_what_goes_out_carries_who_it_is_from_and_for(node, clock):
    bridge = _bridge(node, clock)
    _connect(node)
    bridge.set_target('pc2')
    bridge.tick()

    node.deliver(topics.XTOUCH_MIDI, _midi(channel=7))
    node.deliver(topics.XTOUCH_MIDI, _midi(channel=9))

    sent = node.sent(topics.GROUP_MIDI)
    assert [message.sequence for message in sent] == [1, 2]
    assert sent[0].group_id == 'test1'
    assert sent[0].source_pc_id == 'pc1'
    assert sent[0].target_pc_id == 'pc2'
    # 장치 상태를 그대로 담는다 · 베껴 적으면 규격이 바뀔 때 갈린다
    assert list(sent[1].midi.channel) == [9]


def test_unplugging_the_device_ends_the_relay(node, clock):
    """대상만 남겨 두면 다시 꽂는 순간 아무도 안 눌렀는데 흘러 나간다."""
    bridge = _bridge(node, clock)
    _connect(node)
    bridge.set_target('pc2')
    bridge.tick()

    _connect(node, connected=False)
    bridge.tick()

    assert not node.subscribes(topics.XTOUCH_MIDI)
    assert bridge.rules.target_pc_id == ''


# --------------------------------------------------------------------- #
# 받기 · 대상 PC 에서
# --------------------------------------------------------------------- #

def test_the_received_midi_goes_out_locally_untouched(node, clock):
    """받는 쪽 `midi_control` 이 보기에 USB 를 꽂은 것과 같아야 한다."""
    bridge = _bridge(node, clock, pc_id='pc2')
    node.deliver(topics.GROUP_MIDI_FEEDBACK, _grant(owned=True))
    incoming = _incoming(channel=11)

    node.deliver(topics.GROUP_MIDI, incoming)

    local = node.sent(topics.XTOUCH_MIDI)
    assert len(local) == 1
    assert local[0] is incoming.midi


def test_the_device_owner_is_learned_from_what_arrives(node, clock):
    """이 PC 에는 장치가 없다 · 어디 있는지는 오는 값이 알려 준다."""
    bridge = _bridge(node, clock, pc_id='pc2')
    node.deliver(topics.GROUP_MIDI_FEEDBACK, _grant(owned=True))

    node.deliver(topics.GROUP_MIDI, _incoming())

    assert bridge.rules.device_pc_id == 'pc1'
    assert bridge.rules.is_target
    assert bridge.rules.relaying


def test_midi_addressed_to_someone_else_is_ignored(node, clock):
    """받으면 엉뚱한 PC 의 모터가 움직인다."""
    bridge = _bridge(node, clock, pc_id='pc2')
    node.deliver(topics.GROUP_MIDI_FEEDBACK, _grant(owned=True))

    node.deliver(topics.GROUP_MIDI, _incoming(target='pc3'))
    node.deliver(topics.GROUP_MIDI, _incoming(group='다른그룹'))

    assert node.sent(topics.XTOUCH_MIDI) == []


def test_my_own_message_coming_back_is_ignored(node, clock):
    bridge = _bridge(node, clock, pc_id='pc2')
    node.deliver(topics.GROUP_MIDI_FEEDBACK, _grant(owned=True))

    node.deliver(topics.GROUP_MIDI, _incoming(source='pc2', target='pc2'))

    assert node.sent(topics.XTOUCH_MIDI) == []


def test_a_late_old_value_is_dropped(node, clock):
    """200Hz 최선형이라 순서가 뒤집힌다 · 옛 값을 쓰면 모터가 뒤로 갔다 온다."""
    bridge = _bridge(node, clock, pc_id='pc2')
    node.deliver(topics.GROUP_MIDI_FEEDBACK, _grant(owned=True))

    node.deliver(topics.GROUP_MIDI, _incoming(sequence=5))
    node.deliver(topics.GROUP_MIDI, _incoming(sequence=4))
    node.deliver(topics.GROUP_MIDI, _incoming(sequence=6))

    assert _channels(node.sent(topics.XTOUCH_MIDI)) == [[1], [1]]
    assert bridge.snapshot()['counters']['midi_dropped'] == 1


# --------------------------------------------------------------------- #
# 통로를 그대로 나른다 · 중간에서 판단하지 않는다
# --------------------------------------------------------------------- #

def _channel(channel, payload, source='pc1', target='pc2', group='test1', sequence=1):
    message = GroupMidiChannel()
    message.group_id = group
    message.source_pc_id = source
    message.target_pc_id = target
    message.sequence = sequence
    message.channel = channel
    message.payload = payload
    return message


def test_the_device_channels_go_with_the_midi(node, clock):
    """원시 MIDI 만 나르면 받는 PC 는 값이 들어오는데도 "장치가 없다" 고 안다 ·
    그래서 녹화가 막히고 SELECT 가 이상했다 · USB 선이 나르던 것을 그대로
    나른다."""
    bridge = _bridge(node, clock)
    _connect(node)
    bridge.set_target('pc2')
    bridge.tick()

    node.deliver(topics.XTOUCH_CONNECTION_STATE, String(data='{"connected":true}'))
    node.deliver(topics.XTOUCH_INPUT_STATE, String(data='{"physical_touch":[true]}'))

    sent = node.sent(topics.GROUP_MIDI_FEEDBACK)
    channels = [message.channel for message in sent]
    assert 'connection_state' in channels, '장치가 붙었다는 사실이 안 간다'
    assert 'input_state' in channels, '지금 눌렸다는 사실이 안 간다'
    assert sent[0].target_pc_id == 'pc2'
    # 내용은 손대지 않는다
    assert '{"connected":true}' in [message.payload for message in sent]


def test_only_the_target_sends_the_surface_back(node, clock):
    """물리 장치는 한 대뿐이다 · **권한을 받은 PC 하나만** 조작을 되돌린다."""
    bridge = _bridge(node, clock, pc_id='pc2')

    node.deliver(topics.XTOUCH_FEEDBACK, String(data='{"fader":1}'))
    assert node.sent(topics.GROUP_MIDI_FEEDBACK) == [], '권한도 없이 되돌린다'

    node.deliver(topics.GROUP_MIDI_FEEDBACK, _grant(owned=True))
    node.deliver(topics.GROUP_MIDI, _incoming())
    node.deliver(topics.XTOUCH_FEEDBACK, String(data='{"fader":1}'))
    node.deliver(topics.XTOUCH_CONNECTION_COMMAND, String(data='{"connect":true}'))

    sent = node.sent(topics.GROUP_MIDI_FEEDBACK)
    assert [message.channel for message in sent] == ['feedback', 'connection_command']
    assert all(message.target_pc_id == 'pc1' for message in sent), '장치를 든 PC 로'


def test_the_device_pc_unwraps_the_returning_surface(node, clock):
    bridge = _bridge(node, clock)
    _connect(node)
    bridge.set_target('pc2')
    bridge.tick()

    node.deliver(
        topics.GROUP_MIDI_FEEDBACK,
        _channel('feedback', '{"fader":3}', source='pc2', target='pc1'),
    )

    assert [m.data for m in node.sent(topics.XTOUCH_FEEDBACK)] == ['{"fader":3}']


def test_a_pc_without_the_device_never_unwraps_the_surface(node, clock):
    bridge = _bridge(node, clock, pc_id='pc2')
    node.deliver(topics.GROUP_MIDI_FEEDBACK, _grant(owned=True))

    node.deliver(
        topics.GROUP_MIDI_FEEDBACK,
        _channel('feedback', '{"fader":3}', source='pc3', target='pc2'),
    )

    assert node.sent(topics.XTOUCH_FEEDBACK) == []


# --------------------------------------------------------------------- #
# 끊김 · 기억을 지운다
# --------------------------------------------------------------------- #

def test_a_dead_stream_clears_the_memory(node, clock):
    """안 지우면 장치가 아직 남에게 있다고 믿어, 아무도 못 받는 페이더 명령을
    계속 내보낸다."""
    bridge = _bridge(node, clock, pc_id='pc2')
    node.deliver(topics.GROUP_MIDI_FEEDBACK, _grant(owned=True))
    node.deliver(topics.GROUP_MIDI, _incoming())
    assert bridge.rules.relaying

    clock.advance(2.0)
    bridge.tick()

    assert bridge.rules.device_pc_id == ''
    assert bridge.rules.target_pc_id == ''
    node.deliver(topics.XTOUCH_FEEDBACK, String(data='{"fader":1}'))
    assert node.sent(topics.GROUP_MIDI_FEEDBACK) == []


def test_a_new_stream_starts_counting_again(node, clock):
    """번호를 안 지우면 새 연결의 첫 값이 지난 큰 번호에 막혀 통째로 버려진다."""
    bridge = _bridge(node, clock, pc_id='pc2')
    node.deliver(topics.GROUP_MIDI_FEEDBACK, _grant(owned=True))
    node.deliver(topics.GROUP_MIDI, _incoming(sequence=900))

    clock.advance(2.0)
    bridge.tick()
    node.deliver(topics.GROUP_MIDI, _incoming(sequence=1, channel=4))

    assert _channels(node.sent(topics.XTOUCH_MIDI)) == [[1], [4]]


def test_a_brief_gap_is_not_a_disconnect(node, clock):
    bridge = _bridge(node, clock, pc_id='pc2')
    node.deliver(topics.GROUP_MIDI_FEEDBACK, _grant(owned=True))
    node.deliver(topics.GROUP_MIDI, _incoming())

    clock.advance(0.3)
    bridge.tick()

    assert bridge.rules.relaying


# --------------------------------------------------------------------- #
# 넘기면 이 PC 는 장치를 놓는다 · §6-94
# --------------------------------------------------------------------- #

def test_the_target_can_ask_the_device_pc_to_reconnect(node, clock):
    """받은 PC 에서 `MIDI 재연결` 을 누르면 장치가 꽂힌 PC 로 가야 한다 · §6-94

    장치는 남의 USB 에 있다 · 그 PC 의 브리지만 포트를 다시 열 수 있으므로,
    이 요청은 반드시 건너가야 한다 · 안 가면 받은 PC 에서는 아무 일도 안
    일어난다.
    """
    bridge = _bridge(node, clock, pc_id='pc2')
    node.deliver(topics.GROUP_MIDI_FEEDBACK, _grant(owned=True))
    node.deliver(topics.GROUP_MIDI, _incoming())

    node.deliver(topics.XTOUCH_CONNECTION_COMMAND, String(data='connect'))

    sent = [
        message for message in node.sent(topics.GROUP_MIDI_FEEDBACK)
        if message.channel == 'connection_command'
    ]
    assert sent, '재연결 요청이 장치를 든 PC 로 가지 않았다'
    assert sent[-1].payload == 'connect', '내용이 바뀌었다'
    assert sent[-1].target_pc_id == 'pc1', '장치를 든 PC 가 아닌 곳으로 갔다'


# --------------------------------------------------------------------- #
# 표면 권한 · §6-94
#
# **권한은 장치 연결과 다른 사실이다** · 섞어 놓았더니 재연결 한 번에 서로를
# 덮어써서, 넘긴 PC 가 페이더를 0 으로 밀고 그것이 받은 PC 의 모터까지 0 으로
# 끌고 갔다 · 여기서는 그 두 사실이 **서로를 건드리지 않는지**를 본다.
# --------------------------------------------------------------------- #

def _surface(node):
    """이 PC 가 표면을 쓸 수 있는가 · 마지막으로 낸 말."""
    sent = node.sent(topics.XTOUCH_SURFACE)
    return json.loads(sent[-1].data) if sent else None


def _grants(node):
    """다른 PC 로 나간 권한 전달 · (받는 PC, 줬나)."""
    return [
        (message.target_pc_id, json.loads(message.payload)['owned'])
        for message in node.sent(topics.GROUP_MIDI_FEEDBACK)
        if message.channel == 'owner'
    ]


def _grant(*, owned, source='pc1', target='pc2', connected=True):
    message = GroupMidiChannel()
    message.group_id = 'test1'
    message.source_pc_id = source
    message.target_pc_id = target
    message.channel = 'owner'
    message.payload = json.dumps(
        {'owned': owned, 'device_pc_id': source, 'connected': connected}
    )
    return message


def test_the_relay_never_speaks_on_the_device_channel(node, clock):
    """장치 통로의 주인은 입력 브리지 하나다 · §6-94

    중계가 거기에 끼어들면 진짜 장치가 하는 말과 섞인다 · 그 순간부터 누가
    맞는 말을 했는지 아무도 모른다.
    """
    bridge = _bridge(node, clock)
    _connect(node)
    bridge.set_target('pc2')
    _connect(node, connected=True)
    bridge.set_target('')

    assert node.sent(topics.XTOUCH_CONNECTION_STATE) == [], (
        '중계가 장치인 척했다'
    )


def test_handing_over_closes_this_pcs_surface(node, clock):
    bridge = _bridge(node, clock)
    _connect(node)

    bridge.set_target('pc2')

    assert _surface(node)['owned'] is False, '넘겼는데 표면이 열려 있다'
    assert ('pc2', True) in _grants(node), '받는 PC 에게 권한을 주지 않았다'


def test_taking_it_back_opens_this_pcs_surface(node, clock):
    bridge = _bridge(node, clock)
    _connect(node)
    bridge.set_target('pc2')

    bridge.set_target('')

    assert _surface(node)['owned'] is True, '되돌렸는데 표면이 닫혀 있다'
    assert _grants(node)[-1] == ('pc2', False), '쓰던 PC 에게서 권한을 안 거뒀다'


def test_unplugging_takes_the_permission_back(node, clock):
    """장치가 빠지면 아무도 그 표면을 쓸 수 없다."""
    bridge = _bridge(node, clock)
    _connect(node)
    bridge.set_target('pc2')

    _connect(node, connected=False)

    assert _grants(node)[-1] == ('pc2', False)
    assert _surface(node)['owned'] is False


def test_a_reconnect_cannot_reopen_a_handed_over_surface(node, clock):
    """**이번에 난 문제다** · §6-94

    넘긴 PC 에서 `MIDI 재연결` 을 누르면 브리지가 "붙었다" 고 알린다 · 그것을
    권한으로 읽으면 이 PC 가 표면을 되찾아 페이더를 0 으로 밀고, 그 움직임이
    받은 PC 의 모터를 0 으로 끌고 간다 · 재연결은 **장치 사실만** 바꾼다.
    """
    bridge = _bridge(node, clock)
    _connect(node)
    bridge.set_target('pc2')

    _connect(node, connected=True)

    assert _surface(node)['owned'] is False, '재연결로 표면이 도로 열렸다'
    assert bridge.rules.target_pc_id == 'pc2', '재연결이 주인을 바꿨다'


def test_the_target_opens_its_surface_only_when_granted(node, clock):
    """값이 흐르는 것을 보고 짐작하지 않는다 · 준다고 해야 연다."""
    bridge = _bridge(node, clock, pc_id='pc2')

    node.deliver(topics.GROUP_MIDI, _incoming())
    assert _surface(node) is None or _surface(node)['owned'] is False, (
        '주지도 않았는데 열었다'
    )

    node.deliver(topics.GROUP_MIDI_FEEDBACK, _grant(owned=True))
    assert _surface(node)['owned'] is True
    assert _surface(node)['remote'] is True, '남의 표면인 줄 모른다'
    assert bridge.rules.should_send_feedback, '페이더가 안 돌아간다'


def test_the_target_closes_its_surface_the_moment_it_is_taken_back(node, clock):
    """값이 끊기기를 기다리지 않는다 · 거두면 그때 닫는다."""
    bridge = _bridge(node, clock, pc_id='pc2')
    node.deliver(topics.GROUP_MIDI_FEEDBACK, _grant(owned=True))
    node.deliver(topics.GROUP_MIDI, _incoming())
    node.deliver(topics.GROUP_MIDI_FEEDBACK, _grant(owned=True))

    node.deliver(topics.GROUP_MIDI_FEEDBACK, _grant(owned=False))

    assert _surface(node)['owned'] is False
    assert bridge.rules.should_send_feedback is False


def test_my_own_empty_bridge_cannot_close_a_borrowed_surface(node, clock):
    """받은 PC 에는 장치가 없다 · 제 브리지는 "없다" 고 말할 수밖에 없다.

    그 말은 **내 USB 이야기**일 뿐, 내가 빌려 쓰는 표면과 무관하다 · 전에는
    받은 PC 에서 `MIDI 재연결` 을 누르면 이 말 때문에 쓰던 장치를 놓았다.
    """
    bridge = _bridge(node, clock, pc_id='pc2')
    node.deliver(topics.GROUP_MIDI_FEEDBACK, _grant(owned=True))
    node.deliver(topics.GROUP_MIDI, _incoming())
    node.deliver(topics.GROUP_MIDI_FEEDBACK, _grant(owned=True))

    _connect(node, connected=False)

    assert _surface(node)['owned'] is True, '남의 표면을 제 브리지가 닫았다'
    assert _surface(node)['connected'] is True


def test_a_dead_remote_surface_is_reported_as_dead(node, clock):
    """빌려 쓰는 표면이 죽으면 그것은 알아야 한다."""
    bridge = _bridge(node, clock, pc_id='pc2')
    node.deliver(topics.GROUP_MIDI_FEEDBACK, _grant(owned=True))
    node.deliver(topics.GROUP_MIDI, _incoming())
    node.deliver(topics.GROUP_MIDI_FEEDBACK, _grant(owned=True))

    node.deliver(
        topics.GROUP_MIDI_FEEDBACK,
        _channel('connection_state', '{"connected":false}'),
    )

    assert _surface(node)['connected'] is False
    assert _surface(node)['owned'] is True, '죽었다고 권한까지 사라지진 않는다'


def test_the_permission_is_repeated_so_a_late_pc_can_still_hear_it(node, clock):
    """권한 전달은 남지 않는다 · 받는 PC 가 그때 없었으면 못 듣는다 · §6-94

    되살아나는 길이 없으면 그 PC 에서는 MIDI 가 아무것도 안 되고, 장치를 든
    PC 에서 다시 누르는 것 말고는 방법이 없다.
    """
    bridge = _bridge(node, clock)
    _connect(node)
    bridge.set_target('pc2')
    first = len(_grants(node))

    bridge.tick()
    assert len(_grants(node)) == first, '0.1 초마다 보내면 낭비다'

    clock.advance(1.5)
    bridge.tick()

    assert _grants(node)[-1] == ('pc2', True), '다시 말해 주지 않는다'


def test_nothing_is_repeated_once_it_is_taken_back(node, clock):
    """되돌린 뒤에는 조용해야 한다 · 안 그러면 남의 PC 가 계속 열린다."""
    bridge = _bridge(node, clock)
    _connect(node)
    bridge.set_target('pc2')
    bridge.set_target('')
    before = len(_grants(node))

    clock.advance(5.0)
    bridge.tick()

    assert len(_grants(node)) == before, '되돌렸는데 권한을 계속 준다'


def test_a_resumed_stream_revives_the_surface_at_once(node, clock):
    """잠깐 끊겼다 다시 오면 **첫 값에** 되살아나야 한다 · §6-94

    되풀이되는 권한 알림(1초)을 기다리면 그 동안 표면이 죽은 채로 있고,
    되살아나는 순간 SELECT 가 꺼진다 · 값이 온다는 것은 저쪽 장치가 살아
    있다는 뜻이다.
    """
    bridge = _bridge(node, clock, pc_id='pc2')
    node.deliver(topics.GROUP_MIDI_FEEDBACK, _grant(owned=True))
    node.deliver(topics.GROUP_MIDI_FEEDBACK, _grant(owned=True))
    node.deliver(topics.GROUP_MIDI, _incoming())
    assert _surface(node)['connected'] is True

    clock.advance(2.0)
    bridge.tick()
    assert _surface(node)['connected'] is False, '끊겼는데 살아 있다고 한다'

    node.deliver(topics.GROUP_MIDI, _incoming(sequence=99))

    assert _surface(node)['connected'] is True, '값이 왔는데 죽은 채로 있다'


# --------------------------------------------------------------------- #
# 통로마다 쓰는 쪽이 **한 번에 하나** · §6-94
#
# 장치 통로에는 쓰는 쪽이 둘이다 · 입력 브리지(진짜 장치)와 중계(건너온 값) ·
# 이것 자체는 의도한 설계다 · 받는 PC 의 `midi_control` 이 보기에 USB 를 직접
# 꽂은 것과 구별되지 않아야 하기 때문이다.
#
# **성립 조건은 하나다** · 둘이 동시에 쓰지 않는 것 · 누가 쓰느냐는 권한
# (`xtouch/surface`)이 정하고, 그 권한 통로에는 쓰는 쪽이 하나뿐이다 ·
# 지금까지 난 버그는 전부 권한을 **같은 통로에서 읽어** 서로를 덮어쓴 것이었다.
#
# 아래 검사들은 그 배타성을 통로마다 직접 본다 · 하나씩 터질 때마다 찾는 대신
# 조건 자체를 박아 둔다.
# --------------------------------------------------------------------- #

def _wrote_local(node, channel):
    """이 PC 의 로컬 통로로 중계가 무언가 썼나."""
    return bool(node.sent(_channel_topic(channel)))


def test_the_device_holder_never_writes_relayed_values_to_its_own_surface(node, clock):
    """장치를 든 PC 에서는 **브리지만** 쓴다 · 중계는 되뿌릴 것이 없다."""
    bridge = _bridge(node, clock)
    _connect(node)
    bridge.set_target('pc2')
    bridge.tick()

    # 넘겨준 PC 에게 남이 보낸 장치 값이 와도 받지 않는다
    node.deliver(topics.GROUP_MIDI, _incoming(source='pc2', target='pc1'))
    node.deliver(
        topics.GROUP_MIDI_FEEDBACK,
        _channel('input_state', '{"physical_touch":[true]}', source='pc2'),
    )

    assert node.sent(topics.XTOUCH_MIDI) == [], '중계가 제 표면에 값을 썼다'
    assert not _wrote_local(node, 'input_state'), '중계가 제 표면에 값을 썼다'


def test_the_relay_has_no_way_to_write_the_device_state(node, clock):
    """"안 쓴다" 를 규칙으로 두면 언젠가 누가 쓴다 · 수단을 없애 둔다."""
    bridge = _bridge(node, clock)

    assert 'connection_state' not in bridge._local_channel_pub, (
        '장치 상태를 쓸 수단이 남아 있다'
    )


def test_only_one_side_may_write_each_channel_in_each_state(node, clock):
    """권한이 바뀌면 쓰는 쪽도 바뀐다 · 겹치는 순간이 없어야 한다."""
    # 장치를 들고 직접 쓰는 중 · 되돌려 받을 조작이 없다
    holder = _bridge(node, clock)
    _connect(node)
    assert holder.rules.should_send_midi is False, '안 넘겼는데 내보낸다'
    assert holder.owns_surface is True

    # 넘긴 뒤 · 내 표면은 닫히고, 되돌아오는 조작만 받는다
    holder.set_target('pc2')
    assert holder.owns_surface is False, '넘겼는데 표면이 열려 있다'
    assert holder.rules.should_send_midi is True
    assert holder.rules.should_send_feedback is False, '넘긴 PC 가 조작을 되돌린다'

    # 받은 쪽 · 내 표면이 열리고, 값을 내보내지는 않는다
    other = FakeNode()
    target = _bridge(other, clock, pc_id='pc2')
    other.deliver(topics.GROUP_MIDI_FEEDBACK, _grant(owned=True))
    other.deliver(topics.GROUP_MIDI, _incoming())
    assert target.owns_surface is True
    assert target.rules.should_send_midi is False, '받은 PC 가 값을 내보낸다'
    assert target.rules.should_send_feedback is True


def test_a_borrowed_surface_is_released_when_the_lease_stops(node, clock):
    """거두는 말을 못 들어도 스스로 놓는다 · §6-94

    주는 말만 되풀이하고 거두는 말은 한 번뿐이면, 그 한 번을 놓친 PC 는 영영
    제가 주인인 줄 안다 · 빌려준 PC 의 조정 노드를 다시 시작했더니 실제로
    그랬다 · **양쪽이 모두 "내가 주인"** 이라고 했다.
    """
    bridge = _bridge(node, clock, pc_id='pc2')
    node.deliver(topics.GROUP_MIDI_FEEDBACK, _grant(owned=True))
    assert _surface(node)['owned'] is True

    clock.advance(1.0)
    bridge.tick()
    assert _surface(node)['owned'] is True, '한 번 늦었다고 놓아 버렸다'

    clock.advance(5.0)
    bridge.tick()

    assert _surface(node)['owned'] is False, '갱신이 끊겼는데 아직 주인이라 한다'
    assert bridge.rules.should_send_feedback is False


def test_a_renewed_lease_keeps_the_surface(node, clock):
    """갱신이 계속 오면 계속 쓴다."""
    bridge = _bridge(node, clock, pc_id='pc2')
    node.deliver(topics.GROUP_MIDI_FEEDBACK, _grant(owned=True))

    for _ in range(5):
        clock.advance(1.0)
        node.deliver(topics.GROUP_MIDI_FEEDBACK, _grant(owned=True))
        bridge.tick()

    assert _surface(node)['owned'] is True, '갱신이 오는데 놓았다'
