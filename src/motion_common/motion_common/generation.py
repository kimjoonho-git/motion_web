"""프로젝트 세대 검증 단일 구현.

프로젝트를 바꾸면 세대 번호가 오른다. 이전 세대의 요청·응답이 뒤늦게 도착해
새 프로젝트의 모터를 움직이는 일을 막는 것이 이 검증의 목적이다.

같은 검증이 네 노드에 복제돼 있었고, 요청 식별자에 세대를 박아 넣는 형식
(``{접두사}-g{세대}-{꼬리}``)도 네 곳에서 각자 문자열로 조립하고 있었다.
형식이 한 곳만 어긋나면 응답이 조용히 버려진다. 여기서 한 번만 정의한다.
"""

from __future__ import annotations

import time
from typing import Any, Iterable, Mapping, Optional

__all__ = [
    'CONTEXT_COMMANDS',
    'generation_marker',
    'new_request_id',
    'payload_generation',
    'request_id_matches',
    'response_matches',
    'validate_request_generation',
]

#: 실행 컨텍스트를 새로 세우는 명령 · 이때만 세대가 올라갈 수 있다.
#: `midi_control`은 `select_project`를 쓴다 · 호출부가 자기 집합을 넘긴다.
CONTEXT_COMMANDS = frozenset({'apply_context', 'invalidate_context'})


# --------------------------------------------------------------------------- #
# 요청 식별자
# --------------------------------------------------------------------------- #

def generation_marker(generation: Any) -> str:
    """요청 식별자에 박히는 세대 표식 · ``-g12-``."""
    return f'-g{int(generation)}-'


def new_request_id(prefix: str, generation: Any, suffix: Optional[Any] = None) -> str:
    """세대가 박힌 요청 식별자를 만든다.

    ``suffix``를 주지 않으면 나노초 시각을 쓴다. 채널 번호처럼 호출부가 정하는
    값이 있으면 그것을 넘긴다.
    """
    tail = time.time_ns() if suffix is None else suffix
    return f'{prefix}-g{int(generation)}-{tail}'


def request_id_matches(request_id: Any, generation: Any) -> bool:
    """요청 식별자가 주어진 세대의 것인지 판정한다."""
    try:
        marker = generation_marker(generation)
    except (TypeError, ValueError):
        return False
    return marker in str(request_id or '')


# --------------------------------------------------------------------------- #
# 페이로드 검사
# --------------------------------------------------------------------------- #

def payload_generation(payload: Any) -> Optional[int]:
    """페이로드에서 세대 번호를 꺼낸다. 없거나 정수가 아니면 ``None``."""
    if not isinstance(payload, Mapping):
        return None
    try:
        return int(payload.get('project_generation'))
    except (TypeError, ValueError):
        return None


def response_matches(payload: Any, generation: Any) -> bool:
    """응답이 현재 세대의 것인지 판정한다.

    세대 번호와 요청 식별자 표식을 모두 확인한다. 둘 중 하나만 보면 형식이
    어긋난 응답이 통과한다.
    """
    if not isinstance(payload, Mapping):
        return False
    value = payload_generation(payload)
    if value is None:
        return False
    try:
        expected = int(generation)
    except (TypeError, ValueError):
        return False
    return value == expected and request_id_matches(payload.get('request_id'), expected)


# --------------------------------------------------------------------------- #
# 요청 검증
# --------------------------------------------------------------------------- #

def validate_request_generation(
    request_generation: Any,
    payload: Any,
    *,
    current_generation: Any,
    advances_context: bool,
) -> int:
    """요청의 세대 번호를 검증하고 확정된 값을 돌려준다.

    실패하면 ``ValueError``를 올린다. 호출부는 이 값을 자기 상태에 반영할지
    스스로 결정한다 — 이 함수는 상태를 바꾸지 않는다.

    ``advances_context``가 참이면 현재 세대보다 앞선 요청을 받아들인다.
    실행 컨텍스트를 새로 세우는 명령만 그렇게 다룬다.
    """
    try:
        generation = int(request_generation)
        payload_value = int(payload.get('project_generation'))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError('프로젝트 세대 번호가 필요합니다') from exc

    if generation < 1 or payload_value != generation:
        raise ValueError('요청의 프로젝트 세대 번호가 일치하지 않습니다')

    try:
        current = int(current_generation or 0)
    except (TypeError, ValueError):
        current = 0

    if advances_context:
        if generation < current:
            raise ValueError('이전 프로젝트 세대의 요청을 폐기했습니다')
        return generation

    if generation != current:
        raise ValueError('현재 프로젝트 세대와 다른 요청을 폐기했습니다')
    return generation


def advances_context(command: Any, commands: Iterable[str] = CONTEXT_COMMANDS) -> bool:
    """명령이 실행 컨텍스트를 새로 세우는 것인지 판정한다."""
    return str(command) in set(commands)
