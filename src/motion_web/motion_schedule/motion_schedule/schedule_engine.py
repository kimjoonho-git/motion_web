import time
import logging
from datetime import datetime
from typing import List, Optional, Tuple, Set
from motion_common.schedule_models import ScheduleItem

logger = logging.getLogger("motion_schedule.engine")


class ScheduleEngine:
    def __init__(self, grace_period_sec: int = 30):
        self.grace_period_sec = grace_period_sec
        self.previous_check_time: Optional[datetime] = None
        self.triggered_start_keys: Set[str] = set()
        self.triggered_stop_keys: Set[str] = set()
        
        # Track active schedule duration
        self.active_schedule_id: Optional[str] = None
        self.active_start_monotonic: Optional[float] = None
        self.active_duration_sec: Optional[int] = None

    def _parse_time(self, time_str: str) -> Tuple[int, int, int]:
        parts = [int(p) for p in time_str.split(":")]
        if len(parts) == 2:
            return parts[0], parts[1], 0
        elif len(parts) >= 3:
            return parts[0], parts[1], parts[2]
        return 0, 0, 0

    def _is_day_matched(self, item: ScheduleItem, now: datetime) -> bool:
        if item.repeat_type == "once":
            if item.run_date:
                today_str = now.strftime("%Y-%m-%d")
                return today_str == item.run_date
            return True
        elif item.repeat_type == "daily":
            return True
        elif item.repeat_type == "weekly":
            day_abbrs = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]
            today_abbr = day_abbrs[now.weekday()]
            return today_abbr in item.repeat_days
        return False

    def tick(self, now: datetime, schedules: List[ScheduleItem]) -> List[Tuple[str, ScheduleItem]]:
        """
        Returns list of (action, ScheduleItem) tuples.
        action: 'start' or 'stop-after-cycle'
        """
        actions: List[Tuple[str, ScheduleItem]] = []
        mono_now = time.monotonic()

        # 1. Check Duration stop for active schedule
        if self.active_schedule_id and self.active_start_monotonic and self.active_duration_sec:
            elapsed = mono_now - self.active_start_monotonic
            if elapsed >= self.active_duration_sec:
                active_item = next((s for s in schedules if s.schedule_id == self.active_schedule_id), None)
                stop_key = f"{self.active_schedule_id}:{now.strftime('%Y-%m-%d')}:duration_stop"
                if stop_key not in self.triggered_stop_keys:
                    self.triggered_stop_keys.add(stop_key)
                    logger.info(f"[Engine] Duration reached ({elapsed:.1f}s >= {self.active_duration_sec}s). Triggering stop-after-cycle.")
                    if active_item:
                        actions.append(("stop-after-cycle", active_item))
                    self.active_schedule_id = None
                    self.active_start_monotonic = None
                    self.active_duration_sec = None

        # 2. Process all enabled schedules
        for item in schedules:
            if not item.enabled:
                continue

            if not self._is_day_matched(item, now):
                continue

            today_str = now.strftime("%Y-%m-%d")
            sh, sm, ss = self._parse_time(item.start_time)
            scheduled_start = now.replace(hour=sh, minute=sm, second=ss, microsecond=0)

            start_key = f"{item.schedule_id}:{today_str}:start"
            stop_key = f"{item.schedule_id}:{today_str}:stop"

            # Check Start Trigger
            if start_key not in self.triggered_start_keys:
                delay = (now - scheduled_start).total_seconds()
                # If current time is within 300 seconds after scheduled_start
                if 0 <= delay <= 300:
                    self.triggered_start_keys.add(start_key)
                    logger.info(f"[Engine] Start time matched for '{item.schedule_name}' ({item.start_time}, delay={delay:.1f}s). Triggering start.")
                    actions.append(("start", item))
                    
                    if item.stop_mode == "duration" and item.duration_sec:
                        self.active_schedule_id = item.schedule_id
                        self.active_start_monotonic = mono_now
                        self.active_duration_sec = item.duration_sec

            # Check Time Stop Trigger
            if item.stop_mode == "time" and item.stop_time:
                eh, em, es = self._parse_time(item.stop_time)
                scheduled_stop = now.replace(hour=eh, minute=em, second=es, microsecond=0)

                if stop_key not in self.triggered_stop_keys:
                    stop_delay = (now - scheduled_stop).total_seconds()
                    if 0 <= stop_delay <= 60:
                        self.triggered_stop_keys.add(stop_key)
                        logger.info(f"[Engine] Stop time matched for '{item.schedule_name}' ({item.stop_time}, delay={stop_delay:.1f}s). Triggering stop-after-cycle.")
                        actions.append(("stop-after-cycle", item))
                        if self.active_schedule_id == item.schedule_id:
                            self.active_schedule_id = None
                            self.active_start_monotonic = None
                            self.active_duration_sec = None

        self.previous_check_time = now
        return actions
