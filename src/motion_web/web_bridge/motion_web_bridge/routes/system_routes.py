import asyncio
import os
import subprocess
from pathlib import Path
from typing import List

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from ament_index_python.packages import get_package_share_directory


def register_system_routes(app: FastAPI, bridge, project_call) -> None:
    ui_share = Path(get_package_share_directory('motion_web_ui')) / 'static'
    workspace_dir = os.environ.get('MOTION_WORKSPACE', '')
    dev_static = Path(workspace_dir) / 'src' / 'motion_web' / 'web_ui' / 'static'
    if workspace_dir and dev_static.is_dir():
        ui_share = dev_static

    @app.get('/')
    async def index():
        return FileResponse(
            str(ui_share / 'index.html'),
            headers={'Cache-Control': 'no-store'},
        )

    @app.get('/static/{asset_path:path}')
    async def static_asset(asset_path: str):
        relative_path = Path(asset_path)
        if relative_path.is_absolute() or '..' in relative_path.parts:
            raise HTTPException(status_code=404, detail='Not Found')
        asset = ui_share / relative_path
        if not asset.is_file():
            raise HTTPException(status_code=404, detail='Not Found')
        return FileResponse(
            str(asset),
            headers={'Cache-Control': 'no-store'},
        )

    @app.get('/api/status')
    async def status():
        return bridge.snapshot()

    @app.get('/api/system/version')
    async def system_version():
        def _git_text(args: List[str], cwd: str) -> str:
            return subprocess.check_output(
                ['git', *args], cwd=cwd, stderr=subprocess.DEVNULL
            ).decode('utf-8').strip()

        def _web_url(remote: str) -> str:
            if remote.startswith('git@github.com:'):
                return 'https://github.com/' + remote.split(':', 1)[1].removesuffix('.git')
            if remote.startswith('https://github.com/'):
                return remote.removesuffix('.git')
            return remote

        try:
            cwd = os.environ.get('MOTION_WORKSPACE', os.getcwd())
            branch = _git_text(['rev-parse', '--abbrev-ref', 'HEAD'], cwd)
            hash_str = _git_text(['rev-parse', '--short', 'HEAD'], cwd)
            full_hash = _git_text(['rev-parse', 'HEAD'], cwd)
            msg = _git_text(['log', '-1', '--format=%s'], cwd)
            remote = _git_text(['remote', 'get-url', 'origin'], cwd)
            return {
                'branch': branch,
                'hash': hash_str,
                'full_hash': full_hash,
                'message': msg,
                'remote_url': remote,
                'remote_web_url': _web_url(remote),
                'is_main': branch == 'main',
            }
        except Exception:
            return {
                'branch': 'unknown',
                'hash': 'unknown',
                'full_hash': '',
                'message': '',
                'remote_url': '',
                'remote_web_url': '',
                'is_main': False,
            }

    @app.get('/api/coordination')
    async def coordination_status():
        return bridge.coordination_status()

    @app.put('/api/coordination/settings')
    async def update_coordination_settings(request: Request):
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail='request body must be an object')
        try:
            return await asyncio.to_thread(bridge.update_coordination_settings, body)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post('/api/coordination/local-readiness')
    async def coordination_local_readiness():
        return await asyncio.to_thread(bridge.coordination_local_readiness)

    @app.get('/api/coordination/local-status')
    async def coordination_local_status(request: Request):
        remote_ip = request.client.host if request.client else ''
        if remote_ip not in {'127.0.0.1', '::1'}:
            raise HTTPException(status_code=403, detail='loopback only')
        return bridge.coordination_local_status()

    @app.post('/api/coordination/control')
    async def coordination_control(request: Request):
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail='request body must be an object')
        try:
            return await asyncio.to_thread(bridge.coordination_control, body)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post('/api/coordination/local-control')
    async def coordination_local_control(request: Request):
        remote_ip = request.client.host if request.client else ''
        if remote_ip not in {'127.0.0.1', '::1'}:
            raise HTTPException(status_code=403, detail='loopback only')
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail='request body must be an object')
        return await asyncio.to_thread(bridge.coordination_local_control, body)

    @app.post('/api/system/program/restart')
    async def restart_managed_program():
        return await asyncio.to_thread(project_call, bridge.restart_managed_program)

    @app.post('/api/system/desktop-shortcut')
    async def create_desktop_shortcut():
        return await asyncio.to_thread(bridge.create_desktop_shortcut)

    @app.post('/api/system/motor-control/restart')
    async def restart_motor_control_system():
        return await asyncio.to_thread(
            project_call,
            bridge.restart_motor_control_system,
        )

    @app.post('/api/system/motor-runtime/clear')
    async def clear_motor_runtime_application():
        return await asyncio.to_thread(
            project_call,
            bridge.clear_motor_runtime_application,
        )

    @app.post('/api/monitoring/enabled')
    async def set_monitoring(request: Request):
        body = await request.json()
        enabled = bool(body.get('enabled', True))
        return bridge.set_monitoring(enabled)

    @app.websocket('/ws/status')
    async def websocket_status(websocket: WebSocket):
        await websocket.accept()
        period_sec = 1.0 / max(bridge.web_publish_hz, 0.1)
        try:
            while True:
                await websocket.send_json(bridge.snapshot())
                try:
                    event = await asyncio.wait_for(
                        websocket.receive(), timeout=period_sec
                    )
                except asyncio.TimeoutError:
                    continue
                if event.get('type') == 'websocket.disconnect':
                    return
        except WebSocketDisconnect:
            return
        except (ConnectionError, RuntimeError):
            return
