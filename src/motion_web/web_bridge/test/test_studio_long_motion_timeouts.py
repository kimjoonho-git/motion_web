"""10분 모션이 제한 시간에 걸려 죽지 않는다 · §6-114

30,000프레임(10분·20ms) 프로젝트를 직접 재어 본 값:

    프로젝트 저장   1.1초 · 디스크 10.3MB
    프로젝트 읽기   0.7초
    응답 직렬화     0.2초 · 6.5MB
    포인트 생성     축 하나에 2.7~4.9초
    포인트 편집     1.2초

**시간 초과가 곧 취소는 아니다** · 노드는 하던 일을 끝내고 저장한다 · 화면에만
"응답 시간 초과" 가 뜨고 실제로는 바뀌어 있는, 가장 나쁜 모양이 된다.
"""

import re
from pathlib import Path

from motion_web_bridge.motion_studio_bridge import (
    STUDIO_EDITOR_TIMEOUT_SEC,
    STUDIO_REQUEST_TIMEOUT_SEC,
)

MODULE_DIR = Path(__file__).resolve().parents[1] / 'motion_web_bridge'
ROUTES = (MODULE_DIR / 'motion_studio_routes.py').read_text(encoding='utf-8')
SYNC = (MODULE_DIR / 'motion_studio_sync.py').read_text(encoding='utf-8')
TRANSPORT = (MODULE_DIR / 'motion_studio_bridge.py').read_text(encoding='utf-8')

#: 10분 프로젝트 저장 1.1초 + 읽기 0.7초 + 직렬화 0.2초 · 느린 PC 의 몇 배까지
MEASURED_WRITE_SEC = 2.0
#: 10분 모션 축 하나 포인트 생성 4.9초
MEASURED_EDIT_SEC = 5.0


def test_write_requests_allow_several_times_the_measured_cost():
    assert STUDIO_REQUEST_TIMEOUT_SEC >= MEASURED_WRITE_SEC * 5


def test_editor_requests_allow_several_times_the_measured_cost():
    assert STUDIO_EDITOR_TIMEOUT_SEC >= MEASURED_EDIT_SEC * 5


def test_no_studio_call_hard_codes_a_short_timeout():
    """숫자를 직접 박으면 여기 값을 올려도 그 자리만 짧게 남는다."""
    for name, source in (('routes', ROUTES), ('sync', SYNC), ('transport', TRANSPORT)):
        for match in re.finditer(r'timeout_sec(?:\s*[:=]\s*(?:float\s*=\s*)?)([0-9.]+)', source):
            value = float(match.group(1))
            assert value >= MEASURED_WRITE_SEC * 5, (
                f'{name} 에 {value}초짜리 제한이 남아 있다'
            )


def test_every_studio_write_route_uses_the_shared_values():
    assert 'timeout_sec=STUDIO_REQUEST_TIMEOUT_SEC' in ROUTES
    assert 'STUDIO_EDITOR_TIMEOUT_SEC' in ROUTES
