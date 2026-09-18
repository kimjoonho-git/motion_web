"""같은 모션 파일을 시작할 때마다 세 번 읽지 않는다 · §6-173

**한 번 시작할 때 계획을 여러 번 만든다.**

    단독 재생   둘  · 재생 + 초기 이동
    연동 그룹   셋  · 검증 + 재생 + 초기 이동

계획을 만들 때마다 같은 파일을 다시 읽고 파싱했다 · 실제 파일(183KB · 4,866행)
로 재 보니 한 번에 **45ms**, 세 번이면 **135ms** 다.

그 135ms 가 하필 **그룹 동기 시작 직전**에 놓인다 · 세 대가 같은 순간에
출발해야 하는 그 자리다.

읽는 횟수를 줄이는 것이지 **검사를 줄이는 것이 아니다** · 계획은 여전히
세 번 만들어지고 세 번 다 검사한다 (그 셋이 서로 다른 것을 만든다) ·
파일을 다시 읽지 않을 뿐이다.

여기서 지키는 것 둘:

    파일이 바뀌면 **반드시** 새로 읽는다 · 옛 모션을 돌리면 사고다
    돌려주는 목록은 **매번 새것** · 부르는 쪽이 여기 덧붙인다
"""

import json
import time
from pathlib import Path

import pytest

from motion_runtime.motion_run_manager import MotionRunManager


#: 진짜 모션 파일과 같은 모양 · 첫 줄이 머리, 그다음은 JSON 배열 한 줄씩
HEADER = {
    'type': 'motion_header',
    'rotation_mode': 'relative',
    'rotation_unit': 'deg',
    'fields': ['frame', 'time_sec', 'id', 'value'],
}


def _write(path: Path, rows, value=1.0):
    lines = [json.dumps(HEADER, ensure_ascii=False)]
    for index in range(1, rows + 1):
        lines.append(json.dumps([index, round(index * 0.02, 3), '1-1', value]))
    path.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return path


@pytest.fixture
def manager():
    return MotionRunManager.__new__(MotionRunManager)


@pytest.fixture
def motion_file(tmp_path):
    return _write(tmp_path / 'motion.json', 40)


def test_the_same_file_is_parsed_only_once(manager, motion_file, monkeypatch):
    parsed = []
    original = MotionRunManager._parse_motion_records
    monkeypatch.setattr(
        MotionRunManager, '_parse_motion_records',
        lambda self, path: parsed.append(path) or original(self, path),
    )

    for _ in range(3):
        manager._load_motion_records(motion_file)

    assert len(parsed) == 1, f'{len(parsed)}번 읽었습니다 · 한 번이어야 합니다'


def test_a_changed_file_is_read_again(manager, motion_file, tmp_path):
    """옛 모션을 돌리면 사고다 · 파일이 바뀌면 반드시 다시 읽는다."""
    before = manager._load_motion_records(motion_file)

    time.sleep(0.01)
    _write(motion_file, 40, value=2.0)
    after = manager._load_motion_records(motion_file)

    assert before[0]['value'] != after[0]['value'], '바뀐 파일을 옛것으로 돌려줬습니다'


def test_a_file_that_only_grew_is_read_again(manager, motion_file):
    """`mtime` 만 보면 같은 순간의 수정을 놓친다 · 크기도 같이 본다."""
    before = manager._load_motion_records(motion_file)
    stat = motion_file.stat()

    _write(motion_file, 80)
    import os
    os.utime(motion_file, ns=(stat.st_atime_ns, stat.st_mtime_ns))

    after = manager._load_motion_records(motion_file)
    assert len(after) > len(before), '길어진 파일을 옛것으로 돌려줬습니다'


def test_each_call_gets_its_own_list(manager, motion_file):
    """부르는 쪽이 여기에 덧붙인다 (초기 이동 대체값) · 번지면 안 된다."""
    first = manager._load_motion_records(motion_file)
    first.append({'frame': 0, 'time_sec': 0.0, 'motion_id': '1-1', 'value': 0.0})

    second = manager._load_motion_records(motion_file)

    assert len(second) == len(first) - 1, '덧붙인 값이 다음 계획으로 번졌습니다'


def test_the_rows_themselves_are_identical(manager, motion_file):
    """내용까지 달라지면 캐시가 아니라 버그다."""
    first = manager._load_motion_records(motion_file)
    second = manager._load_motion_records(motion_file)

    assert first == second
    assert first is not second


def test_the_cache_does_not_grow_without_bound(manager, tmp_path):
    """프로젝트를 옮겨 다니며 큰 파일을 읽으면 메모리가 는다."""
    for index in range(MotionRunManager.MOTION_CACHE_SIZE + 3):
        manager._load_motion_records(_write(tmp_path / f'motion{index}.json', 10))

    assert len(manager._motion_record_cache) <= MotionRunManager.MOTION_CACHE_SIZE


def test_a_missing_file_still_raises(manager, tmp_path):
    """캐시가 오류를 삼키면 안 된다."""
    with pytest.raises(OSError):
        manager._load_motion_records(tmp_path / '없는파일.json')


def test_an_empty_file_still_raises(manager, tmp_path):
    empty = tmp_path / 'empty.json'
    empty.write_text(json.dumps(HEADER) + '\n', encoding='utf-8')

    with pytest.raises(ValueError, match='no valid records'):
        manager._load_motion_records(empty)
