"""모션 실행이 쓰는 상수 · 노드와 분해된 서비스가 함께 본다.

`MotionRunManager`를 나누면서 상수를 양쪽에 복사하면 언젠가 갈라진다 ·
§6-16에서 Dynamixel 스캔 제한시간이 그렇게 갈라져 40초가 20초로 돌았다.
한 곳에 둔다 · §6-31
"""

from __future__ import annotations

from motion_common.timing import CONTROL_PERIOD_SEC

#: 모터 명령의 필드 식별자 · `motion_supervisor`와 맞춘 값
ID_CONTROLWORD = 0
ID_TARGET_POSITION = 1

#: MINAS 컨트롤워드 · 운전 허가 · 새 목표값 적용
CW_ENABLE_OPERATION_MINAS = 0x000F
CW_NEW_SET_POINT_MINAS = 0x003F

#: Dynamixel 토크 활성
DYNAMIXEL_TORQUE_ENABLE = 1

DEFAULT_PERIOD_SEC = CONTROL_PERIOD_SEC
#: 모터 상태가 이 시간보다 오래되면 신뢰하지 않는다
STATE_TIMEOUT_SEC = 1.0
SAFETY_STATUS_TIMEOUT_SEC = 2.0
#: 목표 도달 판정 허용 오차
AC_TARGET_TOLERANCE_DEG = 0.1
DYNAMIXEL_TARGET_TOLERANCE_DEG = 1.0
TARGET_SETTLE_TIMEOUT_SEC = 3.0
#: 연속 재생 이음매 허용 오차
CONTINUOUS_LOOP_TOLERANCE_DEG = 5.0
#: 초기 위치 이동 시간 후보
INITIAL_MOVE_TIME_OPTIONS_SEC = (5.0, 7.0, 10.0)
