"""FastAPI route registration for Motion Studio endpoints."""

from __future__ import annotations

import asyncio
from typing import Any, Callable

from fastapi import FastAPI, HTTPException, Request


def register_motion_studio_routes(
    app: FastAPI,
    bridge: Any,
    project_call: Callable[..., Any],
    safety_first_stop: Callable[..., Any],
) -> None:
    @app.get('/api/motion-studio')
    async def motion_studio():
        return await asyncio.to_thread(
            project_call, bridge.prepare_unified_motion_studio
        )

    @app.post('/api/motion-studio/projects')
    async def motion_studio_create(request: Request):
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(
                status_code=400, detail='request body must be an object'
            )
        return await asyncio.to_thread(
            lambda: bridge.sync_motion_studio_result(
                bridge.request_motion_studio('create', body)
            )
        )

    @app.post('/api/motion-studio/projects/load')
    async def motion_studio_load(request: Request):
        body = await request.json()
        return await asyncio.to_thread(
            bridge.request_motion_studio, 'load', body
        )

    @app.post('/api/motion-studio/import')
    async def motion_studio_import(request: Request):
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(
                status_code=400, detail='request body must be an object'
            )
        return await asyncio.to_thread(
            project_call, bridge.import_motion_studio_layer, body
        )

    @app.put('/api/motion-studio/project')
    async def motion_studio_save(request: Request):
        body = await request.json()
        return await asyncio.to_thread(
            lambda: bridge.sync_motion_studio_result(
                bridge.request_motion_studio('save', body)
            )
        )

    @app.put('/api/motion-studio/layers')
    async def motion_studio_layer(request: Request):
        body = await request.json()
        return await asyncio.to_thread(
            lambda: bridge.sync_motion_studio_result(
                bridge.request_motion_studio('update_layer', body)
            )
        )

    @app.post('/api/motion-studio/layers')
    async def motion_studio_layer_create(request: Request):
        body = await request.json()
        return await asyncio.to_thread(
            lambda: bridge.sync_motion_studio_result(
                bridge.request_motion_studio('create_layer', body)
            )
        )

    @app.put('/api/motion-studio/layers/data')
    async def motion_studio_layer_data(request: Request):
        body = await request.json()
        return await asyncio.to_thread(
            lambda: bridge.sync_motion_studio_result(
                bridge.request_motion_studio(
                    'replace_layer_data', body, timeout_sec=8.0
                )
            )
        )

    @app.delete('/api/motion-studio/layers/{layer_id}')
    async def motion_studio_layer_delete(layer_id: str):
        return await asyncio.to_thread(
            lambda: bridge.sync_motion_studio_result(
                bridge.request_motion_studio(
                    'delete_layer', {'layer_id': layer_id}
                )
            )
        )

    @app.post('/api/motion-studio/layers/{layer_id}/duplicate')
    async def motion_studio_layer_duplicate(layer_id: str):
        return await asyncio.to_thread(
            lambda: bridge.sync_motion_studio_result(
                bridge.request_motion_studio(
                    'duplicate_layer', {'layer_id': layer_id}
                )
            )
        )

    @app.post('/api/motion-studio/editor/transform')
    async def motion_studio_editor_transform(request: Request):
        body = await request.json()
        return await asyncio.to_thread(
            bridge.request_motion_studio_editor, 'edit', body, 12.0
        )

    @app.post('/api/motion-studio/editor/merge-preview')
    async def motion_studio_editor_merge_preview(request: Request):
        body = await request.json()
        return await asyncio.to_thread(
            bridge.request_motion_studio_editor, 'merge', body, 20.0
        )

    @app.post('/api/motion-studio/layers/merge')
    async def motion_studio_layers_merge(request: Request):
        body = await request.json()
        return await asyncio.to_thread(
            lambda: bridge.sync_motion_studio_result(
                bridge.request_motion_studio(
                    'commit_merged_layer', body, timeout_sec=12.0
                )
            )
        )

    @app.post('/api/motion-studio/record')
    async def motion_studio_record(request: Request):
        body = await request.json()
        return await asyncio.to_thread(
            bridge.request_prepared_motion_studio, 'record', body
        )

    @app.post('/api/motion-studio/play')
    async def motion_studio_play(request: Request):
        body = await request.json()
        return await asyncio.to_thread(
            bridge.request_prepared_motion_studio, 'play', body
        )

    @app.post('/api/motion-studio/initialize')
    async def motion_studio_initialize(request: Request):
        body = await request.json()
        return await asyncio.to_thread(
            bridge.request_prepared_motion_studio, 'initialize', body
        )

    @app.post('/api/motion-studio/stop')
    async def motion_studio_stop():
        return await asyncio.to_thread(
            safety_first_stop,
            bridge,
            lambda: bridge.sync_motion_studio_result(
                bridge.request_motion_studio('stop')
            ),
        )

    @app.post('/api/motion-studio/export')
    async def motion_studio_export(request: Request):
        body = await request.json()
        return await asyncio.to_thread(bridge.export_motion_studio, body)
