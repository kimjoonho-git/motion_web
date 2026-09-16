"""Thread-safe ownership for the final upper-level motor command output."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Dict, Iterable, Optional


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


@dataclass
class _Claim:
    owner: CommandOwner
    acquired_at: float
    expires_at: Optional[float]


#: 축을 지정하지 않은 요청 · 모든 축을 혼자 쓰겠다는 뜻이다.
_ALL = object()


#: 누가 누구의 축을 뺏는가 · 여기 적힌 관계 하나뿐이다.
#:
#: 재생은 MIDI 가 쥔 축을 가져온다. 오버더빙 중에는 MIDI 가 쉬지 않고 값을
#: 흘리고 있어서, 먼저 온 순서대로 주면 **MIDI 가 축을 선점하고 재생이 영영
#: 막힌다** · 모터가 한 번도 안 움직인다. 어느 축을 언제 재생할지는 녹화
#: 데이터가 이미 정해 놓은 것이라 도착 순서로 정할 일이 아니다 · §6-76
#:
#: 수동(MANUAL)은 뺏지 않는다 · 사람이 조그를 쥐고 있는데 재생이 가져가면
#: 위험하다 · 수동과 재생은 그대로 선착순이다.
_PREEMPTS = {
    CommandOwner.PLAYBACK: frozenset({CommandOwner.MIDI}),
}


class CommandArbiter:
    """Allow one normal command source to own each axis at a time.

    Short-lived streaming sources refresh a lease for every accepted command.
    Manual trajectories use a persistent lease and release it when their active
    command tables become empty. Safety code may revoke every owner immediately.

    소유는 **축마다** 나뉜다 · §6-72

    전에는 최종 출력 전체에 주인이 하나였다. 재생이 잡으면 MIDI 는 어느 축도
    쓸 수 없었고, 그래서 오버더빙(녹화된 축은 재생이 몰고 나머지는 MIDI 로
    녹화)이 불가능했다.

    축을 주지 않으면 예전처럼 **전체를 혼자 쓴다** · 기존 호출은 그대로 동작한다.
    """

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.RLock()
        self._claims: Dict[object, _Claim] = {}

    # ----------------------------------------------------------------- #
    # 획득·반납
    # ----------------------------------------------------------------- #

    def acquire(
        self,
        owner: CommandOwner,
        *,
        axes: Optional[Iterable[int]] = None,
        lease_sec: Optional[float] = None,
    ) -> tuple[bool, CommandOwner]:
        """축을 얻는다 · 하나라도 뺏을 수 없는 주인이 쥐고 있으면 **전부 실패한다**.

        일부만 얻으면 그 축들만 움직여 동작이 반쪽이 된다 · 부른 쪽이 왜 막혔는지
        보고 물러설 수 있도록 막은 주인을 함께 돌려준다.

        `_PREEMPTS` 에 적힌 상대는 뺏는다 · 재생은 MIDI 가 쥔 축을 가져온다.
        """
        if owner is CommandOwner.NONE:
            raise ValueError('CommandOwner.NONE cannot acquire ownership')
        if lease_sec is not None and lease_sec <= 0.0:
            raise ValueError('lease_sec must be greater than zero')

        keys = self._keys(axes)
        with self._lock:
            now = self._clock()
            self._expire_locked(now)
            blocker = self._blocker_locked(owner, keys)
            if blocker is not None:
                return False, blocker
            expires_at = None if lease_sec is None else now + lease_sec
            for key in keys:
                claim = self._claims.get(key)
                acquired_at = claim.acquired_at if claim else now
                self._claims[key] = _Claim(owner, acquired_at, expires_at)
            return True, owner

    def release(
        self,
        owner: CommandOwner,
        *,
        axes: Optional[Iterable[int]] = None,
    ) -> bool:
        """이 주인이 쥔 것을 놓는다 · 하나도 쥔 게 없으면 거짓."""
        with self._lock:
            self._expire_locked(self._clock())
            targets = [
                key for key, claim in self._claims.items()
                if claim.owner is owner
                and (axes is None or key in set(self._keys(axes)))
            ]
            for key in targets:
                self._claims.pop(key, None)
            return bool(targets)

    def revoke_all(self) -> CommandOwner:
        """Revoke every owner for motion-stop or emergency-stop."""
        with self._lock:
            self._expire_locked(self._clock())
            previous = self._dominant_locked()
            self._claims.clear()
            return previous

    # ----------------------------------------------------------------- #
    # 조회
    # ----------------------------------------------------------------- #

    def snapshot(self) -> OwnershipSnapshot:
        """대표 주인 하나 · 상태 표시와 옛 호출부를 위한 축약형."""
        with self._lock:
            self._expire_locked(self._clock())
            owner = self._dominant_locked()
            if owner is CommandOwner.NONE:
                return OwnershipSnapshot(owner, 0.0, None)
            claims = [c for c in self._claims.values() if c.owner is owner]
            return OwnershipSnapshot(
                owner=owner,
                acquired_at=min(c.acquired_at for c in claims),
                expires_at=max(
                    (c.expires_at for c in claims),
                    key=lambda value: (value is None, value),
                ),
            )

    def owner_of(self, axis: int) -> CommandOwner:
        """이 축의 현재 주인 · 축별 판정을 부르는 쪽이 쓴다."""
        with self._lock:
            self._expire_locked(self._clock())
            claim = self._claims.get(_ALL) or self._claims.get(int(axis))
            return claim.owner if claim else CommandOwner.NONE

    def axis_owners(self) -> Dict[str, str]:
        """축별 주인 표 · 판정하는 쪽이 이것을 본다 · §6-106

        `snapshot()` 은 **대표 하나로 줄인 축약형**이다 · 화면에 "지금 뭐가
        도나" 를 적는 자리에는 맞지만, **무엇을 계속할지 정하는 데 쓰면 안
        된다**. MIDI 가 축 하나만 잡아도 대표가 `midi` 로 바뀌어, 다른 축을
        몰던 재생이 "MIDI 가 쓰는 중" 이라며 스스로 멈췄다.

        전체를 쥔 주인은 `all` 칸에 넣는다 · 축을 지정하지 않은 옛 호출이
        그렇게 잡는다.
        """
        with self._lock:
            self._expire_locked(self._clock())
            owners: Dict[str, str] = {}
            for key, claim in self._claims.items():
                name = 'all' if key is _ALL else str(int(key))
                owners[name] = claim.owner.value
            return owners

    def owns_any(self, owner: CommandOwner) -> bool:
        """이 주인이 **한 축이라도** 쥐고 있는가 · §6-106

        "재생이 도는 중인가" 같은 물음은 대표 주인으로 답하면 안 된다 ·
        재생이 한 축을 몰고 있어도 MIDI 가 다른 축을 잡으면 대표가 바뀐다.
        """
        with self._lock:
            self._expire_locked(self._clock())
            return any(claim.owner is owner for claim in self._claims.values())

    # ----------------------------------------------------------------- #
    # 내부
    # ----------------------------------------------------------------- #

    @staticmethod
    def _keys(axes: Optional[Iterable[int]]) -> list:
        if axes is None:
            return [_ALL]
        keys = {int(axis) for axis in axes}
        return sorted(keys) if keys else [_ALL]

    def _blocker_locked(
        self, owner: CommandOwner, keys: list,
    ) -> Optional[CommandOwner]:
        """다른 주인이 막고 있으면 그 주인 · 아니면 None.

        축을 지정하지 않은 요청(`_ALL`)은 **모든 축**과 부딪히고, 축을 지정한
        요청도 남이 전체를 쥐고 있으면 막힌다.
        """
        blanket = self._claims.get(_ALL)
        if blanket is not None and self._outranked(owner, blanket.owner):
            return blanket.owner
        if _ALL in keys:
            for claim in self._claims.values():
                if self._outranked(owner, claim.owner):
                    return claim.owner
            return None
        for key in keys:
            claim = self._claims.get(key)
            if claim is not None and self._outranked(owner, claim.owner):
                return claim.owner
        return None

    @staticmethod
    def _outranked(owner: CommandOwner, holder: CommandOwner) -> bool:
        """`holder` 가 `owner` 를 막는가 · 뺏을 수 있는 상대면 막지 못한다."""
        if holder is owner:
            return False
        return holder not in _PREEMPTS.get(owner, frozenset())

    def _dominant_locked(self) -> CommandOwner:
        for claim in self._claims.values():
            return claim.owner
        return CommandOwner.NONE

    def _expire_locked(self, now: float) -> None:
        for key, claim in list(self._claims.items()):
            if claim.expires_at is not None and now >= claim.expires_at:
                self._claims.pop(key, None)
