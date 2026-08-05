"""Crash-recoverable, process-safe writes for coordination config pairs."""

import base64
import binascii
import fcntl
import json
import os
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Mapping


_LOCKS = {}
_LOCKS_GUARD = threading.Lock()


@contextmanager
def configuration_guard(path: Path):
    """Serialize writes and recover an interrupted two-file commit."""
    path = Path(path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    key = str(path.resolve())
    with _LOCKS_GUARD:
        thread_lock = _LOCKS.setdefault(key, threading.RLock())
    lock_path = path.with_name(f'.{path.name}.lock')
    with thread_lock, lock_path.open('a+b') as lock_file:
        lock_path.chmod(0o600)
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        _recover(path)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def commit_config_pair(
    config_path: Path,
    config_temporary: Path,
    credential_path: Path,
    credential_temporary: Path,
) -> None:
    """Commit config and credentials together or restore both previous files."""
    journal_path = _transaction_path(config_path)
    journal = {
        'version': 1,
        'phase': 'prepared',
        'files': [_snapshot(config_path), _snapshot(credential_path)],
    }
    _write_transaction(journal_path, journal)
    try:
        credential_path.parent.mkdir(parents=True, exist_ok=True)
        os.replace(credential_temporary, credential_path)
        credential_path.chmod(0o600)
        os.replace(config_temporary, config_path)
        config_path.chmod(0o600)
        journal['phase'] = 'committed'
        _write_transaction(journal_path, journal)
        journal_path.unlink(missing_ok=True)
    except Exception:
        _restore(journal)
        journal_path.unlink(missing_ok=True)
        raise


def _recover(config_path: Path) -> None:
    journal_path = _transaction_path(config_path)
    if not journal_path.is_file():
        return
    try:
        journal = json.loads(journal_path.read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise ValueError('중단된 PC 연동 설정 저장 기록을 읽을 수 없습니다') from exc
    if not isinstance(journal, Mapping) or journal.get('version') != 1:
        raise ValueError('PC 연동 설정 저장 기록 형식이 올바르지 않습니다')
    if journal.get('phase') == 'prepared':
        _restore(journal)
    elif journal.get('phase') != 'committed':
        raise ValueError('PC 연동 설정 저장 단계가 올바르지 않습니다')
    journal_path.unlink(missing_ok=True)


def _transaction_path(config_path: Path) -> Path:
    return config_path.with_name(f'.{config_path.name}.transaction.json')


def _snapshot(path: Path) -> Mapping[str, Any]:
    path = Path(path).expanduser()
    exists = path.is_file()
    return {
        'path': str(path),
        'exists': exists,
        'mode': (path.stat().st_mode & 0o777) if exists else 0o600,
        'content_base64': base64.b64encode(path.read_bytes()).decode('ascii')
        if exists else '',
    }


def _restore(journal: Mapping[str, Any]) -> None:
    files = journal.get('files')
    if not isinstance(files, list):
        raise ValueError('PC 연동 설정 복구 대상이 올바르지 않습니다')
    for snapshot in files:
        if not isinstance(snapshot, Mapping) or not snapshot.get('path'):
            raise ValueError('PC 연동 설정 복구 항목이 올바르지 않습니다')
        path = Path(str(snapshot['path'])).expanduser()
        if snapshot.get('exists') is True:
            try:
                content = base64.b64decode(
                    str(snapshot.get('content_base64') or ''), validate=True
                )
            except (ValueError, binascii.Error) as exc:
                raise ValueError('PC 연동 설정 복구 데이터가 손상됐습니다') from exc
            temporary = _write_bytes_temporary(path, content)
            os.replace(temporary, path)
            path.chmod(int(snapshot.get('mode') or 0o600) & 0o777)
        else:
            path.unlink(missing_ok=True)


def _write_transaction(path: Path, value: Mapping[str, Any]) -> None:
    content = json.dumps(
        dict(value), ensure_ascii=False, sort_keys=True, separators=(',', ':')
    ).encode('utf-8')
    temporary = _write_bytes_temporary(path, content)
    os.replace(temporary, path)
    path.chmod(0o600)


def _write_bytes_temporary(path: Path, content: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(
        dir=path.parent, prefix=f'.{path.name}.', suffix='.tmp'
    )
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        result = Path(name)
        result.chmod(0o600)
        return result
    except Exception:
        Path(name).unlink(missing_ok=True)
        raise
