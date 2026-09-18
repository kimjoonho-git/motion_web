import time
from types import SimpleNamespace

import pytest
from rclpy.executors import ExternalShutdownException
from motion_coordination_interfaces.msg import (
    GroupAlarm, GroupCommand, GroupEvent, GroupHeartbeat, GroupTimeSync,
)

import motion_coordination.coordination_node as coordination_node
from motion_coordination.alarm_registry import AlarmRegistry
from motion_coordination.group_execution import GroupExecution, Member, MemberRegistry
from motion_coordination.safety_stop import SafetyStopController


class _MidiRelay:
    """중계 자리만 채운다 · 중계 자체는 `test_midi_relay_bridge` 가 본다."""

    def __init__(self):
        self.ticks = 0

    def tick(self):
        self.ticks += 1

    def snapshot(self):
        return {}


class _Publisher:
    def __init__(self, events=None):
        self.messages = []
        self.events = events

    def publish(self, message):
        self.messages.append(message)
        if self.events is not None:
            self.events.append(('dds', getattr(message, 'command', '')))


def _peer_release_ack(node, *, pc_id='pc-b', group_id='stage-a'):
    release_id = node._execution.pending_command_id
    node._event_callback(GroupEvent(
        group_id=group_id,
        execution_id=node._execution.execution_id,
        pc_id=pc_id,
        event='stopped',
        command_id=release_id,
        success=True,
    ))


def _node():
    node = coordination_node.MotionCoordinationNode.__new__(
        coordination_node.MotionCoordinationNode
    )
    node._config = SimpleNamespace(
        pc_id='pc-a', group_id='stage-a', schedule_ack_margin_sec=0.1,
        display_name='PC A', enabled=True, dds_domain_id=21,
        heartbeat_sec=0.5, warning_timeout_sec=1.5,
        peer_timeout_sec=3.0, start_lead_sec=0.5,
        max_trigger_sync_uncertainty_ms=20.0,
        trigger_sync_samples=3,
        prepare_timeout_sec=6.0,
        trigger_report_timeout_sec=1.0,
        configured=True,
        is_master=True,
        auto_play=False,
        required_peers=(),
    )
    node._seen_commands = {}
    node._cancelled_execution_ids = set()
    node._sequence = 0
    node._joined = False
    node._boot_id = 'boot-a'
    node._git_branch = 'main'
    node._git_hash = ''
    node._git_message = ''
    import threading
    node._lock = threading.RLock()
    node._execution = GroupExecution()
    node._coordination_error = {}
    # 기동 시점의 랜 주소 · `_check_network_drift` 가 견주는 기준점 · §6-96
    node._boot_lan_addresses = ()
    node._network_stale = {}
    node.get_logger = lambda: SimpleNamespace(
        error=lambda _message: None,
        warn=lambda _message: None,
        info=lambda _message: None,
    )
    node._duplicate_pc_boot_id = ''
    node._alarm_registry = AlarmRegistry()
    node._alarm_registry.alarms = {}
    node._alarm_registry.versions = {}
    node._registry = MemberRegistry()
    node._safety_stop = SafetyStopController()
    node._alarm_pub = _Publisher()
    node._local_status = {}
    node._sync_estimators = {}
    node._sync_sent_samples = {}
    node._sync_probes = {}
    node._sync_ready = set()
    node._sync_next_action = ''
    node._sync_deadline = 0.0
    node._sync_last_probe_at = 0.0
    node._local_sync_offset_ns = 0
    node._trigger_sync_status = {
        'trigger_sync_state': 'idle',
        'trigger_sync_uncertainty_ms': 0.0,
        'trigger_sync_source': 'dds_relative_monotonic',
    }
    node._midi_relay = _MidiRelay()
    return node


def test_command_ids_are_accepted_once():
    node = _node()
    assert node._command_seen('command-a') is False
    assert node._command_seen('command-a') is True


def test_heartbeat_does_not_block_on_local_http_status_refresh():
    node = _node()
    node._joined = True
    node._boot_id = 'boot-a'
    node._heartbeat_pub = _Publisher()
    node._local_http = lambda *_args, **_kwargs: pytest.fail(
        'heartbeat must not call the local HTTP API'
    )

    node._heartbeat_tick()

    assert len(node._heartbeat_pub.messages) == 1


def test_state_tick_consumes_background_runtime_sample_before_event_check():
    node = _node()
    node._execution.execution_id = 'exec-a'
    node._execution.participants = ('pc-a',)
    node._local_runtime_monitor = SimpleNamespace(
        snapshot=lambda: {
            'status': {
                'bridge_state': 'ok',
                'motion_run_status': {
                    'group_execution': True,
                    'execution_id': 'exec-a',
                    'phase': 'running',
                    'group_cycle_number': 1,
                    'lifecycle': {'motion_started_monotonic': 123.0},
                },
                'safety_status': {},
            },
            'received_monotonic': time.monotonic(),
            'error': '',
        },
        set_active=lambda _active: None,
    )
    node._last_local_event_key = ()
    node._last_alarm_key = ()
    published = []
    node._publish_runtime_event = lambda *args, **kwargs: published.append(
        (args, kwargs)
    )
    node._enforce_execution_membership = lambda: None
    node._enforce_schedule_ack_deadline = lambda: None
    node._enforce_motion_start_report_deadline = lambda: None
    node._drive_trigger_sync = lambda: None
    node._prune_seen_commands = lambda: None

    node._state_tick()

    assert published[0][0][0] == 'motion_started'
    assert published[0][1]['triggered_at'] == 123.0


def test_stale_local_runtime_status_stops_only_active_group_execution():
    node = _node()
    node._execution.execution_id = 'exec-a'
    node._local_runtime_monitor = SimpleNamespace(
        snapshot=lambda: {
            'status': {'bridge_state': 'ok'},
            'received_monotonic': time.monotonic() - 1.0,
            'error': 'bridge timeout',
        },
    )
    failures = []
    node._stop_for_peer_failure = failures.append

    node._consume_local_runtime_status()

    assert failures == [
        '로컬 Web Bridge 상태 수신 중단: bridge timeout'
    ]


def test_stale_local_runtime_status_does_not_affect_standalone_mode():
    node = _node()
    node._local_runtime_monitor = SimpleNamespace(
        snapshot=lambda: {
            'status': {}, 'received_monotonic': 0.0, 'error': 'offline',
        },
    )
    node._stop_for_peer_failure = lambda _reason: pytest.fail(
        'standalone mode must not be stopped by coordination status'
    )

    node._consume_local_runtime_status()


def test_activation_boundary_waits_for_first_active_runtime_sample():
    node = _node()
    node._execution.execution_id = 'exec-a'
    activated = time.monotonic()
    node._local_runtime_monitor = SimpleNamespace(
        snapshot=lambda: {
            'status': {'bridge_state': 'ok'},
            'received_monotonic': activated - 0.6,
            'active_since_monotonic': activated,
            'error': '',
        },
    )
    node._stop_for_peer_failure = lambda _reason: pytest.fail(
        'activation grace must wait for a fresh active sample'
    )

    node._consume_local_runtime_status()


def test_active_stop_command_is_submitted_with_urgent_priority():
    node = _node()
    node._joined = True
    node._execution.execution_id = 'exec-a'
    node._execution.participants = ('pc-a', 'pc-b')
    submitted = []
    node._command_dispatcher = SimpleNamespace(
        submit=lambda message, urgent_stop=False: (
            submitted.append((message.command, urgent_stop)) or True
        )
    )
    message = GroupCommand(
        group_id='stage-a', execution_id='exec-a', command_id='stop-a',
        coordinator_id='pc-b', command='stop_now',
        participant_ids=['pc-a', 'pc-b'],
    )

    node._command_callback(message)

    assert submitted == [('stop_now', True)]
    assert node._cancelled_execution_ids == {'exec-a'}


def test_cancel_before_start_uses_urgent_dispatch_lane():
    node = _node()
    node._joined = True
    node._execution.execution_id = 'exec-a'
    node._execution.participants = ('pc-a', 'pc-b')
    submitted = []
    node._command_dispatcher = SimpleNamespace(
        submit=lambda message, urgent_stop=False: (
            submitted.append((message.command, urgent_stop)) or True
        )
    )
    message = GroupCommand(
        group_id='stage-a', execution_id='exec-a', command_id='cancel-a',
        coordinator_id='pc-b', command='cancel_before_start',
        participant_ids=['pc-a', 'pc-b'],
    )

    node._command_callback(message)

    assert submitted == [('cancel_before_start', True)]
    assert node._cancelled_execution_ids == {'exec-a'}


def test_delayed_start_is_rejected_after_urgent_stop_was_accepted():
    node = _node()
    node._cancelled_execution_ids.add('exec-a')
    node._event_pub = _Publisher()
    message = GroupCommand(
        group_id='stage-a', execution_id='exec-a', command_id='start-a',
        coordinator_id='pc-a', command='start_at', cycle_number=1,
        participant_ids=['pc-a'],
    )

    node._process_group_command(message)

    assert node._event_pub.messages[-1].event == 'rejected'
    assert '지연 명령' in node._event_pub.messages[-1].message


def test_start_that_finishes_after_stop_is_stopped_and_rejected():
    import threading

    node = _node()
    node._execution.execution_id = 'exec-a'
    node._execution.coordinator_id = 'pc-a'
    node._execution.participants = ('pc-a',)
    node._trigger_sync_status['trigger_sync_state'] = 'ready'
    node._event_pub = _Publisher()
    start_entered = threading.Event()
    release_start = threading.Event()
    calls = []

    def local_control(payload, **_kwargs):
        calls.append(dict(payload))
        if payload['command'] == 'group_start_at':
            start_entered.set()
            release_start.wait(1.0)
        return {'success': True, 'message': payload['command']}

    node._call_local_control = local_control
    message = GroupCommand(
        group_id='stage-a', execution_id='exec-a', command_id='start-a',
        coordinator_id='pc-a', command='start_at', cycle_number=1,
        participant_ids=['pc-a'],
        scheduled_monotonic_ns=time.monotonic_ns() + 5_000_000_000,
    )
    thread = threading.Thread(target=node._process_group_command, args=(message,))
    thread.start()
    assert start_entered.wait(0.5)
    with node._lock:
        node._cancelled_execution_ids.add('exec-a')
    release_start.set()
    thread.join(timeout=1.0)

    assert [row['command'] for row in calls] == ['group_start_at', 'stop_now']
    assert node._event_pub.messages[-1].event == 'rejected'
    assert '지연 그룹 명령' in node._event_pub.messages[-1].message


def test_scheduled_command_requires_ack_margin():
    node = _node()
    node._trigger_sync_status['trigger_sync_state'] = 'ready'
    command = GroupCommand()
    command.scheduled_monotonic_ns = time.monotonic_ns() + 200_000_000
    assert node._local_schedule_ns(command) > time.monotonic_ns()
    command.scheduled_monotonic_ns = time.monotonic_ns() + 50_000_000
    with pytest.raises(ValueError, match='여유'):
        node._local_schedule_ns(command)


def test_immediate_group_stop_applies_local_stop_before_dds_publish():
    node = _node()
    node._execution = GroupExecution()
    node._execution.begin('pc-a', ('pc-a', 'pc-b'))
    events = []
    node._call_local_control = lambda payload, **_kwargs: (
        events.append(('local', payload['command'])) or {'success': True}
    )
    node._command_pub = _Publisher(events)

    result = node._request_group_stop(after_cycle=False)

    assert result['success'] is True
    assert result['dds_stop_published'] is True
    assert events == [('local', 'stop_now'), ('dds', 'stop_now')]
    assert node._execution.execution_id == ''
    assert node._execution.state == 'stopped'


def test_local_stop_failure_is_reported_but_dds_stop_is_still_published():
    node = _node()
    node._execution = GroupExecution()
    node._execution.begin('pc-a', ('pc-a', 'pc-b'))
    node._call_local_control = lambda _payload: {
        'success': False, 'message': '로컬 응답 없음',
    }
    node._command_pub = _Publisher()

    result = node._request_group_stop(after_cycle=False)

    assert result['success'] is False
    assert result['dds_stop_published'] is True
    assert node._command_pub.messages[0].command == 'stop_now'


def test_cancel_failure_escalates_to_local_and_dds_stop_now():
    node = _node()
    node._joined = True
    node._boot_id = 'boot-a'
    node._execution = GroupExecution()
    execution_id = node._execution.begin('pc-a', ('pc-a', 'pc-b'))
    node._execution.execution_id = execution_id
    node._execution.coordinator_id = 'pc-a'
    node._execution.participants = ('pc-a', 'pc-b')
    calls = []

    def local(payload, **_kwargs):
        calls.append(payload['command'])
        return {
            'success': payload['command'] == 'stop_now',
            'message': payload['command'],
        }

    node._call_local_control = local
    node._command_pub = _Publisher()
    node._alarm_pub = _Publisher()

    node._cancel_before_start('prepare failed', code='GROUP_START_REJECTED')

    assert calls == ['group_cancel', 'stop_now']
    assert node._command_pub.messages[-1].command == 'cancel_before_start'
    assert node._execution.state == 'releasing'
    _peer_release_ack(node)
    assert node._execution.execution_id == ''
    assert node._coordination_error['code'] == 'GROUP_START_REJECTED'


def test_stop_after_cycle_applies_local_request_before_dds_publish():
    node = _node()
    node._execution = GroupExecution()
    node._execution.begin('pc-a', ('pc-a', 'pc-b'))
    events = []
    node._call_local_control = lambda payload, **_kwargs: (
        events.append(('local', payload['command'])) or {'success': True}
    )
    node._command_pub = _Publisher(events)

    result = node._request_group_stop(after_cycle=True)

    assert result['success'] is True
    assert events == [('local', 'stop_after_cycle'), ('dds', 'stop_after_cycle')]
    assert node._execution.stop_after_cycle is True


def test_fixed_roster_participant_can_issue_group_stop():
    node = _node()
    node._execution.execution_id = 'exec-a'
    node._execution.coordinator_id = 'pc-a'
    node._execution.participants = ('pc-a', 'pc-b')
    command = GroupCommand(
        group_id='stage-a', execution_id='exec-a', coordinator_id='pc-b',
        command='stop_now', participant_ids=['pc-a', 'pc-b'],
    )
    node._require_stop_command(command, ('pc-a', 'pc-b'))
    command.coordinator_id = 'pc-c'
    with pytest.raises(ValueError, match='정지 요청'):
        node._require_stop_command(command, ('pc-a', 'pc-b'))


def test_stop_now_accepted_when_lease_cleared_but_group_worker_active():
    node = _node()
    node._config.pc_id = 'pc-b'
    node._execution.clear_active()
    node._execution.state = 'running'
    node._local_status['motion_run_status'] = {
        'group_execution': True,
        'execution_id': 'exec-a',
        'state': 'running',
        'phase': 'running',
    }
    command = GroupCommand(
        group_id='stage-a', execution_id='exec-a', coordinator_id='pc-a',
        command='stop_now', participant_ids=['pc-a', 'pc-b'],
    )
    node._require_stop_command(command, ('pc-a', 'pc-b'))


def test_release_worker_escalation_broadcasts_full_participant_roster():
    node = _node()
    node._execution.execution_id = 'exec-a'
    node._execution.participants = ('pc-a', 'pc-b')
    published = []
    node._issue_stop_now = lambda **kwargs: (
        published.append(kwargs) or SimpleNamespace(local_result={'success': True})
    )
    node._call_local_control = lambda *_args, **_kwargs: {'success': False}

    node._release_local_worker(
        'exec-a',
        network_operation_id='release-1',
        participants=('pc-a', 'pc-b'),
    )

    assert published == [{
        'execution_id': 'exec-a',
        'participants': ('pc-a', 'pc-b'),
        'cycle_number': 0,
        'command_id': 'release-1',
    }]


def test_resolve_motion_cycle_prefers_display_cycle():
    from motion_coordination.coordination_node import MotionCoordinationNode

    assert MotionCoordinationNode._resolve_motion_cycle({
        'display_cycle': 4,
        'group_cycle_number': 2,
        'current_cycle': 1,
    }) == 4
    assert MotionCoordinationNode._resolve_motion_cycle({
        'group_cycle_number': 2,
        'current_cycle': 1,
    }) == 2
    assert MotionCoordinationNode._resolve_motion_cycle({
        'current_cycle': 3,
    }) == 3


def test_alarm_on_execution_member_blocks_next_cycle():
    node = _node()
    node._execution.participants = ('pc-a', 'pc-b')
    node._registry = MemberRegistry(warning_timeout_sec=1.5, timeout_sec=3.0)
    node._registry.update(Member(
        pc_id='pc-b',
        boot_id='boot-b',
        joined=True,
        is_master=False,
        state='ready',
        trigger_sync_state='idle',
        trigger_sync_uncertainty_ms=0.0,
        alarm_grade=2,
        received_monotonic=time.monotonic(),
    ))
    assert node._execution_unhealthy_members() == ['pc-b']


def test_cycle_barrier_schedules_initialization_only_after_all_motion_completed():
    node = _node()
    node._execution = GroupExecution()
    execution_id = node._execution.begin('pc-a', ('pc-a', 'pc-b'))
    for pc_id in ('pc-a', 'pc-b'):
        node._execution.mark_ready(pc_id)
    node._execution.initialize_action(now=1.0)
    for pc_id in ('pc-a', 'pc-b'):
        node._execution.mark_armed(pc_id)
    node._execution.start_action(now=2.0)
    for pc_id in ('pc-a', 'pc-b'):
        node._execution.mark_triggered(pc_id, 1, 2.1)
    scheduled = []
    next_starts = []
    node._publish_next_cycle = lambda: scheduled.append('initialize')
    node._publish_next_start = lambda: next_starts.append('start')

    node._event_callback(GroupEvent(
        group_id='stage-a', execution_id=execution_id, pc_id='pc-a',
        event='motion_completed', cycle_number=1, success=True,
    ))
    assert scheduled == []
    node._event_callback(GroupEvent(
        group_id='stage-a', execution_id=execution_id, pc_id='pc-b',
        event='motion_completed', cycle_number=1, success=True,
    ))
    assert scheduled == ['initialize']
    node._execution.cycle_initialize_action(now=3.0)
    node._event_callback(GroupEvent(
        group_id='stage-a', execution_id=execution_id, pc_id='pc-a',
        event='cycle_initialized', cycle_number=1, success=True,
    ))
    assert next_starts == []
    node._event_callback(GroupEvent(
        group_id='stage-a', execution_id=execution_id, pc_id='pc-b',
        event='cycle_initialized', cycle_number=1, success=True,
    ))
    assert next_starts == ['start']


def test_peer_timeout_stops_local_then_broadcasts_before_clearing_roster():
    node = _node()
    node._registry = MemberRegistry(warning_timeout_sec=0.1, timeout_sec=0.2)
    node._registry.update(Member(
        pc_id='pc-b',
        boot_id='boot-b',
        joined=True,
        is_master=False,
        state='running',
        trigger_sync_state='ready',
        trigger_sync_uncertainty_ms=1.0,
        alarm_grade=0,
        received_monotonic=time.monotonic() - 1.0,
    ))
    node._execution = GroupExecution()
    node._execution.begin('pc-a', ('pc-a', 'pc-b'))
    events = []
    node._call_local_control = lambda payload, **_kwargs: (
        events.append(('local', payload['command'])) or {'success': True}
    )
    node._command_pub = _Publisher(events)
    node.get_logger = lambda: SimpleNamespace(error=lambda _message: None)

    node._enforce_execution_membership()

    assert events == [('local', 'stop_now'), ('dds', 'stop_now')]
    assert node._command_pub.messages[0].participant_ids == ['pc-a', 'pc-b']
    assert node._execution.execution_id == ''
    assert node._execution.state == 'error'
    assert node._coordination_error['code'] == 'GROUP_PARTICIPANT_DISCONNECTED'


def test_peer_restart_stops_local_then_broadcasts_and_clears_execution():
    node = _node()
    node._execution = GroupExecution()
    node._execution.begin('pc-a', ('pc-a', 'pc-b'))
    events = []
    node._call_local_control = lambda payload, **_kwargs: (
        events.append(('local', payload['command'])) or {'success': True}
    )
    node._command_pub = _Publisher(events)
    node.get_logger = lambda: SimpleNamespace(error=lambda _message: None)

    node._stop_for_peer_failure('pc-b 프로그램 재시작')

    assert events == [('local', 'stop_now'), ('dds', 'stop_now')]
    assert node._execution.execution_id == ''
    assert node._execution.state == 'error'
    assert node._coordination_error['code'] == 'GROUP_PARTICIPANT_FAILURE'


def test_missing_prepare_ack_cancels_every_participant_before_start():
    node = _node()
    node._execution = GroupExecution()
    node._execution.begin('pc-a', ('pc-a', 'pc-b'))
    node._execution.pending_command = 'prepare'
    node._execution.pending_command_id = 'prepare-command'
    node._execution.pending_acks = {'pc-a'}
    node._execution.pending_ack_deadline = time.monotonic() - 0.1
    sent = []
    node._call_local_control = lambda payload, **_kwargs: (
        sent.append(('local', payload['command'])) or {'success': True}
    )
    node._command_pub = _Publisher(sent)
    node.get_logger = lambda: SimpleNamespace(error=lambda _message: None)

    node._enforce_schedule_ack_deadline()

    assert sent == [('local', 'group_cancel'), ('dds', 'cancel_before_start')]
    assert node._execution.state == 'releasing'
    assert node._execution.pending_command == 'cancel_before_start'
    _peer_release_ack(node)
    assert node._execution.state == 'error'
    assert node._execution.pending_command_id == ''
    assert node._execution.execution_id == ''


def test_late_start_ack_timeout_uses_stop_now_instead_of_cancel():
    node = _node()
    node._execution = GroupExecution()
    node._execution.begin('pc-a', ('pc-a', 'pc-b'))
    node._execution.pending_command = 'start_at'
    node._execution.pending_command_id = 'start-command'
    node._execution.pending_acks = {'pc-a'}
    node._execution.pending_ack_deadline = time.monotonic() - 0.2
    node._execution.pending_scheduled_at = time.monotonic() - 0.1
    events = []
    node._call_local_control = lambda payload, **_kwargs: (
        events.append(('local', payload['command'])) or {'success': True}
    )
    node._command_pub = _Publisher(events)
    node.get_logger = lambda: SimpleNamespace(error=lambda _message: None)

    node._enforce_schedule_ack_deadline()

    assert events == [('local', 'stop_now'), ('dds', 'stop_now')]
    assert node._execution.execution_id == ''


def test_warning_joined_member_is_visible_and_blocks_partial_group_start():
    node = _node()
    node._joined = True
    node._execution = GroupExecution()
    node._registry = MemberRegistry(warning_timeout_sec=0.1, timeout_sec=3.0)
    node._registry.update(Member(
        pc_id='pc-b',
        boot_id='boot-b',
        joined=True,
        is_master=False,
        state='ready',
        trigger_sync_state='idle',
        trigger_sync_uncertainty_ms=0.0,
        alarm_grade=0,
        received_monotonic=time.monotonic() - 0.2,
        sequence=1,
    ))

    snapshot = node.snapshot()

    assert snapshot['peers'][0]['state'] == 'warning'
    with pytest.raises(ValueError, match=r'pc-b\(warning\)'):
        node._start_group_execution()


def test_leave_publishes_explicit_not_joined_heartbeat():
    node = _node()
    node._joined = True
    node._execution = GroupExecution()
    node._heartbeat_pub = _Publisher()
    node._boot_id = 'boot-a'

    result = node._handle_local_request({'command': 'leave'})

    assert result['success'] is True
    assert node._joined is False
    assert node._heartbeat_pub.messages[-1].joined is False


def test_temporary_disable_releases_stale_prepare_without_peer_approval():
    node = _node()
    node._joined = True
    node._heartbeat_pub = _Publisher()
    node._execution.state = 'preparing'
    node._coordination_error = {'active': True, 'code': 'GROUP_START_REJECTED'}

    result = node._handle_local_request({'command': 'temporarily_disable'})

    assert result['success'] is True
    assert node._joined is False
    assert node._execution.state == 'idle'
    assert node._execution.execution_id == ''
    assert node._coordination_error == {}
    assert node._heartbeat_pub.messages[-1].joined is False


def test_rejected_prepare_cancels_execution_and_releases_lease():
    node = _node()
    node._execution = GroupExecution()
    execution_id = node._execution.begin('pc-a', ('pc-a', 'pc-b'))
    node._execution.execution_id = execution_id
    node._execution.coordinator_id = 'pc-a'
    node._execution.participants = ('pc-a', 'pc-b')
    events = []
    node._call_local_control = lambda payload, **_kwargs: (
        events.append(('local', payload['command'])) or {'success': True}
    )
    node._command_pub = _Publisher(events)
    rejected = GroupEvent(
        group_id='stage-a', execution_id=execution_id, pc_id='pc-b',
        event='rejected', success=False, message='not ready',
    )

    node._event_callback(rejected)

    assert events == [('local', 'group_cancel'), ('dds', 'cancel_before_start')]
    assert node._execution.state == 'releasing'
    _peer_release_ack(node)
    assert node._execution.execution_id == ''
    assert node._execution.pending_command_id == ''
    assert node._coordination_error['code'] == 'GROUP_START_REJECTED'


def test_winning_simultaneous_claim_cancels_and_resets_losing_coordinator():
    node = _node()
    node._execution = GroupExecution()
    previous_execution_id = node._execution.begin(
        'pc-b', ('pc-a', 'pc-b')
    )
    node._registry.update(Member(
        pc_id='pc-b',
        boot_id='boot-b',
        joined=True,
        is_master=False,
        state='ready',
        trigger_sync_state='idle',
        trigger_sync_uncertainty_ms=0.0,
        alarm_grade=0,
        received_monotonic=time.monotonic(),
        sequence=1,
    ))
    node._execution.pending_command = 'prepare'
    node._execution.pending_command_id = 'old-command'
    node._execution.pending_acks = {'pc-b'}
    node._execution.pending_ack_deadline = time.monotonic() + 5.0
    calls = []
    node._call_local_control = lambda payload, **_kwargs: calls.append(dict(payload)) or {
        'success': True,
    }
    incoming = GroupCommand(
        group_id='stage-a', execution_id='exec-a', coordinator_id='pc-a',
        command='prepare', participant_ids=['pc-a', 'pc-b'],
    )

    node._accept_execution_claim(incoming, ('pc-a', 'pc-b'))

    assert calls[0]['command'] == 'group_cancel'
    assert calls[0]['execution_id'] == previous_execution_id
    assert node._execution.state == 'preparing'
    assert node._execution.pending_command == ''
    assert node._execution.pending_command_id == ''
    assert node._execution.pending_acks == set()
    assert node._execution.pending_ack_deadline == 0.0
    assert node._execution.execution_id == 'exec-a'
    assert node._execution.coordinator_id == 'pc-a'


def test_execution_claim_defaults_missing_target_cycle_count():
    node = _node()
    incoming = SimpleNamespace(
        execution_id='exec-a',
        coordinator_id='pc-a',
    )

    node._accept_execution_claim(incoming, ('pc-a',))

    assert node._execution.execution_id == 'exec-a'
    assert node._execution.target_cycle_count == 0


def test_execution_claim_rejects_different_local_joined_roster():
    node = _node()
    node._registry.update(Member(
        pc_id='pc-b',
        boot_id='boot-b',
        joined=True,
        is_master=False,
        state='ready',
        trigger_sync_state='idle',
        trigger_sync_uncertainty_ms=0.0,
        alarm_grade=0,
        received_monotonic=time.monotonic(),
        sequence=1,
    ))
    node._registry.update(Member(
        pc_id='pc-c',
        boot_id='boot-c',
        joined=True,
        is_master=False,
        state='ready',
        trigger_sync_state='idle',
        trigger_sync_uncertainty_ms=0.0,
        alarm_grade=0,
        received_monotonic=time.monotonic(),
        sequence=1,
    ))
    command = GroupCommand(
        group_id='stage-a', execution_id='exec-a', coordinator_id='pc-a',
        command='prepare', participant_ids=['pc-a', 'pc-b'],
    )

    with pytest.raises(ValueError, match='참가 목록'):
        node._accept_execution_claim(command, ('pc-a', 'pc-b'))


def test_duplicate_pc_id_blocks_join_and_group_execution():
    node = _node()
    node._boot_id = 'boot-a'
    node._joined = False
    node._alarm_pub = _Publisher()
    node._command_pub = _Publisher()
    node.get_logger = lambda: SimpleNamespace(error=lambda _message: None)
    heartbeat = GroupHeartbeat(
        group_id='stage-a', pc_id='pc-a', boot_id='boot-other', joined=True,
    )

    node._heartbeat_callback(heartbeat)

    assert node._coordination_error['code'] == 'DUPLICATE_PC_ID'
    assert node._handle_local_request({'command': 'join'})['success'] is False
    assert node._handle_local_request({
        'command': 'acknowledge_group_error',
    })['success'] is False


def test_execution_claim_coordinator_must_be_in_fixed_roster():
    node = _node()
    node._execution = GroupExecution()
    command = GroupCommand(
        group_id='stage-a', execution_id='exec-x', coordinator_id='pc-x',
        command='prepare', participant_ids=['pc-a', 'pc-b'],
    )
    with pytest.raises(ValueError, match='참가 목록'):
        node._accept_execution_claim(command, ('pc-a', 'pc-b'))


def test_remote_alarm_only_updates_status_without_motion_control():
    node = _node()
    node._execution = GroupExecution()
    node._execution.begin('pc-a', ('pc-a', 'pc-b'))
    calls = []
    node._call_local_control = lambda payload, **_kwargs: calls.append(dict(payload)) or {'success': True}

    grade1 = GroupAlarm(group_id='stage-a', execution_id='exec-a', pc_id='pc-b')
    grade1.active = True
    grade1.grade = 1
    grade1.sequence = 1
    node._alarm_callback(grade1)
    assert node._alarm_registry.alarms['pc-b']['grade'] == 1

    grade2 = GroupAlarm(group_id='stage-a', execution_id='exec-a', pc_id='pc-b')
    grade2.active = True
    grade2.grade = 2
    grade2.sequence = 2
    node._alarm_callback(grade2)
    assert node._alarm_registry.alarms['pc-b']['grade'] == 2
    assert calls == []
    assert node._execution.execution_id != ''


def test_alarm_callback_ignores_duplicate_older_and_previous_boot_messages():
    node = _node()
    node._registry.update(Member(
        pc_id='pc-b',
        boot_id='boot-b',
        joined=True,
        is_master=False,
        state='ready',
        trigger_sync_state='idle',
        trigger_sync_uncertainty_ms=0.0,
        alarm_grade=0,
        received_monotonic=time.monotonic(),
        sequence=1,
    ))
    active = GroupAlarm(
        group_id='stage-a', pc_id='pc-b', boot_id='boot-b',
        active=True, grade=2, sequence=5, error_source='servo_alarm',
    )
    node._alarm_callback(active)
    assert node._alarm_registry.alarms['pc-b']['grade'] == 2

    older_clear = GroupAlarm(
        group_id='stage-a', pc_id='pc-b', boot_id='boot-b',
        active=False, grade=0, sequence=4, error_source='servo_alarm',
    )
    node._alarm_callback(older_clear)
    assert 'pc-b' in node._alarm_registry.alarms

    previous_boot_clear = GroupAlarm(
        group_id='stage-a', pc_id='pc-b', boot_id='boot-old',
        active=False, grade=0, sequence=99, error_source='servo_alarm',
    )
    node._alarm_callback(previous_boot_clear)
    assert 'pc-b' in node._alarm_registry.alarms


def test_local_alarm_blocks_new_group_execution_before_prepare():
    node = _node()
    node._joined = True
    node._local_status = {'safety_status': {'servo_alarm_grade': 3}}
    node._execution = GroupExecution()

    with pytest.raises(ValueError, match='Servo 알람'):
        node._start_group_execution()


def test_local_grade_three_alarm_stops_before_broadcast_and_clears_execution():
    node = _node()
    node._boot_id = 'boot-a'
    node._joined = True
    node._last_alarm_key = ()
    node._execution = GroupExecution()
    node._execution.begin('pc-a', ('pc-a', 'pc-b'))
    node._local_status = {
        'safety_status': {
            'servo_alarm_grade': 3,
            'servo_alarm_active': [{'axis': 2, 'code': 17, 'grade': 3}],
            'message': 'critical',
        },
    }
    events = []
    node._call_local_control = lambda payload, **_kwargs: (
        events.append(('local', payload['command'])) or {'success': True}
    )
    node._command_pub = _Publisher(events)
    node._alarm_pub = _Publisher()

    node._publish_local_alarm_if_changed()

    assert events == [('local', 'stop_now'), ('dds', 'stop_now')]
    assert node._alarm_pub.messages[0].grade == 3
    assert node._execution.state == 'error'
    assert node._execution.execution_id == ''


def test_alarm_from_previous_execution_cannot_stop_current_or_standalone_motion():
    node = _node()
    calls = []
    node._call_local_control = lambda payload, **_kwargs: calls.append(dict(payload)) or {'success': True}
    alarm = GroupAlarm(
        group_id='stage-a', execution_id='old-exec', pc_id='pc-b',
        active=True, grade=2, sequence=1,
    )
    node._alarm_callback(alarm)
    node._execution.execution_id = 'current-exec'
    node._alarm_callback(alarm)
    assert calls == []


def test_snapshot_reports_software_trigger_spreads():
    node = _node()
    node._registry = MemberRegistry()
    node._execution = GroupExecution()
    node._execution.begin('pc-a', ['pc-a', 'pc-b'])
    for pc in node._execution.participants:
        node._execution.mark_ready(pc)
    node._execution.initialize_action(now=1.0)
    node._execution.mark_armed('pc-a', 1.300)
    node._execution.mark_armed('pc-b', 1.312)
    node._execution.start_action(now=2.0)
    node._execution.mark_triggered('pc-a', 1, 2.300)
    node._execution.mark_triggered('pc-b', 1, 2.319)
    node._local_status = {}
    node._execution.execution_id = node._execution.execution_id
    node._execution.coordinator_id = 'pc-a'
    node._execution.participants = node._execution.participants
    node._joined = True
    node._trigger_sync_status = {
        'trigger_sync_state': 'ready',
        'trigger_sync_uncertainty_ms': 1.0,
        'trigger_sync_source': 'dds_relative_monotonic',
    }

    snapshot = node.snapshot()

    assert snapshot['execution']['initialize_spread_ms'] == pytest.approx(12.0)
    assert snapshot['execution']['initialize_within_tolerance'] is True
    assert snapshot['execution']['start_spread_ms'] == pytest.approx(19.0)
    assert snapshot['execution']['start_within_tolerance'] is True


def test_sync_result_maps_coordinator_deadline_to_local_monotonic():
    node = _node()
    node._joined = True
    node._execution.execution_id = 'exec-a'
    node._execution.coordinator_id = 'pc-a'
    node._execution.participants = ('pc-a', 'pc-b')
    node._time_sync_pub = _Publisher()
    result = GroupTimeSync(
        group_id='stage-a', execution_id='exec-a', coordinator_id='pc-a',
        target_pc_id='pc-a', kind='result', offset_ns=25_000_000,
        uncertainty_ns=2_000_000,
    )

    node._time_sync_callback(result)

    assert node._local_sync_offset_ns == 25_000_000
    assert node._trigger_sync_status['trigger_sync_state'] == 'ready'
    assert node._time_sync_pub.messages[-1].kind == 'result_ack'
    command = GroupCommand()
    command.scheduled_monotonic_ns = time.monotonic_ns() + 300_000_000
    local_target = node._local_schedule_ns(command)
    assert local_target - command.scheduled_monotonic_ns == 25_000_000


def test_missing_sync_responses_cancel_group_before_initialization():
    node = _node()
    node._joined = True
    node._execution = GroupExecution()
    execution_id = node._execution.begin('pc-a', ('pc-a', 'pc-b'))
    node._execution.execution_id = execution_id
    node._execution.coordinator_id = 'pc-a'
    node._execution.participants = ('pc-a', 'pc-b')
    node._sync_next_action = 'initialize'
    node._sync_deadline = time.monotonic() - 0.01
    node._sync_ready = {'pc-a'}
    events = []
    node._call_local_control = lambda payload, **_kwargs: (
        events.append(('local', payload['command'])) or {'success': True}
    )
    node._command_pub = _Publisher(events)
    node.get_logger = lambda: SimpleNamespace(error=lambda _message: None)

    node._drive_trigger_sync()

    assert events == [('local', 'group_cancel'), ('dds', 'cancel_before_start')]
    assert node._execution.state == 'releasing'
    assert node._trigger_sync_status['trigger_sync_state'] == 'failed'
    _peer_release_ack(node)
    assert node._execution.execution_id == ''
    assert node._execution.state == 'error'


def test_trigger_spread_excess_stops_once_and_blocks_group():
    node = _node()
    node._execution = GroupExecution()
    execution_id = node._execution.begin('pc-a', ('pc-a', 'pc-b'))
    node._execution.execution_id = execution_id
    node._execution.coordinator_id = 'pc-a'
    node._execution.participants = ('pc-a', 'pc-b')
    node._command_pub = _Publisher()
    node._call_local_control = lambda payload, **_kwargs: {'success': True, 'message': payload['command']}

    node._handle_trigger_spread_exceeded({
        'execution_id': execution_id,
        'participants': ('pc-a', 'pc-b'),
        'cycle_number': 1,
        'spread_ms': 24.0,
        'triggered': {'pc-a': 1.0, 'pc-b': 1.024},
    })

    assert node._command_pub.messages[-1].command == 'stop_now'
    assert node._execution.execution_id == ''
    assert node._coordination_error['code'] == 'GROUP_TRIGGER_SPREAD_EXCEEDED'


def test_initialize_spread_excess_stops_once_and_blocks_group():
    node = _node()
    node._execution = GroupExecution()
    execution_id = node._execution.begin('pc-a', ('pc-a', 'pc-b'))
    node._execution.execution_id = execution_id
    node._execution.coordinator_id = 'pc-a'
    node._execution.participants = ('pc-a', 'pc-b')
    node._command_pub = _Publisher()
    node._call_local_control = lambda payload, **_kwargs: {
        'success': True, 'message': payload['command'],
    }

    node._handle_trigger_spread_exceeded({
        'stage': 'initialize',
        'execution_id': execution_id,
        'participants': ('pc-a', 'pc-b'),
        'cycle_number': 0,
        'spread_ms': 24.0,
        'triggered': {'pc-a': 1.0, 'pc-b': 1.024},
    })

    assert node._command_pub.messages[-1].command == 'stop_now'
    assert node._execution.execution_id == ''
    assert node._coordination_error['code'] == (
        'GROUP_INITIALIZE_TRIGGER_SPREAD_EXCEEDED'
    )


def test_missing_motion_started_report_stops_and_blocks_group():
    node = _node()
    node._joined = True
    node._boot_id = 'boot-a'
    node._execution = GroupExecution()
    execution_id = node._execution.begin('pc-a', ('pc-a', 'pc-b'))
    node._execution.execution_id = execution_id
    node._execution.coordinator_id = 'pc-a'
    node._execution.participants = ('pc-a', 'pc-b')
    node._execution.motion_start_report_deadline = time.monotonic() - 0.1
    events = []
    node._call_local_control = lambda payload, **_kwargs: (
        events.append(('local', payload['command'])) or {'success': True}
    )
    node._command_pub = _Publisher(events)
    node._alarm_pub = _Publisher()
    node.get_logger = lambda: SimpleNamespace(error=lambda _message: None)

    node._enforce_motion_start_report_deadline()

    assert events == [('local', 'stop_now'), ('dds', 'stop_now')]
    assert node._coordination_error['code'] == (
        'GROUP_MOTION_START_REPORT_TIMEOUT'
    )
    with pytest.raises(ValueError, match='그룹 동기화 오류'):
        node._start_group_execution()


def test_group_error_acknowledgement_is_shared_and_unblocks_group_only():
    node = _node()
    node._joined = True
    node._boot_id = 'boot-a'
    node._coordination_error = {
        'active': True, 'code': 'GROUP_TRIGGER_SPREAD_EXCEEDED',
        'execution_id': 'exec-a', 'message': 'spread',
    }
    node._alarm_pub = _Publisher()

    result = node._acknowledge_coordination_error()

    assert result['success'] is True
    assert node._coordination_error == {}
    assert node._alarm_pub.messages[-1].active is False
    assert node._alarm_pub.messages[-1].error_source == 'group_coordination'


def test_shared_group_error_clear_removes_coordination_alarm_from_every_peer():
    node = _node()
    node._alarm_registry.alarms = {
        'pc-b': {'error_source': 'group_coordination'},
        'pc-c': {'error_source': 'servo_alarm'},
    }
    node._coordination_error = {'active': True}
    clear = GroupAlarm(
        group_id='stage-a', pc_id='pc-b', active=False,
        error_source='group_coordination',
    )

    node._alarm_callback(clear)

    assert node._coordination_error == {}
    assert set(node._alarm_registry.alarms) == {'pc-c'}


def test_begin_group_release_completes_after_all_stopped_acks():
    node = _node()
    node._execution = GroupExecution()
    execution_id = node._execution.begin('pc-a', ('pc-a', 'pc-b'))
    node._execution.execution_id = execution_id
    node._execution.coordinator_id = 'pc-a'
    node._execution.participants = ('pc-a', 'pc-b')
    node._execution.run_mode = 'once'
    node._execution.state = 'running'
    events = []
    node._call_local_control = lambda payload, **_kwargs: (
        events.append(('local', payload['command'])) or {'success': True}
    )
    node._command_pub = _Publisher(events)

    node._begin_group_release()

    assert events == [('local', 'group_cancel'), ('dds', 'cancel_before_start')]
    assert node._execution.state == 'releasing'
    assert node._execution.execution_id == execution_id
    _peer_release_ack(node)
    assert node._execution.execution_id == ''
    assert node._execution.state == 'stopped'


def test_shutdown_stops_active_local_execution_before_dds_notification():
    node = _node()
    node._execution = GroupExecution()
    execution_id = node._execution.begin('pc-a', ('pc-a', 'pc-b'))
    node._execution.execution_id = execution_id
    node._execution.coordinator_id = 'pc-a'
    node._execution.participants = ('pc-a', 'pc-b')
    events = []
    node._call_local_control = lambda payload, **_kwargs: (
        events.append(('local', payload['command'])) or {'success': True}
    )
    node._command_pub = _Publisher(events)

    node._stop_active_execution_for_shutdown()

    assert events == [('local', 'stop_now'), ('dds', 'stop_now')]
    assert node._execution.execution_id == ''


def test_main_treats_external_shutdown_as_a_clean_stop(monkeypatch):
    calls = []

    class FakeNode:
        def __init__(self, _config=None):
            pass

        def destroy_node(self):
            calls.append('destroy')

    monkeypatch.setattr(coordination_node.rclpy, 'init', lambda **_kwargs: None)
    monkeypatch.setattr(coordination_node, 'MotionCoordinationNode', FakeNode)
    monkeypatch.setattr(
        coordination_node.rclpy, 'spin',
        lambda _node: (_ for _ in ()).throw(ExternalShutdownException()),
    )
    monkeypatch.setattr(coordination_node.rclpy, 'ok', lambda: False)
    monkeypatch.setattr(coordination_node.rclpy, 'shutdown', lambda: calls.append('shutdown'))

    coordination_node.main()

    assert calls == ['destroy']


# 랜이 서비스보다 늦게 올라온 것을 알아채는가 · §6-96
#
# 실제로 겪은 고장이다 · 전원을 껐다 켜니 DHCP 가 서비스보다 5.6초 늦었고,
# DDS 는 루프백만 광고한 채 30분을 돌았다 · 로그에도 화면에도 「통신 단절」
# 말고는 아무것도 없어서 원인을 찾는 데만 한참 걸렸다 · 다시는 말없이 죽지
# 않도록 이 네 가지를 못 박는다.

def _drift(node, monkeypatch, addresses):
    monkeypatch.setattr(
        coordination_node.net_ready, 'lan_addresses', lambda: addresses,
    )
    node._check_network_drift()


def test_says_so_when_the_lan_arrives_after_the_service(monkeypatch):
    """주소 없이 떴는데 나중에 랜이 생겼다 · 이 PC 는 아무도 못 찾는다."""
    node = _node()
    node._boot_lan_addresses = ()

    _drift(node, monkeypatch, ('172.16.21.10',))

    assert node._network_stale['active'] is True
    assert node._network_stale['reason'] == '랜이 이 서비스보다 늦게 올라왔습니다'
    assert '172.16.21.10' in node._network_stale['message']
    assert '다시' in node._network_stale['message']  # 무엇을 해야 하는지까지 말한다


def test_says_so_when_the_address_changes_after_boot(monkeypatch):
    """WiFi 가 끊겼다 다른 주소로 붙어도 같은 고장이 난다."""
    node = _node()
    node._boot_lan_addresses = ('172.16.21.10',)

    _drift(node, monkeypatch, ('172.16.21.77',))

    assert node._network_stale['reason'] == '랜 주소가 기동 뒤에 바뀌었습니다'
    assert node._network_stale['boot'] == ['172.16.21.10']
    assert node._network_stale['now'] == ['172.16.21.77']


def test_clears_when_the_address_comes_back(monkeypatch):
    """주소가 제자리로 돌아오면 광고한 주소가 다시 맞다 · 경고를 거둔다."""
    node = _node()
    node._boot_lan_addresses = ('172.16.21.10',)

    _drift(node, monkeypatch, ('172.16.21.77',))
    assert node._network_stale

    _drift(node, monkeypatch, ('172.16.21.10',))
    assert node._network_stale == {}


def test_stays_quiet_while_the_address_is_unchanged(monkeypatch):
    """멀쩡할 때 떠들면 아무도 경고를 안 읽게 된다."""
    node = _node()
    node._boot_lan_addresses = ('172.16.21.10',)

    _drift(node, monkeypatch, ('172.16.21.10',))

    assert node._network_stale == {}


def test_snapshot_carries_the_network_warning(monkeypatch):
    """화면이 읽을 수 있어야 한다 · 로그에만 있으면 아무도 안 본다."""
    node = _node()
    node._boot_lan_addresses = ()
    node._joined = True
    node._local_status = {}

    _drift(node, monkeypatch, ('172.16.21.10',))

    assert node.snapshot()['network_stale']['active'] is True


# 20ms 와 70ms · 두 숫자가 갈리지 않게 · §6-148
#
# 상태 열쇠 이름이 `start_within_20ms` 였는데 판정은 `max_start_spread_ms`
# (70ms)로 했다 · 화면과 문서는 20ms 라 적혀 있어서, 실측 22.5ms 가 24회차
# 내내 나와도 "20 안에 들었겠거니" 하고 넘어갔다 · 숫자를 두 군데 적으면
# 반드시 갈린다 · 허용값은 연동 노드가 내려주는 것 하나만 쓴다.

def test_the_snapshot_says_which_tolerance_it_judged_by():
    node = _node()
    node._joined = True
    node._local_status = {}

    execution = node.snapshot()['execution']

    assert execution['spread_tolerance_ms'] == node._execution.max_start_spread_ms
    assert 'start_within_20ms' not in execution, '이름이 다시 숫자를 박았다'
    assert 'start_within_tolerance' in execution
    assert 'initialize_within_tolerance' in execution


def test_the_tolerance_is_not_twenty():
    """20ms 는 다른 것을 재는 값이다 · 둘을 같은 숫자로 만들면 또 헷갈린다."""
    node = _node()
    assert node._execution.max_start_spread_ms == 70.0
    assert node._config.max_trigger_sync_uncertainty_ms == 20.0
