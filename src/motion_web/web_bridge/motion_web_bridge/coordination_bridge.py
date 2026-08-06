"""Local web/ROS adapter for the independent PC coordination service."""

import copy
from dataclasses import replace
import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Dict, Mapping

import yaml

from motion_coordination.group_configuration import (
    load_group_config,
    migrate_legacy_group_config,
    save_group_config,
)


class CoordinationWebBridge:
    """Expose global coordination state without mixing it into project data."""

    def __init__(
        self,
        node: Any,
        workspace: Path,
        project_generation: Callable[[], int],
    ) -> None:
        self._node = node
        self._workspace = Path(workspace).resolve()
        self._project_generation = project_generation
        self._config_path = Path(
            os.environ.get('MOTION_COORDINATION_CONFIG')
            or self._workspace / 'config/motion_coordination.yaml'
        ).expanduser()
        self._config, _migrated = migrate_legacy_group_config(self._config_path)
        self._status: Dict[str, Any] = {}
        self._status_received_at = 0.0
        self._local_port = int(
            os.environ.get('MOTION_COORDINATION_LOCAL_PORT') or 8011
        )

    def snapshot(self) -> Dict[str, Any]:
        """Return runtime state plus a non-secret global configuration summary."""
        try:
            runtime = self._local_api('/status')
            self._status = copy.deepcopy(runtime)
            self._status_received_at = time.time()
            runtime_error = ''
        except (OSError, ValueError) as exc:
            runtime = copy.deepcopy(self._status)
            runtime_error = str(exc)
        received_at = self._status_received_at
        try:
            config = load_group_config(self._config_path)
            config_error = ''
            configured = self._config_summary(config)
        except (ImportError, OSError, ValueError) as exc:
            configured = {}
            config_error = str(exc)
        age = max(time.time() - received_at, 0.0) if received_at else None
        return {
            'success': not bool(config_error or runtime_error),
            'node_connected': age is not None and age <= 3.0,
            'status_age_sec': round(age, 3) if age is not None else None,
            'config': configured,
            'config_error': config_error or runtime_error,
            'runtime': runtime,
        }

    def local_execution_blocker(self) -> str:
        """Return why a local motion action conflicts with upper ownership."""
        runtime = self.snapshot().get('runtime') or {}
        execution = runtime.get('execution') if isinstance(runtime, Mapping) else {}
        connected = self._status_received_at and time.time() - self._status_received_at <= 3.0
        active_states = {
            'preparing', 'initializing', 'armed', 'start_scheduled', 'waiting',
            'running', 'waiting_cycle_ready', 'cycle_ready', 'stop_after_cycle',
        }
        if (
            connected and isinstance(execution, Mapping)
            and execution.get('state') in active_states
        ):
            return 'DDS 그룹 실행이 로컬 모션 실행을 사용 중입니다'
        return ''

    def update_settings(self, payload: Mapping[str, Any]) -> Dict[str, Any]:
        """Save project-independent DDS group settings and restart the node."""
        if not isinstance(payload, Mapping):
            raise ValueError('연동 설정 요청은 객체여야 합니다')
        allowed = {'enabled', 'group_id', 'dds_domain_id', 'display_name'}
        if set(payload).difference(allowed):
            raise ValueError('허용되지 않은 연동 설정 항목이 있습니다')
        if 'enabled' in payload and not isinstance(payload['enabled'], bool):
            raise ValueError('enabled는 true 또는 false여야 합니다')
        current = load_group_config(self._config_path)
        config = replace(
            current,
            enabled=bool(payload.get('enabled', current.enabled)),
            group_id=str(payload.get('group_id', current.group_id)).strip(),
            dds_domain_id=int(payload.get('dds_domain_id', current.dds_domain_id)),
            display_name=(
                str(payload.get('display_name', current.display_name)).strip()
                or current.pc_id
            ),
        )
        # Validate the complete value through the canonical loader before use.
        save_group_config(self._config_path, config)
        config = load_group_config(self._config_path)
        restart = self._restart_coordination_service()
        if not restart['service_installed']:
            return {
                'success': False,
                'saved': True,
                'message': f'설정 저장 완료 · {restart["message"]}',
                'config': self._config_summary(config),
            }
        if not restart['restart_pending']:
            return {
                'success': False,
                'saved': True,
                'message': restart['message'],
                'config': self._config_summary(config),
            }
        return {
            'success': True,
            'saved': True,
            'restart_pending': True,
            'message': 'DDS 그룹 설정 저장 · PC 연동 서비스 재시작 요청 완료',
            'config': self._config_summary(config),
        }

    @staticmethod
    def _restart_coordination_service() -> Dict[str, Any]:
        service = str(
            os.environ.get('MOTION_COORDINATION_SERVICE_UNIT') or ''
        ).strip()
        unit_path = Path.home() / '.config/systemd/user/motion-coordination.service'
        if service != 'motion-coordination.service' or not unit_path.is_file():
            return {
                'service_installed': False,
                'restart_pending': False,
                'message': 'PC 연동 자동실행 서비스가 설치되지 않았습니다',
            }
        completed = subprocess.run(
            ['/usr/bin/systemctl', '--user', 'restart', '--no-block', service],
            check=False,
            capture_output=True,
            text=True,
            timeout=5.0,
        )
        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip()
            return {
                'service_installed': True,
                'restart_pending': False,
                'message': detail or 'PC 연동 서비스 재시작 요청 실패',
            }
        return {
            'service_installed': True,
            'restart_pending': True,
            'message': 'PC 연동 서비스 재시작 요청 완료',
        }

    def request_control(self, payload: Mapping[str, Any]) -> Dict[str, Any]:
        """Send one manual DDS group operation through the local ROS adapter."""
        if not isinstance(payload, Mapping):
            raise ValueError('연동 실행 요청은 객체여야 합니다')
        command = str(payload.get('command') or '').strip()
        allowed = {
            'join', 'leave', 'start_group', 'stop_after_cycle', 'stop_now',
            'acknowledge_group_error',
        }
        if command not in allowed:
            raise ValueError('지원하지 않는 DDS 그룹 실행 요청입니다')
        start_generation = int(self._project_generation())
        try:
            result = self._local_api('/control', {'command': command})
        except (OSError, ValueError) as exc:
            result = {'success': False, 'message': str(exc)}
        if int(self._project_generation()) != start_generation:
            return {
                'success': False, 'stale_project_generation': True,
                'message': '프로젝트 전환 전 시작한 그룹 실행 결과를 폐기했습니다',
            }
        return result

    def _local_api(
        self, path: str, payload: Mapping[str, Any] | None = None
    ) -> Dict[str, Any]:
        data = None
        method = 'GET'
        headers: Dict[str, str] = {}
        if payload is not None:
            data = json.dumps(dict(payload), separators=(',', ':')).encode('utf-8')
            method = 'POST'
            headers['Content-Type'] = 'application/json'
        request = urllib.request.Request(
            f'http://127.0.0.1:{self._local_port}{path}',
            data=data, headers=headers, method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=5.0) as response:
                value = json.loads(response.read(64 * 1024).decode('utf-8'))
        except (urllib.error.URLError, OSError, UnicodeError, ValueError) as exc:
            raise OSError(f'DDS 그룹 연동 노드 응답 없음: {exc}') from exc
        if not isinstance(value, dict):
            raise ValueError('DDS 그룹 연동 노드 응답 형식 오류')
        return value

    @staticmethod
    def _config_summary(config: Any) -> Dict[str, Any]:
        return {
            'pc_id': config.pc_id,
            'display_name': config.display_name,
            'enabled': config.enabled,
            'group_id': config.group_id,
            'dds_domain_id': config.dds_domain_id,
            'heartbeat_sec': config.heartbeat_sec,
            'warning_timeout_sec': config.warning_timeout_sec,
            'peer_timeout_sec': config.peer_timeout_sec,
            'start_lead_sec': config.start_lead_sec,
            'schedule_ack_margin_sec': config.schedule_ack_margin_sec,
            'max_trigger_sync_uncertainty_ms': (
                config.max_trigger_sync_uncertainty_ms
            ),
            'trigger_sync_samples': config.trigger_sync_samples,
            'prepare_timeout_sec': config.prepare_timeout_sec,
            'trigger_report_timeout_sec': config.trigger_report_timeout_sec,
        }


def local_motion_readiness(bridge: Any) -> Dict[str, Any]:
    """Run the existing local motion readiness check using local active files."""
    try:
        selection = _local_motion_selection(bridge)
    except ValueError as exc:
        return {'success': False, 'message': str(exc)}
    return bridge.motion_run_check({
        **selection,
        'initial_move_time_sec': None,
        'run_mode': 'once',
        'request_source': 'network_readiness',
    })


def local_motion_control(bridge: Any, payload: Mapping[str, Any]) -> Dict[str, Any]:
    """Execute one loopback-only high-level command through motion_run_manager."""
    command = str(payload.get('command') or '')
    if command in {'stop_motion', 'stop_initialize', 'stop_now'}:
        return bridge.motion_run_stop()
    if command in {'cancel_before_start', 'group_cancel'}:
        return bridge.motion_group_cancel({
            'execution_id': str(payload.get('execution_id') or ''),
        })
    if command == 'stop_after_cycle':
        return bridge.motion_run_stop_after_cycle()
    try:
        selection = _local_motion_selection(bridge)
    except ValueError as exc:
        return {'success': False, 'message': str(exc)}
    request = {
        **selection,
        'initial_move_time_sec': None,
        'run_mode': 'once',
        'request_source': 'network_control',
        'network_operation_id': str(payload.get('network_operation_id') or ''),
    }
    if command == 'group_prepare':
        run_status = bridge.motion_run_status()
        automation = (
            (run_status.get('status') or {}).get('automation')
            if isinstance(run_status, Mapping) else {}
        )
        automation = automation if isinstance(automation, Mapping) else {}
        request.update({
            'execution_id': str(payload.get('execution_id') or ''),
            'initialize_monotonic': payload.get('initialize_monotonic'),
            'group_execution': True,
            'repeat_mode': str(automation.get('repeat_mode') or 'direct'),
            'dwell_sec': float(automation.get('dwell_sec') or 0.0),
        })
        return bridge.motion_group_prepare(request)
    if command == 'group_start_at':
        request.update({
            'execution_id': str(payload.get('execution_id') or ''),
            'cycle_number': payload.get('cycle_number'),
            'start_monotonic': payload.get('start_monotonic'),
            'group_execution': True,
        })
        return bridge.motion_group_start_at(request)
    return {'success': False, 'message': '지원하지 않는 로컬 연동 실행 명령입니다'}


def _local_motion_selection(bridge: Any) -> Dict[str, str]:
    project_id = bridge.project_repository.selected_project_id()
    if not project_id:
        raise ValueError('로컬 프로젝트를 먼저 선택하세요')
    try:
        project = bridge.project_repository.get_project(project_id).get('project') or {}
        active = project.get('active_files') or {}
        mapping_id = str(active.get('motion_axis_matching') or '').strip()
        if not mapping_id:
            raise ValueError('로컬 모션축 설정을 선택하세요')
        mapping_path = bridge.project_repository.export_path(
            project_id, 'motion_axis_matching', mapping_id
        )
        mapping = yaml.safe_load(mapping_path.read_text(encoding='utf-8')) or {}
        motion_id = str(
            mapping.get('motion_file_id') if isinstance(mapping, dict) else ''
        ).strip()
        if not motion_id:
            raise ValueError('로컬 모션 파일을 재생 등록하세요')
        bridge.project_repository.export_path(project_id, 'motions', motion_id)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        raise ValueError(f'로컬 실행 파일 확인 실패: {exc}') from exc
    return {'motion_file_id': motion_id, 'mapping_file_id': mapping_id}
