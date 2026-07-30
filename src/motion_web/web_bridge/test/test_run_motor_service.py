import os
import subprocess
from pathlib import Path


def _write_executable(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding='utf-8')
    path.chmod(0o755)


def test_ethercat_error_recovery_keeps_master_and_slave_position_together(
    tmp_path,
):
    workspace = tmp_path / 'workspace'
    config_file = tmp_path / 'motor.yaml'
    config_file.write_text('masters: []\n', encoding='utf-8')
    (workspace / 'install').mkdir(parents=True)
    (workspace / 'install' / 'setup.bash').write_text('', encoding='utf-8')

    command_log = tmp_path / 'ethercat.log'
    recovery_state = tmp_path / 'recovery'
    fake_bin = tmp_path / 'bin'
    fake_bin.mkdir()

    _write_executable(
        workspace
        / 'install'
        / 'motion_control_bridge'
        / 'lib'
        / 'motion_control_bridge'
        / 'motor_manager_node',
        '#!/usr/bin/env bash\nexit 0\n',
    )
    _write_executable(
        fake_bin / 'timeout',
        '#!/usr/bin/env bash\nshift\nexec "$@"\n',
    )
    _write_executable(fake_bin / 'sleep', '#!/usr/bin/env bash\nexit 0\n')
    _write_executable(
        fake_bin / 'ethercat',
        '''#!/usr/bin/env bash
set -eu
echo "$*" >> "${FAKE_ETHERCAT_LOG}"
command_name="$1"
shift
case "${command_name}" in
  master)
    printf 'Master0\\nMaster1\\n'
    ;;
  slaves)
    master_index=""
    while (($#)); do
      case "$1" in
        -m) master_index="$2"; shift 2 ;;
        *) shift ;;
      esac
    done
    if [[ ! -f "${FAKE_ETHERCAT_STATE}/master-${master_index}" ]]; then
      printf '1  0:1  PREOP  E  MCDLN35BE\\n'
    else
      printf '1  0:1  PREOP  +  MCDLN35BE\\n'
    fi
    ;;
  reg_write)
    master_index=""
    while (($#)); do
      case "$1" in
        -m) master_index="$2"; shift 2 ;;
        *) shift ;;
      esac
    done
    mkdir -p "${FAKE_ETHERCAT_STATE}"
    touch "${FAKE_ETHERCAT_STATE}/master-${master_index}"
    ;;
  states)
    ;;
esac
''',
    )

    script = (
        Path(__file__).resolve().parents[1]
        / 'deploy'
        / 'run_motor_service.sh'
    )
    environment = os.environ.copy()
    environment.update({
        'MOTION_WORKSPACE': str(workspace),
        'MOTOR_CONFIG_FILE': str(config_file),
        'FAKE_ETHERCAT_LOG': str(command_log),
        'FAKE_ETHERCAT_STATE': str(recovery_state),
        'PATH': f'{fake_bin}:{environment["PATH"]}',
    })

    completed = subprocess.run(
        ['/bin/bash', str(script)],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )

    assert completed.returncode == 0, completed.stderr
    commands = command_log.read_text(encoding='utf-8').splitlines()
    assert 'slaves -m 0' in commands
    assert 'slaves -m 1' in commands
    assert (
        'reg_write -m 0 -p 1 -t uint16 0x0120 0x0011'
        in commands
    )
    assert (
        'reg_write -m 1 -p 1 -t uint16 0x0120 0x0011'
        in commands
    )
    assert 'states -m 0 -p 1 PREOP' in commands
    assert 'states -m 1 -p 1 PREOP' in commands
