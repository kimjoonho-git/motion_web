"""원시 MIDI 중계 규칙 · §6-94

MIDI 장치는 한 대뿐이고, 한 번에 한 PC 만 그것을 쓴다 · USB 를 옮겨 꽂는 것과
같되 선을 뽑지 않는다.

잘못 판단하면 두 PC 가 같은 물리 페이더를 밀거나(떨림), 엉뚱한 PC 의 모터가
움직인다.
"""

from types import SimpleNamespace

import pytest

from motion_coordination.midi_relay import MidiRelayRules, SequenceGate


def _rules(pc_id, *, device='', target='', group='g1'):
    rules = MidiRelayRules(pc_id)
    rules.update(group_id=group, device_pc_id=device, target_pc_id=target)
    return rules


def _message(*, group='g1', source='pc1', target='pc2', sequence=1):
    return SimpleNamespace(
        group_id=group, source_pc_id=source, target_pc_id=target,
        sequence=sequence,
    )


# --------------------------------------------------------------------- #
# 장치를 든 PC 와 쓰는 PC
# --------------------------------------------------------------------- #

def test_the_device_holder_sends_when_someone_else_uses_it():
    rules = _rules('pc1', device='pc1', target='pc2')
    assert rules.holds_device is True
    assert rules.should_send_midi is True
    assert rules.should_send_feedback is False, '장치를 든 쪽이 피드백을 만들면 안 된다'


def test_the_user_sends_the_fader_commands_back():
    rules = _rules('pc2', device='pc1', target='pc2')
    assert rules.is_target is True
    assert rules.should_send_feedback is True
    assert rules.should_send_midi is False


def test_a_pc_that_is_neither_does_nothing():
    rules = _rules('pc3', device='pc1', target='pc2')
    assert rules.should_send_midi is False
    assert rules.should_send_feedback is False
    assert rules.accepts_midi(_message()) is False


def test_using_the_device_on_its_own_pc_relays_nothing():
    """USB 가 꽂힌 PC 가 직접 쓰는 평소 상태 · 네트워크로 나갈 일이 없다."""
    rules = _rules('pc1', device='pc1', target='pc1')
    assert rules.relaying is False
    assert rules.should_send_midi is False
    assert rules.should_send_feedback is False


@pytest.mark.parametrize('device,target', [('', 'pc2'), ('pc1', ''), ('', '')])
def test_an_unset_side_relays_nothing(device, target):
    rules = _rules('pc1', device=device, target=target)
    assert rules.relaying is False
    assert rules.should_send_midi is False


def test_without_a_group_nothing_moves():
    rules = _rules('pc1', device='pc1', target='pc2', group='')
    assert rules.relaying is False
    assert rules.accepts_midi(_message(group='')) is False


# --------------------------------------------------------------------- #
# 받을 것인가
# --------------------------------------------------------------------- #

def test_the_target_takes_the_midi_addressed_to_it():
    rules = _rules('pc2', device='pc1', target='pc2')
    assert rules.accepts_midi(_message(target='pc2')) is True


def test_midi_addressed_to_another_pc_is_ignored():
    """남에게 간 MIDI 를 받으면 엉뚱한 PC 의 모터가 움직인다."""
    rules = _rules('pc2', device='pc1', target='pc2')
    assert rules.accepts_midi(_message(target='pc3')) is False


def test_midi_from_another_group_is_ignored():
    rules = _rules('pc2', device='pc1', target='pc2')
    assert rules.accepts_midi(_message(group='other')) is False


def test_my_own_message_coming_back_is_ignored():
    rules = _rules('pc2', device='pc1', target='pc2')
    assert rules.accepts_midi(_message(source='pc2', target='pc2')) is False


def test_only_the_device_holder_takes_the_fader_commands():
    """물리 페이더는 장치가 꽂힌 PC 에만 있다."""
    holder = _rules('pc1', device='pc1', target='pc2')
    assert holder.accepts_feedback(_message(source='pc2', target='pc1')) is True

    other = _rules('pc3', device='pc1', target='pc2')
    assert other.accepts_feedback(_message(source='pc2', target='pc3')) is False


# --------------------------------------------------------------------- #
# 늦게 온 옛 값
# --------------------------------------------------------------------- #

def test_old_values_are_dropped():
    """200Hz 최선형 전송이라 순서가 뒤집힐 수 있다 · 지난 값을 쓰면 모터가
    잠깐 뒤로 갔다 온다."""
    gate = SequenceGate()
    assert gate.accepts(1) is True
    assert gate.accepts(2) is True
    assert gate.accepts(1) is False, '지난 값을 받아들였다'
    assert gate.accepts(2) is False, '같은 값을 두 번 받아들였다'
    assert gate.accepts(3) is True


def test_a_gap_is_fine():
    """놓친 것은 다시 안 보낸다 · 건너뛴 번호는 정상이다."""
    gate = SequenceGate()
    assert gate.accepts(1) is True
    assert gate.accepts(500) is True


def test_a_broken_sequence_is_dropped():
    gate = SequenceGate()
    for bad in (None, 'x', object()):
        assert gate.accepts(bad) is False


def test_a_new_link_starts_counting_again():
    gate = SequenceGate()
    gate.accepts(500)
    gate.reset()
    assert gate.accepts(1) is True, '연결이 새로 섰는데 옛 번호에 막혔다'
