"""Start the managed motion-control stack from the last explicitly applied project."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Optional


def resolve_applied_motor_config(workspace: Path) -> Optional[Path]:
    projects_root = (workspace / 'motion_projects').resolve()
    selection_file = projects_root / '.selected_project.json'
    try:
        payload = json.loads(selection_file.read_text(encoding='utf-8'))
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    project_id = str(payload.get('applied_project_id') or '').strip()
    if not project_id or project_id != Path(project_id).name:
        return None
    candidate = (
        projects_root / project_id / 'runtime' / 'applied_motor_config.yaml'
    ).resolve()
    try:
        candidate.relative_to(projects_root)
    except ValueError:
        return None
    # A full-program restart restores only the last configuration which the
    # user explicitly applied. Unapplied project edits remain separate and
    # must never replace this runtime file implicitly.
    return candidate if candidate.is_file() else None


def main() -> None:
    workspace = Path(
        os.environ.get('MOTION_WORKSPACE') or Path.cwd()
    ).expanduser().resolve()
    restart_script = workspace / 'scripts' / 'restart_motion_monitor.sh'
    if not restart_script.is_file():
        raise SystemExit(f'restart script not found: {restart_script}')

    runtime_config = resolve_applied_motor_config(workspace)
    if '--print-config' in sys.argv:
        print(str(runtime_config) if runtime_config else '')
        return

    environment = dict(os.environ)
    environment['MOTION_WORKSPACE'] = str(workspace)
    environment.setdefault('ROS_LOCALHOST_ONLY', '1')
    if runtime_config:
        environment['MOTOR_CONFIG_FILE'] = str(runtime_config)
    else:
        environment.pop('MOTOR_CONFIG_FILE', None)
    os.execvpe(
        '/bin/bash',
        ['/bin/bash', str(restart_script)],
        environment,
    )


if __name__ == '__main__':
    main()
