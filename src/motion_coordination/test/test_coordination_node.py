from types import SimpleNamespace

from rclpy.executors import ExternalShutdownException

import motion_coordination.coordination_node as coordination_node
from motion_coordination.execution_control import ExecutionLease, OperationJournal


class _NoopLock:
    def release(self):
        pass


def _coordinator_node():
    node = coordination_node.MotionCoordinationNode.__new__(
        coordination_node.MotionCoordinationNode
    )
    node._config = SimpleNamespace(
        machine_id='pc-a',
        display_name='PC A',
        role='coordinator',
        mode='participant',
        peers=(),
    )
    node._coordinator_lease_id = 'lease-a'
    node._synchronized_operation_active = False
    node._readiness_lock = _NoopLock()
    node._publish_response = lambda payload: None
    return node


def test_direct_motion_commands_use_the_acquired_coordinator_lease():
    node = _coordinator_node()
    sent = []
    responses = []
    node._broadcast_control = lambda payload: (
        sent.append(dict(payload))
        or {'success': True, 'message': 'ok', 'results': []}
    )
    node._publish_response = lambda payload: responses.append(dict(payload))

    node._run_control('request-a', {'command': 'run_once'})
    node._run_control('request-b', {'command': 'initialize'})

    assert [payload['lease_id'] for payload in sent] == ['lease-a', 'lease-a']
    assert all(response['success'] for response in responses)


def test_direct_motion_command_is_rejected_without_acquired_lease():
    node = _coordinator_node()
    node._coordinator_lease_id = ''
    sent = []
    responses = []
    node._broadcast_control = lambda payload: sent.append(dict(payload))
    node._publish_response = lambda payload: responses.append(dict(payload))

    node._run_control('request-a', {'command': 'run_once'})

    assert sent == []
    assert responses[0]['success'] is False
    assert 'lease_id' in responses[0]['message']


def test_local_motion_execution_rejects_a_mismatched_lease(tmp_path):
    node = _coordinator_node()
    node._operation_journal = OperationJournal(tmp_path / 'operations.json')
    node._execution_lease = ExecutionLease()
    node._execution_lease.acquire('pc-a', lease_id='lease-a')
    local_calls = []
    node._call_local_control = lambda payload: (
        local_calls.append(dict(payload)) or {'success': True}
    )

    rejected = node._execute_control('pc-a', {
        'network_operation_id': 'run-with-wrong-lease',
        'command': 'run_once',
        'lease_id': 'lease-b',
    })
    accepted = node._execute_control('pc-a', {
        'network_operation_id': 'run-with-correct-lease',
        'command': 'run_once',
        'lease_id': 'lease-a',
    })

    assert rejected['success'] is False
    assert accepted['success'] is True
    assert len(local_calls) == 1


def test_snapshot_exposes_synchronized_execution_state():
    node = _coordinator_node()
    published = []
    node._runtime = SimpleNamespace(snapshot=lambda: {'mode': 'participant'})
    node._execution_lease = SimpleNamespace(
        snapshot=lambda: {'state': 'network'}
    )
    node._synchronized_operation_active = True
    node._publisher = SimpleNamespace(
        publish=lambda message: published.append(message)
    )

    node._publish_snapshot()

    assert '"synchronized_operation_active":true' in published[0].data


def test_manual_release_is_rejected_during_synchronized_execution():
    node = _coordinator_node()
    node._synchronized_operation_active = True
    sent = []
    responses = []
    node._broadcast_control = lambda payload: sent.append(dict(payload))
    node._publish_response = lambda payload: responses.append(dict(payload))

    node._run_control('request-a', {'command': 'release_control'})

    assert sent == []
    assert responses[0]['success'] is False
    assert '동기 실행 중' in responses[0]['message']


def test_partial_start_is_cancelled_before_lease_is_released():
    node = _coordinator_node()
    node._config.peers = (SimpleNamespace(machine_id='pc-b', url='http://pc-b'),)
    calls = []

    def execute(_sender, payload):
        calls.append(('local', payload['command']))
        return {'success': True, 'state': 'accepted'}

    def request(_peer_id, _peer_url, payload):
        calls.append(('peer', payload['command']))
        if payload['command'] == 'start_at':
            return {'success': False, 'state': 'rejected'}
        return {'success': True, 'state': 'accepted'}

    node._execute_control = execute
    node._request_peer_control = request

    result = node._broadcast_control({
        'network_operation_id': 'start-operation',
        'command': 'start_at',
        'lease_id': 'lease-a',
        'start_at': 100.0,
        'cycle_sec': 1.0,
        'repeat_count': 1,
    })

    assert result['success'] is False
    assert calls == [
        ('local', 'start_at'),
        ('peer', 'start_at'),
        ('local', 'cancel_before_start'),
        ('peer', 'cancel_before_start'),
        ('local', 'release_control'),
        ('peer', 'release_control'),
    ]


def test_main_treats_external_shutdown_as_a_clean_stop(monkeypatch):
    calls = []

    class FakeNode:
        def destroy_node(self):
            calls.append('destroy')

    monkeypatch.setattr(coordination_node.rclpy, 'init', lambda args=None: None)
    monkeypatch.setattr(
        coordination_node, 'MotionCoordinationNode', FakeNode
    )
    monkeypatch.setattr(
        coordination_node.rclpy,
        'spin',
        lambda _node: (_ for _ in ()).throw(ExternalShutdownException()),
    )
    monkeypatch.setattr(coordination_node.rclpy, 'ok', lambda: False)
    monkeypatch.setattr(
        coordination_node.rclpy,
        'shutdown',
        lambda: calls.append('shutdown'),
    )

    coordination_node.main()

    assert calls == ['destroy']
