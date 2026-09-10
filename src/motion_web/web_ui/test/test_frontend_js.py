"""프런트엔드 `.mjs` 테스트를 파이썬 실행 경로에 올린다 · §6-59.

`test/` 에 `node --test` 로 도는 테스트가 38개 · 257건 있는데 `colcon` 에도
`pytest` 에도 걸려 있지 않았다. 아무도 돌리지 않으니 UI 를 고치고도 깨진 줄
몰랐다 · 실제로 §6-57 커밋이 7건을 깨뜨린 채 올라갔다.

여기서 감싸면 `pytest src/` 한 번에 딸려 온다. 테스트를 옮기지 않는다 ·
`.mjs` 는 그대로 두고 실행 경로만 잇는다.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

WEB_UI = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(shutil.which('node') is None, reason='node 가 없다')
def test_frontend_mjs_suite_passes():
    result = subprocess.run(
        ['node', '--test', 'test/'],
        cwd=WEB_UI,
        capture_output=True,
        text=True,
        timeout=600,
    )
    if result.returncode != 0:
        failures = [
            line for line in result.stdout.splitlines()
            if line.startswith('not ok')
        ]
        pytest.fail(
            'node --test 실패 {}건\n{}\n\n재현 · cd {} && node --test test/'.format(
                len(failures), '\n'.join(failures), WEB_UI,
            ),
            pytrace=False,
        )
