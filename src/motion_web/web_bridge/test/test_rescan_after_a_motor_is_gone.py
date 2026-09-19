"""빠진 모터 때문에 재검색했는데 「검색 실패」가 뜨던 것 · §6-196

AC 서보 검색은 EtherCAT 소유권 때문에 Motor Manager 를 껐다 켠다.

    1  Motor Manager 정지
    2  물리 검색            ← 여기서 실제로 찾는다
    3  Motor Manager 재시작
    4  축이 다 돌아왔나 확인  ← 여기가 문제였다

4번이 **검색 전 설정에 있던 축이 전부 돌아와야** 성공으로 쳤다.

    if service_active and all(axis in online_axes for axis in expected):

모터가 빠져서 재검색하는 경우 그 축은 당연히 안 돌아온다 · 12초를 기다린
뒤 `recovered=False` → `success=False` → 화면에 **「직접 검색 실패」** ·
정작 축 목록은 제대로 갱신돼 있었다.

`fault` 인 축도 온라인으로 안 쳤다 · **알람 때문에 재검색하면 그것 때문에
실패로 나왔다.**

**사람이 직접 「검색」을 눌렀다는 것은 모터 상태를 보고 눌렀다는 뜻이다** ·
없는 모터가 없다고 나오는 것은 실패가 아니라 그 검색의 답이다.

이제 둘을 가른다.

    Motor Manager 가 안 돌아옴  →  진짜 실패
    축 몇 개가 안 돌아옴        →  알림 · 무엇이 없는지 말해 준다
"""

import threading
import time

import pytest

from motion_web_bridge.motor_runtime_service import MotorRuntimeService


class _Bridge:
    """살아 있는 축만 돌려주는 브리지 대역."""

    def __init__(self, online):
        self._online = online

    def motion_state_with_time(self):
        motors = [
            {'controller_index': axis, 'connection_connected': True, 'fault': False}
            for axis in self._online
        ]
        return {'motors': motors}, time.time()


def _runtime(online, *, service_active=True):
    service = MotorRuntimeService.__new__(MotorRuntimeService)
    service.bridge = _Bridge(online)
    service.managed_service_active = lambda _service: service_active
    service._recovery_lock = threading.Lock()
    return service


# --------------------------------------------------------------------------- #
# 무엇이 돌아오지 않았는지 말해 준다
# --------------------------------------------------------------------------- #

def test_everything_came_back():
    recovery = _runtime([0, 1]).wait_for_runtime_recovery(
        [0, 1], timeout_sec=1.0, motor_service='motion-motor.service'
    )

    assert recovery['recovered'] is True
    assert recovery['missing_axes'] == []


def test_it_names_the_axis_that_did_not_come_back():
    """전에는 「1/2축」 이라고만 했다 · 어느 축인지 말하지 않았다."""
    recovery = _runtime([0]).wait_for_runtime_recovery(
        [0, 1], timeout_sec=1.0, motor_service='motion-motor.service'
    )

    assert recovery['recovered'] is False
    assert recovery['missing_axes'] == [1]
    assert recovery['online_axes'] == [0]
    # Motor Manager 는 멀쩡하다 · 이것이 실패와 알림을 가르는 값이다
    assert recovery['service_active'] is True


def test_a_dead_motor_manager_is_a_different_thing():
    recovery = _runtime([], service_active=False).wait_for_runtime_recovery(
        [0, 1], timeout_sec=1.0, motor_service='motion-motor.service'
    )

    assert recovery['recovered'] is False
    assert recovery['service_active'] is False


def test_every_answer_carries_the_missing_list():
    """부르는 쪽이 있는지 없는지 따지지 않게 · 셋 다 같은 칸을 준다."""
    cases = [
        _runtime([0, 1]).wait_for_runtime_recovery([0, 1], 1.0, 'motion-motor.service'),
        _runtime([0]).wait_for_runtime_recovery([0, 1], 1.0, 'motion-motor.service'),
        _runtime([0]).wait_for_runtime_recovery([], 1.0, 'motion-motor.service'),
    ]

    for recovery in cases:
        assert 'missing_axes' in recovery


# --------------------------------------------------------------------------- #
# 검색 쪽이 둘을 가르는가 · 글자가 아니라 뜻을 본다
# --------------------------------------------------------------------------- #

def test_a_faulted_motor_counts_as_missing_not_online():
    """알람 때문에 재검색하는데 그것 때문에 실패가 되면 안 된다."""
    service = MotorRuntimeService.__new__(MotorRuntimeService)
    service.managed_service_active = lambda _service: True

    class _Faulted:
        @staticmethod
        def motion_state_with_time():
            return {'motors': [
                {'controller_index': 0, 'connection_connected': True, 'fault': False},
                {'controller_index': 1, 'connection_connected': True, 'fault': True},
            ]}, time.time()

    service.bridge = _Faulted()
    recovery = service.wait_for_runtime_recovery(
        [0, 1], timeout_sec=1.0, motor_service='motion-motor.service'
    )

    assert recovery['missing_axes'] == [1], '알람 축은 온라인이 아니다'
    assert recovery['service_active'] is True, '그래도 Motor Manager 는 멀쩡하다'


@pytest.mark.parametrize('needle', [
    "if not recovery.get('service_active')",
    "result['missing_axes'] = missing",
    '돌아오지 않았습니다',
])
def test_the_scan_keeps_the_two_apart(needle):
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[1]
        / 'motion_web_bridge' / 'scan_orchestrator.py'
    ).read_text(encoding='utf-8')

    assert needle in source
