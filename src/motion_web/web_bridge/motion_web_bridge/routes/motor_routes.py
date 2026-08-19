import asyncio

from fastapi import FastAPI, HTTPException, Request


def register_motor_routes(app: FastAPI, bridge, project_call) -> None:
    @app.post('/api/motors/scan')
    async def scan_motors():
        handler = getattr(bridge, 'motor', bridge)
        return await asyncio.to_thread(handler.scan_motors)

    @app.post('/api/motors/scan/ac-servo')
    async def scan_ac_servo_motors():
        handler = getattr(bridge, 'motor', bridge)
        return await asyncio.to_thread(handler.scan_ac_servo_motors)

    @app.post('/api/motors/scan/dynamixel')
    async def scan_dynamixel_motors():
        handler = getattr(bridge, 'motor', bridge)
        return await asyncio.to_thread(handler.scan_dynamixel_motors)

    @app.get('/api/motors/scan/progress')
    async def motor_scan_progress():
        handler = getattr(bridge, 'motor', bridge)
        return handler.motor_scan_progress()

    @app.get('/api/motors/ethercat-aliases')
    async def read_ethercat_aliases():
        handler = getattr(bridge, 'motor', bridge)
        return await asyncio.to_thread(handler.read_ethercat_aliases)

    @app.post('/api/motors/ethercat-alias')
    async def write_ethercat_alias(request: Request):
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail='request body must be an object')
        handler = getattr(bridge, 'motor', bridge)
        return await asyncio.to_thread(handler.write_ethercat_alias, body)

    @app.get('/api/motor-config')
    async def motor_config():
        handler = getattr(bridge, 'motor', bridge)
        return handler.load_motor_config()

    @app.put('/api/motor-config')
    async def save_motor_config(request: Request):
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail='request body must be an object')
        handler = getattr(bridge, 'motor', bridge)
        return handler.save_motor_config(body)

    @app.delete('/api/motor-config')
    async def delete_motor_config():
        handler = getattr(bridge, 'motor', bridge)
        return project_call(handler.delete_motor_config)

    @app.post('/api/motor-config/apply')
    async def apply_motor_config():
        handler = getattr(bridge, 'motor', bridge)
        return await asyncio.to_thread(handler.apply_motor_config)

    @app.get('/api/motor-events')
    async def motor_events(
        limit: int = 200, category: str = 'all', file_name: str = 'all'
    ):
        handler = getattr(bridge, 'motor', bridge)
        return handler.motor_events(limit=limit, category=category, file_name=file_name)

    @app.delete('/api/motor-events')
    async def clear_motor_events():
        handler = getattr(bridge, 'motor', bridge)
        return handler.clear_motor_events()

    @app.delete('/api/motor-events/files/{file_name}')
    async def delete_motor_event_file(file_name: str):
        handler = getattr(bridge, 'motor', bridge)
        return project_call(handler.delete_motor_event_file, file_name)

    @app.get('/api/servo-alarm-policy')
    async def servo_alarm_policy():
        handler = getattr(bridge, 'motor', bridge)
        return project_call(handler.servo_alarm_policy)

    @app.put('/api/servo-alarm-policy')
    async def save_servo_alarm_policy(request: Request):
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail='request body must be an object')
        handler = getattr(bridge, 'motor', bridge)
        return project_call(handler.save_servo_alarm_policy, body)

    @app.post('/api/motion-test/ac-servo/jog')
    async def ac_servo_jog(request: Request):
        body = await request.json()
        return await asyncio.to_thread(
            bridge.request_ac_servo_jog,
            body.get('axis'),
            body.get('relative_deg'),
        )

    @app.post('/api/motion-test/dynamixel/jog')
    async def dynamixel_jog(request: Request):
        body = await request.json()
        return await asyncio.to_thread(
            bridge.request_dynamixel_jog,
            body.get('axis'),
            body.get('relative_deg'),
        )

    @app.post('/api/motion-test/ac-servo/action')
    async def ac_servo_action(request: Request):
        body = await request.json()
        return await asyncio.to_thread(
            bridge.request_ac_servo_action,
            body.get('axis'),
            body.get('target_deg'),
            body.get('duration_sec'),
            body.get('range_recovery', False),
        )

    @app.post('/api/motion-test/dynamixel/action')
    async def dynamixel_action(request: Request):
        body = await request.json()
        return await asyncio.to_thread(
            bridge.request_dynamixel_action,
            body.get('axis'),
            body.get('target_deg'),
            body.get('duration_sec'),
            body.get('range_recovery', False),
        )

    @app.post('/api/motion-test/ac-servo/control')
    async def ac_servo_control(request: Request):
        body = await request.json()
        return await asyncio.to_thread(
            bridge.request_ac_servo_control,
            body.get('action'),
            body.get('axis'),
            body.get('scope', 'selected'),
        )
