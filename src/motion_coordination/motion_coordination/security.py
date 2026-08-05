"""HMAC authentication and replay protection for coordination requests."""

import base64
import binascii
import hashlib
import hmac
import json
import math
import re
import secrets
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Dict, Mapping, Optional

from .access_policy import AccessPolicy
from .protocol import ProtocolError, validate_envelope


AUTH_SCHEME = 'motion-hmac-sha256-v1'
HEADER_MACHINE_ID = 'x-motion-machine-id'
HEADER_NONCE = 'x-motion-nonce'
HEADER_SIGNATURE = 'x-motion-signature'
_NONCE = re.compile(r'^[A-Za-z0-9_-]{16,128}$')
_OPERATION_ID = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.:-]{7,127}$')
_MACHINE_ID = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$')


class AuthenticationError(ValueError):
    """Raised when a signed coordination request cannot be trusted."""


class ReplayError(AuthenticationError):
    """Raised for a reused nonce, old sequence or duplicate operation."""


@dataclass(frozen=True)
class SignedRequest:
    """Canonical request body and authentication headers."""

    body: bytes
    headers: Dict[str, str]


def create_nonce() -> str:
    """Create a cryptographically random request nonce."""
    return secrets.token_urlsafe(24)


def create_hmac_key() -> str:
    """Create one Base64-encoded 256-bit peer credential."""
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).decode('ascii')


def peer_secrets_from_config(value: Mapping[str, Any]) -> Dict[str, bytes]:
    """Decode a project-independent version-1 peer credential mapping."""
    if not isinstance(value, Mapping) or value.get('version') != 1:
        raise AuthenticationError('자격증명 파일 version은 1이어야 합니다')
    peers = value.get('peers')
    if not isinstance(peers, Mapping) or not peers:
        raise AuthenticationError('peer별 HMAC 자격증명이 필요합니다')
    decoded: Dict[str, bytes] = {}
    for raw_machine_id, raw_peer in peers.items():
        machine_id = str(raw_machine_id or '').strip()
        if not _MACHINE_ID.fullmatch(machine_id):
            raise AuthenticationError('자격증명 machine_id 형식이 올바르지 않습니다')
        if not isinstance(raw_peer, Mapping):
            raise AuthenticationError(f'{machine_id} 자격증명은 객체여야 합니다')
        encoded = raw_peer.get('hmac_key_base64')
        if not isinstance(encoded, str) or not encoded.strip():
            raise AuthenticationError(f'{machine_id} HMAC 키가 없습니다')
        try:
            secret = base64.b64decode(
                encoded.strip().encode('ascii'),
                altchars=b'-_',
                validate=True,
            )
        except (UnicodeEncodeError, binascii.Error) as exc:
            raise AuthenticationError(
                f'{machine_id} HMAC 키 Base64 형식이 올바르지 않습니다'
            ) from exc
        decoded[machine_id] = _secret(secret)
    return decoded


def canonical_json(value: Mapping[str, Any]) -> bytes:
    """Serialize one JSON object deterministically for signing."""
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(',', ':'),
        )
    except (TypeError, ValueError) as exc:
        raise AuthenticationError('서명 본문을 JSON으로 변환할 수 없습니다') from exc
    return encoded.encode('utf-8')


def sign_request(
    secret: bytes,
    *,
    method: str,
    path: str,
    envelope: Mapping[str, Any],
    nonce: str,
) -> SignedRequest:
    """Create a canonical body and HMAC headers for one peer request."""
    key = _secret(secret)
    validated = validate_envelope(envelope)
    machine_id = str(validated['sender']['machine_id'])
    clean_nonce = _nonce(nonce)
    clean_method, clean_path = _request_target(method, path)
    AccessPolicy.validate_coordination_path(clean_path)
    body = canonical_json(validated)
    signature = _signature(
        key,
        clean_method,
        clean_path,
        body,
        clean_nonce,
    )
    return SignedRequest(
        body=body,
        headers={
            HEADER_MACHINE_ID: machine_id,
            HEADER_NONCE: clean_nonce,
            HEADER_SIGNATURE: signature,
        },
    )


class NonceReplayGuard:
    """Bounded per-peer nonce cache with a monotonic expiry clock."""

    def __init__(
        self,
        *,
        ttl_sec: float = 120.0,
        max_entries_per_peer: int = 4096,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not math.isfinite(ttl_sec) or ttl_sec <= 0:
            raise ValueError('nonce TTL은 0보다 커야 합니다')
        if max_entries_per_peer < 1:
            raise ValueError('peer별 nonce 저장 수는 1 이상이어야 합니다')
        self._ttl_sec = float(ttl_sec)
        self._max_entries = int(max_entries_per_peer)
        self._clock = clock
        self._entries: Dict[str, Dict[str, float]] = {}
        self._lock = threading.Lock()

    def accept(self, machine_id: str, nonce: str) -> None:
        """Record a nonce or raise when the peer already used it."""
        now = float(self._clock())
        with self._lock:
            entries = self._entries.setdefault(machine_id, {})
            expired = [key for key, expiry in entries.items() if expiry <= now]
            for key in expired:
                entries.pop(key, None)
            if nonce in entries:
                raise ReplayError('이미 사용한 nonce 요청입니다')
            if len(entries) >= self._max_entries:
                oldest = min(entries, key=entries.get)
                entries.pop(oldest, None)
            entries[nonce] = now + self._ttl_sec


class SequenceGuard:
    """Reject reordered messages and messages from retired peer sessions."""

    def __init__(self) -> None:
        self._current: Dict[str, tuple[str, int]] = {}
        self._retired: Dict[str, set[str]] = {}
        self._lock = threading.Lock()

    def accept(self, machine_id: str, boot_id: str, sequence: int) -> None:
        """Advance one peer sequence or raise for replayed state."""
        with self._lock:
            current = self._current.get(machine_id)
            if current is None:
                self._current[machine_id] = (boot_id, sequence)
                return
            current_boot, current_sequence = current
            if boot_id == current_boot:
                if sequence <= current_sequence:
                    raise ReplayError('이전 또는 중복 sequence 요청입니다')
                self._current[machine_id] = (boot_id, sequence)
                return
            retired = self._retired.setdefault(machine_id, set())
            if boot_id in retired:
                raise ReplayError('종료된 coordination_boot_id 요청입니다')
            retired.add(current_boot)
            self._current[machine_id] = (boot_id, sequence)


class DuplicateOperationGuard:
    """Reject repeated network operation IDs independently from nonces."""

    def __init__(
        self,
        *,
        ttl_sec: float = 3600.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not math.isfinite(ttl_sec) or ttl_sec <= 0:
            raise ValueError('operation TTL은 0보다 커야 합니다')
        self._ttl_sec = float(ttl_sec)
        self._clock = clock
        self._entries: Dict[tuple[str, str], float] = {}
        self._lock = threading.Lock()

    def accept(self, machine_id: str, operation_id: str) -> None:
        """Record an operation ID or reject a duplicate within the TTL."""
        clean_id = str(operation_id or '').strip()
        if not _OPERATION_ID.fullmatch(clean_id):
            raise AuthenticationError('network_operation_id 형식이 올바르지 않습니다')
        now = float(self._clock())
        key = (machine_id, clean_id)
        with self._lock:
            expired = [item for item, expiry in self._entries.items() if expiry <= now]
            for item in expired:
                self._entries.pop(item, None)
            if key in self._entries:
                raise ReplayError('이미 처리한 network_operation_id입니다')
            self._entries[key] = now + self._ttl_sec


class PeerRequestVerifier:
    """Verify peer identity, signature, freshness, nonce and sequence."""

    def __init__(
        self,
        peer_secrets: Mapping[str, bytes],
        *,
        max_clock_skew_sec: float = 30.0,
        nonce_guard: Optional[NonceReplayGuard] = None,
        sequence_guard: Optional[SequenceGuard] = None,
        wall_clock: Callable[[], float] = time.time,
    ) -> None:
        if not math.isfinite(max_clock_skew_sec) or max_clock_skew_sec <= 0:
            raise ValueError('허용 시각 차이는 0보다 커야 합니다')
        self._peer_secrets = {
            str(machine_id): _secret(secret)
            for machine_id, secret in peer_secrets.items()
        }
        self._max_clock_skew_sec = float(max_clock_skew_sec)
        self._nonce_guard = nonce_guard or NonceReplayGuard()
        self._sequence_guard = sequence_guard or SequenceGuard()
        self._wall_clock = wall_clock

    def verify(
        self,
        *,
        method: str,
        path: str,
        body: bytes,
        headers: Mapping[str, str],
    ) -> Dict[str, Any]:
        """Return a validated envelope or reject the request."""
        clean_method, clean_path = _request_target(method, path)
        AccessPolicy.validate_coordination_path(clean_path)
        normalized_headers = {
            str(key).lower(): str(value).strip()
            for key, value in headers.items()
        }
        machine_id = normalized_headers.get(HEADER_MACHINE_ID, '')
        nonce = _nonce(normalized_headers.get(HEADER_NONCE, ''))
        supplied = normalized_headers.get(HEADER_SIGNATURE, '').lower()
        if not re.fullmatch(r'[0-9a-f]{64}', supplied):
            raise AuthenticationError('HMAC 서명 형식이 올바르지 않습니다')
        secret = self._peer_secrets.get(machine_id)
        if secret is None:
            raise AuthenticationError('허용되지 않은 송신 PC입니다')
        expected = _signature(
            secret,
            clean_method,
            clean_path,
            body,
            nonce,
        )
        if not hmac.compare_digest(supplied, expected):
            raise AuthenticationError('HMAC 서명이 일치하지 않습니다')

        envelope = validate_envelope(_decode_json_object(body))
        sender = envelope['sender']
        if sender['machine_id'] != machine_id:
            raise AuthenticationError('헤더와 본문의 송신 PC가 일치하지 않습니다')
        sent_at = _timestamp(str(envelope['sent_at']))
        if abs(float(self._wall_clock()) - sent_at) > self._max_clock_skew_sec:
            raise AuthenticationError('요청 시각이 허용 범위를 벗어났습니다')

        self._nonce_guard.accept(machine_id, nonce)
        self._sequence_guard.accept(
            machine_id,
            str(sender['coordination_boot_id']),
            int(envelope['sequence']),
        )
        return envelope


def _secret(value: bytes) -> bytes:
    if not isinstance(value, bytes) or len(value) < 32:
        raise AuthenticationError('HMAC 키는 32바이트 이상이어야 합니다')
    return bytes(value)


def _nonce(value: str) -> str:
    clean = str(value or '').strip()
    if not _NONCE.fullmatch(clean):
        raise AuthenticationError('nonce 형식이 올바르지 않습니다')
    return clean


def _request_target(method: str, path: str) -> tuple[str, str]:
    clean_method = str(method or '').strip().upper()
    clean_path = str(path or '').strip()
    if not re.fullmatch(r'[A-Z]+', clean_method):
        raise AuthenticationError('HTTP 메서드 형식이 올바르지 않습니다')
    if not clean_path.startswith('/') or '#' in clean_path:
        raise AuthenticationError('요청 경로 형식이 올바르지 않습니다')
    return clean_method, clean_path


def _signature(
    secret: bytes,
    method: str,
    path: str,
    body: bytes,
    nonce: str,
) -> str:
    body_hash = hashlib.sha256(body).hexdigest()
    signing_input = '\n'.join([
        AUTH_SCHEME,
        method,
        path,
        body_hash,
        nonce,
    ]).encode('utf-8')
    return hmac.new(secret, signing_input, hashlib.sha256).hexdigest()


def _decode_json_object(body: bytes) -> Dict[str, Any]:
    if not isinstance(body, bytes):
        raise AuthenticationError('요청 본문은 bytes여야 합니다')

    def no_duplicates(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise AuthenticationError(f'중복 JSON 필드입니다: {key}')
            result[key] = value
        return result

    try:
        value = json.loads(body.decode('utf-8'), object_pairs_hook=no_duplicates)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AuthenticationError('요청 본문 JSON 형식이 올바르지 않습니다') from exc
    if not isinstance(value, dict):
        raise AuthenticationError('요청 본문은 JSON 객체여야 합니다')
    return value


def _timestamp(value: str) -> float:
    try:
        parsed = datetime.fromisoformat(value[:-1] + '+00:00')
        return parsed.timestamp()
    except (TypeError, ValueError) as exc:
        raise ProtocolError('sent_at을 해석할 수 없습니다') from exc
