import json

import pytest
import yaml

from motion_coordination.pairing import (
    PairingCoordinator,
    PairingError,
    join_pairing,
    _coordinator_url,
)


class _Response:
    def __init__(self, value):
        self.body = json.dumps(value).encode('utf-8')

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, limit):
        return self.body[:limit]


def _pair(tmp_path, *, participant_code=None):
    central_root = tmp_path / 'central'
    participant_root = tmp_path / 'participant'
    central = PairingCoordinator(
        central_root,
        central_root / 'config/motion_coordination.yaml',
        local_ip_resolver=lambda _peer: '192.168.10.10',
    )
    offer = central.start('pc-a', 'PC A')
    requests = []

    def opener(request, timeout):
        requests.append((request.method, request.full_url, request.data))
        if request.method == 'GET':
            return _Response(central.info())
        return _Response(central.claim(
            json.loads(request.data.decode('utf-8')),
            '192.168.10.20',
        ))

    result = join_pairing(
        '192.168.10.10',
        participant_code or offer['pairing_code'],
        'pc-b',
        'PC B',
        workspace=participant_root,
        config_path=participant_root / 'config/motion_coordination.yaml',
        opener=opener,
        local_ip_resolver=lambda _peer: '192.168.10.20',
    )
    return central_root, participant_root, central, offer, result, requests


def test_encrypted_pairing_saves_matching_two_pc_configuration(tmp_path):
    central_root, participant_root, central, offer, result, requests = _pair(
        tmp_path
    )

    central_config = yaml.safe_load(
        (central_root / 'config/motion_coordination.yaml').read_text()
    )
    participant_config = yaml.safe_load(
        (participant_root / 'config/motion_coordination.yaml').read_text()
    )
    central_credentials = yaml.safe_load(
        (central_root / 'config/motion_coordination.credentials.yaml').read_text()
    )
    participant_credentials = yaml.safe_load(
        (participant_root / 'config/motion_coordination.credentials.yaml').read_text()
    )

    assert result['success'] is True
    assert central.status()['state'] == 'paired'
    assert central_config['role'] == 'coordinator'
    assert participant_config['role'] == 'peer'
    assert central_config['peers'] == [{
        'machine_id': 'pc-b', 'url': 'http://192.168.10.20:8010',
    }]
    assert participant_config['peers'] == [{
        'machine_id': 'pc-a', 'url': 'http://192.168.10.10:8010',
    }]
    assert central_credentials['peers']['pc-b'] == (
        participant_credentials['peers']['pc-a']
    )
    assert offer['pairing_code'].replace('-', '').encode() not in requests[1][2]
    for root in (central_root, participant_root):
        assert (
            root / 'config/motion_coordination.yaml'
        ).stat().st_mode & 0o777 == 0o600
        assert (
            root / 'config/motion_coordination.credentials.yaml'
        ).stat().st_mode & 0o777 == 0o600


def test_public_pairing_info_never_contains_the_user_code(tmp_path):
    central = PairingCoordinator(
        tmp_path,
        tmp_path / 'config/motion_coordination.yaml',
    )
    offer = central.start('pc-a', 'PC A')

    assert 'pairing_code' not in central.info()
    assert offer['pairing_code'].replace('-', '') not in str(central.info())


def test_wrong_pairing_code_does_not_create_participant_files(tmp_path):
    with pytest.raises(PairingError, match='연동 코드'):
        _pair(tmp_path, participant_code='AAAA-AAAA')

    assert not (tmp_path / 'participant/config/motion_coordination.yaml').exists()


def test_pairing_offer_expires_without_becoming_reusable(tmp_path):
    now = [100.0]
    central = PairingCoordinator(
        tmp_path,
        tmp_path / 'config/motion_coordination.yaml',
        wall_clock=lambda: now[0],
        monotonic_clock=lambda: now[0],
    )
    central.start('pc-a')
    now[0] = 401.0

    assert central.status() == {'state': 'expired'}
    with pytest.raises(PairingError, match='만료'):
        central.info()


def test_pairing_preserves_an_existing_peer_and_credential(tmp_path):
    central_root = tmp_path / 'central'
    config_dir = central_root / 'config'
    config_dir.mkdir(parents=True)
    (config_dir / 'motion_coordination.yaml').write_text(
        'version: 1\n'
        'machine_id: pc-a\n'
        'display_name: PC A\n'
        'mode: status\n'
        'role: coordinator\n'
        'coordinator_machine_id: pc-a\n'
        'peers:\n'
        '  - machine_id: pc-old\n'
        '    url: http://192.168.10.30:8010\n'
        'access:\n'
        '  web: {host: 0.0.0.0, port: 8000}\n'
        '  coordination:\n'
        '    enabled: true\n'
        '    host: 192.168.10.10\n'
        '    port: 8010\n'
        '    allowed_peer_networks: [192.168.10.10/32, 192.168.10.30/32]\n'
        'credential_file: config/motion_coordination.credentials.yaml\n',
        encoding='utf-8',
    )
    (config_dir / 'motion_coordination.credentials.yaml').write_text(
        'version: 1\npeers:\n  pc-old:\n'
        '    hmac_key_base64: MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=\n',
        encoding='utf-8',
    )
    central = PairingCoordinator(
        central_root,
        config_dir / 'motion_coordination.yaml',
        local_ip_resolver=lambda _peer: '192.168.10.10',
    )
    offer = central.start('pc-a', 'PC A')

    def opener(request, timeout):
        if request.method == 'GET':
            return _Response(central.info())
        return _Response(central.claim(
            json.loads(request.data.decode('utf-8')), '192.168.10.20'
        ))

    participant_root = tmp_path / 'participant'
    join_pairing(
        '192.168.10.10', offer['pairing_code'], 'pc-b', 'PC B',
        workspace=participant_root,
        config_path=participant_root / 'config/motion_coordination.yaml',
        opener=opener,
        local_ip_resolver=lambda _peer: '192.168.10.20',
    )

    config = yaml.safe_load(
        (config_dir / 'motion_coordination.yaml').read_text(encoding='utf-8')
    )
    credentials = yaml.safe_load(
        (config_dir / 'motion_coordination.credentials.yaml').read_text(
            encoding='utf-8'
        )
    )
    assert {peer['machine_id'] for peer in config['peers']} == {'pc-old', 'pc-b'}
    assert set(credentials['peers']) == {'pc-old', 'pc-b'}


def test_pairing_rejects_same_machine_id_without_writing_participant(tmp_path):
    central_root = tmp_path / 'central'
    central = PairingCoordinator(
        central_root,
        central_root / 'config/motion_coordination.yaml',
        local_ip_resolver=lambda _peer: '192.168.10.10',
    )
    offer = central.start('pc-a')

    def opener(request, timeout):
        if request.method == 'GET':
            return _Response(central.info())
        return _Response(central.claim(
            json.loads(request.data.decode('utf-8')), '192.168.10.20'
        ))

    participant_root = tmp_path / 'participant'
    with pytest.raises(PairingError, match='서로 달라야'):
        join_pairing(
            '192.168.10.10', offer['pairing_code'], 'pc-a', 'PC A duplicate',
            workspace=participant_root,
            config_path=participant_root / 'config/motion_coordination.yaml',
            opener=opener,
        )
    assert not participant_root.exists()


@pytest.mark.parametrize(
    ('value', 'message'),
    [
        ('0.0.0.0', '내부망'),
        ('240.0.0.1', '내부망'),
        ('192.168.10.10:wrong', '포트 형식'),
        ('http://user@192.168.10.10', '사용자 정보'),
    ],
)
def test_pairing_rejects_unusable_or_ambiguous_coordinator_addresses(
    value, message,
):
    with pytest.raises(PairingError, match=message):
        _coordinator_url(value)
