"""「지금 움직이는가」의 주인은 하나다 · §6-165

**같은 목록이 다섯 파일에 손으로 적혀 있었다.**

    bridge_helpers.py            midi_control_node.py
    playback_session.py (둘)     recording_session.py

전부 `{'running', 'verifying'}` 라고 직접 썼다 · 새 상태가 하나 생기면 다섯
곳을 고쳐야 하고, 빠뜨린 곳만 조용히 틀린다 · 틀린 화면은 「모션 동작 중」을
안 띄우거나 테이크 시계를 잘못 센다 — 터지지 않으니 한참 모른다.

**비슷하지만 다른 판정이 둘 더 있다** · 합치면 안 된다.

    is_running   「멈춰 있지 않다」 · initializing·countdown 포함 (더 넓다)
    is_moving    「축이 실제로 움직인다」 · 여기

    playback_session.py  '이미 시작했거나 끝났다' · preparing·countdown 의 반대
    group_session.py     '회차를 끝까지 둬도 되나' · motion_completed 포함

셋은 서로 다른 질문이다 · 하나로 뭉치면 그게 다음 버그가 된다.
"""

import re
from pathlib import Path

import pytest

from motion_common import run_state

WORKSPACE = Path(__file__).resolve().parents[3]

#: 이 판정을 쓰는 곳 · 여기 손으로 적은 목록이 다시 생기면 안 된다
USERS = [
    'src/motion_web/web_bridge/motion_web_bridge/bridge_helpers.py',
    'src/motion_control_studio/motion_control/midi_control/midi_control/midi_control_node.py',
    'src/motion_control_studio/motion_studio/motion_studio/playback_session.py',
    'src/motion_control_studio/motion_studio/motion_studio/recording_session.py',
]


@pytest.mark.parametrize('state', ['running', 'verifying', 'RUNNING', ' running '])
def test_moving_states_are_moving(state):
    assert run_state.is_moving(state) is True


@pytest.mark.parametrize('state', [
    'initializing',   # 초기 위치로 가는 중 · 모션은 아직 시작 안 했다
    'countdown',      # 녹화 카운트다운
    'stopped', 'completed', 'idle', 'error', '', None,
])
def test_everything_else_is_not_moving(state):
    assert run_state.is_moving(state) is False


def test_moving_is_narrower_than_running():
    """둘을 헷갈리면 초기 이동 중에 「모션 동작 중」이라고 쓴다."""
    assert run_state.is_running('initializing') is True
    assert run_state.is_moving('initializing') is False
    assert run_state.MOVING_STATES.isdisjoint(run_state.IDLE_STATES)


def test_an_unknown_state_is_not_moving():
    """`is_running` 과 반대다 · 모를 때 「움직인다」고 하면 없는 진행을 그린다."""
    assert run_state.is_moving('무슨상태') is False
    assert run_state.is_running('무슨상태') is True


@pytest.mark.parametrize('path', USERS, ids=lambda p: Path(p).name)
def test_nobody_writes_the_list_by_hand_again(path):
    """목록을 다시 손으로 적으면 주인이 둘이 된다."""
    source = (WORKSPACE / path).read_text(encoding='utf-8')
    handwritten = re.findall(
        r"\{\s*'(?:running|verifying)'\s*,\s*'(?:running|verifying)'\s*\}", source
    )
    assert handwritten == [], (
        f'{Path(path).name} 이 상태 목록을 직접 적고 있습니다 · '
        'run_state.is_moving() 을 쓰세요'
    )


@pytest.mark.parametrize('path', USERS, ids=lambda p: Path(p).name)
def test_every_user_asks_the_owner(path):
    """부르지 않으면 이 시험은 빈 껍데기다."""
    source = (WORKSPACE / path).read_text(encoding='utf-8')
    assert 'run_state_rules' in source, (
        f'{Path(path).name} 이 판정의 주인을 부르지 않습니다'
    )


def test_the_other_two_questions_stay_separate():
    """다른 질문까지 끌어다 붙이면 그게 다음 버그다.

    `playback_session` 의 「이미 시작했거나 끝났다」와 `group_session` 의
    「회차를 끝까지 둬도 되나」는 여기서 답할 수 없다 · 그대로 둔다.
    """
    playback = (
        WORKSPACE
        / 'src/motion_control_studio/motion_studio/motion_studio/playback_session.py'
    ).read_text(encoding='utf-8')
    group = (
        WORKSPACE
        / 'src/motion_control_studio/motion_control/motion_runtime/motion_runtime'
        / 'group_session.py'
    ).read_text(encoding='utf-8')

    assert "'completed', 'stopped', 'error'" in playback, (
        '「이미 시작했거나 끝났다」 판정이 사라졌습니다 · is_moving 으로 '
        '바꿨다면 preparing·countdown 처리가 달라집니다'
    )
    assert "'motion_completed'" in group, (
        '「회차를 끝까지 둬도 되나」 판정이 사라졌습니다 · motion_completed 가 '
        '빠지면 회차 마무리 중에 강제 정지합니다'
    )
