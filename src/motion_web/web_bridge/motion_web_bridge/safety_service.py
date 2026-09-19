"""정지 · 길목이 아니라 여기서 한다 · §6-191

**이 저장소에서 가장 중요한 코드가 길목 파일 안에 있었다.**

`safety_routes.py` 가 64줄에 길은 둘뿐이라 길 하나당 32줄이었다 · 그 안에
「그룹도 함께 세울까」 판단과 정지 한 벌이 들어 있어서, **정지를 시험하려면
HTTP 를 거쳐야 했다.**

정지는 눌러서 확인하기 어려운 기능이다 · 눌러 보려면 실제로 장비를 세워야
하고, 세우고 나면 프로그램을 다시 시작해야 한다 · 그러니 더더욱 HTTP 없이
시험할 수 있어야 한다.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

logger = logging.getLogger('bridge.safety')

#: 정지 요청을 보내고 붙이는 꼬리말 · 화면이 이 말을 그대로 보여준다
GROUP_STOP_SENT = ' · 그룹 실행도 함께 정지 요청'
GROUP_STOP_FAILED = ' · 그룹 정지 요청 실패 · 연동 화면에서 확인하세요'


class SafetyService:
    """전체 동작 정지와 긴급 정지 · 이 PC 를 세우고, 필요하면 그룹도 세운다."""

    def __init__(self, bridge: Any) -> None:
        self.bridge = bridge

    def stop(self, *, emergency: bool, kind: str, message: str) -> Dict[str, Any]:
        """정지 한 벌 · §6-146

        **이벤트 루프 밖에서 부른다** · `_stop_group_too` 가 로컬 노드에
        HTTP 로 묻기 때문이다 · 루프 안에서 그대로 하면 그 동안 웹 서버가
        통째로 멈추고, 50ms 마다 오는 `local-status` 조회가 굶어
        **정지시키려다 고장을 만든다** · 그래서 길목이 스레드로 옮겨 부른다.

        **순서가 정해져 있다.**

            1  예약된 스튜디오 시작을 취소한다 (아직 안 뜬 것을 막는다)
            2  이 PC 에 정지를 쏜다        ← 제일 급한 일
            3  그다음 그룹을 챙긴다

        2번을 먼저 하는 이유 · 3번은 HTTP 왕복이라 느리다 · 그 사이 이 PC 가
        계속 돌면 안 된다.
        """
        cancel_pending = getattr(self.bridge, 'cancel_pending_motion_studio_start', None)
        if callable(cancel_pending):
            cancel_pending()
        request_id = self.bridge.publish_safety_stop(emergency)
        return {
            'success': True,
            'message': message + self._stop_group_too(kind),
            'request_id': request_id,
            # 쏘기만 했다 · 실제로 섰는지는 뒤따라오는 상태로 확인한다
            'acknowledgement_pending': True,
        }

    def _stop_group_too(self, kind: str) -> str:
        """그룹이 도는 중이면 그룹도 함께 세운다 · §6-70

        `publish_safety_stop` 은 **이 PC 만** 세운다 · 그룹 실행 중에 「전체
        동작 정지」를 누르면 이 PC 만 서고 다른 PC 는 계속 돌았다 · 이름이
        「전체」라 더 위험했다 · 참가 PC 이상 감지가 결국 세우기는 하지만
        시간이 걸리고 고장으로 기록된다.

        **정지는 누구나 할 수 있어야 한다** · 마스터를 요구하지 않는다 ·
        시작만 마스터로 제한한다.

        실패해도 **예외를 올리지 않는다** · 이 PC 는 이미 섰고, 그것이 가장
        급한 일이다 · 그룹을 못 세운 사실은 문구로 알린다.
        """
        service = getattr(self.bridge, 'coordination', None)
        if service is None:
            return ''
        if not service.local_execution_blocker():
            return ''          # 그룹 실행 중이 아니면 로컬 정지로 충분하다
        try:
            service.request_control({'command': 'stop_now'})
            return GROUP_STOP_SENT
        except Exception as exc:
            logger.error('%s · 그룹 정지 요청 실패 · %s', kind, exc, exc_info=True)
            return GROUP_STOP_FAILED
