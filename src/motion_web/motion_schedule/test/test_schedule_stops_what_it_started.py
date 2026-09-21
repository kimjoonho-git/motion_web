"""구간이 끝나면 멈춘다 · **모드와 상관없이** · §6-270

실제로 일어난 일이다.

    17:22:48  사람이 운전 모드를 「수동」으로 저장 (파일에 남음)
    17:41:01  브릿지·스케줄 노드 재시작
    17:58:17  브릿지가 626ms 막힘 · 같은 초에 스케줄이 START 를 보냄
              (조회가 0.5초를 넘기면 None 이 되고, None 은 「스케줄」로 친다)
    17:58:23  모션 시작 · 연속 · 무제한
    18:00:00  스케줄 구간 끝
    18:16     아직도 돌고 있음 · 스케줄 로그는 17:58:17 이 마지막

수동 모드라 `_reconcile` 이 맨 앞에서 돌아갔고, 그래서 **정지도 검사하지
않았다** · 켠 주인은 사라졌는데 끌 사람이 아무도 없었다.

이제 정지는 모드보다 앞에 온다 · **구간 밖에서는 아무것도 돌지 않는다** ·
사람이 손으로 켠 것도 구간이 끝나면 멈춘다.
"""

import re
from pathlib import Path

NODE = (
    Path(__file__).resolve().parents[1]
    / 'motion_schedule' / 'motion_schedule_node.py'
).read_text(encoding='utf-8')


def _body(name: str) -> str:
    start = NODE.index(f'def {name}(')
    nxt = NODE.find('\n    def ', start)
    body = NODE[start:nxt if nxt > 0 else len(NODE)]
    return re.sub(r'^\s*#.*$', '', body, flags=re.M)


def test_stopping_comes_before_the_mode_check():
    """수동 모드라고 맨 앞에서 돌아가면 정지도 못 한다."""
    body = _body('_reconcile')

    stop_at = body.index('_execute_stop_after_cycle')
    mode_at = body.index('self._run_mode != SCHEDULE_MODE')
    assert stop_at < mode_at, '모드 검사가 정지보다 앞에 있다'


def test_starting_still_needs_schedule_mode():
    """수동 모드에서는 새로 시작하지 않는다."""
    body = _body('_reconcile')

    mode_at = body.index('self._run_mode != SCHEDULE_MODE')
    start_at = body.index('_execute_start')
    assert mode_at < start_at, '수동 모드에서도 시작해 버린다'


def test_it_does_not_ask_who_started_it():
    """누가 켰는지 묻지 않는다 · 구간 밖이면 무조건 멈춘다."""
    body = _body('_reconcile')

    assert 'schedule_id' not in body


def test_it_asks_for_a_graceful_stop():
    """회차 도중에 끊지 않는다."""
    body = _body('_execute_stop_after_cycle')

    assert 'stop_after_cycle' in body


def test_it_only_stops_when_something_runs():
    body = _body('_reconcile')

    assert 'wanted is None and running' in body


# --------------------------------------------------------------------------- #
# 못 읽었으면 **짐작하지 않는다** · 그리고 실패를 남긴다 · §6-271
#
# 전에는 조회가 실패하면 `normalize_run_mode(None)` 이 기본값인 「스케줄」을
# 돌려줬다 · 사람이 걸어 둔 「수동」이 조회 한 번 실패로 풀렸고, 그 순간 구간
# 안이면 모터가 돌기 시작했다.
#
# 실패 기록은 `debug` 였다 · 디버그가 꺼져 있어 아무 데도 안 남았고, 그래서
# 17:58 에 실제로 실패했는지조차 확인할 수 없었다.
# --------------------------------------------------------------------------- #


def test_a_failed_read_does_not_change_the_mode():
    body = _body('_on_timer_tick')

    assert "data.get('run_mode') is not None" in body, '못 읽어도 모드를 덮어쓴다'
    assert 'isinstance(data, dict)' in body


def test_a_failed_read_is_logged_where_people_can_see_it():
    body = _body('_read_json')

    assert '.debug(' not in body, '디버그로 적으면 아무 데도 안 남는다'
    assert '.warn(' in body


def test_it_does_not_flood_the_log():
    """1초마다 물어보므로 실패할 때마다 적으면 로그가 넘친다."""
    body = _body('_read_json')

    assert 'failures == 1' in body, '상태가 바뀔 때만 적어야 한다'


def test_it_says_when_it_recovers():
    body = _body('_read_json')

    assert '조회 회복' in body
