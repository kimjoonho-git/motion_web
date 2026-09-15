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
from motion_coordination.midi_relay_bridge import MidiRelayBridge


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
    incoming = _incoming(channel=11)

    node.deliver(topics.GROUP_MIDI, incoming)

    local = node.sent(topics.XTOUCH_MIDI)
    assert len(local) == 1
    assert local[0] is incoming.midi


def test_the_device_owner_is_learned_from_what_arrives(node, clock):
    """이 PC 에는 장치가 없다 · 어디 있는지는 오는 값이 알려 준다."""
    bridge = _bridge(node, clock, pc_id='pc2')

    node.deliver(topics.GROUP_MIDI, _incoming())

    assert bridge.rules.device_pc_id == 'pc1'
    assert bridge.rules.is_target
    assert bridge.rules.relaying


def test_midi_addressed_to_someone_else_is_ignored(node, clock):
    """받으면 엉뚱한 PC 의 모터가 움직인다."""
    bridge = _bridge(node, clock, pc_id='pc2')

    node.deliver(topics.GROUP_MIDI, _incoming(target='pc3'))
    node.deliver(topics.GROUP_MIDI, _incoming(group='다른그룹'))

    assert node.sent(topics.XTOUCH_MIDI) == []


def test_my_own_message_coming_back_is_ignored(node, clock):
    bridge = _bridge(node, clock, pc_id='pc2')

    node.deliver(topics.GROUP_MIDI, _incoming(source='pc2', target='pc2'))

    assert node.sent(topics.XTOUCH_MIDI) == []


def test_a_late_old_value_is_dropped(node, clock):
    """200Hz 최선형이라 순서가 뒤집힌다 · 옛 값을 쓰면 모터가 뒤로 갔다 온다."""
    bridge = _bridge(node, clock, pc_id='pc2')

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


def test_the_received_channels_are_republished_untouched(node, clock):
    """받는 PC 가 제 것으로 판단할 수 있게, 온 것을 그대로 내보낸다."""
    bridge = _bridge(node, clock, pc_id='pc2')
    node.deliver(topics.GROUP_MIDI, _incoming())

    node.deliver(
        topics.GROUP_MIDI_FEEDBACK,
        _channel('connection_state', '{"connected":true}'),
    )

    local = node.sent(topics.XTOUCH_CONNECTION_STATE)
    assert [message.data for message in local] == ['{"connected":true}']


def test_only_the_target_sends_the_surface_back(node, clock):
    """물리 장치는 한 대뿐이다 · 대상 PC 하나만 조작을 되돌린다."""
    bridge = _bridge(node, clock, pc_id='pc2')

    node.deliver(topics.XTOUCH_FEEDBACK, String(data='{"fader":1}'))
    assert node.sent(topics.GROUP_MIDI_FEEDBACK) == []

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
    node.deliver(topics.GROUP_MIDI, _incoming(sequence=900))

    clock.advance(2.0)
    bridge.tick()
    node.deliver(topics.GROUP_MIDI, _incoming(sequence=1, channel=4))

    assert _channels(node.sent(topics.XTOUCH_MIDI)) == [[1], [4]]


def test_a_brief_gap_is_not_a_disconnect(node, clock):
    bridge = _bridge(node, clock, pc_id='pc2')
    node.deliver(topics.GROUP_MIDI, _incoming())

    clock.advance(0.3)
    bridge.tick()

    assert bridge.rules.relaying


# --------------------------------------------------------------------- #
# 넘기면 이 PC 는 장치를 놓는다 · §6-94
# --------------------------------------------------------------------- #

def test_handing_over_releases_the_local_device(node, clock):
    """USB 를 뽑아 옮겨 꽂은 것과 같아야 한다 · 넘겨준 PC 가 계속 모터를
    움직이고 페이더·LED 를 밀면 SELECT 가 이상해진다 · 실제로 그랬다."""
    bridge = _bridge(node, clock)
    _connect(node)

    bridge.set_target('pc2')

    announced = node.sent(topics.XTOUCH_CONNECTION_STATE)
    assert announced, '이 PC 에 아무 말도 안 했다'
    payload = json.loads(announced[-1].data)
    assert payload['connected'] is False, '넘겼는데 장치를 계속 들고 있다'
    assert 'pc2' in payload['message']


def test_taking_it_back_picks_the_device_up_again(node, clock):
    bridge = _bridge(node, clock)
    _connect(node)
    bridge.set_target('pc2')

    bridge.set_target('')

    payload = json.loads(node.sent(topics.XTOUCH_CONNECTION_STATE)[-1].data)
    assert payload['connected'] is True


def test_the_relay_does_not_believe_its_own_announcement(node, clock):
    """그 알림을 중계가 다시 읽으면 장치가 없다고 믿고 스스로 멈춘다."""
    bridge = _bridge(node, clock)
    _connect(node)
    bridge.set_target('pc2')

    # 자기가 낸 알림이 돌아온다
    node.deliver(topics.XTOUCH_CONNECTION_STATE, node.sent(topics.XTOUCH_CONNECTION_STATE)[-1])
    bridge.tick()

    assert bridge.rules.holds_device, '자기 알림에 속아 장치를 놓았다'
    assert bridge.rules.should_send_midi, '중계가 멈췄다'
