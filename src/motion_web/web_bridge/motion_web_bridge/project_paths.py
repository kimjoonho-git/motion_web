"""프로젝트 경로와 해시 · 공용 순수 함수.

`ProjectRepository`에서 떼어냈다 · §6-46

`project_repository`와 `project_tree`가 함께 쓴다 · 한쪽에 두면 순환 참조가 된다.
경로가 프로젝트 밖으로 새지 않는지 보는 검사가 여기 있다.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any


PROJECT_CATEGORIES = {
    'motor_axes': {'.yaml', '.yml'},
    'motion_axis_matching': {'.yaml', '.yml'},
    'motions': {'.json'},
    'layers': {'.json'},
}
DISPLAY_NAMES = {
    'motor_axes': '모터축 설정',
    'motion_axis_matching': '모션축 설정',
    'motions': '모션 파일',
    'layers': '레이어',
}
def _safe_stem(value: Any, fallback: str = 'project') -> str:
    text = re.sub(r'[^0-9A-Za-z가-힣._-]+', '_', str(value or '').strip())
    text = text.strip('._-')
    return text[:80] or fallback
def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()
def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()
def _is_user_file(path: Path) -> bool:
    """사용자 파일인가 · 숨김 파일과 심볼릭 링크는 제외한다.

    프로세스 간 락이 대상 파일 옆에 `.<이름>.lock`을 만든다(§6-24). 그것을
    사용자 파일로 세면 목록·활성 파일 판정·해시 계산이 전부 어긋난다.
    """
    return path.is_file() and not path.is_symlink() and not path.name.startswith('.')

def local_directory(project_dir: Path, *parts: str) -> Path:
    """Create a directory without following a link outside its project."""
    root = project_dir.resolve()
    current = project_dir
    for part in parts:
        current = current / part
        if current.is_symlink():
            raise ValueError(
                f'프로젝트 내부 폴더는 링크일 수 없습니다: {current.name}'
            )
        if current.exists() and not current.is_dir():
            raise ValueError(
                f'프로젝트 폴더 경로가 올바르지 않습니다: {current.name}'
            )
        current.mkdir(exist_ok=True)
        try:
            current.resolve().relative_to(root)
        except ValueError as exc:
            raise ValueError('프로젝트 외부 폴더는 사용할 수 없습니다') from exc
    return current
