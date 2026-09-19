"""다이나믹셀 모델을 못 읽던 이유 · §6-200

**모터 매니저가 같은 시리얼 포트를 계속 쓰고 있다.**

다이나믹셀 검색은 매니저를 멈추지 않는다 (EtherCAT 과 달리 소유권을 뺏을
필요가 없다) · 그래서 검색이 브로드캐스트 핑을 쏘는 동안에도 매니저의
읽기·쓰기 패킷이 같은 선으로 오간다.

전에는 상태 패킷이면 **무엇이든** 핑 답장으로 받아들였다.

    'model_number': params[0] | (params[1] << 8) if len(params) >= 2 else None

매니저의 쓰기 응답은 파라미터가 **0바이트**다 · 그것을 핑 답장으로 세면
`model_number: None`, `firmware_version: None` 이 되어 화면에

    Dynamixel XM540-W150 · ID 3 · **FW 미수신**

처럼 「모델 미확인」이 뜨고 체크가 자동으로 안 켜졌다 · **모터는 멀쩡히
돌고 있는데도** (적용하면 축 2·3 이 online 이었다).

실제 검색 결과가 이랬다.

    {"id": 3, "model_number": null, "firmware_version": null,
     "source": "broadcast_ping"}

PING 답장은 규격상 파라미터가 **정확히 3바이트**다 (모델 2 + 펌웨어 1) ·
그보다 짧으면 다른 명령의 답장이므로 흘려보낸다 · 진짜 핑 답장이 뒤이어 온다.
"""

import pytest

from motion_state_monitor.dynamixel_scanner import DynamixelScanner


def _packet(params: bytes, dxl_id: int = 3) -> dict:
    return {'id': dxl_id, 'error': 0, 'params': params}


def test_a_real_ping_reply_is_kept():
    """모델 2바이트 + 펌웨어 1바이트 · XM540-W150 은 1120번이다."""
    reply = DynamixelScanner._ping_reply_or_none(_packet(bytes([0x60, 0x04, 0x2E])))

    assert reply is not None
    params = reply['params']
    assert params[0] | (params[1] << 8) == 1120
    assert params[2] == 0x2E


def test_a_write_acknowledgement_is_ignored():
    """**이것이 그 버그다** · 매니저의 쓰기 응답은 파라미터가 없다."""
    assert DynamixelScanner._ping_reply_or_none(_packet(b'')) is None


@pytest.mark.parametrize('params', [b'', b'\x60', b'\x60\x04'])
def test_anything_shorter_than_a_ping_reply_is_ignored(params):
    """2바이트짜리도 버린다 · 펌웨어까지 와야 진짜 핑 답장이다."""
    assert DynamixelScanner._ping_reply_or_none(_packet(params)) is None


def test_a_longer_reply_is_still_accepted():
    """규격보다 길게 오는 장치가 있어도 앞 3바이트는 유효하다."""
    assert DynamixelScanner._ping_reply_or_none(
        _packet(bytes([0x60, 0x04, 0x2E, 0x00]))
    ) is not None


@pytest.mark.parametrize('nothing', [None, {}, {'params': None}])
def test_nothing_is_not_a_ping_reply(nothing):
    """패킷이 아예 없을 때도 터지지 않아야 한다."""
    assert DynamixelScanner._ping_reply_or_none(nothing) is None


def test_both_ping_paths_go_through_the_same_gate():
    """브로드캐스트와 개별 핑 둘 다 걸러야 한다 · 한쪽만 고치면 또 샌다."""
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[1]
        / 'motion_state_monitor' / 'dynamixel_scanner.py'
    ).read_text(encoding='utf-8')

    assert source.count('_ping_reply_or_none(') == 3   # 정의 1 + 부르는 곳 2
