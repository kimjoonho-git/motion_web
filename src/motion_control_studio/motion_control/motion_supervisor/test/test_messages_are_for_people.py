"""거절 문구는 사람이 읽는 말이다 · §6-178

**영어 문구가 그대로 화면에 나왔다.**

조그를 거절당한 사람이 `motionTestActualText` 에서 이런 것을 봤다.

    Axis 0 is not detected
    Axis 99 not found in current motion_state
    emergency stop is latched; restart the full program

같은 상황에 화면은 한글로, 서버는 영어로 말했다 · 사용자는 두 가지 말을
번갈아 보게 됐다.

**그리고 같은 문장이 여러 곳에 적혀 있었다.**

    긴급정지    일곱 곳
    수동 명령   네 곳

한 곳만 고치면 같은 상황에 두 가지 말이 나온다 · 그래서 주인을 만들었다.

2026-09-18 에 축 관련 문구 77곳을 한글로 옮겼다.
"""

import re
from pathlib import Path

import pytest

from motion_supervisor import supervisor_node

WORKSPACE = Path(__file__).resolve().parents[5]

#: 사용자에게 보이는 문구를 만드는 곳 · 여기 영어가 있으면 화면에 나온다
USER_FACING = [
    'src/motion_control_studio/motion_control/motion_supervisor/motion_supervisor/supervisor_node.py',
    'src/motion_web/web_bridge/motion_web_bridge/manual_motor_commands.py',
    'src/motion_web/web_bridge/motion_web_bridge/motor_profile_validation.py',
    'src/motion_common/motion_common/motor_readiness.py',
]

#: `Axis {n}` 은 이제 `{n}번 축` 이다
AXIS_PREFIX = re.compile(r"f'Axis \{")


@pytest.mark.parametrize('path', USER_FACING, ids=lambda p: Path(p).name)
def test_no_axis_message_is_english_anymore(path):
    source = (WORKSPACE / path).read_text(encoding='utf-8')
    found = [
        source[:m.start()].count('\n') + 1 for m in AXIS_PREFIX.finditer(source)
    ]
    assert found == [], (
        f'{Path(path).name} 에 영어 축 안내가 남아 있습니다 (줄 {found}) · '
        '`{n}번 축` 으로 쓰세요'
    )


def test_the_emergency_sentence_has_one_owner():
    """일곱 곳에 적혀 있었다 · 한 곳만 고치면 말이 갈린다."""
    source = (WORKSPACE / USER_FACING[0]).read_text(encoding='utf-8')

    assert supervisor_node.EMERGENCY_LATCHED_MESSAGE.startswith('긴급정지')
    # 상수 정의 한 줄 말고는 글자로 적힌 곳이 없어야 한다
    assert source.count("'긴급정지가 걸려 있습니다") == 1
    assert source.count('EMERGENCY_LATCHED_MESSAGE') >= 7


def test_the_manual_command_sentence_has_one_owner():
    source = (WORKSPACE / USER_FACING[0]).read_text(encoding='utf-8')

    assert supervisor_node.MANUAL_COMMAND_ACTIVE_MESSAGE == '수동 명령이 실행 중입니다'
    assert source.count("'수동 명령이 실행 중입니다'") == 1
    assert source.count('MANUAL_COMMAND_ACTIVE_MESSAGE') >= 5


def test_the_owner_really_answers_with_it():
    """상수만 두고 안 쓰면 소용없다 · 실제로 그 말이 나오는지."""
    reason = supervisor_node.motion_run_rejection_reason(
        motor_state_available=True, manual_command_active=False, emergency_latched=True,
    )
    assert reason == supervisor_node.EMERGENCY_LATCHED_MESSAGE

    reason = supervisor_node.motion_run_rejection_reason(
        motor_state_available=True, manual_command_active=True,
    )
    assert reason == supervisor_node.MANUAL_COMMAND_ACTIVE_MESSAGE


def test_nothing_blocks_when_all_is_well():
    """막을 것만 막아야 한다 · 늘 막으면 아무것도 못 돈다."""
    assert supervisor_node.motion_run_rejection_reason(
        motor_state_available=True, manual_command_active=False,
    ) is None


def test_the_other_blockers_speak_korean_too():
    for kwargs in (
        {'motor_state_available': False, 'manual_command_active': False},
        {'motor_state_available': True, 'manual_command_active': False,
         'midi_command_active': True},
    ):
        reason = supervisor_node.motion_run_rejection_reason(**kwargs)
        assert reason and re.search(r'[가-힣]', reason), (
            f'영어로 답하고 있습니다: {reason!r}'
        )
