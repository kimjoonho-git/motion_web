"""이 PC 의 모션이 지금 돌고 있는가 · 이 판단의 주인 · §6-136

스케줄 점검은 1분마다 "돌아야 하는데 안 도나 · 멈춰야 하는데 도나" 를 본다 ·
그러려면 **돌고 있다**의 뜻이 하나여야 한다.

돌고 있는 상태를 늘어놓으면 목록이 길고, 새 단계가 생길 때마다 여기를 고쳐야
한다 · 빠뜨리면 점검이 "안 돈다"로 잘못 읽고 이미 도는 모션을 또 시작시킨다.

그래서 **멈춰 있는 상태만** 적는다 · 이쪽이 짧고 잘 안 늘어난다 · 모르는
상태는 "돌고 있다"로 본다 · 또 시작시키는 것보다 가만두는 쪽이 안전하다.

그룹 실행도 이 PC 의 모션 실행을 쓴다 (`local_execution_blocker` 참고) ·
그래서 연동이든 단독이든 여기 한 곳으로 답이 나온다.
"""

#: 아무것도 돌지 않는 상태 · 여기 없으면 도는 중으로 본다
#:
#: 빈 값은 여기 **없다** · 「멈춰 있다」가 아니라 「못 읽었다」이고, 못 읽었으면
#: 가만두는 쪽이 안전하다 · 실제로 멈춰 있으면 `idle` 이 온다.
IDLE_STATES = frozenset({
    'idle',
    'off',
    'ready',
    'stopped',
    'completed',
    'motion_completed',
    'error',
    'blocked',
})


def is_running(state) -> bool:
    """이 상태를 「돌고 있다」로 볼 것인가.

    모르는 상태는 돌고 있는 것으로 본다 · 도는 모션을 또 시작시키면
    "previous motion run task is still running" 으로 막히거나, 더 나쁘면
    모터가 두 명령을 받는다.
    """
    return str(state or '').strip().lower() not in IDLE_STATES


def is_idle(state) -> bool:
    return not is_running(state)


#: 그룹 실행이 살아 있는 단계 · §6-145
#:
#: 여기는 **살아 있는 쪽**을 적는다 · 로컬(`IDLE_STATES`)과 반대다 · 그룹은
#: `execution_id` 가 함께 와서 「없으면 안 도는 것」이 분명하기 때문이다.
#:
#: 전에는 이 목록이 두 곳에 따로 있었다 (`coordination_bridge.py` 와
#: `coordination.js`) · 스케줄 점검이 세 번째로 쓸 뻔했다.
GROUP_ACTIVE_STATES = frozenset({
    'preparing',
    'initializing',
    'armed',
    'start_scheduled',
    'waiting',
    'running',
    'waiting_cycle_ready',
    'cycle_ready',
    'stop_after_cycle',
    'releasing',
})


def group_is_active(execution) -> bool:
    """그룹 실행이 지금 이 PC 의 모션 실행을 쓰고 있는가.

    **준비 단계도 포함한다** · 마스터가 신호를 보내고 각 PC 가 응답하고 시각을
    맞추는 동안, 이 PC 의 로컬 모션은 아직 `stopped` 다 · 그 틈을 「멈춤」으로
    읽으면 스케줄 점검이 이미 시작된 그룹 실행을 또 시작시킨다 · §6-145

    `execution_id` 가 비면 끝난 것이다 · 상태만 보고 판단하지 않는다.
    """
    if not isinstance(execution, dict):
        return False
    if not str(execution.get('execution_id') or '').strip():
        return False
    return str(execution.get('state') or '').strip().lower() in GROUP_ACTIVE_STATES
