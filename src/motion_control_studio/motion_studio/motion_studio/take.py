"""한 번의 작업 · 테이크 · §6-80

사용자가 버튼 하나를 누르면 테이크가 하나 선다 · 녹화든 추가 녹화든 미리보기든
초기 위치 이동이든, 끝날 때까지 **자기가 무엇인지 안다**.

전에는 그 자리를 공유 딕셔너리 안의 문자열 하나(`_status['state']`)가 맡았다.
문자열은 자기가 무엇인지 모른다 · 실행 노드가 `running` 을 보내자 녹화 중이던
화면이 "미리보기 재생"으로 바뀌었고, 재생이 끝나는 순간 녹화까지 함께 끝났다.
막고 있던 것은 조건문 하나뿐이었다 · §6-76

이제 바꿀 수 있는 것은 **단계(phase)** 뿐이다 · `kind` 는 만들 때 정해지고 끝날
때까지 그대로다. 녹화 테이크가 재생이라고 말하는 일이 **구조적으로 불가능하다**.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Dict, Mapping, Optional

#: 모터를 MIDI 로 기록하는 테이크
RECORD_KINDS = frozenset({'record', 'overdub'})

#: 녹화된 것을 모터로 재생하는 테이크 · 추가 녹화는 양쪽 다 한다
PLAY_KINDS = frozenset({'preview', 'overdub'})

KINDS = RECORD_KINDS | PLAY_KINDS | {'initialize'}

#: 실행 노드가 처음부터 끝까지 이끄는 테이크 · 그 상태를 그대로 비춘다.
#:
#: 녹화는 여기 없다 · 스튜디오가 MIDI 단계를 사이에 끼워 직접 이끌기 때문이다.
#: 추가 녹화도 재생을 돌리지만 **이끄는 것은 스튜디오다** · 실행 노드가 끝났다고
#: 녹화까지 끝내면 녹화된 구간 뒤가 통째로 사라진다 · §6-76
RUN_LED_KINDS = frozenset({'preview', 'initialize'})

#: 준비 → 카운트다운 → 진행 → 정지 중
PHASES = ('preparing', 'countdown', 'running', 'stopping')

#: 테이크가 없을 때의 상태 · 쉬는 중이거나, 실패했거나, 멈추는 중
RESTING_STATES = frozenset({'idle', 'error', 'stopping'})


def take_state(kind: str, phase: str) -> str:
    """이 종류·단계가 화면에 어떤 상태로 보이는가 · §6-80

    **종류에서 나온다** · 단계만 바뀌므로 녹화 테이크는 어느 단계에서도
    'playing' 이 될 수 없다.
    """
    if phase in {'preparing', 'countdown'}:
        return 'initializing'
    if phase == 'stopping':
        return 'stopping'
    if kind == 'initialize':
        return 'initializing'
    return 'recording' if kind in RECORD_KINDS else 'playing'


#: **화면이 읽는 상태 이름** · 여기가 유일한 정의다 · §6-88
#:
#: 같은 이름을 파이썬 50곳과 JS 54곳이 맨 문자열로 들고 있었다 · 한쪽에 새
#: 이름이 생기면 다른 쪽은 모른 채로 돌고, 알 방법도 없었다.
#:
#: 프런트엔드의 `MOTION_STUDIO_STATES` 와 같아야 한다 · 갈리면 검사가 잡는다.
STATES = frozenset(
    {take_state(kind, phase) for kind in KINDS for phase in PHASES}
    | RESTING_STATES
)

#: 상태에 더해 화면이 `phase` 로 받는 이름 · 카운트다운만 따로 보인다
STATUS_PHASES = STATES | {'countdown'}


@dataclass(frozen=True)
class StudioTake:
    kind: str
    phase: str
    token: int
    message: str
    ownership: Mapping[str, Any] = field(default_factory=dict)

    #: 이 테이크의 시계 · 진행 단계에서만 뜻이 있다 · §6-81
    elapsed_sec: float = 0.0
    total_sec: float = 0.0

    #: 지금 단계의 진행 · 초기 이동 3.2/5.0 초 같은 것 · 테이크 시계와 다르다
    phase_elapsed_sec: float = 0.0
    phase_total_sec: float = 0.0

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise ValueError(f'알 수 없는 테이크 종류: {self.kind}')
        if self.phase not in PHASES:
            raise ValueError(f'알 수 없는 테이크 단계: {self.phase}')

    # ----------------------------------------------------------------- #
    # 이 테이크는 무엇을 하는가
    # ----------------------------------------------------------------- #

    @property
    def records(self) -> bool:
        return self.kind in RECORD_KINDS

    @property
    def plays(self) -> bool:
        return self.kind in PLAY_KINDS

    @property
    def overdub(self) -> bool:
        return self.kind == 'overdub'

    @property
    def run_led(self) -> bool:
        """실행 노드의 상태를 그대로 비춰도 되는 테이크인가."""
        return self.kind in RUN_LED_KINDS

    @property
    def state(self) -> str:
        """화면이 읽는 상태 · `take_state` 하나에서 나온다."""
        return take_state(self.kind, self.phase)

    def advanced(self, phase: str, message: str) -> 'StudioTake':
        return replace(self, phase=phase, message=message)

    def with_message(self, message: str) -> 'StudioTake':
        return replace(self, message=message)

    def timed(self, elapsed_sec: float, total_sec: float) -> 'StudioTake':
        return replace(
            self,
            elapsed_sec=max(0.0, float(elapsed_sec)),
            total_sec=max(0.0, float(total_sec)),
        )

    def phase_timed(self, elapsed_sec: float, total_sec: float) -> 'StudioTake':
        return replace(
            self,
            phase_elapsed_sec=max(0.0, float(elapsed_sec)),
            phase_total_sec=max(0.0, float(total_sec)),
        )


def take_status_fields(take: Optional[StudioTake]) -> Dict[str, Any]:
    """화면으로 나가는 테이크 관련 필드 · 테이크 하나에서만 나온다 · §6-81

    시간도 여기서 나온다 · 전에는 `elapsed_sec` · `playback_duration_sec` ·
    `runtime_progress` · `initialization_progress` · `countdown_progress` 다섯
    군데에 흩어져 있었고, **어느 게 진짜인지가 상태에 달려 있었다** · 화면이
    그걸 다시 조립하다 녹화 중의 축 길이를 놓쳐 플레이헤드가 멈췄다 · §6-79
    """
    if take is None:
        return {
            'record_mode': None, 'overdub_spans': {},
            'elapsed_sec': 0.0, 'total_sec': 0.0,
            'phase_elapsed_sec': 0.0, 'phase_total_sec': 0.0,
        }
    return {
        'record_mode': take.kind if take.records else None,
        'elapsed_sec': round(take.elapsed_sec, 3),
        'total_sec': round(take.total_sec, 3),
        'phase_elapsed_sec': round(take.phase_elapsed_sec, 3),
        'phase_total_sec': round(take.phase_total_sec, 3),
        'overdub_spans': {
            str(motion_id): [[float(start), float(end)] for start, end in spans]
            for motion_id, spans in (take.ownership or {}).items() if spans
        } if take.overdub else {},
    }


class StudioTakeBoard:
    """테이크를 열고 닫는 유일한 창구 · §6-80

    바꿀 수 있는 것은 **단계**뿐이다 · "지금부터 재생" 이라고 말할 방법이 없다.
    전에는 상태 문자열을 아무 데서나 덮어쓸 수 있었고, 그래서 녹화가 미리보기로
    둔갑했다 · §6-76
    """

    def __init__(self, studio: Any) -> None:
        self.studio = studio
        self.take: Optional[StudioTake] = None
        #: 테이크가 없을 때의 상태 · 'idle' · 'error' · 'stopping'
        self.resting_state = 'idle'

    @property
    def state(self) -> str:
        return self.take.state if self.take else self.resting_state

    def begin(self, kind: str, message: str, ownership: Any = None) -> int:
        """테이크를 연다 · 작업 토큰을 돌려준다."""
        token = self.studio._operation_machine().begin(self.state)
        self.take = StudioTake(
            kind=kind, phase='preparing', token=token,
            message=message, ownership=dict(ownership or {}),
        )
        self.studio._project_status_locked(message)
        return token

    def advance(self, phase: str, message: str) -> None:
        """단계를 옮긴다 · **종류는 못 바꾼다** · 그게 이 구조의 요점이다."""
        if self.take is None:
            return
        self.take = self.take.advanced(phase, message)
        self.studio._project_status_locked(message)

    def begin_stop(self, message: str) -> None:
        """정지 · 테이크가 없어도 반드시 통한다.

        노드가 다시 뜬 뒤처럼 테이크를 모르는 채로 실행 노드만 도는 경우가
        있다 · 그때도 사용자는 멈출 수 있어야 한다.
        """
        if self.take is not None:
            self.advance('stopping', message)
            return
        self.resting_state = 'stopping'
        self.studio._project_status_locked(message)

    def finish(self, message: str) -> None:
        self.take = None
        self.resting_state = 'idle'
        self.studio._project_status_locked(message)

    def fail(self, message: str) -> None:
        self.take = None
        self.resting_state = 'error'
        self.studio._project_status_locked(message)

    def tick(self, elapsed_sec: float, total_sec: float) -> None:
        """테이크 시계를 옮긴다 · 50Hz 로 불리므로 상태 전체를 다시 세우지 않는다."""
        if self.take is None:
            return
        self.take = self.take.timed(elapsed_sec, total_sec)
        self.studio._status['elapsed_sec'] = round(self.take.elapsed_sec, 3)
        self.studio._status['total_sec'] = round(self.take.total_sec, 3)

    def phase_tick(self, elapsed_sec: float, total_sec: float) -> None:
        """지금 단계의 진행 · 초기 이동 3.2/5.0 초 같은 것."""
        if self.take is None:
            return
        self.take = self.take.phase_timed(elapsed_sec, total_sec)
        self.studio._status['phase_elapsed_sec'] = round(self.take.phase_elapsed_sec, 3)
        self.studio._status['phase_total_sec'] = round(self.take.phase_total_sec, 3)

    def note_idle(self, message: str) -> None:
        """테이크 없이 안내만 바꾼다 · 프로젝트 열기·삭제 같은 일."""
        self.resting_state = 'idle'
        self.studio._project_status_locked(message)
