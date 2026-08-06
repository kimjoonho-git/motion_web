import json
import urllib.error
import urllib.request

import pytest

from motion_coordination.local_api import LocalCoordinationApi


def _request(port, path, payload=None):
    data = None if payload is None else json.dumps(payload).encode('utf-8')
    request = urllib.request.Request(
        f'http://127.0.0.1:{port}{path}', data=data,
        headers={'Content-Type': 'application/json'} if data else {},
        method='POST' if data else 'GET',
    )
    with urllib.request.urlopen(request, timeout=1.0) as response:
        return json.loads(response.read().decode('utf-8'))


def test_loopback_api_exposes_status_and_high_level_control():
    calls = []
    try:
        server = LocalCoordinationApi(
            lambda: {'transport': 'ros2_dds', 'joined': True},
            lambda payload: calls.append(dict(payload)) or {
                'success': True, 'message': 'accepted',
            },
            port=0,
        )
    except PermissionError:
        pytest.skip('sandbox blocks loopback sockets')
    server.start()
    try:
        status = _request(server.port, '/status')
        result = _request(server.port, '/control', {'command': 'join'})
    finally:
        server.close()
    assert status['transport'] == 'ros2_dds'
    assert result['success'] is True
    assert calls == [{'command': 'join'}]


def test_loopback_api_rejects_unknown_route():
    try:
        server = LocalCoordinationApi(lambda: {}, lambda _payload: {}, port=0)
    except PermissionError:
        pytest.skip('sandbox blocks loopback sockets')
    server.start()
    try:
        try:
            _request(server.port, '/peer-network')
        except urllib.error.HTTPError as exc:
            assert exc.code == 404
        else:
            raise AssertionError('unknown route accepted')
    finally:
        server.close()
