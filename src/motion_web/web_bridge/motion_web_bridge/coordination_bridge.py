"""Local web/ROS adapter for the independent PC coordination service."""

import copy
import json
import os
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, Mapping

import yaml
from std_msgs.msg import String


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
        self._status_lock = threading.Lock()
        self._status: Dict[str, Any] = {}
        self._status_received_at = 0.0
        self._result_lock = threading.Lock()
        self._results: Dict[str, Dict[str, Any]] = {}
        self._publisher = node.create_publisher(
            String, '/motion_coordination/request', 10
        )
        self._status_subscription = node.create_subscription(
            String,
            '/motion_coordination/status',
            self._status_callback,
            10,
        )
        self._response_subscription = node.create_subscription(
            String,
            '/motion_coordination/response',
            self._response_callback,
            10,
        )

    def snapshot(self) -> Dict[str, Any]:
        """Return runtime state plus a non-secret global configuration summary."""
        with self._status_lock:
            runtime = copy.deepcopy(self._status)
            received_at = self._status_received_at
        try:
            from motion_coordination.configuration import load_config

            config = load_config(self._config_path, workspace=self._workspace)
            config_error = ''
            configured = {
                'machine_id': config.machine_id,
                'display_name': config.display_name,
                'mode': config.mode,
                'role': config.role,
                'coordinator_machine_id': config.coordinator_machine_id,
                'access_enabled': config.access.coordination_enabled,
                'listen_host': config.access.coordination_host,
                'listen_port': config.access.coordination_port,
                'peers': [
                    {'machine_id': peer.machine_id, 'url': peer.url}
                    for peer in config.peers
                ],
            }
        except (ImportError, OSError, ValueError) as exc:
            configured = {}
            config_error = str(exc)
        age = max(time.time() - received_at, 0.0) if received_at else None
        return {
            'success': not bool(config_error),
            'node_connected': age is not None and age <= 3.0,
            'status_age_sec': round(age, 3) if age is not None else None,
            'config': configured,
            'config_error': config_error,
            'runtime': runtime,
        }

    def local_execution_blocker(self) -> str:
        """Return why a local motion action conflicts with upper ownership."""
        with self._status_lock:
            control = self._status.get('execution_control')
            connected = self._status_received_at and time.time() - self._status_received_at <= 3.0
        if connected and isinstance(control, Mapping) and control.get('state') == 'network':
            return '네트워크 동기 실행이 모션 실행 제어권을 보유 중입니다'
        return ''

    def update_settings(self, payload: Mapping[str, Any]) -> Dict[str, Any]:
        """Save only local mode/role selection and restart its isolated service."""
        if not isinstance(payload, Mapping):
            raise ValueError('연동 설정 요청은 객체여야 합니다')
        allowed = {'mode', 'role', 'coordinator_machine_id'}
        if set(payload).difference(allowed):
            raise ValueError('허용되지 않은 연동 설정 항목이 있습니다')
        from motion_coordination.configuration import update_local_selection

        config = update_local_selection(
            self._config_path,
            workspace=self._workspace,
            mode=str(payload.get('mode') or ''),
            role=str(payload.get('role') or ''),
            coordinator_machine_id=str(
                payload.get('coordinator_machine_id') or ''
            ),
        )
        service = str(
            os.environ.get('MOTION_COORDINATION_SERVICE_UNIT') or ''
        ).strip()
        if service != 'motion-coordination.service':
            return {
                'success': False,
                'saved': True,
                'message': '설정은 저장했지만 PC 연동 자동실행 서비스가 설치되지 않았습니다',
                'config': self._config_summary(config),
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
                'success': False,
                'saved': True,
                'message': detail or 'PC 연동 서비스 재시작 요청 실패',
                'config': self._config_summary(config),
            }
        return {
            'success': True,
            'saved': True,
            'restart_pending': True,
            'message': '연동 설정 저장 · PC 연동 서비스 재시작 요청 완료',
            'config': self._config_summary(config),
        }

    def request_readiness(self) -> Dict[str, Any]:
        """Request readiness and discard a result crossing a project boundary."""
        start_generation = int(self._project_generation())
        request_id = f'coordination-{uuid.uuid4().hex}'
        operation_id = f'readiness-{uuid.uuid4().hex}'
        self._publisher.publish(String(data=json.dumps({
            'request_id': request_id,
            'command': 'check_readiness',
            'network_operation_id': operation_id,
        }, ensure_ascii=False, separators=(',', ':'))))
        deadline = time.monotonic() + 12.0
        result = None
        while time.monotonic() < deadline:
            with self._result_lock:
                result = self._results.pop(request_id, None)
            if result is not None:
                break
            time.sleep(0.02)
        if int(self._project_generation()) != start_generation:
            return {
                'success': False,
                'stale_project_generation': True,
                'message': '프로젝트 전환 전 시작한 준비 확인 결과를 폐기했습니다',
                'results': [],
            }
        if result is None:
            return {
                'success': False,
                'message': 'PC 연동 노드 준비 확인 응답 없음',
                'results': [],
            }
        return result

    def request_control(self, payload: Mapping[str, Any]) -> Dict[str, Any]:
        """Request one coordinator command and enforce the project boundary."""
        if not isinstance(payload, Mapping):
            raise ValueError('연동 실행 요청은 객체여야 합니다')
        start_generation = int(self._project_generation())
        request_id = f'coordination-control-{uuid.uuid4().hex}'
        self._publisher.publish(String(data=json.dumps({
            'request_id': request_id, 'command': 'control',
            'payload': dict(payload),
        }, ensure_ascii=False, separators=(',', ':'))))
        deadline = time.monotonic() + 18.0
        result = None
        while time.monotonic() < deadline:
            with self._result_lock:
                result = self._results.pop(request_id, None)
            if result is not None:
                break
            time.sleep(0.02)
        if int(self._project_generation()) != start_generation:
            return {
                'success': False, 'stale_project_generation': True,
                'message': '프로젝트 전환 전 시작한 연동 실행 결과를 폐기했습니다',
                'results': [],
            }
        return result or {
            'success': False, 'message': 'PC 연동 노드 실행 응답 없음', 'results': [],
        }

    def _status_callback(self, message: String) -> None:
        try:
            payload = json.loads(message.data)
        except (TypeError, ValueError):
            return
        if not isinstance(payload, dict):
            return
        with self._status_lock:
            self._status = payload
            self._status_received_at = time.time()

    def _response_callback(self, message: String) -> None:
        try:
            payload = json.loads(message.data)
        except (TypeError, ValueError):
            return
        if not isinstance(payload, dict):
            return
        request_id = str(payload.get('request_id') or '').strip()
        if not request_id:
            return
        with self._result_lock:
            self._results[request_id] = payload
            if len(self._results) > 64:
                self._results.pop(next(iter(self._results)), None)

    @staticmethod
    def _config_summary(config: Any) -> Dict[str, Any]:
        return {
            'machine_id': config.machine_id,
            'display_name': config.display_name,
            'mode': config.mode,
            'role': config.role,
            'coordinator_machine_id': config.coordinator_machine_id,
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
    if command in {'stop_motion', 'stop_initialize', 'stop_now', 'cancel_before_start'}:
        return bridge.motion_run_stop()
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
    if command == 'initialize':
        return bridge.motion_run_initialize(request)
    if command == 'run_once':
        return bridge.motion_run_start(request)
    if command == 'start_at':
        repeat_count = int(payload.get('repeat_count') or 1)
        request.update({
            'run_mode': 'continuous' if repeat_count > 1 else 'once',
            'scheduled_start_at': payload.get('start_at'),
            'synchronized_cycle_sec': payload.get('cycle_sec'),
            'synchronized_repeat_count': repeat_count,
            'hold_final_until_cycle': bool(payload.get('hold_final', True)),
            'network_lease_id': str(payload.get('lease_id') or ''),
        })
        return bridge.motion_run_start(request)
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
