import copy

import pytest

from motion_coordination.protocol import (
    ProtocolError,
    build_envelope,
    validate_envelope,
)


def _message(**overrides):
    message = {
        'schema_version': 1,
        'message_type': 'status',
        'sender': {
            'machine_id': 'pc-a',
            'coordination_boot_id': 'boot-123',
        },
        'sequence': 7,
        'sent_at': '2026-08-04T13:20:30.123Z',
        'payload': {},
    }
    message.update(overrides)
    return message


def test_build_envelope_keeps_payload_contract_open_for_later_fields():
    first = build_envelope(
        message_type='status',
        machine_id='pc-a',
        coordination_boot_id='boot-123',
        sequence=1,
        sent_at='2026-08-04T13:20:30Z',
        payload={'program': {'state': 'ready'}},
    )
    expanded = build_envelope(
        message_type='status',
        machine_id='pc-a',
        coordination_boot_id='boot-123',
        sequence=2,
        sent_at='2026-08-04T13:20:31Z',
        payload={
            'program': {'state': 'ready'},
            'future_status': {'value': 1},
        },
        extensions={'vendor.example': {'flag': True}},
    )

    assert first['payload']['program']['state'] == 'ready'
    assert expanded['payload']['future_status']['value'] == 1
    assert expanded['extensions']['vendor.example']['flag'] is True


def test_validation_preserves_unknown_optional_envelope_fields():
    message = _message(future_optional={'enabled': True})

    validated = validate_envelope(message)

    assert validated['future_optional'] == {'enabled': True}


def test_validation_result_does_not_share_mutable_payload_with_caller():
    message = _message(payload={'status': {'items': [1]}})
    original = copy.deepcopy(message)

    validated = validate_envelope(message)
    validated['payload']['status']['items'].append(2)

    assert message == original


@pytest.mark.parametrize(
    ('change', 'error'),
    [
        ({'schema_version': 2}, 'schema_version'),
        ({'message_type': 'Status Changed'}, 'message_type'),
        ({'sender': {}}, 'sender.machine_id'),
        ({'sequence': -1}, 'sequence'),
        ({'sequence': True}, 'sequence'),
        ({'sent_at': '2026-08-04T13:20Z'}, 'sent_at'),
        ({'sent_at': '2026-08-04T13:20:30+09:00'}, 'sent_at'),
        ({'payload': []}, 'payload'),
        ({'payload': {'invalid': float('nan')}}, 'JSON'),
        ({'payload': {'invalid': b'bytes'}}, 'JSON'),
        ({'extensions': []}, 'extensions'),
    ],
)
def test_invalid_stable_envelope_fields_are_rejected(change, error):
    with pytest.raises(ProtocolError, match=error):
        validate_envelope(_message(**change))


def test_missing_stable_field_is_rejected():
    message = _message()
    del message['payload']

    with pytest.raises(ProtocolError, match='payload'):
        validate_envelope(message)
