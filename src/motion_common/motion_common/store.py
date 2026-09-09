"""파일 기록 단일 구현 · atomic write + 파일락.

프로젝트 디렉터리에 직접 기록하는 모듈이 여럿이고 각자 atomic write를 구현하면
보장 수준이 갈라진다. 실제로 갈라져 있던 지점:

- 임시파일 이름 · 고정(`<name>.tmp`) ↔ `mkstemp` 무작위
  고정 이름은 두 프로세스가 같은 대상을 쓸 때 서로의 임시파일을 덮어쓴다.
- `fsync` · 있는 구현과 없는 구현
  없으면 전원 차단 시 rename은 반영됐는데 내용이 비어 있을 수 있다.
- 실패 시 임시파일 정리 · 하는 구현과 남기는 구현

이 모듈은 가장 강한 쪽으로 통일한다 · 같은 디렉터리에 `mkstemp` → 기록 → `fsync`
→ `os.replace` → 실패 시 정리.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, Optional, Union

__all__ = [
    'LOCK_SUFFIX',
    'atomic_write_text',
    'atomic_write_json',
    'atomic_write_yaml',
    'file_lock',
    'locked_update',
    'lock_path_for',
    'read_json',
    'read_text',
    'update_json',
]

#: 프로세스 간 락은 POSIX 전용이다. Windows에서는 잠금 없이 진행하며,
#: 기록 자체는 원자적이므로 읽는 쪽이 깨진 내용을 보는 일은 없다.
#: 실제 운용은 Linux이고 Windows는 코드 편집·단위 테스트 용도다.
try:
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None

LOCK_SUFFIX = '.lock'

PathLike = Union[str, Path]


# --------------------------------------------------------------------------- #
# 기록
# --------------------------------------------------------------------------- #

def atomic_write_text(
    path: PathLike,
    content: str,
    *,
    encoding: str = 'utf-8',
    fsync: bool = True,
    mode: Optional[int] = None,
    max_bytes: Optional[int] = None,
    max_bytes_message: str = 'content exceeds the allowed size',
) -> None:
    """원자적으로 텍스트를 기록한다.

    같은 디렉터리에 임시파일을 만들어 기록한 뒤 ``os.replace``로 교체한다.
    교체는 같은 파일시스템 안에서 원자적이므로, 읽는 쪽은 항상 이전 내용이나
    새 내용 중 하나를 온전히 본다.

    ``max_bytes``를 주면 교체 전에 크기를 확인하고 초과 시 ``ValueError``를
    올린다 · 대상 파일은 건드리지 않는다.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)

    handle, temporary_name = tempfile.mkstemp(
        prefix=f'.{target.name}.',
        suffix='.tmp',
        dir=str(target.parent),
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(handle, 'w', encoding=encoding) as stream:
            stream.write(content)
            stream.flush()
            if fsync:
                os.fsync(stream.fileno())

        if max_bytes is not None and temporary.stat().st_size > max_bytes:
            raise ValueError(max_bytes_message)

        if mode is not None:
            os.chmod(temporary, mode)

        os.replace(temporary, target)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def atomic_write_json(
    path: PathLike,
    payload: Any,
    *,
    indent: int = 2,
    ensure_ascii: bool = False,
    mode: Optional[int] = None,
    fsync: bool = True,
    newline_at_end: bool = True,
) -> None:
    """원자적으로 JSON을 기록한다."""
    text = json.dumps(payload, indent=indent, ensure_ascii=ensure_ascii)
    if newline_at_end:
        text += '\n'
    atomic_write_text(path, text, mode=mode, fsync=fsync)


def atomic_write_yaml(path: PathLike, payload: Any, *, mode: Optional[int] = None) -> None:
    """원자적으로 YAML을 기록한다."""
    import yaml

    text = yaml.safe_dump(payload, allow_unicode=True, sort_keys=False)
    atomic_write_text(path, text, mode=mode)


# --------------------------------------------------------------------------- #
# 읽기
# --------------------------------------------------------------------------- #

def read_text(path: PathLike, default: Optional[str] = None) -> Optional[str]:
    """텍스트를 읽는다. 파일이 없거나 읽지 못하면 ``default``."""
    try:
        return Path(path).read_text(encoding='utf-8')
    except (OSError, UnicodeDecodeError):
        return default


def read_json(path: PathLike, default: Any = None) -> Any:
    """JSON을 읽는다. 파일이 없거나 해석하지 못하면 ``default``."""
    text = read_text(path)
    if text is None:
        return default
    try:
        return json.loads(text)
    except ValueError:
        return default


# --------------------------------------------------------------------------- #
# 파일락
# --------------------------------------------------------------------------- #

def lock_path_for(path: PathLike) -> Path:
    """대상 파일에 대응하는 락 파일 경로 · ``.<이름>.lock``.

    **숨김 이름을 쓴다.** 락 파일은 프로젝트 데이터 디렉터리 안에 생기는데,
    화면의 파일 목록과 해시 계산이 그것을 사용자 파일로 세면 안 된다 · §6-24
    목록 쪽은 이미 `.`으로 시작하는 이름을 거른다.

    두 프로세스가 같은 함수로 경로를 얻으므로 규약은 저절로 맞는다.
    """
    target = Path(path)
    return target.parent / f'.{target.name}{LOCK_SUFFIX}'


#: 같은 프로세스 안에서 경로마다 하나씩 두는 재진입 락.
#: `flock`은 **파일 서술자 단위**라 같은 프로세스가 다른 서술자로 다시 잠그면
#: 자기 자신을 기다리며 멈춘다. 프로세스 안쪽은 이 락이 막고, 프로세스 사이는
#: `flock`이 막는다.
_process_locks: Dict[str, threading.RLock] = {}
_process_locks_guard = threading.Lock()
#: 이 스레드가 이미 `flock`을 잡고 있는 경로 · 중첩 호출을 알아보기 위한 표시
_thread_state = threading.local()


def _process_lock_for(key: str) -> threading.RLock:
    with _process_locks_guard:
        lock = _process_locks.get(key)
        if lock is None:
            lock = threading.RLock()
            _process_locks[key] = lock
        return lock


def _flocked_paths() -> Dict[str, int]:
    held = getattr(_thread_state, 'held', None)
    if held is None:
        held = {}
        _thread_state.held = held
    return held


@contextmanager
def file_lock(path: PathLike, *, exclusive: bool = True) -> Iterator[None]:
    """대상 파일에 대한 프로세스 간 락을 잡는다.

    잠금 대상은 대상 파일 자체가 아니라 옆에 둔 ``<이름>.lock``이다. 대상 파일은
    ``os.replace``로 교체되므로 inode가 바뀌어 직접 잠그면 락이 풀린다.

    **재진입 가능하다.** 같은 스레드가 같은 경로를 다시 잠그면 `flock`을 다시
    걸지 않고 그대로 진행한다. `flock`은 서술자 단위라 다시 걸면 자기 자신을
    기다리며 멈추기 때문이다 · 저장 API가 서로를 감싸는 구조에서 실제로 걸린다.

    락 파일을 만들지 못하는 환경(읽기 전용 디렉터리 등)에서는 잠금 없이 진행한다 ·
    기록 자체는 원자적이므로 읽는 쪽이 깨진 내용을 보는 일은 없다.
    """
    key = str(Path(path).resolve() if Path(path).parent.exists() else Path(path))
    process_lock = _process_lock_for(key)
    with process_lock:
        held = _flocked_paths()
        if fcntl is None or held.get(key):
            # 잠금 불가 환경이거나 이미 이 스레드가 잡고 있다 · 중첩 진입
            if fcntl is not None:
                held[key] = held.get(key, 0) + 1
            try:
                yield
            finally:
                if fcntl is not None:
                    held[key] -= 1
                    if not held[key]:
                        held.pop(key, None)
            return

        lock_file = lock_path_for(path)
        try:
            lock_file.parent.mkdir(parents=True, exist_ok=True)
            handle = lock_file.open('a+', encoding='utf-8')
        except OSError:
            yield
            return

        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
            held[key] = 1
            try:
                yield
            finally:
                held.pop(key, None)
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


@contextmanager
def locked_update(path: PathLike) -> Iterator[None]:
    """읽기-수정-기록 구간 전체를 배타 락으로 감싼다.

    두 프로세스가 각자 읽고 각자 기록하면 나중 기록이 앞선 수정을 지운다.
    갱신 구간 전체를 감싸야 그 경합이 사라진다.
    """
    with file_lock(path, exclusive=True):
        yield


def update_json(
    path: PathLike,
    mutate: Callable[[Any], Any],
    *,
    default: Any = None,
    indent: int = 2,
) -> Any:
    """JSON을 락 안에서 읽고 ``mutate``를 적용한 뒤 원자적으로 기록한다."""
    with locked_update(path):
        current = read_json(path, default)
        updated = mutate(current)
        atomic_write_json(path, updated, indent=indent)
        return updated
