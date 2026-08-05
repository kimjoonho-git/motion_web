import pytest

from motion_coordination.access_policy import AccessPolicy, AccessPolicyError


def _enabled(**coordination):
    value = {
        'web': {'host': '0.0.0.0', 'port': 8000},
        'coordination': {
            'enabled': True,
            'host': '192.168.10.20',
            'port': 8010,
            'allowed_peer_networks': ['192.168.10.0/24'],
        },
    }
    value['coordination'].update(coordination)
    return value


def test_disabled_policy_reserves_a_separate_port_without_allowing_peers():
    policy = AccessPolicy.from_mapping({})

    assert policy.web_port == 8000
    assert policy.coordination_port == 8010
    assert policy.coordination_enabled is False
    assert policy.allows_peer('192.168.10.5') is False


def test_enabled_policy_requires_and_applies_internal_network_allowlist():
    policy = AccessPolicy.from_mapping(_enabled())

    assert policy.allows_peer('192.168.10.5') is True
    assert policy.allows_peer('192.168.11.5') is False
    assert policy.allows_peer('not-an-ip') is False


def test_enabled_policy_builds_only_narrow_declarative_firewall_rules():
    policy = AccessPolicy.from_mapping(_enabled())

    rules = policy.coordination_firewall_rules()

    assert len(rules) == 1
    assert rules[0].source_network == '192.168.10.0/24'
    assert rules[0].destination_ip == '192.168.10.20'
    assert rules[0].destination_port == 8010
    assert rules[0].protocol == 'tcp'


def test_disabled_policy_has_no_coordination_firewall_rules():
    assert AccessPolicy.from_mapping({}).coordination_firewall_rules() == ()


@pytest.mark.parametrize(
    ('change', 'error'),
    [
        ({'port': 8000}, '포트는 달라야'),
        ({'host': '0.0.0.0'}, 'wildcard'),
        ({'host': '127.0.0.1'}, 'loopback'),
        ({'host': '8.8.8.8'}, '내부망 주소'),
        ({'allowed_peer_networks': []}, '허용 PC 네트워크'),
        ({'allowed_peer_networks': ['0.0.0.0/0']}, '전체 인터넷'),
        (
            {'allowed_peer_networks': ['192.168.20.0/24']},
            '수신 IP가 허용 PC 네트워크',
        ),
    ],
)
def test_unsafe_coordination_boundary_is_rejected(change, error):
    with pytest.raises(AccessPolicyError, match=error):
        AccessPolicy.from_mapping(_enabled(**change))


def test_coordination_routes_have_a_separate_namespace():
    assert (
        AccessPolicy.validate_coordination_path('/coordination/v1/status')
        == '/coordination/v1/status'
    )
    with pytest.raises(AccessPolicyError, match='/coordination/v1/'):
        AccessPolicy.validate_coordination_path('/api/status')


@pytest.mark.parametrize('host', ['0.0.0.0', '127.0.0.1', '240.0.0.1'])
def test_internal_address_policy_rejects_unusable_ipv4(host):
    with pytest.raises(AccessPolicyError):
        AccessPolicy.from_mapping(_enabled(host=host))


def test_internal_address_policy_accepts_cgnat_for_managed_private_lans():
    policy = AccessPolicy.from_mapping(_enabled(
        host='100.64.0.10', allowed_peer_networks=['100.64.0.0/24']
    ))

    assert policy.allows_peer('100.64.0.20') is True
