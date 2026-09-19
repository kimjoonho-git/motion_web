"""모터 실행 상태 파일과 조작 기록 · §6-47.

`ProjectRepository`에서 떼어냈다. **파일과 그 락을 이 객체가 갖는다** ·
`.motor_runtime.json` 하나에 적용된 설정·조작 진행 상황이 함께 들어 있고,
웹·조율·모니터가 모두 이 파일을 통해 이야기한다.

프로젝트 목록·파일 트리와는 다른 물건이다 · 저장소는 프로젝트 **파일들**을
다루고, 이쪽은 **지금 무엇이 돌고 있는지**를 다룬다.

프로젝트 경로·선택 상태는 저장소에 남겨 두고 필요할 때 물어본다.
"""

from __future__ import annotations

import json
import time
import uuid
from functools import wraps
from pathlib import Path
from typing import Any, Dict, Optional

from motion_common import store

from .project_paths import _sha256, local_directory

MOTOR_RUNTIME_TARGET_FIELDS = {
    'target_project_id',
    'session_id',
    'config_relpath',
    'config_sha256',
    'project_generation',
    'applied_at',
}


#: 모터 작업이 겹칠 때 하는 말 · §6-188
#:
#: 이 자물쇠의 주인은 `MotorRuntimeStore` 다 · 그런데 이 문구가 **파일 셋에
#: 여섯 번** 흩어져 있었다 (여기 둘 · `motor_config_service` 셋 ·
#: `scan_orchestrator` 하나) · 하나만 고치면 **어느 길로 거절당했느냐에 따라
#: 화면이 다른 말을 한다** · 사용자는 같은 상황인데 다른 안내를 본다.
MOTOR_BUSY_MESSAGE = '다른 모터 설정·검색·재시작 작업이 진행 중입니다'


def _motor_runtime_locked(method):
    """모터 실행 상태 파일 갱신을 프로세스 간 락으로 감싼다.

    예전에는 재진입 `flock`을 여기서 직접 구현했다 · 스레드 지역 깊이 계수까지
    손으로 세고 있었다. 지금은 `store.file_lock`이 같은 일을 한다 · §6-24
    """
    @wraps(method)
    def guarded(self, *args, **kwargs):
        with store.file_lock(self.path):
            return method(self, *args, **kwargs)

    return guarded


class MotorRuntimeStore:
    def __init__(self, repository: Any, path: Path) -> None:
        self.repository = repository
        #: `.motor_runtime.json` · 이 파일과 그 락을 이 객체가 갖는다
        self.path = path

    @_motor_runtime_locked
    def _migrate_legacy_motor_runtime_state(self) -> None:
        """Separate the applied motor target from the editor selection.

        Older releases stored both identities in ``.selected_project.json``.
        Project selection is an editing action and must not erase or retarget
        the already running Motor Manager.  Migrate only a runtime file whose
        ownership and location can be proven inside this repository.
        """
        selection = self.repository._read_selection()
        applied = str(selection.get('applied_project_id') or '').strip()
        if not applied:
            return
        if self.motor_runtime_state().get('valid') is not True:
            try:
                project_dir = self.repository._project_dir(applied)
                runtime = project_dir / 'runtime' / 'applied_motor_config.yaml'
                content = runtime.read_bytes()
            except (OSError, ValueError):
                return
            existing = self._read_motor_runtime_payload()
            self._write_motor_runtime_state({
                **existing,
                'version': 1,
                'target_project_id': applied,
                'config_sha256': _sha256(content),
                'project_generation': self.repository.project_generation(),
                'applied_at': time.time(),
            })
        selection.pop('applied_project_id', None)
        self.repository._atomic_write(
            self.repository.selection_file,
            json.dumps(selection, ensure_ascii=False) + '\n',
        )

    @_motor_runtime_locked
    def mark_runtime_motor_config_applied(self, project_id: Any) -> Path:
        """Commit an immutable runtime session for the managed motor service."""
        project_dir = self.repository._project_dir(project_id)
        if self.repository.selected_project_id() != project_dir.name:
            raise ValueError('현재 선택 프로젝트의 모터 설정만 적용할 수 있습니다')
        prepared = project_dir / 'runtime' / 'applied_motor_config.yaml'
        if not prepared.is_file():
            raise ValueError('적용할 런타임 모터축 설정 파일이 없습니다')
        runtime_content = prepared.read_bytes()
        config_sha256 = _sha256(runtime_content)
        session_id = f'motor-{config_sha256}'
        session_dir = local_directory(project_dir, 'runtime', 'sessions')
        runtime = session_dir / f'{session_id}.yaml'
        if runtime.exists():
            if runtime.is_symlink() or _sha256(runtime.read_bytes()) != config_sha256:
                raise ValueError('동일 ID의 모터 실행 세션 파일이 손상되었습니다')
        else:
            self.repository._atomic_write(runtime, runtime_content.decode('utf-8'))
        existing = self._read_motor_runtime_payload()
        self._write_motor_runtime_state({
            **existing,
            'version': 1,
            'target_project_id': project_dir.name,
            'session_id': session_id,
            'config_relpath': runtime.relative_to(project_dir).as_posix(),
            'config_sha256': config_sha256,
            'project_generation': self.repository.project_generation(),
            'applied_at': time.time(),
        })
        selection = self.repository._read_selection()
        if 'applied_project_id' in selection:
            selection.pop('applied_project_id', None)
            self.repository._atomic_write(
                self.repository.selection_file,
                json.dumps(selection, ensure_ascii=False) + '\n',
            )
        return runtime

    @_motor_runtime_locked
    def motor_runtime_state(self) -> Dict[str, Any]:
        """Return the durable motor target plus strict file validation."""
        payload = self._read_motor_runtime_payload()
        if not payload:
            return {}
        operation = payload.get('operation')
        operation_payload = dict(operation) if isinstance(operation, dict) else {}
        project_id = str(payload.get('target_project_id') or '').strip()
        if not project_id:
            return {
                **payload,
                'operation': operation_payload,
                'valid': False,
                'validation_error': 'motor runtime target is not configured',
            }
        if project_id != Path(project_id).name:
            return {**payload, 'valid': False, 'validation_error': 'invalid project id'}
        try:
            project_dir = self.repository._project_dir(project_id)
            runtime = self._runtime_config_path(project_dir, payload)
            content = runtime.read_bytes()
        except (OSError, ValueError) as exc:
            return {**payload, 'valid': False, 'validation_error': str(exc)}
        expected_sha = str(payload.get('config_sha256') or '').strip()
        actual_sha = _sha256(content)
        valid = bool(expected_sha and expected_sha == actual_sha)
        return {
            **payload,
            'operation': operation_payload,
            'config_file': str(runtime),
            'actual_config_sha256': actual_sha,
            'valid': valid,
            'validation_error': '' if valid else 'runtime config sha256 mismatch',
        }

    @_motor_runtime_locked
    def motor_runtime_target_snapshot(self) -> Dict[str, Any]:
        """Return only a verified target suitable for transactional rollback."""
        state = self.motor_runtime_state()
        if state.get('valid') is not True:
            return {}
        return {
            key: state[key]
            for key in MOTOR_RUNTIME_TARGET_FIELDS
            if key in state
        }

    @_motor_runtime_locked
    def restore_motor_runtime_target(
        self,
        target: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Restore a previous target without discarding the current operation."""
        payload = self._read_motor_runtime_payload() or {'version': 1}
        for key in MOTOR_RUNTIME_TARGET_FIELDS:
            payload.pop(key, None)
        if isinstance(target, dict):
            payload.update({
                key: target[key]
                for key in MOTOR_RUNTIME_TARGET_FIELDS
                if key in target
            })
        payload['version'] = 1
        self._write_motor_runtime_state(payload)
        return self.motor_runtime_state()

    @_motor_runtime_locked
    def _read_motor_runtime_payload(self) -> Dict[str, Any]:
        try:
            payload = json.loads(self.path.read_text(encoding='utf-8'))
        except (OSError, ValueError, json.JSONDecodeError):
            return {}
        if not isinstance(payload, dict) or payload.get('version') != 1:
            return {}
        return dict(payload)

    @_motor_runtime_locked
    def _write_motor_runtime_state(self, payload: Dict[str, Any]) -> None:
        self.repository._atomic_write(
            self.path,
            json.dumps(payload, ensure_ascii=False, indent=2) + '\n',
        )

    @_motor_runtime_locked
    def begin_motor_operation(
        self,
        operation_type: str,
        phase: str,
        *,
        timeout_sec: float,
        details: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        payload = self._read_motor_runtime_payload() or {'version': 1}
        current = payload.get('operation')
        now = time.time()
        if (
            isinstance(current, dict)
            and current.get('status') == 'running'
            and float(current.get('deadline_at') or 0.0) > now
        ):
            raise ValueError(MOTOR_BUSY_MESSAGE)
        operation = {
            'operation_id': f'motor-{uuid.uuid4().hex}',
            'type': str(operation_type),
            'phase': str(phase),
            'status': 'running',
            'started_at': now,
            'updated_at': now,
            'deadline_at': now + max(float(timeout_sec), 1.0),
            'message': '',
            'error': '',
            'details': dict(details or {}),
        }
        payload['operation'] = operation
        self._write_motor_runtime_state(payload)
        return dict(operation)

    @_motor_runtime_locked
    def update_motor_operation(
        self,
        operation_id: str,
        phase: str,
        *,
        message: str = '',
        details: Optional[Dict[str, Any]] = None,
        timeout_sec: Optional[float] = None,
    ) -> Dict[str, Any]:
        payload = self._read_motor_runtime_payload()
        operation = payload.get('operation')
        if (
            not isinstance(operation, dict)
            or operation.get('operation_id') != operation_id
            or operation.get('status') != 'running'
        ):
            raise ValueError('진행 중인 모터 작업이 일치하지 않습니다')
        operation = dict(operation)
        operation['phase'] = str(phase)
        operation['updated_at'] = time.time()
        if timeout_sec is not None:
            operation['deadline_at'] = operation['updated_at'] + max(
                float(timeout_sec), 1.0
            )
        if message:
            operation['message'] = str(message)
        if details:
            merged = dict(operation.get('details') or {})
            merged.update(details)
            operation['details'] = merged
        payload['operation'] = operation
        self._write_motor_runtime_state(payload)
        return dict(operation)

    @_motor_runtime_locked
    def finish_motor_operation(
        self,
        operation_id: str,
        status: str,
        *,
        phase: str,
        message: str = '',
        error: str = '',
        details: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        if status not in {
            'success',
            'partial',
            'failure',
            'timeout',
            'cancelled',
        }:
            raise ValueError('올바르지 않은 모터 작업 종료 상태입니다')
        payload = self._read_motor_runtime_payload()
        operation = payload.get('operation')
        if not isinstance(operation, dict) or operation.get('operation_id') != operation_id:
            raise ValueError('종료할 모터 작업이 일치하지 않습니다')
        operation = dict(operation)
        operation.update({
            'phase': str(phase),
            'status': status,
            'updated_at': time.time(),
            'finished_at': time.time(),
            'message': str(message),
            'error': str(error),
        })
        if details:
            merged = dict(operation.get('details') or {})
            merged.update(details)
            operation['details'] = merged
        payload['operation'] = operation
        self._write_motor_runtime_state(payload)
        return dict(operation)

    @_motor_runtime_locked
    def motor_operation_status(self) -> Dict[str, Any]:
        payload = self._read_motor_runtime_payload()
        operation = payload.get('operation')
        if not isinstance(operation, dict):
            return {}
        result = dict(operation)
        if (
            result.get('status') == 'running'
            and float(result.get('deadline_at') or 0.0) <= time.time()
        ):
            result.update({
                'status': 'timeout',
                'phase': 'timeout',
                'error': '모터 작업 제한시간을 초과했습니다',
            })
        return result

    @_motor_runtime_locked
    def clear_motor_runtime_target(self) -> Dict[str, Any]:
        """Release Motor Manager ownership without applying another project."""
        existing = self._read_motor_runtime_payload() or {'version': 1}
        operation = existing.get('operation')
        now = time.time()
        if (
            isinstance(operation, dict)
            and operation.get('status') == 'running'
            and float(operation.get('deadline_at') or 0.0) > now
            and operation.get('type') != 'motor_runtime_clear'
        ):
            raise ValueError(MOTOR_BUSY_MESSAGE)
        previous_project_id = str(existing.get('target_project_id') or '').strip()
        if not previous_project_id:
            return {
                'cleared': False,
                'previous_project_id': '',
                'message': '해제할 모터 실행 적용이 없습니다',
            }
        self._write_motor_runtime_state({
            'version': 1,
            'target_project_id': '',
            'session_id': '',
            'config_relpath': '',
            'config_sha256': '',
            'project_generation': self.repository.project_generation(),
            'cleared_at': now,
            'cleared_from_project_id': previous_project_id,
            'operation': dict(operation) if isinstance(operation, dict) else {},
        })
        return {
            'cleared': True,
            'previous_project_id': previous_project_id,
            'message': (
                f"프로젝트 '{previous_project_id}'의 모터 실행 적용을 해제했습니다"
            ),
        }

    @staticmethod
    def _runtime_config_path(
        project_dir: Path,
        payload: Dict[str, Any],
    ) -> Path:
        """Resolve a project-owned runtime target without accepting traversal."""
        relative = str(payload.get('config_relpath') or '').strip()
        if relative:
            requested = Path(relative)
            if requested.is_absolute() or '..' in requested.parts:
                raise ValueError('invalid motor runtime config path')
            unresolved_runtime = project_dir / requested
            unresolved_sessions = project_dir / 'runtime' / 'sessions'
            if unresolved_runtime.is_symlink() or unresolved_sessions.is_symlink():
                raise ValueError('motor runtime session must not be a symbolic link')
            runtime = unresolved_runtime.resolve()
            sessions = unresolved_sessions.resolve()
            try:
                runtime.relative_to(sessions)
            except ValueError as exc:
                raise ValueError('motor runtime session is outside project runtime') from exc
            return runtime
        # Compatibility for runtime records created before immutable sessions.
        return project_dir / 'runtime' / 'applied_motor_config.yaml'

    def applied_runtime_motor_config(self) -> Optional[Path]:
        """Resolve the exact applied target independently from editor selection."""
        state = self.motor_runtime_state()
        if state.get('valid') is not True:
            return None
        return Path(str(state['config_file']))

    def selected_runtime_motor_config(self) -> Optional[Path]:
        """Return the runtime only when it belongs to the selected project."""
        state = self.motor_runtime_state()
        if (
            state.get('valid') is not True
            or state.get('target_project_id') != self.repository.selected_project_id()
        ):
            return None
        return Path(str(state['config_file']))
