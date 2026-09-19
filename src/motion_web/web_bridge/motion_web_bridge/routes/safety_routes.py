"""정지 길 · 받아서 넘기기만 한다 · §6-191

업무는 `safety_service.SafetyService` 가 한다 · 전에는 이 파일 안에
「그룹도 세울까」 판단과 정지 한 벌이 들어 있어서 길 하나당 32줄이었다.

여기서 하는 일은 하나다 — **이벤트 루프 밖으로 옮겨 부르기** (§6-146) ·
정지는 로컬 노드에 HTTP 로도 묻기 때문에, 루프 안에서 그대로 하면 그 동안
웹 서버가 통째로 멈춘다.
"""

from __future__ import annotations

import asyncio

from fastapi import FastAPI

from motion_web_bridge.safety_service import SafetyService


def register_safety_routes(app: FastAPI, bridge) -> None:
    service = SafetyService(bridge)

    @app.post('/api/safety/motion-stop')
    async def safety_motion_stop():
        return await asyncio.to_thread(
            service.stop,
            emergency=False,
            kind='전체 동작 정지',
            message='전체 동작 정지 명령 우선 전송 완료',
        )

    @app.post('/api/safety/emergency-stop')
    async def safety_emergency_stop():
        return await asyncio.to_thread(
            service.stop,
            emergency=True,
            kind='긴급 정지',
            message='긴급정지 명령 우선 전송 완료',
        )
