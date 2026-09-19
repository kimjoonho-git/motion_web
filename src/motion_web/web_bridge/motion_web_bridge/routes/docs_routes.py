"""문서 길 · 받아서 넘기기만 한다 · §6-190

업무는 `docs_service.DocsService` 가 한다 · 전에는 이 파일 안에 목록·읽기·
그림 꺼내기가 들어 있어서 길 하나당 41.3줄이었다 (다른 라우트는 5~10줄).

서비스가 올리는 예외를 HTTP 로 옮기는 것이 이 파일의 일이다.
"""

from __future__ import annotations

import asyncio

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

from motion_web_bridge.docs_service import (
    DOCUMENTS,
    DocsService,
    DocumentAssetRefused,
    DocumentNotFound,
)

__all__ = ['DOCUMENTS', 'register_docs_routes']


def register_docs_routes(app: FastAPI, bridge) -> None:
    service = DocsService(bridge)

    async def _call(work, *args):
        try:
            return await asyncio.to_thread(work, *args)
        except DocumentNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except DocumentAssetRefused as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc

    @app.get('/api/docs')
    async def list_documents():
        return await _call(service.listing)

    @app.get('/api/docs/images/{name:path}')
    async def document_image(name: str):
        return FileResponse(str(await _call(service.asset_path, name)))

    @app.get('/api/docs/{doc_id}')
    async def read_document(doc_id: str):
        return await _call(service.read, doc_id)
