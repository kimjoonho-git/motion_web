"""Read and atomically update the MIDI-bank section owned by a mapping YAML."""

from motion_common import store as common_store

import time
from pathlib import Path
from typing import Any, Dict, Optional

import yaml
from yaml.nodes import MappingNode, ScalarNode


BLOCK_START = '# motion-control-web: midi-banks start'
BLOCK_END = '# motion-control-web: midi-banks end'


def _top_level_key_span(content: str, key: str) -> Optional[tuple[int, int]]:
    root = yaml.compose(content)
    if not isinstance(root, MappingNode):
        return None
    for key_node, value_node in root.value:
        if isinstance(key_node, ScalarNode) and key_node.value == key:
            return key_node.start_mark.index, value_node.end_mark.index
    return None


def load_midi_banks(mapping_file: Path) -> Optional[Dict[str, Any]]:
    root = yaml.safe_load(mapping_file.read_text(encoding='utf-8')) or {}
    if not isinstance(root, dict):
        raise ValueError('motion-axis mapping YAML root must be an object')
    section = root.get('midi_banks')
    if section is None:
        return None
    if not isinstance(section, dict):
        raise ValueError('midi_banks must be an object')
    return section


def render_with_midi_banks(existing: str, state: Dict[str, Any]) -> str:
    root = yaml.safe_load(existing) or {}
    if not isinstance(root, dict):
        raise ValueError('motion-axis mapping YAML root must be an object')
    if not isinstance(state, dict):
        raise ValueError('midi_banks must be an object')

    start = existing.find(BLOCK_START)
    end = existing.find(BLOCK_END)
    if (start < 0) != (end < 0):
        raise ValueError('motion-axis mapping contains an incomplete MIDI bank block')

    section = yaml.safe_dump(
        {'midi_banks': state},
        sort_keys=False,
        allow_unicode=True,
    ).rstrip()
    block = f'{BLOCK_START}\n{section}\n{BLOCK_END}'

    if start >= 0:
        if end < start:
            raise ValueError('motion-axis mapping MIDI bank block is malformed')
        end += len(BLOCK_END)
        return f'{existing[:start]}{block}{existing[end:]}'.rstrip() + '\n'
    if 'midi_banks' in root:
        span = _top_level_key_span(existing, 'midi_banks')
        if span is None:
            raise ValueError('failed to locate existing midi_banks section')
        section_start, section_end = span
        return f'{existing[:section_start]}{block}{existing[section_end:]}'.rstrip() + '\n'
    return f'{existing.rstrip()}\n\n{block}\n'


def atomic_write_with_backup(
    path: Path, updated: str, backup_dir: Optional[Path] = None
) -> Optional[Path]:
    """이전 내용을 백업하고 원자적으로 교체한다 · 프로세스 간 락 안에서.

    같은 모션축 설정 파일을 웹 브리지(`project_repository`)도 쓴다. 읽고-백업하고-
    쓰는 구간을 통째로 감싸야 두 프로세스가 서로의 수정을 지우지 않는다 · §6-24
    """
    with common_store.locked_update(path):
        existing = path.read_text(encoding='utf-8') if path.is_file() else None
        backup = None
        if existing is not None:
            timestamp = time.strftime('%Y%m%d-%H%M%S')
            backup_root = Path(backup_dir) if backup_dir is not None else path.parent
            backup_root.mkdir(parents=True, exist_ok=True)
            backup = backup_root / f'{timestamp}-{path.name}'
            counter = 2
            while backup.exists():
                backup = backup_root / f'{timestamp}-{counter}-{path.name}'
                counter += 1
            common_store.atomic_write_text(backup, existing)
        common_store.atomic_write_text(path, updated)
        return backup


def save_midi_banks(
    mapping_file: Path, state: Dict[str, Any], backup_dir: Optional[Path] = None
) -> Path:
    """MIDI 뱅크를 모션축 설정 파일에 반영한다.

    읽기부터 기록까지 한 락 안에서 한다 · 락 밖에서 읽으면 그 사이 다른
    프로세스의 수정을 못 보고 덮어쓴다. 안쪽 `atomic_write_with_backup`도 같은
    락을 잡지만 재진입 가능하다 · §6-24
    """
    with common_store.locked_update(mapping_file):
        existing = mapping_file.read_text(encoding='utf-8')
        updated = render_with_midi_banks(existing, state)
        backup = atomic_write_with_backup(mapping_file, updated, backup_dir)
        if backup is None:  # save_midi_banks always requires an existing mapping.
            raise ValueError(f'motion-axis mapping YAML not found: {mapping_file}')
        return backup
