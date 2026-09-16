from motion_supervisor.command_arbiter import CommandArbiter, CommandOwner


class FakeClock:
    def __init__(self):
        self.now = 10.0

    def __call__(self):
        return self.now


def test_normal_owner_cannot_be_stolen():
    arbiter = CommandArbiter()

    acquired, owner = arbiter.acquire(CommandOwner.MANUAL)
    assert acquired is True
    assert owner is CommandOwner.MANUAL

    acquired, owner = arbiter.acquire(CommandOwner.PLAYBACK, lease_sec=0.15)
    assert acquired is False
    assert owner is CommandOwner.MANUAL


def test_streaming_owner_refreshes_then_expires():
    clock = FakeClock()
    arbiter = CommandArbiter(clock=clock)

    assert arbiter.acquire(CommandOwner.MIDI, lease_sec=0.15)[0] is True
    clock.now += 0.10
    assert arbiter.acquire(CommandOwner.MIDI, lease_sec=0.15)[0] is True
    clock.now += 0.14
    assert arbiter.snapshot().owner is CommandOwner.MIDI
    clock.now += 0.02
    assert arbiter.snapshot().owner is CommandOwner.NONE


def test_release_only_accepts_current_owner():
    arbiter = CommandArbiter()
    arbiter.acquire(CommandOwner.MANUAL)

    assert arbiter.release(CommandOwner.MIDI) is False
    assert arbiter.snapshot().owner is CommandOwner.MANUAL
    assert arbiter.release(CommandOwner.MANUAL) is True
    assert arbiter.snapshot().owner is CommandOwner.NONE


def test_safety_revoke_clears_any_owner_immediately():
    arbiter = CommandArbiter()
    arbiter.acquire(CommandOwner.PLAYBACK, lease_sec=1.0)

    assert arbiter.revoke_all() is CommandOwner.PLAYBACK
    assert arbiter.snapshot().owner is CommandOwner.NONE


# 축별 소유 · §6-72
#
# 전에는 최종 출력 전체에 주인이 하나였다 · 재생이 잡으면 MIDI 는 어느 축도 쓸 수
# 없었고, 그래서 오버더빙(녹화된 축은 재생이 몰고 나머지는 MIDI 로 녹화)이
# 불가능했다.


def test_different_axes_can_have_different_owners():
    """오버더빙의 핵심 · 재생이 0번을 몰 때 MIDI 가 1번을 쓸 수 있어야 한다."""
    arbiter = CommandArbiter()

    assert arbiter.acquire(CommandOwner.PLAYBACK, axes=[0], lease_sec=0.15)[0] is True
    assert arbiter.acquire(CommandOwner.MIDI, axes=[1], lease_sec=0.15)[0] is True
    assert arbiter.owner_of(0) is CommandOwner.PLAYBACK
    assert arbiter.owner_of(1) is CommandOwner.MIDI


def test_the_same_axis_still_cannot_be_stolen():
    arbiter = CommandArbiter()
    arbiter.acquire(CommandOwner.PLAYBACK, axes=[0], lease_sec=0.15)

    acquired, blocker = arbiter.acquire(CommandOwner.MIDI, axes=[0], lease_sec=0.15)
    assert acquired is False
    assert blocker is CommandOwner.PLAYBACK


def test_a_partial_overlap_fails_as_a_whole():
    """일부만 얻으면 그 축들만 움직여 동작이 반쪽이 된다 · 전부 실패시킨다."""
    arbiter = CommandArbiter()
    arbiter.acquire(CommandOwner.PLAYBACK, axes=[0], lease_sec=0.15)

    acquired, blocker = arbiter.acquire(CommandOwner.MIDI, axes=[1, 0], lease_sec=0.15)
    assert acquired is False
    assert blocker is CommandOwner.PLAYBACK
    assert arbiter.owner_of(1) is CommandOwner.NONE


def test_axis_free_blocks_a_blanket_request():
    """축을 지정하지 않은 요청은 전체를 혼자 쓰겠다는 뜻 · 남이 하나라도 쥐면 막힌다."""
    arbiter = CommandArbiter()
    arbiter.acquire(CommandOwner.MIDI, axes=[3], lease_sec=0.15)

    acquired, blocker = arbiter.acquire(CommandOwner.MANUAL)
    assert acquired is False
    assert blocker is CommandOwner.MIDI


def test_a_blanket_owner_blocks_every_axis():
    arbiter = CommandArbiter()
    arbiter.acquire(CommandOwner.MANUAL)

    assert arbiter.acquire(CommandOwner.MIDI, axes=[7], lease_sec=0.15)[0] is False
    assert arbiter.owner_of(7) is CommandOwner.MANUAL


def test_per_axis_leases_expire_independently():
    clock = FakeClock()
    arbiter = CommandArbiter(clock=clock)
    arbiter.acquire(CommandOwner.PLAYBACK, axes=[0], lease_sec=0.15)
    clock.now += 0.10
    arbiter.acquire(CommandOwner.MIDI, axes=[1], lease_sec=0.15)

    clock.now += 0.06          # 0 번만 만료
    assert arbiter.owner_of(0) is CommandOwner.NONE
    assert arbiter.owner_of(1) is CommandOwner.MIDI


def test_release_frees_only_that_owner():
    arbiter = CommandArbiter()
    arbiter.acquire(CommandOwner.PLAYBACK, axes=[0], lease_sec=1.0)
    arbiter.acquire(CommandOwner.MIDI, axes=[1], lease_sec=1.0)

    assert arbiter.release(CommandOwner.MIDI) is True
    assert arbiter.owner_of(0) is CommandOwner.PLAYBACK
    assert arbiter.owner_of(1) is CommandOwner.NONE


def test_safety_revoke_clears_every_axis():
    """정지는 축을 가리지 않는다."""
    arbiter = CommandArbiter()
    arbiter.acquire(CommandOwner.PLAYBACK, axes=[0], lease_sec=1.0)
    arbiter.acquire(CommandOwner.MIDI, axes=[1], lease_sec=1.0)

    assert arbiter.revoke_all() is not CommandOwner.NONE
    assert arbiter.owner_of(0) is CommandOwner.NONE
    assert arbiter.owner_of(1) is CommandOwner.NONE


# 축별 주인 표 · §6-106
#
# `snapshot()` 은 대표 하나로 줄인 축약형이다 · 무엇을 계속할지 정하는 데 쓰면
# MIDI 가 축 하나만 잡아도 대표가 바뀌어, 다른 축을 몰던 재생이 스스로 멈춘다.


def test_axis_owners_lists_each_axis_separately():
    arbiter = CommandArbiter()
    arbiter.acquire(CommandOwner.PLAYBACK, axes=[1], lease_sec=1.0)
    arbiter.acquire(CommandOwner.MIDI, axes=[0], lease_sec=1.0)

    assert arbiter.axis_owners() == {'1': 'playback', '0': 'midi'}


def test_axis_owners_puts_a_blanket_owner_in_the_all_slot():
    arbiter = CommandArbiter()
    arbiter.acquire(CommandOwner.MANUAL)

    assert arbiter.axis_owners() == {'all': 'manual'}


def test_axis_owners_drops_expired_leases():
    clock = FakeClock()
    arbiter = CommandArbiter(clock=clock)
    arbiter.acquire(CommandOwner.MIDI, axes=[2], lease_sec=0.15)

    clock.now += 0.16
    assert arbiter.axis_owners() == {}


def test_owns_any_sees_playback_even_when_midi_is_the_dominant_owner():
    """대표가 MIDI 여도 재생은 축 하나를 몰고 있다 · 이걸 못 보면 재생이 죽는다."""
    arbiter = CommandArbiter()
    arbiter.acquire(CommandOwner.MIDI, axes=[0], lease_sec=1.0)
    arbiter.acquire(CommandOwner.PLAYBACK, axes=[1], lease_sec=1.0)

    assert arbiter.owns_any(CommandOwner.PLAYBACK) is True
    assert arbiter.owns_any(CommandOwner.MIDI) is True
    assert arbiter.owns_any(CommandOwner.MANUAL) is False


def test_owns_any_is_false_after_the_lease_expires():
    clock = FakeClock()
    arbiter = CommandArbiter(clock=clock)
    arbiter.acquire(CommandOwner.PLAYBACK, axes=[1], lease_sec=0.15)

    assert arbiter.owns_any(CommandOwner.PLAYBACK) is True
    clock.now += 0.16
    assert arbiter.owns_any(CommandOwner.PLAYBACK) is False
