"""Serialized command execution for DDS group commands."""

from __future__ import annotations

import itertools
import queue
import threading
from typing import Any, Callable


class CommandDispatcher:
    """Serialize normal commands while safety commands use a dedicated lane."""

    _STOP_PRIORITY = 0
    _NORMAL_PRIORITY = 10
    _CLOSE_PRIORITY = -1

    def __init__(self, handler: Callable[[Any], None]) -> None:
        self._handler = handler
        self._queue: queue.PriorityQueue[tuple[int, int, Any]] = (
            queue.PriorityQueue()
        )
        self._sequence = itertools.count()
        self._closed = threading.Event()
        self._urgent_queue: queue.PriorityQueue[tuple[int, int, Any]] = (
            queue.PriorityQueue()
        )
        self._thread = threading.Thread(
            target=self._run,
            args=(self._queue,),
            name='motion-coordination-command-dispatcher',
            daemon=True,
        )
        self._urgent_thread = threading.Thread(
            target=self._run,
            args=(self._urgent_queue,),
            name='motion-coordination-safety-dispatcher',
            daemon=True,
        )

    def start(self) -> None:
        self._thread.start()
        self._urgent_thread.start()

    def submit(self, command: Any, *, urgent_stop: bool = False) -> bool:
        if self._closed.is_set():
            return False
        target = self._urgent_queue if urgent_stop else self._queue
        priority = self._STOP_PRIORITY if urgent_stop else self._NORMAL_PRIORITY
        target.put((priority, next(self._sequence), command))
        return True

    def close(self) -> None:
        if self._closed.is_set():
            return
        self._closed.set()
        self._queue.put((self._CLOSE_PRIORITY, next(self._sequence), None))
        self._urgent_queue.put(
            (self._CLOSE_PRIORITY, next(self._sequence), None)
        )
        if self._thread.is_alive():
            self._thread.join(timeout=2.0)
        if self._urgent_thread.is_alive():
            self._urgent_thread.join(timeout=2.0)

    def _run(
        self, command_queue: queue.PriorityQueue[tuple[int, int, Any]],
    ) -> None:
        while True:
            _priority, _sequence, command = command_queue.get()
            try:
                if command is None:
                    return
                self._handler(command)
            finally:
                command_queue.task_done()
