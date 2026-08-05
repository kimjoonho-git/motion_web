"""Peer heartbeat registry and manual coordinator conflict detection."""

import copy
import threading
import time
from typing import Any, Callable, Dict, Mapping

from .configuration import CoordinationConfig
from .status_adapter import validate_status_payload


class PeerRegistry:
    """Store only validated, project-neutral peer status payloads."""

    def __init__(
        self,
        config: CoordinationConfig,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._config = config
        self._clock = clock
        self._peers: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()

    def accept(
        self,
        machine_id: str,
        boot_id: str,
        sequence: int,
        payload: Mapping[str, Any],
        remote_ip: str,
    ) -> Dict[str, Any]:
        """Record one authenticated status update."""
        if machine_id == self._config.machine_id:
            raise ValueError('자기 machine_id를 사용하는 원격 PC를 거부했습니다')
        allowed = {peer.machine_id for peer in self._config.peers}
        if machine_id not in allowed:
            raise ValueError('등록되지 않은 peer 상태입니다')
        record = {
            'machine_id': machine_id,
            'coordination_boot_id': boot_id,
            'sequence': int(sequence),
            'received_at': float(self._clock()),
            'remote_ip': str(remote_ip),
            'payload': validate_status_payload(payload),
        }
        with self._lock:
            self._peers[machine_id] = record
        return copy.deepcopy(record)

    def snapshot(self) -> Dict[str, Any]:
        """Return live peers and current manual coordinator state."""
        now = float(self._clock())
        with self._lock:
            live = {
                machine_id: copy.deepcopy(record)
                for machine_id, record in self._peers.items()
                if now - float(record['received_at']) <= self._config.peer_timeout_sec
            }
            self._peers = copy.deepcopy(live)
        return {
            'peers': list(live.values()),
            'coordinator': self._coordinator_status(live),
        }

    def _coordinator_status(self, peers: Mapping[str, Dict[str, Any]]) -> Dict[str, Any]:
        if self._config.mode == 'off':
            return {
                'state': 'off',
                'machine_id': '',
                'claims': [],
                'authority_allowed': False,
            }
        claims = set()
        if self._config.role == 'coordinator':
            claims.add(self._config.machine_id)
        for machine_id, record in peers.items():
            coordination = record['payload'].get('coordination') or {}
            if coordination.get('role') == 'coordinator':
                claims.add(machine_id)
        expected = self._config.coordinator_machine_id
        conflict = len(claims) > 1 or bool(
            claims and expected and expected not in claims
        )
        if conflict:
            state = 'conflict'
            machine_id = ''
        elif len(claims) == 1:
            state = 'active'
            machine_id = next(iter(claims))
        else:
            state = 'waiting'
            machine_id = expected
        return {
            'state': state,
            'machine_id': machine_id,
            'claims': sorted(claims),
            'authority_allowed': state == 'active' and machine_id == expected,
        }
