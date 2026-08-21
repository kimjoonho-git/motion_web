"""그룹 연동 설정 조회 · 마스터 역할 판정 단일 지점.

스케줄 노드와 웹 브리지가 각자 마스터 여부를 판정하면 두 판정이 갈라진다.
두 경로 모두 이 모듈을 경유해 정본 설정 파일(`config/motion_coordination.yaml`)의
같은 키를 읽는다.

판정 규칙 · 설정 파일이 없으면 연동 미구성으로 보고 단독 동작(마스터)으로 처리한다.
파일이 있는데 읽지 못하면 마스터로 보지 않는다 — 다중 PC에서 전원이 마스터가 되어
같은 모션을 중복 발화하는 쪽이 스케줄이 멈추는 쪽보다 위험하다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, NamedTuple, Optional

from .paths import config_dir

SETTINGS_FILENAME = 'motion_coordination.yaml'


class MasterRole(NamedTuple):
    """마스터 판정 결과와 근거."""

    is_master: bool
    reason: str


def coordination_settings_path(package_hint: Optional[str] = None) -> Path:
    """그룹 연동 정본 설정 파일 경로."""
    return config_dir(package_hint) / SETTINGS_FILENAME


def load_coordination_settings(
    path: Optional[Path] = None,
    package_hint: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """설정을 dict로 읽는다. 파일이 없거나 읽지 못하면 None."""
    import yaml

    target = Path(path) if path is not None else coordination_settings_path(package_hint)
    if not target.is_file():
        return None
    try:
        loaded = yaml.safe_load(target.read_text(encoding='utf-8'))
    except (OSError, yaml.YAMLError):
        return None
    return loaded if isinstance(loaded, dict) else None


def resolve_master_role(
    path: Optional[Path] = None,
    package_hint: Optional[str] = None,
) -> MasterRole:
    """이 PC가 그룹 마스터인지 판정한다.

    반환값의 ``reason``은 호출자가 로그로 남기기 위한 근거 문자열이다.
    """
    target = Path(path) if path is not None else coordination_settings_path(package_hint)

    if not target.is_file():
        return MasterRole(True, f'연동 설정 없음 · 단독 동작으로 간주 · {target}')

    settings = load_coordination_settings(target)
    if settings is None:
        return MasterRole(
            False,
            f'연동 설정을 읽지 못함 · 중복 발화 방지를 위해 마스터 아님으로 처리 · {target}',
        )

    if not bool(settings.get('enabled', False)):
        return MasterRole(True, '연동 비활성 · 단독 동작으로 간주')

    # 정본 로더(motion_coordination.group_configuration)와 같은 기본값을 쓴다
    is_master = bool(settings.get('is_master', False))
    pc_id = str(settings.get('pc_id') or '').strip() or '(pc_id 없음)'
    return MasterRole(is_master, f'연동 활성 · pc_id={pc_id} · is_master={is_master}')


def is_master_pc(
    path: Optional[Path] = None,
    package_hint: Optional[str] = None,
) -> bool:
    """마스터 여부만 필요할 때 쓰는 축약형."""
    return resolve_master_role(path, package_hint).is_master
