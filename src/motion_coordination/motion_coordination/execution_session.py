"""Owned mutable state for one DDS group execution session."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict


@dataclass
class ExecutionSession:
    execution_id: str = ''
    coordinator_id: str = ''
    participants: tuple[str, ...] = ()
    stopped_members: set[str] = field(default_factory=set)
    pending_command: str = ''
    pending_command_id: str = ''
    pending_acks: set[str] = field(default_factory=set)
    pending_ack_deadline: float = 0.0
    pending_scheduled_at: float = 0.0
    motion_start_report_deadline: float = 0.0
    motion_start_report_cycle: int = 0
    retry_attempt: int = 0
    retry_root_execution_id: str = ''
    retry_pending: Dict[str, Any] = field(default_factory=dict)
    stop_confirmation_deadline: float = 0.0

    def activate(
        self, execution_id: str, coordinator_id: str,
        participants: tuple[str, ...],
    ) -> None:
        self.clear_active()
        self.execution_id = str(execution_id)
        self.coordinator_id = str(coordinator_id)
        self.participants = tuple(participants)

    def clear_active(self) -> None:
        """Release the active lease while preserving an intentional retry."""
        self.execution_id = ''
        self.coordinator_id = ''
        self.participants = ()
        self.stopped_members.clear()
        self.pending_command = ''
        self.pending_command_id = ''
        self.pending_acks.clear()
        self.pending_ack_deadline = 0.0
        self.pending_scheduled_at = 0.0
        self.motion_start_report_deadline = 0.0
        self.motion_start_report_cycle = 0

    def reset(self) -> None:
        self.clear_active()
        self.retry_attempt = 0
        self.retry_root_execution_id = ''
        self.retry_pending.clear()
        self.stop_confirmation_deadline = 0.0
