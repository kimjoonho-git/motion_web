import asyncio
import logging

from fastapi import FastAPI

logger = logging.getLogger("bridge.routes.safety")


def register_safety_routes(app: FastAPI, bridge) -> None:
    def _stop_group_too(kind: str) -> str:
        """그룹이 도는 중이면 그룹도 함께 세운다 · §6-70

        `publish_safety_stop` 은 이 PC 만 세운다. 그룹 실행 중에 "전체 동작 정지"
        를 누르면 이 PC 만 서고 다른 PC 는 계속 돌았다 · 이름이 "전체" 라 더
        위험했다. 참가 PC 이상 감지(`_stop_for_peer_failure`)가 결국 세우기는
        하지만 시간이 걸리고 고장으로 기록된다.

        정지는 역할과 무관하게 누구나 할 수 있어야 하므로 마스터를 요구하지
        않는다 · 시작만 마스터로 제한한다.
        """
        service = getattr(bridge, '_coordination_web_bridge', None)
        if service is None:
            return ''
        if not service.local_execution_blocker():
            return ''          # 그룹 실행 중이 아니면 로컬 정지로 충분하다
        try:
            service.request_control({'command': 'stop_now'})
            return ' · 그룹 실행도 함께 정지 요청'
        except Exception as exc:
            logger.error('%s · 그룹 정지 요청 실패 · %s', kind, exc, exc_info=True)
            return ' · 그룹 정지 요청 실패 · 연동 화면에서 확인하세요'

    def _stop_blocking(emergency: bool, kind: str, message: str) -> dict:
        """정지 한 벌 · **이벤트 루프 밖에서** 돈다 · §6-146

        `_stop_group_too` 는 로컬 연동 노드에 HTTP 로 묻는다 · 이벤트 루프에서
        그대로 하면 그 동안 웹 서버가 통째로 멈추고, 연동 노드가 50ms 마다
        묻는 `local-status` 가 굶어 **정지시키려다 그룹 고장을 만든다**.
        """
        cancel_pending = getattr(bridge, 'cancel_pending_motion_studio_start', None)
        if callable(cancel_pending):
            cancel_pending()
        request_id = bridge.publish_safety_stop(emergency)
        group_note = _stop_group_too(kind)
        return {
            'success': True,
            'message': message + group_note,
            'request_id': request_id,
            'acknowledgement_pending': True,
        }

    @app.post('/api/safety/motion-stop')
    async def safety_motion_stop():
        return await asyncio.to_thread(
            _stop_blocking, False, '전체 동작 정지',
            '전체 동작 정지 명령 우선 전송 완료',
        )

    @app.post('/api/safety/emergency-stop')
    async def safety_emergency_stop():
        return await asyncio.to_thread(
            _stop_blocking, True, '긴급 정지',
            '긴급정지 명령 우선 전송 완료',
        )
