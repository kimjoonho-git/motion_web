"""수치 변환 단일 구현.

노드마다 같은 변환 함수를 다시 정의하면 미세한 차이(유한성 검사 누락, 진법 해석
차이)가 조용히 갈라진다. 같은 계약을 쓰는 곳은 모두 이 모듈을 경유한다.

의도적으로 흡수하지 않은 변형은 `docs/ARCHITECTURE_REVIEW.md` §6-5 참조.
"""

from __future__ import annotations

import math
from typing import Any, Optional

__all__ = ['finite_float', 'optional_float', 'optional_int']


def optional_float(value: Any, default: Optional[float] = None) -> Optional[float]:
    """유한 실수로 변환한다. 변환 불가·비유한이면 ``default``.

    빈 문자열과 ``None``은 '값 없음'으로 보고 변환을 시도하지 않는다.
    ``inf``·``nan``은 계산을 오염시키므로 실패로 처리한다.
    """
    if value is None or value == '':
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def finite_float(value: Any) -> Optional[float]:
    """``optional_float(value, None)``의 축약형."""
    return optional_float(value, None)


def optional_int(value: Any, default: Optional[int] = None) -> Optional[int]:
    """정수로 변환한다. 변환 불가면 ``default``.

    진법 접두사를 해석한다(``'0x10'`` → 16). 모터 레지스터 주소·ID가 설정
    파일에 16진 문자열로 들어오는 경우가 있어 10진 전용 변환으로는 부족하다.
    소수 표기(``'3.7'``)는 절사하지 않고 실패로 처리한다.
    """
    if value is None or value == '':
        return default
    try:
        return int(str(value), 0)
    except (TypeError, ValueError):
        return default
