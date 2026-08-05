import pytest
import yaml
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import motion_coordination.config_transaction as transaction_module

from motion_coordination.configuration import (
    ConfigurationError,
    configure_paired_peer,
    load_config,
    pairing_identity_state,
    update_local_selection,
)


def _write(tmp_path, text):
    path = tmp_path / 'coordination.yaml'
    path.write_text(text, encoding='utf-8')
    return path


def test_missing_config_starts_in_safe_off_mode(tmp_path):
    config = load_config(tmp_path / 'missing.yaml', workspace=tmp_path)

    assert config.mode == 'off'
    assert config.access.coordination_enabled is False
    assert config.peers == ()


def test_peer_status_mode_requires_registered_coordinator(tmp_path):
    path = _write(tmp_path, '''
version: 1
machine_id: pc-b
mode: status
role: peer
coordinator_machine_id: pc-a
peers:
  - machine_id: pc-a
    url: http://192.168.10.10:8010
credential_file: config/credentials.yaml
access:
  coordination:
    enabled: true
    host: 192.168.10.20
    allowed_peer_networks: [192.168.10.0/24]
''')

    config = load_config(path, workspace=tmp_path)

    assert config.mode == 'status'
    assert config.coordinator_machine_id == 'pc-a'
    assert config.peers[0].machine_id == 'pc-a'
    assert config.credential_file == tmp_path / 'config/credentials.yaml'


def test_coordinator_requires_explicit_private_listener(tmp_path):
    path = _write(tmp_path, '''
version: 1
machine_id: pc-a
mode: status
role: coordinator
access:
  coordination:
    enabled: false
''')

    with pytest.raises(ConfigurationError, match='8010 수신'):
        load_config(path, workspace=tmp_path)


def test_participant_mode_is_configuration_only_until_control_steps(tmp_path):
    path = _write(tmp_path, '''
version: 1
machine_id: pc-b
mode: participant
role: peer
coordinator_machine_id: pc-a
peers:
  - machine_id: pc-a
    url: http://192.168.10.10:8010
credential_file: credentials.yaml
access:
  coordination:
    enabled: true
    host: 192.168.10.20
    allowed_peer_networks: [192.168.10.0/24]
''')

    assert load_config(path, workspace=tmp_path).mode == 'participant'


def test_peer_url_cannot_use_existing_web_port(tmp_path):
    path = _write(tmp_path, '''
version: 1
machine_id: pc-b
mode: status
role: peer
coordinator_machine_id: pc-a
peers:
  - machine_id: pc-a
    url: http://192.168.10.10:8000
access: {}
''')

    with pytest.raises(ConfigurationError, match='8010'):
        load_config(path, workspace=tmp_path)


def test_active_mode_cannot_silently_disable_receiver(tmp_path):
    path = _write(tmp_path, '''
version: 1
machine_id: pc-b
mode: status
role: peer
coordinator_machine_id: pc-a
peers:
  - machine_id: pc-a
    url: http://192.168.10.10:8010
access:
  coordination:
    enabled: false
''')

    with pytest.raises(ConfigurationError, match='수신을 활성화'):
        load_config(path, workspace=tmp_path)


def test_machine_id_is_ascii_to_match_signed_protocol(tmp_path):
    path = _write(tmp_path, '''
version: 1
machine_id: 피씨-a
mode: off
access: {}
''')

    with pytest.raises(ConfigurationError, match='machine_id 형식'):
        load_config(path, workspace=tmp_path)


def test_peer_url_rejects_public_or_dns_hosts(tmp_path):
    for host in ('8.8.8.8', 'pc-a.local'):
        path = _write(tmp_path, f'''
version: 1
machine_id: pc-b
mode: status
role: peer
coordinator_machine_id: pc-a
peers:
  - machine_id: pc-a
    url: http://{host}:8010
access:
  coordination:
    enabled: true
    host: 192.168.10.20
    allowed_peer_networks: [192.168.10.0/24]
''')

        with pytest.raises(ConfigurationError, match='내부망 IP'):
            load_config(path, workspace=tmp_path)


def test_local_machine_cannot_be_registered_as_its_own_peer(tmp_path):
    path = _write(tmp_path, '''
version: 1
machine_id: pc-a
mode: status
role: coordinator
peers:
  - machine_id: pc-a
    url: http://192.168.10.10:8010
access:
  coordination:
    enabled: true
    host: 192.168.10.10
    allowed_peer_networks: [192.168.10.0/24]
''')

    with pytest.raises(ConfigurationError, match='자기 machine_id'):
        load_config(path, workspace=tmp_path)


def test_local_selection_is_atomic_and_preserves_peer_configuration(tmp_path):
    path = _write(tmp_path, '''
version: 1
machine_id: pc-b
display_name: PC B
mode: status
role: peer
coordinator_machine_id: pc-a
peers:
  - machine_id: pc-a
    url: http://192.168.10.10:8010
access:
  coordination:
    enabled: true
    host: 192.168.10.20
    allowed_peer_networks: [192.168.10.0/24]
''')

    off = update_local_selection(
        path, workspace=tmp_path, mode='off', role='coordinator'
    )
    restored = update_local_selection(
        path,
        workspace=tmp_path,
        mode='participant',
        role='peer',
        coordinator_machine_id='pc-a',
    )

    assert off.mode == 'off'
    assert off.role == 'peer'
    assert restored.mode == 'participant'
    assert restored.peers[0].machine_id == 'pc-a'
    assert restored.access.coordination_enabled is True
    assert path.stat().st_mode & 0o777 == 0o600


def _pair_config(path, tmp_path, peer_id, peer_ip, key):
    return configure_paired_peer(
        path,
        workspace=tmp_path,
        machine_id='pc-a',
        display_name='PC A',
        local_ip='192.168.10.10',
        peer_machine_id=peer_id,
        peer_ip=peer_ip,
        coordinator=True,
        hmac_key_base64=key,
    )


def test_existing_pairing_locks_machine_id(tmp_path):
    path = tmp_path / 'config/motion_coordination.yaml'
    key = 'MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA='
    _pair_config(path, tmp_path, 'pc-b', '192.168.10.20', key)

    with pytest.raises(ConfigurationError, match='machine_id를 변경'):
        configure_paired_peer(
            path,
            workspace=tmp_path,
            machine_id='renamed-pc-a',
            display_name='Renamed',
            local_ip='192.168.10.10',
            peer_machine_id='pc-c',
            peer_ip='192.168.10.30',
            coordinator=True,
            hmac_key_base64=key,
        )


def test_repair_replaces_peer_ip_and_removes_stale_exact_network(tmp_path):
    path = tmp_path / 'config/motion_coordination.yaml'
    key = 'MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA='
    _pair_config(path, tmp_path, 'pc-b', '192.168.10.20', key)
    _pair_config(path, tmp_path, 'pc-b', '192.168.10.21', key)

    value = yaml.safe_load(path.read_text(encoding='utf-8'))
    assert value['peers'] == [{
        'machine_id': 'pc-b', 'url': 'http://192.168.10.21:8010',
    }]
    networks = value['access']['coordination']['allowed_peer_networks']
    assert '192.168.10.20/32' not in networks
    assert '192.168.10.21/32' in networks


def test_concurrent_pairing_writes_preserve_both_peers(tmp_path):
    path = tmp_path / 'config/motion_coordination.yaml'
    key = 'MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA='
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(
                _pair_config, path, tmp_path, 'pc-b', '192.168.10.20', key
            ),
            executor.submit(
                _pair_config, path, tmp_path, 'pc-c', '192.168.10.30', key
            ),
        ]
        for future in futures:
            future.result()

    value = yaml.safe_load(path.read_text(encoding='utf-8'))
    credentials = yaml.safe_load(
        (tmp_path / 'config/motion_coordination.credentials.yaml').read_text(
            encoding='utf-8'
        )
    )
    assert {peer['machine_id'] for peer in value['peers']} == {'pc-b', 'pc-c'}
    assert set(credentials['peers']) == {'pc-b', 'pc-c'}


def test_pairing_two_file_commit_restores_previous_files_on_failure(
    tmp_path, monkeypatch,
):
    path = tmp_path / 'config/motion_coordination.yaml'
    credential_path = tmp_path / 'config/motion_coordination.credentials.yaml'
    key = 'MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA='
    _pair_config(path, tmp_path, 'pc-b', '192.168.10.20', key)
    previous_config = path.read_bytes()
    previous_credentials = credential_path.read_bytes()
    original_replace = transaction_module.os.replace
    failed = [False]

    def fail_config_replace(source, destination):
        if Path(destination) == path and not failed[0]:
            failed[0] = True
            raise OSError('simulated config replace failure')
        return original_replace(source, destination)

    monkeypatch.setattr(transaction_module.os, 'replace', fail_config_replace)
    with pytest.raises(OSError, match='simulated'):
        _pair_config(path, tmp_path, 'pc-c', '192.168.10.30', key)

    assert path.read_bytes() == previous_config
    assert credential_path.read_bytes() == previous_credentials
    assert not (path.parent / '.motion_coordination.yaml.transaction.json').exists()


def test_existing_credential_alone_keeps_machine_identity_locked(tmp_path):
    config = tmp_path / 'config/motion_coordination.yaml'
    credential = tmp_path / 'config/motion_coordination.credentials.yaml'
    config.parent.mkdir(parents=True)
    config.write_text(
        'version: 1\nmachine_id: pc-a\nmode: off\nrole: peer\n'
        'peers: []\naccess: {}\n'
        'credential_file: config/motion_coordination.credentials.yaml\n',
        encoding='utf-8',
    )
    credential.write_text(
        'version: 1\npeers:\n  pc-old:\n'
        '    hmac_key_base64: MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=\n',
        encoding='utf-8',
    )

    assert pairing_identity_state(config, workspace=tmp_path) == {
        'machine_id': 'pc-a', 'locked': True,
    }
