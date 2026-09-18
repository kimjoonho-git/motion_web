import asyncio

from fastapi import FastAPI, HTTPException, Request


def register_motor_routes(app: FastAPI, bridge, project_call) -> None:
    @app.post('/api/motors/scan')
    async def scan_motors():
        return await asyncio.to_thread(bridge._scan.scan_all)

    @app.post('/api/motors/scan/ac-servo')
    async def scan_ac_servo_motors():
        return await asyncio.to_thread(bridge._scan.scan_ac_servo)

    @app.post('/api/motors/scan/dynamixel')
    async def scan_dynamixel_motors():
        return await asyncio.to_thread(bridge._scan.scan_dynamixel)

    @app.get('/api/motors/scan/progress')
    async def motor_scan_progress():
        return await asyncio.to_thread(bridge._scan.progress)

    @app.post('/api/motors/scan/cancel')
    async def cancel_motor_scan():
        # 진행 중인 물리 검색은 끝까지 간다 · 다음 장치 종류부터 중단된다 (§6-26)
        return await asyncio.to_thread(bridge._scan.cancel)

    @app.get('/api/motors/ethercat-aliases')
    async def read_ethercat_aliases():
        return await asyncio.to_thread(bridge.read_ethercat_aliases)

    @app.post('/api/motors/ethercat-alias')
    async def write_ethercat_alias(request: Request):
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail='request body must be an object')
        return await asyncio.to_thread(bridge.write_ethercat_alias, body)

    @app.get('/api/motor-config')
    async def motor_config():
        return await asyncio.to_thread(bridge._motor_config.load)

    @app.put('/api/motor-config')
    async def save_motor_config(request: Request):
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail='request body must be an object')
        return await asyncio.to_thread(bridge._motor_config.save, body)

    @app.delete('/api/motor-config')
    async def delete_motor_config():
        return await project_call(bridge._motor_config.delete)

    @app.post('/api/motor-config/apply')
    async def apply_motor_config():
        return await asyncio.to_thread(bridge._motor_config.apply)

    @app.get('/api/motor-events')
    async def motor_events(
        limit: int = 200, category: str = 'all', file_name: str = 'all'
    ):
        return await asyncio.to_thread(
            lambda: bridge._motor_event_log.events(
                limit=limit, category=category, file_name=file_name,
            )
        )

    @app.delete('/api/motor-events')
    async def clear_motor_events():
        return await asyncio.to_thread(bridge._motor_event_log.clear)

    @app.delete('/api/motor-events/files/{file_name}')
    async def delete_motor_event_file(file_name: str):
        return await project_call(bridge._motor_event_log.delete_file, file_name)

    @app.get('/api/servo-alarm-policy')
    async def servo_alarm_policy():
        return await project_call(bridge.servo_alarm_policy)

    @app.put('/api/servo-alarm-policy')
    async def save_servo_alarm_policy(request: Request):
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail='request body must be an object')
        return await project_call(bridge.save_servo_alarm_policy, body)

    @app.post('/api/motion-test/ac-servo/jog')
    async def ac_servo_jog(request: Request):
        body = await request.json()
        return await asyncio.to_thread(
            bridge._manual.ac_servo_jog,
            body.get('axis'),
            body.get('relative_deg'),
        )

    @app.post('/api/motion-test/dynamixel/jog')
    async def dynamixel_jog(request: Request):
        body = await request.json()
        return await asyncio.to_thread(
            bridge._manual.dynamixel_jog,
            body.get('axis'),
            body.get('relative_deg'),
        )

    @app.post('/api/motion-test/ac-servo/action')
    async def ac_servo_action(request: Request):
        body = await request.json()
        return await asyncio.to_thread(
            bridge._manual.ac_servo_action,
            body.get('axis'),
            body.get('target_deg'),
            body.get('duration_sec'),
            body.get('range_recovery', False),
        )

    @app.post('/api/motion-test/dynamixel/action')
    async def dynamixel_action(request: Request):
        body = await request.json()
        return await asyncio.to_thread(
            bridge._manual.dynamixel_action,
            body.get('axis'),
            body.get('target_deg'),
            body.get('duration_sec'),
            body.get('range_recovery', False),
        )

    @app.post('/api/motion-test/ac-servo/control')
    async def ac_servo_control(request: Request):
        body = await request.json()
        return await asyncio.to_thread(
            bridge._manual.ac_servo_control,
            body.get('action'),
            body.get('axis'),
            body.get('scope', 'selected'),
        )
