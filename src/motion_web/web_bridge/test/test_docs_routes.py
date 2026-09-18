"""사용법·설치법을 웹에서 읽는다 · §6-157

문서가 저장소 안에만 있으면 아무도 안 읽는다 · 그래서 화면에서 바로 보여 준다.

여기서 지키는 것은 **읽기 전용**이라는 점이다 · 경로를 밖에서 받지 않고,
목록에 적힌 것만 내준다 · 그림도 `docs/images` 안쪽만 나간다.
"""

from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from motion_web_bridge.routes.docs_routes import (
    DOCUMENTS,
    register_docs_routes,
)


class _Bridge:
    def __init__(self, root: Path) -> None:
        self.workspace_root = root


@pytest.fixture
def workspace(tmp_path):
    (tmp_path / 'docs').mkdir()
    (tmp_path / 'docs' / '사용법.md').write_text(
        '# 사용법\n\n## 9. 스케줄 걸기\n', encoding='utf-8'
    )
    (tmp_path / 'README.md').write_text('# 설치\n', encoding='utf-8')
    (tmp_path / 'docs' / 'images').mkdir()
    (tmp_path / 'docs' / 'images' / 'shot.png').write_bytes(b'\x89PNG\r\n\x1a\n')
    return tmp_path


@pytest.fixture
def client(workspace):
    app = FastAPI()
    register_docs_routes(app, _Bridge(workspace))
    return TestClient(app)


def test_the_listing_names_both_documents(client):
    payload = client.get('/api/docs').json()
    assert payload['success'] is True
    ids = [document['id'] for document in payload['documents']]
    assert ids == ['usage', 'install']
    assert all(document['available'] for document in payload['documents'])


def test_a_document_comes_back_as_markdown(client):
    payload = client.get('/api/docs/usage').json()
    assert payload['success'] is True
    assert payload['markdown'].startswith('# 사용법')
    assert payload['source'] == 'docs/사용법.md'


def test_a_missing_file_says_so_instead_of_failing(client, workspace):
    """`git pull` 전에는 없을 수 있다 · 그때 「통신 오류」로 보이면 안 된다."""
    (workspace / 'docs' / '사용법.md').unlink()

    payload = client.get('/api/docs/usage').json()

    assert payload['success'] is False
    assert '없습니다' in payload['message']
    assert client.get('/api/docs').json()['documents'][0]['available'] is False


def test_an_unlisted_document_is_refused(client):
    """경로를 밖에서 받지 않는다 · 목록에 적힌 것만 나간다."""
    assert client.get('/api/docs/etc-passwd').status_code == 404


def test_an_image_under_docs_is_served(client):
    response = client.get('/api/docs/images/shot.png')
    assert response.status_code == 200
    assert response.content.startswith(b'\x89PNG')


@pytest.mark.parametrize('name', [
    '../../README.md',
    '../사용법.md',
    'shot.png.py',
])
def test_a_path_out_of_the_image_folder_is_refused(client, name):
    """`..` 로 걸어 나가거나 그림이 아닌 것을 달라고 하면 막는다."""
    assert client.get(f'/api/docs/images/{name}').status_code in {403, 404}


def test_every_listed_document_really_exists_in_the_repository():
    """목록에 적어 놓고 파일이 없으면 화면에 빈칸만 나온다 · 실제 저장소로 본다."""
    root = Path(__file__).resolve().parents[4]
    missing = [
        document['path'] for document in DOCUMENTS
        if not (root / document['path']).is_file()
    ]
    assert missing == [], f'문서 목록에 있는데 저장소에 없습니다: {missing}'
