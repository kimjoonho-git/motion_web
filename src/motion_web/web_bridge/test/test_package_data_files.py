"""꾸러미가 설치하겠다고 적은 파일이 실제로 있는가 · §6-99

`setup.py` 의 `data_files` 는 "이 파일들을 설치본에 넣어라" 는 목록이다 ·
거기 적힌 경로와 실제 파일이 어긋나면 빌드가 **그 자리에서** 깨진다 ·

    error: can't copy 'deploy/...': doesn't exist or not a regular file

파일을 옮기거나 지울 때 목록을 같이 안 고치면 난다 · 다른 PC 에서 빌드가
멈춘 원인 중 하나로 지목됐다 · 사람이 매번 확인하지 않도록 검사로 둔다.
"""

import ast
import os
from glob import glob
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parents[4]

#: 별도 저장소는 우리가 고치지 않는다 · 검사도 하지 않는다
SKIP = ('motion_system',)


def _data_files(setup_path: Path):
    """`data_files` 에 적힌 것을 있는 그대로 뽑는다 · glob 은 그대로 둔다."""
    tree = ast.parse(setup_path.read_text(encoding='utf-8'))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        for keyword in node.keywords or []:
            if keyword.arg != 'data_files':
                continue
            for item in keyword.value.elts:
                _destination, files = item.elts
                if isinstance(files, ast.List):
                    for entry in files.elts:
                        try:
                            yield ('file', ast.literal_eval(entry))
                        except ValueError:
                            continue
                elif (
                    isinstance(files, ast.Call)
                    and getattr(files.func, 'id', '') == 'glob'
                ):
                    yield ('glob', ast.literal_eval(files.args[0]))


def _setup_files():
    for setup in sorted((WORKSPACE / 'src').glob('**/setup.py')):
        text = str(setup)
        if '/build/' in text or '/install/' in text:
            continue
        if any(name in text for name in SKIP):
            continue
        yield setup


def test_every_listed_file_exists():
    """적어 둔 파일이 없으면 빌드가 거기서 멈춘다."""
    missing = []
    for setup in _setup_files():
        for kind, value in _data_files(setup):
            if kind != 'file':
                continue
            if not (setup.parent / value).is_file():
                missing.append(f'{setup.parent.name}: {value}')

    assert missing == [], (
        '설치 목록에 적힌 파일이 없습니다 · 옮기거나 지웠으면 setup.py 도 고치세요:\n  '
        + '\n  '.join(missing)
    )


def test_no_directory_sneaks_into_a_glob():
    """`glob('deploy/*')` 같은 목록에 폴더가 걸리면 복사가 실패한다 ·
    파일만 들어가야 한다."""
    offenders = []
    for setup in _setup_files():
        previous = os.getcwd()
        os.chdir(setup.parent)
        try:
            for kind, pattern in _data_files(setup):
                if kind != 'glob':
                    continue
                for match in glob(pattern):
                    if not os.path.isfile(match):
                        offenders.append(f'{setup.parent.name}: {match}')
        finally:
            os.chdir(previous)

    assert offenders == [], (
        '설치 목록에 파일이 아닌 것이 걸립니다:\n  ' + '\n  '.join(offenders)
    )


def test_the_installer_builds_from_scratch():
    """`colcon` 은 **지워진 파일을 정리하지 않는다** · 꾸러미에서 파일이 빠지면
    `install/` 에 옛 흔적이 남아 다음 빌드가 거기서 깨진다 · 다른 PC 가 실제로
    그렇게 멈췄다 · 설치는 늘 지우고 처음부터 한다."""
    installer = (WORKSPACE / 'src/motion_web/install.sh').read_text(encoding='utf-8')

    assert 'rm -rf "${WORKSPACE_DIR}/build" "${WORKSPACE_DIR}/install"' in installer
    assert installer.index('rm -rf "${WORKSPACE_DIR}/build"') < installer.index(
        'colcon build'
    ), '지우기 전에 빌드한다'


def test_the_installer_tries_the_build_twice():
    """지운 뒤 첫 빌드는 한 번 더 필요할 수 있다 · 어떤 꾸러미는 다른 꾸러미가
    설치된 뒤에야 제 경로가 풀린다 · 단독으로는 되고 전체로는 깨진다 ·
    실제로 그랬고, 이어서 한 번 더 하니 31개가 전부 붙었다."""
    installer = (WORKSPACE / 'src/motion_web/install.sh').read_text(encoding='utf-8')

    assert installer.count('colcon build --symlink-install') == 2, (
        '빌드를 한 번만 한다 · 지운 뒤 첫 빌드가 깨지면 설치가 멈춘다'
    )
    assert '빌드를 이어서 한 번 더 합니다' in installer
