"""축별 판정이 서버와 화면에서 갈리지 않는다 · §6-177

**같은 판정이 두 곳에 있다 · 없앨 수가 없다.**

    서버   `motor_readiness.readiness_error` · 명령을 받고 「실행해도 되나」
    화면   `motion_test.js` · 그리면서 「이 버튼을 켜도 되나」

화면 것을 없애고 서버 답을 받아쓰려 했지만 못 한다 · 사유를 계산하려면
**모터 종류**를 판단해야 하는데, 그 판단은 일부러 호출부마다 다르게 두었다.

    모터 종류 판정과 내부리밋 판정은 호출부가 한다 · 노드마다 판정 근거가
    다르고 (`_is_ac_servo` · `motor_config_rules.is_ac_servo_motor` ·
    매핑의 `motor_type`), 그것까지 여기로 끌어오면 이 모듈이 모터 모델을
    알아야 한다.                              — motor_readiness.py

브리지가 제 나름의 판단을 하나 더 만들면 **주인이 셋**이 된다 · 그게 더 나쁘다.

그래서 **없애는 대신 갈리지 않게 지킨다** · 둘은 같은 것을 같은 순서로 본다 ·
한쪽만 고치면 여기서 걸린다.

2026-09-18 에 문구는 한글로 통일했다 · 전에는 서버가 영어로 답해서, 조그를
거절당한 사람이 화면에서 `Axis 0 is not detected` 를 봤다.
"""

import re
from pathlib import Path

from motion_common import motor_readiness

WORKSPACE = Path(__file__).resolve().parents[4]
MOTION_TEST_JS = WORKSPACE / 'src/motion_web/web_ui/static/js/motion_test.js'


def _function(source: str, name: str) -> str:
    start = source.index(f'function {name}(')
    return source[start:source.index('\n}', start)]


def _screen_order(name: str):
    """화면이 무엇을 어떤 순서로 보는가."""
    body = _function(MOTION_TEST_JS.read_text(encoding='utf-8'), name)
    order = []
    for line in body.splitlines():
        if "!== 'detected'" in line:
            order.append('detected')
        elif 'servo_on !== true' in line:
            order.append('servo_on')
        elif 'motor.fault' in line:
            order.append('fault')
    return tuple(order)


def test_the_ac_servo_order_is_the_same_on_both_sides():
    """서버 `MANUAL_ORDER` 와 화면이 같은 순서여야 한다.

    순서가 갈리면 같은 고장에 **다른 사유**가 나온다 · 서보가 꺼진 채
    에러도 있는 축에서 한쪽은 「서보 꺼짐」, 다른 쪽은 「에러」라고 한다.
    """
    assert _screen_order('acServoReadyBlockReason') == motor_readiness.MANUAL_ORDER


def test_the_dynamixel_path_skips_servo_on_just_like_the_server():
    """다이나믹셀에는 서보 ON 이 없다 · 서버는 `is_ac_servo` 로 건너뛴다."""
    screen = _screen_order('dynamixelReadyBlockReason')

    assert 'servo_on' not in screen
    assert screen == tuple(
        check for check in motor_readiness.MANUAL_ORDER if check != 'servo_on'
    )


def test_the_server_really_skips_servo_on_for_dynamixel():
    """화면만 보고 믿지 않는다 · 서버가 정말 건너뛰는지 돌려 본다."""
    motor = {'controller_index': 1, 'state': 'detected', 'fault': False}

    assert motor_readiness.readiness_error(
        motor, order=motor_readiness.MANUAL_ORDER, is_ac_servo=False,
    ) == ''
    assert '서보' in motor_readiness.readiness_error(
        motor, order=motor_readiness.MANUAL_ORDER, is_ac_servo=True,
    )


def test_both_sides_speak_korean():
    """서버가 영어로 답하면 사용자가 화면에서 영어를 본다 · 실제로 그랬다."""
    motor = {'controller_index': 0, 'state': 'missing'}
    server = motor_readiness.readiness_error(motor, order=motor_readiness.MANUAL_ORDER)

    assert '감지되지 않았습니다' in server
    assert not re.search(r'[A-Za-z]{4,}', server), (
        f'서버 문구에 영어가 남아 있습니다: {server!r}'
    )


def test_the_screen_says_the_same_thing():
    """문구가 갈리면 같은 상황에 두 가지 말이 나온다."""
    source = MOTION_TEST_JS.read_text(encoding='utf-8')

    for sentence in ('감지되지 않았습니다', '에러가 있습니다', '서보가 켜진 상태가 아닙니다'):
        assert sentence in source, f'화면에서 사라진 안내: {sentence}'


def test_jog_and_absolute_move_share_one_function():
    """둘이 갈라지면 한쪽에만 검사가 붙는다 · §6-172 에서 합쳤다."""
    source = MOTION_TEST_JS.read_text(encoding='utf-8')

    assert 'function axisActionBlockReason(motor, actionText)' in source
    for name in ('jogBlockReason', 'actionBlockReason'):
        body = _function(source, name)
        assert 'axisActionBlockReason(' in body, f'{name} 이 따로 판단합니다'
