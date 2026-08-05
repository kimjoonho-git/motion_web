"""
Stable envelope for network coordination messages.

The envelope is deliberately smaller than any status payload.  Transport,
authentication and status adapters may add their own rules without coupling
the base protocol to the local application's internal state shape.
"""

import copy
import json
import re
from datetime import datetime, timezone
from typing import Any, Dict, Mapping


SCHEMA_VERSION = 1
_MESSAGE_TYPE = re.compile(r'^[a-z][a-z0-9_]{0,63}$')
_IDENTIFIER = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$')
_UTC_RFC3339 = re.compile(
    r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?Z$'
)
_REQUIRED_FIELDS = {
    'schema_version',
    'message_type',
    'sender',
    'sequence',
    'sent_at',
    'payload',
}


class ProtocolError(ValueError):
    """Raised when a coordination message violates the stable envelope."""


def build_envelope(
    *,
    message_type: str,
    machine_id: str,
    coordination_boot_id: str,
    sequence: int,
    sent_at: str,
    payload: Mapping[str, Any],
    extensions: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    """Build and validate a version-1 coordination envelope."""
    if not isinstance(payload, Mapping):
        raise ProtocolError('payload는 객체여야 합니다')
    if extensions is not None and not isinstance(extensions, Mapping):
        raise ProtocolError('extensions는 객체여야 합니다')
    envelope: Dict[str, Any] = {
        'schema_version': SCHEMA_VERSION,
        'message_type': message_type,
        'sender': {
            'machine_id': machine_id,
            'coordination_boot_id': coordination_boot_id,
        },
        'sequence': sequence,
        'sent_at': sent_at,
        'payload': copy.deepcopy(dict(payload)),
    }
    if extensions is not None:
        envelope['extensions'] = copy.deepcopy(dict(extensions))
    return validate_envelope(envelope)


def validate_envelope(message: Mapping[str, Any]) -> Dict[str, Any]:
    """Validate stable fields while preserving future optional fields."""
    if not isinstance(message, Mapping):
        raise ProtocolError('네트워크 메시지는 객체여야 합니다')
    missing = sorted(_REQUIRED_FIELDS.difference(message.keys()))
    if missing:
        raise ProtocolError(f"필수 공통 필드가 없습니다: {', '.join(missing)}")
    if message.get('schema_version') != SCHEMA_VERSION:
        raise ProtocolError(
            f"지원하지 않는 schema_version입니다: {message.get('schema_version')}"
        )

    message_type = message.get('message_type')
    if not isinstance(message_type, str) or not _MESSAGE_TYPE.fullmatch(message_type):
        raise ProtocolError('message_type 형식이 올바르지 않습니다')

    sender = message.get('sender')
    if not isinstance(sender, Mapping):
        raise ProtocolError('sender는 객체여야 합니다')
    _validate_identifier(sender.get('machine_id'), 'sender.machine_id')
    _validate_identifier(
        sender.get('coordination_boot_id'),
        'sender.coordination_boot_id',
    )

    sequence = message.get('sequence')
    if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 0:
        raise ProtocolError('sequence는 0 이상의 정수여야 합니다')
    _validate_utc_timestamp(message.get('sent_at'))

    if not isinstance(message.get('payload'), Mapping):
        raise ProtocolError('payload는 객체여야 합니다')
    extensions = message.get('extensions')
    if extensions is not None and not isinstance(extensions, Mapping):
        raise ProtocolError('extensions는 객체여야 합니다')
    try:
        json.dumps(message, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ProtocolError('네트워크 메시지는 JSON 형식으로 변환할 수 있어야 합니다') from exc

    return copy.deepcopy(dict(message))


def _validate_identifier(value: Any, field: str) -> None:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise ProtocolError(f'{field} 형식이 올바르지 않습니다')


def _validate_utc_timestamp(value: Any) -> None:
    if not isinstance(value, str) or not _UTC_RFC3339.fullmatch(value):
        raise ProtocolError('sent_at은 UTC RFC3339 형식이어야 합니다')
    try:
        parsed = datetime.fromisoformat(value[:-1] + '+00:00')
    except ValueError as exc:
        raise ProtocolError('sent_at은 UTC RFC3339 형식이어야 합니다') from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ProtocolError('sent_at은 UTC RFC3339 형식이어야 합니다')
