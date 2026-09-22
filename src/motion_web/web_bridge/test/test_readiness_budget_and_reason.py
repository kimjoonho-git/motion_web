"""준비 확인은 **기다려 주는 시간 안에 끝내고, 왜 안 되는지 말한다** · §6-297

실측 · 부르는 쪽(연동 노드)은 4초를 기다리는데 브리지는 안에서 최대 10초를
썼다 · 그래서 모터가 멀쩡해도 화면에는 이렇게 떴다.

    joonhoTest 그룹 시작 거부: 로컬 Web Bridge 응답 없음: timed out

브리지는 멀쩡했다 · 진짜 원인은 모터 피드백이 끊긴 것이었고, 그 사실은
실행 컨텍스트가 이미 알고 있었다.

    motor_runtime: motor_manager_node 시작 후 첫 모터 상태를 기다리는 중입니다

시간이 다 되기 전에 답이 돌아가야 그 말이 위로 올라간다.
"""

from types import SimpleNamespace

from motion_web_bridge.coordination_bridge import (
    DEFAULT_READINESS_BUDGET_SEC,
    READINESS_MARGIN_SEC,
    local_motion_readiness,
    readiness_failure_text,
)


class _Context:
    def __init__(self, ready=False, message='', failures=None):
        self.asked_timeout = None
        self._answer = {
            'ready': ready, 'message': message, 'failures': failures or {},
        }

    def reconcile_blocking(self, timeout_sec):
        self.asked_timeout = timeout_sec
        return dict(self._answer)


def _bridge(context):
    return SimpleNamespace(_execution_context=context)


def test_the_inner_work_finishes_before_the_caller_gives_up():
    context = _Context(message='모터 관리 노드 상태 확인 대기 중')

    local_motion_readiness(_bridge(context), {'budget_sec': 6.0})

    assert context.asked_timeout == 6.0 - READINESS_MARGIN_SEC
    assert context.asked_timeout < 6.0, '부르는 쪽보다 오래 쓰면 답이 못 돌아간다'


def test_without_a_budget_it_uses_the_old_value():
    context = _Context()

    local_motion_readiness(_bridge(context), None)

    assert context.asked_timeout == DEFAULT_READINESS_BUDGET_SEC - READINESS_MARGIN_SEC


def test_a_tiny_budget_still_leaves_time_to_work():
    context = _Context()

    local_motion_readiness(_bridge(context), {'budget_sec': 0.2})

    assert context.asked_timeout >= 0.5


def test_the_answer_carries_the_real_reason():
    context = _Context(
        message='현재 프로젝트의 모터 관리 노드 상태 확인 대기 중',
        failures={'motor_runtime': 'motor_manager_node 시작 후 첫 모터 상태를 기다리는 중입니다'},
    )

    result = local_motion_readiness(_bridge(context), {'budget_sec': 6.0})

    assert result['success'] is False
    assert '모터 관리 노드 상태 확인 대기 중' in result['message']
    assert 'motor_runtime' in result['message'], '어느 노드가 막았는지 없다'
    assert '첫 모터 상태를 기다리는' in result['message'], '진짜 사유가 없다'


def test_a_plain_message_stays_plain():
    assert readiness_failure_text({'message': '적용 대기 중'}) == '적용 대기 중'
    assert readiness_failure_text({}) == '적용 대기 중'


def test_empty_reasons_are_not_appended():
    text = readiness_failure_text({'message': '대기', 'failures': {'a': '', 'b': None}})

    assert text == '대기'
