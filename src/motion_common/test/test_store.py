"""파일 기록 단일 구현 검증 · atomic write + 파일락."""

import json
import multiprocessing
import os
from pathlib import Path

import pytest

from motion_common import store


# --------------------------------------------------------------------------- #
# atomic_write_text
# --------------------------------------------------------------------------- #

def test_writes_content_and_creates_parent_dirs(tmp_path):
    target = tmp_path / 'nested' / 'deep' / 'file.txt'
    store.atomic_write_text(target, '내용')
    assert target.read_text(encoding='utf-8') == '내용'


def test_overwrites_existing_content(tmp_path):
    target = tmp_path / 'f.txt'
    target.write_text('old', encoding='utf-8')
    store.atomic_write_text(target, 'new')
    assert target.read_text(encoding='utf-8') == 'new'


def test_leaves_no_temporary_files_behind(tmp_path):
    target = tmp_path / 'f.txt'
    store.atomic_write_text(target, 'x')
    assert [p.name for p in tmp_path.iterdir()] == ['f.txt']


def test_uses_unique_temporary_names(tmp_path, monkeypatch):
    """고정 임시파일명은 두 기록이 서로를 덮어쓴다 · mkstemp로 회피한다."""
    seen = []
    real_mkstemp = store.tempfile.mkstemp

    def spy(*args, **kwargs):
        result = real_mkstemp(*args, **kwargs)
        seen.append(result[1])
        return result

    monkeypatch.setattr(store.tempfile, 'mkstemp', spy)
    store.atomic_write_text(tmp_path / 'f.txt', 'a')
    store.atomic_write_text(tmp_path / 'f.txt', 'b')
    assert len(seen) == 2
    assert seen[0] != seen[1]


def test_max_bytes_rejects_and_preserves_target(tmp_path):
    target = tmp_path / 'f.txt'
    target.write_text('원본', encoding='utf-8')
    with pytest.raises(ValueError, match='too big'):
        store.atomic_write_text(target, 'x' * 100, max_bytes=10, max_bytes_message='too big')
    # 대상은 그대로 · 임시파일도 남지 않는다
    assert target.read_text(encoding='utf-8') == '원본'
    assert [p.name for p in tmp_path.iterdir()] == ['f.txt']


def test_max_bytes_allows_content_at_the_limit(tmp_path):
    target = tmp_path / 'f.txt'
    store.atomic_write_text(target, 'x' * 10, max_bytes=10)
    assert target.read_text(encoding='utf-8') == 'x' * 10


def test_applies_requested_mode(tmp_path):
    target = tmp_path / 'secret.yaml'
    store.atomic_write_text(target, 'a: 1', mode=0o600)
    assert oct(target.stat().st_mode & 0o777) == '0o600'


def test_cleans_up_when_serialization_fails(tmp_path):
    target = tmp_path / 'f.json'
    with pytest.raises(TypeError):
        store.atomic_write_json(target, {'bad': object()})
    assert not target.exists()
    assert list(tmp_path.iterdir()) == []


# --------------------------------------------------------------------------- #
# JSON / YAML
# --------------------------------------------------------------------------- #

def test_json_round_trip_preserves_unicode(tmp_path):
    target = tmp_path / 'f.json'
    payload = [{'name': '연동 스케줄', 'enabled': True}]
    store.atomic_write_json(target, payload)
    assert '연동' in target.read_text(encoding='utf-8')
    assert store.read_json(target) == payload


def test_yaml_round_trip(tmp_path):
    yaml = pytest.importorskip('yaml')
    target = tmp_path / 'f.yaml'
    store.atomic_write_yaml(target, {'is_master': True, 'pc_id': '가나'})
    assert yaml.safe_load(target.read_text(encoding='utf-8')) == {
        'is_master': True, 'pc_id': '가나'
    }


def test_read_helpers_fall_back_on_missing_or_broken(tmp_path):
    assert store.read_text(tmp_path / 'absent') is None
    assert store.read_json(tmp_path / 'absent', default=[]) == []
    broken = tmp_path / 'broken.json'
    broken.write_text('{not json', encoding='utf-8')
    assert store.read_json(broken, default={'fallback': True}) == {'fallback': True}


def test_read_json_distinguishes_null_from_missing(tmp_path):
    target = tmp_path / 'null.json'
    target.write_text('null', encoding='utf-8')
    assert store.read_json(target, default='D') is None


# --------------------------------------------------------------------------- #
# 파일락
# --------------------------------------------------------------------------- #

def test_lock_path_follows_existing_convention(tmp_path):
    assert store.lock_path_for(tmp_path / 'schedule_store.json').name == (
        'schedule_store.json.lock'
    )


def test_lock_is_reentrant_across_sequential_uses(tmp_path):
    target = tmp_path / 'f.json'
    with store.file_lock(target):
        pass
    with store.file_lock(target, exclusive=False):
        pass


def test_lock_survives_unwritable_directory(tmp_path):
    """락 파일을 못 만들어도 기록 자체는 진행되어야 한다."""
    target = tmp_path / 'ro' / 'f.txt'
    target.parent.mkdir()
    target.write_text('x', encoding='utf-8')
    os.chmod(target.parent, 0o500)
    try:
        with store.file_lock(target):
            pass
    finally:
        os.chmod(target.parent, 0o700)


def _hold_then_write(path, marker, barrier):
    from motion_common import store as s
    barrier.wait(timeout=10)
    with s.locked_update(path):
        current = s.read_json(path, default=[])
        current.append(marker)
        s.atomic_write_json(path, current)


def test_concurrent_read_modify_write_does_not_lose_updates(tmp_path):
    """락 없이는 나중 기록이 앞선 수정을 지운다 · 이 검증이 락의 존재 이유다."""
    target = tmp_path / 'f.json'
    store.atomic_write_json(target, [])

    barrier = multiprocessing.Barrier(4)
    procs = [
        multiprocessing.Process(target=_hold_then_write, args=(str(target), i, barrier))
        for i in range(4)
    ]
    for p in procs:
        p.start()
    for p in procs:
        p.join(timeout=30)

    assert all(p.exitcode == 0 for p in procs)
    assert sorted(json.loads(Path(target).read_text(encoding='utf-8'))) == [0, 1, 2, 3]


def test_update_json_applies_mutation_under_lock(tmp_path):
    target = tmp_path / 'f.json'
    result = store.update_json(target, lambda current: (current or []) + ['a'], default=[])
    assert result == ['a']
    assert store.read_json(target) == ['a']
