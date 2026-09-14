"""업데이트 스크립트를 실제로 돌려 본다 · §6-97

성질만 글자로 확인하면(`test_workspace_update.py`) **한 번도 안 돌아 본
경로**가 남는다 · 되돌리기와 실패 경로가 바로 그런 자리이고, 거기가 깨지면
장비가 멈춘 채로 남는다.

그래서 가짜 원격·가짜 빌드로 전체를 돌린다 · `MOTION_UPDATE_SERVICES` 를
비워 실제 서비스는 건드리지 않는다.
"""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT_RELATIVE = 'src/motion_web/web_bridge/deploy/update_workspace.sh'
INSTALLER_RELATIVE = 'src/motion_web/web_bridge/deploy/install_user_service.sh'
REAL_SCRIPT = Path(__file__).resolve().parents[1] / 'deploy/update_workspace.sh'


def _git(repo, *args):
    subprocess.run(
        ['git', *args], cwd=str(repo), check=True, capture_output=True, text=True,
    )


#: 스크립트를 bash 의 읽기 버퍼(8KB)보다 크게 만든다
#:
#: 작으면 bash 가 한 번에 다 읽어 버려 **자기를 덮어도 아무 일이 안 난다** ·
#: 지금 스크립트는 7KB 라 우연히 그 안에 들어간다 · 한 번만 더 자라면 위험이
#: 살아난다 · 검사는 그 자란 상태를 미리 만들어 둔다.
def _padded(text, marker, lines):
    # 채움을 **받은 다음에 실행되는 자리**에 넣는다 · 합치기 앞쪽에 넣으면
    # bash 가 이미 다 읽어 둔 뒤라 파일이 바뀌어도 아무 일이 안 난다 ·
    # 뒤쪽에 8KB 넘게 남아 있어야 다시 읽으면서 어긋난다
    anchor = "write_state running building"
    assert anchor in text, '채움을 넣을 자리를 못 찾았다'
    filler = '\n'.join(f'# {marker} {index:05d} ' + 'x' * 60 for index in range(lines))
    return text.replace(anchor, f'{filler}\n{anchor}', 1)


def _installer(exit_code):
    return f'#!/usr/bin/env bash\necho "가짜 설치 · exit {exit_code}"\nexit {exit_code}\n'


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8')
    path.chmod(0o755)


@pytest.fixture
def world(tmp_path):
    """원격 하나 · 작업공간 하나 · 가짜 빌드 도구."""
    origin = tmp_path / 'origin.git'
    seed = tmp_path / 'seed'
    workspace = tmp_path / 'ws'
    subprocess.run(
        ['git', 'init', '--bare', '-b', 'main', str(origin)],
        check=True, capture_output=True,
    )
    seed.mkdir()
    _git(seed, 'init', '-b', 'main', '.')
    _git(seed, 'config', 'user.email', 'test@example.com')
    _git(seed, 'config', 'user.name', 'test')
    _write(
        seed / SCRIPT_RELATIVE,
        _padded(REAL_SCRIPT.read_text(encoding='utf-8'), '채움', 400),
    )
    _write(seed / INSTALLER_RELATIVE, _installer(0))
    _git(seed, 'add', '-A')
    _git(seed, 'commit', '-m', '처음')
    _git(seed, 'remote', 'add', 'origin', str(origin))
    _git(seed, 'push', '-q', 'origin', 'main')
    subprocess.run(
        ['git', 'clone', '-q', str(origin), str(workspace)],
        check=True, capture_output=True,
    )

    fake_bin = tmp_path / 'bin'
    fake_bin.mkdir()
    _write(fake_bin / 'colcon', '#!/usr/bin/env bash\necho "가짜 빌드"\n')
    (tmp_path / 'ros_setup.bash').write_text('# 비어 있다\n', encoding='utf-8')

    def publish(*, script_text=None, installer_exit=None, note='다음'):
        """원격에 새 커밋을 하나 올린다."""
        if script_text is not None:
            _write(seed / SCRIPT_RELATIVE, script_text)
        if installer_exit is not None:
            _write(seed / INSTALLER_RELATIVE, _installer(installer_exit))
        (seed / 'note.txt').write_text(note, encoding='utf-8')
        _git(seed, 'add', '-A')
        _git(seed, 'commit', '-m', note)
        _git(seed, 'push', '-q', 'origin', 'main')

    def run(distributions=''):
        environment = {
            **os.environ,
            'MOTION_WORKSPACE': str(workspace),
            'MOTION_UPDATE_STATE_DIR': str(tmp_path / 'state'),
            'MOTION_UPDATE_SERVICES': '',
            'MOTION_UPDATE_DISTRIBUTIONS': distributions,
            'MOTION_ROS_SETUP': str(tmp_path / 'ros_setup.bash'),
            'PATH': f'{fake_bin}{os.pathsep}{os.environ["PATH"]}',
        }
        environment.pop('MOTION_UPDATE_REEXEC', None)
        completed = subprocess.run(
            ['bash', str(workspace / SCRIPT_RELATIVE)],
            env=environment, capture_output=True, text=True, timeout=120,
        )
        state = json.loads(
            (tmp_path / 'state/state.json').read_text(encoding='utf-8')
        )
        log = (tmp_path / 'state/update.log').read_text(encoding='utf-8')
        return completed, state, log

    def head():
        return subprocess.run(
            ['git', 'rev-parse', 'HEAD'], cwd=str(workspace),
            capture_output=True, text=True, check=True,
        ).stdout.strip()

    return {
        'workspace': workspace, 'publish': publish, 'run': run, 'head': head,
    }


# --------------------------------------------------------------------- #
# 되는 길
# --------------------------------------------------------------------- #

def test_it_pulls_and_ends_on_the_new_commit(world):
    before = world['head']()
    world['publish'](note='새 기능')

    completed, state, _log = world['run']()

    assert completed.returncode == 0, completed.stderr
    assert state['status'] == 'success'
    assert state['phase'] == 'done'
    assert world['head']() != before
    assert state['rolled_back'] is False


def test_it_survives_rewriting_itself(world):
    """받는 도중 이 스크립트 자신이 바뀌어도 끝까지 간다 · 오늘도 이 파일을
    두 번 고쳤다.

    이 검사만으로는 위험을 증명하지 못한다 · bash 는 바뀐 지점의 **한 줄만**
    망가뜨리고 다음 줄바꿈에서 다시 맞추므로, 그 한 줄이 주석이면 아무 일도
    안 난다 · 실제로 깨지는 것은 그 줄이 실행문일 때다(43KB 스크립트로 재현 ·
    `line 611: ER: command not found`).

    그래서 대비(복사본에서 다시 시작)는 `test_workspace_update.py` 가 **있는지**
    로 지킨다 · 여기서는 그 대비를 둔 채 전체가 도는지만 본다.
    """
    # 길이가 달라지면 그 뒤 글자 위치가 통째로 밀린다 · 대비가 없으면 bash 가
    # 읽던 자리부터 엉뚱한 글을 읽는다
    grown = _padded(REAL_SCRIPT.read_text(encoding='utf-8'), '바뀜', 700)
    world['publish'](script_text=grown, note='스크립트 자신을 고침')

    completed, state, _log = world['run']()

    assert completed.returncode == 0, completed.stderr
    assert state['status'] == 'success'
    assert state['phase'] == 'done'
    # 새 내용이 실제로 들어와 있다
    assert '바뀜 00500' in (
        world['workspace'] / SCRIPT_RELATIVE
    ).read_text(encoding='utf-8')


# --------------------------------------------------------------------- #
# 안 되는 길 · 여기가 중요하다
# --------------------------------------------------------------------- #

def test_a_failure_keeps_the_new_code_and_starts_what_it_can(world):
    """**옛 코드로 되돌리지 않는다.**

    전에는 되돌렸는데, 그러면 방금 받은 고침까지 함께 지워져 다음 시도도 같은
    자리에서 실패한다 · 실제로 한 대가 그 고리에 빠졌다.

    코드는 새것으로 두고, 켤 수 있는 것은 켜고 나간다 · 고침이 올라오면 다시
    누르기만 하면 된다.
    """
    before = world['head']()
    world['publish'](installer_exit=1, note='설치가 깨지는 커밋')

    completed, state, log = world['run']()

    assert completed.returncode != 0
    assert state['status'] == 'failure'
    assert world['head']() != before, '옛 코드로 되돌렸다'
    assert '되돌린다' not in log
    assert '서비스만 켠다' in log, '켤 수 있는 것도 안 켰다'


def test_needing_a_human_is_not_a_rollback(world):
    """설치 스크립트는 실시간 권한처럼 사람이 sudo 로 해야 할 일이 남으면
    78 로 끝난다 · 새 PC 에서 늘 그렇다 · 받기도 빌드도 끝났는데 되돌리면
    멀쩡한 새 코드를 버린다."""
    before = world['head']()
    world['publish'](installer_exit=78, note='사람 손이 필요한 커밋')

    completed, state, _log = world['run']()

    assert completed.returncode == 0
    assert state['status'] == 'success'
    assert state['phase'] == 'needs_attention'
    assert state['rolled_back'] is False
    assert world['head']() != before, '새 코드는 그대로 두어야 한다'


def test_a_dirty_workspace_never_reaches_the_services(world):
    """합치다 사용자의 수정을 덮는 것이 제일 나쁘다 · 서비스를 멈추기 전에
    막아야 한다."""
    world['publish'](note='새 것')
    (world['workspace'] / 'note.txt').write_text('손으로 고침', encoding='utf-8')
    _git(world['workspace'], 'add', '-A')
    (world['workspace'] / 'note.txt').write_text('또 고침', encoding='utf-8')

    completed, state, log = world['run']()

    assert completed.returncode != 0
    assert state['phase'] == 'blocked'
    assert '고친 파일' in state['message']
    assert '서비스를 멈춘다' not in log


def test_the_installer_turns_on_its_own_ros_environment():
    """이것이 진짜 원인이었다 · §6-97

    설치 스크립트는 모터 설정을 읽으려고 실행 파일을 **직접 부른다** · 그
    프로그램은 자기 꾸러미 정보를 찾아야 하고, 그건 `install/setup.bash` 가
    PYTHONPATH 에 넣어 준다.

    사람이 터미널에서 부를 때는 이미 켜져 있어 보이지 않았다 · 웹 업데이트는
    깨끗한 환경(`systemd-run`)에서 돌기 때문에 **늘 여기서 죽었다** ·
    `No package metadata was found for motion-web-bridge`.

        환경 없이 실행 → PackageNotFoundError
        환경 있이 실행 → 정상

    그래서 스크립트가 스스로 환경을 켠다 · 누가 부르든 같게 동작해야 한다.
    """
    from pathlib import Path as _Path
    installer = (
        _Path(__file__).resolve().parents[1] / 'deploy/install_user_service.sh'
    ).read_text(encoding='utf-8')

    assert 'source "${WORKSPACE}/install/setup.bash"' in installer
    assert installer.index('source "${WORKSPACE}/install/setup.bash"') < (
        installer.index('--print-config')
    ), '실행 파일을 부른 뒤에 환경을 켠다'


def test_a_failed_build_is_retried_from_scratch(world, tmp_path):
    """옛 결과가 새 빌드를 막는 경우가 있다 · 빌드 방식이 한 번이라도
    달라지면(링크 ↔ 복사) 부딪힌다 ·

        failed to create symbolic link ... existing path cannot be removed

    사람이 하던 "지우고 다시 빌드" 를 스크립트가 한다 · 늘 지우지는 않는다.
    """
    (tmp_path / 'ws/build/옛찌꺼기').mkdir(parents=True, exist_ok=True)
    counter = tmp_path / 'bin/tries'
    (tmp_path / 'bin/colcon').write_text(
        '#!/usr/bin/env bash\n'
        f'echo x >> "{counter}"\n'
        f'[[ $(wc -l < "{counter}") -ge 2 ]] || {{ echo "첫 번째 실패"; exit 1; }}\n'
        'echo "가짜 빌드 · 통과"\n',
        encoding='utf-8',
    )
    (tmp_path / 'bin/colcon').chmod(0o755)
    world['publish'](note='새 것')

    completed, state, log = world['run']()

    assert completed.returncode == 0, completed.stderr
    assert state['status'] == 'success'
    assert '지우고 처음부터 다시 빌드한다' in log
    assert not (tmp_path / 'ws/build/옛찌꺼기').exists()


def test_a_build_that_only_fails_together_gets_one_more_pass(world, tmp_path):
    """전체를 한꺼번에 빌드할 때만 가끔 깨지는 꾸러미가 있다 · 단독으로는 잘
    된다 · 이어서 한 번 더 하면 넘어간다 · 실제로 `robot_manager` 가 그랬다.
    """
    counter = tmp_path / 'bin/tries'
    (tmp_path / 'bin/colcon').write_text(
        '#!/usr/bin/env bash\n'
        f'echo x >> "{counter}"\n'
        f'[[ $(wc -l < "{counter}") -ge 3 ]] || {{ echo "Failed <<< robot_manager"; exit 1; }}\n'
        'echo "가짜 빌드 · 통과"\n',
        encoding='utf-8',
    )
    (tmp_path / 'bin/colcon').chmod(0o755)
    world['publish'](note='새 것')

    completed, state, log = world['run']()

    assert completed.returncode == 0, completed.stderr
    assert state['status'] == 'success'
    assert '지우고 처음부터 다시 빌드한다' in log
    assert '한 번 더 이어서 빌드한다' in log
