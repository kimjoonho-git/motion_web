"""Start the managed stack only from the currently selected project."""

from __future__ import annotations

import json
import hashlib
import os
import sys
from pathlib import Path
from typing import Optional


MOTOR_CONFIG_ERROR_EXIT = 78


def resolve_applied_motor_config(workspace: Path) -> Optional[Path]:
    projects_root = (workspace / 'motion_projects').resolve()
    runtime_state_file = projects_root / '.motor_runtime.json'
    try:
        runtime_state = json.loads(runtime_state_file.read_text(encoding='utf-8'))
    except (OSError, ValueError, json.JSONDecodeError):
        runtime_state = None
    if isinstance(runtime_state, dict) and runtime_state.get('version') == 1:
        project_id = str(runtime_state.get('target_project_id') or '').strip()
        expected_sha = str(runtime_state.get('config_sha256') or '').strip()
        if project_id and project_id == Path(project_id).name and expected_sha:
            candidate = (
                projects_root / project_id / 'runtime' / 'applied_motor_config.yaml'
            ).resolve()
            try:
                candidate.relative_to(projects_root)
                actual_sha = hashlib.sha256(candidate.read_bytes()).hexdigest()
            except (OSError, ValueError):
                return None
            return candidate if actual_sha == expected_sha else None
        return None

    # One-release compatibility for workspaces not yet opened by
    # ProjectRepository.  Repository initialization migrates this field to the
    # independent motor runtime record.
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
    if '--print-config' in sys.argv:
        print(str(runtime_config) if runtime_config else '')
        return
    if runtime_config is None:
        print(
            'Motor Manager 시작 실패: 검증된 모터 실행 설정이 없습니다.',
            flush=True,
        )
        raise SystemExit(MOTOR_CONFIG_ERROR_EXIT)

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
