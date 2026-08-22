"""사설 요청·응답 채널의 공통 부분 단일 구현.

`std_msgs/String` + JSON으로 주고받는 요청·응답이 네 곳에서 같은 형태로 반복된다.

    request_id 발급 → 발행 → 콜백에서 dict 저장 → 폴링 대기 → 만료 항목 정리

반복되던 코드가 조금씩 어긋나 있었다.

- 만료 주기 · 10초와 20초가 섞여 있었다
- 만료 기준 시각 · 발신자가 채운 `stamp`와 수신 시각(`_received_at`)이 섞여 있었다
  발신자 시계에 의존하면 PC 간 시계 차이만큼 결과가 일찍 버려진다
- 폴링 간격 · 10ms와 20ms
- 시계 · `time.time()`(벽시계)과 `time.monotonic()`
  벽시계는 NTP 보정으로 뒤로 갈 수 있어 대기가 즉시 끝나거나 길어진다

이 모듈은 수신 시각 기준 만료 · 단조 시계 대기로 통일한다.

이것은 **전송 계약을 바꾸지 않는다.** 토픽 이름과 페이로드 형식은 그대로이며,
Service/Action 전환은 별도 단계다(로드맵 6단계).
"""

from __future__ import annotations

import threading
import time
import uuid
from typing import Any, Callable, Dict, Optional

__all__ = ['DEFAULT_POLL_INTERVAL_SEC', 'DEFAULT_TTL_SEC', 'ResultStore', 'new_request_id']

#: 응답을 보관하는 기본 시간 · 이보다 오래된 항목은 대기자가 없다고 보고 버린다
DEFAULT_TTL_SEC = 20.0

#: 폴링 간격 · 응답 지연과 CPU 점유의 절충
DEFAULT_POLL_INTERVAL_SEC = 0.01


def new_request_id(prefix: str = '') -> str:
    """요청 식별자를 발급한다."""
    token = uuid.uuid4().hex
    return f'{prefix}-{token}' if prefix else token


class ResultStore:
    """``request_id``로 응답을 모으고 기다리는 저장소.

    스레드 안전하다. 콜백 스레드가 :meth:`store`로 넣고, 요청 스레드가
    :meth:`wait`로 꺼낸다.

    한 번 꺼낸 응답은 사라진다. 같은 ``request_id``를 두 번 기다리지 않는다는
    전제이며, 이는 요청·응답 1:1 계약과 일치한다.
    """

    def __init__(
        self,
        *,
        ttl_sec: float = DEFAULT_TTL_SEC,
        poll_interval_sec: float = DEFAULT_POLL_INTERVAL_SEC,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._ttl_sec = float(ttl_sec)
        self._poll_interval_sec = float(poll_interval_sec)
        self._clock = clock
        self._sleep = sleep
        self._lock = threading.Lock()
        self._results: Dict[str, Any] = {}
        self._received_at: Dict[str, float] = {}

    # ----------------------------------------------------------------- #
    # 저장 · 회수
    # ----------------------------------------------------------------- #

    def store(self, request_id: str, payload: Any) -> bool:
        """응답을 보관한다. ``request_id``가 비어 있으면 무시하고 ``False``."""
        key = str(request_id or '')
        if not key:
            return False
        now = self._clock()
        with self._lock:
            self._results[key] = payload
            self._received_at[key] = now
            self._purge_locked(now)
        return True

    def take(self, request_id: str) -> Optional[Any]:
        """보관된 응답을 꺼낸다. 없으면 ``None``."""
        key = str(request_id or '')
        if not key:
            return None
        with self._lock:
            self._received_at.pop(key, None)
            return self._results.pop(key, None)

    def wait(self, request_id: str, timeout_sec: float) -> Optional[Any]:
        """응답이 올 때까지 기다렸다가 꺼낸다. 시간 내에 없으면 ``None``.

        마감 직전에 도착한 응답을 놓치지 않도록 마감 후 한 번 더 확인한다.
        """
        key = str(request_id or '')
        if not key:
            return None

        deadline = self._clock() + max(0.0, float(timeout_sec))
        while self._clock() < deadline:
            result = self.take(key)
            if result is not None:
                return result
            self._sleep(self._poll_interval_sec)
        return self.take(key)

    # ----------------------------------------------------------------- #
    # 정리 · 점검
    # ----------------------------------------------------------------- #

    def purge(self) -> int:
        """만료 항목을 버리고 버린 개수를 돌려준다."""
        now = self._clock()
        with self._lock:
            return self._purge_locked(now)

    def _purge_locked(self, now: float) -> int:
        cutoff = now - self._ttl_sec
        stale = [key for key, at in self._received_at.items() if at < cutoff]
        for key in stale:
            self._results.pop(key, None)
            self._received_at.pop(key, None)
        return len(stale)

    def clear(self) -> None:
        """보관 중인 응답을 모두 버린다 · 프로젝트 전환 등 맥락이 끊길 때."""
        with self._lock:
            self._results.clear()
            self._received_at.clear()

    def pending_count(self) -> int:
        """보관 중인 응답 수 · 진단용."""
        with self._lock:
            return len(self._results)

    def keys(self) -> set:
        """보관 중인 ``request_id`` 집합 · 진단용."""
        with self._lock:
            return set(self._results)

    def __contains__(self, request_id: object) -> bool:
        with self._lock:
            return str(request_id) in self._results
