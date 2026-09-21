"""스케줄이 말하는 상태와 실제를 1분마다 맞춘다 · §6-137

전에는 「시각을 지나갔나」만 봤다 · 시작은 5분 창, 정지는 60초 창이었고
그 창을 놓치면 그날은 끝이었다.

    09:00~18:00 스케줄 · 13:00 에 재부팅  →  그날 하루 종일 안 돌았다
    18:00 에 서비스 재시작 중              →  종료 명령을 못 받아 계속 돌았다

지금은 「지금 구간 안인가」를 본다 · 그래서 재부팅해도, 놓쳐도, 어긋나도
다음 점검에서 스스로 맞춘다.
"""

from datetime import datetime
from types import SimpleNamespace

import pytest

from motion_common.schedule_models import ScheduleItem
from motion_schedule.motion_schedule_node import MotionScheduleNode


class _Logger:
    def info(self, *_args): pass
    def debug(self, *_args): pass
    def warning(self, *_args): pass
    def error(self, *_args): pass


DAY = datetime(2026, 9, 17)


def _node(tmp_path, *, run_state, run_mode='schedule', schedules=None, group=None):
    node = MotionScheduleNode.__new__(MotionScheduleNode)
    node.projects_dir = str(tmp_path)
    node.coordination_file = str(tmp_path / 'motion_coordination.yaml')
    node.store = SimpleNamespace(
        current_project_id='proj-a',
        list_schedules=lambda: list(schedules or []),
    )
    node.get_logger = lambda: _Logger()
    node._coordination_enabled = lambda: False
    node._coordination_joined = lambda: False
    node._local_run_state = lambda: run_state
    node._group_execution = lambda: dict(group or {})
    node._run_mode = run_mode
    node.sent = []
    node._send_http_request = lambda endpoint, payload: (
        node.sent.append((endpoint, payload)) or True
    )
    from motion_schedule.schedule_engine import ScheduleEngine
    node.engine = ScheduleEngine()
    return node


def _day_schedule(**overrides):
    return ScheduleItem(
        schedule_id='s1', schedule_name='낮 공연',
        start_time='09:00:00', stop_time='18:00:00',
        repeat_type='daily', enabled=True, **overrides,
    )


def test_inside_the_window_and_stopped_starts_it(tmp_path):
    """13:00 에 재부팅해도 돈다 · 전에는 그날 하루 종일 안 돌았다."""
    node = _node(tmp_path, run_state='stopped', schedules=[_day_schedule()])

    node._reconcile(DAY.replace(hour=13))

    endpoint, payload = node.sent[0]
    assert endpoint == '/api/motion-run/start'
    assert payload['run_mode'] == 'continuous'


def test_inside_the_window_and_already_running_does_nothing(tmp_path):
    node = _node(tmp_path, run_state='running', schedules=[_day_schedule()])

    node._reconcile(DAY.replace(hour=13))

    assert node.sent == [], '이미 도는 모션을 또 시작시키면 안 된다'


def test_outside_the_window_and_running_stops_after_the_cycle(tmp_path):
    """18:00 정지를 놓쳐도 다음 점검에서 멈춘다 · 회차는 끝까지 돈다."""
    node = _node(tmp_path, run_state='running', schedules=[_day_schedule()])

    node._reconcile(DAY.replace(hour=20))

    endpoint, _payload = node.sent[0]
    assert endpoint == '/api/motion-run/stop-after-cycle'


def test_outside_the_window_and_stopped_does_nothing(tmp_path):
    node = _node(tmp_path, run_state='stopped', schedules=[_day_schedule()])

    node._reconcile(DAY.replace(hour=20))

    assert node.sent == []


def test_manual_mode_does_nothing_at_all(tmp_path):
    """수동 모드 · 정비·시험 중에 1분 점검이 모션을 되살리면 위험하다 · §6-143

    전에는 「사람이 멈췄나」를 요청 내용으로 추측했다 · 그룹 정지나 안전 정지
    까지 사람이 멈춘 것으로 읽어서, 1회 연동 실행만 해도 "사람이 모션을
    정지했습니다" 가 떴다 · 추측을 없애고 스위치 하나로 만들었다.
    """
    node = _node(
        tmp_path, run_state='stopped', run_mode='manual',
        schedules=[_day_schedule()],
    )

    node._reconcile(DAY.replace(hour=13))

    assert node.sent == [], '수동 모드인데 스케줄이 시작시켰다'


def test_manual_mode_still_stops_when_the_window_ends(tmp_path):
    """**구간이 끝나면 멈춘다 · 모드와 상관없이** · §6-270

    전에는 반대였다 — 손으로 돌리는 중이면 세우지 않았다 · 그런데 수동
    모드에서는 정지 자체를 검사하지 않아서, 스케줄이 잘못 켠 모션이 구간이
    끝나도 영영 돌았다 (실측 17:58 시작 → 18:16 까지 18분).

    「구간 밖에서는 아무것도 돌지 않는다」가 규칙이다 · 누가 켰는지는 묻지
    않는다.
    """
    node = _node(
        tmp_path, run_state='running', run_mode='manual',
        schedules=[_day_schedule()],
    )

    node._reconcile(DAY.replace(hour=20))

    assert len(node.sent) == 1, '구간이 끝났는데 정지를 안 보냈다'
    assert 'stop-after-cycle' in node.sent[0][0] or 'stop_after_cycle' in str(node.sent[0][1])


def test_switching_back_to_schedule_mode_resumes_management(tmp_path):
    node = _node(tmp_path, run_state='running', schedules=[_day_schedule()])

    node._reconcile(DAY.replace(hour=20))

    assert node.sent[0][0] == '/api/motion-run/stop-after-cycle'


def test_a_midnight_window_runs_through_the_night(tmp_path):
    """23:00~01:00 · 종료가 시작보다 이르면 자정을 넘긴 것으로 본다."""
    night = _day_schedule()
    night.start_time, night.stop_time = '23:00:00', '01:00:00'
    node = _node(tmp_path, run_state='stopped', schedules=[night])

    node._reconcile(DAY.replace(day=18, hour=0, minute=30))
    assert node.sent, '자정을 넘긴 구간 안인데 시작하지 않았다'

    node.sent.clear()
    node._reconcile(DAY.replace(day=18, hour=1, minute=30))
    assert node.sent == [], '구간이 끝났는데 시작했다'


def test_a_disabled_schedule_is_ignored(tmp_path):
    off = _day_schedule()
    off.enabled = False
    node = _node(tmp_path, run_state='stopped', schedules=[off])

    node._reconcile(DAY.replace(hour=13))

    assert node.sent == []


@pytest.mark.parametrize('state', ['preparing', 'initializing', 'waiting', 'stopping'])
def test_starting_up_counts_as_running(tmp_path, state):
    """준비 중인 모션을 또 시작시키면 안 된다 · §6-136"""
    node = _node(tmp_path, run_state=state, schedules=[_day_schedule()])

    node._reconcile(DAY.replace(hour=13))

    assert node.sent == []


def test_an_unreadable_run_state_counts_as_running(tmp_path):
    """상태를 못 읽었으면 가만둔다 · 모르는 채로 시작시키지 않는다."""
    node = _node(tmp_path, run_state='', schedules=[_day_schedule()])

    node._reconcile(DAY.replace(hour=13))

    assert node.sent == []


def test_a_group_execution_being_prepared_is_not_restarted(tmp_path):
    """오늘 08:59:50 에 난 헛시도 · §6-145

    그룹 실행은 준비가 길다 · 그동안 이 PC 의 로컬 모션은 아직 `stopped` 다 ·
    로컬만 보면 「구간 안인데 멈춰 있다」로 읽고 이미 시작된 그룹 실행을 또
    시작시킨다 · 조정 노드가 막아 피해는 없었지만 1분마다 헛시도가 나갔다.

        08:57:49  시작 → success: True  · 그룹 실행 준비 확인 시작
        08:59:50  시작 → success: False · 이전 그룹 실행 정리 확인 중입니다
    """
    node = _node(
        tmp_path, run_state='stopped', schedules=[_day_schedule()],
        group={'execution_id': 'exec-1', 'state': 'preparing'},
    )

    node._reconcile(DAY.replace(hour=13))

    assert node.sent == [], '준비 중인 그룹 실행을 또 시작시켰다'


@pytest.mark.parametrize('state', ['armed', 'start_scheduled', 'waiting', 'running'])
def test_every_live_group_stage_counts_as_running(tmp_path, state):
    node = _node(
        tmp_path, run_state='stopped', schedules=[_day_schedule()],
        group={'execution_id': 'exec-1', 'state': state},
    )

    node._reconcile(DAY.replace(hour=13))

    assert node.sent == []


def test_a_finished_group_execution_does_not_block_the_start(tmp_path):
    """끝난 그룹 실행 때문에 영영 안 시작하면 안 된다."""
    node = _node(
        tmp_path, run_state='stopped', schedules=[_day_schedule()],
        group={'execution_id': '', 'state': 'stopped'},
    )

    node._reconcile(DAY.replace(hour=13))

    assert node.sent, '끝난 그룹 실행 때문에 시작하지 못했다'
    assert node.sent[0][0] == '/api/motion-run/start'
