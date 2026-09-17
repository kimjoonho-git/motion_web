"""지금 어느 스케줄 구간 안인가 · §6-137

**「시각을 지나갔나」가 아니라 「지금 구간 안인가」를 본다.**

전에는 시작 시각을 지나고 5분 안에만 시작했고, 종료 시각을 지나고 60초
안에만 정지했다 · 그 창을 놓치면 그날은 끝이었다.

    09:00~18:00 스케줄 · 13:00 에 재부팅  →  그날 하루 종일 안 돌았다
    18:00 에 서비스 재시작 중              →  종료 명령을 못 받아 계속 돌았다

지금은 상태를 본다 · 구간 안이면 돌아야 하고, 밖이면 멈춰야 한다 · 그래서
재부팅해도, 놓쳐도, 어긋나도 다음 점검에서 스스로 맞춘다.

정지 방식은 **지정 시각 하나**다 · 유지시간(초)은 없앴다 · 시작한 순간부터
세는 값이라 프로그램이 재시작하면 카운트가 사라져, 상태로 보는 방식과
원리적으로 맞지 않았다.
"""

from datetime import datetime, timedelta
from typing import List, Optional

from motion_common.schedule_models import ScheduleItem

DAY_ABBREVIATIONS = ('MON', 'TUE', 'WED', 'THU', 'FRI', 'SAT', 'SUN')


def parse_time_of_day(value) -> Optional[tuple]:
    """"HH:MM" · "HH:MM:SS" 를 (시, 분, 초) 로 · 못 읽으면 None."""
    parts = str(value or '').strip().split(':')
    if len(parts) < 2:
        return None
    try:
        numbers = [int(part) for part in parts[:3]]
    except ValueError:
        return None
    while len(numbers) < 3:
        numbers.append(0)
    hour, minute, second = numbers
    if not (0 <= hour <= 23 and 0 <= minute <= 59 and 0 <= second <= 59):
        return None
    return hour, minute, second


def _at(day: datetime, time_of_day: tuple) -> datetime:
    hour, minute, second = time_of_day
    return day.replace(hour=hour, minute=minute, second=second, microsecond=0)


def _day_matches(item: ScheduleItem, day: datetime) -> bool:
    """그 날짜에 이 스케줄이 시작하는가 · 시작 날짜 기준으로 본다."""
    repeat_type = str(item.repeat_type or 'daily')
    if repeat_type == 'daily':
        return True
    if repeat_type == 'once':
        if not item.run_date:
            return True
        return day.strftime('%Y-%m-%d') == str(item.run_date)
    if repeat_type == 'weekly':
        return DAY_ABBREVIATIONS[day.weekday()] in (item.repeat_days or ())
    return False


def window_of(item: ScheduleItem, start_day: datetime) -> Optional[tuple]:
    """그 날 시작하는 이 스케줄의 구간 · [시작, 종료) · 없으면 None.

    종료가 시작보다 이르면 **자정을 넘긴 것**으로 본다 · 23:00~01:00 은
    그날 23시부터 다음 날 1시까지다.
    """
    start_time = parse_time_of_day(item.start_time)
    stop_time = parse_time_of_day(item.stop_time)
    if start_time is None or stop_time is None:
        return None
    starts_at = _at(start_day, start_time)
    ends_at = _at(start_day, stop_time)
    if ends_at <= starts_at:
        ends_at += timedelta(days=1)
    return starts_at, ends_at


def active_schedule(
    now: datetime, schedules: List[ScheduleItem],
) -> Optional[ScheduleItem]:
    """지금 돌아야 하는 스케줄 · 없으면 None.

    어제 시작해 자정을 넘긴 구간도 본다 · 여러 개가 겹치면 **먼저 시작한**
    것을 고른다 · 순서가 늘 같아야 점검이 흔들리지 않는다.
    """
    found = []
    for item in schedules:
        if not item.enabled:
            continue
        for day in (now - timedelta(days=1), now):
            if not _day_matches(item, day):
                continue
            window = window_of(item, day)
            if window is None:
                continue
            starts_at, ends_at = window
            if starts_at <= now < ends_at:
                found.append((starts_at, item))
                break
    if not found:
        return None
    found.sort(key=lambda pair: (pair[0], str(pair[1].schedule_id)))
    return found[0][1]


class ScheduleEngine:
    """상태를 보는 엔진 · 기억하는 것이 없다.

    전에는 「오늘 이 스케줄을 발화했나」를 메모리에 들고 있었다 · 프로그램이
    재시작하면 사라져서, 같은 구간에 두 번 시작하거나 영영 안 시작했다.
    지금은 아무것도 기억하지 않는다 · 매번 시각만 보고 답한다.
    """

    def active(
        self, now: datetime, schedules: List[ScheduleItem],
    ) -> Optional[ScheduleItem]:
        return active_schedule(now, schedules)
