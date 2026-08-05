"""Network boundary policy for user web and PC coordination endpoints."""

import ipaddress
from dataclasses import dataclass
from typing import Any, Mapping, Tuple


DEFAULT_WEB_PORT = 8000
DEFAULT_COORDINATION_PORT = 8010
COORDINATION_PATH_PREFIX = '/coordination/v1/'
_INTERNAL_IPV4_NETWORKS = tuple(
    ipaddress.ip_network(value) for value in (
        '10.0.0.0/8',
        '100.64.0.0/10',
        '169.254.0.0/16',
        '172.16.0.0/12',
        '192.168.0.0/16',
    )
)


class AccessPolicyError(ValueError):
    """Raised when endpoint settings weaken the required network boundary."""


@dataclass(frozen=True)
class FirewallRule:
    """Declarative TCP allow rule for the future service installer."""

    source_network: str
    destination_ip: str
    destination_port: int
    protocol: str = 'tcp'


@dataclass(frozen=True)
class AccessPolicy:
    """Validated deployment boundary without opening a network listener."""

    web_host: str = '0.0.0.0'
    web_port: int = DEFAULT_WEB_PORT
    coordination_enabled: bool = False
    coordination_host: str = ''
    coordination_port: int = DEFAULT_COORDINATION_PORT
    allowed_peer_networks: Tuple[str, ...] = ()

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> 'AccessPolicy':
        """Build a policy from global, project-independent settings."""
        if not isinstance(value, Mapping):
            raise AccessPolicyError('네트워크 접근정책은 객체여야 합니다')
        web = value.get('web')
        coordination = value.get('coordination')
        web = web if isinstance(web, Mapping) else {}
        coordination = coordination if isinstance(coordination, Mapping) else {}
        networks = coordination.get('allowed_peer_networks', ())
        if not isinstance(networks, (list, tuple)):
            raise AccessPolicyError('allowed_peer_networks는 목록이어야 합니다')
        policy = cls(
            web_host=str(web.get('host') or '0.0.0.0').strip(),
            web_port=_port(web.get('port'), DEFAULT_WEB_PORT, 'web.port'),
            coordination_enabled=coordination.get('enabled') is True,
            coordination_host=str(coordination.get('host') or '').strip(),
            coordination_port=_port(
                coordination.get('port'),
                DEFAULT_COORDINATION_PORT,
                'coordination.port',
            ),
            allowed_peer_networks=tuple(str(item).strip() for item in networks),
        )
        policy.validate()
        return policy

    def validate(self) -> None:
        """Reject shared ports, wildcard peer listeners and broad networks."""
        _port(self.web_port, DEFAULT_WEB_PORT, 'web.port')
        _port(
            self.coordination_port,
            DEFAULT_COORDINATION_PORT,
            'coordination.port',
        )
        if self.web_port == self.coordination_port:
            raise AccessPolicyError('사용자 웹과 PC 연동 포트는 달라야 합니다')
        if not self.coordination_enabled:
            return
        host = _coordination_address(self.coordination_host)
        if not self.allowed_peer_networks:
            raise AccessPolicyError('연동 사용 시 허용 PC 네트워크가 필요합니다')
        networks = tuple(_peer_network(item) for item in self.allowed_peer_networks)
        if not any(host in network for network in networks):
            raise AccessPolicyError(
                '연동 수신 IP가 허용 PC 네트워크에 포함되어야 합니다'
            )

    def allows_peer(self, remote_ip: str) -> bool:
        """Return whether a remote address is allowed by the coordination boundary."""
        if not self.coordination_enabled:
            return False
        try:
            address = ipaddress.ip_address(str(remote_ip).strip())
            networks = tuple(
                _peer_network(item) for item in self.allowed_peer_networks
            )
        except (AccessPolicyError, ValueError):
            return False
        return any(address in network for network in networks)

    def coordination_firewall_rules(self) -> Tuple[FirewallRule, ...]:
        """Return narrow rules without modifying the operating-system firewall."""
        self.validate()
        if not self.coordination_enabled:
            return ()
        host = _coordination_address(self.coordination_host)
        return tuple(
            FirewallRule(
                source_network=_peer_network(item).with_prefixlen,
                destination_ip=host.compressed,
                destination_port=self.coordination_port,
            )
            for item in self.allowed_peer_networks
        )

    @staticmethod
    def validate_coordination_path(path: str) -> str:
        """Keep coordination routes off the existing user-web API namespace."""
        clean = str(path or '').strip()
        if not clean.startswith(COORDINATION_PATH_PREFIX):
            raise AccessPolicyError(
                f'PC 연동 경로는 {COORDINATION_PATH_PREFIX} 아래에 있어야 합니다'
            )
        return clean


def _port(value: Any, default: int, field: str) -> int:
    if value in (None, ''):
        return default
    if isinstance(value, bool):
        raise AccessPolicyError(f'{field}는 1~65535 정수여야 합니다')
    try:
        port = int(value)
    except (TypeError, ValueError) as exc:
        raise AccessPolicyError(f'{field}는 1~65535 정수여야 합니다') from exc
    if not 1 <= port <= 65535:
        raise AccessPolicyError(f'{field}는 1~65535 정수여야 합니다')
    return port


def _coordination_address(value: str) -> Any:
    try:
        address = ipaddress.ip_address(value)
    except ValueError as exc:
        raise AccessPolicyError('연동 수신 IP는 명시적인 내부망 IP여야 합니다') from exc
    if address.is_unspecified or address.is_loopback or address.is_multicast:
        raise AccessPolicyError('연동 수신 IP에 wildcard·loopback을 사용할 수 없습니다')
    if address.version != 4 or not is_internal_ipv4(address):
        raise AccessPolicyError('연동 수신 IP는 내부망 주소여야 합니다')
    return address


def _peer_network(value: str) -> Any:
    try:
        network = ipaddress.ip_network(value, strict=False)
    except ValueError as exc:
        raise AccessPolicyError(f'허용 PC 네트워크 형식이 잘못됐습니다: {value}') from exc
    if network.prefixlen == 0:
        raise AccessPolicyError('전체 인터넷을 허용 PC 네트워크로 사용할 수 없습니다')
    if network.version != 4 or not any(
        network.subnet_of(allowed) for allowed in _INTERNAL_IPV4_NETWORKS
    ):
        raise AccessPolicyError('허용 PC 네트워크는 내부망 범위여야 합니다')
    return network


def is_internal_ipv4(value: Any) -> bool:
    """Return whether an address is usable on an explicitly supported LAN."""
    try:
        address = value if isinstance(value, ipaddress.IPv4Address) else ipaddress.ip_address(value)
    except ValueError:
        return False
    return (
        address.version == 4
        and not address.is_unspecified
        and not address.is_loopback
        and not address.is_multicast
        and not address.is_reserved
        and any(address in network for network in _INTERNAL_IPV4_NETWORKS)
    )
