"""물리 검색 시한 계약 · §6-37.

§6-16에서 Dynamixel 시한만 20 → 40초로 올리고 전체 검색은 20초로 남겨두었다.
단독 검색은 되는데 전체 검색만 시한 초과로 실패하는 상태였고, 값이 두 곳에
흩어져 있어 한쪽만 고쳐도 아무도 몰랐다. 관계를 시험으로 고정한다.
"""

import inspect

from motion_web_bridge import scan_orchestrator as so


def _default_timeout(method_name):
    signature = inspect.signature(getattr(so.ScanOrchestrator, method_name))
    return signature.parameters['timeout_sec'].default


def test_full_scan_budget_covers_both_transports():
    """전체 검색은 두 종류를 차례로 돌린다 · 합보다 짧으면 안 된다."""
    assert _default_timeout('scan_all') >= (
        _default_timeout('scan_ac_servo') + _default_timeout('scan_dynamixel')
    )


def test_each_scan_uses_the_shared_constant():
    """기본값을 숫자로 다시 적으면 또 갈라진다 · 상수에서만 나와야 한다."""
    assert _default_timeout('scan_all') == so.FULL_SCAN_TIMEOUT_SEC
    assert _default_timeout('scan_ac_servo') == so.AC_SERVO_SCAN_TIMEOUT_SEC
    assert _default_timeout('scan_dynamixel') == so.DYNAMIXEL_SCAN_TIMEOUT_SEC
    assert so.FULL_SCAN_TIMEOUT_SEC == (
        so.AC_SERVO_SCAN_TIMEOUT_SEC + so.DYNAMIXEL_SCAN_TIMEOUT_SEC
    )


def test_dynamixel_keeps_the_raised_budget():
    """§6-16이 올린 값이다 · 되돌아가면 Dynamixel 검색이 다시 잘린다."""
    assert so.DYNAMIXEL_SCAN_TIMEOUT_SEC >= 40.0
