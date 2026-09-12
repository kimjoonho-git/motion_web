"""원격 MIDI 연결의 끊김 판정과 복구 대응 · §6-94

끊기는 순간은 이미 안전하다 · `midi_control` 이 마지막 값을 50Hz 로 계속
내보내므로 모터는 그 자리에 멈춘다.

위험한 것은 **돌아오는 순간**이다 · 끊긴 동안 사용자가 페이더를 움직였다면
다시 이어질 때 모터가 그 자리로 튄다.

판정과 대응은 나뉘어 있어야 한다 · 대응은 바뀔 수 있고, 바뀔 때 판정까지
건드리면 안 된다.
"""

import pytest

from midi_control.remote_midi_link import (
    DEFAULT_STALE_TIMEOUT_SEC,
    LinkEvent,
    RecoveryPolicy,
    RemoteMidiLink,
)


class _Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def _link(**kwargs):
    clock = _Clock()
    return RemoteMidiLink(clock=clock, **kwargs), clock


# --------------------------------------------------------------------- #
# 끊김 판정
# --------------------------------------------------------------------- #

def test_the_first_value_is_not_a_recovery():
    """처음 붙는 것은 복구가 아니다 · 끊긴 적이 없으니 튈 것도 없다."""
    link, _ = _link()
    assert link.receive() is None
    assert link.connected is True


def test_silence_past_the_timeout_is_a_loss():
    link, clock = _link(timeout_sec=0.5)
    link.receive()

    clock.advance(0.4)
    assert link.poll() is None, '아직 끊긴 게 아니다'

    clock.advance(0.2)
    assert link.poll() is LinkEvent.LOST
    assert link.connected is False


def test_a_loss_is_reported_once_not_every_tick():
    """매 주기마다 "끊겼다" 를 던지면 복구 처리가 계속 돈다."""
    link, clock = _link(timeout_sec=0.5)
    link.receive()
    clock.advance(1.0)

    assert link.poll() is LinkEvent.LOST
    assert link.poll() is None
    assert link.poll() is None


def test_a_value_after_a_loss_is_a_recovery():
    link, clock = _link(timeout_sec=0.5)
    link.receive()
    clock.advance(1.0)
    link.poll()

    assert link.receive() is LinkEvent.RESTORED
    assert link.connected is True


def test_a_steady_stream_never_reports_anything():
    """200Hz 로 계속 오는 동안에는 아무 일도 없어야 한다."""
    link, clock = _link(timeout_sec=0.5)
    link.receive()
    for _ in range(400):
        clock.advance(0.005)
        assert link.receive() is None
        assert link.poll() is None


def test_polling_before_any_value_reports_nothing():
    link, clock = _link()
    clock.advance(60.0)
    assert link.poll() is None
    assert link.connected is False


# --------------------------------------------------------------------- #
# 대상에서 빠지면 기억을 지운다
# --------------------------------------------------------------------- #

def test_detaching_forgets_the_previous_link():
    """지우지 않으면 다음에 대상이 됐을 때 지난 연결에서 이어진 것처럼 보여
    엉뚱하게 복구 처리가 돈다."""
    link, clock = _link()
    link.receive()
    clock.advance(1.0)
    link.poll()

    link.detach()

    assert link.connected is False
    assert link.age_sec() is None
    assert link.receive() is None, '대상에서 빠졌다 붙은 것은 복구가 아니다'


# --------------------------------------------------------------------- #
# 정책은 갈아끼울 수 있다 · 판정은 그대로다
# --------------------------------------------------------------------- #

@pytest.mark.parametrize('policy', list(RecoveryPolicy))
def test_the_verdict_does_not_depend_on_the_policy(policy):
    """정책을 바꿔도 "끊겼나 · 돌아왔나" 판정은 똑같아야 한다 · 나중에 정책만
    갈아끼울 수 있어야 한다."""
    link, clock = _link(timeout_sec=0.5, policy=policy)

    assert link.receive() is None
    clock.advance(1.0)
    assert link.poll() is LinkEvent.LOST
    assert link.receive() is LinkEvent.RESTORED
    assert link.policy is policy


def test_the_default_is_what_we_chose():
    link, _ = _link()
    assert link.policy is RecoveryPolicy.RESYNC
    assert link.timeout_sec == DEFAULT_STALE_TIMEOUT_SEC


def test_the_timeout_can_be_changed_but_not_to_nothing():
    """0 으로 두면 한 주기만 늦어도 끊긴 것이 된다 · 바닥을 둔다."""
    link, _ = _link(timeout_sec=3.0)
    assert link.timeout_sec == 3.0

    link, _ = _link(timeout_sec=0.0)
    assert link.timeout_sec >= 0.05


# --------------------------------------------------------------------- #
# 화면이 읽는 모양
# --------------------------------------------------------------------- #

def test_the_snapshot_says_why_nothing_is_moving():
    link, clock = _link(timeout_sec=0.5)
    assert link.snapshot()['connected'] is False
    assert link.snapshot()['age_sec'] is None

    link.receive()
    clock.advance(0.25)
    shot = link.snapshot()
    assert shot['connected'] is True
    assert shot['age_sec'] == 0.25
    assert shot['timeout_sec'] == 0.5
    assert shot['recovery_policy'] == 'resync'
