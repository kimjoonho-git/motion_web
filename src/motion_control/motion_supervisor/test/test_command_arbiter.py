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
