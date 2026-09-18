"""스케줄 길 · 받아서 넘기기만 한다 · §6-182

업무는 `schedule_service.ScheduleService` 가 한다 · 전에는 이 파일 안에
로직이 여덟 개 있어서 길 하나당 27.7줄이었다 (다른 라우트는 5~10줄).

서비스가 올리는 예외를 HTTP 로 옮기는 것이 이 파일의 일이다.
"""

import asyncio

from fastapi import FastAPI, HTTPException, Request

from motion_web_bridge.schedule_service import (
    ScheduleNotFound,
    ScheduleOwnershipError,
    ScheduleService,
)


def register_schedule_routes(app: FastAPI, bridge, project_call) -> None:
    service = ScheduleService(bridge)

    async def _call(work, *args):
        """서비스를 스레드에서 부르고, 예외를 HTTP 로 옮긴다.

        `ValueError` 는 사용자에게 보일 거부다 (400) · 이 저장소의 규칙이다.
        """
        try:
            return await asyncio.to_thread(work, *args)
        except ScheduleOwnershipError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ScheduleNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.get('/api/schedule/list')
    async def get_schedule_list():
        return await _call(service.list_schedules)

    @app.get('/api/schedule/status')
    async def get_schedule_status():
        return await _call(service.status)

    @app.put('/api/schedule/mode')
    async def set_schedule_mode(request: Request):
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail='request body must be an object')
        return await _call(service.set_run_mode, body.get('run_mode'))

    @app.post('/api/schedule/save')
    async def save_schedule(request: Request):
        return await _call(service.save_schedule, await request.json())

    @app.delete('/api/schedule/{schedule_id}')
    async def delete_schedule(schedule_id: str):
        return await _call(service.delete_schedule, schedule_id)

    @app.post('/api/schedule/{schedule_id}/enable')
    async def enable_schedule(schedule_id: str):
        return await _call(service.set_enabled, schedule_id, True)

    @app.post('/api/schedule/{schedule_id}/disable')
    async def disable_schedule(schedule_id: str):
        return await _call(service.set_enabled, schedule_id, False)
