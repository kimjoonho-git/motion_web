"""원격 MIDI 연결의 끊김 판정과 복구 대응 · §6-94

USB 대신 네트워크로 MIDI 를 받을 때, 값이 끊기고 다시 오는 순간을 다룬다.

**끊기는 순간은 이미 안전하다** · `midi_control` 이 자기 마지막 값을 50Hz 로
계속 내보내므로 모터는 그 자리에 멈춘다 · 아무것도 안 해도 된다.

**위험한 것은 돌아오는 순간이다** · 끊긴 동안 사용자가 페이더를 움직였다면,
다시 이어지는 순간 모터가 그 자리로 튄다.

판정(끊겼나)과 대응(그래서 뭘 할까)을 나눠 둔다 · 대응은 바뀔 수 있고, 바뀔
때 판정까지 건드리지 않아야 한다.
"""

from __future__ import annotations

import time
from enum import Enum
from typing import Callable, Optional

#: 이만큼 값이 없으면 끊긴 것으로 본다 · 파라미터로 덮을 수 있다
#:
#: 보내는 쪽은 움직임이 없어도 200Hz 로 장치 상태를 계속 흘린다 · 0.5초면
#: 100개를 놓친 것이라 의심의 여지가 없다.
DEFAULT_STALE_TIMEOUT_SEC = 0.5


class RecoveryPolicy(str, Enum):
    """다시 이어졌을 때 무엇을 할 것인가 · 갈아끼울 수 있다."""

    #: SELECT 는 그대로 두고 페이더를 다시 맞춘다 · 맞을 때까지 명령을 막는다
    #: 사용자가 다시 누르지 않아도 되고, 튀지도 않는다 · 지금 고른 것
    RESYNC = 'resync'

    #: SELECT 를 전부 끄고 페이더를 0 으로 · USB 를 뽑았다 꽂은 것과 같다
    #: 가장 안전하지만 사용자가 다시 눌러야 한다
    RELEASE = 'release'

    #: 아무것도 안 한다 · 끊김이 아주 짧다고 확신할 때만
    KEEP = 'keep'


class LinkEvent(str, Enum):
    LOST = 'lost'
    RESTORED = 'restored'


class RemoteMidiLink:
    """값이 오는지 지켜보고, 끊김·복구 순간만 알려 준다.

    이 객체는 **판정만 한다** · 모터에 무엇을 할지는 부르는 쪽이 정책을 보고
    정한다. 그래야 정책을 바꿀 때 판정을 안 건드린다.
    """

    def __init__(
        self,
        *,
        timeout_sec: float = DEFAULT_STALE_TIMEOUT_SEC,
        policy: RecoveryPolicy = RecoveryPolicy.RESYNC,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.timeout_sec = max(0.05, float(timeout_sec))
        self.policy = RecoveryPolicy(policy)
        self._clock = clock
        self._last_seen: Optional[float] = None
        self._connected = False
        #: 한 번이라도 받은 적이 있는가 · 처음 붙는 것과 다시 붙는 것은 다르다
        self._seen_once = False

    # ----------------------------------------------------------------- #
    # 지켜보기
    # ----------------------------------------------------------------- #

    def receive(self) -> Optional[LinkEvent]:
        """원격 MIDI 값이 하나 도착했다 · 상태가 바뀌었으면 알려 준다."""
        self._last_seen = self._clock()
        if self._connected:
            return None
        self._connected = True
        was_seen = self._seen_once
        self._seen_once = True
        # 처음 붙는 것은 복구가 아니다 · 끊긴 적이 없으니 튈 것도 없다
        return LinkEvent.RESTORED if was_seen else None

    def poll(self) -> Optional[LinkEvent]:
        """주기적으로 부른다 · 값이 끊겼으면 알려 준다."""
        if not self._connected or self._last_seen is None:
            return None
        if self._clock() - self._last_seen <= self.timeout_sec:
            return None
        self._connected = False
        return LinkEvent.LOST

    def detach(self) -> None:
        """이 PC 가 더 이상 원격 MIDI 대상이 아니다 · 기억을 지운다.

        지우지 않으면 다음에 대상이 됐을 때 **지난 연결에서 이어진 것처럼**
        보여 엉뚱하게 복구 처리가 돈다.
        """
        self._last_seen = None
        self._connected = False
        self._seen_once = False

    # ----------------------------------------------------------------- #
    # 조회
    # ----------------------------------------------------------------- #

    @property
    def connected(self) -> bool:
        return self._connected

    def age_sec(self) -> Optional[float]:
        """마지막 값이 온 지 얼마나 됐나 · 한 번도 없었으면 None."""
        if self._last_seen is None:
            return None
        return max(0.0, self._clock() - self._last_seen)

    def snapshot(self) -> dict:
        """화면이 읽는 모양 · 왜 안 움직이는지 사용자가 알아야 한다."""
        age = self.age_sec()
        return {
            'connected': self._connected,
            'age_sec': None if age is None else round(age, 3),
            'timeout_sec': self.timeout_sec,
            'recovery_policy': self.policy.value,
        }
