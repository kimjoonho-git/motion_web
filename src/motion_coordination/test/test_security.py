from datetime import datetime, timezone

import pytest

from motion_coordination.access_policy import AccessPolicyError
from motion_coordination.protocol import build_envelope
from motion_coordination.security import (
    AuthenticationError,
    DuplicateOperationGuard,
    NonceReplayGuard,
    PeerRequestVerifier,
    ReplayError,
    SequenceGuard,
    canonical_json,
    create_hmac_key,
    peer_secrets_from_config,
    sign_request,
)


SECRET_A = b'a' * 32
NOW = datetime(2026, 8, 4, 1, 2, 3, tzinfo=timezone.utc).timestamp()


def _envelope(*, sequence=1, boot_id='boot-a', machine_id='pc-a'):
    return build_envelope(
        message_type='status',
        machine_id=machine_id,
        coordination_boot_id=boot_id,
        sequence=sequence,
        sent_at='2026-08-04T01:02:03Z',
        payload={'program': {'state': 'ready'}},
    )


def _signed(*, sequence=1, boot_id='boot-a', nonce='nonce-value-0001'):
    return sign_request(
        SECRET_A,
        method='POST',
        path='/coordination/v1/status',
        envelope=_envelope(sequence=sequence, boot_id=boot_id),
        nonce=nonce,
    )


def _verifier(**kwargs):
    return PeerRequestVerifier(
        {'pc-a': SECRET_A},
        wall_clock=lambda: NOW,
        **kwargs,
    )


def test_signed_request_round_trip_verifies_identity_and_envelope():
    request = _signed()

    verified = _verifier().verify(
        method='POST',
        path='/coordination/v1/status',
        body=request.body,
        headers=request.headers,
    )

    assert verified['sender']['machine_id'] == 'pc-a'
    assert verified['sequence'] == 1


def test_canonical_json_is_independent_from_mapping_order():
    assert canonical_json({'b': 2, 'a': 1}) == canonical_json({'a': 1, 'b': 2})


@pytest.mark.parametrize('changed', ['method', 'path', 'body'])
def test_signature_binds_method_path_and_body(changed):
    request = _signed()
    values = {
        'method': 'POST',
        'path': '/coordination/v1/status',
        'body': request.body,
    }
    if changed == 'method':
        values['method'] = 'PUT'
    elif changed == 'path':
        values['path'] = '/coordination/v1/other'
    else:
        values['body'] = request.body.replace(b'ready', b'error')

    with pytest.raises(AuthenticationError, match='서명이 일치하지 않습니다'):
        _verifier().verify(headers=request.headers, **values)


def test_unknown_peer_is_rejected_before_message_acceptance():
    request = _signed()
    headers = dict(request.headers)
    headers['x-motion-machine-id'] = 'pc-unknown'

    with pytest.raises(AuthenticationError, match='허용되지 않은 송신 PC'):
        _verifier().verify(
            method='POST',
            path='/coordination/v1/status',
            body=request.body,
            headers=headers,
        )


def test_stale_signed_request_is_rejected():
    request = _signed()
    verifier = PeerRequestVerifier(
        {'pc-a': SECRET_A},
        wall_clock=lambda: NOW + 31.0,
        max_clock_skew_sec=30.0,
    )

    with pytest.raises(AuthenticationError, match='요청 시각'):
        verifier.verify(
            method='POST',
            path='/coordination/v1/status',
            body=request.body,
            headers=request.headers,
        )


def test_reused_nonce_is_rejected_even_with_a_new_sequence():
    verifier = _verifier()
    first = _signed(sequence=1)
    second = _signed(sequence=2)
    verifier.verify(
        method='POST',
        path='/coordination/v1/status',
        body=first.body,
        headers=first.headers,
    )

    with pytest.raises(ReplayError, match='nonce'):
        verifier.verify(
            method='POST',
            path='/coordination/v1/status',
            body=second.body,
            headers=second.headers,
        )


def test_sequence_guard_rejects_duplicate_and_old_messages():
    guard = SequenceGuard()
    guard.accept('pc-a', 'boot-a', 2)

    with pytest.raises(ReplayError, match='sequence'):
        guard.accept('pc-a', 'boot-a', 2)
    with pytest.raises(ReplayError, match='sequence'):
        guard.accept('pc-a', 'boot-a', 1)


def test_sequence_guard_retires_the_previous_boot_session():
    guard = SequenceGuard()
    guard.accept('pc-a', 'boot-a', 10)
    guard.accept('pc-a', 'boot-b', 0)

    with pytest.raises(ReplayError, match='종료된 coordination_boot_id'):
        guard.accept('pc-a', 'boot-a', 11)


def test_nonce_can_be_used_again_only_after_guard_expiry():
    now = [10.0]
    guard = NonceReplayGuard(ttl_sec=5.0, clock=lambda: now[0])
    guard.accept('pc-a', 'nonce-value-0001')
    with pytest.raises(ReplayError, match='nonce'):
        guard.accept('pc-a', 'nonce-value-0001')
    now[0] = 15.0

    guard.accept('pc-a', 'nonce-value-0001')


def test_duplicate_operation_is_blocked_per_peer_until_expiry():
    now = [10.0]
    guard = DuplicateOperationGuard(ttl_sec=5.0, clock=lambda: now[0])
    guard.accept('pc-a', 'operation-0001')
    guard.accept('pc-b', 'operation-0001')
    with pytest.raises(ReplayError, match='network_operation_id'):
        guard.accept('pc-a', 'operation-0001')
    now[0] = 15.0

    guard.accept('pc-a', 'operation-0001')


def test_short_hmac_key_is_rejected():
    with pytest.raises(AuthenticationError, match='32바이트'):
        sign_request(
            b'short',
            method='POST',
            path='/coordination/v1/status',
            envelope=_envelope(),
            nonce='nonce-value-0001',
        )


def test_signer_rejects_existing_user_web_api_paths():
    with pytest.raises(AccessPolicyError, match='/coordination/v1/'):
        sign_request(
            SECRET_A,
            method='POST',
            path='/api/status',
            envelope=_envelope(),
            nonce='nonce-value-0001',
        )


def test_generated_hmac_key_round_trips_through_credential_contract():
    encoded = create_hmac_key()

    secrets = peer_secrets_from_config({
        'version': 1,
        'peers': {'pc-a': {'hmac_key_base64': encoded}},
    })

    assert len(secrets['pc-a']) == 32


@pytest.mark.parametrize(
    ('config', 'error'),
    [
        ({'version': 2, 'peers': {}}, 'version'),
        ({'version': 1, 'peers': {}}, 'peer별'),
        (
            {'version': 1, 'peers': {'invalid machine': {'hmac_key_base64': 'x'}}},
            'machine_id',
        ),
        (
            {'version': 1, 'peers': {'pc-a': {'hmac_key_base64': 'not-base64'}}},
            'Base64',
        ),
    ],
)
def test_invalid_credential_contract_is_rejected(config, error):
    with pytest.raises(AuthenticationError, match=error):
        peer_secrets_from_config(config)
