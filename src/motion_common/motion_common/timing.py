"""제어 주기 상수 단일 정의.

모션 데이터 샘플 간격 20ms가 네 곳에 따로 적혀 있었다.

    motion_studio/constants.py      DEFAULT_PERIOD_SEC
    motion_run_manager.py           DEFAULT_PERIOD_SEC
    midi_control_node.py            MIDI_COMMAND_PERIOD_SEC
    web_bridge/bridge_helpers.py    MOTION_DATA_PERIOD_SEC

같은 값이지만 이름이 달라 한 곳만 바꾸면 어긋난다. 모션 파일의 프레임 간격과
명령 발행 주기가 갈라지면 재생 속도가 틀어지므로, 한 군데서만 정의한다.
"""

from __future__ import annotations

__all__ = ['CONTROL_PERIOD_SEC', 'JOG_COMMAND_PERIOD_SEC', 'period_matches']

#: 모션 데이터 샘플 간격이자 명령 발행 주기 · 50Hz
CONTROL_PERIOD_SEC = 0.02

#: 수동 조그 명령 주기 · 조그는 연속 재생보다 성기게 보낸다
JOG_COMMAND_PERIOD_SEC = 0.04

#: 주기 비교 허용 오차 · 부동소수 표현 차이를 흡수한다
PERIOD_TOLERANCE_SEC = 0.001


def period_matches(interval_sec: float, period_sec: float = CONTROL_PERIOD_SEC) -> bool:
    """간격이 기준 주기와 같은지 판정한다 · 부동소수 오차를 감안한다."""
    try:
        return abs(float(interval_sec) - float(period_sec)) <= PERIOD_TOLERANCE_SEC
    except (TypeError, ValueError):
        return False
