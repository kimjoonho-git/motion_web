from pathlib import Path

from motion_coordination.access_policy import AccessPolicy
from motion_coordination.configuration import CoordinationConfig, PeerConfig
from motion_coordination.peer_registry import PeerRegistry
from motion_coordination.status_adapter import adapt_status


def _config(*, role='peer', coordinator='pc-a'):
    return CoordinationConfig(
        machine_id='pc-b',
        display_name='PC B',
        mode='status',
        role=role,
        coordinator_machine_id=coordinator,
        access=AccessPolicy.from_mapping({}),
        peers=(PeerConfig('pc-a', 'http://192.168.10.10:8010'),),
        credential_file=Path('/tmp/not-used'),
        peer_timeout_sec=3.0,
    )


def _payload(role='coordinator'):
    return adapt_status(
        {},
        coordination_mode='status',
        coordination_role=role,
        coordinator_machine_id='pc-a',
    )


def test_registered_coordinator_claim_becomes_active():
    registry = PeerRegistry(_config(), clock=lambda: 10.0)
    registry.accept('pc-a', 'boot-a', 1, _payload(), '192.168.10.10')

    status = registry.snapshot()['coordinator']

    assert status == {
        'state': 'active',
        'machine_id': 'pc-a',
        'claims': ['pc-a'],
        'authority_allowed': True,
    }


def test_duplicate_coordinator_claim_is_reported_as_conflict():
    config = _config(role='coordinator', coordinator='pc-b')
    registry = PeerRegistry(config, clock=lambda: 10.0)
    registry.accept('pc-a', 'boot-a', 1, _payload(), '192.168.10.10')

    status = registry.snapshot()['coordinator']

    assert status['state'] == 'conflict'
    assert status['claims'] == ['pc-a', 'pc-b']
    assert status['authority_allowed'] is False


def test_expired_peer_is_removed_and_coordinator_returns_to_waiting():
    now = [10.0]
    registry = PeerRegistry(_config(), clock=lambda: now[0])
    registry.accept('pc-a', 'boot-a', 1, _payload(), '192.168.10.10')
    now[0] = 14.0

    snapshot = registry.snapshot()

    assert snapshot['peers'] == []
    assert snapshot['coordinator']['state'] == 'waiting'
