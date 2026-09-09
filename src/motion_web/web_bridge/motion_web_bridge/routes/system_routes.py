import asyncio
import os
import subprocess
from pathlib import Path
from typing import List

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response
from ament_index_python.packages import get_package_share_directory

from motion_web_bridge import desktop_shortcut


def _is_not_modified(response_headers, request_headers) -> bool:
    """`ETag`가 같으면 본문을 다시 보내지 않아도 된다.

    `Last-Modified`는 보지 않는다 · 초 단위라 같은 초 안의 수정을 놓친다 ·
    낡은 화면이 뜨느니 한 번 더 보내는 편이 낫다.
    """
    request_etag = request_headers.get('if-none-match')
    if not request_etag:
        return False
    return request_etag == response_headers.get('etag')


def register_system_routes(app: FastAPI, bridge, project_call) -> None:
    ui_share = Path(get_package_share_directory('motion_web_ui')) / 'static'
    workspace_dir = os.environ.get('MOTION_WORKSPACE', '')
    dev_static = Path(workspace_dir) / 'src' / 'motion_web' / 'web_ui' / 'static'
    if workspace_dir and dev_static.is_dir():
        ui_share = dev_static

    def _asset_response(asset: Path, request: Request = None):
        """정적 파일을 재검증 가능한 형태로 돌려준다 · §6-42.

        `no-store`는 브라우저가 아예 캐시하지 않게 만들어, 함께 나가는 `ETag`를
        무의미하게 한다. `no-cache`는 **매번 물어보되 안 바뀌었으면 본문을 받지
        않는** 것이라 낡은 화면 위험은 같고 전송만 줄어든다.

        Starlette의 `FileResponse`는 조건부 요청을 스스로 처리하지 않는다 ·
        `If-None-Match`를 보고 304를 돌려주는 것은 여기서 한다.
        """
        response = FileResponse(
            str(asset),
            headers={'Cache-Control': 'no-cache'},
            stat_result=asset.stat(),
        )
        request_headers = getattr(request, 'headers', None)
        if request_headers is None:
            return response
        if not _is_not_modified(response.headers, request_headers):
            return response
        return Response(
            status_code=304,
            headers={
                'Cache-Control': 'no-cache',
                'ETag': response.headers['etag'],
                'Last-Modified': response.headers['last-modified'],
            },
        )

    @app.get('/')
    async def index(request: Request = None):
        return _asset_response(ui_share / 'index.html', request)

    @app.get('/static/{asset_path:path}')
    async def static_asset(asset_path: str, request: Request = None):
        relative_path = Path(asset_path)
        if relative_path.is_absolute() or '..' in relative_path.parts:
            raise HTTPException(status_code=404, detail='Not Found')
        asset = ui_share / relative_path
        if not asset.is_file():
            raise HTTPException(status_code=404, detail='Not Found')
        return _asset_response(asset, request)

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
        return bridge._coordination_web_bridge.snapshot()

    @app.put('/api/coordination/settings')
    async def update_coordination_settings(request: Request):
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail='request body must be an object')
        try:
            return await asyncio.to_thread(bridge._coordination_web_bridge.update_settings, body)
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
            return await asyncio.to_thread(bridge._coordination_web_bridge.request_control, body)
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
        return await asyncio.to_thread(project_call, bridge._motor_config.restart_managed_program)

    @app.post('/api/system/desktop-shortcut')
    async def create_desktop_shortcut():
        return await asyncio.to_thread(
            desktop_shortcut.create_desktop_shortcut, bridge.workspace_root
        )

    @app.post('/api/system/motor-control/restart')
    async def restart_motor_control_system():
        return await asyncio.to_thread(
            project_call,
            bridge._motor_config.restart_motor_control,
        )

    @app.post('/api/system/motor-runtime/clear')
    async def clear_motor_runtime_application():
        return await asyncio.to_thread(
            project_call,
            bridge._motor_config.clear_runtime_application,
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
