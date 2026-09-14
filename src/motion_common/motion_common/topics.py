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

#: 모터 상태 브로드캐스트
MOTION_STATE = scoped('/motion_control/motion_state')
#: 모터 하드웨어 상태
MOTOR_STATUS = scoped('/motion_control/motor_status')
#: 최종 하드웨어 명령 · motion_supervisor 단독 발행
MOTOR_COMMAND = scoped('/motion_control/motor_command')
#: 모터 스캔 진행률
MOTOR_SCAN_PROGRESS = scoped('/motion_control/motor_scan_progress')
#: 선택 프로젝트 전파
ACTIVE_PROJECT = scoped('/motion_control/active_project')

#: 모션 재생 합산 요청 · motion_run_manager → motion_supervisor
MOTION_RUN_COMMAND = scoped('/motion_control/motion_run_command')
MOTION_RUN_REQUEST = scoped('/motion_control/motion_run_request')
MOTION_RUN_RESPONSE = scoped('/motion_control/motion_run_response')
MOTION_RUN_STATUS = scoped('/motion_control/motion_run_status')

#: 모션 축 매핑
MOTION_MAPPING_REQUEST = scoped('/motion_control/motion_mapping_request')
MOTION_MAPPING_RESPONSE = scoped('/motion_control/motion_mapping_response')
#: 모션값 상태
MOTION_VALUE_STATE = scoped('/motion_control/motion_value_state')

#: 수동 조그
MANUAL_JOG_REQUEST = scoped('/motion_control/manual_jog_request')
MANUAL_JOG_RESULT = scoped('/motion_control/manual_jog_result')
#: 수동 동작
MANUAL_ACTION_REQUEST = scoped('/motion_control/manual_action_request')
MANUAL_ACTION_RESULT = scoped('/motion_control/manual_action_result')

#: MIDI 위치 지정
MIDI_POSITION_REQUEST = scoped('/motion_control/midi_position_request')
MIDI_POSITION_RESULT = scoped('/motion_control/midi_position_result')

#: 안전
SAFETY_REQUEST = scoped('/motion_control/safety_request')
SAFETY_STATUS = scoped('/motion_control/safety_status')

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


def all_topics() -> Dict[str, str]:
    """정의된 토픽 전체를 ``{상수명: 토픽}``으로 돌려준다 · 점검·문서화용."""
    return {
        name: value
        for name, value in globals().items()
        if name.isupper() and isinstance(value, str) and value.startswith('/')
    }
