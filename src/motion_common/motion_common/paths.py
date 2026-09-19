"""워크스페이스 경로 단일 해석 지점.

절대경로 하드코딩 금지. 모든 패키지는 이 모듈을 경유해 workspace/project 경로를
얻는다. 해석 순서는 `MOTION_WORKSPACE` 환경변수 → 설치 트리 역추적 → 소스 트리
역추적 → 현재 작업 디렉터리이며, 다중 PC 배포에서도 동일하게 동작한다.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

WORKSPACE_ENV = 'MOTION_WORKSPACE'

#: 고른 프로젝트가 없을 때 하는 말 · §6-169
#:
#: 같은 두 줄이 여덟 곳에 붙어 있었고, 같은 상황인데 화면마다 말이 달랐다 ·
#: 한 곳은 「왼쪽에서」 가 붙고 다른 곳은 안 붙었다 · 묻는 것이 하나면 답도
#: 하나여야 한다.
#:
#: 여기 두는 이유 · 「어느 프로젝트인가」의 주인은 웹 브리지의 저장소지만,
#: 그것을 못 가져오는 **순수 모듈**(`motion_file_analysis`)도 같은 말을 한다 ·
#: 양쪽이 볼 수 있는 곳은 여기뿐이다.
NO_PROJECT_SELECTED = '통합 프로젝트를 먼저 선택하세요'

PROJECTS_DIRNAME = 'motion_projects'
CONFIG_DIRNAME = 'config'
SCRIPTS_DIRNAME = 'scripts'
LOG_DIRNAME = 'log'


def _from_environment() -> Optional[Path]:
    configured = str(os.environ.get(WORKSPACE_ENV) or '').strip()
    if not configured:
        return None
    return Path(configured).expanduser().resolve()


def _package_share_dir(package_hint: Optional[str]) -> Optional[Path]:
    if not package_hint:
        return None
    try:
        from ament_index_python.packages import (
            PackageNotFoundError,
            get_package_share_directory,
        )
    except ImportError:
        return None
    try:
        return Path(get_package_share_directory(package_hint)).resolve()
    except (PackageNotFoundError, ValueError, OSError):
        # 설치되지 않은 패키지 이름을 힌트로 받아도 탐색은 계속되어야 한다
        return None


def _walk_up(candidate: Path) -> Optional[Path]:
    for parent in (candidate, *candidate.parents):
        if parent.name == 'install':
            return parent.parent
        if (parent / 'src').is_dir() and (parent / SCRIPTS_DIRNAME).is_dir():
            return parent
    return None


def workspace_root(package_hint: Optional[str] = None) -> Path:
    """워크스페이스 루트를 반환한다.

    `package_hint`를 주면 해당 패키지의 share 디렉터리에서 먼저 역추적한다.
    호출자가 설치 트리에서 실행 중일 때 탐색이 더 정확해진다.
    """
    configured = _from_environment()
    if configured is not None:
        return configured

    candidates = []
    share_dir = _package_share_dir(package_hint)
    if share_dir is not None:
        candidates.append(share_dir)
    candidates.append(Path.cwd().resolve())
    candidates.append(Path(__file__).resolve())

    for candidate in candidates:
        found = _walk_up(candidate)
        if found is not None:
            return found
    return Path.cwd().resolve()


def motion_projects_dir(package_hint: Optional[str] = None) -> Path:
    """모션 프로젝트 루트 디렉터리."""
    return workspace_root(package_hint) / PROJECTS_DIRNAME


def config_dir(package_hint: Optional[str] = None) -> Path:
    """워크스페이스 설정 디렉터리."""
    return workspace_root(package_hint) / CONFIG_DIRNAME


def config_file(name: str, package_hint: Optional[str] = None) -> Path:
    """워크스페이스 설정 파일 경로."""
    return config_dir(package_hint) / name


def scripts_dir(package_hint: Optional[str] = None) -> Path:
    """워크스페이스 스크립트 디렉터리."""
    return workspace_root(package_hint) / SCRIPTS_DIRNAME


def log_dir(package_hint: Optional[str] = None) -> Path:
    """워크스페이스 로그 디렉터리."""
    return workspace_root(package_hint) / LOG_DIRNAME


#: 프로젝트 ID 가 이름 하나가 아닐 때 · `../` 같은 것이 들어온 경우다
INVALID_PROJECT_ID = '유효한 통합 프로젝트 ID가 필요합니다'


def project_dir_for(projects_dir: Path, project_id: str) -> Path:
    """그 프로젝트 폴더를 돌려준다 · 우리 폴더 밖이면 거부한다 · §6-194

    **같은 검사가 네 벌 복사돼 있었고, 둘이 틀렸다.**

        workspace_session      (스튜디오)      ❌
        motion_mapping_manager (모션축 매핑)    ❌
        motion_run_manager     (모션 실행)      ✅
        midi_control_node      (MIDI)          ✅

    틀린 둘은 **푼 경로와 안 푼 경로를 견주었다.**

        project_dir = (projects_dir / project_id).resolve()   ← 풀었다
        if project_dir.parent != projects_dir:                ← 안 풀었다

    작업공간 경로에 심링크가 하나라도 끼면 이 둘은 **모든 프로젝트를 거부**
    한다 · 모션 실행과 MIDI 는 멀쩡히 도는데 스튜디오와 매핑만 「통합
    프로젝트를 찾을 수 없습니다」를 낸다 · 반쪽만 도는 상태이고, 문구가
    「없다」라서 진짜 원인을 짐작하기 어렵다.

    **두 단계다.**

        1  이름이 이름인가      `../`·`/`·숨김파일을 여기서 막는다
        2  정말 그 안에 있나    푼 경로끼리 견준다 · 심링크를 넘어서 본다

    1번만으로는 부족하다 · `projects_dir` 자체가 심링크를 타고 엉뚱한 곳을
    가리킬 수 있다 · 2번이 마지막 빗장이다.
    """
    name = str(project_id or '').strip()
    if (
        not name
        or name != Path(name).name
        or name.startswith('.')
        or '/' in name
        or '\\' in name
    ):
        raise ValueError(INVALID_PROJECT_ID)
    root = Path(projects_dir).resolve()
    project_dir = (root / name).resolve()
    if project_dir.parent != root or not (project_dir / 'project.json').is_file():
        raise ValueError(f'통합 프로젝트를 찾을 수 없습니다: {name}')
    return project_dir
