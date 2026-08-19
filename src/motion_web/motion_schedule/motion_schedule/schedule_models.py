import uuid
from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field, asdict


@dataclass
class MotionConfig:
    motion_file_id: Optional[str] = None
    mapping_file_id: Optional[str] = None
    repeat_mode: str = "continuous"
    target_cycles: Optional[int] = None


@dataclass
class ScheduleItem:
    schedule_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    schedule_name: str = "New Schedule"
    start_time: str = "09:00:00"
    stop_mode: str = "time"  # "time" or "duration"
    stop_time: Optional[str] = "18:00:00"
    duration_sec: Optional[int] = None
    repeat_type: str = "daily"  # "once", "daily", "weekly"
    repeat_days: List[str] = field(default_factory=lambda: ["MON", "TUE", "WED", "THU", "FRI"])
    run_date: Optional[str] = None  # "YYYY-MM-DD" for "once"
    motion_config: MotionConfig = field(default_factory=MotionConfig)
    enabled: bool = True

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ScheduleItem":
        mc_data = data.get("motion_config", {})
        if isinstance(mc_data, dict):
            mc = MotionConfig(**mc_data)
        else:
            mc = MotionConfig()

        return cls(
            schedule_id=data.get("schedule_id", str(uuid.uuid4())),
            schedule_name=data.get("schedule_name", "New Schedule"),
            start_time=data.get("start_time", "09:00:00"),
            stop_mode=data.get("stop_mode", "time"),
            stop_time=data.get("stop_time"),
            duration_sec=data.get("duration_sec"),
            repeat_type=data.get("repeat_type", "daily"),
            repeat_days=data.get("repeat_days", ["MON", "TUE", "WED", "THU", "FRI"]),
            run_date=data.get("run_date"),
            motion_config=mc,
            enabled=data.get("enabled", True),
        )

    def to_dict(self) -> Dict[str, Any]:
        res = asdict(self)
        return res
