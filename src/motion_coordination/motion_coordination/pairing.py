"""Short-lived encrypted pairing for two coordination PCs."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import ipaddress
import json
import secrets
import socket
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Dict, Mapping
from urllib.parse import urlparse

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import x25519
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from .configuration import configure_paired_peer
from .access_policy import is_internal_ipv4
from .security import canonical_json, create_hmac_key


PAIRING_VERSION = 1
PAIRING_TTL_SEC = 300.0
PAIRING_CODE_ALPHABET = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'
PAIRING_CODE_LENGTH = 8
PAIRING_PBKDF2_ITERATIONS = 200_000
PAIRING_MAX_ATTEMPTS = 5
PAIRING_INFO_PATH = '/api/coordination/pairing/info'
PAIRING_CLAIM_PATH = '/api/coordination/pairing/claim'
MAX_PAIRING_BODY_BYTES = 32 * 1024


class PairingError(ValueError):
    """Raised when a pairing offer or encrypted claim is invalid."""


class PairingCoordinator:
    """Own one expiring central-PC pairing offer in memory."""

    def __init__(
        self,
        workspace: Path,
        config_path: Path,
        *,
        wall_clock: Callable[[], float] = time.time,
        monotonic_clock: Callable[[], float] = time.monotonic,
        local_ip_resolver: Callable[[str], str] | None = None,
    ) -> None:
        self.workspace = Path(workspace).resolve()
        self.config_path = Path(config_path).expanduser()
        self._wall_clock = wall_clock
        self._monotonic_clock = monotonic_clock
        self._local_ip_resolver = local_ip_resolver or local_ipv4_for_peer
        self._lock = threading.Lock()
        self._session: Dict[str, Any] = {}

    def start(self, machine_id: str, display_name: str = '') -> Dict[str, Any]:
        """Replace any previous offer and return its one-time user code."""
        clean_id = _machine_id(machine_id)
        code = ''.join(
            secrets.choice(PAIRING_CODE_ALPHABET)
            for _ in range(PAIRING_CODE_LENGTH)
        )
        private_key = x25519.X25519PrivateKey.generate()
        now = float(self._wall_clock())
        with self._lock:
            self._session = {
                'session_id': secrets.token_urlsafe(24),
                'machine_id': clean_id,
                'display_name': str(display_name or clean_id).strip()[:128],
                'code': code,
                'private_key': private_key,
                'public_key': _public_key_text(private_key.public_key()),
                'expires_at': now + PAIRING_TTL_SEC,
                'expires_monotonic': float(self._monotonic_clock()) + PAIRING_TTL_SEC,
                'attempts': 0,
                'state': 'waiting',
                'paired_peer': '',
            }
        return {
            'success': True,
            'pairing_code': f'{code[:4]}-{code[4:]}',
            **self.status(),
        }

    def status(self) -> Dict[str, Any]:
        """Return a non-secret summary suitable for the user web."""
        with self._lock:
            session = dict(self._session)
        if not session:
            return {'state': 'idle'}
        if self._expired(session):
            return {'state': 'expired'}
        return {
            'state': str(session.get('state') or 'waiting'),
            'machine_id': str(session.get('machine_id') or ''),
            'paired_peer': str(session.get('paired_peer') or ''),
            'expires_at': float(session.get('expires_at') or 0.0),
        }

    def info(self) -> Dict[str, Any]:
        """Return public offer material; the pairing code is never returned."""
        with self._lock:
            session = dict(self._session)
        self._require_waiting(session)
        return {
            'pairing_version': PAIRING_VERSION,
            'session_id': session['session_id'],
            'coordinator_machine_id': session['machine_id'],
            'coordinator_display_name': session['display_name'],
            'coordinator_public_key': session['public_key'],
            'expires_at': session['expires_at'],
        }

    def claim(self, payload: Mapping[str, Any], remote_ip: str) -> Dict[str, Any]:
        """Verify one code proof, save the central config, and encrypt the key."""
        if not isinstance(payload, Mapping):
            raise PairingError('연동 요청 형식이 올바르지 않습니다')
        participant_ip = _private_ipv4(remote_ip, '참여 PC IP')
        with self._lock:
            session = self._session
            self._require_waiting(session)
            session['attempts'] = int(session.get('attempts') or 0) + 1
            if session['attempts'] > PAIRING_MAX_ATTEMPTS:
                session['state'] = 'blocked'
                raise PairingError('연동 코드 확인 횟수를 초과했습니다')
            participant_id = _machine_id(payload.get('participant_machine_id'))
            if participant_id == session['machine_id']:
                raise PairingError('두 PC의 machine_id는 서로 달라야 합니다')
            participant_display = str(
                payload.get('participant_display_name') or participant_id
            ).strip()[:128]
            participant_public_text = str(
                payload.get('participant_public_key') or ''
            ).strip()
            participant_public = _load_public_key(participant_public_text)
            transcript = _transcript(
                session['session_id'],
                session['machine_id'],
                participant_id,
                session['public_key'],
                participant_public_text,
            )
            code_key = _code_key(session['code'], session['session_id'])
            supplied_proof = _decode(
                str(payload.get('proof') or ''), '연동 코드 증명'
            )
            expected_proof = hmac.new(
                code_key, transcript, hashlib.sha256
            ).digest()
            if not hmac.compare_digest(supplied_proof, expected_proof):
                raise PairingError('연동 코드가 올바르지 않습니다')

            coordinator_ip = _private_ipv4(
                self._local_ip_resolver(participant_ip), '중앙 PC IP'
            )
            shared = session['private_key'].exchange(participant_public)
            encryption_key = _encryption_key(shared, code_key, transcript)
            hmac_key = create_hmac_key()
            configure_paired_peer(
                self.config_path,
                workspace=self.workspace,
                machine_id=session['machine_id'],
                display_name=session['display_name'],
                local_ip=coordinator_ip,
                peer_machine_id=participant_id,
                peer_ip=participant_ip,
                coordinator=True,
                hmac_key_base64=hmac_key,
            )
            bundle = canonical_json({
                'pairing_version': PAIRING_VERSION,
                'session_id': session['session_id'],
                'coordinator_machine_id': session['machine_id'],
                'coordinator_display_name': session['display_name'],
                'coordinator_ip': coordinator_ip,
                'participant_machine_id': participant_id,
                'participant_ip': participant_ip,
                'hmac_key_base64': hmac_key,
            })
            nonce = secrets.token_bytes(12)
            encrypted = AESGCM(encryption_key).encrypt(nonce, bundle, transcript)
            session['state'] = 'paired'
            session['paired_peer'] = participant_id
            session.pop('code', None)
            session.pop('private_key', None)
            return {
                'pairing_version': PAIRING_VERSION,
                'session_id': session['session_id'],
                'nonce': _encode(nonce),
                'encrypted_bundle': _encode(encrypted),
            }

    def _expired(self, session: Mapping[str, Any]) -> bool:
        return float(self._monotonic_clock()) >= float(
            session.get('expires_monotonic') or 0.0
        )

    def _require_waiting(self, session: Mapping[str, Any]) -> None:
        if not session:
            raise PairingError('중앙 PC에서 연동 코드를 먼저 생성하세요')
        if self._expired(session):
            raise PairingError('연동 코드가 만료되었습니다')
        if session.get('state') != 'waiting':
            raise PairingError('이미 사용했거나 차단된 연동 코드입니다')


def join_pairing(
    coordinator_host: str,
    code: str,
    machine_id: str,
    display_name: str,
    *,
    workspace: Path,
    config_path: Path,
    opener: Callable[..., Any] = urllib.request.urlopen,
    local_ip_resolver: Callable[[str], str] | None = None,
) -> Dict[str, Any]:
    """Join one central offer and save the decrypted participant config."""
    coordinator_ip, base_url = _coordinator_url(coordinator_host)
    info = _request_json(opener, f'{base_url}{PAIRING_INFO_PATH}')
    if int(info.get('pairing_version') or 0) != PAIRING_VERSION:
        raise PairingError('지원하지 않는 연동 코드 버전입니다')
    session_id = str(info.get('session_id') or '').strip()
    coordinator_id = _machine_id(info.get('coordinator_machine_id'))
    coordinator_display = str(
        info.get('coordinator_display_name') or coordinator_id
    ).strip()[:128]
    coordinator_public_text = str(
        info.get('coordinator_public_key') or ''
    ).strip()
    coordinator_public = _load_public_key(coordinator_public_text)
    participant_id = _machine_id(machine_id)
    if participant_id == coordinator_id:
        raise PairingError('두 PC의 machine_id는 서로 달라야 합니다')
    clean_code = _pairing_code(code)
    private_key = x25519.X25519PrivateKey.generate()
    participant_public_text = _public_key_text(private_key.public_key())
    transcript = _transcript(
        session_id,
        coordinator_id,
        participant_id,
        coordinator_public_text,
        participant_public_text,
    )
    code_key = _code_key(clean_code, session_id)
    claim = {
        'participant_machine_id': participant_id,
        'participant_display_name': str(
            display_name or participant_id
        ).strip()[:128],
        'participant_public_key': participant_public_text,
        'proof': _encode(hmac.new(code_key, transcript, hashlib.sha256).digest()),
    }
    response = _request_json(
        opener,
        f'{base_url}{PAIRING_CLAIM_PATH}',
        payload=claim,
    )
    if str(response.get('session_id') or '') != session_id:
        raise PairingError('연동 응답 세션이 일치하지 않습니다')
    shared = private_key.exchange(coordinator_public)
    encryption_key = _encryption_key(shared, code_key, transcript)
    try:
        decrypted = AESGCM(encryption_key).decrypt(
            _decode(str(response.get('nonce') or ''), '암호화 nonce'),
            _decode(
                str(response.get('encrypted_bundle') or ''),
                '암호화 연동 설정',
            ),
            transcript,
        )
        bundle = json.loads(decrypted.decode('utf-8'))
    except (ValueError, UnicodeError, json.JSONDecodeError) as exc:
        raise PairingError('연동 설정 암호화 검증에 실패했습니다') from exc
    if (
        not isinstance(bundle, dict)
        or bundle.get('session_id') != session_id
        or bundle.get('participant_machine_id') != participant_id
        or bundle.get('coordinator_machine_id') != coordinator_id
    ):
        raise PairingError('연동 설정 대상 PC가 일치하지 않습니다')
    resolver = local_ip_resolver or local_ipv4_for_peer
    participant_ip = _private_ipv4(
        resolver(coordinator_ip), '참여 PC IP'
    )
    if bundle.get('participant_ip') != participant_ip:
        raise PairingError('중앙 PC가 확인한 참여 PC IP와 일치하지 않습니다')
    try:
        configure_paired_peer(
            config_path,
            workspace=workspace,
            machine_id=participant_id,
            display_name=str(display_name or participant_id),
            local_ip=participant_ip,
            peer_machine_id=coordinator_id,
            peer_ip=coordinator_ip,
            coordinator=False,
            hmac_key_base64=str(bundle.get('hmac_key_base64') or ''),
        )
    except (OSError, ValueError) as exc:
        raise PairingError(
            '부분 완료 · 중앙 PC에는 설정이 저장됐지만 참여 PC 저장에 실패했습니다. '
            f'새 코드를 생성해 다시 연결하세요: {exc}'
        ) from exc
    return {
        'success': True,
        'paired': True,
        'machine_id': participant_id,
        'coordinator_machine_id': coordinator_id,
        'coordinator_ip': coordinator_ip,
        'central_restart': response.get('central_restart') or {},
        'message': 'PC 연동 설정과 암호화 키 저장 완료',
    }


def local_ipv4_for_peer(peer_ip: str) -> str:
    """Resolve the local IPv4 route selected for one private peer."""
    peer = _private_ipv4(peer_ip, '상대 PC IP')
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect((peer, 9))
        return _private_ipv4(sock.getsockname()[0], '이 PC IP')
    except OSError as exc:
        raise PairingError(f'상대 PC로 사용할 로컬 IP를 확인할 수 없습니다: {exc}') from exc
    finally:
        sock.close()


def _request_json(
    opener: Callable[..., Any], url: str, payload: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    data = None
    headers = {}
    if payload is not None:
        data = canonical_json(payload)
        headers['Content-Type'] = 'application/json'
    request = urllib.request.Request(
        url, data=data, headers=headers,
        method='POST' if payload is not None else 'GET',
    )
    try:
        with opener(request, timeout=5.0) as response:
            body = response.read(MAX_PAIRING_BODY_BYTES)
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read(MAX_PAIRING_BODY_BYTES).decode('utf-8'))
            message = str(detail.get('detail') or detail.get('message') or '')
        except (ValueError, UnicodeError):
            message = ''
        raise PairingError(message or f'중앙 PC 연동 요청 실패 · HTTP {exc.code}') from exc
    except (OSError, urllib.error.URLError) as exc:
        if payload is not None:
            raise PairingError(
                '연동 응답을 확인하지 못했습니다. 중앙 PC에는 설정이 저장됐을 수 '
                f'있으므로 새 코드를 생성해 다시 연결하세요: {exc}'
            ) from exc
        raise PairingError(f'중앙 PC에 연결할 수 없습니다: {exc}') from exc
    try:
        value = json.loads(body.decode('utf-8'))
    except (ValueError, UnicodeError) as exc:
        raise PairingError('중앙 PC 연동 응답 형식이 올바르지 않습니다') from exc
    if not isinstance(value, dict):
        raise PairingError('중앙 PC 연동 응답은 객체여야 합니다')
    return value


def _coordinator_url(value: str) -> tuple[str, str]:
    clean = str(value or '').strip().rstrip('/')
    if '://' not in clean:
        clean = f'http://{clean}'
    parsed = urlparse(clean)
    if parsed.scheme != 'http' or not parsed.hostname:
        raise PairingError('중앙 PC 주소는 내부망 IPv4 주소여야 합니다')
    if parsed.username or parsed.password:
        raise PairingError('중앙 PC 주소에 사용자 정보를 넣을 수 없습니다')
    if parsed.path not in {'', '/'} or parsed.query or parsed.fragment:
        raise PairingError('중앙 PC 주소에는 경로를 넣을 수 없습니다')
    ip = _private_ipv4(parsed.hostname, '중앙 PC IP')
    try:
        port = parsed.port or 8000
    except ValueError as exc:
        raise PairingError('중앙 PC 웹 포트 형식이 올바르지 않습니다') from exc
    if port != 8000:
        raise PairingError('중앙 PC 웹 포트는 8000이어야 합니다')
    return ip, f'http://{ip}:8000'


def _transcript(
    session_id: str,
    coordinator_id: str,
    participant_id: str,
    coordinator_public: str,
    participant_public: str,
) -> bytes:
    return canonical_json({
        'pairing_version': PAIRING_VERSION,
        'session_id': str(session_id),
        'coordinator_machine_id': str(coordinator_id),
        'participant_machine_id': str(participant_id),
        'coordinator_public_key': str(coordinator_public),
        'participant_public_key': str(participant_public),
    })


def _code_key(code: str, session_id: str) -> bytes:
    return PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=str(session_id).encode('utf-8'),
        iterations=PAIRING_PBKDF2_ITERATIONS,
    ).derive(_pairing_code(code).encode('ascii'))


def _encryption_key(shared: bytes, code_key: bytes, transcript: bytes) -> bytes:
    return HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=code_key,
        info=b'motion-coordination-pairing-v1\0' + hashlib.sha256(transcript).digest(),
    ).derive(shared)


def _pairing_code(value: str) -> str:
    clean = ''.join(character for character in str(value or '').upper() if character.isalnum())
    if (
        len(clean) != PAIRING_CODE_LENGTH
        or any(character not in PAIRING_CODE_ALPHABET for character in clean)
    ):
        raise PairingError('연동 코드는 8자리 영문·숫자 코드여야 합니다')
    return clean


def _machine_id(value: Any) -> str:
    clean = str(value or '').strip()
    if (
        not clean
        or len(clean) > 128
        or not clean[0].isalnum()
        or not all(character.isascii() and (
            character.isalnum() or character in '_.:-'
        ) for character in clean)
    ):
        raise PairingError('PC ID는 영문·숫자와 _ . : -만 사용할 수 있습니다')
    return clean


def _private_ipv4(value: str, field: str) -> str:
    try:
        address = ipaddress.ip_address(str(value or '').strip())
    except ValueError as exc:
        raise PairingError(f'{field} 형식이 올바르지 않습니다') from exc
    if not is_internal_ipv4(address):
        raise PairingError(f'{field}는 내부망 IPv4 주소여야 합니다')
    return address.compressed


def _public_key_text(key: x25519.X25519PublicKey) -> str:
    return _encode(key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    ))


def _load_public_key(value: str) -> x25519.X25519PublicKey:
    raw = _decode(value, '공개키')
    if len(raw) != 32:
        raise PairingError('공개키 길이가 올바르지 않습니다')
    try:
        return x25519.X25519PublicKey.from_public_bytes(raw)
    except ValueError as exc:
        raise PairingError('공개키 형식이 올바르지 않습니다') from exc


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode('ascii').rstrip('=')


def _decode(value: str, field: str) -> bytes:
    clean = str(value or '').strip()
    try:
        return base64.b64decode(
            clean + ('=' * (-len(clean) % 4)),
            altchars=b'-_',
            validate=True,
        )
    except (ValueError, binascii.Error) as exc:
        raise PairingError(f'{field} 형식이 올바르지 않습니다') from exc
