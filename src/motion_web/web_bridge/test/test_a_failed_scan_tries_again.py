"""검색이 5번에 한 번꼴로 실패하던 것 · §6-234

모터 프로그램이 죽은 뒤에도 슬레이브가 `OP` 로 남는 일이 있다 (검색 30회 중
5회 · 실측) · 그러면 「버스를 놨는지」 검사에 걸려 검색이 시작도 못 한다.

    AC Servo 검색 미실행: Motor Manager 정지 후에도 EtherCAT Master 또는
    Slave 운전 상태가 해제되지 않았습니다: Master0 0 103:0 SAFEOP+ERROR
                                                 1 403:0 OP

왜 남는지는 `motor_manager_node` 종료 처리에 달려 있고 그 코드는
`src/motion_system`(별도 저장소)에 있다 · 판정 규칙을 고쳐 봤지만 실패
지점만 옮겨 갔다 (`ethercat_scanner` 의 같은 검사에 걸림).

실패하면 복구 단계가 모터 프로그램을 다시 켠다 · 그 뒤 다시 하면 슬레이브가
정상으로 돌아와 있다 · **사람이 버튼을 다시 누르는 것과 같은 일을 프로그램이
한다.**
"""

import pytest

from motion_web_bridge.scan_orchestrator import ScanOrchestrator


def _orchestrator(results):
    orchestrator = ScanOrchestrator.__new__(ScanOrchestrator)
    calls = []

    def call_service(*args, **kwargs):
        calls.append((args, kwargs))
        return results[min(len(calls) - 1, len(results) - 1)]

    orchestrator._call_service = call_service
    orchestrator.calls = calls
    orchestrator.bridge = type('Bridge', (), {
        'get_logger': lambda _self: type('Log', (), {'warn': lambda _s, _m: None})(),
    })()
    return orchestrator


_OK = {'success': True, 'message': '모터 검색 완료'}
_PARTIAL = {'success': False, 'partial': True, 'message': '부분 완료'}
_FAIL = {'success': False, 'message': 'AC Servo 검색 미실행: ... 해제되지 않았습니다'}


def test_a_good_scan_runs_once():
    orchestrator = _orchestrator([_OK])

    assert orchestrator._scan_with_retry('client', 'service', 1.0) == _OK
    assert len(orchestrator.calls) == 1, '한 번에 됐는데 또 했습니다'


def test_a_failed_scan_is_tried_again():
    """**이것이 그 버그다** · 한 번 걸렸다고 사람에게 실패를 보이지 않는다."""
    orchestrator = _orchestrator([_FAIL, _OK])

    assert orchestrator._scan_with_retry('client', 'service', 1.0) == _OK
    assert len(orchestrator.calls) == 2


def test_two_failures_in_a_row_still_get_a_third_try():
    orchestrator = _orchestrator([_FAIL, _FAIL, _OK])

    assert orchestrator._scan_with_retry('client', 'service', 1.0) == _OK
    assert len(orchestrator.calls) == 3


def test_it_gives_up_after_three_tries():
    orchestrator = _orchestrator([_FAIL])

    result = orchestrator._scan_with_retry('client', 'service', 1.0)

    assert result == _FAIL
    assert len(orchestrator.calls) == ScanOrchestrator.ETHERCAT_SCAN_ATTEMPTS == 3


def test_a_partial_scan_is_not_retried():
    """부분 완료는 찾을 것을 찾은 것이다 · 다시 하면 모터만 더 껐다 켠다."""
    orchestrator = _orchestrator([_PARTIAL, _OK])

    assert orchestrator._scan_with_retry('client', 'service', 1.0) == _PARTIAL
    assert len(orchestrator.calls) == 1


def test_every_try_asks_the_same_thing():
    orchestrator = _orchestrator([_FAIL])

    orchestrator._scan_with_retry('client', 'service', 1.0, release_ethercat=True)

    assert orchestrator.calls == [
        (('client', 'service', 1.0), {'release_ethercat': True}),
    ] * 3
