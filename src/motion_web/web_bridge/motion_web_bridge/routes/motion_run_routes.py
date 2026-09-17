import asyncio

from fastapi import FastAPI, HTTPException, Request

from motion_web_bridge import motion_file_analysis


def register_motion_run_routes(app: FastAPI, bridge, safety_first_stop) -> None:
    @app.get('/api/motion-files')
    async def motion_files():
        return await asyncio.to_thread(
            motion_file_analysis.list_motion_files,
            bridge.project_repository,
            bridge.motion_projects_dir,
        )

    @app.get('/api/motion-files/{file_id}')
    async def motion_file(file_id: str):
        return await asyncio.to_thread(
            motion_file_analysis.load_motion_file,
            bridge.project_repository,
            bridge.motion_projects_dir,
            file_id,
        )

    @app.delete('/api/motion-files/{file_id}')
    async def delete_motion_file(file_id: str):
        return bridge.delete_motion_file(file_id)

    @app.get('/api/motion-mappings')
    async def motion_mappings():
        return bridge.list_motion_mappings()

    @app.post('/api/motion-mappings')
    async def save_motion_mapping(request: Request):
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail='request body must be an object')
        return bridge.save_motion_mapping(body)

    @app.post('/api/motion-mappings/validate')
    async def validate_motion_mapping(request: Request):
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail='request body must be an object')
        return bridge.validate_motion_mapping(body)

    @app.get('/api/motion-mappings/{file_id}')
    async def motion_mapping(file_id: str):
        return bridge.load_motion_mapping(file_id)

    @app.delete('/api/motion-mappings/{file_id}')
    async def delete_motion_mapping(file_id: str):
        return bridge.delete_motion_mapping(file_id)

    @app.get('/api/motion-run/status')
    async def motion_run_status():
        return bridge.motion_run_status()

    @app.post('/api/motion-run/check')
    async def motion_run_check(request: Request):
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail='request body must be an object')
        handler = bridge.motion_run_check
        return await asyncio.to_thread(handler, body)

    @app.post('/api/motion-run/initialize')
    async def motion_run_initialize(request: Request):
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail='request body must be an object')
        handler = bridge.motion_run_initialize
        return await asyncio.to_thread(handler, body)

    @app.post('/api/motion-run/start')
    async def motion_run_start(request: Request):
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail='request body must be an object')
        handler = bridge.motion_run_start
        return await asyncio.to_thread(handler, body)

    @app.put('/api/motion-run/automation')
    async def motion_automation_configure(request: Request):
        try:
            body = await request.json()
        except Exception:
            body = {}
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail='request body must be an object')
        try:
            handler = bridge.motion_automation_configure
            return await asyncio.to_thread(handler, body)
        except Exception as exc:
            import traceback
            trace = traceback.format_exc()
            bridge.get_logger().error(f'motion_automation_configure API error: {trace}')
            return {'success': False, 'message': f'서버 내부 오류: {exc}'}

    async def _optional_body(request: Request) -> dict:
        """스케줄이 보내면 `schedule_id` 가 들어 있다 · 화면에서 누르면 없다.

        누가 멈췄는지 추측하지 않기 위해 본문을 받는다 · 본문이 없어도 된다.
        """
        try:
            body = await request.json()
        except Exception:
            return {}
        return body if isinstance(body, dict) else {}

    @app.post('/api/motion-run/stop')
    async def motion_run_stop(request: Request):
        payload = await _optional_body(request)
        return await asyncio.to_thread(
            safety_first_stop, bridge, lambda: bridge.motion_run_stop(payload),
        )

    @app.post('/api/motion-run/stop-after-cycle')
    async def motion_run_stop_after_cycle_api(request: Request):
        payload = await _optional_body(request)
        return await asyncio.to_thread(bridge.motion_run_stop_after_cycle, payload)
