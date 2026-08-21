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

from typing import Dict

# --------------------------------------------------------------------------- #
# /motion_control · 제어 평면
# --------------------------------------------------------------------------- #

#: 모터 상태 브로드캐스트
MOTION_STATE = '/motion_control/motion_state'
#: 모터 하드웨어 상태
MOTOR_STATUS = '/motion_control/motor_status'
#: 최종 하드웨어 명령 · motion_supervisor 단독 발행
MOTOR_COMMAND = '/motion_control/motor_command'
#: 모터 스캔 진행률
MOTOR_SCAN_PROGRESS = '/motion_control/motor_scan_progress'
#: 선택 프로젝트 전파
ACTIVE_PROJECT = '/motion_control/active_project'

#: 모션 재생 합산 요청 · motion_run_manager → motion_supervisor
MOTION_RUN_COMMAND = '/motion_control/motion_run_command'
MOTION_RUN_REQUEST = '/motion_control/motion_run_request'
MOTION_RUN_RESPONSE = '/motion_control/motion_run_response'
MOTION_RUN_STATUS = '/motion_control/motion_run_status'

#: 모션 축 매핑
MOTION_MAPPING_REQUEST = '/motion_control/motion_mapping_request'
MOTION_MAPPING_RESPONSE = '/motion_control/motion_mapping_response'
#: 모션값 상태
MOTION_VALUE_STATE = '/motion_control/motion_value_state'

#: 수동 조그
MANUAL_JOG_REQUEST = '/motion_control/manual_jog_request'
MANUAL_JOG_RESULT = '/motion_control/manual_jog_result'
#: 수동 동작
MANUAL_ACTION_REQUEST = '/motion_control/manual_action_request'
MANUAL_ACTION_RESULT = '/motion_control/manual_action_result'

#: MIDI 위치 지정
MIDI_POSITION_REQUEST = '/motion_control/midi_position_request'
MIDI_POSITION_RESULT = '/motion_control/midi_position_result'

#: 안전
SAFETY_REQUEST = '/motion_control/safety_request'
SAFETY_STATUS = '/motion_control/safety_status'

# --------------------------------------------------------------------------- #
# /motion_studio · 편집
# --------------------------------------------------------------------------- #

STUDIO_REQUEST = '/motion_studio/request'
STUDIO_RESPONSE = '/motion_studio/response'
STUDIO_STATUS = '/motion_studio/status'
STUDIO_EDITOR_REQUEST = '/motion_studio/editor/request'
STUDIO_EDITOR_RESPONSE = '/motion_studio/editor/response'

# --------------------------------------------------------------------------- #
# /motion_group · 다중 PC 연동 (DDS 별도 도메인)
# --------------------------------------------------------------------------- #

GROUP_HEARTBEAT = '/motion_group/heartbeat'
GROUP_COMMAND = '/motion_group/command'
GROUP_EVENT = '/motion_group/event'
GROUP_ALARM = '/motion_group/alarm'
GROUP_TIME_SYNC = '/motion_group/time_sync'
GROUP_SYSTEM_INFO = '/motion_group/system_info'

# --------------------------------------------------------------------------- #
# /motion_schedule · 스케줄
# --------------------------------------------------------------------------- #

SCHEDULE_STATUS = '/motion_schedule/status'

# --------------------------------------------------------------------------- #
# /motion_web · 웹 브리지 부가 채널
# --------------------------------------------------------------------------- #

MIDI_MONITOR_REQUEST = '/motion_web/midi_monitor/request'
MIDI_MONITOR_RESPONSE = '/motion_web/midi_monitor/response'
MIDI_MONITOR_STATE = '/motion_web/midi_monitor/state'

# --------------------------------------------------------------------------- #
# /xtouch · MIDI 컨트롤 서피스
# --------------------------------------------------------------------------- #

XTOUCH_MIDI = '/xtouch/midi'
XTOUCH_FEEDBACK = '/xtouch/feedback'
XTOUCH_INPUT_STATE = '/xtouch/input_state'
XTOUCH_CONNECTION_STATE = '/xtouch/connection/state'
XTOUCH_CONNECTION_COMMAND = '/xtouch/connection/command'


def all_topics() -> Dict[str, str]:
    """정의된 토픽 전체를 ``{상수명: 토픽}``으로 돌려준다 · 점검·문서화용."""
    return {
        name: value
        for name, value in globals().items()
        if name.isupper() and isinstance(value, str) and value.startswith('/')
    }
