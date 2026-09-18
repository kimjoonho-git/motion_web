"""이 PC 가 지금 몇 시라고 믿는가 · §6-147

**시간대는 네트워크가 못 고쳐준다.**

NTP 는 절대 시각(UTC)만 맞춘다 · 시간대는 사람이 `timedatectl` 로 정하는
값이라, PC 를 들고 다른 나라에 가서 네트워크에 붙여도 **바뀌지 않는다** ·
시계는 정확한데 현지 시각만 틀린 상태가 되고, 화면에는 아무 표시도 없다.

한국에서 만든 09:17 스케줄을 그대로 파리에 가져가면 현지 02:17 에 돈다 ·
개장 시각에 안 돌고 아무도 없는 새벽에 돈다 · 하루가 지나야 안다.

그래서 **화면이 늘 말해야 한다** · 시각을 입력하는 그 자리에서 시간대가
보이면, 설치할 때 그 자리에서 걸린다 · 이 파일은 그 한 줄을 만든다.

"이 PC 의 시계"의 주인은 여기 하나다 · `net_ready.py` 와 같은 규칙으로
표준 라이브러리만 쓴다.
"""

from __future__ import annotations

import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

#: 시간대 이름이 적힌 곳 · 프로세스를 띄우지 않고 읽을 수 있다
_TIMEZONE_FILE = Path('/etc/timezone')

#: NTP 동기 여부는 프로세스를 띄워야 안다 · 자주 묻는 값이라 잠깐 기억한다
#:
#: 상태 조회는 1초에도 여러 번 온다 · 그때마다 `timedatectl` 을 띄우면
#: 보여주려던 화면이 오히려 서버를 느리게 만든다 · §6-146 에서 겪었다.
_NTP_CACHE_SEC = 30.0

_ntp_cache: tuple[float, Any] = (0.0, None)


def timezone_name() -> str:
    """`Asia/Seoul` 같은 지역 이름 · 못 읽으면 약칭(`KST`)이라도 돌려준다.

    지역 이름이 중요하다 · 고정 오프셋(`UTC+9`)과 달리 지역 이름이라야
    서머타임을 OS 가 알아서 처리한다 · 화면에 이름을 띄워야 설치자가
    "고정 오프셋으로 잡았구나" 를 알아챌 수 있다.
    """
    try:
        name = _TIMEZONE_FILE.read_text(encoding='utf-8').strip()
        if name:
            return name
    except OSError:
        pass
    try:
        done = subprocess.run(
            ['timedatectl', 'show', '-p', 'Timezone', '--value'],
            capture_output=True, text=True, timeout=2.0, check=False,
        )
        name = (done.stdout or '').strip()
        if name:
            return name
    except (OSError, subprocess.SubprocessError):
        pass
    return time.tzname[0] if time.tzname else ''


def ntp_synced() -> Any:
    """시계가 맞춰져 있는가 · 알 수 없으면 None.

    **모른다와 아니다를 가른다** · 인터넷이 없는 현장에서는 이 값이 `False` 고,
    그건 "시각이 틀어져 있을 수 있다" 는 뜻이라 사람이 직접 맞춰야 한다 ·
    `None`(확인 불가)을 `False` 로 적으면 없는 문제를 만든다.
    """
    global _ntp_cache
    now = time.monotonic()
    fetched_at, value = _ntp_cache
    if fetched_at and now - fetched_at < _NTP_CACHE_SEC:
        return value
    result: Any = None
    try:
        done = subprocess.run(
            ['timedatectl', 'show', '-p', 'NTPSynchronized', '--value'],
            capture_output=True, text=True, timeout=2.0, check=False,
        )
        answer = (done.stdout or '').strip().lower()
        if answer in {'yes', 'true', '1'}:
            result = True
        elif answer in {'no', 'false', '0'}:
            result = False
    except (OSError, subprocess.SubprocessError):
        result = None
    _ntp_cache = (now, result)
    return result


#: 고를 수 있는 지역 이름 · 목록은 바뀌지 않으므로 한 번만 읽는다
_zones_cache: tuple = ()


def timezones() -> tuple:
    """고를 수 있는 지역 이름 전부 · 못 읽으면 빈 목록.

    화면이 여기서 고르게 해야 오타가 없다 · `Europe/Pari` 처럼 한 글자만
    틀려도 `timedatectl` 이 거부하고, 사람은 왜 안 되는지 모른 채 시간이 간다.

    **자동으로 고르지는 않는다** · 위치 기반 자동 시간대는 전시장 네트워크에서
    자주 틀리고, 더 나쁜 건 전시 중에 저절로 바뀔 수 있다는 것이다 · 고르는
    일은 사람이 하고, 여기서는 고를 거리만 준다.
    """
    global _zones_cache
    if _zones_cache:
        return _zones_cache
    try:
        done = subprocess.run(
            ['timedatectl', 'list-timezones'],
            capture_output=True, text=True, timeout=5.0, check=False,
        )
        found = tuple(
            line.strip() for line in (done.stdout or '').splitlines()
            if line.strip()
        )
    except (OSError, subprocess.SubprocessError):
        found = ()
    _zones_cache = found
    return found


def snapshot() -> Dict[str, Any]:
    """화면이 한 줄로 보여줄 것 · 스케줄 엔진이 보는 것과 같은 시각이다.

    엔진도 `datetime.now().astimezone()` 으로 판단한다 · 화면이 다른 방법으로
    시각을 만들면 둘이 갈리고, 갈린 줄도 모른다.
    """
    now = datetime.now().astimezone()
    return {
        'local_time': now.isoformat(),
        'timezone': timezone_name(),
        'abbreviation': now.tzname() or '',
        'utc_offset': now.strftime('%z'),
        'ntp_synced': ntp_synced(),
    }
