"""ROS request/response transport used by Motion Studio operations."""

from __future__ import annotations

import time
from typing import Any, Dict


class StudioRosGateway:
    """Publish correlated requests and collect generation-matched responses."""

    def __init__(self, studio: Any) -> None:
        self.studio = studio

    def accept_run_response(self, payload: Any) -> None:
        self._accept_response(payload, self.studio._run_results)

    def accept_midi_response(self, payload: Any) -> None:
        self._accept_response(payload, self.studio._midi_results)

    def _accept_response(
        self, payload: Any, result_store: Dict[str, Dict[str, Any]]
    ) -> None:
        studio = self.studio
        request_id = (
            str(payload.get('request_id') or '')
            if isinstance(payload, dict)
            else ''
        )
        if request_id and studio._response_generation_matches(payload):
            with studio._lock:
                result_store[request_id] = payload

    def request_run(
        self, command: str, payload: Dict[str, Any], timeout: float
    ) -> Dict[str, Any]:
        studio = self.studio
        generation = studio._context_generation()
        request_id = f'studio-run-g{generation}-{time.time_ns()}'
        studio._publish_json(studio._request_pub, {
            'request_id': request_id,
            'project_generation': generation,
            'command': command,
            'payload': {**payload, 'project_generation': generation},
        })
        return self.wait_for_run_result(request_id, timeout)

    def request_run_for_operation(
        self,
        command: str,
        payload: Dict[str, Any],
        timeout: float,
        operation_generation: int,
        expected_state: str,
    ) -> Dict[str, Any]:
        """Publish atomically only while the originating operation is active."""
        studio = self.studio
        generation = studio._context_generation()
        request_id = f'studio-run-g{generation}-{time.time_ns()}'
        with studio._lock:
            if not studio._operation_machine().is_active(
                operation_generation,
                str(studio._status.get('state') or ''),
                expected_state,
            ):
                return {
                    'success': False,
                    'message': '사용자가 모션 동작을 정지했습니다',
                }
            studio._publish_json(studio._request_pub, {
                'request_id': request_id,
                'project_generation': generation,
                'command': command,
                'payload': {
                    **payload,
                    'project_generation': generation,
                    'operation_generation': operation_generation,
                },
            })
        return self.wait_for_run_result(request_id, timeout)

    def wait_for_run_result(
        self, request_id: str, timeout: float
    ) -> Dict[str, Any]:
        result = self._wait_for_result(
            self.studio._run_results, request_id, timeout
        )
        if result is not None:
            return result
        return {
            'success': False,
            'message': 'motion_run_manager 응답 시간 초과',
        }

    def request_midi(
        self, command: str, payload: Dict[str, Any], timeout: float
    ) -> Dict[str, Any]:
        studio = self.studio
        generation = studio._context_generation()
        request_id = f'studio-midi-g{generation}-{time.time_ns()}'
        request_payload = dict(payload)
        request_payload['project_id'] = studio._workspace_project_id
        request_payload['project_generation'] = generation
        studio._publish_json(studio._midi_request_pub, {
            'request_id': request_id,
            'project_generation': generation,
            'command': command,
            'payload': request_payload,
        })
        result = self._wait_for_result(
            studio._midi_results, request_id, timeout
        )
        if result is not None:
            return result
        return {
            'success': False,
            'message': 'midi_control_node 응답 시간 초과',
        }

    def _wait_for_result(
        self,
        result_store: Dict[str, Dict[str, Any]],
        request_id: str,
        timeout: float,
    ) -> Dict[str, Any] | None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self.studio._lock:
                result = result_store.pop(request_id, None)
            if result is not None:
                return result
            time.sleep(0.02)
        return None
