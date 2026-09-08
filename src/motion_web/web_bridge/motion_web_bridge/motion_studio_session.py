"""모션 스튜디오의 프로젝트 범위 상태 · 단일 소유자.

`MotionWebBridge`가 들고 있던 스튜디오 상태 7개를 한 객체로 모았다. 이전에는
노드 · `MotionStudioRosBridge` · `MotionStudioSync` 세 곳이 같은 필드를 각자
`bridge.___`로 집어 썼고, 전송 계층이 노드의 위임 껍데기를 다시 부르는 순환도
있었다 · `docs/ARCHITECTURE_REVIEW.md` §6-15

노드는 이 객체를 소유하기만 한다. 상태를 읽고 쓰는 쪽이 이 객체를 직접 받는다.
"""

from __future__ import annotations

import threading
from typing import Any, Dict, Optional

from motion_common import rpc


class MotionStudioSession:
    """스튜디오 상태 · 락 · 응답 저장소를 함께 갖는다."""

    def __init__(self) -> None:
        #: `status`를 지키는 락 · `workspace_signatures`도 같은 락 아래 갱신한다
        self.lock = threading.Lock()
        #: 시작 명령의 순서를 지키는 락 · `start_generation`과 짝이다
        self.order_lock = threading.Lock()
        self.status: Dict[str, Any] = {}
        self.workspace_signatures: Dict[str, Dict[str, str]] = {}
        self.start_generation = 0
        self.store = rpc.ResultStore()
        self.editor_store = rpc.ResultStore()

    # -- 상태 읽기 ---------------------------------------------------------

    def snapshot_status(self) -> Dict[str, Any]:
        with self.lock:
            return dict(self.status) if self.status else {}

    def state(self) -> str:
        with self.lock:
            return str((self.status or {}).get('state') or 'idle')

    # -- 상태 쓰기 ---------------------------------------------------------

    def replace_status(self, status: Dict[str, Any]) -> None:
        with self.lock:
            self.status = dict(status)

    def settle_state(self, *, when: str, becomes: str, message: str) -> bool:
        """`when` 상태에 머물러 있으면 `becomes`로 정리한다.

        정지 지시가 끝난 뒤 화면에 `stopping`이 남는 것을 지우는 용도다.
        바꿨으면 참을 돌려준다.
        """
        with self.lock:
            status = self.status or {}
            if str(status.get('state') or '') != when:
                return False
            self.status = {**dict(status), 'state': becomes, 'message': message}
            return True

    # -- 시작 순서 ---------------------------------------------------------

    def next_start_generation(self) -> int:
        """진행 중이던 시작 요청을 무효로 만들고 새 세대를 돌려준다."""
        with self.order_lock:
            self.start_generation += 1
            return self.start_generation

    # -- 프로젝트 전환 -----------------------------------------------------

    def clear_project_memory(self) -> None:
        """프로젝트가 바뀌면 이전 프로젝트의 흔적을 남기지 않는다."""
        self.store.clear()
        self.editor_store.clear()
        with self.lock:
            self.status = {}
            self.workspace_signatures = {}


def session_of(owner: Any) -> Optional[MotionStudioSession]:
    """노드에서 세션을 꺼낸다 · 세션이 없는 테스트 스텁이면 `None`."""
    return getattr(owner, '_motion_studio_session', None)
