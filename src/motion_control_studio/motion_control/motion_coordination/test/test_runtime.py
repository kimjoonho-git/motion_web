from pathlib import Path

import pytest

from motion_coordination.access_policy import AccessPolicy
from motion_coordination.configuration import CoordinationConfig, PeerConfig
from motion_coordination.runtime import CoordinationRuntime
from motion_coordination.security import ReplayError


SECRET = b's' * 32


def _config(
    machine_id, peer_id, host, peer_host, *, role, coordinator, mode='status'
):
    return CoordinationConfig(
        machine_id=machine_id,
        display_name=machine_id.upper(),
        mode=mode,
        role=role,
        coordinator_machine_id=coordinator,
        access=AccessPolicy.from_mapping({
            'coordination': {
                'enabled': True,
                'host': host,
                'port': 8010,
                'allowed_peer_networks': ['192.168.10.0/24'],
            },
        }),
        peers=(PeerConfig(peer_id, f'http://{peer_host}:8010'),),
        credential_file=Path('/tmp/not-used'),
    )


def test_two_peers_exchange_signed_status_and_acknowledgement():
    now = 1785805323.0
    pc_a = CoordinationRuntime(
        _config(
            'pc-a', 'pc-b', '192.168.10.10', '192.168.10.20',
            role='coordinator', coordinator='pc-a',
        ),
        {'pc-b': SECRET},
        wall_clock=lambda: now,
    )
    pc_b = CoordinationRuntime(
        _config(
            'pc-b', 'pc-a', '192.168.10.20', '192.168.10.10',
            role='peer', coordinator='pc-a',
        ),
        {'pc-a': SECRET},
        wall_clock=lambda: now,
    )
    sequence, request = pc_b.build_status_request('pc-a')

    response = pc_a.accept_status_request(
        body=request.body,
        headers=request.headers,
        remote_ip='192.168.10.20',
    )
    acknowledgement = pc_b.verify_status_response(
        'pc-a', sequence, response.body, response.headers
    )

    assert acknowledgement['payload']['accepted_sequence'] == sequence
    assert pc_a.snapshot()['peers'][0]['machine_id'] == 'pc-b'
    assert pc_b.snapshot()['remote_control_enabled'] is False


def test_off_mode_neither_sends_nor_accepts_status():
    config = CoordinationConfig.disabled(Path('/tmp'))
    runtime = CoordinationRuntime(config, {}, wall_clock=lambda: 1.0)

    try:
        runtime.build_status_request('pc-a')
    except ValueError as error:
        assert '연동 끔' in str(error)
    else:
        raise AssertionError('off mode unexpectedly sent status')


def _exchange_status(sender, receiver, sender_id, receiver_id, remote_ip):
    sequence, request = sender.build_status_request(receiver_id)
    response = receiver.accept_status_request(
        body=request.body,
        headers=request.headers,
        remote_ip=remote_ip,
    )
    sender.verify_status_response(
        receiver_id, sequence, response.body, response.headers
    )


def test_participant_readiness_round_trip_requires_active_manual_coordinator():
    now = 1785805323.0
    pc_a = CoordinationRuntime(
        _config(
            'pc-a', 'pc-b', '192.168.10.10', '192.168.10.20',
            role='coordinator', coordinator='pc-a', mode='participant',
        ),
        {'pc-b': SECRET},
        wall_clock=lambda: now,
    )
    pc_b = CoordinationRuntime(
        _config(
            'pc-b', 'pc-a', '192.168.10.20', '192.168.10.10',
            role='peer', coordinator='pc-a', mode='participant',
        ),
        {'pc-a': SECRET},
        wall_clock=lambda: now,
    )
    _exchange_status(pc_a, pc_b, 'pc-a', 'pc-b', '192.168.10.10')
    _exchange_status(pc_b, pc_a, 'pc-b', 'pc-a', '192.168.10.20')
    operation_id = pc_a.begin_readiness_operation('readiness-operation-0001')
    sequence, request = pc_a.build_readiness_request('pc-b', operation_id)

    accepted = pc_b.accept_readiness_request(
        body=request.body,
        headers=request.headers,
        remote_ip='192.168.10.10',
    )
    response = pc_b.build_readiness_response(
        'pc-a',
        accepted['request_sequence'],
        operation_id,
        {
            'readiness_version': 1,
            'state': 'ready',
            'reason_code': 'ready',
            'message': '실행 준비 완료',
        },
    )
    readiness = pc_a.verify_readiness_response(
        'pc-b', sequence, operation_id, response.body, response.headers
    )

    assert readiness['state'] == 'ready'


def test_participant_control_round_trip_contains_no_project_data():
    now = 1785805323.0
    pc_a = CoordinationRuntime(
        _config(
            'pc-a', 'pc-b', '192.168.10.10', '192.168.10.20',
            role='coordinator', coordinator='pc-a', mode='participant',
        ), {'pc-b': SECRET}, wall_clock=lambda: now,
    )
    pc_b = CoordinationRuntime(
        _config(
            'pc-b', 'pc-a', '192.168.10.20', '192.168.10.10',
            role='peer', coordinator='pc-a', mode='participant',
        ), {'pc-a': SECRET}, wall_clock=lambda: now,
    )
    _exchange_status(pc_a, pc_b, 'pc-a', 'pc-b', '192.168.10.10')
    _exchange_status(pc_b, pc_a, 'pc-b', 'pc-a', '192.168.10.20')
    payload = {'network_operation_id': 'run-1', 'command': 'run_once'}
    sequence, request = pc_a.build_control_request('pc-b', payload)
    assert b'project' not in request.body and b'motion_file' not in request.body
    accepted = pc_b.accept_control_request(
        body=request.body, headers=request.headers,
        remote_ip='192.168.10.10',
    )
    response = pc_b.build_control_response(
        'pc-a', accepted['request_sequence'], 'run-1',
        {'success': True, 'state': 'accepted', 'message': 'ok'},
    )
    result = pc_a.verify_control_response(
        'pc-b', sequence, 'run-1', response.body, response.headers,
    )
    assert result == {'success': True, 'state': 'accepted', 'message': 'ok'}
    assert pc_b.snapshot()['remote_control_enabled'] is True


def test_readiness_operation_id_cannot_be_reused():
    runtime = CoordinationRuntime(
        _config(
            'pc-a', 'pc-b', '192.168.10.10', '192.168.10.20',
            role='coordinator', coordinator='pc-a', mode='participant',
        ),
        {'pc-b': SECRET},
    )
    runtime.begin_readiness_operation('readiness-operation-0001')

    try:
        runtime.begin_readiness_operation('readiness-operation-0001')
    except ValueError as error:
        assert 'network_operation_id' in str(error)
    else:
        raise AssertionError('duplicate readiness operation was accepted')


def test_peer_restart_replaces_boot_session_and_rejects_retired_session():
    now = 1785805323.0
    config_a = _config(
        'pc-a', 'pc-b', '192.168.10.10', '192.168.10.20',
        role='coordinator', coordinator='pc-a',
    )
    pc_a_old = CoordinationRuntime(
        config_a, {'pc-b': SECRET}, wall_clock=lambda: now
    )
    pc_a_new = CoordinationRuntime(
        config_a, {'pc-b': SECRET}, wall_clock=lambda: now
    )
    pc_b = CoordinationRuntime(
        _config(
            'pc-b', 'pc-a', '192.168.10.20', '192.168.10.10',
            role='peer', coordinator='pc-a',
        ),
        {'pc-a': SECRET},
        wall_clock=lambda: now,
    )
    _exchange_status(pc_a_old, pc_b, 'pc-a', 'pc-b', '192.168.10.10')
    old_boot = pc_b.snapshot()['peers'][0]['coordination_boot_id']
    _exchange_status(pc_a_new, pc_b, 'pc-a', 'pc-b', '192.168.10.10')
    new_boot = pc_b.snapshot()['peers'][0]['coordination_boot_id']

    assert new_boot != old_boot
    _, retired_request = pc_a_old.build_status_request('pc-b')
    with pytest.raises(ReplayError, match='종료된'):
        pc_b.accept_status_request(
            body=retired_request.body,
            headers=retired_request.headers,
            remote_ip='192.168.10.10',
        )


def test_program_and_project_boundaries_rotate_only_opaque_session_ids():
    runtime = CoordinationRuntime(
        _config(
            'pc-a', 'pc-b', '192.168.10.10', '192.168.10.20',
            role='coordinator', coordinator='pc-a',
        ),
        {'pc-b': SECRET},
    )
    first = runtime.update_local_status({
        'bridge_instance_id': 'private-bridge-a',
        'project_generation': 1,
        'project_scope': {'selected_project_id': 'private-project-a'},
    })
    project_changed = runtime.update_local_status({
        'bridge_instance_id': 'private-bridge-a',
        'project_generation': 2,
        'project_scope': {'selected_project_id': 'private-project-b'},
    })
    program_restarted = runtime.update_local_status({
        'bridge_instance_id': 'private-bridge-b',
        'project_generation': 2,
        'project_scope': {'selected_project_id': 'private-project-b'},
    })

    assert (
        first['session']['program_session_id']
        == project_changed['session']['program_session_id']
    )
    assert (
        first['session']['readiness_session_id']
        != project_changed['session']['readiness_session_id']
    )
    assert (
        project_changed['session']['program_session_id']
        != program_restarted['session']['program_session_id']
    )
    encoded = str(program_restarted)
    assert 'private-project' not in encoded
    assert 'private-bridge' not in encoded
