"""Loopback-only adapter between the DDS process and the local Web Bridge."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Mapping


MAX_BODY_BYTES = 32 * 1024


class LocalCoordinationApi:
    def __init__(
        self,
        status: Callable[[], Mapping[str, Any]],
        control: Callable[[Mapping[str, Any]], Mapping[str, Any]],
        *,
        port: int = 8011,
    ) -> None:
        self._status = status
        self._control = control
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802 - stdlib handler contract
                if self.path != '/status':
                    self._send(404, {'success': False, 'message': 'Not Found'})
                    return
                self._send(200, {'success': True, **dict(owner._status())})

            def do_POST(self) -> None:  # noqa: N802 - stdlib handler contract
                if self.path != '/control':
                    self._send(404, {'success': False, 'message': 'Not Found'})
                    return
                try:
                    length = int(self.headers.get('Content-Length') or 0)
                    if length <= 0 or length > MAX_BODY_BYTES:
                        raise ValueError('로컬 그룹 요청 크기가 올바르지 않습니다')
                    value = json.loads(self.rfile.read(length).decode('utf-8'))
                    if not isinstance(value, dict):
                        raise ValueError('로컬 그룹 요청은 객체여야 합니다')
                    result = dict(owner._control(value))
                except (OSError, UnicodeError, ValueError) as exc:
                    self._send(400, {'success': False, 'message': str(exc)})
                    return
                except Exception as exc:  # coordinator safety boundary
                    self._send(500, {'success': False, 'message': str(exc)})
                    return
                self._send(200, result)

            def log_message(self, _format: str, *_args: Any) -> None:
                return

            def _send(self, status: int, payload: Mapping[str, Any]) -> None:
                body = json.dumps(
                    dict(payload), ensure_ascii=False, separators=(',', ':'),
                ).encode('utf-8')
                self.send_response(status)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.send_header('Content-Length', str(len(body)))
                self.send_header('Cache-Control', 'no-store')
                self.end_headers()
                self.wfile.write(body)

        self._server = ThreadingHTTPServer(('127.0.0.1', int(port)), Handler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name='motion-coordination-local-api',
            daemon=True,
        )

    @property
    def port(self) -> int:
        return int(self._server.server_address[1])

    def start(self) -> None:
        self._thread.start()

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        if self._thread.is_alive():
            self._thread.join(timeout=2.0)
