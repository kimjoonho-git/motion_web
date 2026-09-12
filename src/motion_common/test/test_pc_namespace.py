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


def _topics(namespace=None):
    """이름공간을 주고 모듈을 새로 읽는다 · 상수는 읽을 때 정해진다."""
    previous = os.environ.get('MOTION_PC_NAMESPACE')
    if namespace is None:
        os.environ.pop('MOTION_PC_NAMESPACE', None)
    else:
        os.environ['MOTION_PC_NAMESPACE'] = namespace
    try:
        from motion_common import topics
        return importlib.reload(topics)
    finally:
        if previous is None:
            os.environ.pop('MOTION_PC_NAMESPACE', None)
        else:
            os.environ['MOTION_PC_NAMESPACE'] = previous


#: 지금 도는 시스템이 쓰는 이름 · 하나라도 달라지면 통신이 끊긴다
HISTORICAL = {
    'MOTION_STATE': '/motion_control/motion_state',
    'MOTOR_STATUS': '/motion_control/motor_status',
    'MOTOR_COMMAND': '/motion_control/motor_command',
    'MOTION_RUN_REQUEST': '/motion_control/motion_run_request',
    'MOTION_RUN_STATUS': '/motion_control/motion_run_status',
    'MIDI_POSITION_REQUEST': '/motion_control/midi_position_request',
    'SAFETY_REQUEST': '/motion_control/safety_request',
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
