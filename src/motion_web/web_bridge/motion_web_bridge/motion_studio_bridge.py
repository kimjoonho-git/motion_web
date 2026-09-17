"""ROS request/response transport for Motion Studio.

상태는 `MotionStudioSession`이 갖는다. 이 클래스는 전송만 한다 · §6-15
이전에는 노드의 `_wait_for_motion_studio_result` 껍데기를 되불러 순환이 있었다.
지금은 세션의 저장소를 직접 기다린다.
"""

from __future__ import annotations

import json
import threading
from typing import Any, Dict, Optional

from std_msgs.msg import String


#: 스튜디오 요청을 기다리는 시간 · §6-114
#:
#: 10분짜리 모션(30,000프레임)은 저장 1.1초, 읽기 0.7초, 응답 6.5MB 다 ·
#: 4초로는 조금 느린 PC 에서 아슬아슬하다.
#:
#: **시간 초과가 곧 취소는 아니다** · 노드는 하던 일을 끝내고 저장한다 ·
#: 화면에만 "응답 시간 초과" 가 뜨고 실제로는 바뀌어 있는, 가장 나쁜 모양이
#: 된다 · 그래서 넉넉하게 잡는다 · 노드가 정말 죽었을 때만 이 시간을 다 쓴다.
STUDIO_REQUEST_TIMEOUT_SEC = 20.0

#: 포인트 생성·편집은 표본 수에 비례해 오래 걸린다 · 10분 모션의 축 하나에
#: 5초 안팎이고, 느린 PC 는 그 몇 배다.
STUDIO_EDITOR_TIMEOUT_SEC = 60.0


class MotionStudioRosBridge:
    def __init__(
        self, bridge: Any, session: Any, context_id: Any = None, project: Any = None
    ) -> None:
        self.bridge = bridge
        #: 프로젝트 서비스 · 노드를 거치지 않는다 (§6-23)
        self.project = project
        self.session = session
        #: 실행 컨텍스트 식별자를 돌려주는 콜러블 (§6-20)
        self.context_id = context_id or (lambda: '')

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
            and self.project.payload_matches_selected(payload)
        ):
            self.session.replace_status(payload)

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
            self.session.store.store(request_id, payload)

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
            self.session.editor_store.store(request_id, payload)

    def wait_for_result(
        self, request_id: str, timeout_sec: float = STUDIO_REQUEST_TIMEOUT_SEC
    ) -> Optional[Dict[str, Any]]:
        return self.session.store.wait(request_id, timeout_sec)

    def wait_for_editor_result(
        self, request_id: str, timeout_sec: float = STUDIO_EDITOR_TIMEOUT_SEC
    ) -> Optional[Dict[str, Any]]:
        return self.session.editor_store.wait(request_id, timeout_sec)

    def request(
        self,
        command: str,
        payload: Optional[Dict[str, Any]] = None,
        timeout_sec: float = STUDIO_REQUEST_TIMEOUT_SEC,
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
            request_payload['context_id'] = self.context_id()
        msg.data = json.dumps({
            'request_id': request_id,
            'project_generation': project_generation,
            'command': command,
            'payload': request_payload,
        }, ensure_ascii=False)
        if start_generation is None:
            bridge._motion_studio_request_publisher.publish(msg)
        else:
            with self.session.order_lock:
                if start_generation != self.session.start_generation:
                    return {
                        'success': False,
                        'start_cancelled': True,
                        'message': (
                            '모션 스튜디오 시작 요청이 정지 또는 '
                            '더 최근 동작 요청으로 취소되었습니다'
                        ),
                    }
                bridge._motion_studio_request_publisher.publish(msg)
        result = self.wait_for_result(request_id, timeout_sec)
        if result is None:
            cached = self.session.snapshot_status()
            return {
                'success': False,
                'message': 'motion_studio_node 응답 시간 초과',
                'status': cached,
            }
        status = result.get('status') if isinstance(result, dict) else None
        if isinstance(status, dict):
            self.session.replace_status(status)
        return result

    def request_editor(
        self,
        command: str,
        payload: Optional[Dict[str, Any]] = None,
        timeout_sec: float = STUDIO_EDITOR_TIMEOUT_SEC,
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
        result = self.wait_for_editor_result(request_id, timeout_sec)
        return result or {
            'success': False,
            'message': 'motion_studio_editor_node 응답 시간 초과',
        }

    def cancel_pending_start(self) -> int:
        return self.session.next_start_generation()

    def start_order_lock(self) -> threading.Lock:
        return self.session.order_lock
