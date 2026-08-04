"""Project-independent configuration for network PC coordination."""

import ipaddress
import os
import re
import socket
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Tuple
from urllib.parse import urlparse

import yaml

from .access_policy import AccessPolicy
from .status_adapter import COORDINATION_MODES, COORDINATION_ROLES


_MACHINE_ID_PATTERN = re.compile(r'^[A-Za-z0-9_.:-]{1,128}$')


class ConfigurationError(ValueError):
    """Raised when coordination settings are unsafe or inconsistent."""


@dataclass(frozen=True)
class PeerConfig:
    """One explicitly registered peer endpoint."""

    machine_id: str
    url: str


@dataclass(frozen=True)
class CoordinationConfig:
    """Validated global settings, never sourced from a project."""

    machine_id: str
    display_name: str
    mode: str
    role: str
    coordinator_machine_id: str
    access: AccessPolicy
    peers: Tuple[PeerConfig, ...]
    credential_file: Path
    heartbeat_sec: float = 1.0
    peer_timeout_sec: float = 3.0

    @classmethod
    def disabled(cls, workspace: Path) -> 'CoordinationConfig':
        """Return a safe startup state when no per-PC config exists."""
        machine_id = _machine_id(socket.gethostname())
        return cls(
            machine_id=machine_id,
            display_name=machine_id,
            mode='off',
            role='peer',
            coordinator_machine_id='',
            access=AccessPolicy.from_mapping({}),
            peers=(),
            credential_file=workspace / 'config/motion_coordination.credentials.yaml',
        )


def load_config(path: Path, *, workspace: Path) -> CoordinationConfig:
    """Load one global YAML file or return the disabled default."""
    path = Path(path).expanduser()
    if not path.is_file():
        return CoordinationConfig.disabled(workspace)
    try:
        value = yaml.safe_load(path.read_text(encoding='utf-8')) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigurationError(f'연동 설정을 읽을 수 없습니다: {exc}') from exc
    if not isinstance(value, Mapping) or value.get('version') != 1:
        raise ConfigurationError('연동 설정 version은 1이어야 합니다')
    machine_id = _machine_id(value.get('machine_id'))
    display_name = str(value.get('display_name') or machine_id).strip()[:128]
    mode = str(value.get('mode') or 'off').strip().lower()
    role = str(value.get('role') or 'peer').strip().lower()
    if mode not in COORDINATION_MODES:
        raise ConfigurationError('mode는 off, status, participant 중 하나여야 합니다')
    if role not in COORDINATION_ROLES:
        raise ConfigurationError('role은 peer 또는 coordinator여야 합니다')
    access = AccessPolicy.from_mapping(value.get('access') or {})
    if mode != 'off' and not access.coordination_enabled:
        raise ConfigurationError('연동 사용 모드에서는 8010 수신을 활성화해야 합니다')
    peers = _peers(value.get('peers') or [])
    if machine_id in {peer.machine_id for peer in peers}:
        raise ConfigurationError('자기 machine_id를 peers 목록에 등록할 수 없습니다')
    coordinator_id = str(value.get('coordinator_machine_id') or '').strip()
    if role == 'coordinator':
        if mode == 'off' or not access.coordination_enabled:
            raise ConfigurationError('중앙 PC는 연동과 8010 수신을 활성화해야 합니다')
        if coordinator_id and coordinator_id != machine_id:
            raise ConfigurationError('중앙 PC의 coordinator_machine_id는 자기 ID여야 합니다')
        coordinator_id = machine_id
    elif mode != 'off':
        coordinator_id = _machine_id(coordinator_id)
        if coordinator_id == machine_id:
            raise ConfigurationError('peer의 중앙 PC ID는 자기 ID와 달라야 합니다')
        if coordinator_id not in {peer.machine_id for peer in peers}:
            raise ConfigurationError('중앙 PC가 peers 목록에 등록되지 않았습니다')
    credential = Path(str(
        value.get('credential_file')
        or 'config/motion_coordination.credentials.yaml'
    )).expanduser()
    if not credential.is_absolute():
        credential = workspace / credential
    heartbeat = _positive(value.get('heartbeat_sec'), 1.0, 'heartbeat_sec')
    timeout = _positive(value.get('peer_timeout_sec'), 3.0, 'peer_timeout_sec')
    if timeout <= heartbeat:
        raise ConfigurationError('peer_timeout_sec는 heartbeat_sec보다 커야 합니다')
    return CoordinationConfig(
        machine_id=machine_id,
        display_name=display_name,
        mode=mode,
        role=role,
        coordinator_machine_id=coordinator_id,
        access=access,
        peers=peers,
        credential_file=credential,
        heartbeat_sec=heartbeat,
        peer_timeout_sec=timeout,
    )


def update_local_selection(
    path: Path,
    *,
    workspace: Path,
    mode: str,
    role: str,
    coordinator_machine_id: str = '',
) -> CoordinationConfig:
    """Atomically update only this PC's global mode and manual role."""
    path = Path(path).expanduser()
    if path.is_file():
        try:
            value = yaml.safe_load(path.read_text(encoding='utf-8')) or {}
        except (OSError, yaml.YAMLError) as exc:
            raise ConfigurationError(f'연동 설정을 읽을 수 없습니다: {exc}') from exc
    else:
        disabled = CoordinationConfig.disabled(workspace)
        value = {
            'version': 1,
            'machine_id': disabled.machine_id,
            'display_name': disabled.display_name,
            'access': {},
            'peers': [],
            'credential_file': 'config/motion_coordination.credentials.yaml',
        }
    if not isinstance(value, dict):
        raise ConfigurationError('연동 설정은 객체여야 합니다')
    clean_mode = str(mode or '').strip().lower()
    clean_role = str(role or '').strip().lower()
    if clean_mode not in COORDINATION_MODES:
        raise ConfigurationError('mode는 off, status, participant 중 하나여야 합니다')
    if clean_role not in COORDINATION_ROLES:
        raise ConfigurationError('role은 peer 또는 coordinator여야 합니다')
    if clean_mode == 'off':
        clean_role = 'peer'
        coordinator_machine_id = ''
    value['mode'] = clean_mode
    value['role'] = clean_role
    value['coordinator_machine_id'] = str(coordinator_machine_id or '').strip()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode='w',
            encoding='utf-8',
            dir=path.parent,
            prefix=f'.{path.name}.',
            suffix='.tmp',
            delete=False,
        ) as temporary:
            yaml.safe_dump(value, temporary, allow_unicode=True, sort_keys=False)
            temporary_path = Path(temporary.name)
        validated = load_config(temporary_path, workspace=workspace)
        os.replace(temporary_path, path)
        temporary_path = None
        path.chmod(0o600)
        return validated
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _machine_id(value: Any) -> str:
    clean = str(value or '').strip()
    if not _MACHINE_ID_PATTERN.fullmatch(clean):
        raise ConfigurationError('machine_id 형식이 올바르지 않습니다')
    return clean


def _peers(value: Any) -> Tuple[PeerConfig, ...]:
    if not isinstance(value, list):
        raise ConfigurationError('peers는 목록이어야 합니다')
    if len(value) > 31:
        raise ConfigurationError('로컬 PC를 제외한 peer는 최대 31대입니다')
    result = []
    seen = set()
    seen_urls = set()
    for row in value:
        if not isinstance(row, Mapping):
            raise ConfigurationError('peer 설정은 객체여야 합니다')
        machine_id = _machine_id(row.get('machine_id'))
        if machine_id in seen:
            raise ConfigurationError(f'중복 peer ID입니다: {machine_id}')
        raw_url = str(row.get('url') or '').strip().rstrip('/')
        parsed = urlparse(raw_url)
        if parsed.scheme not in {'http', 'https'} or not parsed.hostname:
            raise ConfigurationError(f'{machine_id} peer URL이 올바르지 않습니다')
        try:
            peer_address = ipaddress.ip_address(parsed.hostname)
        except ValueError as exc:
            raise ConfigurationError(
                f'{machine_id} peer URL은 명시적인 내부망 IP를 사용해야 합니다'
            ) from exc
        if not (peer_address.is_private or peer_address.is_link_local):
            raise ConfigurationError(
                f'{machine_id} peer URL은 내부망 IP를 사용해야 합니다'
            )
        if parsed.path not in {'', '/'} or parsed.query or parsed.fragment:
            raise ConfigurationError(f'{machine_id} peer URL에는 경로를 넣을 수 없습니다')
        if parsed.port != 8010:
            raise ConfigurationError(f'{machine_id} peer URL은 8010 포트를 사용해야 합니다')
        if raw_url in seen_urls:
            raise ConfigurationError(f'중복 peer URL입니다: {raw_url}')
        seen.add(machine_id)
        seen_urls.add(raw_url)
        result.append(PeerConfig(machine_id, raw_url))
    return tuple(result)


def _positive(value: Any, default: float, field: str) -> float:
    try:
        number = float(default if value in (None, '') else value)
    except (TypeError, ValueError) as exc:
        raise ConfigurationError(f'{field}는 양수여야 합니다') from exc
    if number <= 0:
        raise ConfigurationError(f'{field}는 양수여야 합니다')
    return number
