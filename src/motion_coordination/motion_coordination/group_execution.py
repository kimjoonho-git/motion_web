"""Transport-independent state machine for one DDS group execution."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Dict, Iterable, Optional


@dataclass
class Member:
    pc_id: str
    boot_id: str
    joined: bool
    is_master: bool
    state: str
    trigger_sync_state: str
    trigger_sync_uncertainty_ms: float
    alarm_grade: int
    received_monotonic: float
    sequence: int = 0
    display_name: str = ''
    git_branch: str = ''
    git_hash: str = ''
    git_message: str = ''
    motion_phase: str = ''
    motion_elapsed_sec: float = 0.0
    motion_duration_sec: float = 0.0
    motion_progress_ratio: float = 0.0
    current_cycle: int = 0
    display_step: str = ''


class MemberRegistry:
    def __init__(self, *, warning_timeout_sec: float = 1.5, timeout_sec: float = 3.0):
        self.warning_timeout_sec = float(warning_timeout_sec)
        self.timeout_sec = float(timeout_sec)
        self._members: Dict[str, Member] = {}

    def update(self, member: Member) -> None:
        previous = self._members.get(member.pc_id)
        if previous and previous.boot_id == member.boot_id:
            if member.sequence and member.sequence <= previous.sequence:
                return
            if member.received_monotonic < previous.received_monotonic:
                return
        self._members[member.pc_id] = member

    def status(self, pc_id: str, *, now: Optional[float] = None) -> str:
        member = self._members.get(pc_id)
        if member is None:
            return 'offline'
        age = (time.monotonic() if now is None else float(now)) - member.received_monotonic
        if age >= self.timeout_sec:
            return 'offline'
        if age >= self.warning_timeout_sec:
            return 'warning'
        return 'online'

    def live_joined(self, *, now: Optional[float] = None) -> tuple[str, ...]:
        return tuple(sorted(
            pc_id for pc_id, member in self._members.items()
            if member.joined and self.status(pc_id, now=now) == 'online'
        ))

    def joined(self) -> tuple[str, ...]:
        """Return every explicitly joined member, including delayed members."""
        return tuple(sorted(
            pc_id for pc_id, member in self._members.items() if member.joined
        ))

    def member(self, pc_id: str) -> Optional[Member]:
        return self._members.get(pc_id)


@dataclass
class ScheduledAction:
    command: str
    execution_id: str
    cycle_number: int
    scheduled_at: float
    command_id: str = field(default_factory=lambda: f'cmd-{uuid.uuid4().hex}')


class GroupExecution:
    """Coordinator-side barrier state; it never executes a local motion itself."""

    def __init__(self, *, start_lead_sec: float = 0.5, max_start_spread_ms: float = 20.0):
        self.start_lead_sec = float(start_lead_sec)
        self.max_start_spread_ms = float(max_start_spread_ms)
        self.reset()

    def reset(self) -> None:
        self.execution_id = ''
        self.coordinator_id = ''
        self.participants: tuple[str, ...] = ()
        self.state = 'idle'
        self.cycle_number = 0
        self.ready: set[str] = set()
        self.armed: set[str] = set()
        self.initialize_triggered: Dict[str, float] = {}
        self.cycle_ready: set[str] = set()
        self.motion_completed: set[str] = set()
        self.cycle_initialized: set[str] = set()
        self.scheduled: set[str] = set()
        self.triggered: Dict[str, float] = {}
        self.stop_after_cycle = False
        self.run_mode = 'continuous'
        self.repeat_mode = 'reinitialize'
        self.dwell_sec = 0.0
        self.initialization_only = False
        self.last_start_spread_ms: Optional[float] = None
        self.last_initialize_spread_ms: Optional[float] = None
        self.pending_command = ''
        self.pending_command_id = ''
        self.pending_acks: set[str] = set()
        self.pending_ack_deadline = 0.0
        self.pending_scheduled_at = 0.0
        self.motion_start_report_deadline = 0.0
        self.release_error = False

    def activate_claim(
        self, execution_id: str, coordinator_id: str,
        participants: Iterable[str],
    ) -> None:
        """Accept one peer-owned execution as this node's active lease."""
        self.reset()
        self.execution_id = str(execution_id)
        self.coordinator_id = str(coordinator_id)
        self.participants = tuple(participants)
        self.state = 'preparing'

    def clear_active(self) -> None:
        """Release the active lease while keeping its final display state."""
        self.execution_id = ''
        self.coordinator_id = ''
        self.participants = ()
        self.pending_command = ''
        self.pending_command_id = ''
        self.pending_acks.clear()
        self.pending_ack_deadline = 0.0
        self.pending_scheduled_at = 0.0
        self.motion_start_report_deadline = 0.0
        self.release_error = False

    def begin(
        self,
        coordinator_id: str,
        participants: Iterable[str],
        *,
        run_mode: str = 'continuous',
        repeat_mode: str = 'reinitialize',
        dwell_sec: float = 0.0,
        initialization_only: bool = False,
    ) -> str:
        selected = tuple(sorted(set(str(value) for value in participants if str(value))))
        if not 1 <= len(selected) <= 8:
            raise ValueError('그룹 실행 참가 PC는 1~8대여야 합니다')
        if coordinator_id not in selected:
            raise ValueError('임시 진행 PC가 참가 목록에 없습니다')
        if self.execution_id:
            raise ValueError('이전 그룹 실행 정리 확인 중입니다')
        if self.state not in {'idle', 'stopped', 'error'}:
            raise ValueError('다른 그룹 실행이 진행 중입니다')
        self.reset()
        self.execution_id = f'exec-{uuid.uuid4().hex}'
        self.coordinator_id = coordinator_id
        self.participants = selected
        self.run_mode = str(run_mode)
        self.repeat_mode = str(repeat_mode)
        self.dwell_sec = float(dwell_sec)
        self.initialization_only = bool(initialization_only)
        self.state = 'preparing'
        return self.execution_id

    def mark_ready(self, pc_id: str) -> None:
        self._participant(pc_id)
        if self.state != 'preparing':
            raise ValueError('준비 응답을 받을 수 있는 상태가 아닙니다')
        self.ready.add(pc_id)

    def initialize_action(self, *, now: float) -> ScheduledAction:
        if self.ready != set(self.participants):
            raise ValueError('전체 PC 실행 준비가 완료되지 않았습니다')
        self.state = 'initializing'
        self.scheduled.clear()
        return ScheduledAction(
            'initialize_at', self.execution_id, 0,
            float(now) + self.start_lead_sec,
        )

    def mark_armed(self, pc_id: str, triggered_at: float = 0.0) -> None:
        self._participant(pc_id)
        if self.state not in {'initializing', 'armed'}:
            raise ValueError('초기 위치 완료를 받을 수 있는 상태가 아닙니다')
        self.armed.add(pc_id)
        if triggered_at > 0.0:
            self.initialize_triggered[pc_id] = float(triggered_at)
        if self.armed == set(self.participants):
            if self.initialize_triggered.keys() >= set(self.participants):
                values = list(self.initialize_triggered.values())
                self.last_initialize_spread_ms = (max(values) - min(values)) * 1000.0
            self.state = 'armed'

    def start_action(self, *, now: float) -> ScheduledAction:
        if self.state == 'armed':
            next_cycle = 1
        elif (
            self.state == 'cycle_ready'
            and self.cycle_initialized == set(self.participants)
        ):
            next_cycle = self.cycle_number + 1
        else:
            raise ValueError('전체 PC가 다음 모션을 시작할 준비가 되지 않았습니다')
        self.state = 'start_scheduled'
        self.cycle_ready.clear()
        self.motion_completed.clear()
        self.scheduled.clear()
        self.triggered.clear()
        return ScheduledAction(
            'start_at', self.execution_id, next_cycle,
            float(now) + self.start_lead_sec,
        )

    def mark_scheduled(self, pc_id: str, cycle_number: int) -> None:
        self._participant(pc_id)
        if self.state != 'start_scheduled' or cycle_number != self.cycle_number + 1:
            raise ValueError('시작 예약 회차가 일치하지 않습니다')
        self.scheduled.add(pc_id)

    def mark_triggered(self, pc_id: str, cycle_number: int, triggered_at: float) -> None:
        self._participant(pc_id)
        if cycle_number != self.cycle_number + 1:
            raise ValueError('모션 시작 회차가 일치하지 않습니다')
        self.triggered[pc_id] = float(triggered_at)
        if self.triggered.keys() >= set(self.participants):
            values = list(self.triggered.values())
            self.last_start_spread_ms = (max(values) - min(values)) * 1000.0
            self.cycle_number = cycle_number
            self.state = 'running'

    def mark_cycle_ready(self, pc_id: str, cycle_number: int) -> None:
        self._participant(pc_id)
        if cycle_number != self.cycle_number:
            raise ValueError('준비 완료 회차가 일치하지 않습니다')
        self.cycle_ready.add(pc_id)
        if self.cycle_ready == set(self.participants):
            self.state = 'cycle_ready'

    def mark_motion_completed(self, pc_id: str, cycle_number: int) -> None:
        """Hold every participant at the motion-completed barrier."""
        self._participant(pc_id)
        if self.state != 'running' or cycle_number != self.cycle_number:
            raise ValueError('모션 완료 회차가 일치하지 않습니다')
        self.motion_completed.add(pc_id)
        if self.motion_completed == set(self.participants):
            self.state = 'motion_completed'

    def cycle_initialize_action(self, *, now: float) -> ScheduledAction:
        """Schedule the next cycle's initialization only after all motions finish."""
        if (
            self.state != 'motion_completed'
            or self.motion_completed != set(self.participants)
        ):
            raise ValueError('전체 PC 모션 완료 전에는 회차 초기화를 시작할 수 없습니다')
        self.state = 'cycle_initializing'
        self.cycle_initialized.clear()
        self.scheduled.clear()
        return ScheduledAction(
            'cycle_initialize_at', self.execution_id, self.cycle_number,
            float(now) + self.start_lead_sec,
        )

    def mark_cycle_initialized(self, pc_id: str, cycle_number: int) -> None:
        self._participant(pc_id)
        if self.state != 'cycle_initializing' or cycle_number != self.cycle_number:
            raise ValueError('회차 초기화 완료 회차가 일치하지 않습니다')
        self.cycle_initialized.add(pc_id)
        if self.cycle_initialized == set(self.participants):
            self.state = 'cycle_ready'

    def request_stop_after_cycle(self) -> None:
        self.stop_after_cycle = True
        if self.state in {'preparing', 'initializing', 'armed', 'cycle_ready', 'start_scheduled'}:
            self.state = 'stopped'

    def stop_now(self, *, error: bool = False) -> None:
        self.state = 'error' if error else 'stopped'

    def trigger_within_tolerance(self) -> Optional[bool]:
        if self.last_start_spread_ms is None:
            return None
        return self.last_start_spread_ms <= self.max_start_spread_ms

    def initialize_within_tolerance(self) -> Optional[bool]:
        if self.last_initialize_spread_ms is None:
            return None
        return self.last_initialize_spread_ms <= self.max_start_spread_ms

    def _participant(self, pc_id: str) -> None:
        if pc_id not in self.participants:
            raise ValueError('그룹 실행 참가 PC가 아닙니다')
