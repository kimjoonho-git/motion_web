import asyncio

from fastapi import FastAPI, HTTPException, Request


def register_motion_run_routes(app: FastAPI, bridge, safety_first_stop) -> None:
    def service():
        return getattr(bridge, 'motion_run', bridge)

    @app.get('/api/motion-files')
    async def motion_files():
        return service().list_files() if hasattr(service(), 'list_files') else bridge.list_motion_files()

    @app.get('/api/motion-files/{file_id}')
    async def motion_file(file_id: str):
        return service().load_file(file_id) if hasattr(service(), 'load_file') else bridge.load_motion_file(file_id)

    @app.delete('/api/motion-files/{file_id}')
    async def delete_motion_file(file_id: str):
        return service().delete_file(file_id) if hasattr(service(), 'delete_file') else bridge.delete_motion_file(file_id)

    @app.get('/api/motion-mappings')
    async def motion_mappings():
        return service().list_mappings() if hasattr(service(), 'list_mappings') else bridge.list_motion_mappings()

    @app.post('/api/motion-mappings')
    async def save_motion_mapping(request: Request):
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail='request body must be an object')
        return service().save_mapping(body) if hasattr(service(), 'save_mapping') else bridge.save_motion_mapping(body)

    @app.post('/api/motion-mappings/validate')
    async def validate_motion_mapping(request: Request):
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail='request body must be an object')
        return service().validate_mapping(body) if hasattr(service(), 'validate_mapping') else bridge.validate_motion_mapping(body)

    @app.get('/api/motion-mappings/{file_id}')
    async def motion_mapping(file_id: str):
        return service().load_mapping(file_id) if hasattr(service(), 'load_mapping') else bridge.load_motion_mapping(file_id)

    @app.delete('/api/motion-mappings/{file_id}')
    async def delete_motion_mapping(file_id: str):
        return service().delete_mapping(file_id) if hasattr(service(), 'delete_mapping') else bridge.delete_motion_mapping(file_id)

    @app.get('/api/motion-run/status')
    async def motion_run_status():
        return service().status() if hasattr(service(), 'status') else bridge.motion_run_status()

    @app.post('/api/motion-run/check')
    async def motion_run_check(request: Request):
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail='request body must be an object')
        handler = service().check if hasattr(service(), 'check') else bridge.motion_run_check
        return await asyncio.to_thread(handler, body)

    @app.post('/api/motion-run/initialize')
    async def motion_run_initialize(request: Request):
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail='request body must be an object')
        handler = service().initialize if hasattr(service(), 'initialize') else bridge.motion_run_initialize
        return await asyncio.to_thread(handler, body)

    @app.post('/api/motion-run/start')
    async def motion_run_start(request: Request):
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail='request body must be an object')
        handler = service().start if hasattr(service(), 'start') else bridge.motion_run_start
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
            srv = service()
            auto = getattr(srv, 'automation', None)
            handler = auto.configure if auto and hasattr(auto, 'configure') else bridge.motion_automation_configure
            return await asyncio.to_thread(handler, body)
        except Exception as exc:
            import traceback
            trace = traceback.format_exc()
            bridge.get_logger().error(f'motion_automation_configure API error: {trace}')
            return {'success': False, 'message': f'서버 내부 오류: {exc}'}

    @app.post('/api/motion-run/automation/start')
    async def motion_automation_start(request: Request):
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail='request body must be an object')
        srv = service()
        auto = getattr(srv, 'automation', None)
        handler = auto.start if auto and hasattr(auto, 'start') else bridge.motion_automation_start
        return await asyncio.to_thread(handler, body)

    @app.post('/api/motion-run/automation/reserve')
    async def motion_automation_reserve(request: Request):
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail='request body must be an object')
        srv = service()
        auto = getattr(srv, 'automation', None)
        handler = auto.reserve if auto and hasattr(auto, 'reserve') else bridge.motion_automation_reserve
        return await asyncio.to_thread(handler, body)

    @app.post('/api/motion-run/automation/disable')
    async def motion_automation_disable():
        srv = service()
        auto = getattr(srv, 'automation', None)
        handler = auto.disable if auto and hasattr(auto, 'disable') else bridge.motion_automation_disable
        return await asyncio.to_thread(handler)

    @app.post('/api/motion-run/stop')
    async def motion_run_stop():
        stop_fn = service().stop if hasattr(service(), 'stop') else bridge.motion_run_stop
        return await asyncio.to_thread(safety_first_stop, bridge, stop_fn)

    @app.post('/api/motion-run/stop-after-cycle')
    async def motion_run_stop_after_cycle_api():
        handler = service().stop_after_cycle if hasattr(service(), 'stop_after_cycle') else bridge.motion_run_stop_after_cycle
        return await asyncio.to_thread(handler)
