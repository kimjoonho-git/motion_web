"""Start the managed stack only from the currently selected project."""

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
    selected_project_id = str(payload.get('project_id') or '').strip()
    project_id = str(payload.get('applied_project_id') or '').strip()
    if project_id != selected_project_id:
        return None
    if not project_id or project_id != Path(project_id).name:
        return None
    candidate = (
        projects_root / project_id / 'runtime' / 'applied_motor_config.yaml'
    ).resolve()
    try:
        candidate.relative_to(projects_root)
    except ValueError:
        return None
    # A full-program restart may restore only the selected project's explicit
    # runtime. A previous project's runtime must never cross this boundary.
    return candidate if candidate.is_file() else None


def resolve_project_generation(workspace: Path) -> int:
    """Return the persisted generation owned by the selected project runtime."""
    selection_file = workspace.resolve() / 'motion_projects' / '.selected_project.json'
    try:
        payload = json.loads(selection_file.read_text(encoding='utf-8'))
        generation = int(payload.get('project_generation'))
    except (AttributeError, OSError, TypeError, ValueError, json.JSONDecodeError):
        return 0
    return generation if generation > 0 else 0


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
    environment['MOTION_PROJECT_GENERATION'] = str(
        resolve_project_generation(workspace)
    )
    # The installed upper-level service must never recreate Motor Manager.
    # motion-motor.service owns the driver/EtherCAT process independently.
    environment['START_MOTOR_MANAGER'] = 'false'
    if runtime_config:
        environment['MOTOR_CONFIG_FILE'] = str(runtime_config)
    else:
        environment.pop('MOTOR_CONFIG_FILE', None)
    os.execvpe(
        '/bin/bash',
        ['/bin/bash', str(restart_script)],
        environment,
    )


def motor_main() -> None:
    """Start only the persistent low-level Motor Manager service."""
    workspace = Path(
        os.environ.get('MOTION_WORKSPACE') or Path.cwd()
    ).expanduser().resolve()
    runtime_config = resolve_applied_motor_config(workspace)
    if runtime_config is None:
        print(
            '적용된 프로젝트 모터 설정이 없어 Motor Manager를 시작하지 않습니다.',
            flush=True,
        )
        return

    runner = workspace / 'src' / 'motion_web' / 'web_bridge' / 'deploy' / 'run_motor_service.sh'
    if not runner.is_file():
        raise SystemExit(f'motor service runner not found: {runner}')

    environment = dict(os.environ)
    environment['MOTION_WORKSPACE'] = str(workspace)
    environment.setdefault('ROS_LOCALHOST_ONLY', '1')
    environment['MOTOR_CONFIG_FILE'] = str(runtime_config)
    os.execvpe('/bin/bash', ['/bin/bash', str(runner)], environment)


if __name__ == '__main__':
    main()
