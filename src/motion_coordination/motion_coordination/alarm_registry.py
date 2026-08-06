"""Boot-aware peer alarm ordering and storage."""

from __future__ import annotations

from typing import Any, Dict, Optional


class AlarmRegistry:
    def __init__(self) -> None:
        self.alarms: Dict[str, Dict[str, Any]] = {}
        self.versions: Dict[str, tuple[str, int]] = {}
        self._previous_boot_ids: Dict[str, set[str]] = {}
        self._pending_by_pc: Dict[str, Any] = {}

    def accept(self, message: Any, current_boot_id: str = '') -> bool:
        pc_id = str(message.pc_id)
        boot_id = str(message.boot_id)
        if current_boot_id and boot_id != current_boot_id:
            if boot_id in self._previous_boot_ids.get(pc_id, set()):
                return False
            self._pending_by_pc[pc_id] = message
            return False
        previous = self.versions.get(pc_id)
        sequence = int(message.sequence)
        if previous is not None and previous[0] == boot_id and sequence <= previous[1]:
            return False
        self.versions[pc_id] = (boot_id, sequence)
        return True

    def member_boot_changed(
        self, pc_id: str, boot_id: str, previous_boot_id: str = '',
    ) -> Optional[Any]:
        if previous_boot_id and previous_boot_id != boot_id:
            self._previous_boot_ids.setdefault(pc_id, set()).add(previous_boot_id)
        pending = self._pending_by_pc.get(pc_id)
        if pending is not None and str(pending.boot_id) == str(boot_id):
            self._pending_by_pc.pop(pc_id, None)
            return pending
        return None

    def set(self, pc_id: str, alarm: Dict[str, Any]) -> None:
        self.alarms[str(pc_id)] = dict(alarm)

    def remove(self, pc_id: str) -> None:
        self.alarms.pop(str(pc_id), None)

    def clear_coordination(self) -> None:
        self.alarms = {
            pc_id: alarm for pc_id, alarm in self.alarms.items()
            if alarm.get('error_source') != 'group_coordination'
        }
