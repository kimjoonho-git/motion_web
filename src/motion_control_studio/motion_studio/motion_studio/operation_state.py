"""State rules shared by recording, initialization, playback, and stop."""

from __future__ import annotations


class StudioOperationStateMachine:
    """Issue operation tokens and reject work that belongs to a cancelled run."""

    IDLE_STATES = {'idle', 'error'}

    def __init__(self, generation: int = 0) -> None:
        self.generation = max(0, int(generation))

    def require_idle(self, state: str) -> None:
        if state not in self.IDLE_STATES:
            raise ValueError('녹화 또는 재생 중에는 프로젝트를 변경할 수 없습니다')

    def begin(self, state: str) -> int:
        self.require_idle(state)
        self.generation += 1
        return self.generation

    def cancel(self) -> int:
        self.generation += 1
        return self.generation

    def is_active(self, token: int, current_state: str, expected_state: str) -> bool:
        return token == self.generation and current_state == expected_state

    def require_active(
        self, token: int, current_state: str, expected_state: str
    ) -> None:
        if not self.is_active(token, current_state, expected_state):
            raise RuntimeError('사용자가 모션 동작을 정지했습니다')
