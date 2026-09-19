"""밖에서 부를 것에는 밑줄을 붙이지 않는다 · §6-186

**밑줄은 「밖에서 부르지 마라」는 뜻이다.**

그런데 서비스들이 밑줄 붙은 것을 밖에서 불렀다.

    _new_project_request_id                 다섯 자리
    _ensure_project_mutation_allowed        두 자리
    _motor_runtime_control_blocker          한 자리
    _coordination_execution_blocker         한 자리
    _establish_project_generation_boundary  두 자리

부르는 쪽이 규칙을 어기고 있으면 **규칙이 없는 것과 같다** · 어느 것이 정말
속살이고 어느 것이 그냥 이름을 안 고친 것인지 알 수 없다 · 그러면 브리지를
고칠 때 무엇을 깨뜨릴지 모른다.

그리고 하나는 밖에서 **쓰고** 있었다 · `motor_config_service` 가
`bridge._motion_run_status` 에 직접 적었고, 자물쇠가 없을 때를 위한 **같은
블록이 한 벌 더** 있었다 · 두 벌이 조금씩 달라지면 「정지 중」이 영영 안
풀린다 · 실행 적용 해제 뒤에 모터가 잠긴 것처럼 보인다.
"""

from pathlib import Path

import pytest

SOURCES = Path(__file__).resolve().parents[1] / 'motion_web_bridge'

#: 서비스가 브리지에게 부르는 공개된 말 · 밑줄이 붙어 있으면 안 된다
PUBLIC_ON_THE_BRIDGE = [
    'new_project_request_id',
    'ensure_project_mutation_allowed',
    'motor_runtime_control_blocker',
    'coordination_execution_blocker',
    'establish_project_generation_boundary',
    'settle_stopping_run_state',
    'execution_context_status',
    'managed_context_nodes',
    'motion_state',
    'motion_state_with_time',
    'fresh_motion_state',
    'changing_project',
    'select_motor_axes_file',
    'mark_project_selected',
    'reconcile_execution_context',
    'current_project_generation',
]

SERVICES = [
    'project_service.py',
    'scan_orchestrator.py',
    'execution_context_service.py',
    'manual_motor_commands.py',
    'motor_config_service.py',
    'motor_runtime_service.py',
]


def _read(name: str) -> str:
    return (SOURCES / name).read_text(encoding='utf-8')


@pytest.mark.parametrize('name', PUBLIC_ON_THE_BRIDGE)
def test_the_bridge_actually_offers_it(name):
    """목록에 적었는데 브리지에 없으면 목록이 거짓말이다."""
    bridge = _read('bridge_node.py')

    assert f'    def {name}(' in bridge, f'브리지에 {name}() 가 없습니다'


@pytest.mark.parametrize('name', SERVICES)
def test_no_service_calls_a_private_name(name):
    """밑줄 붙은 것을 밖에서 부르면 규칙이 없는 것과 같다."""
    offenders = [
        f'{number}: {line.strip()}'
        for number, line in enumerate(_read(name).splitlines(), 1)
        if 'self.bridge._' in line
    ]

    assert offenders == [], (
        f'{name} 이 브리지 속살을 부릅니다:\n  ' + '\n  '.join(offenders)
    )


# --------------------------------------------------------------------------- #
# 정지 매듭짓기 · 두 벌이 한 벌로
# --------------------------------------------------------------------------- #

class _Bridge:
    """브리지 대역 · 진짜 메서드를 빌려 쓴다."""

    from motion_web_bridge.bridge_node import MotionWebBridge as _Real
    settle_stopping_run_state = _Real.settle_stopping_run_state

    def __init__(self, state, *, with_lock=True):
        self._motion_run_status = state
        if with_lock:
            import threading
            self._motion_run_lock = threading.Lock()


def test_it_settles_only_when_it_was_stopping():
    bridge = _Bridge({'state': 'stopping', 'axis': 3})

    assert bridge.settle_stopping_run_state('정리했습니다') is True
    assert bridge._motion_run_status['state'] == 'stopped'
    assert bridge._motion_run_status['message'] == '정리했습니다'
    assert bridge._motion_run_status['axis'] == 3, '나머지 값이 날아갔습니다'


@pytest.mark.parametrize('state', ['running', 'stopped', 'idle', ''])
def test_it_leaves_other_states_alone(state):
    """돌고 있는 것을 정지로 적으면 화면이 거짓말을 한다."""
    bridge = _Bridge({'state': state})

    assert bridge.settle_stopping_run_state('정리했습니다') is False
    assert bridge._motion_run_status == {'state': state}


def test_it_survives_a_bridge_that_has_no_lock_yet():
    """노드가 다 서기 전, 시험용 껍데기 · 예비는 여기 한 벌만 있다."""
    bridge = _Bridge({'state': 'stopping'}, with_lock=False)

    assert bridge.settle_stopping_run_state('정리했습니다') is True
    assert bridge._motion_run_status['state'] == 'stopped'


def test_it_survives_an_empty_status():
    bridge = _Bridge(None)

    assert bridge.settle_stopping_run_state('정리했습니다') is False


def test_the_release_message_is_written_once():
    """같은 문구가 두 곳에 있었다 · 하나만 고치면 화면이 달리 말한다."""
    source = _read('motor_config_service.py')

    assert source.count("'실행 적용 해제로 정지 상태를 정리했습니다'") == 1
    assert source.count('RELEASE_SETTLED_MESSAGE') == 3  # 정의 + 쓰는 곳 둘


def test_the_duplicated_fallback_is_gone():
    """자물쇠가 없을 때를 위한 같은 블록이 한 벌 더 있었다."""
    source = _read('motor_config_service.py')

    assert '_motion_run_lock' not in source
    assert '_motion_run_status' not in source
