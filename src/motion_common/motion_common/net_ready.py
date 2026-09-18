"""랜 주소가 생겼는가 · §6-96

**DDS 는 뜨는 순간의 랜카드만 본다.**

Fast DDS 는 참가자를 만드는 그 한 순간에 인터페이스를 훑어 자기 주소를 정하고,
그 뒤로는 다시 보지 않는다 · 그래서 주소가 없을 때 뜬 노드는 루프백 주소
(`127.0.0.1`)만 광고하고, 나중에 랜이 살아나도 **영영 다른 PC 를 못 본다** ·
화면에는 그냥 「통신 단절」로만 보여서 원인을 알 길이 없다.

서비스 유닛에 `After=network-online.target` 을 적어도 소용없다 · 그 타깃은
**시스템 스코프에만** 있고 우리 서비스는 사용자 스코프라, systemd 가 없는 유닛을
조용히 무시한다 · 그래서 기다리는 일을 여기서 직접 한다.

실행 스크립트가 ROS 를 켜기 **전에** 부르므로 표준 라이브러리만 쓴다 ·
`group_env.py` 와 같은 규칙이다.

    python3 .../net_ready.py

"랜 주소가 무엇인가"의 주인은 이 파일 하나다 · 기다리는 쪽(실행 스크립트)과
늦었는지 보는 쪽(연동 노드)이 각자 세면 둘이 갈린다.
"""

from __future__ import annotations

import fcntl
import os
import socket
import struct
import sys
import time

#: `SIOCGIFADDR` · 인터페이스 하나의 IPv4 주소를 묻는 ioctl 번호
_SIOCGIFADDR = 0x8915

#: 인터페이스 이름 길이 제한 · `IFNAMSIZ` 15 자 + 끝의 NUL
_IFNAMSIZ = 15

#: 랜을 기다리는 최대 시간 · 이 시간이 지나면 포기하고 그냥 띄운다
#:
#: 랜이 영영 없는 PC(혼자 쓰는 PC · 공유기가 죽은 현장)도 **떠야 한다** ·
#: 연동만 못 할 뿐 모션과 웹은 멀쩡히 돌아야 하므로 무한히 기다리지 않는다.
#:
#: 60초인 이유 · DHCP 는 보통 몇 초면 끝난다 · 반대로 이 값이 크면 랜 없는 PC
#: 에서 서비스가 다시 뜰 때마다 그만큼 웹이 죽어 있다 · 넉넉하되 짧게 잡는다.
DEFAULT_TIMEOUT_SEC = 60.0

#: 현장에서 바꾸는 손잡이 · `MOTION_NET_WAIT_SEC`
#:
#: DHCP 가 느린 현장은 늘리고, 랜을 아예 안 쓰는 PC 는 `0` 으로 꺼 둔다 ·
#: `MOTION_GROUP_NETWORK=0` 과 같은 결의 되돌릴 수 있는 장치다.
TIMEOUT_ENV = 'MOTION_NET_WAIT_SEC'

#: 다시 확인하는 간격
POLL_SEC = 0.5


def lan_addresses() -> tuple[str, ...]:
    """지금 이 PC 에 붙어 있는 루프백 아닌 IPv4 주소들 · 정렬해서 돌려준다.

    주소가 하나라도 있으면 DDS 가 바깥을 향해 열 수 있다는 뜻이다 ·
    "어느 인터페이스라야 한다"까지는 따지지 않는다 · 그건 현장마다 다르고
    (유선일 수도 WiFi 일 수도 있다) 여기서 정하면 틀린 현장이 생긴다 ·
    대신 **기동 뒤에 주소가 바뀌었는지**를 연동 노드가 따로 본다.
    """
    found: set[str] = set()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        for _index, name in socket.if_nameindex():
            request = struct.pack('256s', name.encode('utf-8')[:_IFNAMSIZ])
            try:
                packed = fcntl.ioctl(sock.fileno(), _SIOCGIFADDR, request)
            except OSError:
                # 아직 주소가 없는 인터페이스 · 기다리는 중이니 정상이다
                continue
            address = socket.inet_ntoa(packed[20:24])
            if address.startswith('127.'):
                continue
            found.add(address)
    finally:
        sock.close()
    return tuple(sorted(found))


def configured_timeout_sec(environ=None) -> float:
    """기다릴 시간 · 환경변수가 이기고, 못 읽으면 기본값으로 물러난다."""
    raw = (environ if environ is not None else os.environ).get(TIMEOUT_ENV)
    if raw is None or not str(raw).strip():
        return DEFAULT_TIMEOUT_SEC
    try:
        seconds = float(str(raw).strip())
    except ValueError:
        return DEFAULT_TIMEOUT_SEC
    return max(0.0, seconds)


def wait_for_lan(
    timeout_sec: float | None = None,
    poll_sec: float = POLL_SEC,
    report=None,
) -> tuple[str, ...]:
    """랜 주소가 하나라도 생길 때까지 기다린다 · 생긴 주소들을 돌려준다.

    시간이 다 되면 **빈 값을 돌려주고 끝낸다** · 막지 않는다 ·
    부르는 쪽은 이 값이 비었는지로 "랜 없이 뜬다"를 알 수 있다.
    """
    say = report if report is not None else _say
    if timeout_sec is None:
        timeout_sec = configured_timeout_sec()
    deadline = time.monotonic() + timeout_sec
    addresses = lan_addresses()
    if addresses:
        return addresses

    say(f'랜 주소를 기다립니다 · 최대 {timeout_sec:.0f}초')
    while time.monotonic() < deadline:
        time.sleep(poll_sec)
        addresses = lan_addresses()
        if addresses:
            say(f'랜 주소 확인 · {", ".join(addresses)}')
            return addresses

    say(
        f'랜 주소가 {timeout_sec:.0f}초 안에 생기지 않았습니다 · '
        '연동 없이 시작합니다'
    )
    return ()


def _say(message: str) -> None:
    """서비스 로그(journal)에 남도록 표준 오류로 적는다."""
    print(f'[net_ready] {message}', file=sys.stderr, flush=True)


def main() -> int:
    """실행 스크립트가 ROS 를 켜기 전에 부른다.

    **어떤 경우에도 0 으로 끝난다** · 여기서 실패해서 서비스가 안 뜨면
    랜 문제가 모션 전체를 멈추는 더 큰 고장이 된다.
    """
    try:
        wait_for_lan()
    except Exception as exc:  # noqa: BLE001 · 여기서 죽으면 서비스가 안 뜬다
        _say(f'랜 확인을 건너뜁니다 · {exc}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
