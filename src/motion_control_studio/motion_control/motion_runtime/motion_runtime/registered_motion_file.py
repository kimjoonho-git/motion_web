"""재생 등록 칸만 따로 읽고 쓴다 · §6-160

모션축 매칭 파일 하나에 **주인이 셋**이다.

    mappings        모션축 설정 화면
    midi_banks      MIDI 입력 설정 화면
    motion_file_id  모션 실행 화면 (재생 등록)

MIDI 는 오래전에 제 길을 얻었다 (`midi_bank_store`) · 재생 등록만 남아서
「설정 전체 저장」 길로 다녔다 · 그래서 모션 파일 하나 바꾸려는 사람이
모션축 설정을 통째로 덮어쓰게 됐고, 편집 중이면 그마저 막혔다.

여기서는 **그 한 칸만** 건드린다 · 나머지 글자는 손대지 않는다 · 편집 중인
모션축 설정이 있어도 그 위를 지나가지 않는다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

import yaml

from motion_common import store as common_store

from motion_runtime.midi_bank_store import _top_level_key_span, atomic_write_with_backup


def load_registered_motion_file(mapping_file: Path) -> str:
    root = yaml.safe_load(mapping_file.read_text(encoding='utf-8')) or {}
    if not isinstance(root, dict):
        raise ValueError('motion-axis mapping YAML root must be an object')
    return str(root.get('motion_file_id') or '').strip()


def render_with_registered_motion_file(existing: str, motion_file_id: Any) -> str:
    """`motion_file_id:` 줄 하나만 갈아 끼운 본문을 돌려준다."""
    root = yaml.safe_load(existing) or {}
    if not isinstance(root, dict):
        raise ValueError('motion-axis mapping YAML root must be an object')

    value = str(motion_file_id or '').strip()
    line = yaml.safe_dump(
        {'motion_file_id': value},
        sort_keys=False,
        allow_unicode=True,
    ).rstrip()

    if 'motion_file_id' in root:
        span = _top_level_key_span(existing, 'motion_file_id')
        if span is None:
            raise ValueError('failed to locate existing motion_file_id')
        start, end = span
        return f'{existing[:start]}{line}{existing[end:]}'.rstrip() + '\n'

    # 칸이 없던 파일 · 이름 뒤가 제자리다 · 없으면 맨 앞에 둔다
    span = _top_level_key_span(existing, 'name')
    if span is None:
        return f'{line}\n{existing.lstrip()}'
    return f'{existing[:span[1]]}\n{line}{existing[span[1]:]}'.rstrip() + '\n'


def save_registered_motion_file(
    mapping_file: Path,
    motion_file_id: Any,
    backup_dir: Optional[Path] = None,
) -> Optional[Path]:
    """다른 쓰는 쪽과 같은 락 안에서 바꾼다 · §6-24

    모션축 설정 저장과 MIDI 뱅크 저장도 같은 파일을 쓴다 · 읽고-고치고-쓰는
    구간을 통째로 감싸야 서로의 수정을 지우지 않는다.
    """
    with common_store.locked_update(mapping_file):
        existing = mapping_file.read_text(encoding='utf-8')
        updated = render_with_registered_motion_file(existing, motion_file_id)
        if updated == existing:
            return None
        return atomic_write_with_backup(mapping_file, updated, backup_dir)
