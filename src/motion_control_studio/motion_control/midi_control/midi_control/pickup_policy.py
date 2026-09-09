"""Pickup 정책 · 물리 페이더가 논리값을 따라잡을 때까지 명령을 막는다.

`midi_control_node`에서 떼어냈다 · §5 분해 목표안의 `PickupPolicy` · §6-39

물리 페이더 위치와 실제 모션값이 어긋난 채로 SELECT를 켜면 축이 튄다. 페이더가
기준값을 지나갈 때까지 기다렸다가 그때부터 명령을 낸다 · 그 판정만 모았다.

채널별 대기 상태 넷을 이 객체가 갖는다. 노드의 `_lock` 아래에서만 불린다 ·
이름 끝의 `_locked`가 그 약속이다.
"""

from __future__ import annotations

from typing import Any, Dict, List

from midi_control.bank_manager import MIDI_CHANNEL_COUNT
from midi_control.motion_value_map import (
    LINKED_MOTION_VALUE_TOLERANCE_DEG,
    _finite_float,
    motion_value_from_motor,
    require_motion_value_within_limits,
)

PICKUP_TOLERANCE_DEG = 0.5
PICKUP_FEEDBACK_CONSISTENCY_DEG = 1.0


class PickupPolicy:
    def __init__(self, node: Any) -> None:
        self.node = node
        #: 채널별 · 페이더가 기준값을 아직 지나지 않았는가
        self.pending = [False] * MIDI_CHANNEL_COUNT
        #: 채널별 · 따라잡아야 할 기준 모션값
        self.reference_motion = [None] * MIDI_CHANNEL_COUNT
        #: 채널별 · 직전 모션값 · 기준을 지나쳤는지 보려면 필요하다
        self.previous_motion = [None] * MIDI_CHANNEL_COUNT
        #: 채널별 · 기준값의 출처
        self.reference_source = [''] * MIDI_CHANNEL_COUNT

    def reset(self) -> None:
        """모든 채널의 대기 상태를 처음으로 되돌린다."""
        self.pending = [False] * MIDI_CHANNEL_COUNT
        self.reference_motion = [None] * MIDI_CHANNEL_COUNT
        self.previous_motion = [None] * MIDI_CHANNEL_COUNT
        self.reference_source = [''] * MIDI_CHANNEL_COUNT

    def _ensure_pickup_state_locked(self) -> None:
        if not hasattr(self, 'pending'):
            self.pending = [False] * MIDI_CHANNEL_COUNT
        if not hasattr(self, 'reference_motion'):
            self.reference_motion = [None] * MIDI_CHANNEL_COUNT
        if not hasattr(self, 'previous_motion'):
            self.previous_motion = [None] * MIDI_CHANNEL_COUNT
        if not hasattr(self, 'reference_source'):
            self.reference_source = [''] * MIDI_CHANNEL_COUNT

    def _clear_pickup_state_locked(self, channel: int) -> None:
        self._ensure_pickup_state_locked()
        self.pending[channel] = False
        self.reference_motion[channel] = None
        self.previous_motion[channel] = None
        self.reference_source[channel] = ''

    def _pickup_reference_for_group_locked(
        self, group: List[Dict[str, Any]]
    ) -> tuple[float, str]:
        """Prefer the latest accepted logical value, then invert live feedback."""
        if not group:
            raise ValueError('연결할 Motion ID가 없습니다')
        self.node._ensure_current_motion_state_locked()
        motion_ids = [str(item['motion_id']) for item in group]
        context = (
            str(getattr(self.node, '_project_id', '') or ''),
            int(getattr(self.node, '_execution_context', {}).get('project_generation') or 0),
        )
        source_values = (
            getattr(self.node, '_source_motion_values', {})
            if getattr(self.node, '_source_motion_value_context', ('', 0)) == context
            else {}
        )
        candidates = (
            ('source_topic', source_values),
            ('midi_approved', self.node._current_motion_values),
        )
        for source, values_by_id in candidates:
            values = [
                _finite_float(values_by_id.get(motion_id))
                for motion_id in motion_ids
            ]
            if any(value is None for value in values):
                continue
            logical_values = [float(value) for value in values if value is not None]
            if (
                max(logical_values) - min(logical_values)
                > LINKED_MOTION_VALUE_TOLERANCE_DEG
            ):
                continue
            candidate = sum(logical_values) / len(logical_values)
            if self._logical_value_matches_feedback(group, candidate):
                return candidate, source

        feedback_values = []
        for item in group:
            if not self._motor_feedback_ready_for_pickup(item.get('motor')):
                raise ValueError(
                    f"{item['motion_id']}: Pickup에 사용할 최신 모터 피드백이 없습니다"
                )
            position = self.node._position_from_motor(item.get('motor'))
            if position is None:
                raise ValueError(
                    f"{item['motion_id']}: Pickup 기준을 계산할 실제 모터 위치가 없습니다"
                )
            feedback_values.append(
                motion_value_from_motor(position, item['row'])
            )
        tolerance = self._pickup_feedback_consistency_tolerance()
        if max(feedback_values) - min(feedback_values) > tolerance:
            raise ValueError(
                '연동 축의 실제 위치를 같은 모션값으로 환산할 수 없습니다. '
                '초기 위치 정렬 후 다시 SELECT 하세요'
            )
        return sum(feedback_values) / len(feedback_values), 'motor_feedback'

    def _logical_value_matches_feedback(
        self, group: List[Dict[str, Any]], motion_value: float
    ) -> bool:
        tolerance = self._pickup_feedback_consistency_tolerance()
        for item in group:
            if not self._motor_feedback_ready_for_pickup(item.get('motor')):
                return False
            position = self.node._position_from_motor(item.get('motor'))
            if position is None:
                return False
            try:
                target = require_motion_value_within_limits(
                    item['motion_id'], motion_value, item['row'], item['motor']
                )
            except ValueError:
                return False
            if abs(position - target) > tolerance:
                return False
        return True

    def _motor_feedback_ready_for_pickup(self, motor: Any) -> bool:
        if not isinstance(motor, dict):
            return False
        connection_state = str(motor.get('connection_state') or '').strip().lower()
        if connection_state and connection_state != 'online':
            return False
        runtime_state = str(motor.get('state') or '').strip().lower()
        if runtime_state and runtime_state != 'detected':
            return False
        age = _finite_float(motor.get('age_sec'))
        if age is not None and age > max(
            float(getattr(self.node, 'stale_timeout_sec', 0.5)), 0.1
        ):
            return False
        if bool(motor.get('fault')):
            return False
        return self.node._position_from_motor(motor) is not None

    def _pickup_tolerance(self) -> float:
        return max(
            0.0,
            float(getattr(self.node, 'pickup_tolerance_deg', PICKUP_TOLERANCE_DEG)),
        )

    def _pickup_feedback_consistency_tolerance(self) -> float:
        return max(
            0.0,
            float(
                getattr(
                    self,
                    'pickup_feedback_consistency_deg',
                    PICKUP_FEEDBACK_CONSISTENCY_DEG,
                )
            ),
        )

    @staticmethod
    def _pickup_reached(
        previous: float | None,
        current: float,
        reference: float,
        tolerance: float,
    ) -> bool:
        if abs(current - reference) <= tolerance:
            return True
        if previous is None:
            return False
        return (previous <= reference <= current) or (current <= reference <= previous)
