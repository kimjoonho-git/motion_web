"""FastAPI route registration for Motion Studio endpoints.

노드의 위임 껍데기를 거치지 않고 서비스를 직접 부른다 · §6-15
`transport()`·`sync()`는 노드가 소유한 서비스를 꺼내는 접근자다.
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable

from fastapi import FastAPI, HTTPException, Request

from .motion_studio_bridge import (
    STUDIO_EDITOR_TIMEOUT_SEC,
    STUDIO_REQUEST_TIMEOUT_SEC,
)


def register_motion_studio_routes(
    app: FastAPI,
    bridge: Any,
    project_call: Callable[..., Any],
    safety_first_stop: Callable[..., Any],
) -> None:
    def transport():
        return bridge._motion_studio_transport()

    def sync():
        return bridge._motion_studio_sync()

    @app.get('/api/motion-studio')
    async def motion_studio():
        return await project_call(sync().prepare)

    @app.post('/api/motion-studio/import')
    async def motion_studio_import(request: Request):
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(
                status_code=400, detail='request body must be an object'
            )
        return await project_call(sync().import_layer, body)

    @app.put('/api/motion-studio/project')
    async def motion_studio_save(request: Request):
        body = await request.json()
        return await asyncio.to_thread(
            lambda: sync().sync_result(
                sync().request_attached('save', body)
            )
        )

    @app.put('/api/motion-studio/layers')
    async def motion_studio_layer(request: Request):
        body = await request.json()
        return await asyncio.to_thread(
            lambda: sync().sync_result(
                sync().request_attached('update_layer', body)
            )
        )

    @app.post('/api/motion-studio/layers')
    async def motion_studio_layer_create(request: Request):
        body = await request.json()
        return await asyncio.to_thread(
            lambda: sync().sync_result(
                sync().request_attached('create_layer', body)
            )
        )

    @app.put('/api/motion-studio/layers/data')
    async def motion_studio_layer_data(request: Request):
        body = await request.json()
        return await asyncio.to_thread(
            lambda: sync().sync_result(
                sync().request_attached(
                    'replace_layer_data', body,
                    timeout_sec=STUDIO_REQUEST_TIMEOUT_SEC
                )
            )
        )

    @app.delete('/api/motion-studio/layers/{layer_id}')
    async def motion_studio_layer_delete(layer_id: str):
        return await asyncio.to_thread(
            lambda: sync().sync_result(
                sync().request_attached(
                    'delete_layer', {'layer_id': layer_id}
                )
            )
        )

    @app.post('/api/motion-studio/layers/{layer_id}/duplicate')
    async def motion_studio_layer_duplicate(layer_id: str):
        return await asyncio.to_thread(
            lambda: sync().sync_result(
                sync().request_attached(
                    'duplicate_layer', {'layer_id': layer_id}
                )
            )
        )

    @app.post('/api/motion-studio/editor/transform')
    async def motion_studio_editor_transform(request: Request):
        body = await request.json()
        return await asyncio.to_thread(
            transport().request_editor, 'edit', body, STUDIO_EDITOR_TIMEOUT_SEC
        )

    @app.post('/api/motion-studio/editor/merge-preview')
    async def motion_studio_editor_merge_preview(request: Request):
        body = await request.json()
        return await asyncio.to_thread(
            transport().request_editor, 'merge', body, STUDIO_EDITOR_TIMEOUT_SEC
        )

    @app.post('/api/motion-studio/layers/merge')
    async def motion_studio_layers_merge(request: Request):
        body = await request.json()
        return await asyncio.to_thread(
            lambda: sync().sync_result(
                sync().request_attached(
                    'commit_merged_layer', body,
                    timeout_sec=STUDIO_EDITOR_TIMEOUT_SEC
                )
            )
        )

    @app.post('/api/motion-studio/record')
    async def motion_studio_record(request: Request):
        body = await request.json()
        return await asyncio.to_thread(
            sync().request_prepared, 'record', body
        )

    @app.post('/api/motion-studio/play')
    async def motion_studio_play(request: Request):
        body = await request.json()
        return await asyncio.to_thread(
            sync().request_prepared, 'play', body
        )

    @app.post('/api/motion-studio/initialize')
    async def motion_studio_initialize(request: Request):
        body = await request.json()
        return await asyncio.to_thread(
            sync().request_prepared, 'initialize', body
        )

    @app.post('/api/motion-studio/stop')
    async def motion_studio_stop():
        return await asyncio.to_thread(
            safety_first_stop,
            bridge,
            lambda: sync().sync_result(
                transport().request('stop')
            ),
        )

    @app.post('/api/motion-studio/export')
    async def motion_studio_export(request: Request):
        body = await request.json()
        return await asyncio.to_thread(sync().export, body)
