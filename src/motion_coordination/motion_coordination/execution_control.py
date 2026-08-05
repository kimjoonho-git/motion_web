"""Project-neutral execution ownership and synchronized schedule contracts."""

from __future__ import annotations

import json
import math
import os
import tempfile
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping


CONTROL_COMMANDS = {
    'run_once', 'stop_motion', 'initialize', 'stop_initialize',
    'acquire_control', 'release_control', 'start_at',
    'stop_after_cycle', 'stop_now', 'cancel_before_start',
}


def validate_control_payload(value: Mapping[str, Any]) -> Dict[str, Any]:
    """Validate the small, project-neutral network command payload."""
    if not isinstance(value, Mapping):
        raise ValueError('실행 명령 payload가 올바르지 않습니다')
    allowed = {
        'network_operation_id', 'command', 'lease_id', 'expires_at',
        'start_at', 'cycle_sec', 'repeat_count', 'hold_final',
    }
    unknown = set(value).difference(allowed)
    if unknown:
        raise ValueError('허용되지 않은 실행 명령 항목이 있습니다')
    operation_id = str(value.get('network_operation_id') or '').strip()
    if not operation_id or len(operation_id) > 160:
        raise ValueError('network_operation_id가 필요합니다')
    command = str(value.get('command') or '').strip()
    if command not in CONTROL_COMMANDS:
        raise ValueError('지원하지 않는 연동 실행 명령입니다')
    result: Dict[str, Any] = {
        'network_operation_id': operation_id,
        'command': command,
    }
    lease_id = str(value.get('lease_id') or '').strip()
    if lease_id:
        if len(lease_id) > 160:
            raise ValueError('lease_id가 너무 깁니다')
        result['lease_id'] = lease_id
    for field in ('expires_at', 'start_at', 'cycle_sec'):
        if value.get(field) is not None:
            number = _finite(value[field], field)
            if number <= 0:
                raise ValueError(f'{field}는 양수여야 합니다')
            result[field] = number
    if value.get('repeat_count') is not None:
        count = value['repeat_count']
        if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= 10000:
            raise ValueError('repeat_count는 1~10000 정수여야 합니다')
        result['repeat_count'] = count
    if value.get('hold_final') is not None:
        if not isinstance(value['hold_final'], bool):
            raise ValueError('hold_final은 bool이어야 합니다')
        result['hold_final'] = value['hold_final']
    if command == 'start_at':
        for field in ('lease_id', 'start_at', 'cycle_sec', 'repeat_count'):
            if field not in result:
                raise ValueError(f'start_at 명령에는 {field}가 필요합니다')
    if command in {
        'run_once', 'initialize', 'release_control', 'stop_after_cycle',
    } and not lease_id:
        raise ValueError(f'{command} 명령에는 lease_id가 필요합니다')
    return result


class ExecutionLease:
    """One non-persistent upper motion-execution lease."""

    def __init__(self, *, clock=time.time) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._value: Dict[str, Any] = {}

    def acquire(
        self, owner: str, *, duration_sec: float = 5.0, lease_id: str = ''
    ) -> Dict[str, Any]:
        now = self._clock()
        with self._lock:
            self._expire(now)
            if self._value and self._value['owner'] != owner:
                raise ValueError('다른 중앙 PC가 모션 실행 제어권을 보유 중입니다')
            if (
                self._value and lease_id
                and self._value.get('lease_id') != lease_id
            ):
                raise ValueError('이미 다른 lease ID의 모션 실행 제어권이 활성 상태입니다')
            selected_id = self._value.get('lease_id') or lease_id or f'lease-{uuid.uuid4().hex}'
            self._value = {
                'state': 'network', 'owner': owner, 'lease_id': selected_id,
                'expires_at': now + max(float(duration_sec), 1.0),
            }
            return dict(self._value)

    def renew(self, owner: str, lease_id: str, *, duration_sec: float = 5.0) -> Dict[str, Any]:
        now = self._clock()
        with self._lock:
            self._expire(now)
            if not self._matches(owner, lease_id):
                raise ValueError('유효한 네트워크 모션 실행 제어권이 아닙니다')
            self._value['expires_at'] = now + max(float(duration_sec), 1.0)
            return dict(self._value)

    def release(self, owner: str, lease_id: str) -> Dict[str, Any]:
        with self._lock:
            self._expire(self._clock())
            if self._value and not self._matches(owner, lease_id):
                raise ValueError('반환하려는 모션 실행 제어권이 일치하지 않습니다')
            self._value = {}
            return self.snapshot()

    def require(self, owner: str, lease_id: str) -> None:
        with self._lock:
            self._expire(self._clock())
            if not self._matches(owner, lease_id):
                raise ValueError('네트워크 모션 실행 제어권이 만료되었거나 일치하지 않습니다')

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            self._expire(self._clock())
            return dict(self._value) if self._value else {'state': 'local'}

    def _matches(self, owner: str, lease_id: str) -> bool:
        return (
            bool(self._value)
            and self._value['owner'] == owner
            and self._value['lease_id'] == lease_id
        )

    def _expire(self, now: float) -> None:
        if self._value and float(self._value.get('expires_at') or 0) <= now:
            self._value = {}


class OperationJournal:
    """Bounded persistent idempotency journal for accepted control operations."""

    def __init__(self, path: Path, *, max_entries: int = 4096) -> None:
        self.path = Path(path)
        self.max_entries = max_entries
        self._lock = threading.Lock()

    def begin(self, sender: str, operation_id: str, command: str) -> None:
        key = f'{sender}:{operation_id}'
        with self._lock:
            data = self._read()
            if key in data:
                raise ValueError('이미 처리한 network_operation_id입니다')
            data[key] = {'command': command, 'state': 'accepted', 'updated_at': time.time()}
            self._write(self._prune(data))

    def finish(self, sender: str, operation_id: str, result: Mapping[str, Any]) -> None:
        key = f'{sender}:{operation_id}'
        with self._lock:
            data = self._read()
            if key not in data:
                return
            data[key].update({
                'state': 'completed' if result.get('success') else 'failed',
                'updated_at': time.time(),
            })
            self._write(self._prune(data))

    def _read(self) -> Dict[str, Dict[str, Any]]:
        try:
            value = json.loads(self.path.read_text(encoding='utf-8'))
        except FileNotFoundError:
            return {}
        except OSError as exc:
            raise ValueError(f'실행 이력 파일을 읽을 수 없습니다: {exc}') from exc
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError('실행 이력 파일이 손상되었습니다') from exc
        if not isinstance(value, dict):
            raise ValueError('실행 이력 파일 형식이 올바르지 않습니다')
        return value

    def _write(self, value: Mapping[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode='w', encoding='utf-8', dir=self.path.parent,
                prefix=f'.{self.path.name}.', suffix='.tmp', delete=False,
            ) as temporary:
                json.dump(value, temporary, ensure_ascii=False, separators=(',', ':'))
                temporary_path = Path(temporary.name)
            os.replace(temporary_path, self.path)
            temporary_path = None
            self.path.chmod(0o600)
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)

    def _prune(self, value: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        if len(value) <= self.max_entries:
            return value
        rows = sorted(value.items(), key=lambda row: float(row[1].get('updated_at') or 0))
        return dict(rows[-self.max_entries:])


def build_synchronized_schedule(
    durations: Iterable[float], *, start_at: float, dwell_sec: float = 0.0,
    repeat_count: int = 1, period_sec: float = 0.02,
) -> Dict[str, Any]:
    """Build absolute cycle boundaries without accumulating timer drift."""
    values = [_finite(value, 'duration') for value in durations]
    if not values or any(value <= 0 for value in values):
        raise ValueError('참여 PC의 모션 시간이 필요합니다')
    if repeat_count < 1 or repeat_count > 10000:
        raise ValueError('repeat_count는 1~10000이어야 합니다')
    dwell = max(_finite(dwell_sec, 'dwell_sec'), 0.0)
    longest = max(values)
    cycle = math.ceil((longest + dwell) / period_sec) * period_sec
    starts = [float(start_at) + (index * cycle) for index in range(repeat_count)]
    return {
        'start_at': float(start_at), 'longest_motion_sec': longest,
        'cycle_sec': cycle, 'repeat_count': repeat_count, 'cycle_starts': starts,
    }


def start_error_ms(requested_start_at: float, actual_start_at: float) -> float:
    return round((float(actual_start_at) - float(requested_start_at)) * 1000.0, 3)


def bounded_parallel_map(function, values, *, max_workers: int = 16):
    """Execute peer I/O with a fixed fan-out bound and stable input ordering."""
    rows = list(values)
    if not rows:
        return []
    with ThreadPoolExecutor(max_workers=min(max_workers, len(rows))) as pool:
        return list(pool.map(function, rows))


def _finite(value: Any, field: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f'{field} 값이 올바르지 않습니다') from exc
    if not math.isfinite(number):
        raise ValueError(f'{field} 값이 올바르지 않습니다')
    return number
