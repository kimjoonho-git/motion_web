"""한 회차가 끝나면 어떻게 잇는가 · 이 낱말의 주인 · §6-135

같은 기본값이 여덟 곳에 적혀 있었고 답이 **두 가지**였다.

    화면 선택칸 · 화면 저장 · 실행 설정 저장   `reinitialize`
    스케줄 노드 · 저장 형식 기본값 · 그 외      `direct`

그래서 화면에는 「초기 위치 이동 후 다음」이 골라져 보이는데, 스케줄이 발화하면
`direct` 로 나갔다 · `direct` 는 끝값에서 시작값으로 **곧바로 튀므로** 둘이
5° 이상 벌어져 있으면 실행을 거부한다 · 화면에서 손으로 누르면 되는 모션이
스케줄로는 "모션 시작·종료값 차이가 5°를 초과합니다" 로 죽었다.

기본값은 `reinitialize` 다 · 초기 위치로 옮긴 뒤 다시 재생하므로 시작값과
끝값이 달라도 된다 · 사람이 보고 있지 않은 자리에서 더 안전하기도 하다.
"""

DIRECT = 'direct'
DWELL = 'dwell'
REINITIALIZE = 'reinitialize'
DWELL_REINITIALIZE = 'dwell_reinitialize'

REPEAT_MODES = frozenset({DIRECT, DWELL, REINITIALIZE, DWELL_REINITIALIZE})

DEFAULT_REPEAT_MODE = REINITIALIZE

#: 초기 위치로 돌아가지 않고 이어 붙이는 방식 · 시작값과 끝값이 맞아야 한다
LOOPS_WITHOUT_REINITIALIZE = frozenset({DIRECT, DWELL})


def normalize_repeat_mode(value, default: str = DEFAULT_REPEAT_MODE) -> str:
    """빈 값·모르는 값은 기본값으로 · 판정은 여기서만 한다."""
    mode = str(value or '').strip().lower()
    return mode if mode in REPEAT_MODES else default


def needs_loop_value_match(repeat_mode, *, unknown_needs_match: bool = True) -> bool:
    """이 방식으로 이으려면 모션 시작값과 끝값이 맞아야 하는가.

    **설정 기본값과는 다른 질문이다.** 안 적힌 값을 설정에서는 `reinitialize`
    로 읽지만(사람이 화면에서 보는 것과 같게), 여기서는 모르면 **검사하는
    쪽**이 안전하다 · 검사를 건너뛴 채로 시작하면 모터가 끝값에서 시작값으로
    튄다.

    계획(`plan_builder`)은 언제나 값을 정규화해 실어 주므로, 실제로 모르는
    값이 여기까지 오는 일은 없어야 한다.
    """
    mode = str(repeat_mode or '').strip().lower()
    if mode not in REPEAT_MODES:
        return unknown_needs_match
    return mode in LOOPS_WITHOUT_REINITIALIZE
