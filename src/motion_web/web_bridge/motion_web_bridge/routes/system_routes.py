import asyncio
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path
from typing import List

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response
from ament_index_python.packages import get_package_share_directory
from motion_common import local_clock

from motion_web_bridge import desktop_shortcut
from motion_web_bridge.index_composer import IndexComposer


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

    index_composer = IndexComposer(ui_share / 'index.html')

    @app.get('/')
    async def index(request: Request = None):
        html, etag = index_composer.compose()
        headers = {'Cache-Control': 'no-cache', 'ETag': etag}
        request_headers = getattr(request, 'headers', None)
        if request_headers is not None and request_headers.get('if-none-match') == etag:
            return Response(status_code=304, headers=headers)
        return Response(content=html, media_type='text/html; charset=utf-8', headers=headers)

    @app.get('/favicon.ico')
    async def favicon():
        """탭 아이콘 · 없어도 되지만 **404 를 남기지 않는다** · §6-223

        브라우저가 페이지를 열 때마다 자동으로 요청한다 · 없으면 콘솔에 늘
        빨간 줄이 하나 남고, 진짜 오류가 났을 때 그 속에 묻힌다.
        """
        return Response(status_code=204)

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
        return await asyncio.to_thread(bridge.snapshot)

    @app.get('/api/system/time')
    async def system_time():
        """이 PC 의 시각·시간대와 고를 수 있는 지역 목록 · §6-150

        **바꾸지는 않는다** · `timedatectl` 은 root 권한이 필요하고, 그 권한을
        웹 서비스에 주는 것이 시간대를 잘못 잡는 것보다 위험하다 · 화면은
        고를 거리와 **칠 명령**까지만 만들고, 치는 일은 사람이 터미널에서 한다.
        """
        return await asyncio.to_thread(
            lambda: {
                'clock': local_clock.snapshot(),
                'timezones': list(local_clock.timezones()),
            }
        )

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
        return await asyncio.to_thread(bridge.coordination.snapshot)

    @app.put('/api/coordination/settings')
    async def update_coordination_settings(request: Request):
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail='request body must be an object')
        try:
            return await asyncio.to_thread(bridge.coordination.update_settings, body)
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
        # 연동 노드가 50ms 마다 묻는 길 · 이벤트 루프에 두면 다른 요청이
        # 몰릴 때 0.25초 제한을 넘기고 그룹이 통째로 선다 · §6-146
        return await asyncio.to_thread(bridge.coordination_local_status)

    @app.post('/api/coordination/control')
    async def coordination_control(request: Request):
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail='request body must be an object')
        try:
            return await asyncio.to_thread(bridge.coordination.request_control, body)
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
        return await project_call(bridge.motor_config.restart_managed_program)

    @app.post('/api/system/desktop-shortcut')
    async def create_desktop_shortcut():
        return await asyncio.to_thread(
            desktop_shortcut.create_desktop_shortcut, bridge.workspace_root
        )

    @app.post('/api/system/motor-control/restart')
    async def restart_motor_control_system():
        return await project_call(bridge.motor_config.restart_motor_control)

    @app.post('/api/system/motor-runtime/clear')
    async def clear_motor_runtime_application():
        return await project_call(bridge.motor_config.clear_runtime_application)

    @app.post('/api/monitoring/enabled')
    async def set_monitoring(request: Request):
        body = await request.json()
        enabled = bool(body.get('enabled', True))
        return await asyncio.to_thread(bridge.set_monitoring, enabled)

    def _snapshot_text() -> str:
        """스냅샷을 만들고 글자로 바꾸는 일까지 한 번에 · §6-152

        둘 다 무겁다 · 실측 66KB, 만드는 데만 17ms · 그런데 이걸 **이벤트
        루프에서** 하고 있었다 · 화면 탭 하나가 초당 10번 부르니 탭 하나당
        루프를 초당 170ms 씩 막는다 · 탭 두셋이면 루프가 거의 서 있다.

        그동안 연동 노드가 50ms 마다 묻는 `local-status` 가 굶는다 · 0.5초
        못 받으면 **그룹 실행이 통째로 정지한다**(`GROUP_PARTICIPANT_FAILURE`) ·
        실제로 `실시간 모니터링` 탭을 누를 때마다 그렇게 멈췄다.

        `@app.get` 들은 스레드로 옮겼는데(§6-146) 여기만 남아 있었다 ·
        `@app.websocket` 은 그때 훑은 대상이 아니었다.
        """
        return json.dumps(bridge.snapshot())

    @app.websocket('/ws/status')
    async def websocket_status(websocket: WebSocket):
        await websocket.accept()
        period_sec = 1.0 / max(bridge.web_publish_hz, 0.1)
        try:
            while True:
                await websocket.send_text(await asyncio.to_thread(_snapshot_text))
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
