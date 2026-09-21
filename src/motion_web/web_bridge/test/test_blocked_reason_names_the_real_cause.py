"""막힌 이유는 **아는 쪽이** 붙인다 · §6-258

스튜디오 노드는 「실행 컨텍스트가 아직 아니다」까지만 안다 · 왜 아닌지는
브릿지가 안다.

실제로 터진 모습은 이랬다 · 모터 네 대가 전부 오프라인이라 실행 컨텍스트가
`ready` 가 못 됐고, 저장을 누르면 이렇게 떴다.

    저장 실패 · 먼저 왼쪽에서 통합 프로젝트를 선택하세요

왼쪽은 멀쩡한데 왼쪽을 보라고 한다 · 진짜 이유는 「모터 상태를 기다리는 중」
이었다 · 사람이 볼 문구에 그 말이 들어가야 한다.

문구 글자를 맞춰 보지 않는다 · 노드가 달아 준 표시(`project_attached`,
`context_ready`)만 본다 · 문구가 바뀌어도 조용히 깨지지 않는다.
"""

from motion_web_bridge.motion_studio_sync import MotionStudioSync

MOTOR_REASON = 'motor_manager_node 시작 후 첫 모터 상태를 기다리는 중입니다'


class _Context:
    def __init__(self, status):
        self._status = status

    def status(self, validate_files=True):
        return self._status


class _Bridge:
    def __init__(self, status):
        self._execution_context = _Context(status)


def _sync(status):
    return MotionStudioSync(_Bridge(status), session=None, transport=None)


def _waiting_status():
    return {
        'state': 'waiting_motor_runtime',
        'ready': False,
        'message': '현재 프로젝트의 모터 관리 노드 상태 확인 대기 중',
        'failures': {'motor_runtime': MOTOR_REASON},
    }


def test_the_motor_reason_is_added():
    sync = _sync(_waiting_status())
    result = {
        'success': False,
        'message': '먼저 왼쪽에서 통합 프로젝트를 선택하세요',
        'project_attached': False,
    }

    explained = sync.blocked_reason(result)

    assert MOTOR_REASON in explained['message']
    assert explained['blocked_by'] == 'execution_context'


def test_it_works_for_the_running_mark_too():
    sync = _sync(_waiting_status())
    result = {
        'success': False,
        'message': '현재 프로젝트 실행 컨텍스트 적용 대기 중입니다',
        'context_ready': False,
    }

    assert MOTOR_REASON in sync.blocked_reason(result)['message']


def test_other_failures_are_left_alone():
    """엉뚱한 실패에까지 모터 얘기를 붙이지 않는다."""
    sync = _sync(_waiting_status())
    result = {'success': False, 'message': '레이어를 찾을 수 없습니다'}

    assert sync.blocked_reason(result) == result


def test_nothing_is_added_when_everything_is_ready():
    sync = _sync({'state': 'ready', 'ready': True, 'message': '사용자 제어 가능'})
    result = {
        'success': False,
        'message': '먼저 왼쪽에서 통합 프로젝트를 선택하세요',
        'project_attached': False,
    }

    assert sync.blocked_reason(result) == result


def test_the_original_result_is_not_changed_in_place():
    sync = _sync(_waiting_status())
    result = {
        'success': False,
        'message': '먼저 왼쪽에서 통합 프로젝트를 선택하세요',
        'project_attached': False,
    }

    sync.blocked_reason(result)

    assert result['message'] == '먼저 왼쪽에서 통합 프로젝트를 선택하세요'
