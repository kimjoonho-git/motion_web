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
        return await asyncio.to_thread(bridge.delete_motion_file, file_id)

    @app.get('/api/motion-mappings')
    async def motion_mappings():
        return await asyncio.to_thread(bridge.list_motion_mappings)

    @app.post('/api/motion-mappings')
    async def save_motion_mapping(request: Request):
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail='request body must be an object')
        return await asyncio.to_thread(bridge.save_motion_mapping, body)

    @app.post('/api/motion-mappings/validate')
    async def validate_motion_mapping(request: Request):
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail='request body must be an object')
        return await asyncio.to_thread(bridge.validate_motion_mapping, body)

    @app.post('/api/motion-mappings/motion-file')
    async def save_registered_motion_file(request: Request):
        # 재생 등록만 바꾸는 좁은 길 · 모션축 설정은 안 건드린다 · §6-160
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail='request body must be an object')
        return await asyncio.to_thread(bridge.save_registered_motion_file, body)

    @app.get('/api/motion-mappings/{file_id}')
    async def motion_mapping(file_id: str):
        return await asyncio.to_thread(bridge.load_motion_mapping, file_id)

    @app.delete('/api/motion-mappings/{file_id}')
    async def delete_motion_mapping(file_id: str):
        return await asyncio.to_thread(bridge.delete_motion_mapping, file_id)

    @app.get('/api/motion-run/status')
    async def motion_run_status():
        return await asyncio.to_thread(bridge.motion_run_status)

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

    @app.post('/api/motion-run/stop')
    async def motion_run_stop():
        return await asyncio.to_thread(
            safety_first_stop, bridge, bridge.motion_run_stop,
        )

    @app.post('/api/motion-run/stop-after-cycle')
    async def motion_run_stop_after_cycle_api():
        return await asyncio.to_thread(bridge.motion_run_stop_after_cycle)
