"""Non-blocking local runtime status monitor for the DDS coordinator."""

from __future__ import annotations

import copy
import threading
import time
from typing import Any, Callable, Dict, Mapping


class LocalRuntimeMonitor:
    """Poll loopback state outside the ROS executor and retain the latest sample."""

    def __init__(
        self,
        fetch: Callable[[], Mapping[str, Any]],
        *,
        active_interval_sec: float = 0.05,
        idle_interval_sec: float = 0.5,
    ) -> None:
        self._fetch = fetch
        self._active_interval_sec = max(float(active_interval_sec), 0.01)
        self._idle_interval_sec = max(
            float(idle_interval_sec), self._active_interval_sec
        )
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._closed = threading.Event()
        self._active = False
        self._active_since_monotonic = 0.0
        self._status: Dict[str, Any] = {}
        self._received_monotonic = 0.0
        self._error = ''
        self._thread = threading.Thread(
            target=self._run,
            name='motion-coordination-local-runtime-monitor',
            daemon=True,
        )

    def start(self) -> None:
        self._thread.start()

    def set_active(self, active: bool) -> None:
        active = bool(active)
        with self._lock:
            changed = active != self._active
            self._active = active
            if changed:
                self._active_since_monotonic = (
                    time.monotonic() if active else 0.0
                )
        if changed:
            self._wake.set()

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return {
                'status': copy.deepcopy(self._status),
                'received_monotonic': self._received_monotonic,
                'active': self._active,
                'active_since_monotonic': self._active_since_monotonic,
                'error': self._error,
            }

    def close(self) -> None:
        self._closed.set()
        self._wake.set()
        if self._thread.is_alive():
            self._thread.join(timeout=2.0)

    def poll_once(self) -> None:
        """Fetch one sample; public for deterministic unit tests."""
        try:
            value = self._fetch()
            status = dict(value) if isinstance(value, Mapping) else {}
            if status.get('bridge_state') != 'ok':
                raise ValueError('로컬 Web Bridge 상태 형식 오류')
        except Exception as exc:  # loopback monitor safety boundary
            with self._lock:
                self._error = str(exc)
            return
        with self._lock:
            self._status = status
            self._received_monotonic = time.monotonic()
            self._error = ''

    def _run(self) -> None:
        while not self._closed.is_set():
            self.poll_once()
            with self._lock:
                interval = (
                    self._active_interval_sec
                    if self._active else self._idle_interval_sec
                )
            self._wake.wait(interval)
            self._wake.clear()
