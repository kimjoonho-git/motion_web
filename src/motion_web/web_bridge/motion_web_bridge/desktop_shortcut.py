"""바탕화면 바로가기 설치 · 노드 비의존.

`MotionWebBridge.create_desktop_shortcut`에서 떼어냈다. 노드 상태에 닿는 것은
`workspace_root` 하나뿐이었고 그마저 재대입되지 않는 불변 경로였으므로 인자로
받는다 · `docs/ARCHITECTURE_REVIEW.md` §6-11
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from ament_index_python.packages import get_package_share_directory

#: 바탕화면에 놓이는 파일 이름 · 화면 표기와 같다
SHORTCUT_FILENAME = '모션 프로그램 열기.desktop'


def create_desktop_shortcut(workspace_root: Optional[Path] = None) -> Dict[str, Any]:
    """Install the packaged web launcher on this service user's desktop."""
    home = Path(str(os.environ.get('HOME') or Path.home())).expanduser().resolve()
    try:
        desktop_result = subprocess.run(
            ['xdg-user-dir', 'DESKTOP'],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {
            'success': False,
            'message': f'바탕화면 경로를 확인할 수 없습니다: {exc}',
        }
    desktop_text = desktop_result.stdout.strip()
    if desktop_result.returncode != 0 or not desktop_text:
        return {
            'success': False,
            'message': '바탕화면 경로를 확인할 수 없습니다',
        }
    desktop = Path(desktop_text).expanduser().resolve()
    if desktop == home or not desktop.is_dir():
        return {
            'success': False,
            'message': '현재 사용자에게 사용할 수 있는 바탕화면 폴더가 없습니다',
        }

    source_candidates: List[Path] = []
    try:
        source_candidates.append(
            Path(get_package_share_directory('motion_web_bridge'))
            / 'deploy'
            / 'motion-program.desktop'
        )
    except Exception:
        pass
    source_candidates.append(
        Path(workspace_root if workspace_root is not None else Path.cwd())
        / 'src'
        / 'motion_web'
        / 'web_bridge'
        / 'deploy'
        / 'motion-program.desktop'
    )
    source = next((path for path in source_candidates if path.is_file()), None)
    if source is None:
        return {
            'success': False,
            'message': '설치된 바탕화면 바로가기 원본을 찾을 수 없습니다',
        }

    destination = desktop / SHORTCUT_FILENAME
    try:
        launcher_data = source.read_bytes()
        if (
            b'[Desktop Entry]' not in launcher_data
            or b'Exec=xdg-open http://localhost:8000' not in launcher_data
        ):
            raise ValueError('바탕화면 바로가기 원본 형식이 올바르지 않습니다')
        already_installed = (
            destination.is_file()
            and destination.read_bytes() == launcher_data
            and bool(destination.stat().st_mode & 0o111)
        )
        if not already_installed:
            temporary_path: Optional[Path] = None
            try:
                with tempfile.NamedTemporaryFile(
                    dir=desktop,
                    prefix='.motion-program-',
                    suffix='.desktop',
                    delete=False,
                ) as temporary:
                    temporary.write(launcher_data)
                    temporary_path = Path(temporary.name)
                temporary_path.chmod(0o755)
                os.replace(temporary_path, destination)
                temporary_path = None
            finally:
                if temporary_path is not None:
                    temporary_path.unlink(missing_ok=True)
        else:
            destination.chmod(destination.stat().st_mode | 0o111)
    except (OSError, ValueError) as exc:
        return {
            'success': False,
            'message': f'바탕화면 바로가기를 만들 수 없습니다: {exc}',
        }

    trusted = False
    try:
        trust_result = subprocess.run(
            ['gio', 'set', str(destination), 'metadata::trusted', 'true'],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
        trusted = trust_result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        trusted = False
    if trusted:
        message = (
            '바탕화면 바로가기가 이미 설치되어 있습니다'
            if already_installed
            else '바탕화면 바로가기를 만들었습니다'
        )
    else:
        message = (
            '바탕화면 바로가기를 만들었습니다. '
            '아이콘을 우클릭해 실행 허용을 선택하세요'
        )
    return {
        'success': True,
        'status': 'already_installed' if already_installed else 'created',
        'message': message,
        'path': str(destination),
        'trusted': trusted,
    }
