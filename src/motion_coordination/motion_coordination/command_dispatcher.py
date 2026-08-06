"""Serialized command execution for DDS group commands."""

from __future__ import annotations

import queue
import threading
from typing import Any, Callable


class CommandDispatcher:
    """Serialize normal commands while safety commands use a dedicated lane."""

    def __init__(self, handler: Callable[[Any], None]) -> None:
        self._handler = handler
        self._queue: queue.Queue[Any] = queue.Queue()
        self._closed = threading.Event()
        self._urgent_queue: queue.Queue[Any] = queue.Queue()
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
        target.put(command)
        return True

    def close(self) -> None:
        if self._closed.is_set():
            return
        self._closed.set()
        self._queue.put(None)
        self._urgent_queue.put(None)
        if self._thread.is_alive():
            self._thread.join(timeout=2.0)
        if self._urgent_thread.is_alive():
            self._urgent_thread.join(timeout=2.0)

    def _run(
        self, command_queue: queue.Queue[Any],
    ) -> None:
        while True:
            command = command_queue.get()
            try:
                if command is None:
                    return
                if self._closed.is_set():
                    continue
                self._handler(command)
            finally:
                command_queue.task_done()
