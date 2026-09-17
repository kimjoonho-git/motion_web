"""스케줄이 보내는 반복 방식 · §6-135.

화면에서 손으로 누르면 되는 모션이 스케줄로는 죽었다.

    [SCHEDULE TRIGGER] START -> 발화 확인
    HTTP /api/motion-run/start → '모션 실행 준비 실패: 모션 시작·종료값 차이가
                                 5°를 초과합니다: Axis 0 모션값 차이 23.216°'

화면 선택칸은 「초기 위치 이동 후 다음」이 기본인데, 스케줄 노드만 `direct` 를
기본으로 삼고 있었다 · `direct` 는 끝값에서 시작값으로 곧바로 튀므로 둘이
벌어져 있으면 실행이 거부된다.
"""

import json
from types import SimpleNamespace

from motion_common import repeat_policy
from motion_common.schedule_models import ScheduleItem
import motion_schedule.motion_schedule_node as schedule_node
from motion_schedule.motion_schedule_node import MotionScheduleNode


class _Logger:
    def info(self, *_args): pass
    def debug(self, *_args): pass
    def warning(self, *_args): pass
    def error(self, *_args): pass


def _node(tmp_path, project_id='proj-a'):
    node = MotionScheduleNode.__new__(MotionScheduleNode)
    node.projects_dir = str(tmp_path)
    node.coordination_file = str(tmp_path / 'motion_coordination.yaml')
    node.store = SimpleNamespace(current_project_id=project_id)
    node.get_logger = lambda: _Logger()
    node._coordination_enabled = lambda: False
    node.sent = []
    node._send_http_request = lambda endpoint, payload: (
        node.sent.append((endpoint, payload)) or True
    )
    return node


def _write_automation(tmp_path, project_id, **fields):
    runtime = tmp_path / project_id / 'runtime'
    runtime.mkdir(parents=True, exist_ok=True)
    (runtime / 'motion_automation.json').write_text(
        json.dumps(fields), encoding='utf-8',
    )


def test_missing_automation_file_means_reinitialize(tmp_path):
    """설정 파일이 없는 프로젝트 · 여기서 죽었다.

    초기 위치로 옮긴 뒤 재생하므로 시작값과 끝값이 달라도 된다 · 사람이 보고
    있지 않은 자리에서 더 안전하기도 하다.
    """
    node = _node(tmp_path)

    node._execute_start(ScheduleItem(schedule_id='s1'))

    _endpoint, payload = node.sent[0]
    assert payload['repeat_mode'] == 'reinitialize'
    assert payload['repeat_mode'] == repeat_policy.DEFAULT_REPEAT_MODE


def test_saved_repeat_mode_is_used_as_is(tmp_path):
    _write_automation(tmp_path, 'proj-a', repeat_mode='dwell', dwell_sec=4.0)
    node = _node(tmp_path)

    node._execute_start(ScheduleItem(schedule_id='s1'))

    _endpoint, payload = node.sent[0]
    assert payload['repeat_mode'] == 'dwell'
    assert payload['dwell_sec'] == 4.0


def test_broken_repeat_mode_falls_back_to_the_default(tmp_path):
    _write_automation(tmp_path, 'proj-a', repeat_mode='이상한값')
    node = _node(tmp_path)

    node._execute_start(ScheduleItem(schedule_id='s1'))

    _endpoint, payload = node.sent[0]
    assert payload['repeat_mode'] == 'reinitialize'


def test_the_group_path_sends_the_same_mode(tmp_path):
    node = _node(tmp_path)
    node._coordination_enabled = lambda: True

    node._execute_start(ScheduleItem(schedule_id='s1'))

    endpoint, payload = node.sent[0]
    assert endpoint == '/api/coordination/control'
    assert payload['repeat_mode'] == 'reinitialize'
