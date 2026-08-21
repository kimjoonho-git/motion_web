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
    assert topics.MOTION_RUN_COMMAND == '/motion_control/motion_run_command'


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
