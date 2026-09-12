"""원시 MIDI 를 연동된 PC 로 중계한다 · §6-94

MIDI 장치는 **한 대뿐이고, 한 번에 한 PC 만** 그것을 쓴다 · USB 를 옮겨 꽂는
것과 같되 선을 뽑지 않는다.

    장치 ─USB─▶ PC1 bridge ─[midi]─▶ PC1 조정 ══▶ PC2 조정 ─▶ PC2 midi_control
         ◀───── PC1 bridge ◀[feedback]─ PC1 조정 ◀══ PC2 조정 ◀─ PC2 midi_control

경계는 그대로 둔다 · `midi_control` 과 모터 노드는 네트워크에서 끊긴 채
(`ROS_LOCALHOST_ONLY=1`) 이고, 조정 노드만 네트워크로 나간다 · 값도 조정
노드를 지난다.

여기 있는 것은 **판단뿐**이다 · 보낼 것인가, 받을 것인가, 이 값이 내 것인가.
실제 발행·구독은 노드가 한다 · 그래야 이 규칙을 ROS 없이 시험할 수 있다.
"""

from __future__ import annotations

from typing import Any, Optional


class MidiRelayRules:
    """이 PC 가 지금 무엇을 해야 하는가.

    세 가지 사실만 본다 · 내 PC 이름, 장치가 어디 꽂혔나, 누가 쓰기로 했나.
    """

    def __init__(self, pc_id: Any) -> None:
        self.pc_id = str(pc_id or '')
        #: 장치가 꽂힌 PC · 보통 이 PC 이거나 빈 값
        self.device_pc_id = ''
        #: 이 MIDI 를 쓰기로 한 PC · 한 번에 하나
        self.target_pc_id = ''
        self.group_id = ''

    # ----------------------------------------------------------------- #
    # 지금 상태
    # ----------------------------------------------------------------- #

    def update(
        self, *, group_id: Any = None, device_pc_id: Any = None,
        target_pc_id: Any = None,
    ) -> None:
        if group_id is not None:
            self.group_id = str(group_id or '')
        if device_pc_id is not None:
            self.device_pc_id = str(device_pc_id or '')
        if target_pc_id is not None:
            self.target_pc_id = str(target_pc_id or '')

    @property
    def holds_device(self) -> bool:
        """이 PC 에 장치가 꽂혀 있나."""
        return bool(self.pc_id) and self.pc_id == self.device_pc_id

    @property
    def is_target(self) -> bool:
        """이 PC 가 지금 MIDI 를 쓰기로 된 PC 인가."""
        return bool(self.pc_id) and self.pc_id == self.target_pc_id

    @property
    def relaying(self) -> bool:
        """중계가 실제로 일어나고 있나 · 장치와 사용자가 다른 PC 일 때만."""
        return bool(
            self.group_id
            and self.device_pc_id
            and self.target_pc_id
            and self.device_pc_id != self.target_pc_id
        )

    # ----------------------------------------------------------------- #
    # 무엇을 할 것인가
    # ----------------------------------------------------------------- #

    @property
    def should_send_midi(self) -> bool:
        """장치를 들고 있고, 쓰는 쪽이 남이면 보낸다."""
        return self.holds_device and self.relaying

    @property
    def should_send_feedback(self) -> bool:
        """MIDI 를 쓰고 있고, 장치가 남에게 있으면 페이더 명령을 되돌린다.

        물리 페이더는 한 대뿐이다 · 대상 PC 하나만 보내야 떨지 않는다.
        """
        return self.is_target and self.relaying

    def accepts_midi(self, message: Any) -> bool:
        """네트워크에서 온 MIDI 가 내 것인가."""
        return self._addressed_to_me(message, target_field='target_pc_id')

    def accepts_feedback(self, message: Any) -> bool:
        """네트워크에서 온 페이더 명령이 내 장치로 갈 것인가."""
        if not self.holds_device:
            return False
        return self._addressed_to_me(message, target_field='target_pc_id')

    # ----------------------------------------------------------------- #
    # 내부
    # ----------------------------------------------------------------- #

    def _addressed_to_me(self, message: Any, *, target_field: str) -> bool:
        if not self.group_id or not self.pc_id:
            return False
        if str(getattr(message, 'group_id', '') or '') != self.group_id:
            return False
        if str(getattr(message, target_field, '') or '') != self.pc_id:
            return False
        # 내가 보낸 것이 돌아온 것은 받지 않는다
        return str(getattr(message, 'source_pc_id', '') or '') != self.pc_id

    def snapshot(self) -> dict:
        return {
            'pc_id': self.pc_id,
            'device_pc_id': self.device_pc_id,
            'target_pc_id': self.target_pc_id,
            'holds_device': self.holds_device,
            'is_target': self.is_target,
            'relaying': self.relaying,
        }


class SequenceGate:
    """늦게 도착한 옛 값을 버린다 · §6-94

    200Hz 최선형 전송이라 순서가 뒤집힐 수 있다 · 지난 값을 그대로 쓰면 모터가
    잠깐 뒤로 갔다 온다.
    """

    def __init__(self) -> None:
        self._last: Optional[int] = None

    def accepts(self, sequence: Any) -> bool:
        try:
            number = int(sequence)
        except (TypeError, ValueError):
            return False
        if self._last is not None and number <= self._last:
            return False
        self._last = number
        return True

    def reset(self) -> None:
        """연결이 새로 서면 번호도 새로 센다."""
        self._last = None
