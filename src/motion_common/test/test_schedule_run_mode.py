"""스케줄이 실행을 관리하는가 · 스위치 하나로 정한다 · §6-143

전에는 「사람이 멈췄나」를 요청 내용으로 **추측**했다 · 그룹 정지·안전 정지·
모터 설정 적용도 로컬 모션 실행을 세우는데, 그것까지 사람이 멈춘 것으로 읽어
1회 연동 실행만 해도 "사람이 모션을 정지했습니다" 가 떴다 · 아무도 안 눌렀다.

추측을 없앴다 · 지금 어느 쪽인지는 사람이 정하고 화면에 보인다.
"""

import json

import pytest

from motion_common.schedule_models import ScheduleItem
from motion_common.schedule_store import (
    DEFAULT_RUN_MODE,
    MANUAL_MODE,
    SCHEDULE_MODE,
    ScheduleStore,
    normalize_run_mode,
)


def _store(tmp_path):
    return ScheduleStore(projects_dir=str(tmp_path), current_project_id='proj-a')


def test_schedule_mode_is_the_default():
    """스케줄을 걸었으면 도는 것이 기대다 · 정비할 때만 수동으로 바꾼다."""
    assert DEFAULT_RUN_MODE == SCHEDULE_MODE


@pytest.mark.parametrize('value', ['', None, '이상한값', 'SCHEDULED'])
def test_unknown_modes_fall_back_to_the_default(value):
    assert normalize_run_mode(value) == SCHEDULE_MODE


def test_manual_mode_is_kept_as_is():
    assert normalize_run_mode('manual') == MANUAL_MODE
    assert normalize_run_mode('MANUAL') == MANUAL_MODE


def test_the_mode_survives_a_restart(tmp_path):
    """정비하려고 수동으로 바꿔 뒀는데 재시작하면 풀리면 위험하다."""
    store = _store(tmp_path)
    store.set_mode(MANUAL_MODE)

    reopened = _store(tmp_path)

    assert reopened.mode == MANUAL_MODE


def test_schedules_and_mode_live_in_one_file(tmp_path):
    store = _store(tmp_path)
    store.upsert_schedule(ScheduleItem(schedule_id='s1', schedule_name='아침'))
    store.set_mode(MANUAL_MODE)

    saved = json.loads(
        (tmp_path / 'proj-a' / 'schedule_store.json').read_text(encoding='utf-8')
    )

    assert saved['mode'] == MANUAL_MODE
    assert [item['schedule_id'] for item in saved['schedules']] == ['s1']


def test_an_old_file_still_loads(tmp_path):
    """모드가 없던 시절 파일은 스케줄만 담은 목록이었다."""
    project = tmp_path / 'proj-a'
    project.mkdir(parents=True, exist_ok=True)
    (project / 'schedule_store.json').write_text(
        json.dumps([ScheduleItem(schedule_id='s1').to_dict()]),
        encoding='utf-8',
    )

    store = _store(tmp_path)

    assert [item.schedule_id for item in store.list_schedules()] == ['s1']
    assert store.mode == SCHEDULE_MODE, '옛 파일은 스케줄대로 돌던 상태다'
