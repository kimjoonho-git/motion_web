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
    #: 이 스케줄을 저장할 때 그 PC 의 시간대 · §6-150
    #:
    #: **판단에는 쓰지 않는다** · 시각은 지금 이 PC 의 시간대로 해석한다 ·
    #: 전시장 운영자가 원하는 건 "현지 09시" 지 "서울 기준 몇 시" 가 아니다.
    #:
    #: 이 값은 오직 **어긋남을 알아채는 지문**이다 · PC 를 들고 나가서
    #: 네트워크에 붙였는데 시간대가 안 바뀐 경우, 스케줄이 자기와 PC 가
    #: 어긋난 걸 스스로 알 수 있다 · NTP 는 시간대를 안 고쳐준다.
    saved_timezone: Optional[str] = None

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
            saved_timezone=data.get("saved_timezone"),
        )

    def to_dict(self) -> Dict[str, Any]:
        res = asdict(self)
        return res
