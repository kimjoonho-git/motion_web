"""모터 동작 로그 · 기록 · 보존 · 조회 · 단일 소유자.

`MotionWebBridge`가 흩어 갖고 있던 이벤트 로그 상태와 메서드를 모았다.
`docs/ARCHITECTURE_REVIEW.md` §5 분해 목표안의 `MotorEventLog`다 · §6-17

노드에서 받는 것은 협력자뿐이다 · 저장소 · 경로 · 세대 판정 콜러블 · 로거.
락은 이 객체가 갖는다. `append`가 `prune`을 락 안에서 다시 부르므로 `RLock`이다.

전이 기록 두 개(`record_motor_error_transitions` · `record_motion_run_transition`)도
여기 있다. 이전 상태와 비교해 새 이벤트만 남기는 판정이 같은 락 아래에서
일어나야 중복 기록이 생기지 않는다.
"""

from __future__ import annotations

import json
import re
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from motion_common.values import optional_int


def _event_log_paths(log_dir: Path) -> List[Path]:
    return sorted(
        path for path in log_dir.glob('*.jsonl')
        if path.is_file() and not path.is_symlink()
    )


def _event_log_lines(path: Path) -> List[str]:
    try:
        return [
            line
            for line in path.read_text(encoding='utf-8').splitlines()
            if line.strip()
        ]
    except OSError:
        return []


class MotorEventLog:
    def __init__(
        self,
        *,
        log_dir: Path,
        retention_days: int,
        max_bytes: int,
        max_records: int,
        max_files: int,
        repository: Any,
        workspace_root: Path,
        runtime_project_id: Callable[[], str],
        logger: Callable[[], Any],
    ) -> None:
        self.log_dir = log_dir
        self.retention_days = retention_days
        self.max_bytes = max_bytes
        self.max_records = max_records
        self.max_files = max_files
        self.repository = repository
        self.workspace_root = workspace_root
        self.runtime_project_id = runtime_project_id
        self.logger = logger
        #: `append`가 락 안에서 `prune`을 다시 부른다 · 재진입 필요
        self._lock = threading.RLock()
        self._active_motor_errors: Dict[str, str] = {}
        self._last_motion_run_state: Optional[str] = None

    def clear_project_memory(self) -> None:
        """프로젝트가 바뀌면 전이 판정 기준을 지운다."""
        with self._lock:
            self._active_motor_errors = {}
            self._last_motion_run_state = None

    def record_motor_error_transitions(self, payload: Dict[str, Any]) -> None:
        motors = payload.get('motors')
        if not isinstance(motors, list):
            return

        current_errors: Dict[str, str] = {}
        new_events: List[Dict[str, Any]] = []
        with self._lock:
            previous_errors = dict(self._active_motor_errors)
            for motor in motors:
                if not isinstance(motor, dict):
                    continue
                axis = optional_int(motor.get('controller_index'), None)
                if axis is None:
                    continue
                errorcode = optional_int(motor.get('errorcode'), 0) or 0
                statusword = optional_int(motor.get('statusword'), 0) or 0
                fault = bool(motor.get('fault')) or errorcode != 0 or bool(statusword & 0x0008)
                if not fault:
                    continue

                error_hex = str(motor.get('errorcode_hex') or f'0x{errorcode & 0xFFFF:04X}')
                error_text = str(
                    motor.get('error_text')
                    or motor.get('status_text')
                    or '모터 오류 상태'
                )
                signature = f'{error_hex}|{statusword & 0x0008}|{error_text}'
                axis_key = str(axis)
                current_errors[axis_key] = signature
                if previous_errors.get(axis_key) == signature:
                    continue

                name = str(motor.get('display_name') or f'Axis {axis}')
                new_events.append({
                    'category': 'error',
                    'event_type': 'motor_error',
                    'target': f'Axis {axis} · {name}',
                    'content': f'{error_hex} {error_text}',
                    'details': {
                        'axis': axis,
                        'name': name,
                        'motor_type': str(motor.get('motor_type_label') or motor.get('motor_type') or ''),
                        'errorcode': errorcode,
                        'errorcode_hex': error_hex,
                        'error_text': error_text,
                        'statusword': statusword,
                    },
                })
            self._active_motor_errors = current_errors

        for event in new_events:
            self.append(**event)

    def record_motion_run_transition(self, status: Dict[str, Any]) -> None:
        state = str(status.get('state') or 'idle')
        with self._lock:
            previous_state = self._last_motion_run_state
            self._last_motion_run_state = state
        if previous_state is None or previous_state == state:
            return

        motion_file = str(status.get('motion_file_id') or '-')
        mapping_file = str(status.get('mapping_file_id') or '-')
        axes = status.get('axes') if isinstance(status.get('axes'), list) else []
        target = f'{motion_file} · {len(axes)}축'
        details = {
            'previous_state': previous_state,
            'state': state,
            'motion_file_id': motion_file,
            'mapping_file_id': mapping_file,
            'axis_count': len(axes),
            'run_mode': str(status.get('run_mode') or 'once'),
        }

        if state == 'initializing' and previous_state != 'initializing':
            self.append(
                category='initial_position',
                event_type='initial_position_started',
                target=target,
                content=f'초기 위치 이동 시작 · 매핑 {mapping_file}',
                details=details,
            )

        if previous_state == 'initializing' and state != 'initializing':
            completed = state in {'initialized', 'ready', 'running'}
            self.append(
                category='initial_position',
                event_type='initial_position_completed' if completed else 'initial_position_stopped',
                target=target,
                content='초기 위치 이동 완료' if completed else f'초기 위치 이동 종료 · 상태 {state}',
                details=details,
            )

        if state == 'running' and previous_state != 'running':
            continuous = details['run_mode'] == 'continuous'
            motion_label = '연속 모션' if continuous else '1회 모션'
            self.append(
                category='motion',
                event_type='continuous_motion_started' if continuous else 'single_motion_started',
                target=target,
                content=f'{motion_label} 시작 · 매핑 {mapping_file}',
                details=details,
            )

    def append(
        self,
        category: str,
        event_type: str,
        target: str,
        content: str,
        details: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        now = datetime.now().astimezone()
        record = {
            'id': f'{time.time_ns()}',
            'timestamp': now.timestamp(),
            'timestamp_text': now.isoformat(timespec='milliseconds'),
            'category': str(category),
            'event_type': str(event_type),
            'target': str(target),
            'content': str(content),
            'details': details if isinstance(details, dict) else {},
        }
        log_dir, project_id, project_name = self.context(for_write=True)
        if project_id:
            record['project_id'] = project_id
            record['project_name'] = project_name
        path = log_dir / f'{now:%Y-%m-%d}.jsonl'
        try:
            with self._lock:
                log_dir.mkdir(parents=True, exist_ok=True)
                with path.open('a', encoding='utf-8') as stream:
                    stream.write(json.dumps(record, ensure_ascii=False, separators=(',', ':')))
                    stream.write('\n')
                self.prune(log_dir)
        except OSError as error:
            self.logger().error(f'Failed to write motor event log {path}: {error}')
        return record

    def context(self, for_write: bool = False) -> tuple[Path, str, str]:
        configured_fallback = self.log_dir
        workspace_root = self.workspace_root
        fallback = Path(configured_fallback or workspace_root / 'log' / 'motor_events')
        repository = self.repository
        if repository is None:
            return fallback, '', ''

        project_id = ''
        if for_write:
            try:
                project_id = str(self.runtime_project_id() or '')
            except (AttributeError, OSError, ValueError):
                project_id = ''
        if not project_id:
            try:
                project_id = str(repository.selected_project_id() or '')
            except (AttributeError, OSError, ValueError):
                project_id = ''
        if not project_id:
            return fallback, '', ''
        try:
            project = repository.get_project(project_id).get('project') or {}
            return repository.project_logs_dir(project_id), project_id, str(project.get('name') or project_id)
        except (AttributeError, OSError, ValueError):
            return fallback, '', ''


    def prune(self, log_dir: Optional[Path] = None) -> None:
        target_dir = Path(log_dir or self.log_dir)
        cutoff = datetime.now().astimezone().date() - timedelta(
            days=self.retention_days - 1
        )
        with self._lock:
            paths = _event_log_paths(target_dir)
            for path in list(paths):
                try:
                    file_date = datetime.strptime(path.stem, '%Y-%m-%d').date()
                except ValueError:
                    continue
                if file_date < cutoff:
                    try:
                        path.unlink()
                    except OSError:
                        continue
                    paths.remove(path)

            while len(paths) > self.max_files:
                oldest = paths.pop(0)
                try:
                    oldest.unlink()
                except OSError:
                    pass

            sizes: Dict[Path, int] = {}
            for path in paths:
                try:
                    sizes[path] = path.stat().st_size
                except OSError:
                    sizes[path] = 0
            total_bytes = sum(sizes.values())
            while total_bytes > self.max_bytes and len(paths) > 1:
                oldest = paths.pop(0)
                try:
                    oldest.unlink()
                except OSError:
                    continue
                total_bytes -= sizes.get(oldest, 0)

            line_counts = {path: len(_event_log_lines(path)) for path in paths}
            total_records = sum(line_counts.values())
            while total_records > self.max_records and len(paths) > 1:
                oldest = paths.pop(0)
                try:
                    oldest.unlink()
                except OSError:
                    continue
                total_records -= line_counts.get(oldest, 0)

            if paths:
                newest = paths[-1]
                lines = _event_log_lines(newest)
                if len(lines) > self.max_records:
                    lines = lines[-self.max_records:]
                encoded_lines = [(line + '\n').encode('utf-8') for line in lines]
                encoded_size = sum(len(line) for line in encoded_lines)
                while encoded_lines and encoded_size > self.max_bytes:
                    encoded_size -= len(encoded_lines.pop(0))
                try:
                    newest.write_bytes(b''.join(encoded_lines))
                except OSError:
                    pass

    def clear(self) -> Dict[str, Any]:
        log_dir, project_id, project_name = self.context()
        deleted_files = 0
        deleted_bytes = 0
        with self._lock:
            for path in _event_log_paths(log_dir):
                try:
                    deleted_bytes += path.stat().st_size
                    path.unlink()
                    deleted_files += 1
                except OSError:
                    continue
        return {
            'success': True,
            'message': '현재 프로젝트의 모터 동작 로그를 삭제했습니다.',
            'deleted_files': deleted_files,
            'deleted_bytes': deleted_bytes,
            'project_id': project_id,
            'project_name': project_name,
        }

    def delete_file(self, file_name: Any) -> Dict[str, Any]:
        name = str(file_name or '').strip()
        if name != Path(name).name or not re.fullmatch(r'\d{4}-\d{2}-\d{2}\.jsonl', name):
            raise ValueError('올바르지 않은 로그 파일명입니다')
        log_dir, project_id, project_name = self.context()
        path = log_dir / name
        if not path.is_file() or path.is_symlink():
            raise ValueError(f'로그 파일을 찾을 수 없습니다: {name}')
        with self._lock:
            size = path.stat().st_size
            path.unlink()
        return {
            'success': True,
            'message': f'{name} 로그 파일을 삭제했습니다.',
            'deleted_file': name,
            'deleted_bytes': size,
            'project_id': project_id,
            'project_name': project_name,
        }

    def events(
        self, limit: int = 200, category: str = 'all', file_name: str = 'all'
    ) -> Dict[str, Any]:
        safe_limit = max(1, min(int(limit), 1000))
        category_filter = str(category or 'all')
        file_filter = str(file_name or 'all')
        log_dir, project_id, project_name = self.context()
        events: List[Dict[str, Any]] = []
        file_rows: List[Dict[str, Any]] = []
        with self._lock:
            paths = list(reversed(_event_log_paths(log_dir)))
            for path in paths:
                lines = _event_log_lines(path)
                try:
                    size = path.stat().st_size
                except OSError:
                    size = 0
                file_rows.append({
                    'name': path.name,
                    'size': size,
                    'record_count': len(lines),
                })
            if file_filter != 'all':
                paths = [path for path in paths if path.name == file_filter]
            for path in paths:
                try:
                    lines = path.read_text(encoding='utf-8').splitlines()
                except OSError:
                    continue
                for line in reversed(lines):
                    try:
                        event = json.loads(line)
                    except (json.JSONDecodeError, TypeError):
                        continue
                    if not isinstance(event, dict):
                        continue
                    if category_filter != 'all' and event.get('category') != category_filter:
                        continue
                    events.append(event)
                    if len(events) >= safe_limit:
                        break
                if len(events) >= safe_limit:
                    break
        return {
            'success': True,
            'category': category_filter,
            'file_name': file_filter,
            'count': len(events),
            'events': events,
            'files': file_rows,
            'retention_days': self.retention_days,
            'max_bytes': self.max_bytes,
            'max_records': self.max_records,
            'max_files': self.max_files,
            'project_id': project_id,
            'project_name': project_name,
        }
