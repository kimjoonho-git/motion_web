"""스케줄이 보낸 시작은 **받는 쪽에서 한 번 더 본다** · §6-270

스케줄 노드는 운전 모드를 0.5초짜리 조회로 받는다 · 못 받으면 「스케줄」로
친다(`DEFAULT_RUN_MODE`) · 그래서 브릿지가 잠깐 막히면 사람이 걸어 둔
「수동」이 무시된다.

실측으로 17:58:17 에 브릿지가 626ms 막혔고 같은 초에 스케줄이 수동 모드인데도
모터를 돌렸다 · 저장 파일은 그때도 `manual` 이었다.

여기서는 조회가 아니라 **저장 파일**을 읽는다 · 브릿지가 막혀도 흔들리지 않는다.
"""

import json
from pathlib import Path
from types import SimpleNamespace

from motion_web_bridge.bridge_node import MotionWebBridge


class _Logger:
    def __init__(self):
        self.warns = []

    def warn(self, message):
        self.warns.append(str(message))

    def info(self, message):
        pass


def _bridge(tmp_path, mode):
    project_id = '시험프로젝트-0001'
    project_dir = tmp_path / 'motion_projects' / project_id
    project_dir.mkdir(parents=True)
    (project_dir.parent / f'{project_id}' / 'schedule_store.json').write_text(
        json.dumps({'version': 1, 'mode': mode, 'schedules': []}, ensure_ascii=False),
        encoding='utf-8',
    )
    bridge = MotionWebBridge.__new__(MotionWebBridge)
    bridge.workspace_root = tmp_path
    bridge.project_repository = SimpleNamespace(
        selected_project_id=lambda: project_id,
    )
    bridge._logger = _Logger()
    bridge.get_logger = lambda: bridge._logger
    return bridge


def test_a_schedule_start_is_refused_in_manual_mode(tmp_path):
    bridge = _bridge(tmp_path, 'manual')

    reason = bridge.schedule_start_blocked_by_manual_mode({'schedule_id': 's1'})

    assert '수동' in reason


def test_a_schedule_start_passes_in_schedule_mode(tmp_path):
    bridge = _bridge(tmp_path, 'schedule')

    assert bridge.schedule_start_blocked_by_manual_mode({'schedule_id': 's1'}) == ''


def test_a_hand_start_is_never_blocked(tmp_path):
    """사람이 화면에서 누른 시작에는 이름표가 없다 · 수동 모드여도 돌아야 한다."""
    bridge = _bridge(tmp_path, 'manual')

    assert bridge.schedule_start_blocked_by_manual_mode({}) == ''
    assert bridge.schedule_start_blocked_by_manual_mode({'schedule_id': ''}) == ''


def test_no_project_means_no_schedule_start(tmp_path):
    bridge = _bridge(tmp_path, 'manual')
    bridge.project_repository = SimpleNamespace(selected_project_id=lambda: '')

    assert '프로젝트' in bridge.schedule_start_blocked_by_manual_mode({'schedule_id': 's1'})


def test_it_reads_the_file_not_a_query(tmp_path):
    """조회가 막혀도 흔들리지 않아야 한다 · 파일을 직접 읽는 것이 요점이다."""
    source = (
        Path(__file__).resolve().parents[1]
        / 'motion_web_bridge' / 'bridge_node.py'
    ).read_text(encoding='utf-8')
    start = source.index('def schedule_start_blocked_by_manual_mode(')
    body = source[start:source.index('\n    def ', start)]

    assert 'ScheduleStore(' in body
    assert 'schedule/status' not in body, '조회로 물어보면 같은 함정에 빠진다'
