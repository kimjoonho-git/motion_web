"""명령 요청 봉투 단일 구현.

`std_msgs/String` + JSON으로 명령을 받는 노드 네 곳이 같은 봉투를 각자 펼치고
있었다. 봉투는 이렇게 생겼다.

    JSON 해석 → dict 확인 → request_id·command·payload 추출 → 세대 검증
    → 분기 → 예외를 응답으로 → request_id·세대 되붙이기 → 발행

가운데 '분기'만 노드마다 다르고 나머지는 같다. 다른 곳을 한 군데로 모으면
노드 골격이 같아지고, 분기 표를 도입할 자리도 생긴다.

이 모듈은 전송 계약을 바꾸지 않는다 · 토픽·페이로드 형식은 그대로다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, Mapping, Optional

from . import generation as generation_mod

__all__ = ['CommandRouter', 'Request', 'error_response', 'finalize', 'parse_request']


@dataclass(frozen=True)
class Request:
    """펼쳐 놓은 명령 요청."""

    request_id: str
    command: str
    payload: Dict[str, Any]
    #: 봉투에 실려 온 세대 번호 · 아직 검증되지 않은 원본
    generation: Any = None
    raw: Mapping[str, Any] = field(default_factory=dict)


def parse_request(data: Any, *, default_command: str = '') -> Optional[Request]:
    """요청 문자열을 :class:`Request`로 펼친다.

    JSON이 아니거나 객체가 아니면 ``None``을 돌려준다. 호출부가 로그 문구를
    정해야 하므로 여기서는 로그를 남기지 않는다.
    """
    try:
        raw = json.loads(data)
    except (TypeError, ValueError):
        return None
    if not isinstance(raw, Mapping):
        return None

    payload = raw.get('payload')
    if not isinstance(payload, Mapping):
        payload = {}

    return Request(
        request_id=str(raw.get('request_id') or ''),
        command=str(raw.get('command') or default_command).strip(),
        payload=dict(payload),
        generation=raw.get('project_generation'),
        raw=raw,
    )


def error_response(message: str, **extra: Any) -> Dict[str, Any]:
    """실패 응답을 만든다."""
    response: Dict[str, Any] = {'success': False, 'message': str(message)}
    response.update(extra)
    return response


def finalize(response: Any, request: Request) -> Dict[str, Any]:
    """응답에 요청 식별자와 세대를 되붙인다.

    이것이 빠지면 요청한 쪽이 자기 응답을 알아보지 못하고 시간 초과로 끝난다.
    """
    if not isinstance(response, dict):
        response = error_response('명령 처리기가 응답 객체를 돌려주지 않았습니다')
    response['request_id'] = request.request_id
    response['project_generation'] = request.generation
    return response


class CommandRouter:
    """명령 이름 → 처리기 표.

    `if/elif` 사슬을 대신한다. 어떤 명령을 다루는지 표 하나만 보면 되고,
    처리기를 추가할 때 사슬 중간을 건드리지 않는다.
    """

    def __init__(
        self,
        *,
        context_commands: Iterable[str] = generation_mod.CONTEXT_COMMANDS,
    ) -> None:
        self._handlers: Dict[str, Callable[..., Any]] = {}
        self.context_commands = frozenset(context_commands)

    # ----------------------------------------------------------------- #
    # 등록
    # ----------------------------------------------------------------- #

    def register(self, command: str, handler: Callable[..., Any]) -> None:
        """처리기를 등록한다. 같은 명령을 두 번 등록하면 실수이므로 막는다."""
        key = str(command)
        if key in self._handlers:
            raise ValueError(f'명령 처리기가 중복 등록되었습니다: {key}')
        self._handlers[key] = handler

    def handler(self, *commands: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        """등록 데코레이터 · 한 처리기에 여러 명령을 붙일 수 있다."""
        def decorate(func: Callable[..., Any]) -> Callable[..., Any]:
            for command in commands:
                self.register(command, func)
            return func
        return decorate

    # ----------------------------------------------------------------- #
    # 조회
    # ----------------------------------------------------------------- #

    def handles(self, command: str) -> bool:
        return str(command) in self._handlers

    def resolve(self, command: str) -> Optional[Callable[..., Any]]:
        return self._handlers.get(str(command))

    def commands(self) -> frozenset:
        """등록된 명령 전체 · 진단·문서화용."""
        return frozenset(self._handlers)

    def advances_context(self, command: str) -> bool:
        return str(command) in self.context_commands

    def __contains__(self, command: object) -> bool:
        return str(command) in self._handlers

    def __len__(self) -> int:
        return len(self._handlers)
