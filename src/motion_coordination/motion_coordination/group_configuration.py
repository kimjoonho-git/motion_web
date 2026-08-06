"""Global, project-independent DDS group settings."""

from __future__ import annotations

import os
import re
import socket
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import yaml


_IDENTIFIER = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$')


@dataclass(frozen=True)
class GroupConfig:
    pc_id: str
    display_name: str
    enabled: bool
    group_id: str
    dds_domain_id: int
    heartbeat_sec: float = 0.5
    warning_timeout_sec: float = 1.5
    peer_timeout_sec: float = 3.0
    start_lead_sec: float = 0.5
    schedule_ack_margin_sec: float = 0.1
    max_trigger_sync_uncertainty_ms: float = 5.0
    trigger_sync_samples: int = 5
    prepare_timeout_sec: float = 6.0
    trigger_report_timeout_sec: float = 1.0

    @property
    def configured(self) -> bool:
        return bool(self.enabled and self.group_id)


def load_group_config(path: Path) -> GroupConfig:
    path = Path(path).expanduser()
    value: Mapping[str, Any] = {}
    if path.is_file():
        try:
            loaded = yaml.safe_load(path.read_text(encoding='utf-8')) or {}
        except (OSError, yaml.YAMLError) as exc:
            raise ValueError(f'그룹 연동 설정을 읽을 수 없습니다: {exc}') from exc
        if not isinstance(loaded, Mapping):
            raise ValueError('그룹 연동 설정은 객체여야 합니다')
        value = loaded
    version = int(value.get('version') or 1)
    if version not in (1, 2):
        raise ValueError('그룹 연동 설정 version은 1 또는 2여야 합니다')
    pc_id = _identifier(
        value.get('pc_id') or value.get('machine_id') or socket.gethostname(),
        'pc_id',
    )
    display_name = str(value.get('display_name') or pc_id).strip()[:128]
    group_id = str(value.get('group_id') or '').strip()
    if group_id:
        _identifier(group_id, 'group_id')
    enabled = bool(value.get('enabled', False)) if version == 2 else False
    domain = _integer(value.get('dds_domain_id'), 21, 'dds_domain_id')
    if not 0 <= domain <= 101:
        raise ValueError('dds_domain_id는 0~101이어야 합니다')
    heartbeat = _positive(value.get('heartbeat_sec'), 0.5, 'heartbeat_sec')
    warning = _positive(value.get('warning_timeout_sec'), 1.5, 'warning_timeout_sec')
    timeout = _positive(value.get('peer_timeout_sec'), 3.0, 'peer_timeout_sec')
    if not heartbeat < warning < timeout:
        raise ValueError('heartbeat_sec < warning_timeout_sec < peer_timeout_sec 순서여야 합니다')
    lead = _positive(value.get('start_lead_sec'), 0.5, 'start_lead_sec')
    ack = _positive(value.get('schedule_ack_margin_sec'), 0.1, 'schedule_ack_margin_sec')
    if lead <= ack:
        raise ValueError('start_lead_sec는 schedule_ack_margin_sec보다 커야 합니다')
    max_uncertainty = _positive(
        value.get(
            'max_trigger_sync_uncertainty_ms', value.get('max_clock_offset_ms')
        ),
        5.0,
        'max_trigger_sync_uncertainty_ms',
    )
    sync_samples = _integer(value.get('trigger_sync_samples'), 5, 'trigger_sync_samples')
    if not 3 <= sync_samples <= 20:
        raise ValueError('trigger_sync_samples는 3~20이어야 합니다')
    prepare_timeout = _positive(
        value.get('prepare_timeout_sec'), 6.0, 'prepare_timeout_sec'
    )
    trigger_report_timeout = _positive(
        value.get('trigger_report_timeout_sec'), 1.0,
        'trigger_report_timeout_sec',
    )
    config = GroupConfig(
        pc_id=pc_id,
        display_name=display_name,
        enabled=enabled,
        group_id=group_id,
        dds_domain_id=domain,
        heartbeat_sec=heartbeat,
        warning_timeout_sec=warning,
        peer_timeout_sec=timeout,
        start_lead_sec=lead,
        schedule_ack_margin_sec=ack,
        max_trigger_sync_uncertainty_ms=max_uncertainty,
        trigger_sync_samples=sync_samples,
        prepare_timeout_sec=prepare_timeout,
        trigger_report_timeout_sec=trigger_report_timeout,
    )
    validate_group_config(config)
    return config


def save_group_config(path: Path, config: GroupConfig) -> None:
    validate_group_config(config)
    path = Path(path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        'version': 2,
        'pc_id': config.pc_id,
        'display_name': config.display_name,
        'enabled': bool(config.enabled),
        'group_id': config.group_id,
        'dds_domain_id': int(config.dds_domain_id),
        'heartbeat_sec': float(config.heartbeat_sec),
        'warning_timeout_sec': float(config.warning_timeout_sec),
        'peer_timeout_sec': float(config.peer_timeout_sec),
        'start_lead_sec': float(config.start_lead_sec),
        'schedule_ack_margin_sec': float(config.schedule_ack_margin_sec),
        'max_trigger_sync_uncertainty_ms': float(
            config.max_trigger_sync_uncertainty_ms
        ),
        'trigger_sync_samples': int(config.trigger_sync_samples),
        'prepare_timeout_sec': float(config.prepare_timeout_sec),
        'trigger_report_timeout_sec': float(
            config.trigger_report_timeout_sec
        ),
    }
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode='w', encoding='utf-8', dir=path.parent,
            prefix=f'.{path.name}.', suffix='.tmp', delete=False,
        ) as temporary:
            yaml.safe_dump(payload, temporary, allow_unicode=True, sort_keys=False)
            temporary_path = Path(temporary.name)
        os.replace(temporary_path, path)
        temporary_path = None
        path.chmod(0o600)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def migrate_legacy_group_config(path: Path) -> tuple[GroupConfig, bool]:
    """Replace an existing v1 file with a disabled v2 DDS configuration."""
    path = Path(path).expanduser()
    config = load_group_config(path)
    if not path.is_file():
        return config, False
    try:
        loaded = yaml.safe_load(path.read_text(encoding='utf-8')) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f'그룹 연동 설정을 읽을 수 없습니다: {exc}') from exc
    if not isinstance(loaded, Mapping):
        raise ValueError('그룹 연동 설정은 객체여야 합니다')
    version = int(loaded.get('version') or 1)
    if version == 2 and 'max_clock_offset_ms' in loaded:
        migrated = GroupConfig(
            pc_id=config.pc_id,
            display_name=config.display_name,
            enabled=config.enabled,
            group_id=config.group_id,
            dds_domain_id=config.dds_domain_id,
            heartbeat_sec=config.heartbeat_sec,
            warning_timeout_sec=config.warning_timeout_sec,
            peer_timeout_sec=config.peer_timeout_sec,
            start_lead_sec=0.5,
            schedule_ack_margin_sec=config.schedule_ack_margin_sec,
            max_trigger_sync_uncertainty_ms=config.max_trigger_sync_uncertainty_ms,
            trigger_sync_samples=config.trigger_sync_samples,
            prepare_timeout_sec=config.prepare_timeout_sec,
            trigger_report_timeout_sec=config.trigger_report_timeout_sec,
        )
        save_group_config(path, migrated)
        return migrated, True
    if version != 1:
        return config, False
    migrated = GroupConfig(
        pc_id=config.pc_id,
        display_name=config.display_name,
        enabled=False,
        group_id='',
        dds_domain_id=21,
    )
    save_group_config(path, migrated)
    return migrated, True


def validate_group_config(config: GroupConfig) -> None:
    """Validate a complete value before the global file is replaced."""
    _identifier(config.pc_id, 'pc_id')
    if not str(config.display_name).strip() or len(str(config.display_name)) > 128:
        raise ValueError('display_name은 1~128자여야 합니다')
    if config.group_id:
        _identifier(config.group_id, 'group_id')
    if config.enabled and not config.group_id:
        raise ValueError('연동 사용 시 그룹 ID가 필요합니다')
    if not 0 <= int(config.dds_domain_id) <= 101:
        raise ValueError('dds_domain_id는 0~101이어야 합니다')
    heartbeat = _positive(config.heartbeat_sec, 0.5, 'heartbeat_sec')
    warning = _positive(config.warning_timeout_sec, 1.5, 'warning_timeout_sec')
    timeout = _positive(config.peer_timeout_sec, 3.0, 'peer_timeout_sec')
    if not heartbeat < warning < timeout:
        raise ValueError('heartbeat_sec < warning_timeout_sec < peer_timeout_sec 순서여야 합니다')
    lead = _positive(config.start_lead_sec, 0.5, 'start_lead_sec')
    ack = _positive(config.schedule_ack_margin_sec, 0.1, 'schedule_ack_margin_sec')
    if lead <= ack:
        raise ValueError('start_lead_sec는 schedule_ack_margin_sec보다 커야 합니다')
    _positive(
        config.max_trigger_sync_uncertainty_ms,
        5.0,
        'max_trigger_sync_uncertainty_ms',
    )
    if not 3 <= int(config.trigger_sync_samples) <= 20:
        raise ValueError('trigger_sync_samples는 3~20이어야 합니다')
    _positive(config.prepare_timeout_sec, 6.0, 'prepare_timeout_sec')
    _positive(
        config.trigger_report_timeout_sec, 1.0,
        'trigger_report_timeout_sec',
    )


def _identifier(value: Any, field: str) -> str:
    clean = str(value or '').strip()
    if not _IDENTIFIER.fullmatch(clean):
        raise ValueError(f'{field} 형식이 올바르지 않습니다')
    return clean


def _positive(value: Any, default: float, field: str) -> float:
    try:
        number = float(default if value is None else value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f'{field} 값이 올바르지 않습니다') from exc
    if number <= 0:
        raise ValueError(f'{field}는 양수여야 합니다')
    return number


def _integer(value: Any, default: int, field: str) -> int:
    try:
        return int(default if value is None else value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f'{field} 값이 올바르지 않습니다') from exc
