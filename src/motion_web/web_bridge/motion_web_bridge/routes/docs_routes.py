"""사용법·설치법을 웹에서 읽는다 · §6-157

**문서가 저장소 안에만 있으면 아무도 안 읽는다.**

사용법과 설치법은 `.md` 파일이라 터미널에서 `cat` 하거나 깃허브에 올려야
읽혔다 · 정작 이 프로그램을 쓰는 사람은 웹 화면 앞에 앉아 있고, 현장 PC 는
인터넷이 없을 수도 있다 · 그래서 화면 안에서 그대로 보여 준다.

**읽기 전용이다** · 목록에 없는 경로는 내주지 않는다 · 파일이 없으면
404 가 아니라 「아직 없다」고 알려 준다 (`git pull` 전에는 없을 수 있다).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Dict, List

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

#: 내줄 문서 · **여기 적힌 것만** 나간다 · 경로를 밖에서 받지 않는다
DOCUMENTS: List[Dict[str, str]] = [
    {
        'id': 'usage',
        'title': '사용법',
        'subtitle': '프로젝트 만들기부터 스케줄·상황별 루틴까지',
        'path': 'docs/사용법.md',
    },
    {
        'id': 'install',
        'title': '설치·설정',
        'subtitle': '우분투 설치부터 프로그램이 뜰 때까지',
        'path': 'README.md',
    },
]

#: 문서에 넣은 그림 · 캡처를 여기 두고 `![설명](images/파일.png)` 로 부른다
ASSET_DIR = 'docs/images'

#: 그림으로 인정하는 것 · 나머지는 내주지 않는다
ASSET_SUFFIXES = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg'}


def _entry(doc_id: str) -> Dict[str, str]:
    for document in DOCUMENTS:
        if document['id'] == doc_id:
            return document
    raise HTTPException(status_code=404, detail=f'그런 문서가 없습니다: {doc_id}')


def _listing(root: Path) -> Dict[str, Any]:
    documents = []
    for document in DOCUMENTS:
        path = root / document['path']
        exists = path.is_file()
        documents.append({
            'id': document['id'],
            'title': document['title'],
            'subtitle': document['subtitle'],
            'source': document['path'],
            'available': exists,
            'modified': path.stat().st_mtime if exists else 0.0,
        })
    return {'success': True, 'documents': documents}


def _read(root: Path, doc_id: str) -> Dict[str, Any]:
    document = _entry(doc_id)
    path = root / document['path']
    if not path.is_file():
        # 404 로 던지면 화면에 「통신 오류」로 보인다 · 사실은 그냥 없는 것이다
        return {
            'success': False,
            'id': document['id'],
            'title': document['title'],
            'source': document['path'],
            'markdown': '',
            'message': (
                f'{document["path"]} 파일이 이 PC 에 없습니다 · '
                '최신 코드를 받은 뒤 다시 보세요'
            ),
        }
    return {
        'success': True,
        'id': document['id'],
        'title': document['title'],
        'subtitle': document['subtitle'],
        'source': document['path'],
        'markdown': path.read_text(encoding='utf-8'),
        'modified': path.stat().st_mtime,
    }


def _asset_path(root: Path, name: str) -> Path:
    """`docs/images` 안의 그림 하나 · 밖으로 나가는 경로는 막는다."""
    base = (root / ASSET_DIR).resolve()
    target = (base / name).resolve()
    if base not in target.parents and target != base:
        raise HTTPException(status_code=403, detail='문서 그림 폴더 밖입니다')
    if target.suffix.lower() not in ASSET_SUFFIXES:
        raise HTTPException(status_code=403, detail='그림 파일이 아닙니다')
    if not target.is_file():
        raise HTTPException(status_code=404, detail='그림이 없습니다')
    return target


def register_docs_routes(app: FastAPI, bridge) -> None:
    def root() -> Path:
        """작업공간은 **부를 때** 묻는다 · 등록 시점에는 아직 없을 수 있다."""
        return Path(getattr(bridge, 'workspace_root', None) or Path.cwd())

    @app.get('/api/docs')
    async def list_documents():
        return await asyncio.to_thread(_listing, root())

    @app.get('/api/docs/images/{name:path}')
    async def document_image(name: str):
        path = await asyncio.to_thread(_asset_path, root(), name)
        return FileResponse(str(path))

    @app.get('/api/docs/{doc_id}')
    async def read_document(doc_id: str):
        return await asyncio.to_thread(_read, root(), doc_id)
