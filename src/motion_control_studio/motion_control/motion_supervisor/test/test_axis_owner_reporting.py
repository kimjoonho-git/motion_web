"""Supervisor 가 축별 주인을 알린다 · 알람 판정도 축별로 본다 · §6-106

`snapshot()` 은 대표 하나로 줄인 축약형이다 · 추가 녹화에서 MIDI 가 축 하나를
잡으면 대표가 `midi` 로 바뀐다 · 그 대표를 보고 "재생이 도나" 를 판정하면,
다른 축을 몰고 있는 재생이 없는 것으로 취급된다.
"""

import json
import time

from motion_supervisor.command_arbiter import CommandArbiter, CommandOwner
from motion_supervisor.servo_alarm_guard import policy_revision
from motion_supervisor.supervisor_node import MotionSupervisor


class FakePublisher:
    def __init__(self):
        self.payloads = []

    def publish(self, msg):
        self.payloads.append(json.loads(msg.data))


def _bare_node() -> MotionSupervisor:
    node = MotionSupervisor.__new__(MotionSupervisor)
    node._command_arbiter = CommandArbiter()
    node._emergency_latched = False
    node._motion_stop_block_until = 0.0
    node._active_jogs = {}
    node._active_actions = {}
    node._safety_status_pub = FakePublisher()
    node._latest_state = {'motors': []}
    node._last_motion_run_command_at = time.monotonic() - 10.0
    return node


def _midi_then_playback(node: MotionSupervisor) -> None:
    """대표 주인은 MIDI · 재생은 축 1 을 몰고 있다 · 추가 녹화의 모양."""
    arbiter = node._command_arbiter
    arbiter.acquire(CommandOwner.MIDI, axes=[0], lease_sec=5.0)
    arbiter.acquire(CommandOwner.PLAYBACK, axes=[1], lease_sec=5.0)
    assert arbiter.snapshot().owner is CommandOwner.MIDI, '대표가 MIDI 여야 시험이 성립한다'


def test_safety_status_carries_the_axis_owner_table():
    node = _bare_node()
    _midi_then_playback(node)

    node._publish_safety_status()

    payload = node._safety_status_pub.payloads[-1]
    assert payload['command_axis_owners'] == {'0': 'midi', '1': 'playback'}, (
        '축별 주인 표가 비어 있다 · 재생이 자기 축을 볼 수 없다'
    )
    # 축약형은 화면용으로 그대로 남는다
    assert payload['command_owner'] == 'midi'


def test_alarm_evaluation_sees_playback_on_another_axis():
    """1등급 알람이 풀려도 재생 중에는 그 축을 계속 막아 둔다 · 재생이 보여야 한다."""
    node = _bare_node()
    guard = node._servo_alarm_guard_instance()
    motor = {
        'controller_index': 0,
        'motor_type': 'ac_servo',
        'errorcode_raw': 1,
        'errorcode': 1,
        'fault': True,
    }
    grades = {'1': 1}
    applied, message = guard.apply_policy(
        grades, project_id='p', catalog_version=1,
        revision=policy_revision(grades, 1))
    assert applied is True, message
    node._latest_state = {'motors': [motor]}
    node._evaluate_servo_alarms()
    assert guard.snapshot()['blocked_axes'] == [0], '1등급 알람이 축 0 을 막아야 한다'

    # 알람이 사라졌다 · 그러나 재생이 축 1 을 몰고 있다
    node._latest_state = {'motors': [dict(motor, errorcode=0, errorcode_raw=0, fault=False)]}
    _midi_then_playback(node)
    node._evaluate_servo_alarms()

    assert guard.snapshot()['blocked_axes'] == [0], (
        '재생이 도는데 축이 풀렸다 · 대표 주인만 보고 재생을 놓쳤다'
    )


def test_alarm_evaluation_releases_the_hold_when_nothing_plays():
    node = _bare_node()
    guard = node._servo_alarm_guard_instance()
    motor = {
        'controller_index': 0,
        'motor_type': 'ac_servo',
        'errorcode_raw': 1,
        'errorcode': 1,
        'fault': True,
    }
    grades = {'1': 1}
    applied, message = guard.apply_policy(
        grades, project_id='p', catalog_version=1,
        revision=policy_revision(grades, 1))
    assert applied is True, message
    node._latest_state = {'motors': [motor]}
    node._evaluate_servo_alarms()

    node._latest_state = {'motors': [dict(motor, errorcode=0, errorcode_raw=0, fault=False)]}
    node._command_arbiter.acquire(CommandOwner.MIDI, axes=[0], lease_sec=5.0)
    node._evaluate_servo_alarms()

    assert guard.snapshot()['blocked_axes'] == [], '재생이 없으면 잡아 둘 이유가 없다'
