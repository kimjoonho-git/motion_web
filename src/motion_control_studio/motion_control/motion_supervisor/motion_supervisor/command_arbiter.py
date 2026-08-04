"""Thread-safe ownership for the final upper-level motor command output."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Optional


class CommandOwner(str, Enum):
    NONE = 'none'
    MANUAL = 'manual'
    MIDI = 'midi'
    PLAYBACK = 'playback'


@dataclass(frozen=True)
class OwnershipSnapshot:
    owner: CommandOwner
    acquired_at: float
    expires_at: Optional[float]


class CommandArbiter:
    """Allow one normal command source to own the final publisher at a time.

    Short-lived streaming sources refresh a lease for every accepted command.
    Manual trajectories use a persistent lease and release it when their active
    command tables become empty. Safety code may revoke every owner immediately.
    """

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.RLock()
        self._owner = CommandOwner.NONE
        self._acquired_at = 0.0
        self._expires_at: Optional[float] = None

    def acquire(
        self,
        owner: CommandOwner,
        *,
        lease_sec: Optional[float] = None,
    ) -> tuple[bool, CommandOwner]:
        if owner is CommandOwner.NONE:
            raise ValueError('CommandOwner.NONE cannot acquire ownership')
        if lease_sec is not None and lease_sec <= 0.0:
            raise ValueError('lease_sec must be greater than zero')

        with self._lock:
            now = self._clock()
            self._expire_locked(now)
            previous = self._owner
            if previous not in (CommandOwner.NONE, owner):
                return False, previous
            if previous is CommandOwner.NONE:
                self._owner = owner
                self._acquired_at = now
            self._expires_at = None if lease_sec is None else now + lease_sec
            return True, self._owner

    def release(self, owner: CommandOwner) -> bool:
        with self._lock:
            self._expire_locked(self._clock())
            if self._owner is not owner:
                return False
            self._clear_locked()
            return True

    def revoke_all(self) -> CommandOwner:
        """Revoke the current owner for motion-stop or emergency-stop."""
        with self._lock:
            self._expire_locked(self._clock())
            previous = self._owner
            self._clear_locked()
            return previous

    def snapshot(self) -> OwnershipSnapshot:
        with self._lock:
            self._expire_locked(self._clock())
            return OwnershipSnapshot(
                owner=self._owner,
                acquired_at=self._acquired_at,
                expires_at=self._expires_at,
            )

    def _expire_locked(self, now: float) -> None:
        if self._expires_at is not None and now >= self._expires_at:
            self._clear_locked()

    def _clear_locked(self) -> None:
        self._owner = CommandOwner.NONE
        self._acquired_at = 0.0
        self._expires_at = None
