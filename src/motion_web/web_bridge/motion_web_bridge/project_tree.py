"""프로젝트 파일 트리 · 화면에 보여줄 목록을 만든다.

`ProjectRepository`에서 떼어냈다 · §6-46

읽기만 한다 · 저장소 상태를 만지지 않는다. 프로젝트 폴더를 훑어 파일과 해시,
활성 여부, MIDI 뱅크 요약을 모은다.

숨김 파일과 심볼릭 링크는 세지 않는다 · 락 파일을 사용자 파일로 세면 목록과
활성 판정이 어긋난다(§6-24).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import yaml

from .project_paths import (
    DISPLAY_NAMES,
    PROJECT_CATEGORIES,
    _sha256,
    _sha256_file,
    local_directory,
)


def build_tree(project_dir: Path, manifest: Dict[str, Any]) -> list[Dict[str, Any]]:
    tree = []
    manifest_path = project_dir / 'project.json'
    manifest_content = manifest_path.read_bytes()
    tree.append({
        'category': 'project_root',
        'name': '프로젝트 정보',
        'read_only': True,
        'children': [{
            'node_type': 'file',
            'name': manifest_path.name,
            'relative_path': manifest_path.name,
            'category': 'project_root',
            'size': len(manifest_content),
            'sha256': _sha256(manifest_content),
            'active': False,
            'read_only': True,
            'internal': True,
        }],
    })
    active = manifest.get('active_files') or {}
    for category in PROJECT_CATEGORIES:
        children = []
        for path in sorted(
            (project_dir / category).iterdir(), key=lambda item: item.name.lower()
        ):
            if not path.is_file() or path.is_symlink():
                continue
            if path.suffix.lower() not in PROJECT_CATEGORIES[category]:
                continue
            size = path.stat().st_size
            children.append({
                'name': path.name,
                'category': category,
                'size': size,
                'sha256': _sha256_file(path),
                'active': active.get(category) == path.name,
                **(
                    {'midi_banks': midi_bank_tree_info(path)}
                    if category == 'motion_axis_matching' else {}
                ),
            })
        tree.append({
            'category': category,
            'name': DISPLAY_NAMES[category],
            'children': children,
        })
    logs_dir = local_directory(project_dir, 'logs')
    log_children = []
    for path in sorted(logs_dir.glob('*.jsonl'), reverse=True):
        if not path.is_file() or path.is_symlink():
            continue
        try:
            size = path.stat().st_size
            record_count = sum(
                1 for line in path.read_text(encoding='utf-8').splitlines() if line.strip()
            )
        except OSError:
            continue
        log_children.append({
            'name': path.name,
            'category': 'logs',
            'size': size,
            'record_count': record_count,
            'active': False,
        })
    tree.append({
        'category': 'logs',
        'name': '로그',
        'children': log_children,
    })
    for category, label in (
        ('runtime', 'runtime · 실행용'),
        ('trash', 'trash · 휴지통'),
    ):
        directory = local_directory(project_dir, category)
        tree.append({
            'category': category,
            'name': label,
            'read_only': True,
            'children': read_only_directory_tree(directory, project_dir, category),
        })
    return tree

def read_only_directory_tree(
    directory: Path,
    project_dir: Path,
    category: str,
) -> list[Dict[str, Any]]:
    """Return the real on-disk subtree without exposing mutation APIs."""
    nodes: list[Dict[str, Any]] = []
    try:
        entries = sorted(
            directory.iterdir(),
            key=lambda item: (not item.is_dir(), item.name.lower()),
        )
    except OSError:
        return nodes
    for path in entries:
        if path.is_symlink() or path.name.startswith('.'):
            continue
        try:
            relative_path = path.relative_to(project_dir).as_posix()
            if path.is_dir():
                nodes.append({
                    'node_type': 'folder',
                    'name': path.name,
                    'relative_path': relative_path,
                    'category': category,
                    'read_only': True,
                    'internal': True,
                    'children': read_only_directory_tree(
                        path, project_dir, category
                    ),
                })
                continue
            if not path.is_file():
                continue
            content = path.read_bytes()
        except OSError:
            continue
        nodes.append({
            'node_type': 'file',
            'name': path.name,
            'relative_path': relative_path,
            'category': category,
            'size': len(content),
            'sha256': _sha256(content),
            'active': False,
            'read_only': True,
            'internal': True,
        })
    return nodes

def midi_bank_tree_info(path: Path) -> Dict[str, Any]:
    """Describe the MIDI banks embedded in one project-local mapping file."""
    try:
        root = yaml.safe_load(path.read_text(encoding='utf-8')) or {}
    except (OSError, yaml.YAMLError):
        root = {}
    state = root.get('midi_banks') if isinstance(root, dict) else None
    if not isinstance(state, dict):
        return {
            'stored': False,
            'count': 0,
            'active_bank_id': '',
            'banks': [],
        }
    banks = []
    for item in state.get('banks') or []:
        if not isinstance(item, dict):
            continue
        mappings = item.get('mappings')
        banks.append({
            'bank_id': str(item.get('bank_id') or ''),
            'name': str(item.get('name') or item.get('bank_id') or '이름 없음'),
            'mapping_count': len(mappings) if isinstance(mappings, list) else 0,
        })
    return {
        'stored': True,
        'count': len(banks),
        'active_bank_id': str(state.get('active_bank_id') or ''),
        'banks': banks,
    }
