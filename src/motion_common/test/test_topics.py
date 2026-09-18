"""토픽 단일 정의 검증.

노드 기본값·launch 리터럴·상대 노드 기본값 3중 정의로 갈라지던 것을 흡수한
결과라, 이 모듈이 실제로 유일한 정의 지점인지가 핵심이다.
"""

import re
from pathlib import Path

from motion_common import topics

SOURCE_ROOT = Path(__file__).resolve().parents[3]

TOPIC_LITERAL = re.compile(
    r"""['"](/(?:motion_control|motion_studio|motion_group|motion_schedule"""
    r"""|motion_web|xtouch)/[a-z_/]+)['"]"""
)


def test_every_constant_is_an_absolute_topic():
    catalog = topics.all_topics()
    assert catalog, '토픽 상수가 하나도 없다'
    for name, value in catalog.items():
        assert value.startswith('/'), f'{name}={value}'
        assert not value.endswith('/'), f'{name}={value}'
        assert ' ' not in value, f'{name}={value}'


def test_no_duplicate_topic_names():
    catalog = topics.all_topics()
    seen = {}
    for name, value in catalog.items():
        assert value not in seen, f'{name}과 {seen.get(value)}가 같은 토픽을 가리킨다'
        seen[value] = name


def test_run_command_and_motor_command_stay_distinct():
    """혼동의 근원 · 최종 하드웨어 출력과 supervisor 요청은 다른 토픽이다."""
    assert topics.MOTOR_COMMAND != topics.MOTION_RUN_COMMAND
    assert topics.MOTOR_COMMAND == '/motion_control/motor_command'
    assert topics.MOTION_RUN_COMMAND == '/motion_run/command'


def test_every_request_topic_has_a_reply_channel():
    """요청 토픽에는 응답·결과·상태 중 하나가 반드시 짝으로 있어야 한다.

    안전 계열은 요청/응답이 아니라 요청/상태 형태다 · `SAFETY_STATUS`가 짝.
    """
    catalog = topics.all_topics()
    for name in catalog:
        if not name.endswith('_REQUEST'):
            continue
        partner = name[: -len('_REQUEST')]
        assert any(
            f'{partner}_{suffix}' in catalog
            for suffix in ('RESPONSE', 'RESULT', 'STATUS')
        ), f'{name}에 대응하는 응답·결과·상태 토픽이 없다'


def _source_files():
    for path in SOURCE_ROOT.glob('src/**/*.py'):
        parts = path.parts
        if 'motion_system' in parts or 'motion_common' in parts:
            continue
        if 'build' in parts or 'install' in parts or '.pytest_cache' in parts:
            continue
        if path.name.startswith('test_') or '/test/' in str(path):
            continue
        yield path


def test_no_topic_literals_remain_outside_this_module():
    """이 모듈 밖에 토픽 문자열이 남아 있으면 정의가 다시 갈라진다."""
    offenders = []
    for path in _source_files():
        try:
            text = path.read_text(encoding='utf-8')
        except OSError:
            continue
        for match in TOPIC_LITERAL.finditer(text):
            offenders.append(f'{path.relative_to(SOURCE_ROOT)}: {match.group(1)}')
    assert not offenders, '토픽 리터럴 잔존:\n' + '\n'.join(offenders)


# 통로는 묶음 이름 아래 산다 · §6-179
#
# 모터 검색은 **시작 명령 넷이 맨 바깥**에 있고 진행률만 `motion_control/`
# 안에 있었다 · 같은 기능인데 사는 곳이 달랐다 · 다른 통로는 전부 묶음
# 이름이 붙어 있어서, 목록을 볼 때마다 이 넷만 「이건 뭐지」가 됐다.

def test_every_channel_lives_under_a_group():
    """묶음 없이 맨 바깥에 사는 통로가 없어야 한다."""
    loose = []
    for name in dir(topics):
        if not name.isupper():
            continue
        value = getattr(topics, name)
        if not isinstance(value, str) or not value.startswith('/'):
            continue
        # `/묶음/이름` 이어야 한다 · 시험에서는 PC 이름공간이 비어 있으므로
        # 빗금이 둘이면 묶음이 있는 것이다 (`/motor/scan_all`)
        if value.count('/') < 2:
            loose.append(f'{name} = {value}')
    assert loose == [], (
        '묶음 이름 없이 맨 바깥에 있는 통로가 있습니다 · '
        f'기능 이름 아래로 넣으세요:\n  ' + '\n  '.join(loose)
    )


def test_motor_scan_start_and_progress_live_together():
    """시작 명령과 진행률이 흩어져 있으면 한쪽만 옮겨진다 · 실제로 그랬다."""
    starts = [
        topics.MOTOR_SCAN_ACTION,
        topics.SCAN_MOTORS,
        topics.SCAN_AC_SERVO_MOTORS,
        topics.SCAN_DYNAMIXEL_MOTORS,
        topics.SET_MONITORING,
    ]
    for value in starts + [topics.MOTOR_SCAN_PROGRESS]:
        assert '/motor/' in value, f'모터 묶음 밖에 있습니다: {value}'
