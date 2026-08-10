"""Opt-in real DDS process test; enable with MOTION_RUN_DDS_INTEGRATION=1."""

import json
import os
from pathlib import Path
import socket
import subprocess
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from motion_coordination.group_configuration import GroupConfig, save_group_config


pytestmark = pytest.mark.skipif(
    os.environ.get('MOTION_RUN_DDS_INTEGRATION') != '1',
    reason='real DDS process integration is opt-in',
)


def _free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(('127.0.0.1', 0))
        return int(sock.getsockname()[1])


def _get(port, path='/status'):
    with urllib.request.urlopen(
        f'http://127.0.0.1:{port}{path}', timeout=1.0,
    ) as response:
        return json.loads(response.read().decode('utf-8'))


def _post(port, command):
    request = urllib.request.Request(
        f'http://127.0.0.1:{port}/control',
        data=json.dumps({'command': command}).encode('utf-8'),
        headers={'Content-Type': 'application/json'},
        method='POST',
    )
    with urllib.request.urlopen(request, timeout=1.0) as response:
        return json.loads(response.read().decode('utf-8'))


def _wait(predicate, timeout=8.0):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        try:
            last = predicate()
            if last:
                return last
        except OSError:
            pass
        time.sleep(0.1)
    raise AssertionError(f'DDS integration timeout: {last!r}')


def _one_peer_snapshot(port):
    value = _get(port)
    return value if len(value.get('peers') or []) == 1 else None


class _FakeLocalWebBridge:
    def __init__(self, *, cycle_duration_sec, start_trigger_delays=None):
        self._lock = threading.RLock()
        self._timers = []
        self.cycle_duration_sec = float(cycle_duration_sec)
        self.control_calls = []
        self.cycle_ready_at = {}
        self.cycle_initialized_at = {}
        self.motion_started_at = {}
        self.start_trigger_delays = list(start_trigger_delays or [])
        self._motion_status = {}
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                if self.path != '/api/coordination/local-status':
                    self._send(404, {'success': False})
                    return
                with owner._lock:
                    status = dict(owner._motion_status)
                self._send(200, {
                    'bridge_state': 'ok',
                    'motion_run_status': status,
                    'safety_status': {
                        'servo_alarm_grade': 0,
                        'servo_alarm_active': [],
                    },
                })

            def do_POST(self):  # noqa: N802
                length = int(self.headers.get('Content-Length') or 0)
                payload = json.loads(self.rfile.read(length).decode('utf-8'))
                if self.path == '/api/coordination/local-readiness':
                    self._send(200, {'success': True, 'message': 'ready'})
                    return
                if self.path != '/api/coordination/local-control':
                    self._send(404, {'success': False})
                    return
                result = owner._control(payload)
                self._send(200, result)

            def log_message(self, _format, *_args):
                return

            def _send(self, status, payload):
                body = json.dumps(payload).encode('utf-8')
                self.send_response(status)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self._server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.port = int(self._server.server_address[1])
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def _control(self, payload):
        command = str(payload.get('command') or '')
        now = time.monotonic()
        with self._lock:
            self.control_calls.append({**dict(payload), 'received_at': now})
        if command == 'group_prepare':
            execution_id = str(payload['execution_id'])
            initialize_at = float(payload['initialize_monotonic'])
            self._later(
                max(initialize_at - now, 0.0) + 0.05,
                self._arm,
                execution_id,
            )
        elif command == 'group_start_at':
            execution_id = str(payload['execution_id'])
            cycle = int(payload['cycle_number'])
            start_at = float(payload['start_monotonic'])
            start_index = len([
                row for row in self.control_calls
                if row.get('command') == 'group_start_at'
            ]) - 1
            extra_delay = (
                float(self.start_trigger_delays[start_index])
                if start_index < len(self.start_trigger_delays) else 0.0
            )
            delay = max(start_at - now, 0.0) + max(extra_delay, 0.0)
            self._later(delay, self._start_motion, {
                'group_execution': True,
                'execution_id': execution_id,
                'state': 'running',
                'phase': 'running',
                'group_cycle_number': cycle,
                'current_cycle': cycle,
                'lifecycle': {},
            })
            self._later(
                delay + self.cycle_duration_sec,
                self._motion_completed,
                execution_id,
                cycle,
                start_at,
            )
        elif command == 'group_initialize_at':
            execution_id = str(payload['execution_id'])
            cycle = int(payload['cycle_number'])
            initialize_at = float(payload['initialize_monotonic'])
            self._later(
                max(initialize_at - now, 0.0) + 0.05,
                self._cycle_initialized,
                execution_id,
                cycle,
            )
        elif command in {'stop_now', 'group_cancel'}:
            self._set_status({
                'group_execution': True,
                'execution_id': str(payload.get('execution_id') or ''),
                'state': 'stopped',
                'phase': 'stopped',
            })
        elif command == 'stop_after_cycle':
            pass
        return {'success': True, 'message': command}

    def _motion_completed(self, execution_id, cycle, start_at):
        with self._lock:
            self.cycle_ready_at[int(cycle)] = time.monotonic()
        self._set_status({
            'group_execution': True,
            'execution_id': execution_id,
            'state': 'motion_completed',
            'phase': 'group_motion_completed',
            'group_cycle_number': int(cycle),
            'current_cycle': int(cycle),
            'lifecycle': {
                'motion_started_monotonic': float(
                    self.motion_started_at.get(int(cycle), start_at)
                ),
            },
        })

    def _cycle_initialized(self, execution_id, cycle):
        with self._lock:
            self.cycle_initialized_at[int(cycle)] = time.monotonic()
        self._set_status({
            'group_execution': True,
            'execution_id': execution_id,
            'state': 'cycle_ready',
            'phase': 'group_cycle_initialized',
            'group_cycle_number': int(cycle),
            'current_cycle': int(cycle),
        })

    def _arm(self, execution_id):
        self._set_status({
            'group_execution': True,
            'execution_id': execution_id,
            'state': 'armed',
            'phase': 'group_armed',
            'group_cycle_number': 0,
            'current_cycle': 0,
            'initialize_triggered_monotonic': time.monotonic(),
        })

    def _start_motion(self, value):
        actual = time.monotonic()
        cycle = int(value.get('group_cycle_number') or 0)
        with self._lock:
            self.motion_started_at[cycle] = actual
        value = dict(value)
        value['lifecycle'] = {'motion_started_monotonic': actual}
        self._set_status(value)

    def _set_status(self, value):
        with self._lock:
            self._motion_status = dict(value)

    def _later(self, delay, callback, *args):
        timer = threading.Timer(max(float(delay), 0.0), callback, args=args)
        timer.daemon = True
        with self._lock:
            self._timers.append(timer)
        timer.start()

    def start_cycles(self):
        with self._lock:
            return [
                int(row['cycle_number'])
                for row in self.control_calls
                if row.get('command') == 'group_start_at'
            ]

    def start_call(self, cycle):
        with self._lock:
            return next(
                dict(row) for row in self.control_calls
                if row.get('command') == 'group_start_at'
                and int(row.get('cycle_number') or 0) == int(cycle)
            )

    def calls(self, command):
        with self._lock:
            return [
                dict(row) for row in self.control_calls
                if row.get('command') == command
            ]

    def close(self):
        with self._lock:
            timers = list(self._timers)
        for timer in timers:
            timer.cancel()
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=2.0)


def test_two_processes_discover_joined_peer_over_typed_dds(tmp_path):
    executable = Path(
        os.environ.get('MOTION_COORDINATION_EXECUTABLE')
        or Path.cwd()
        / 'install/motion_coordination/lib/motion_coordination/'
        'motion_coordination_node'
    )
    if not executable.is_file():
        pytest.skip('motion_coordination_node is not built')
    ports = [_free_port(), _free_port()]
    configs = []
    for index, pc_id in enumerate(('dds-test-a', 'dds-test-b')):
        path = tmp_path / f'{pc_id}.yaml'
        save_group_config(path, GroupConfig(
            pc_id, pc_id, True, 'dds-process-test', 77,
            heartbeat_sec=0.1, warning_timeout_sec=0.6,
            peer_timeout_sec=1.2, start_lead_sec=0.5,
            schedule_ack_margin_sec=0.1,
            max_trigger_sync_uncertainty_ms=20.0,
            trigger_sync_samples=5,
            prepare_timeout_sec=2.0,
        ))
        configs.append(path)
    processes = []
    try:
        for path, port in zip(configs, ports):
            environment = dict(os.environ)
            environment.update({
                'MOTION_COORDINATION_CONFIG': str(path),
                'MOTION_COORDINATION_LOCAL_PORT': str(port),
                'ROS_LOCALHOST_ONLY': '0',
            })
            processes.append(subprocess.Popen(
                [str(executable)], env=environment,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            ))
        for port in ports:
            _wait(lambda port=port: _get(port).get('node_connected'))
            assert _post(port, 'join')['success'] is True
        for port in ports:
            snapshot = _wait(lambda port=port: _one_peer_snapshot(port))
            assert snapshot['transport'] == 'ros2_dds'
            assert snapshot['peers'][0]['state'] == 'online'
    finally:
        for process in processes:
            process.terminate()
        for process in processes:
            try:
                process.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2.0)


def test_two_processes_complete_two_barrier_cycles_over_typed_dds(tmp_path):
    executable = Path(
        os.environ.get('MOTION_COORDINATION_EXECUTABLE')
        or Path.cwd()
        / 'install/motion_coordination/lib/motion_coordination/'
        'motion_coordination_node'
    )
    if not executable.is_file():
        pytest.skip('motion_coordination_node is not built')
    bridges = [
        _FakeLocalWebBridge(cycle_duration_sec=0.22),
        _FakeLocalWebBridge(cycle_duration_sec=0.38),
    ]
    ports = [_free_port(), _free_port()]
    configs = []
    for pc_id in ('dds-flow-a', 'dds-flow-b'):
        path = tmp_path / f'{pc_id}.yaml'
        save_group_config(path, GroupConfig(
            pc_id, pc_id, True, 'dds-flow-test', 78,
            heartbeat_sec=1.0, warning_timeout_sec=1.5,
            peer_timeout_sec=3.0, start_lead_sec=0.5,
            schedule_ack_margin_sec=0.1,
            max_trigger_sync_uncertainty_ms=20.0,
            trigger_sync_samples=5,
            prepare_timeout_sec=3.0,
            trigger_report_timeout_sec=1.0,
        ))
        configs.append(path)
    processes = []
    try:
        for path, port, bridge in zip(configs, ports, bridges):
            environment = dict(os.environ)
            environment.update({
                'MOTION_COORDINATION_CONFIG': str(path),
                'MOTION_COORDINATION_LOCAL_PORT': str(port),
                'MOTION_WEB_BRIDGE_PORT': str(bridge.port),
                'ROS_LOCALHOST_ONLY': '0',
            })
            processes.append(subprocess.Popen(
                [str(executable)], env=environment,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            ))
        for port in ports:
            _wait(lambda port=port: _get(port).get('node_connected'))
            assert _post(port, 'join')['success'] is True
        for port in ports:
            _wait(lambda port=port: _one_peer_snapshot(port))
        started = _post(ports[0], 'start_group')
        assert started['success'] is True, started
        _wait(lambda: all(bridge.start_cycles() == [1] for bridge in bridges), timeout=8.0)
        _wait(lambda: all(1 in bridge.cycle_ready_at for bridge in bridges), timeout=8.0)
        _wait(
            lambda: all(1 in bridge.cycle_initialized_at for bridge in bridges),
            timeout=8.0,
        )
        _wait(lambda: all(bridge.start_cycles() == [1, 2] for bridge in bridges), timeout=8.0)

        latest_ready = max(bridge.cycle_ready_at[1] for bridge in bridges)
        latest_initialized = max(
            bridge.cycle_initialized_at[1] for bridge in bridges
        )
        for bridge in bridges:
            assert bridge.start_call(2)['received_at'] >= latest_ready
            assert bridge.start_call(2)['received_at'] >= latest_initialized
            assert bridge.start_cycles().count(1) == 1
            assert bridge.start_cycles().count(2) == 1
        _wait(lambda: (
            _get(ports[0]).get('execution', {}).get('start_spread_ms')
            is not None
        ))
        synchronized = _get(ports[0])
        assert synchronized['execution']['start_within_20ms'] is True
        assert synchronized['trigger_sync']['trigger_sync_source'] == (
            'dds_relative_monotonic'
        )
        processes[1].terminate()
        processes[1].wait(timeout=5.0)
        _wait(lambda: bridges[1].calls('stop_now'), timeout=3.0)
        _wait(lambda: bridges[0].calls('stop_now'), timeout=6.0)
    finally:
        for process in processes:
            process.terminate()
        for process in processes:
            try:
                process.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2.0)
        for bridge in bridges:
            bridge.close()


def test_trigger_spread_stops_once_and_blocks(tmp_path):
    executable = Path(
        os.environ.get('MOTION_COORDINATION_EXECUTABLE')
        or Path.cwd()
        / 'install/motion_coordination/lib/motion_coordination/'
        'motion_coordination_node'
    )
    if not executable.is_file():
        pytest.skip('motion_coordination_node is not built')
    bridges = [
        _FakeLocalWebBridge(cycle_duration_sec=0.15),
        _FakeLocalWebBridge(
            cycle_duration_sec=0.15,
            start_trigger_delays=[0.04],
        ),
    ]
    ports = [_free_port(), _free_port()]
    configs = []
    for pc_id in ('dds-spread-a', 'dds-spread-b'):
        path = tmp_path / f'{pc_id}.yaml'
        save_group_config(path, GroupConfig(
            pc_id, pc_id, True, 'dds-spread-79', 79,
            heartbeat_sec=0.1, warning_timeout_sec=0.8,
            peer_timeout_sec=1.6, start_lead_sec=0.5,
            schedule_ack_margin_sec=0.1,
            max_trigger_sync_uncertainty_ms=20.0,
            trigger_sync_samples=5,
            prepare_timeout_sec=3.0,
        ))
        configs.append(path)
    processes = []
    try:
        for path, port, bridge in zip(configs, ports, bridges):
            environment = dict(os.environ)
            environment.update({
                'MOTION_COORDINATION_CONFIG': str(path),
                'MOTION_COORDINATION_LOCAL_PORT': str(port),
                'MOTION_WEB_BRIDGE_PORT': str(bridge.port),
                'ROS_LOCALHOST_ONLY': '0',
            })
            processes.append(subprocess.Popen(
                [str(executable)], env=environment,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            ))
        for port in ports:
            _wait(lambda port=port: _get(port).get('node_connected'))
            assert _post(port, 'join')['success'] is True
        for port in ports:
            _wait(lambda port=port: _one_peer_snapshot(port))
        assert _post(ports[0], 'start_group')['success'] is True
        _wait(lambda: bridges[0].calls('stop_now'), timeout=10.0)
        _wait(lambda: bridges[1].calls('stop_now'), timeout=10.0)
        failed = _wait(
            lambda: (
                _get(ports[0])
                if _get(ports[0]).get('coordination_error', {}).get('active')
                else None
            ),
            timeout=10.0,
        )
        assert failed['coordination_error']['code'] == (
            'GROUP_TRIGGER_SPREAD_EXCEEDED'
        )
        assert len(bridges[0].calls('group_prepare')) == 1
        assert len(bridges[1].calls('group_start_at')) == 1
        assert _post(ports[0], 'start_group')['success'] is False
        assert _post(ports[0], 'acknowledge_group_error')['success'] is True
    finally:
        for process in processes:
            process.terminate()
        for process in processes:
            try:
                process.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2.0)
        for bridge in bridges:
            bridge.close()


def test_duplicate_pc_id_is_detected_by_both_dds_processes(tmp_path):
    executable = Path(
        os.environ.get('MOTION_COORDINATION_EXECUTABLE')
        or Path.cwd()
        / 'install/motion_coordination/lib/motion_coordination/'
        'motion_coordination_node'
    )
    bridges = [
        _FakeLocalWebBridge(cycle_duration_sec=0.1),
        _FakeLocalWebBridge(cycle_duration_sec=0.1),
    ]
    ports = [_free_port(), _free_port()]
    configs = []
    for index in range(2):
        path = tmp_path / f'duplicate-{index}.yaml'
        save_group_config(path, GroupConfig(
            'duplicate-pc', f'Duplicate {index}', True,
            'dds-duplicate-test', 81,
            heartbeat_sec=0.1, warning_timeout_sec=0.8,
            peer_timeout_sec=1.6, start_lead_sec=0.5,
            schedule_ack_margin_sec=0.1,
            max_trigger_sync_uncertainty_ms=20.0,
            trigger_sync_samples=5, prepare_timeout_sec=3.0,
            trigger_report_timeout_sec=0.5,
        ))
        configs.append(path)
    processes = []
    try:
        for path, port, bridge in zip(configs, ports, bridges):
            environment = dict(os.environ)
            environment.update({
                'MOTION_COORDINATION_CONFIG': str(path),
                'MOTION_COORDINATION_LOCAL_PORT': str(port),
                'MOTION_WEB_BRIDGE_PORT': str(bridge.port),
                'ROS_LOCALHOST_ONLY': '0',
            })
            processes.append(subprocess.Popen(
                [str(executable)], env=environment,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            ))
        for port in ports:
            _wait(lambda port=port: _get(port).get('node_connected'))
        join_results = [None, None]
        threads = [
            threading.Thread(
                target=lambda index=index, port=port: join_results.__setitem__(
                    index, _post(port, 'join')
                )
            )
            for index, port in enumerate(ports)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=2.0)
        assert all(result and result['success'] for result in join_results)
        for port in ports:
            failed = _wait(lambda port=port: (
                _get(port)
                if _get(port).get('coordination_error', {}).get('code')
                == 'DUPLICATE_PC_ID' else None
            ))
            assert failed['coordination_error']['active'] is True
            assert _post(port, 'start_group')['success'] is False
    finally:
        for process in processes:
            process.terminate()
        for process in processes:
            try:
                process.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2.0)
        for bridge in bridges:
            bridge.close()


def test_missing_motion_started_report_stops_and_blocks_over_dds(tmp_path):
    executable = Path(
        os.environ.get('MOTION_COORDINATION_EXECUTABLE')
        or Path.cwd()
        / 'install/motion_coordination/lib/motion_coordination/'
        'motion_coordination_node'
    )
    bridges = [
        _FakeLocalWebBridge(cycle_duration_sec=0.1),
        _FakeLocalWebBridge(
            cycle_duration_sec=0.1, start_trigger_delays=[2.0],
        ),
    ]
    ports = [_free_port(), _free_port()]
    configs = []
    for pc_id in ('dds-report-a', 'dds-report-b'):
        path = tmp_path / f'{pc_id}.yaml'
        save_group_config(path, GroupConfig(
            pc_id, pc_id, True, 'dds-report-test', 82,
            heartbeat_sec=0.1, warning_timeout_sec=0.8,
            peer_timeout_sec=1.6, start_lead_sec=0.5,
            schedule_ack_margin_sec=0.1,
            max_trigger_sync_uncertainty_ms=20.0,
            trigger_sync_samples=5, prepare_timeout_sec=3.0,
            trigger_report_timeout_sec=0.5,
        ))
        configs.append(path)
    processes = []
    try:
        for path, port, bridge in zip(configs, ports, bridges):
            environment = dict(os.environ)
            environment.update({
                'MOTION_COORDINATION_CONFIG': str(path),
                'MOTION_COORDINATION_LOCAL_PORT': str(port),
                'MOTION_WEB_BRIDGE_PORT': str(bridge.port),
                'ROS_LOCALHOST_ONLY': '0',
            })
            processes.append(subprocess.Popen(
                [str(executable)], env=environment,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            ))
        for port in ports:
            _wait(lambda port=port: _get(port).get('node_connected'))
            assert _post(port, 'join')['success'] is True
        for port in ports:
            _wait(lambda port=port: _one_peer_snapshot(port))
        assert _post(ports[0], 'start_group')['success'] is True
        failed = _wait(lambda: (
            _get(ports[0])
            if _get(ports[0]).get('coordination_error', {}).get('code')
            == 'GROUP_MOTION_START_REPORT_TIMEOUT' else None
        ), timeout=10.0)
        assert failed['coordination_error']['active'] is True
        assert bridges[0].calls('stop_now')
        assert bridges[1].calls('stop_now')
        assert _post(ports[0], 'start_group')['success'] is False
    finally:
        for process in processes:
            process.terminate()
        for process in processes:
            try:
                process.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2.0)
        for bridge in bridges:
            bridge.close()
