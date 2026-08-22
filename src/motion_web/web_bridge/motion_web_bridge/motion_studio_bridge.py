"""ROS request/response transport for Motion Studio."""

from __future__ import annotations

import json
import threading
from typing import Any, Dict, Optional

from std_msgs.msg import String


class MotionStudioRosBridge:
    def __init__(self, bridge: Any) -> None:
        self.bridge = bridge

    def status_callback(self, msg: String) -> None:
        bridge = self.bridge
        try:
            payload = json.loads(msg.data)
        except json.JSONDecodeError:
            bridge.get_logger().warn(
                f'Invalid {bridge.motion_studio_status_topic} JSON received.'
            )
            return
        if (
            isinstance(payload, dict)
            and bridge._payload_matches_selected_project(payload)
        ):
            with bridge._motion_studio_lock:
                bridge._motion_studio_status = payload

    def response_callback(self, msg: String) -> None:
        bridge = self.bridge
        try:
            payload = json.loads(msg.data)
        except json.JSONDecodeError:
            bridge.get_logger().warn(
                f'Invalid {bridge.motion_studio_response_topic} JSON received.'
            )
            return
        if not isinstance(payload, dict):
            return
        request_id = str(payload.get('request_id') or '')
        if request_id and bridge._response_matches_current_generation(payload):
            bridge._motion_studio_store.store(request_id, payload)

    def editor_response_callback(self, msg: String) -> None:
        bridge = self.bridge
        try:
            payload = json.loads(msg.data)
        except json.JSONDecodeError:
            bridge.get_logger().warn(
                f'Invalid {bridge.motion_studio_editor_response_topic} JSON received.'
            )
            return
        if not isinstance(payload, dict):
            return
        request_id = str(payload.get('request_id') or '')
        if request_id and bridge._response_matches_current_generation(payload):
            bridge._motion_studio_editor_store.store(request_id, payload)

    def wait_for_result(
        self, request_id: str, timeout_sec: float = 3.0
    ) -> Optional[Dict[str, Any]]:
        return self.bridge._motion_studio_store.wait(request_id, timeout_sec)

    def wait_for_editor_result(
        self, request_id: str, timeout_sec: float = 4.0
    ) -> Optional[Dict[str, Any]]:
        return self.bridge._motion_studio_editor_store.wait(request_id, timeout_sec)

    def request(
        self,
        command: str,
        payload: Optional[Dict[str, Any]] = None,
        timeout_sec: float = 4.0,
        start_generation: Optional[int] = None,
    ) -> Dict[str, Any]:
        bridge = self.bridge
        request_id = bridge._new_project_request_id('studio')
        project_generation = bridge._current_project_generation()
        msg = String()
        request_payload = dict(payload) if isinstance(payload, dict) else {}
        request_payload['project_id'] = (
            bridge.project_repository.selected_project_id()
        )
        request_payload['project_generation'] = project_generation
        if command in {'record', 'play'}:
            request_payload['context_id'] = bridge._execution_context_id()
        msg.data = json.dumps({
            'request_id': request_id,
            'project_generation': project_generation,
            'command': command,
            'payload': request_payload,
        }, ensure_ascii=False)
        if start_generation is None:
            bridge._motion_studio_request_publisher.publish(msg)
        else:
            with bridge._motion_studio_start_order_lock():
                if start_generation != bridge._motion_studio_start_generation:
                    return {
                        'success': False,
                        'start_cancelled': True,
                        'message': (
                            '모션 스튜디오 시작 요청이 정지 또는 '
                            '더 최근 동작 요청으로 취소되었습니다'
                        ),
                    }
                bridge._motion_studio_request_publisher.publish(msg)
        result = bridge._wait_for_motion_studio_result(
            request_id, timeout_sec=timeout_sec
        )
        if result is None:
            with bridge._motion_studio_lock:
                cached = dict(bridge._motion_studio_status)
            return {
                'success': False,
                'message': 'motion_studio_node 응답 시간 초과',
                'status': cached,
            }
        status = result.get('status') if isinstance(result, dict) else None
        if isinstance(status, dict):
            with bridge._motion_studio_lock:
                bridge._motion_studio_status = dict(status)
        return result

    def request_editor(
        self,
        command: str,
        payload: Optional[Dict[str, Any]] = None,
        timeout_sec: float = 8.0,
    ) -> Dict[str, Any]:
        bridge = self.bridge
        request_id = bridge._new_project_request_id('studio-editor')
        project_generation = bridge._current_project_generation()
        msg = String()
        msg.data = json.dumps({
            'request_id': request_id,
            'project_generation': project_generation,
            'command': command,
            'payload': {
                **(dict(payload) if isinstance(payload, dict) else {}),
                'project_generation': project_generation,
            },
        }, ensure_ascii=False)
        bridge._motion_studio_editor_request_publisher.publish(msg)
        result = bridge._wait_for_motion_studio_editor_result(
            request_id, timeout_sec
        )
        return result or {
            'success': False,
            'message': 'motion_studio_editor_node 응답 시간 초과',
        }

    def cancel_pending_start(self) -> int:
        bridge = self.bridge
        with self.start_order_lock():
            bridge._motion_studio_start_generation += 1
            return bridge._motion_studio_start_generation

    def start_order_lock(self) -> threading.Lock:
        bridge = self.bridge
        lock = getattr(bridge, '_motion_studio_command_order_lock', None)
        if lock is None:
            lock = threading.Lock()
            bridge._motion_studio_command_order_lock = lock
        if not hasattr(bridge, '_motion_studio_start_generation'):
            bridge._motion_studio_start_generation = 0
        return lock
