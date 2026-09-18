"""PC 이름공간 · §6-95

여러 PC 가 한 DDS 망에 있으면 같은 토픽 이름이 부딪힌다 · PC1 의
`/xtouch/midi` 와 PC2 의 것이 구별되지 않는다.

**이 PC 것**에는 접두사를 붙이고, **그룹 공용**에는 붙이지 않는다 · 그게 PC 끼리
만나는 자리이기 때문이다.

가장 중요한 것은 **켜지 않으면 아무것도 안 바뀐다** 는 것이다 · 지금 도는
시스템의 토픽 이름이 글자 하나라도 달라지면 조용히 통신이 끊긴다.
"""

import importlib
import os

import pytest


@pytest.fixture(autouse=True)
def _restore_topics():
    """이 파일은 토픽 모듈을 이름공간째로 다시 읽는다 · 끝나면 **되돌려 놔야**
    한다 · 안 그러면 다음 검사가 접두사 붙은 이름을 보고 엉뚱하게 실패한다.

    실제로 그랬다 · `test_topics.py` 가 혼자서는 통과하는데 전체로 돌리면
    실패했다.
    """
    previous = os.environ.get('MOTION_PC_NAMESPACE')
    yield
    if previous is None:
        os.environ.pop('MOTION_PC_NAMESPACE', None)
    else:
        os.environ['MOTION_PC_NAMESPACE'] = previous
    from motion_common import topics
    importlib.reload(topics)


def _topics(namespace=None):
    """이름공간을 주고 모듈을 새로 읽는다 · 상수는 읽을 때 정해진다."""
    if namespace is None:
        os.environ.pop('MOTION_PC_NAMESPACE', None)
    else:
        os.environ['MOTION_PC_NAMESPACE'] = namespace
    from motion_common import topics
    return importlib.reload(topics)


#: 지금 도는 시스템이 쓰는 이름 · 하나라도 달라지면 통신이 끊긴다
HISTORICAL = {
    'MOTION_STATE': '/motor/state',
    'MOTOR_STATUS': '/motion_control/motor_status',
    'MOTOR_COMMAND': '/motion_control/motor_command',
    'MOTION_RUN_REQUEST': '/motion_run/request',
    'MOTION_RUN_STATUS': '/motion_run/status',
    'MIDI_POSITION_REQUEST': '/midi/position_request',
    'SAFETY_REQUEST': '/safety/request',
    'STUDIO_REQUEST': '/motion_studio/request',
    'STUDIO_STATUS': '/motion_studio/status',
    'MIDI_MONITOR_STATE': '/motion_web/midi_monitor/state',
    'XTOUCH_MIDI': '/xtouch/midi',
    'XTOUCH_FEEDBACK': '/xtouch/feedback',
    'SCHEDULE_STATUS': '/motion_schedule/status',
    'GROUP_HEARTBEAT': '/motion_group/heartbeat',
    'GROUP_COMMAND': '/motion_group/command',
}


def test_without_a_namespace_nothing_changes():
    """이 검사가 이 변경의 전부다 · 켜지 않은 시스템은 글자 하나 안 바뀐다."""
    topics = _topics(None)
    for name, expected in HISTORICAL.items():
        assert getattr(topics, name) == expected, f'{name} 이 달라졌다'


def test_every_topic_still_starts_with_a_slash():
    topics = _topics(None)
    for name in dir(topics):
        if not name.isupper() or not isinstance(getattr(topics, name), str):
            continue
        value = getattr(topics, name)
        if value.startswith('/'):
            continue
        pytest.fail(f'{name} 이 토픽 이름 같지 않다 · {value}')


# --------------------------------------------------------------------- #
# 이름공간을 켰을 때
# --------------------------------------------------------------------- #

def test_this_pc_topics_get_the_prefix():
    topics = _topics('pc1')
    assert topics.XTOUCH_MIDI == '/pc1/xtouch/midi'
    assert topics.MOTOR_COMMAND == '/pc1/motion_control/motor_command'
    assert topics.STUDIO_STATUS == '/pc1/motion_studio/status'


def test_group_topics_never_get_the_prefix():
    """그룹 토픽은 PC 끼리 만나는 자리다 · 접두사가 붙으면 서로 못 만난다."""
    topics = _topics('pc1')
    assert topics.GROUP_HEARTBEAT == '/motion_group/heartbeat'
    assert topics.GROUP_COMMAND == '/motion_group/command'
    assert topics.GROUP_ALARM == '/motion_group/alarm'
    assert topics.GROUP_TIME_SYNC == '/motion_group/time_sync'
    assert topics.GROUP_EVENT == '/motion_group/event'
    assert topics.GROUP_SYSTEM_INFO == '/motion_group/system_info'


def test_two_pcs_never_collide():
    """같은 토픽이 PC 마다 달라야 한다 · 그게 이름공간의 목적이다."""
    first = _topics('pc1').XTOUCH_MIDI
    second = _topics('pc2').XTOUCH_MIDI
    assert first != second
    assert _topics(None).XTOUCH_MIDI not in {first, second}


@pytest.mark.parametrize('given,expected', [
    ('pc1', '/pc1/xtouch/midi'),
    ('/pc1', '/pc1/xtouch/midi'),
    ('/pc1/', '/pc1/xtouch/midi'),
    ('  pc1  ', '/pc1/xtouch/midi'),
    ('', '/xtouch/midi'),
    ('   ', '/xtouch/midi'),
])
def test_the_namespace_is_tidied_before_use(given, expected):
    """앞뒤 빗금이나 공백 때문에 `//pc1//xtouch` 가 되면 안 된다."""
    assert _topics(given).XTOUCH_MIDI == expected


# --------------------------------------------------------------------- #
# 토픽 이름으로 쓸 수 없는 값 · 호스트 이름을 그대로 넣는 일이 흔하다
# --------------------------------------------------------------------- #

@pytest.mark.parametrize('given', ['pc-1', 'pc.2', 'pc 3', 'pc@4'])
def test_characters_a_topic_name_cannot_hold_are_replaced(given):
    """하이픈이나 점이 들어가면 **아무 말 없이 통신이 안 된다**."""
    value = _topics(given).XTOUCH_MIDI
    assert value.startswith('/pc_')
    body = value[1:].split('/')[0]
    assert body.replace('_', '').isalnum()


def test_a_name_starting_with_a_digit_is_fixed():
    """토픽 이름은 숫자로 시작할 수 없다."""
    assert _topics('2호기').XTOUCH_MIDI.startswith('/pc_2')


def test_an_unusable_name_never_silently_drops_the_namespace():
    """쓸 수 있는 글자가 하나도 안 남아도 빈 값으로 두면 안 된다 ·
    공유망에서 이름표가 없어지면 다른 PC 와 토픽이 부딪힌다."""
    value = _topics('한글이름').XTOUCH_MIDI
    assert value != '/xtouch/midi', '이름표가 조용히 사라졌다'
    assert value.startswith('/pc_')


def test_the_fallback_is_the_same_every_restart():
    """다시 켤 때마다 달라지면 토픽 이름이 바뀌어 아무도 못 찾는다."""
    first = _topics('한글이름').XTOUCH_MIDI
    second = _topics('한글이름').XTOUCH_MIDI
    assert first == second


def test_two_unusable_names_still_differ():
    assert _topics('한글이름').XTOUCH_MIDI != _topics('다른이름').XTOUCH_MIDI


def test_scan_and_monitoring_services_carry_the_pc_nameplate():
    """모터 검색·감시 요청에도 이름표가 붙어야 한다 · §6-103

    이것들만 전역(`/motor_scan`)이라 세 PC 가 같은 이름으로 등록했다:

        Action: /motor_scan
        Action servers: 3

    피시1에서 누른 검색을 피시3이 받아, 피시3의 EtherCAT 상태로
    "Master 사용 중" 을 돌려줬다. 검색은 **버스를 다시 훑고 모터 서비스를
    껐다 켠다** · 남의 PC 하드웨어를 건드릴 수 있었다.

    진행률(`MOTOR_SCAN_PROGRESS`)은 처음부터 이름표가 있었다 · 요청 쪽만
    빠져서 한쪽만 어긋나 있었다.
    """
    scoped = _topics('pc1')
    for name in (
        'MOTOR_SCAN_ACTION',
        'SCAN_MOTORS',
        'SCAN_AC_SERVO_MOTORS',
        'SCAN_DYNAMIXEL_MOTORS',
        'SET_MONITORING',
    ):
        assert getattr(scoped, name).startswith('/pc1/'), f'{name} 에 이름표가 없다'


def test_scan_services_keep_their_old_names_when_the_nameplate_is_off():
    """켜지 않은 시스템은 글자 하나도 달라지면 안 된다."""
    plain = _topics('')
    assert plain.MOTOR_SCAN_ACTION == '/motor_scan'
    assert plain.SCAN_MOTORS == '/scan_motors'
    assert plain.SCAN_AC_SERVO_MOTORS == '/scan_ac_servo_motors'
    assert plain.SCAN_DYNAMIXEL_MOTORS == '/scan_dynamixel_motors'
    assert plain.SET_MONITORING == '/set_monitoring'
