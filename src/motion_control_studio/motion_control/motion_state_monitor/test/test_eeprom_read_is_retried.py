"""EEPROM 을 한 번 못 읽어 검색 전체가 실패하던 것 · §6-230

    모터 검색 실패 · AC Servo 2축 · Dynamixel 2축
    · Master 0 · Slave 0: SII EEPROM 헤더가 32바이트보다 짧습니다

서보도 로보티즈도 **네 대를 다 찾은 뒤**에 이렇게 떴다 · `ethercat rescan`
직후에는 EEPROM 읽기가 가끔 32바이트도 안 되게 돌아온다 (평소에는 늘
1380바이트다) · 목록이 안정된 것과 EEPROM 을 읽을 수 있는 것이 같지 않다.

한 번 실패하면 그 슬레이브의 alias 를 모르게 되고, 프로젝트에 저장된 alias
와 「불일치」로 판정되어 검색 전체가 실패가 된다 · **못 읽은 것이 다른 것으로
둔갑한다.**

읽기 한 번이 3ms 라 곧바로 다시 요청한다 · 기다리지 않는다.
"""

import pytest

from motion_state_monitor.ethercat_scanner import EthercatScanner, SII_READ_ATTEMPTS


def _scanner(results):
    scanner = EthercatScanner.__new__(EthercatScanner)
    calls = []

    def once(master_index, slave_position):
        calls.append((master_index, slave_position))
        return results[min(len(calls) - 1, len(results) - 1)]

    scanner._read_sii_identity_once = once
    scanner.calls = calls
    return scanner


_OK = {'ethercat_alias': 103, 'serial_number': 402982152, 'sii_error': ''}
_SHORT = {'ethercat_alias': None, 'sii_error': 'SII EEPROM 헤더가 32바이트보다 짧습니다'}


def test_a_good_read_is_used_right_away():
    scanner = _scanner([_OK])

    assert scanner._read_sii_identity(0, 0) == _OK
    assert len(scanner.calls) == 1, '한 번에 됐는데 또 읽었습니다'


def test_a_short_read_is_asked_again():
    """**이것이 그 버그다** · 한 번 짧게 왔다고 검색 전체를 실패시키지 않는다."""
    scanner = _scanner([_SHORT, _OK])

    assert scanner._read_sii_identity(0, 0) == _OK
    assert len(scanner.calls) == 2


def test_it_gives_up_after_three_tries():
    scanner = _scanner([_SHORT])

    result = scanner._read_sii_identity(0, 0)

    assert result['sii_error']
    assert len(scanner.calls) == SII_READ_ATTEMPTS == 3


def test_every_try_asks_the_same_slave():
    scanner = _scanner([_SHORT])

    scanner._read_sii_identity(1, 2)

    assert scanner.calls == [(1, 2)] * 3
