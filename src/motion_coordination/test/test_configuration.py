import pytest

from motion_coordination.configuration import (
    ConfigurationError,
    load_config,
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
