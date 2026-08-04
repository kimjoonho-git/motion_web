"""Authenticated status-sharing runtime independent from ROS and HTTP servers."""

import copy
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Mapping

from .configuration import CoordinationConfig
from .execution_control import validate_control_payload
from .peer_registry import PeerRegistry
from .protocol import build_envelope
from .security import (
    DuplicateOperationGuard,
    PeerRequestVerifier,
    SignedRequest,
    create_nonce,
    sign_request,
)
from .status_adapter import (
    adapt_status,
    validate_readiness_payload,
    validate_status_payload,
)


STATUS_PATH = '/coordination/v1/status'
STATUS_RESPONSE_PATH = '/coordination/v1/status/response'
READINESS_PATH = '/coordination/v1/readiness'
READINESS_RESPONSE_PATH = '/coordination/v1/readiness/response'
CONTROL_PATH = '/coordination/v1/control'
CONTROL_RESPONSE_PATH = '/coordination/v1/control/response'


class CoordinationRuntime:
    """Own message sequence, peer status, authentication and role conflicts."""

    def __init__(
        self,
        config: CoordinationConfig,
        peer_secrets: Mapping[str, bytes],
        *,
        wall_clock: Callable[[], float] = time.time,
    ) -> None:
        self.config = config
        self._secrets = dict(peer_secrets)
        self._wall_clock = wall_clock
        self._boot_id = f'boot-{uuid.uuid4().hex}'
        self._program_session_id = f'program-{uuid.uuid4().hex}'
        self._readiness_session_id = f'readiness-{uuid.uuid4().hex}'
        self._last_bridge_instance_id = ''
        self._last_project_generation = None
        self._sequence = 0
        self._sequence_lock = threading.Lock()
        self._local_payload = adapt_status(
            {},
            display_name=config.display_name,
            coordination_mode=config.mode,
            coordination_role=config.role,
            coordinator_machine_id=config.coordinator_machine_id,
            program_session_id=self._program_session_id,
            readiness_session_id=self._readiness_session_id,
        )
        self._payload_lock = threading.Lock()
        self.registry = PeerRegistry(config, clock=wall_clock)
        self._verifier = PeerRequestVerifier(
            self._secrets,
            wall_clock=wall_clock,
        )
        self._operation_guard = DuplicateOperationGuard()

    def update_local_status(self, local_status: Mapping[str, Any]) -> Dict[str, Any]:
        """Replace the local approved status payload."""
        with self._payload_lock:
            bridge_id = str(local_status.get('bridge_instance_id') or '')
            generation = local_status.get('project_generation')
            if bridge_id and bridge_id != self._last_bridge_instance_id:
                self._program_session_id = f'program-{uuid.uuid4().hex}'
                self._readiness_session_id = f'readiness-{uuid.uuid4().hex}'
                self._last_bridge_instance_id = bridge_id
                self._last_project_generation = generation
            elif (
                generation is not None
                and self._last_project_generation is not None
                and generation != self._last_project_generation
            ):
                self._readiness_session_id = f'readiness-{uuid.uuid4().hex}'
                self._last_project_generation = generation
            elif generation is not None:
                self._last_project_generation = generation
            payload = adapt_status(
                local_status,
                display_name=self.config.display_name,
                coordination_mode=self.config.mode,
                coordination_role=self.config.role,
                coordinator_machine_id=self.config.coordinator_machine_id,
                program_session_id=self._program_session_id,
                readiness_session_id=self._readiness_session_id,
            )
            self._local_payload = payload
        return payload

    def build_status_request(self, peer_id: str) -> tuple[int, SignedRequest]:
        """Build one signed status request for a registered peer."""
        if self.config.mode == 'off':
            raise ValueError('연동 끔 상태에서는 상태를 전송하지 않습니다')
        secret = self._peer_secret(peer_id)
        sequence = self._next_sequence()
        with self._payload_lock:
            payload = dict(self._local_payload)
        envelope = self._envelope('status', sequence, payload)
        return sequence, sign_request(
            secret,
            method='POST',
            path=STATUS_PATH,
            envelope=envelope,
            nonce=create_nonce(),
        )

    def accept_status_request(
        self,
        *,
        body: bytes,
        headers: Mapping[str, str],
        remote_ip: str,
    ) -> SignedRequest:
        """Authenticate, store and acknowledge one peer status request."""
        if self.config.mode == 'off':
            raise ValueError('연동 끔 상태에서는 상태를 수신하지 않습니다')
        if not self.config.access.allows_peer(remote_ip):
            raise ValueError('허용되지 않은 원격 IP입니다')
        envelope = self._verifier.verify(
            method='POST',
            path=STATUS_PATH,
            body=body,
            headers=headers,
        )
        if envelope.get('message_type') != 'status':
            raise ValueError('status 메시지만 이 경로에서 수신할 수 있습니다')
        sender = envelope['sender']
        machine_id = str(sender['machine_id'])
        self.registry.accept(
            machine_id,
            str(sender['coordination_boot_id']),
            int(envelope['sequence']),
            validate_status_payload(envelope['payload']),
            remote_ip,
        )
        ack = self._envelope(
            'status_ack',
            self._next_sequence(),
            {'accepted_sequence': int(envelope['sequence'])},
        )
        return sign_request(
            self._peer_secret(machine_id),
            method='POST',
            path=STATUS_RESPONSE_PATH,
            envelope=ack,
            nonce=create_nonce(),
        )

    def verify_status_response(
        self,
        peer_id: str,
        request_sequence: int,
        body: bytes,
        headers: Mapping[str, str],
    ) -> Dict[str, Any]:
        """Verify one signed peer acknowledgement."""
        envelope = self._verifier.verify(
            method='POST',
            path=STATUS_RESPONSE_PATH,
            body=body,
            headers=headers,
        )
        if envelope['sender']['machine_id'] != peer_id:
            raise ValueError('응답 peer ID가 요청 대상과 다릅니다')
        if envelope.get('message_type') != 'status_ack':
            raise ValueError('status_ack 응답이 아닙니다')
        if envelope.get('payload', {}).get('accepted_sequence') != request_sequence:
            raise ValueError('응답 sequence가 요청과 다릅니다')
        return envelope

    def build_readiness_request(
        self,
        peer_id: str,
        operation_id: str,
    ) -> tuple[int, SignedRequest]:
        """Build a coordinator request that asks one peer for local readiness."""
        self._require_coordinator_authority()
        secret = self._peer_secret(peer_id)
        sequence = self._next_sequence()
        envelope = self._envelope(
            'readiness_request',
            sequence,
            {'network_operation_id': str(operation_id or '').strip()},
        )
        return sequence, sign_request(
            secret,
            method='POST',
            path=READINESS_PATH,
            envelope=envelope,
            nonce=create_nonce(),
        )

    def begin_readiness_operation(self, operation_id: str) -> str:
        """Validate coordinator authority and reserve one operation ID."""
        self._require_coordinator_authority()
        clean = str(operation_id or '').strip()
        self._operation_guard.accept(self.config.machine_id, clean)
        return clean

    def accept_readiness_request(
        self,
        *,
        body: bytes,
        headers: Mapping[str, str],
        remote_ip: str,
    ) -> Dict[str, Any]:
        """Authenticate one coordinator readiness request without executing it."""
        if self.config.mode != 'participant' or self.config.role != 'peer':
            raise ValueError('연동 참여 peer만 준비 확인 요청을 수신합니다')
        if not self.config.access.allows_peer(remote_ip):
            raise ValueError('허용되지 않은 원격 IP입니다')
        envelope = self._verifier.verify(
            method='POST',
            path=READINESS_PATH,
            body=body,
            headers=headers,
        )
        if envelope.get('message_type') != 'readiness_request':
            raise ValueError('readiness_request 메시지가 아닙니다')
        machine_id = str(envelope['sender']['machine_id'])
        if machine_id != self.config.coordinator_machine_id:
            raise ValueError('지정된 중앙 PC의 요청이 아닙니다')
        coordinator = self.registry.snapshot()['coordinator']
        if not coordinator.get('authority_allowed'):
            raise ValueError('중앙 PC 상태가 활성 상태가 아닙니다')
        operation_id = str(
            envelope.get('payload', {}).get('network_operation_id') or ''
        ).strip()
        self._operation_guard.accept(machine_id, operation_id)
        return {
            'machine_id': machine_id,
            'request_sequence': int(envelope['sequence']),
            'network_operation_id': operation_id,
        }

    def build_readiness_response(
        self,
        peer_id: str,
        request_sequence: int,
        operation_id: str,
        readiness: Mapping[str, Any],
    ) -> SignedRequest:
        """Build a signed, project-neutral readiness response."""
        safe_readiness = dict(readiness)
        with self._payload_lock:
            safe_readiness['readiness_session_id'] = self._readiness_session_id
        payload = {
            'accepted_sequence': int(request_sequence),
            'network_operation_id': str(operation_id),
            'readiness': validate_readiness_payload(safe_readiness),
        }
        return sign_request(
            self._peer_secret(peer_id),
            method='POST',
            path=READINESS_RESPONSE_PATH,
            envelope=self._envelope(
                'readiness_response', self._next_sequence(), payload
            ),
            nonce=create_nonce(),
        )

    def verify_readiness_response(
        self,
        peer_id: str,
        request_sequence: int,
        operation_id: str,
        body: bytes,
        headers: Mapping[str, str],
    ) -> Dict[str, Any]:
        """Verify one peer response and return only its safe readiness payload."""
        envelope = self._verifier.verify(
            method='POST',
            path=READINESS_RESPONSE_PATH,
            body=body,
            headers=headers,
        )
        if envelope['sender']['machine_id'] != peer_id:
            raise ValueError('준비 응답 peer ID가 요청 대상과 다릅니다')
        if envelope.get('message_type') != 'readiness_response':
            raise ValueError('readiness_response 응답이 아닙니다')
        payload = envelope.get('payload') or {}
        if payload.get('accepted_sequence') != request_sequence:
            raise ValueError('준비 응답 sequence가 요청과 다릅니다')
        if payload.get('network_operation_id') != operation_id:
            raise ValueError('준비 응답 operation ID가 요청과 다릅니다')
        readiness = validate_readiness_payload(payload.get('readiness'))
        peer = next(
            (
                record for record in self.registry.snapshot()['peers']
                if record.get('machine_id') == peer_id
            ),
            None,
        )
        expected_session = (
            peer.get('payload', {}).get('session', {}).get('readiness_session_id')
            if isinstance(peer, dict) else ''
        )
        if not expected_session or readiness.get('readiness_session_id') != expected_session:
            raise ValueError('프로젝트 전환 전 준비 응답을 폐기했습니다')
        return readiness

    def attach_local_readiness_session(
        self, readiness: Mapping[str, Any]
    ) -> Dict[str, Any]:
        """Attach the current opaque local project boundary to a UI result."""
        result = dict(readiness)
        with self._payload_lock:
            result['readiness_session_id'] = self._readiness_session_id
        return validate_readiness_payload(result)

    def build_control_request(
        self, peer_id: str, payload: Mapping[str, Any]
    ) -> tuple[int, SignedRequest]:
        """Build one authenticated project-neutral high-level control request."""
        self._require_coordinator_authority()
        sequence = self._next_sequence()
        safe = validate_control_payload(payload)
        return sequence, sign_request(
            self._peer_secret(peer_id), method='POST', path=CONTROL_PATH,
            envelope=self._envelope('control_request', sequence, safe),
            nonce=create_nonce(),
        )

    def accept_control_request(
        self, *, body: bytes, headers: Mapping[str, str], remote_ip: str,
    ) -> Dict[str, Any]:
        """Authenticate a command from the configured active coordinator."""
        if self.config.mode != 'participant' or self.config.role != 'peer':
            raise ValueError('연동 참여 peer만 실행 명령을 수신합니다')
        if not self.config.access.allows_peer(remote_ip):
            raise ValueError('허용되지 않은 원격 IP입니다')
        envelope = self._verifier.verify(
            method='POST', path=CONTROL_PATH, body=body, headers=headers
        )
        if envelope.get('message_type') != 'control_request':
            raise ValueError('control_request 메시지가 아닙니다')
        machine_id = str(envelope['sender']['machine_id'])
        if machine_id != self.config.coordinator_machine_id:
            raise ValueError('지정된 중앙 PC의 요청이 아닙니다')
        if not self.registry.snapshot()['coordinator'].get('authority_allowed'):
            raise ValueError('중앙 PC 상태가 활성 상태가 아닙니다')
        return {
            'machine_id': machine_id,
            'request_sequence': int(envelope['sequence']),
            'payload': validate_control_payload(envelope.get('payload')),
        }

    def build_control_response(
        self, peer_id: str, request_sequence: int,
        operation_id: str, result: Mapping[str, Any],
    ) -> SignedRequest:
        safe_result = {
            'success': bool(result.get('success')),
            'state': str(
                result.get('state')
                or ('accepted' if result.get('success') else 'rejected')
            )[:64],
            'message': str(result.get('message') or '')[:256],
        }
        for field in ('requested_start_at', 'actual_start_at', 'start_error_ms'):
            if result.get(field) is not None:
                safe_result[field] = float(result[field])
        payload = {
            'accepted_sequence': int(request_sequence),
            'network_operation_id': str(operation_id),
            'result': safe_result,
        }
        return sign_request(
            self._peer_secret(peer_id), method='POST', path=CONTROL_RESPONSE_PATH,
            envelope=self._envelope('control_response', self._next_sequence(), payload),
            nonce=create_nonce(),
        )

    def verify_control_response(
        self, peer_id: str, request_sequence: int, operation_id: str,
        body: bytes, headers: Mapping[str, str],
    ) -> Dict[str, Any]:
        envelope = self._verifier.verify(
            method='POST', path=CONTROL_RESPONSE_PATH, body=body, headers=headers
        )
        if envelope['sender']['machine_id'] != peer_id:
            raise ValueError('실행 응답 peer ID가 요청 대상과 다릅니다')
        if envelope.get('message_type') != 'control_response':
            raise ValueError('control_response 응답이 아닙니다')
        payload = envelope.get('payload') or {}
        if payload.get('accepted_sequence') != request_sequence:
            raise ValueError('실행 응답 sequence가 요청과 다릅니다')
        if payload.get('network_operation_id') != operation_id:
            raise ValueError('실행 응답 operation ID가 요청과 다릅니다')
        result = payload.get('result')
        if not isinstance(result, dict) or not isinstance(result.get('success'), bool):
            raise ValueError('실행 응답 형식이 올바르지 않습니다')
        return dict(result)

    def snapshot(self) -> Dict[str, Any]:
        """Return local mode, boot session and live peer state."""
        registry = self.registry.snapshot()
        return {
            'enabled': self.config.mode != 'off',
            'mode': self.config.mode,
            'role': self.config.role,
            'machine_id': self.config.machine_id,
            'coordination_boot_id': self._boot_id,
            'coordinator': registry['coordinator'],
            # Control still requires an authenticated active coordinator.
            'remote_control_enabled': self.config.mode == 'participant',
            'local': copy.deepcopy(self._local_payload),
            'peers': registry['peers'],
        }

    def _envelope(self, message_type: str, sequence: int, payload: Mapping[str, Any]):
        return build_envelope(
            message_type=message_type,
            machine_id=self.config.machine_id,
            coordination_boot_id=self._boot_id,
            sequence=sequence,
            sent_at=datetime.fromtimestamp(
                self._wall_clock(), timezone.utc
            ).isoformat(timespec='milliseconds').replace('+00:00', 'Z'),
            payload=payload,
        )

    def _next_sequence(self) -> int:
        with self._sequence_lock:
            sequence = self._sequence
            self._sequence += 1
            return sequence

    def _peer_secret(self, peer_id: str) -> bytes:
        if peer_id not in {peer.machine_id for peer in self.config.peers}:
            raise ValueError('등록되지 않은 peer입니다')
        secret = self._secrets.get(peer_id)
        if secret is None:
            raise ValueError('peer HMAC 키가 없습니다')
        return secret

    def _require_coordinator_authority(self) -> None:
        if self.config.mode != 'participant' or self.config.role != 'coordinator':
            raise ValueError('연동 참여 중앙 PC만 전체 준비 확인을 요청할 수 있습니다')
        coordinator = self.registry.snapshot()['coordinator']
        if not coordinator.get('authority_allowed'):
            raise ValueError('중앙 PC 권한이 활성 상태가 아닙니다')
