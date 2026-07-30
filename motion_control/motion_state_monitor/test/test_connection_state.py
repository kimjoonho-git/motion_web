import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from motion_state_monitor.monitor_node import MotionStateMonitor


class ConnectionStateTest(unittest.TestCase):
    def setUp(self):
        self.monitor = object.__new__(MotionStateMonitor)
        self.monitor.connection_loss_confirm_sec = 1.0
        self.monitor.connection_recovery_confirm_sec = 0.5
        self.monitor._communication_health = {}

    def test_connection_fields_are_transport_independent(self):
        for transport in ('ethercat', 'serial', 'can'):
            motor = {'transport': transport, 'last_seen_at': 10.0, 'age_sec': 0.01}
            self.monitor._set_connection_fields(
                motor,
                'detected',
                'runtime_feedback_fresh',
                'runtime_topic',
                10.01,
            )
            self.assertEqual(motor['connection_state'], 'online')
            self.assertTrue(motor['connection_connected'])
            self.assertEqual(motor['connection_source'], 'runtime_topic')

    def test_internal_limit_bit_is_reported_for_ac_servo(self):
        self.monitor._motor_metadata = {1: {'motor_type': 'minas'}}
        self.monitor._metadata_for = lambda _axis: {'motor_type': 'minas'}
        message = SimpleNamespace(
            statusword=[0x0E37],
            position=[0.0],
            velocity=[0.0],
            effort=[0.0],
            errorcode=[0],
            controlword=[0x000F],
        )

        motor = self.monitor._motor_from_status(message, 0, 1, 10.0)

        self.assertTrue(motor['internal_limit_active'])
        self.assertIn('Internal limit active', motor['status_text'])

    def test_high_rate_feedback_updates_freshness_without_reprocessing_axes(self):
        self.monitor.monitoring_enabled = True
        self.monitor.feedback_process_hz = 100.0
        self.monitor._last_motor_status_at = 10.0
        self.monitor._last_motor_status_processed_at = 10.0
        self.monitor.max_motors = 50
        self.monitor._motor_from_status = lambda *_args: self.fail(
            'feedback inside the 100 Hz window must not be converted again'
        )
        message = SimpleNamespace(controller_index=[0])

        with patch('motion_state_monitor.monitor_node.time.time', return_value=10.005):
            self.monitor._motor_status_callback(message)

        self.assertEqual(self.monitor._last_motor_status_at, 10.005)
        self.assertEqual(self.monitor._last_motor_status_processed_at, 10.0)

    def test_bus_discovery_does_not_override_runtime_offline(self):
        motors = [{
            'controller_index': 0,
            'display_name': 'Axis 0',
            'motor_type': 'minas',
            'motor_type_label': 'AC Servo',
            'transport': 'ethercat',
            'transport_label': 'EtherCAT',
            'alias': 101,
            'connection_state': 'offline',
            'connection_connected': False,
            'connection_confirmed': True,
            'connection_reason': 'feedback_timeout',
            'connection_source': 'runtime_topic',
            'connection_message': 'runtime offline',
        }]
        rows = self.monitor._build_scan_connection_rows(
            motors,
            {'available': True, 'slaves': [{'ethercat_alias': 101}]},
            {'available': False, 'skipped': True, 'devices': []},
            scan_ethercat=True,
            scan_dynamixel=False,
        )

        self.assertEqual(rows[0]['connection_state'], 'offline')
        self.assertFalse(rows[0]['connection_connected'])
        self.assertEqual(rows[0]['discovery_state'], 'detected')
        self.assertTrue(rows[0]['discovery_detected'])

    def test_physical_connection_is_separate_from_runtime_feedback(self):
        self.monitor._last_ethercat_physical_scan = {
            'complete': True,
            'scanned_at': 12.0,
            'slaves': [{
                'master_index': 0,
                'slave_position': 0,
                'ethercat_alias': 0,
            }],
        }
        motor = {
            'transport': 'ethercat',
            'ethercat_master_index': 0,
            'slave_position': 0,
            'alias': 0,
            'connection_state': 'online',
        }

        self.monitor._set_physical_connection_fields(motor)

        self.assertEqual(motor['connection_state'], 'online')
        self.assertEqual(motor['physical_connection_state'], 'detected')
        self.assertTrue(motor['physical_connection_confirmed'])
        self.assertEqual(motor['physical_connection_checked_at'], 12.0)

    def test_failed_physical_scan_never_turns_runtime_feedback_into_physical_online(self):
        self.monitor._last_ethercat_physical_scan = {
            'complete': False,
            'scanned_at': 15.0,
            'error': 'link down',
            'slaves': [],
        }
        motor = {
            'transport': 'ethercat',
            'connection_state': 'online',
        }

        self.monitor._set_physical_connection_fields(motor)

        self.assertEqual(motor['connection_state'], 'online')
        self.assertEqual(motor['physical_connection_state'], 'unknown')
        self.assertFalse(motor['physical_connection_confirmed'])
        self.assertEqual(motor['physical_connection_message'], 'link down')

    def test_serial_identity_falls_back_to_node_id(self):
        motors = [{
            'controller_index': 1,
            'display_name': 'Axis 1',
            'motor_type': 'dynamixel',
            'motor_type_label': 'Dynamixel',
            'transport': 'serial',
            'transport_label': 'Serial',
            'bus_id': None,
            'node_id': 5,
            'connection_state': 'online',
            'connection_connected': True,
            'connection_confirmed': True,
            'connection_reason': 'runtime_feedback_fresh',
            'connection_source': 'runtime_topic',
            'connection_message': 'runtime online',
        }]
        rows = self.monitor._build_scan_connection_rows(
            motors,
            {'available': False, 'skipped': True, 'slaves': []},
            {
                'available': True,
                'mode': 'direct_ping',
                'devices': [{'id': 5}],
            },
            scan_ethercat=False,
            scan_dynamixel=True,
        )

        self.assertEqual(rows[0]['connection_state'], 'online')
        self.assertEqual(rows[0]['discovery_state'], 'detected')
        self.assertEqual(rows[0]['discovery_source'], 'direct_ping')

    def test_sii_header_is_the_physical_alias_and_identity_source(self):
        data = bytearray(32)
        data[8:10] = (0).to_bytes(2, 'little')
        data[16:20] = (0x0000066F).to_bytes(4, 'little')
        data[20:24] = (0x60380004).to_bytes(4, 'little')
        data[24:28] = (1).to_bytes(4, 'little')
        data[28:32] = (0x24121207).to_bytes(4, 'little')

        identity = self.monitor._parse_sii_identity(bytes(data))

        self.assertEqual(identity['ethercat_alias'], 0)
        self.assertEqual(identity['vendor_id'], 0x0000066F)
        self.assertEqual(identity['product_code'], 0x60380004)
        self.assertEqual(identity['serial_number'], 0x24121207)

    def test_physical_scan_success_requires_complete_direct_result(self):
        success = {'available': True, 'complete': True, 'slaves_count': 5}
        partial = {'available': True, 'complete': False, 'slaves_count': 5}
        self.assertTrue(self.monitor._physical_section_success(success, 'slaves_count'))
        self.assertFalse(self.monitor._physical_section_success(partial, 'slaves_count'))

    def test_dynamixel_scan_never_injects_runtime_devices(self):
        self.monitor._dynamixel_scan_targets = lambda: []

        result = self.monitor._scan_dynamixel_motors()

        self.assertEqual(result['mode'], 'direct_ping')
        self.assertFalse(result['available'])
        self.assertNotIn('runtime_devices', result)

    def test_dynamixel_targets_cover_all_valid_ids(self):
        self.monitor.motor_config_file = ''
        self.monitor.dynamixel_scan_max_id = 252
        self.monitor._dynamixel_serial_ports = lambda _config: [{
            'port': '/dev/ttyUSB0', 'source': 'test', 'resolved': '/dev/ttyUSB0'
        }]

        targets = self.monitor._dynamixel_scan_targets()

        self.assertEqual(targets[0]['ids'][0], 0)
        self.assertEqual(targets[0]['ids'][-1], 252)
        self.assertEqual(len(targets[0]['ids']), 253)

    def test_full_scan_is_not_success_when_only_one_transport_completes(self):
        self.monitor.monitoring_enabled = True
        self.monitor._build_scan_result = lambda **_kwargs: {
            'scan_complete': False,
            'scan_success': True,
        }
        response = SimpleNamespace(success=None, message='')

        self.monitor._scan_motors(None, response)

        self.assertFalse(response.success)

    def test_scan_contract_is_persisted_in_every_scan_result(self):
        self.monitor._scan_sequence = 0
        self.monitor._active_scan_id = ''
        self.monitor._scan_progress_publisher = None
        self.monitor.monitoring_enabled = True
        self.monitor.input_topic = '/motor_status'
        self.monitor.ethercat_status_topic = '/ethercat_status'
        self.monitor._ethercat_status = {}
        self.monitor._motor_metadata = {}
        self.monitor._motors = {}
        self.monitor._started_at = 0.0
        self.monitor._skipped_ethercat_scan = lambda now: {
            'available': False, 'complete': False, 'skipped': True,
            'slaves_count': 0, 'slaves': [], 'scanned_at': now,
        }
        self.monitor._skipped_dynamixel_scan = lambda now: {
            'available': False, 'complete': False, 'skipped': True,
            'devices_count': 0, 'devices': [], 'scanned_at': now,
        }

        result = self.monitor._build_scan_result(
            scan_ethercat=False,
            scan_dynamixel=False,
        )

        contract = result['scan_contract']
        self.assertEqual(contract['version'], 3)
        self.assertTrue(contract['physical_only'])
        self.assertTrue(contract['ethercat_requires_rescan'])
        self.assertEqual(contract['dynamixel_id_min'], 0)
        self.assertEqual(contract['dynamixel_id_max'], 252)
        self.assertTrue(contract['full_success_requires_all_requested_transports'])

    def test_scan_progress_publishes_real_backend_event(self):
        published = []
        self.monitor._active_scan_id = 'scan-1'
        self.monitor._scan_progress_publisher = SimpleNamespace(
            publish=lambda message: published.append(json.loads(message.data))
        )

        self.monitor._publish_scan_progress(
            'ethercat_slave_done',
            'Slave 2 읽기 완료',
            transport='ethercat',
            details={'slave_position': 2},
        )

        self.assertEqual(published[0]['scan_id'], 'scan-1')
        self.assertEqual(published[0]['phase'], 'ethercat_slave_done')
        self.assertEqual(published[0]['details']['slave_position'], 2)

    def test_ethercat_scan_rescans_bus_before_reading_sii(self):
        # A cached frame from the stopped Motor Manager must not block a scan
        # after the master is confirmed idle.
        self.monitor._last_motor_status_at = 10**12
        self.monitor.disconnected_timeout_sec = 2.0
        self.monitor._motor_metadata = {}
        listing = '''=== Master 0, Slave 0 ===
State: PREOP
Identity:
  Vendor Id: 0x0000066f
  Product code: 0x60380004
  Revision number: 0x00010000
  Serial number: 0x24121207
'''
        sii = bytearray(32)
        sii[16:20] = (0x0000066F).to_bytes(4, 'little')
        sii[20:24] = (0x60380004).to_bytes(4, 'little')
        sii[24:28] = (0x00010000).to_bytes(4, 'little')
        sii[28:32] = (0x24121207).to_bytes(4, 'little')
        calls = []

        def run(command, **_kwargs):
            calls.append(command)
            if command[1] == 'master':
                return SimpleNamespace(
                    returncode=0,
                    stdout='Master0\n  Phase: Idle\n  Active: no\n',
                    stderr='',
                )
            if command[1] == 'slaves':
                return SimpleNamespace(returncode=0, stdout=listing, stderr='')
            if command[1] == 'rescan':
                return SimpleNamespace(returncode=0, stdout='', stderr='')
            if command[1] == 'sii_read':
                return SimpleNamespace(returncode=0, stdout=bytes(sii), stderr=b'')
            if command[1] == 'reg_read':
                return SimpleNamespace(returncode=0, stdout='0x0000 0', stderr='')
            raise AssertionError(command)

        with patch('motion_state_monitor.monitor_node.subprocess.run', side_effect=run):
            result = self.monitor._scan_ethercat_slaves()

        self.assertTrue(result['complete'])
        self.assertTrue(result['rescan_performed'])
        self.assertEqual(result['source'], 'ethercat_rescan_sii_and_register')
        self.assertLess(
            calls.index(['ethercat', 'rescan']),
            calls.index(['ethercat', 'sii_read', '-m', '0', '-p', '0']),
        )

    def test_ethercat_scan_reads_duplicate_slave_positions_from_each_master(self):
        self.monitor._last_motor_status_at = None
        self.monitor._motor_metadata = {}
        listing = '''=== Master 0, Slave 0 ===
State: PREOP
Identity:
  Vendor Id: 0x0000066f
  Product code: 0x60380004
  Revision number: 0x00010000
  Serial number: 0x24121207
=== Master 1, Slave 0 ===
State: PREOP
Identity:
  Vendor Id: 0x0000066f
  Product code: 0x60380004
  Revision number: 0x00010000
  Serial number: 0x24121208
'''
        sii_by_master = {}
        for master_index, serial in ((0, 0x24121207), (1, 0x24121208)):
            sii = bytearray(32)
            sii[16:20] = (0x0000066F).to_bytes(4, 'little')
            sii[20:24] = (0x60380004).to_bytes(4, 'little')
            sii[24:28] = (0x00010000).to_bytes(4, 'little')
            sii[28:32] = serial.to_bytes(4, 'little')
            sii_by_master[master_index] = bytes(sii)
        calls = []

        def run(command, **_kwargs):
            calls.append(command)
            if command[1] == 'master':
                return SimpleNamespace(
                    returncode=0,
                    stdout=(
                        'Master0\n  Phase: Idle\n  Active: no\n'
                        'Master1\n  Phase: Idle\n  Active: no\n'
                    ),
                    stderr='',
                )
            if command[1] == 'slaves':
                return SimpleNamespace(returncode=0, stdout=listing, stderr='')
            if command[1] == 'rescan':
                return SimpleNamespace(returncode=0, stdout='', stderr='')
            if command[1] == 'sii_read':
                master_index = int(command[command.index('-m') + 1])
                return SimpleNamespace(
                    returncode=0,
                    stdout=sii_by_master[master_index],
                    stderr=b'',
                )
            if command[1] == 'reg_read':
                return SimpleNamespace(returncode=0, stdout='0x0000 0', stderr='')
            raise AssertionError(command)

        with patch('motion_state_monitor.monitor_node.subprocess.run', side_effect=run):
            result = self.monitor._scan_ethercat_slaves()

        self.assertTrue(result['complete'])
        self.assertEqual(
            [(item['master_index'], item['slave_position']) for item in result['slaves']],
            [(0, 0), (1, 0)],
        )
        self.assertEqual(
            [
                (item['master_index'], item['complete'], item['slaves_count'])
                for item in result['masters']
            ],
            [(0, True, 1), (1, True, 1)],
        )
        self.assertIn(
            ['ethercat', 'sii_read', '-m', '0', '-p', '0'],
            calls,
        )
        self.assertIn(
            ['ethercat', 'sii_read', '-m', '1', '-p', '0'],
            calls,
        )

    def test_ethercat_scan_blocks_rescan_while_slave_is_operational(self):
        self.monitor._last_motor_status_at = None
        self.monitor.disconnected_timeout_sec = 2.0
        self.monitor._motor_metadata = {}
        listing = '=== Master 0, Slave 0 ===\nState: OP\n'
        calls = []

        def run(command, **_kwargs):
            calls.append(command)
            if command[1] == 'master':
                return SimpleNamespace(
                    returncode=0,
                    stdout='Master0\n  Phase: Idle\n  Active: no\n',
                    stderr='',
                )
            return SimpleNamespace(returncode=0, stdout=listing, stderr='')

        with patch('motion_state_monitor.monitor_node.subprocess.run', side_effect=run):
            result = self.monitor._scan_ethercat_slaves()

        self.assertFalse(result['available'])
        self.assertTrue(result['rescan_blocked'])
        self.assertNotIn(['ethercat', 'rescan'], calls)

    def test_ethercat_scan_blocks_rescan_while_master_is_claimed_during_startup(self):
        self.monitor._last_motor_status_at = None
        self.monitor.disconnected_timeout_sec = 2.0
        self.monitor._motor_metadata = {}
        calls = []

        def run(command, **_kwargs):
            calls.append(command)
            if command[1] == 'master':
                return SimpleNamespace(
                    returncode=0,
                    stdout='Master0\n  Phase: Operation\n  Active: yes\n',
                    stderr='',
                )
            raise AssertionError('Slave 조회 전에 Master 사용 상태로 차단해야 합니다')

        with patch('motion_state_monitor.monitor_node.subprocess.run', side_effect=run):
            result = self.monitor._scan_ethercat_slaves()

        self.assertFalse(result['available'])
        self.assertTrue(result['rescan_blocked'])
        self.assertIn('모터 제어 프로그램이 사용 중', result['error'])
        self.assertEqual(calls, [['ethercat', 'master']])

    def test_transient_communication_failure_is_debounced(self):
        first_failure = self.monitor._update_communication_health(3, True, 10.0)
        self.assertFalse(first_failure['confirmed_offline'])

        recovered = self.monitor._update_communication_health(3, False, 10.2)
        self.assertFalse(recovered['confirmed_offline'])

        self.monitor._update_communication_health(3, True, 20.0)
        confirmed = self.monitor._update_communication_health(3, True, 21.0)
        self.assertTrue(confirmed['confirmed_offline'])

        recovering = self.monitor._update_communication_health(3, False, 21.1)
        self.assertTrue(recovering['confirmed_offline'])
        online = self.monitor._update_communication_health(3, False, 21.6)
        self.assertFalse(online['confirmed_offline'])

    def test_zero_alias_axes_match_by_slave_position(self):
        axes = [
            {
                'controller_index': position,
                'display_name': f'Axis {position}',
                'ethercat_alias': 0,
                'ethercat_master_index': 0,
                'slave_position': position,
                'state': 'configured',
            }
            for position in range(5)
        ]
        slaves = [
            {
                'master_index': 0,
                'slave_position': position,
                'ethercat_alias': None,
                'rotary_alias': 0,
            }
            for position in range(5)
        ]

        rows = self.monitor._build_matching_rows(slaves, axes)

        self.assertEqual([row['controller_index'] for row in rows], list(range(5)))
        self.assertEqual([row['match_state'] for row in rows], ['configured'] * 5)

    def test_runtime_motor_keeps_configured_slave_identity_for_scan_matching(self):
        self.monitor._motor_metadata = {
            0: {
                'display_name': 'Axis 0',
                'motor_type': 'minas',
                'motor_type_label': 'AC Servo',
                'transport': 'ethercat',
                'transport_label': 'EtherCAT',
                'alias': 0,
                'ethercat_master_index': 0,
                'slave_position': 0,
            },
        }
        self.monitor._metadata_for = lambda axis: self.monitor._motor_metadata[axis]
        runtime = [{
            'controller_index': 0,
            'display_name': 'Axis 0',
            'motor_type': 'minas',
            'transport': 'ethercat',
            'alias': 0,
            'state': 'detected',
            'connection_state': 'online',
            'connection_connected': True,
        }]

        axes = self.monitor._configured_axis_list(runtime)
        rows = self.monitor._build_matching_rows(
            [{
                'master_index': 0,
                'slave_position': 0,
                'ethercat_alias': 0,
                'rotary_alias': 0,
            }],
            axes,
        )

        self.assertEqual(axes[0]['slave_position'], 0)
        self.assertEqual(rows[0]['controller_index'], 0)
        self.assertEqual(rows[0]['match_state'], 'matched')

    def test_ethercat_bus_poll_reports_powered_off_slaves_as_missing(self):
        self.monitor._motor_metadata = {
            0: {'transport': 'ethercat', 'ethercat_master_index': 0},
        }
        responses = iter([
            SimpleNamespace(
                returncode=0,
                stdout='Phase: Operation\nActive: yes\n  Link: UP\n',
                stderr='',
            ),
            SimpleNamespace(returncode=0, stdout='', stderr=''),
        ])

        with patch(
            'motion_state_monitor.monitor_node.subprocess.run',
            side_effect=lambda *_args, **_kwargs: next(responses),
        ):
            self.monitor._poll_ethercat_bus_status()

        self.assertTrue(self.monitor._ethercat_status['master_active'])
        self.assertTrue(self.monitor._ethercat_status['link_up'])
        self.assertEqual(self.monitor._ethercat_status['slaves_responding'], 0)
        self.assertEqual(
            self.monitor._ethercat_axis_state({'alias': 0, 'slave_position': 0}),
            '',
        )

    def test_ethercat_bus_poll_tracks_each_slave_operational_state(self):
        self.monitor._motor_metadata = {
            0: {'transport': 'ethercat', 'ethercat_master_index': 0},
        }
        responses = iter([
            SimpleNamespace(
                returncode=0,
                stdout='Phase: Operation\nActive: yes\n  Link: UP\n',
                stderr='',
            ),
            SimpleNamespace(
                returncode=0,
                stdout=(
                    '0  0:0  OP  +  Drive A\n'
                    '1  101:0  PREOP  +  Drive B\n'
                ),
                stderr='',
            ),
        ])

        with patch(
            'motion_state_monitor.monitor_node.subprocess.run',
            side_effect=lambda *_args, **_kwargs: next(responses),
        ):
            self.monitor._poll_ethercat_bus_status()

        self.assertEqual(
            self.monitor._ethercat_axis_state({'alias': 0, 'slave_position': 0}),
            'OP',
        )
        self.assertEqual(
            self.monitor._ethercat_axis_state({'alias': 101, 'slave_position': 0}),
            'PREOP',
        )

    def test_ethercat_bus_poll_keeps_same_slave_position_separate_per_master(self):
        self.monitor._motor_metadata = {
            0: {'transport': 'ethercat', 'ethercat_master_index': 0},
            5: {'transport': 'ethercat', 'ethercat_master_index': 1},
        }
        responses = iter([
            SimpleNamespace(
                returncode=0,
                stdout='Phase: Operation\nActive: yes\n  Link: UP\n',
                stderr='',
            ),
            SimpleNamespace(
                returncode=0,
                stdout='0  0:0  OP  +  Drive A\n',
                stderr='',
            ),
            SimpleNamespace(
                returncode=0,
                stdout='Phase: Operation\nActive: yes\n  Link: UP\n',
                stderr='',
            ),
            SimpleNamespace(
                returncode=0,
                stdout='0  0:0  PREOP  +  Drive B\n',
                stderr='',
            ),
        ])

        with patch(
            'motion_state_monitor.monitor_node.subprocess.run',
            side_effect=lambda *_args, **_kwargs: next(responses),
        ) as run:
            self.monitor._poll_ethercat_bus_status()

        self.assertEqual(
            [call.args[0] for call in run.call_args_list],
            [
                ['ethercat', 'master', '-m', '0'],
                ['ethercat', 'slaves', '-m', '0'],
                ['ethercat', 'master', '-m', '1'],
                ['ethercat', 'slaves', '-m', '1'],
            ],
        )
        self.assertEqual(
            self.monitor._ethercat_axis_state({
                'ethercat_master_index': 0,
                'alias': 0,
                'slave_position': 0,
            }),
            'OP',
        )
        self.assertEqual(
            self.monitor._ethercat_axis_state({
                'ethercat_master_index': 1,
                'alias': 0,
                'slave_position': 0,
            }),
            'PREOP',
        )

    def test_physical_alias_matching_is_scoped_to_master(self):
        self.monitor._last_ethercat_physical_scan = {
            'complete': True,
            'scanned_at': 12.0,
            'slaves': [
                {'master_index': 0, 'slave_position': 0, 'ethercat_alias': 101},
                {'master_index': 1, 'slave_position': 0, 'ethercat_alias': 101},
            ],
        }
        motor = {
            'transport': 'ethercat',
            'ethercat_master_index': 1,
            'slave_position': 0,
            'alias': 101,
        }

        self.monitor._set_physical_connection_fields(motor)

        self.assertEqual(motor['physical_connection_state'], 'detected')
        self.assertEqual(motor['physical_slave_position'], 0)

    def test_runtime_motor_state_is_tagged_with_owning_project(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project_dir = root / 'project-a'
            runtime_dir = project_dir / 'runtime'
            runtime_dir.mkdir(parents=True)
            (project_dir / 'project.json').write_text(
                json.dumps({'project_id': 'project-a'}), encoding='utf-8'
            )
            config = runtime_dir / 'applied_motor_config.yaml'
            config.write_text('masters: []\n', encoding='utf-8')

            self.assertEqual(
                self.monitor._project_id_from_motor_config(config),
                'project-a',
            )
            self.assertEqual(
                self.monitor._project_id_from_motor_config(root / 'global.yaml'),
                '',
            )

    def test_immutable_runtime_session_is_tagged_with_owning_project(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project_dir = root / 'project-a'
            sessions_dir = project_dir / 'runtime' / 'sessions'
            sessions_dir.mkdir(parents=True)
            (project_dir / 'project.json').write_text(
                json.dumps({'project_id': 'project-a'}), encoding='utf-8'
            )
            config = sessions_dir / 'motor-session.yaml'
            config.write_text('masters: []\n', encoding='utf-8')

            self.assertEqual(
                self.monitor._project_id_from_motor_config(config),
                'project-a',
            )

    def test_non_runtime_subdirectory_is_not_treated_as_project_runtime(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project_dir = root / 'project-a'
            other_dir = project_dir / 'runtime' / 'other'
            other_dir.mkdir(parents=True)
            (project_dir / 'project.json').write_text(
                json.dumps({'project_id': 'project-a'}), encoding='utf-8'
            )
            config = other_dir / 'motor.yaml'
            config.write_text('masters: []\n', encoding='utf-8')

            self.assertEqual(
                self.monitor._project_id_from_motor_config(config),
                '',
            )


if __name__ == '__main__':
    unittest.main()
