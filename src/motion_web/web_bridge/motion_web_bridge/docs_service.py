"""사용법·설치법 읽기 · 길목이 아니라 여기서 한다 · §6-190

**문서가 저장소 안에만 있으면 아무도 안 읽는다.**

사용법과 설치법은 `.md` 파일이라 터미널에서 `cat` 하거나 깃허브에 올려야
읽혔다 · 정작 이 프로그램을 쓰는 사람은 웹 화면 앞에 앉아 있고, 현장 PC 는
인터넷이 없을 수도 있다 · 그래서 화면 안에서 그대로 보여 준다 (§6-157).

**업무가 길목 안에 있었다** · `docs_routes.py` 가 124줄에 길은 셋뿐이라
길 하나당 41.3줄이었다 (다른 라우트는 5~10줄) · 그 안에 목록·읽기·그림
꺼내기가 전부 들어 있어서 **HTTP 를 거치지 않고는 시험할 수 없었다.**

**게다가 업무 함수가 `HTTPException` 을 던졌다** · 길목의 물건이 업무
안으로 새어든 것이다 · 「그런 문서가 없다」는 업무의 사실이고, 그것을
404 로 옮기는 것은 길목의 일이다 · 섞여 있으면 화면 아닌 곳(스케줄 노드,
설치 스크립트)에서 이 기능을 쓸 수 없다.

**읽기 전용이다** · 목록에 없는 경로는 내주지 않는다 · 경로를 밖에서 받지
않는다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

#: 내줄 문서 · **여기 적힌 것만** 나간다
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


class DocumentNotFound(Exception):
    """목록에 없는 문서이거나, 그림이 없다."""


class DocumentAssetRefused(Exception):
    """그림 폴더 밖이거나 그림이 아니다."""


class DocsService:
    """문서를 목록으로 보여주고, 하나를 읽어 주고, 그림을 찾아 준다."""

    def __init__(self, bridge: Any) -> None:
        self.bridge = bridge

    def _root(self) -> Path:
        """작업공간은 **부를 때** 묻는다 · §6-158

        등록 시점에 한 번 읽으면, 아직 노드가 다 서기 전이라 엉뚱한 곳을
        붙들게 된다 · 시험용 껍데기 브리지에서도 터졌다.
        """
        return Path(getattr(self.bridge, 'workspace_root', None) or Path.cwd())

    def _entry(self, doc_id: Any) -> Dict[str, str]:
        for document in DOCUMENTS:
            if document['id'] == str(doc_id):
                return document
        raise DocumentNotFound(f'그런 문서가 없습니다: {doc_id}')

    # ----------------------------------------------------------------- #
    # 읽기
    # ----------------------------------------------------------------- #

    def listing(self) -> Dict[str, Any]:
        root = self._root()
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

    def read(self, doc_id: Any) -> Dict[str, Any]:
        """문서 하나 · 파일이 없어도 **성공으로 돌려준다** · §6-157

        없는 것을 404 로 던지면 화면에 「통신 오류」로 보인다 · 사실은
        `git pull` 을 아직 안 해서 없는 것뿐이다 · 그래서 「아직 없다」고
        말해 준다 · 목록에 **없는 문서**를 물은 것과는 다른 일이다.
        """
        document = self._entry(doc_id)
        path = self._root() / document['path']
        if not path.is_file():
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

    def asset_path(self, name: Any) -> Path:
        """`docs/images` 안의 그림 하나 · **밖으로 나가는 경로는 막는다.**

        `../../etc/passwd` 같은 것이 들어온다 · 이름을 그대로 붙여 열면
        저장소 밖 파일이 나간다 · 그래서 실제 경로로 풀어 본 뒤, 그림
        폴더 아래인지 확인한다.
        """
        base = (self._root() / ASSET_DIR).resolve()
        target = (base / str(name)).resolve()
        if base not in target.parents and target != base:
            raise DocumentAssetRefused('문서 그림 폴더 밖입니다')
        if target.suffix.lower() not in ASSET_SUFFIXES:
            raise DocumentAssetRefused('그림 파일이 아닙니다')
        if not target.is_file():
            raise DocumentNotFound('그림이 없습니다')
        return target
