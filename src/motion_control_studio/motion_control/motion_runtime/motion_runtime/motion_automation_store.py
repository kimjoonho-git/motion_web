"""Project-isolated persistence for automatic motion playback."""

from __future__ import annotations

import json
import math
import time
import uuid
from pathlib import Path
from typing import Any, Dict


AUTOMATION_VERSION = 1
REPEAT_MODES = {'direct', 'dwell', 'reinitialize', 'dwell_reinitialize'}


def default_automation_state() -> Dict[str, Any]:
    return {
        'version': AUTOMATION_VERSION,
        'enabled': False,
        'armed': False,
        'repeat_mode': 'direct',
        'dwell_sec': 0.0,
        'motion_file_id': '',
        'mapping_file_id': '',
        'motion_sha256': '',
        'mapping_sha256': '',
        'last_error': '',
        'updated_at': None,
    }


def normalize_automation_state(value: Any) -> Dict[str, Any]:
    source = value if isinstance(value, dict) else {}
    state = default_automation_state()
    state['enabled'] = bool(source.get('enabled', False))
    state['armed'] = bool(source.get('armed', False)) and state['enabled']
    repeat_mode = str(source.get('repeat_mode') or 'direct').strip().lower()
    if repeat_mode not in REPEAT_MODES:
        raise ValueError(f'지원하지 않는 자동 반복 방식입니다: {repeat_mode}')
    state['repeat_mode'] = repeat_mode
    try:
        dwell_sec = float(source.get('dwell_sec') or 0.0)
    except (TypeError, ValueError) as exc:
        raise ValueError('자동 반복 대기 시간은 숫자여야 합니다') from exc
    if not math.isfinite(dwell_sec) or dwell_sec < 0.0:
        raise ValueError('자동 반복 대기 시간은 0초 이상이어야 합니다')
    state['dwell_sec'] = dwell_sec
    for key in (
        'motion_file_id',
        'mapping_file_id',
        'motion_sha256',
        'mapping_sha256',
        'last_error',
    ):
        state[key] = str(source.get(key) or '').strip()
    updated_at = source.get('updated_at')
    state['updated_at'] = (
        float(updated_at)
        if isinstance(updated_at, (int, float)) and math.isfinite(float(updated_at))
        else None
    )
    return state


class MotionAutomationStore:
    """Read and atomically update one project's runtime automation state."""

    def __init__(self, projects_root: Path | str) -> None:
        self.projects_root = Path(projects_root).expanduser().resolve()

    def load(self, project_id: Any) -> Dict[str, Any]:
        path = self._state_path(project_id)
        if not path.exists():
            return default_automation_state()
        try:
            payload = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError('자동 반복 설정 파일을 읽을 수 없습니다') from exc
        return normalize_automation_state(payload)

    def save(self, project_id: Any, value: Any) -> Dict[str, Any]:
        state = normalize_automation_state(value)
        state['updated_at'] = time.time()
        path = self._state_path(project_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f'.{path.name}.{uuid.uuid4().hex}.tmp')
        text = json.dumps(state, ensure_ascii=False, indent=2) + '\n'
        try:
            temporary.write_text(text, encoding='utf-8')
            temporary.replace(path)
        finally:
            if temporary.exists():
                temporary.unlink()
        return dict(state)

    def _state_path(self, project_id: Any) -> Path:
        name = str(project_id or '').strip()
        if not name or name != Path(name).name or '/' in name or '\\' in name:
            raise ValueError('올바른 프로젝트 ID가 필요합니다')
        project_dir = (self.projects_root / name).resolve()
        try:
            project_dir.relative_to(self.projects_root)
        except ValueError as exc:
            raise ValueError('프로젝트 경로가 작업공간 밖을 가리킵니다') from exc
        if not (project_dir / 'project.json').is_file():
            raise ValueError(f'프로젝트를 찾을 수 없습니다: {name}')
        return project_dir / 'runtime' / 'motion_automation.json'
