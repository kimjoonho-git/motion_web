"""수동 모드는 「새로 시작 안 함」이지 「켠 것을 방치함」이 아니다 · §6-270

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


def test_manual_mode_still_stops_what_the_schedule_started():
    body = _body('_reconcile')

    assert '_stop_what_we_started' in body, '수동 모드에서 그냥 돌아간다'
    # 새로 시작하는 쪽은 여전히 막혀 있어야 한다
    assert body.index('_stop_what_we_started') < body.index('return')


def test_it_only_stops_its_own_run():
    """사람이 손으로 켠 것은 건드리지 않는다."""
    body = _body('_stop_what_we_started')

    assert "status.get('schedule_id')" in body, '누가 켰는지 보지 않는다'
    assert 'if not started_by' in body, '이름표가 없으면 사람이 켠 것이다'


def test_it_only_stops_outside_the_window():
    body = _body('_stop_what_we_started')

    assert 'self.engine.active(' in body, '구간 안인지 보지 않는다'


def test_it_asks_for_a_graceful_stop():
    """회차 도중에 끊지 않는다."""
    body = _body('_stop_what_we_started')

    assert '/api/motion-run/stop-after-cycle' in body
    assert '/api/motion-run/stop' not in body.replace('/api/motion-run/stop-after-cycle', '')


def test_it_does_nothing_when_nothing_runs():
    body = _body('_stop_what_we_started')

    assert 'is_running(' in body
