import unittest

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
                'mode': 'runtime_topic',
                'devices': [{'id': 5}],
            },
            scan_ethercat=False,
            scan_dynamixel=True,
        )

        self.assertEqual(rows[0]['connection_state'], 'online')
        self.assertEqual(rows[0]['discovery_state'], 'detected')
        self.assertEqual(rows[0]['discovery_source'], 'runtime_topic')

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


if __name__ == '__main__':
    unittest.main()
