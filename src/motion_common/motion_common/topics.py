"""토픽 이름 단일 정의.

같은 토픽이 노드 파라미터 기본값 · launch 리터럴 · 상대 노드 기본값 세 곳에
따로 적히면 한 곳만 고쳤을 때 조용히 어긋난다. 실제로 `motion_run_manager`가
supervisor로 보내는 요청 토픽 파라미터를 `motor_command_topic`이라 불러,
supervisor의 동명 파라미터(최종 하드웨어 출력)와 이름이 겹쳐 있었다.

파라미터 이름과 토픽 이름은 다른 축이다. 이 모듈은 **토픽 이름**만 정의한다.
파라미터 이름은 각 노드가 자기 역할에 맞게 정하되, 기본값은 여기서 가져온다.

명령 최종 출력은 `motion_supervisor`가 단독으로 `MOTOR_COMMAND`에 발행한다.
다른 노드는 `MOTION_RUN_COMMAND` 등 요청 토픽으로 supervisor에 넘긴다.
"""

from __future__ import annotations

import hashlib
import os
from typing import Dict


# --------------------------------------------------------------------------- #
# PC 이름공간 · §6-95
# --------------------------------------------------------------------------- #
#
# 여러 PC 가 한 DDS 망에 있으면 같은 토픽 이름이 부딪힌다 · PC1 의
# `/xtouch/midi` 와 PC2 의 것이 구별되지 않는다.
#
# 그래서 **이 PC 것**에는 접두사를 붙인다 · `/pc1/xtouch/midi`.
# **그룹 공용**(`/motion_group/...`)에는 붙이지 않는다 · 그게 PC 끼리 만나는
# 자리이기 때문이다.
#
# `MOTION_PC_NAMESPACE` 가 비어 있으면 **지금과 글자 하나 다르지 않다** ·
# 켜지 않은 시스템은 아무것도 바뀌지 않는다.


def sanitize_namespace(value: str) -> str:
    """이름 하나를 토픽에 쓸 수 있는 모양으로 · 없으면 빈 문자열.

    토픽 이름에 쓸 수 없는 글자는 밑줄로 바꾼다 · `pc_id` 나 호스트 이름을
    그대로 넣는 일이 흔한데, 하이픈이나 점이 들어가면 **아무 말 없이 통신이
    안 된다** · 모터 노드(`__ns`)는 아예 뜨지도 않는다.
    숫자로 시작해도 안 되므로 앞에 `pc_` 를 붙인다.

    이 규칙의 주인은 여기 하나다 · 실행 스크립트가 넘기는 값도
    `group_env.py` 를 거쳐 여기를 지난다 · 따로 적으면 둘이 갈린다.

    두 번 걸어도 같은 값이다 · 이미 정리된 이름을 다시 넣어도 안 바뀐다.
    """
    raw = (value or '').strip().strip('/')
    if not raw:
        return ''
    cleaned = ''.join(
        character if character.isascii() and (character.isalnum() or character == '_')
        else '_'
        for character in raw
    )
    cleaned = cleaned.strip('_')
    if not cleaned:
        # 쓸 수 있는 글자가 하나도 안 남았다(예: 한글 호스트 이름) · 그렇다고
        # 빈 값으로 두면 **조용히 이름표가 없어진다** · 공유망에서 그러면
        # 다른 PC 와 토픽이 부딪힌다 · 대신 그 이름에서 늘 같은 값을 만든다.
        digest = hashlib.sha1(raw.encode('utf-8')).hexdigest()[:8]
        return f'pc_{digest}'
    if cleaned[0].isdigit():
        cleaned = f'pc_{cleaned}'
    return cleaned


def pc_namespace() -> str:
    """이 PC 의 이름공간 · 없으면 빈 문자열."""
    return sanitize_namespace(os.environ.get('MOTION_PC_NAMESPACE') or '')


def scoped(path: str) -> str:
    """이 PC 것에 이름공간을 붙인다 · 그룹 공용에는 쓰지 않는다."""
    namespace = pc_namespace()
    return f'/{namespace}{path}' if namespace else path

# --------------------------------------------------------------------------- #
# /motion_control · 제어 평면
# --------------------------------------------------------------------------- #

#: 모터 상태 브로드캐스트 · **우리 것** (motion_state_monitor 가 만든다)
MOTION_STATE = scoped('/motor/state')

# --------------------------------------------------------------------------- #
# 모터 시스템과 만나는 자리 · §6-176
# --------------------------------------------------------------------------- #
#
# **이 둘은 우리가 지은 이름이 아니다.**
#
# `motion_system` 은 **별도 저장소**다 (`src/motion_system`) · 그쪽
# `motor_manager_node` 가 이 이름을 **코드에 그대로 박아** 두고 있다.
#
#     motion_system/.../robot_manager_node.py
#         create_subscription(MotorStatus, 'motion_control/motor_command', ...)
#         create_publisher(MotorStatus,    'motion_control/motor_status', ...)
#
# 여기서 이름을 바꾸면 **모터가 통째로 안 돈다** · 오류도 안 난다 · 그냥
# 아무도 듣지 않는 곳에 말하게 된다.
#
# `motion_control/` 이라는 묶음 이름 자체가 그 경계에서 온 것이다 · 그 아래
# 우리 내부 통로 18개가 같이 얹혀 있어서, 이름만 보고는 어디까지가 남의
# 집인지 알 수 없다 · 그래서 여기 적어 둔다.
#
# 바꾸려면 **양쪽 저장소를 같이** 고쳐야 한다 · `motion_system` 은 별도 지시
# 없이 손대지 않는다.
#
# `/motion_control/request` 도 그쪽 이름이지만 **우리는 쓰지 않는다** ·
# 발행도 구독도 하지 않으므로 여기 두지 않는다 · 쓰려 하기 전에 그쪽
# 저장소를 먼저 볼 것.

#: 모터 하드웨어 상태 · **모터 시스템이 발행** · 이름 고정
MOTOR_STATUS = scoped('/motion_control/motor_status')
#: 최종 하드웨어 명령 · motion_supervisor 단독 발행 · **모터 시스템이 구독** · 이름 고정
MOTOR_COMMAND = scoped('/motion_control/motor_command')

#: 모터 시스템과 **지금 실제로 만나는** 통로 · 바꾸면 조용히 끊긴다 · §6-176
MOTOR_SYSTEM_BOUNDARY = {
    'MOTOR_STATUS': '/motion_control/motor_status',
    'MOTOR_COMMAND': '/motion_control/motor_command',
}

#: 이름만 저쪽에서 온 것 · **지금은 우리 노드끼리만 쓴다** · §6-176
#:
#: `/xtouch/midi` 는 `motion_system` 의 `xtouch_midi` 패키지가 기본값으로
#: 쓰는 이름이다 · 그 노드는 이 시스템에서 **안 돈다** (우리
#: `midi_input_bridge` 가 대신한다) · 그래서 지금 바꿔도 아무것도 안 끊긴다.
#:
#: 다만 언젠가 저쪽 노드를 같이 돌리면 그때 이름이 맞아야 한다 · 바꾸기 전에
#: 그 계획이 없는지 확인할 것.
MOTOR_SYSTEM_SHARED_NAMES = {
    'XTOUCH_MIDI': '/xtouch/midi',
}
#: 모터 스캔 진행률
MOTOR_SCAN_PROGRESS = scoped('/motor/scan_progress')

# --------------------------------------------------------------------------- #
# 모터 검색·감시 서비스 · **이름표가 반드시 붙어야 한다** · §6-103
# --------------------------------------------------------------------------- #
#
# 이것들만 이름표 없이 전역(`/motor_scan`)이었다 · 세 PC 가 같은 이름으로
# 등록해서 **누가 받을지 정해지지 않았다**:
#
#     Action: /motor_scan
#     Action servers: 3
#
# 실제로 피시1에서 누른 검색을 피시3이 받아, 피시3의 EtherCAT 상태
# (`Phase: Operation`)로 "Master 사용 중" 을 돌려줬다. 검색은 **버스를 다시
# 훑고 모터 서비스를 껐다 켠다** · 남의 PC 하드웨어를 건드릴 수 있었다.
#
# 진행률(`MOTOR_SCAN_PROGRESS`)은 처음부터 이름표가 있었다 · 요청 쪽만 빠져
# 있었다. 같은 사실을 두 곳에 적으면 이렇게 한쪽만 어긋난다.
#
# 2026-09-18 에 `motor/` 아래로 모았다 · §6-179
#
# 진행률만 `/motion_control/motor_scan_progress` 에 있고 시작 명령 넷은
# **맨 바깥**에 있었다 · 같은 기능인데 사는 곳이 달랐다 · 다른 통로는 전부
# 묶음 이름이 붙어 있는데 이 넷만 없어서, 목록을 볼 때마다 「이건 뭐지」가 됐다.
#
#     /motor_scan              → /motor/scan
#     /scan_motors             → /motor/scan_all
#     /scan_ac_servo_motors    → /motor/scan_ac_servo
#     /scan_dynamixel_motors   → /motor/scan_dynamixel
#     /set_monitoring          → /motor/set_monitoring
#
# 전부 이 PC 안에서만 쓴다 · 그룹 통로가 아니므로 PC 끼리 주고받는 것과
# 무관하다.
#: 모터 검색 Action · 장기 작업
MOTOR_SCAN_ACTION = scoped('/motor/scan')
#: 전체 검색 · Trigger
SCAN_MOTORS = scoped('/motor/scan_all')
#: AC 서보만 검색
SCAN_AC_SERVO_MOTORS = scoped('/motor/scan_ac_servo')
#: 다이나믹셀만 검색
SCAN_DYNAMIXEL_MOTORS = scoped('/motor/scan_dynamixel')
#: 상태 감시 켜고 끄기
SET_MONITORING = scoped('/motor/set_monitoring')
#: 선택 프로젝트 전파
ACTIVE_PROJECT = scoped('/motion_run/active_project')

#: 모션 재생 합산 요청 · motion_run_manager → motion_supervisor
MOTION_RUN_COMMAND = scoped('/motion_run/command')
MOTION_RUN_REQUEST = scoped('/motion_run/request')
MOTION_RUN_RESPONSE = scoped('/motion_run/response')
MOTION_RUN_STATUS = scoped('/motion_run/status')

#: 모션 축 매핑
MOTION_MAPPING_REQUEST = scoped('/motion_mapping/request')
MOTION_MAPPING_RESPONSE = scoped('/motion_mapping/response')
#: 모션값 상태
MOTION_VALUE_STATE = scoped('/midi/value_state')

#: 수동 조그
MANUAL_JOG_REQUEST = scoped('/manual/jog_request')
MANUAL_JOG_RESULT = scoped('/manual/jog_response')
#: 수동 동작
MANUAL_ACTION_REQUEST = scoped('/manual/action_request')
MANUAL_ACTION_RESULT = scoped('/manual/action_response')

#: MIDI 위치 지정
MIDI_POSITION_REQUEST = scoped('/midi/position_request')
MIDI_POSITION_RESULT = scoped('/midi/position_response')

#: 안전
SAFETY_REQUEST = scoped('/safety/request')
SAFETY_STATUS = scoped('/safety/status')

# --------------------------------------------------------------------------- #
# /motion_studio · 편집
# --------------------------------------------------------------------------- #

STUDIO_REQUEST = scoped('/motion_studio/request')
STUDIO_RESPONSE = scoped('/motion_studio/response')
STUDIO_STATUS = scoped('/motion_studio/status')
STUDIO_EDITOR_REQUEST = scoped('/motion_studio/editor/request')
STUDIO_EDITOR_RESPONSE = scoped('/motion_studio/editor/response')

# --------------------------------------------------------------------------- #
# /motion_group · 다중 PC 연동 (DDS 별도 도메인)
# --------------------------------------------------------------------------- #

GROUP_HEARTBEAT = '/motion_group/heartbeat'
GROUP_COMMAND = '/motion_group/command'
GROUP_EVENT = '/motion_group/event'
GROUP_ALARM = '/motion_group/alarm'
GROUP_TIME_SYNC = '/motion_group/time_sync'
GROUP_SYSTEM_INFO = '/motion_group/system_info'

#: 원시 MIDI 중계 · §6-94 · 200Hz 최선형 · 깊이 1
#:
#: 장치가 꽂힌 PC 에서 대상 PC 로 · 되돌아가는 페이더 명령은 반대 길이다.
GROUP_MIDI = '/motion_group/midi'
GROUP_MIDI_FEEDBACK = '/motion_group/midi_feedback'

# --------------------------------------------------------------------------- #
# /motion_schedule · 스케줄
# --------------------------------------------------------------------------- #

SCHEDULE_STATUS = scoped('/motion_schedule/status')

# --------------------------------------------------------------------------- #
# /motion_web · 웹 브리지 부가 채널
# --------------------------------------------------------------------------- #

MIDI_MONITOR_REQUEST = scoped('/motion_web/midi_monitor/request')
MIDI_MONITOR_RESPONSE = scoped('/motion_web/midi_monitor/response')
MIDI_MONITOR_STATE = scoped('/motion_web/midi_monitor/state')

# --------------------------------------------------------------------------- #
# /xtouch · MIDI 컨트롤 서피스
# --------------------------------------------------------------------------- #

XTOUCH_MIDI = scoped('/xtouch/midi')
XTOUCH_FEEDBACK = scoped('/xtouch/feedback')
XTOUCH_INPUT_STATE = scoped('/xtouch/input_state')
XTOUCH_CONNECTION_STATE = scoped('/xtouch/connection/state')
XTOUCH_CONNECTION_COMMAND = scoped('/xtouch/connection/command')

#: 이 PC 가 지금 표면을 쓸 수 있는가 · §6-94
#:
#: **권한은 장치 연결과 다른 사실이다** · USB 가 꽂혔는지는 입력 브리지가
#: 알고, 누가 쓰는지는 조정 노드가 정한다 · 두 사실을 한 통로에 섞었더니
#: 재연결 한 번에 서로를 덮어써서, 넘긴 PC 가 페이더를 0 으로 밀고 그것이
#: 받은 PC 의 모터까지 0 으로 끌고 갔다.
#:
#: 주인은 **조정 노드 하나**다 · `midi_control` 은 이것만 보고 표면 구독을
#: 열고 닫는다 · 권한이 없으면 값을 받아 버리는 것이 아니라 **받지 않는다**.
XTOUCH_SURFACE = scoped('/xtouch/surface')


def all_topics() -> Dict[str, str]:
    """정의된 토픽 전체를 ``{상수명: 토픽}``으로 돌려준다 · 점검·문서화용."""
    return {
        name: value
        for name, value in globals().items()
        if name.isupper() and isinstance(value, str) and value.startswith('/')
    }
