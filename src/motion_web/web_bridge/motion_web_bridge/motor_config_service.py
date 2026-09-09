"""모터축 설정 · 저장 · 적용 · 재시작 · 실행 해제.

`MotionWebBridge`에서 떼어냈다 · §5 분해 목표안의 `MotorConfigService` · §6-19

**가변 상태 두 개의 소유자를 여기로 옮겼다.** §6-13이 "계약을 어디로 옮길지
먼저 정해야 한다"고 남겨둔 항목이다.

- `selected` (옛 `motor_config_file`) · 지금 고른 모터축 설정 파일
- `applied` (옛 `applied_motor_config_file`) · Motor Manager가 실제로 물고 있는 파일

프로젝트를 바꿀 때 `selected`를 비우는 것이 프로젝트 격리 보장이고,
`test_project_repository`가 그 성질을 검사한다. 이제 그 계약이 이 객체 안에 있다.

`lifecycle_lock`은 노드가 소유하고 넘겨준다 · `ScanOrchestrator`와 **같은 락**이다.
"지금 모터 관련 작업이 하나 돌고 있다"를 뜻하므로 나눠 가질 수 없다 · §6-18
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

from motion_common import store

from motion_web_bridge import (
    motion_file_analysis,
    motion_studio_session,
    motor_config_build,
    motor_config_rules,
)


class MotorConfigService:
    def __init__(
        self,
        bridge: Any,
        *,
        project: Any,
        runtime: Any,
        lifecycle_lock: threading.Lock,
        repository: Any,
        workspace_root: Path,
        selected: Path,
        applied: Path,
        restart_script: Path,
    ) -> None:
        self.bridge = bridge
        #: 프로젝트 서비스 협력자 (§6-23)
        self.project = project
        #: 모터 런타임 수명주기 협력자 (§6-22)
        self.runtime = runtime
        #: `ScanOrchestrator`와 공유 · 노드가 소유 (§6-18)
        self.lifecycle_lock = lifecycle_lock
        self.repository = repository
        self.workspace_root = workspace_root
        #: 지금 고른 모터축 설정 파일 · 프로젝트 전환 시 비운다
        self.selected = selected
        #: Motor Manager가 실제로 물고 있는 파일
        self.applied = applied
        self.restart_script = restart_script

    def clear_selection(self) -> None:
        """프로젝트가 바뀌면 이전 프로젝트의 설정 파일을 가리키지 않는다."""
        self.selected = Path()

    def load(self) -> Dict[str, Any]:
        try:
            self.selected = motor_config_rules.selected_motor_config_path(self.repository)
        except ValueError as exc:
            if '모터축 설정 파일이 없습니다' in str(exc):
                return {
                    'success': True,
                    'saved': False,
                    'message': '아직 저장된 모터축 설정 파일이 없습니다',
                    'config_file': '',
                    'config_revision': '',
                    'content': '',
                    'registry': motor_config_rules.empty_motor_registry(),
                }
            return {
                'success': False,
                'message': str(exc),
                'config_file': '',
                'config_revision': '',
                'content': '',
                'registry': motor_config_rules.empty_motor_registry(),
            }
        return self._payload_from_path(self.selected)

    def _payload_from_path(
        self,
        config_file: Path,
        *,
        message: str = 'motor config YAML loaded',
    ) -> Dict[str, Any]:
        """Build the UI/API payload from one concrete motor_axes YAML path."""
        path = Path(config_file)
        if not path.is_file():
            return {
                'success': False,
                'message': 'motor config YAML not found',
                'config_file': str(path),
                'config_revision': '',
                'content': '',
                'registry': motor_config_rules.empty_motor_registry(),
            }
        try:
            raw = path.read_bytes()
            content = raw.decode('utf-8')
            config = yaml.safe_load(content) or {}
        except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
            return {
                'success': False,
                'message': f'failed to load motor config YAML: {exc}',
                'config_file': str(path),
                'config_revision': '',
                'content': '',
                'registry': motor_config_rules.empty_motor_registry(),
            }
        if not isinstance(config, dict):
            return {
                'success': False,
                'message': 'motor config YAML root must be an object',
                'config_file': str(path),
                'config_revision': '',
                'content': '',
                'registry': motor_config_rules.empty_motor_registry(),
            }
        axis_config = motor_config_rules.expand_shared_driver_profiles(config)
        if axis_config != config:
            content = yaml.safe_dump(axis_config, sort_keys=False, allow_unicode=True)
        self.selected = path
        return {
            'success': True,
            'message': message,
            'config_file': str(path),
            'config_revision': hashlib.sha256(raw).hexdigest(),
            'content': content,
            'registry': motor_config_rules.registry_from_motor_config(axis_config),
        }

    def _file_from_payload(self, payload: Dict[str, Any]) -> Path:
        project_id = self.repository.selected_project_id()
        if not project_id:
            raise ValueError('통합 프로젝트를 먼저 선택하세요')
        detail = self.repository.get_project(project_id)
        project = detail.get('project') or {}
        active = project.get('active_files') or {}
        active_name = str(active.get('motor_axes') or '').strip()
        config_dir = (Path(str(project.get('path') or '')) / 'motor_axes').resolve()
        active_path = config_dir / active_name if active_name else config_dir / 'motor_axes.yaml'
        self.selected = active_path
        requested = str(payload.get('file_name') or '').strip()
        if not requested:
            return active_path

        name = Path(requested).name.strip()
        if not name or name in ('.', '..'):
            raise ValueError('motor config file name is empty')
        if not name.lower().endswith(('.yaml', '.yml')):
            name = f'{name}.yaml'

        target = (config_dir / name).resolve()
        try:
            target.relative_to(config_dir)
        except ValueError as exc:
            raise ValueError('motor config file must stay under config directory') from exc
        return target

    def _read_current(self) -> Dict[str, Any]:
        try:
            self.selected = motor_config_rules.selected_motor_config_path(self.repository)
        except ValueError:
            return motor_config_build.default_motor_config(self.workspace_root)
        if not self.selected.is_file():
            return motor_config_build.default_motor_config(self.workspace_root)
        content = self.selected.read_text(encoding='utf-8')
        config = yaml.safe_load(content) or {}
        if isinstance(config, dict):
            return config
        return motor_config_build.default_motor_config(self.workspace_root)

    def _write(self, content: str, target_file: Optional[Path] = None) -> None:
        target = target_file or self.selected
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.is_file():
            timestamp = time.strftime('%Y%m%d-%H%M%S')
            history_dir = target.parent.parent / 'runtime' / 'history' / 'motor_axes'
            history_dir.mkdir(parents=True, exist_ok=True)
            backup = history_dir / f'{timestamp}-{target.name}'
            counter = 2
            while backup.exists():
                backup = history_dir / f'{timestamp}-{counter}-{target.name}'
                counter += 1
            store.atomic_write_text(backup, target.read_text(encoding='utf-8'))
        # 원자적 교체 · 기록 도중 죽어도 모터 설정이 반쪽으로 남지 않는다 · §6-24
        store.atomic_write_text(target, content.rstrip() + '\n')
        self.selected = target
        motor_config_rules.write_motor_config_selection(self.repository, target)

    def save(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        try:
            self.bridge._ensure_project_mutation_allowed(
                self.repository.selected_project_id()
            )
            target_file = self._file_from_payload(payload)
            expected_revision = str(payload.get('base_revision') or '').strip()
            if target_file.is_file():
                actual_revision = hashlib.sha256(target_file.read_bytes()).hexdigest()
                if not expected_revision:
                    raise ValueError(
                        '설정 파일 버전 정보가 없습니다. 설정 다시 불러오기 후 저장하세요'
                    )
                if expected_revision != actual_revision:
                    raise ValueError(
                        '설정 파일이 화면을 불러온 뒤 변경됐습니다. '
                        '현재 파일 보호를 위해 저장을 거부했습니다. 설정 다시 불러오기를 실행하세요'
                    )
            elif expected_revision:
                raise ValueError(
                    '화면에서 불러온 설정 파일이 현재 존재하지 않습니다. '
                    '설정 다시 불러오기를 실행하세요'
                )
            if 'content' in payload:
                content = str(payload.get('content') or '')
                config = yaml.safe_load(content) or {}
                config = motor_config_rules.expand_shared_driver_profiles(config)
                content = yaml.safe_dump(config, sort_keys=False, allow_unicode=True)
            else:
                registry = payload.get('registry', payload)
                if not isinstance(registry, dict):
                    raise ValueError('registry must be an object')
                normalized = motor_config_rules.normalize_motor_registry(registry)
                normalized['updated_at'] = time.time()
                current = self._read_current()
                config = motor_config_build.motor_config_from_registry(
                    self.workspace_root, normalized, current
                )
                content = yaml.safe_dump(config, sort_keys=False, allow_unicode=True)

            if not isinstance(config, dict):
                raise ValueError('motor config YAML root must be an object')
            configured_axes = len(
                motor_config_rules.registry_from_motor_config(
                    motor_config_rules.expand_shared_driver_profiles(config)
                ).get('motors') or []
            )
            if configured_axes == 0:
                raise ValueError(
                    '0축 모터 설정은 저장할 수 없습니다. '
                    '설정 파일 제거는 현재 설정 파일 휴지통으로 이동을 사용하세요'
                )

            self._write(content, target_file)
        except (OSError, ValueError, yaml.YAMLError) as exc:
            return {
                'success': False,
                'message': f'failed to save motor config YAML: {exc}',
                'config_file': str(self.selected),
                'content': payload.get('content', ''),
                'registry': motor_config_rules.empty_motor_registry(),
            }

        # Persist project ownership of the written file, then return that same
        # file as the save response. Do not rebuild the response through the
        # active-file selector alone: a new project has an empty
        # active_files.motor_axes until sync finishes, and an empty registry
        # response would wipe the UI axis list (names/aliases) and leave
        # "설정 적용 및 재시작" disabled until a manual reload.
        synced = self.project.sync_file({}, 'motor_axes', target_file)
        result = self._payload_from_path(
            target_file,
            message=(
                'motor config YAML saved; restart motor_manager_node to apply'
            ),
        )
        if not result.get('success'):
            return result
        saved_motors = (result.get('registry') or {}).get('motors') or []
        if len(saved_motors) == 0:
            return {
                'success': False,
                'message': (
                    '모터축 설정 파일은 저장됐지만 저장 응답에 축 목록이 없습니다. '
                    '설정 불러오기로 파일을 다시 확인하세요'
                ),
                'config_file': str(target_file),
                'config_revision': '',
                'content': '',
                'registry': motor_config_rules.empty_motor_registry(),
            }
        if 'project_sync' in synced:
            result['project_sync'] = synced['project_sync']
        if 'project_sync_warning' in synced:
            result['project_sync_warning'] = synced['project_sync_warning']
        return result

    def delete(self) -> Dict[str, Any]:
        project_id = self.repository.selected_project_id()
        if not project_id:
            return {
                'success': False,
                'message': '통합 프로젝트를 먼저 선택하세요',
                'config_file': '',
                'content': '',
                'registry': motor_config_rules.empty_motor_registry(),
            }
        try:
            current_path = motor_config_rules.selected_motor_config_path(self.repository)
            deleted = self.project.delete_file(
                project_id, 'motor_axes', current_path.name
            )
        except (OSError, ValueError) as exc:
            return {
                'success': False,
                'message': f'모터축 설정 파일 삭제 실패: {exc}',
                'config_file': '',
                'content': '',
                'registry': motor_config_rules.empty_motor_registry(),
            }

        loaded = self.load()
        replacement = str(deleted.get('replacement_active_file') or '')
        loaded.update({
            'success': True,
            'deleted_file': str(deleted.get('deleted_file') or current_path.name),
            'replacement_active_file': replacement,
            'trash_path': str(deleted.get('trash_path') or ''),
            'message': (
                f'모터축 설정 파일을 프로젝트 휴지통으로 이동하고 {replacement} 파일을 선택했습니다'
                if replacement
                else '모터축 설정 파일을 프로젝트 휴지통으로 이동했습니다'
            ),
        })
        return loaded

    def apply(self) -> Dict[str, Any]:
        if not self.restart_script.is_file():
            return {
                'success': False,
                'message': f'restart script not found: {self.restart_script}',
                'restart_script': str(self.restart_script),
                **self.bridge.snapshot(),
            }

        try:
            self.project.ensure_change_allowed()
        except ValueError as exc:
            return {
                'success': False,
                'message': str(exc),
                **self.bridge.snapshot(),
            }
        lifecycle_lock = getattr(self, 'lifecycle_lock', None)
        if lifecycle_lock is None:
            lifecycle_lock = threading.Lock()
            self.lifecycle_lock = lifecycle_lock
        if not lifecycle_lock.acquire(blocking=False):
            return {
                'success': False,
                'message': '다른 모터 설정·검색·재시작 작업이 진행 중입니다',
                **self.bridge.snapshot(),
            }
        project_id = self.repository.selected_project_id()
        if not project_id:
            lifecycle_lock.release()
            return {
                'success': False,
                'message': '적용할 프로젝트를 먼저 선택하세요',
                **self.bridge.snapshot(),
            }
        operation: Dict[str, Any] = {}
        previous_runtime = self.repository.runtime.motor_runtime_target_snapshot()
        try:
            operation = self.repository.runtime.begin_motor_operation(
                'motor_apply',
                'preparing',
                timeout_sec=45.0,
                details={
                    'project_id': project_id,
                    'previous_runtime': previous_runtime,
                },
            )
            prepared = self.repository.prepare_runtime_motor_config(project_id)
            runtime_file = self.repository.runtime.mark_runtime_motor_config_applied(
                project_id
            )
            expected_axes = motion_file_analysis.configured_axes_from_runtime_file(
                runtime_file
            )
            if not expected_axes:
                raise ValueError('적용할 모터 실행 설정에서 대상 축을 확인할 수 없습니다')
            self.repository.runtime.update_motor_operation(
                str(operation['operation_id']),
                'prepared',
                details={
                    'runtime_file': str(runtime_file),
                    'expected_axes': expected_axes,
                },
            )
            managed_service = str(
                os.environ.get('MOTION_CONTROL_SERVICE_UNIT') or ''
            ).strip()
            motor_service = str(
                os.environ.get('MOTION_MOTOR_SERVICE_UNIT') or ''
            ).strip()
            if managed_service and managed_service != 'motion-control.service':
                raise ValueError('허용되지 않은 자동실행 서비스 이름입니다')
            if managed_service:
                if motor_service != 'motion-motor.service':
                    raise ValueError(
                        'Motor Manager 분리 서비스가 설치되지 않았습니다. '
                        '최초 설치를 다시 실행하세요'
                    )
                motor_config_rules.schedule_managed_service_restart(
                    motor_service,
                    managed_service,
                )
                restart_mode = 'split_managed_services'
            else:
                environment = dict(os.environ)
                environment['MOTOR_CONFIG_FILE'] = str(runtime_file)
                environment['MOTION_WORKSPACE'] = str(self.workspace_root)
                environment['START_MOTOR_MANAGER'] = 'true'
                subprocess.Popen(
                    ['/bin/bash', str(self.restart_script)],
                    cwd=str(self.restart_script.parent.parent),
                    env=environment,
                    start_new_session=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                restart_mode = 'legacy_script'
            self.repository.runtime.update_motor_operation(
                str(operation['operation_id']),
                'restart_requested',
                message='새 모터 설정으로 서비스 재시작 요청 완료',
                details={'runtime_file': str(runtime_file)},
            )
        except (OSError, ValueError, yaml.YAMLError) as exc:
            operation_id = str(operation.get('operation_id') or '')
            if operation_id:
                try:
                    self.repository.runtime.finish_motor_operation(
                        operation_id,
                        'failure',
                        phase='failed',
                        error=str(exc),
                    )
                except ValueError:
                    pass
            self.repository.runtime.restore_motor_runtime_target(previous_runtime)
            return {
                'success': False,
                'message': f'프로젝트 설정을 적용할 수 없습니다: {exc}',
                'restart_script': str(self.restart_script),
                **self.bridge.snapshot(),
            }
        finally:
            lifecycle_lock.release()

        return {
            'success': True,
            'message': '프로젝트 설정 적용을 시작했습니다. 웹이 잠시 후 다시 연결됩니다',
            'restart_script': str(self.restart_script),
            'restart_mode': restart_mode,
            'runtime_config': {
                **prepared,
                'session_file': str(runtime_file),
                'session_id': self.repository.runtime.motor_runtime_state().get(
                    'session_id', ''
                ),
            },
            'motor_operation': self.repository.runtime.motor_operation_status(),
            **self.bridge.snapshot(),
        }

    def clear_stopping_release_state(self) -> None:
        run_lock = getattr(self.bridge, '_motion_run_lock', None)
        if run_lock is None:
            run_status = getattr(self.bridge, '_motion_run_status', {}) or {}
            if str(run_status.get('state') or '') == 'stopping':
                self.bridge._motion_run_status = {
                    **dict(run_status),
                    'state': 'stopped',
                    'message': '실행 적용 해제로 정지 상태를 정리했습니다',
                }
        else:
            with run_lock:
                run_status = getattr(self.bridge, '_motion_run_status', {}) or {}
                if str(run_status.get('state') or '') == 'stopping':
                    self.bridge._motion_run_status = {
                        **dict(run_status),
                        'state': 'stopped',
                        'message': '실행 적용 해제로 정지 상태를 정리했습니다',
                    }
        studio_session = motion_studio_session.session_of(self.bridge)
        if studio_session is not None:
            studio_session.settle_state(
                when='stopping',
                becomes='idle',
                message='실행 적용 해제로 정지 상태를 정리했습니다',
            )

    def restart_managed_program(self) -> Dict[str, Any]:
        """Restart only upper-level nodes while Motor Manager keeps running."""
        self.clear_stopping_release_state()
        managed_service = str(
            os.environ.get('MOTION_CONTROL_SERVICE_UNIT') or ''
        ).strip()
        if managed_service != 'motion-control.service':
            self.clear_stopping_release_state()
            return {
                'success': False,
                'message': '자동실행 서비스가 설치되지 않았습니다. 최초 설치를 먼저 완료하세요',
                **self.bridge.snapshot(),
            }
        if os.environ.get('MOTION_MOTOR_SERVICE_UNIT') != 'motion-motor.service':
            self.clear_stopping_release_state()
            return {
                'success': False,
                'message': (
                    'Motor Manager 분리 서비스가 설치되지 않았습니다. '
                    '최초 설치를 다시 실행하세요'
                ),
                **self.bridge.snapshot(),
            }
        self.project.ensure_change_allowed()
        restart_services = [managed_service]
        coordination_service = str(
            os.environ.get('MOTION_COORDINATION_SERVICE_UNIT') or ''
        ).strip()
        if coordination_service == 'motion-coordination.service':
            restart_services.append(coordination_service)
        try:
            motor_config_rules.schedule_managed_service_restart(*restart_services)
        except OSError as exc:
            self.clear_stopping_release_state()
            return {
                'success': False,
                'message': f'프로그램 재시작 요청에 실패했습니다: {exc}',
                **self.bridge.snapshot(),
            }
        return {
            'success': True,
            'message': (
                '상위 프로그램 재시작을 시작했습니다. '
                'Motor Manager와 현재 서보 상태는 유지됩니다'
            ),
            'restart_mode': 'upper_service',
            **self.bridge.snapshot(),
        }

    def restart_motor_control(self) -> Dict[str, Any]:
        """Restart the persistent Motor Manager only after explicit confirmation."""
        self.clear_stopping_release_state()
        motor_service = str(
            os.environ.get('MOTION_MOTOR_SERVICE_UNIT') or ''
        ).strip()
        if motor_service != 'motion-motor.service':
            self.clear_stopping_release_state()
            return {
                'success': False,
                'message': (
                    'Motor Manager 분리 서비스가 설치되지 않았습니다. '
                    '최초 설치를 다시 실행하세요'
                ),
                **self.bridge.snapshot(),
            }
        runtime_config = self.repository.runtime.selected_runtime_motor_config()
        if runtime_config is None:
            self.clear_stopping_release_state()
            return {
                'success': False,
                'message': (
                    '현재 프로젝트의 모터축 설정이 적용되지 않았습니다. '
                    '모터 관리에서 설정 적용·재시작을 먼저 실행하세요'
                ),
                **self.bridge.snapshot(),
            }
        expected_axes = motion_file_analysis.configured_axes_from_runtime_file(runtime_config)
        if not expected_axes:
            self.clear_stopping_release_state()
            return {
                'success': False,
                'message': '현재 모터 실행 설정에서 대상 축을 확인할 수 없습니다',
                **self.bridge.snapshot(),
            }
        self.project.ensure_change_allowed()
        lifecycle_lock = getattr(self, 'lifecycle_lock', None)
        if lifecycle_lock is None:
            lifecycle_lock = threading.Lock()
            self.lifecycle_lock = lifecycle_lock
        if not lifecycle_lock.acquire(blocking=False):
            self.clear_stopping_release_state()
            return {
                'success': False,
                'message': '다른 모터 설정·검색·재시작 작업이 진행 중입니다',
                **self.bridge.snapshot(),
            }
        try:
            operation = self.runtime.restart_lifecycle().begin(
                project_id=self.repository.selected_project_id(),
                runtime_file=runtime_config,
                expected_axes=expected_axes,
            )
        except (OSError, RuntimeError, ValueError) as exc:
            operation_id = str(
                locals().get('operation', {}).get('operation_id') or ''
            )
            if operation_id:
                try:
                    self.repository.runtime.finish_motor_operation(
                        operation_id,
                        'failure',
                        phase='failed',
                        error=str(exc),
                    )
                except ValueError:
                    pass
            return {
                'success': False,
                'message': f'모터 제어 시스템 재시작 요청에 실패했습니다: {exc}',
                **self.bridge.snapshot(),
            }
        finally:
            lifecycle_lock.release()
        return {
            'success': True,
            'message': (
                '모터 제어 시스템 재시작을 시작했습니다. '
                'AC Servo가 OFF됐다가 자동 ON될 수 있습니다'
            ),
            'restart_mode': 'motor_service',
            'motor_operation': self.repository.runtime.motor_operation_status(),
            **self.bridge.snapshot(),
        }

    def clear_runtime_application(self) -> Dict[str, Any]:
        """Stop Motor Manager and clear runtime ownership for project deletion."""
        motor_service = str(
            os.environ.get('MOTION_MOTOR_SERVICE_UNIT') or ''
        ).strip()
        if motor_service != 'motion-motor.service':
            return {
                'success': False,
                'message': (
                    'Motor Manager 분리 서비스가 설치되지 않았습니다. '
                    '최초 설치를 다시 실행하세요'
                ),
                **self.bridge.snapshot(),
            }
        runtime_state = self.repository.runtime.motor_runtime_state()
        runtime_project_id = str(runtime_state.get('target_project_id') or '').strip()
        if not runtime_project_id:
            return {
                'success': True,
                'cleared': False,
                'message': '해제할 모터 실행 적용이 없습니다',
                'runtime_project_id': '',
                **self.bridge.snapshot(),
            }
        project_blocker = self.project.change_blocker(
            allow_run_stopping=True,
            allow_studio_stopping=True,
        )
        if project_blocker:
            return {
                'success': False,
                'message': project_blocker,
                **self.bridge.snapshot(),
            }
        execution_blocker = self.bridge._coordination_execution_blocker()
        if execution_blocker:
            return {
                'success': False,
                'message': f'실행 적용 해제 미실행: {execution_blocker}',
                **self.bridge.snapshot(),
            }
        moving_blocker = self.runtime.ethercat_scan_safety_blocker(
            require_fresh_motor_state=self.runtime.managed_service_active(motor_service),
            allow_run_stopping=True,
            allow_studio_stopping=True,
        )
        if moving_blocker:
            return {
                'success': False,
                'message': (
                    f'실행 적용 해제 미실행: {moving_blocker}. '
                    '먼저 「전체 동작 정지」를 실행하세요'
                ),
                **self.bridge.snapshot(),
            }
        lifecycle_lock = getattr(self, 'lifecycle_lock', None)
        if lifecycle_lock is None:
            lifecycle_lock = threading.Lock()
            self.lifecycle_lock = lifecycle_lock
        if not lifecycle_lock.acquire(blocking=False):
            return {
                'success': False,
                'message': '다른 모터 설정·검색·재시작 작업이 진행 중입니다',
                **self.bridge.snapshot(),
            }
        operation: Dict[str, Any] = {}
        cleared: Dict[str, Any] = {}
        try:
            operation = self.repository.runtime.begin_motor_operation(
                'motor_runtime_clear',
                'preparing',
                timeout_sec=30.0,
                details={'previous_project_id': runtime_project_id},
            )
            try:
                self.bridge.motion_run_stop()
            except Exception:
                pass
            try:
                self.bridge.publish_safety_stop(False)
            except Exception:
                pass
            if self.runtime.managed_service_active(motor_service):
                self.repository.runtime.update_motor_operation(
                    str(operation['operation_id']),
                    'stopping_runtime',
                    message='Motor Manager 정지 및 EtherCAT 소유권 해제 중',
                )
                self.runtime.run_managed_service('stop', motor_service)
                try:
                    motor_config_rules.wait_for_ethercat_release(8.0)
                except Exception:
                    pass
            cleared = self.repository.runtime.clear_motor_runtime_target()
            motor_config_rules.clear_motor_config_selection(self.repository)
            self.clear_stopping_release_state()
            # Drop launch-time ownership so delete / runtime_project_id update
            # without waiting for a Bridge restart.
            self.applied = Path()
            self.repository.runtime.finish_motor_operation(
                str(operation['operation_id']),
                'success',
                phase='completed',
                message=str(cleared.get('message') or '모터 실행 적용 해제 완료'),
                details={
                    'previous_project_id': cleared.get('previous_project_id') or '',
                    'motor_service_stopped': True,
                },
            )
        except (OSError, RuntimeError, ValueError) as exc:
            operation_id = str(operation.get('operation_id') or '')
            if operation_id:
                try:
                    self.repository.runtime.finish_motor_operation(
                        operation_id,
                        'failure',
                        phase='failed',
                        error=str(exc),
                    )
                except ValueError:
                    pass
            return {
                'success': False,
                'message': f'실행 적용 해제 실패: {exc}',
                **self.bridge.snapshot(),
            }
        finally:
            lifecycle_lock.release()
        return {
            'success': True,
            'cleared': bool(cleared.get('cleared')),
            'previous_project_id': cleared.get('previous_project_id') or '',
            'message': (
                f"{cleared.get('message') or '모터 실행 적용을 해제했습니다'}. "
                'Motor Manager는 정지 상태입니다. '
                '다시 사용하려면 프로젝트에서 「설정 적용 및 재시작」을 실행하세요'
            ),
            'runtime_project_id': '',
            'motor_operation': self.repository.runtime.motor_operation_status(),
            **self.project.list_projects(),
            **self.bridge.snapshot(),
        }
