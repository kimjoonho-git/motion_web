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


def _node(tmp_path, *, run_state, hold='', schedules=None):
    node = MotionScheduleNode.__new__(MotionScheduleNode)
    node.projects_dir = str(tmp_path)
    node.coordination_file = str(tmp_path / 'motion_coordination.yaml')
    node.store = SimpleNamespace(
        current_project_id='proj-a',
        list_schedules=lambda: list(schedules or []),
    )
    node.get_logger = lambda: _Logger()
    node._coordination_enabled = lambda: False
    node._local_run_state = lambda: run_state
    node._schedule_hold_reason = hold
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


def test_a_human_stop_is_not_undone(tmp_path):
    """사람이 멈추면 사람이 다시 켤 때까지 스케줄이 손대지 않는다 · §6-138"""
    node = _node(
        tmp_path, run_state='stopped', schedules=[_day_schedule()],
        hold='사람이 모션을 정지했습니다 · 다시 시작하면 스케줄이 이어받습니다',
    )

    node._reconcile(DAY.replace(hour=13))

    assert node.sent == [], '사람이 멈춘 것을 1분 뒤에 되돌리면 정지가 무의미하다'


def test_the_schedule_takes_over_again_once_a_human_starts_it(tmp_path):
    """사람이 다시 켜면 브리지가 걸쇠를 풀고, 스케줄이 이어받는다."""
    node = _node(tmp_path, run_state='running', schedules=[_day_schedule()], hold='')

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
