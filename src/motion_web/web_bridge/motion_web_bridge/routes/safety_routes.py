from fastapi import FastAPI


def register_safety_routes(app: FastAPI, bridge) -> None:
    @app.post('/api/safety/motion-stop')
    async def safety_motion_stop():
        cancel_pending = getattr(bridge, 'cancel_pending_motion_studio_start', None)
        if callable(cancel_pending):
            cancel_pending()
        request_id = bridge.publish_safety_stop(False)
        return {
            'success': True,
            'message': '전체 동작 정지 명령 우선 전송 완료',
            'request_id': request_id,
            'acknowledgement_pending': True,
        }

    @app.post('/api/safety/emergency-stop')
    async def safety_emergency_stop():
        cancel_pending = getattr(bridge, 'cancel_pending_motion_studio_start', None)
        if callable(cancel_pending):
            cancel_pending()
        request_id = bridge.publish_safety_stop(True)
        return {
            'success': True,
            'message': '긴급정지 명령 우선 전송 완료',
            'request_id': request_id,
            'acknowledgement_pending': True,
        }
