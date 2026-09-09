"""페이더 물리 위치 정렬 · 파킹과 동기화 대기.

`midi_control_node`에서 떼어냈다 · §5 분해 목표안의 `FaderStateMachine` · §6-40

전동 페이더는 명령을 보낸다고 즉시 그 자리에 있지 않다. 보낸 목표와 실제 도착을
따로 들고, 도착할 때까지 입력을 신뢰하지 않는다 · 그 대기 상태를 이 객체가 갖는다.

- `parking` · 0으로 되돌리는 중인가
- `awaiting_sync` · 보낸 목표에 아직 도착하지 않았는가
- `input_generation` · 세대 · 재연결 전 입력을 뒤늦게 받아 쓰지 않으려는 표식

노드의 `_lock` 아래에서만 불린다 · 이름 끝의 `_locked`가 그 약속이다.
`_resync_controlled_faders_locked`는 뱅크·축 등록부·SELECT 상태까지 건드리는
노드 조율이라 남겨두었다.
"""

from __future__ import annotations

from typing import Any, List

from midi_control.bank_manager import MIDI_CHANNEL_COUNT

FADER_SYNC_MIN_DURATION_SEC = 0.10
FADER_PARK_RETRY_SEC = 0.15
FADER_PARK_TOLERANCE_RAW = 16
FADER_PARK_TIMEOUT_SEC = 2.0


class FaderStateMachine:
    def __init__(self, node: Any) -> None:
        self.node = node
        self.input_generation = [0] * MIDI_CHANNEL_COUNT
        # 기동 시 SELECT는 항상 꺼져 있다 · 모든 물리 페이더를 0으로 되돌린다
        self.pending_positions: List[int | None] = [0] * MIDI_CHANNEL_COUNT
        self.pending_input_generations = [0] * MIDI_CHANNEL_COUNT
        self.sync_targets: List[int | None] = [None] * MIDI_CHANNEL_COUNT
        self.awaiting_sync = [False] * MIDI_CHANNEL_COUNT
        self.sync_not_before = [0.0] * MIDI_CHANNEL_COUNT
        self.parking = [False] * MIDI_CHANNEL_COUNT
        self.park_last_command_at = [0.0] * MIDI_CHANNEL_COUNT
        self.zero_required = [True] * MIDI_CHANNEL_COUNT
        # park_started_at은 만들지 않는다 · `_ensure_fader_parking_state_locked`가
        # 늦게 만드는 것이 원래 동작이다

    def reset(self) -> None:
        """실행 중 상태를 처음으로 되돌린다 · 세대는 이어간다."""
        self.pending_positions = [0] * MIDI_CHANNEL_COUNT
        self._ensure_fader_input_generation_locked()
        self.pending_input_generations = list(self.input_generation)
        self.sync_targets = [None] * MIDI_CHANNEL_COUNT
        self.awaiting_sync = [False] * MIDI_CHANNEL_COUNT
        self.sync_not_before = [0.0] * MIDI_CHANNEL_COUNT
        self.parking = [False] * MIDI_CHANNEL_COUNT
        self.park_last_command_at = [0.0] * MIDI_CHANNEL_COUNT
        self.park_started_at = [0.0] * MIDI_CHANNEL_COUNT
        self.zero_required = [True] * MIDI_CHANNEL_COUNT

    def _ensure_fader_parking_state_locked(self) -> None:
        if not hasattr(self, 'parking'):
            self.parking = [False] * MIDI_CHANNEL_COUNT
        if not hasattr(self, 'park_last_command_at'):
            self.park_last_command_at = [0.0] * MIDI_CHANNEL_COUNT
        if not hasattr(self, 'park_started_at'):
            self.park_started_at = [0.0] * MIDI_CHANNEL_COUNT
        if not hasattr(self, 'zero_required'):
            self.zero_required = [False] * MIDI_CHANNEL_COUNT

    def _ensure_fader_input_generation_locked(self) -> None:
        if not hasattr(self, 'input_generation'):
            self.input_generation = [0] * MIDI_CHANNEL_COUNT
        if not hasattr(self, 'pending_input_generations'):
            self.pending_input_generations = list(
                self.input_generation
            )

    def _queue_fader_position_locked(
        self, channel: int, position: int | None
    ) -> None:
        self._ensure_fader_input_generation_locked()
        self.pending_positions[channel] = position
        self.pending_input_generations[channel] = int(
            self.input_generation[channel]
        )

    def _start_fader_parking_locked(self, channel: int, now: float) -> None:
        self._ensure_fader_parking_state_locked()
        if not hasattr(self.node, '_last_feedback'):
            self.node._last_feedback = [None] * MIDI_CHANNEL_COUNT
        self.zero_required[channel] = True
        self.parking[channel] = True
        self.park_started_at[channel] = now
        self.park_last_command_at[channel] = now
        self._queue_fader_position_locked(channel, 0)
        self.sync_targets[channel] = 0
        self.awaiting_sync[channel] = True
        self.sync_not_before[channel] = now + FADER_SYNC_MIN_DURATION_SEC
        self.node._last_feedback[channel] = None
        self.node._motor_command_state[channel] = 'parking_fader'
        self.node._motor_command_message[channel] = 'SELECT 해제 · 페이더 0 복귀 중'

    def _queue_normal_fader_zero_locked(self, channel: int, now: float) -> None:
        """Send a best-effort zero command without blocking the next SELECT."""
        self._ensure_fader_parking_state_locked()
        if not hasattr(self.node, '_last_feedback'):
            self.node._last_feedback = [None] * MIDI_CHANNEL_COUNT
        self.zero_required[channel] = True
        self.parking[channel] = False
        self.park_started_at[channel] = 0.0
        self.park_last_command_at[channel] = 0.0
        self._queue_fader_position_locked(channel, 0)
        self.sync_targets[channel] = 0
        self.awaiting_sync[channel] = True
        self.sync_not_before[channel] = now + FADER_SYNC_MIN_DURATION_SEC
        self.node._last_feedback[channel] = None
        self.node._motor_command_state[channel] = 'inactive'
        self.node._motor_command_message[channel] = (
            'SELECT 사용 가능 · 페이더 0 이동 명령 전송(도착 피드백 없음)'
        )

    def _update_fader_parking_locked(
        self, channel: int, raw: int, now: float
    ) -> bool:
        """Advance one mandatory SELECT-off park and return its prior state."""
        self._ensure_fader_parking_state_locked()
        was_parking = self.parking[channel]
        if not was_parking:
            return False
        self.node._raw_channels[channel] = raw
        self.node._channels[channel] = float(raw)
        self.node._filter_stage1[channel] = float(raw)
        self.node._filter_stage2[channel] = float(raw)
        physically_busy = (
            self.node._physical_touch[channel]
            or self.node._fader_moving[channel]
            or self.node._bridge_fader_syncing[channel]
        )
        if raw <= FADER_PARK_TOLERANCE_RAW and not physically_busy:
            self.parking[channel] = False
            self.park_started_at[channel] = 0.0
            self.park_last_command_at[channel] = 0.0
            self.sync_targets[channel] = None
            self.awaiting_sync[channel] = False
            self.sync_not_before[channel] = 0.0
            self.node._raw_channels[channel] = 0
            self.node._channels[channel] = 0.0
            self.node._filter_stage1[channel] = 0.0
            self.node._filter_stage2[channel] = 0.0
            self.node._motor_command_state[channel] = 'inactive'
            self.node._motor_command_message[channel] = (
                'SELECT 사용 가능 · 페이더 0 이동 명령 전송'
                '(물리 도착 피드백 없음)'
            )
            return True
        if (
            not bool(getattr(self.node, '_studio_select_locked', False))
            and self.park_started_at[channel] > 0.0
            and now - self.park_started_at[channel]
            >= FADER_PARK_TIMEOUT_SEC
        ):
            # A failed motorized-fader return must not permanently lock the
            # physical SELECT button. Motor ownership is already released;
            # stop retrying and let the next SELECT perform a fresh pickup
            # from the logical Motion ID value before motor commands resume.
            self.parking[channel] = False
            self.park_started_at[channel] = 0.0
            self.park_last_command_at[channel] = 0.0
            self._queue_fader_position_locked(channel, None)
            self.sync_targets[channel] = None
            self.awaiting_sync[channel] = False
            self.sync_not_before[channel] = 0.0
            self.node._last_feedback[channel] = None
            self.node._motor_command_state[channel] = 'fader_park_failed'
            self.node._motor_command_message[channel] = (
                f'페이더 0 복귀 실패(현재 {raw}) · SELECT 재시도 가능'
            )
            return False
        if (
            not self.node._physical_touch[channel]
            and not self.node._fader_moving[channel]
            and now - self.park_last_command_at[channel]
            >= FADER_PARK_RETRY_SEC
        ):
            self._queue_fader_position_locked(channel, 0)
            self.sync_targets[channel] = 0
            self.awaiting_sync[channel] = True
            self.sync_not_before[channel] = now + FADER_SYNC_MIN_DURATION_SEC
            self.park_last_command_at[channel] = now
            self.node._last_feedback[channel] = None
        return True
