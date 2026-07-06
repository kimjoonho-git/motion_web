import json
import math
import os
import re
import select
import subprocess
import termios
import time
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List, Optional

import rclpy
import yaml
from motion_control_msgs.msg import MotorStatus
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy
from std_msgs.msg import String
from std_srvs.srv import SetBool, Trigger


MOTOR_TYPE_LABELS = {
    'minas': 'AC Servo',
    'zeroerr': 'ZeroErr Motor',
    'dynamixel': 'Dynamixel',
    'cubemars': 'CubeMars',
    'unknown': 'Unknown',
}

TRANSPORT_LABELS = {
    'ethercat': 'EtherCAT',
    'canopen': 'CANopen',
    'socketcan': 'SocketCAN',
    'serial': 'Serial',
    'unknown': 'Unknown',
}

MOTOR_TYPE_CATALOG = [
    {'type': 'minas', 'label': MOTOR_TYPE_LABELS['minas']},
    {'type': 'zeroerr', 'label': MOTOR_TYPE_LABELS['zeroerr']},
    {'type': 'dynamixel', 'label': MOTOR_TYPE_LABELS['dynamixel']},
    {'type': 'cubemars', 'label': MOTOR_TYPE_LABELS['cubemars']},
    {'type': 'unknown', 'label': MOTOR_TYPE_LABELS['unknown']},
]

DYNAMIXEL_SCAN_BAUDRATES = (1000000,)
DYNAMIXEL_SCAN_MAX_ID = 50
DYNAMIXEL_SCAN_PROTOCOL = '2.0'


class MotionStateMonitor(Node):
    """Read-only monitor that converts motion_system status into /motion_control/motion_state JSON."""

    def __init__(self) -> None:
        super().__init__('motion_state_monitor')

        self.input_topic = self.declare_parameter(
            'input_topic',
            '/motion_control/motor_status',
        ).value
        self.input_type = self.declare_parameter('input_type', 'motor_status').value
        self.ethercat_status_topic = self.declare_parameter(
            'ethercat_status_topic',
            '/ethercat_status',
        ).value
        self.motor_config_file = self.declare_parameter('motor_config_file', '').value
        self.output_topic = self.declare_parameter(
            'output_topic',
            '/motion_control/motion_state',
        ).value
        self.publish_hz = float(self.declare_parameter('publish_hz', 10.0).value)
        self.max_motors = int(self.declare_parameter('max_motors', 50).value)
        self.stale_timeout_sec = float(self.declare_parameter('stale_timeout_sec', 0.5).value)
        self.disconnected_timeout_sec = float(
            self.declare_parameter('disconnected_timeout_sec', 2.0).value
        )
        self.dynamixel_scan_max_id = int(
            self.declare_parameter('dynamixel_scan_max_id', DYNAMIXEL_SCAN_MAX_ID).value
        )
        self.dynamixel_scan_timeout_sec = float(
            self.declare_parameter('dynamixel_scan_timeout_sec', 0.08).value
        )
        self.dynamixel_broadcast_timeout_sec = float(
            self.declare_parameter('dynamixel_broadcast_timeout_sec', 0.5).value
        )
        self.dynamixel_scan_attempts = int(
            self.declare_parameter('dynamixel_scan_attempts', 2).value
        )
        self.dynamixel_scan_settle_sec = float(
            self.declare_parameter('dynamixel_scan_settle_sec', 0.05).value
        )
        self.dynamixel_scan_id_fallback = bool(
            self.declare_parameter('dynamixel_scan_id_fallback', True).value
        )
        self.monitoring_enabled = bool(
            self.declare_parameter('monitoring_enabled', True).value
        )

        self._motors: Dict[int, Dict[str, Any]] = {}
        self._motor_metadata: Dict[int, Dict[str, Any]] = {}
        self._ethercat_status: Dict[str, Any] = {}
        self._last_motor_status_at: Optional[float] = None
        self._last_ethercat_status_at: Optional[float] = None
        self._last_disabled_publish_at = 0.0
        self._subscription = None

        self._publisher = self.create_publisher(String, self.output_topic, 10)
        self._input_qos = QoSProfile(depth=10, reliability=ReliabilityPolicy.BEST_EFFORT)
        self._service = self.create_service(SetBool, 'set_monitoring', self._set_monitoring)
        self._scan_service = self.create_service(Trigger, 'scan_motors', self._scan_motors)
        self._scan_ac_servo_service = self.create_service(
            Trigger,
            'scan_ac_servo_motors',
            self._scan_ac_servo_motors,
        )
        self._scan_dynamixel_service = self.create_service(
            Trigger,
            'scan_dynamixel_motors',
            self._scan_dynamixel_motors_service,
        )
        self._load_motor_metadata()

        if self.monitoring_enabled:
            self._create_input_subscription()

        period_sec = 1.0 / max(self.publish_hz, 0.1)
        self._timer = self.create_timer(period_sec, self._publish_motion_state)

        self.get_logger().info(
            f'motion_state_monitor started: input={self.input_topic}, '
            f'input_type={self.input_type}, ethercat_status={self.ethercat_status_topic}, '
            f'motor_config_file={self.motor_config_file or "(none)"}, '
            f'output={self.output_topic}, max_motors={self.max_motors}'
        )

    def _load_motor_metadata(self) -> None:
        self._motor_metadata = {}
        if not self.motor_config_file:
            self.get_logger().warn(
                'motor_config_file is empty; motor_type and transport will be Unknown.'
            )
            return

        path = Path(str(self.motor_config_file)).expanduser()
        if not path.is_file():
            self.get_logger().warn(
                f'motor_config_file not found: {path}; motor_type and transport will be Unknown.'
            )
            return

        try:
            with path.open('r', encoding='utf-8') as file:
                config = yaml.safe_load(file) or {}
        except (OSError, yaml.YAMLError) as exc:
            self.get_logger().warn(f'Failed to read motor_config_file: {exc}')
            return

        drivers_by_id = {
            int(driver.get('id')): driver
            for driver in config.get('drivers', [])
            if isinstance(driver, dict) and driver.get('id') is not None
        }

        for master in config.get('masters', []):
            if not isinstance(master, dict):
                continue
            transport = str(master.get('type', 'unknown'))
            master_id = master.get('id')
            serial_port = master.get('serial_port')
            serial_baudrate = master.get('serial_baudrate')
            for slave in master.get('slaves', []):
                if not isinstance(slave, dict) or slave.get('controller_index') is None:
                    continue
                controller_index = int(slave.get('controller_index'))
                driver = drivers_by_id.get(int(slave.get('driver_id', -1)), {})
                motor_type = str(driver.get('type', 'unknown'))
                driver_model = str(
                    driver.get('driver_model')
                    or driver.get('model_name')
                    or driver.get('model')
                    or ''
                )
                display_name = str(slave.get('name') or f'Axis {controller_index}')
                raw_model = self._dynamixel_raw_model_info(driver) if motor_type == 'dynamixel' else {}
                pulse_per_revolution = driver.get('pulse_per_revolution')
                self._motor_metadata[controller_index] = {
                    'display_name': display_name,
                    'motor_type': motor_type,
                    'motor_type_label': self._motor_type_label(motor_type),
                    'transport': transport,
                    'transport_label': self._transport_label(transport),
                    'master_id': master_id,
                    'driver_id': slave.get('driver_id'),
                    'driver_model': driver_model,
                    'pulse_per_revolution': pulse_per_revolution,
                    **raw_model,
                    'rated_power_w': self._optional_float(driver.get('rated_power_w')),
                    'rated_torque_nm': self._optional_float(
                        driver.get('rated_effort', driver.get('rated_torque'))
                    ),
                    'rated_current_a': self._optional_float(driver.get('rated_current')),
                    'rated_speed_rpm': self._optional_float(driver.get('rated_speed_rpm')),
                    'speed': self._optional_float(driver.get('speed')),
                    'acceleration': self._optional_float(driver.get('acceleration')),
                    'deceleration': self._optional_float(driver.get('deceleration')),
                    'profile_velocity': self._optional_float(driver.get('profile_velocity')),
                    'profile_acceleration': self._optional_float(driver.get('profile_acceleration')),
                    'profile_deceleration': self._optional_float(driver.get('profile_deceleration')),
                    'lower': self._optional_float(driver.get('lower')),
                    'upper': self._optional_float(driver.get('upper')),
                    'alias': slave.get('alias'),
                    'node_id': slave.get('node_id', slave.get('bus_id')),
                    'bus_id': slave.get('bus_id'),
                    'serial_port': serial_port,
                    'serial_baudrate': serial_baudrate,
                }

        self.get_logger().info(
            f'Loaded motor metadata for {len(self._motor_metadata)} axes from {path}.'
        )

    def _dynamixel_raw_model_info(self, driver: Dict[str, Any]) -> Dict[str, Any]:
        info: Dict[str, Any] = {}
        pulse_per_revolution = self._optional_float(driver.get('pulse_per_revolution'))
        if pulse_per_revolution is not None and pulse_per_revolution > 0:
            info['position_raw_per_degree'] = pulse_per_revolution / 360.0

        param_file = str(driver.get('param_file') or '').strip()
        if not param_file:
            if pulse_per_revolution is not None and pulse_per_revolution > 0:
                info['dynamixel_zero_position_raw'] = pulse_per_revolution / 2.0
            return info

        param_path = Path(param_file).expanduser()
        info['dynamixel_param_file'] = str(param_path)
        try:
            param = yaml.safe_load(param_path.read_text(encoding='utf-8')) or {}
        except (OSError, yaml.YAMLError) as exc:
            self.get_logger().warn(f'Failed to read Dynamixel param file {param_path}: {exc}')
            return info

        if not isinstance(param, dict):
            return info

        model_file = str(param.get('model_file') or '').strip()
        if not model_file:
            return info

        model_path = Path(model_file).expanduser()
        info['dynamixel_model_file'] = str(model_path)
        model_info = self._read_dynamixel_model_file(model_path)
        info.update(model_info)

        min_raw = self._optional_float(info.get('dynamixel_min_position_raw'))
        max_raw = self._optional_float(info.get('dynamixel_max_position_raw'))
        min_rad = self._optional_float(info.get('dynamixel_min_radian'))
        max_rad = self._optional_float(info.get('dynamixel_max_radian'))
        if (
            min_raw is not None
            and max_raw is not None
            and min_rad is not None
            and max_rad is not None
            and not math.isclose(max_raw, min_raw)
        ):
            degrees = (max_rad - min_rad) * 180.0 / math.pi
            info['position_raw_per_degree'] = (max_raw - min_raw) / degrees

        return info

    def _read_dynamixel_model_file(self, path: Path) -> Dict[str, Any]:
        try:
            lines = path.read_text(encoding='utf-8').splitlines()
        except OSError as exc:
            self.get_logger().warn(f'Failed to read Dynamixel model file {path}: {exc}')
            return {}

        mapping = {
            'value_of_zero_radian_position': 'dynamixel_zero_position_raw',
            'value_of_max_radian_position': 'dynamixel_max_position_raw',
            'value_of_min_radian_position': 'dynamixel_min_position_raw',
            'min_radian': 'dynamixel_min_radian',
            'max_radian': 'dynamixel_max_radian',
        }
        result: Dict[str, Any] = {}
        for raw_line in lines:
            line = raw_line.strip()
            if not line or line.startswith('['):
                continue
            parts = line.split()
            if len(parts) < 2:
                continue
            key = mapping.get(parts[0])
            if not key:
                continue
            value = self._optional_float(parts[1])
            if value is not None:
                result[key] = value
        return result

    def _create_input_subscription(self) -> None:
        if self._subscription is None:
            self._subscription = self.create_subscription(
                MotorStatus,
                self.input_topic,
                self._motor_status_callback,
                self._input_qos,
            )

    def _destroy_motor_status_subscription(self) -> None:
        if self._subscription is not None:
            self.destroy_subscription(self._subscription)
            self._subscription = None

    def _set_monitoring(self, request: SetBool.Request, response: SetBool.Response):
        self.monitoring_enabled = bool(request.data)
        if self.monitoring_enabled:
            self._create_input_subscription()
            response.message = 'monitoring enabled'
        else:
            self._destroy_motor_status_subscription()
            response.message = 'monitoring disabled'
        response.success = True
        self.get_logger().info(response.message)
        return response

    def _scan_motors(self, request: Trigger.Request, response: Trigger.Response):
        del request
        result = self._build_scan_result(scan_ethercat=True, scan_dynamixel=True)
        response.success = self.monitoring_enabled
        response.message = json.dumps(result, ensure_ascii=False, separators=(',', ':'))
        if not self.monitoring_enabled:
            self.get_logger().warn('motor scan requested while monitoring is disabled.')
        return response

    def _scan_ac_servo_motors(self, request: Trigger.Request, response: Trigger.Response):
        del request
        result = self._build_scan_result(scan_ethercat=True, scan_dynamixel=False)
        response.success = self.monitoring_enabled
        response.message = json.dumps(result, ensure_ascii=False, separators=(',', ':'))
        if not self.monitoring_enabled:
            self.get_logger().warn('AC Servo scan requested while monitoring is disabled.')
        return response

    def _scan_dynamixel_motors_service(self, request: Trigger.Request, response: Trigger.Response):
        del request
        result = self._build_scan_result(scan_ethercat=False, scan_dynamixel=True)
        response.success = self.monitoring_enabled
        response.message = json.dumps(result, ensure_ascii=False, separators=(',', ':'))
        if not self.monitoring_enabled:
            self.get_logger().warn('Dynamixel scan requested while monitoring is disabled.')
        return response

    def _build_scan_result(
        self,
        *,
        scan_ethercat: bool,
        scan_dynamixel: bool,
    ) -> Dict[str, Any]:
        now = time.time()
        ethercat = self._current_ethercat_status(now)
        motors = self._current_motor_list(now)
        configured_axes = self._configured_axis_list(motors)
        ethercat_scan = (
            self._safe_scan_ethercat_slaves()
            if scan_ethercat
            else self._skipped_ethercat_scan(now)
        )
        dynamixel_scan = (
            self._safe_scan_dynamixel_motors()
            if scan_dynamixel
            else self._skipped_dynamixel_scan(now)
        )
        matching_rows = (
            self._build_matching_rows(ethercat_scan.get('slaves', []), configured_axes)
            if scan_ethercat
            else []
        )
        connected_axes = [
            {
                'controller_index': motor['controller_index'],
                'display_name': motor.get('display_name', f"Axis {motor['controller_index']}"),
                'motor_type': motor.get('motor_type', 'unknown'),
                'motor_type_label': motor.get('motor_type_label', 'Unknown'),
                'transport': motor.get('transport', 'unknown'),
                'transport_label': motor.get('transport_label', 'Unknown'),
                'state': motor.get('state', 'unknown'),
                'state_detail': motor.get('state_detail', ''),
                'fault': bool(motor.get('fault', False)),
                'servo_on': bool(motor.get('servo_on', False)),
                'last_seen_at': motor.get('last_seen_at'),
                'age_sec': motor.get('age_sec'),
            }
            for motor in motors
            if motor.get('state') == 'detected'
        ]
        result = {
            'scanned_at': now,
            'monitoring_enabled': self.monitoring_enabled,
            'source_topic': self.input_topic,
            'ethercat_status_topic': self.ethercat_status_topic,
            'ethercat': ethercat,
            'known_axes_count': len(motors),
            'connected_axes_count': len(connected_axes),
            'online_motors_count': len(connected_axes),
            'motor_type_counts': self._count_values(motors, 'motor_type_label'),
            'transport_counts': self._count_values(motors, 'transport_label'),
            'ethercat_scan': ethercat_scan,
            'dynamixel_scan': dynamixel_scan,
            'matching_rows': matching_rows,
            'matching_summary': self._matching_summary(matching_rows),
            'connected_axes': connected_axes,
            'known_axes': configured_axes,
        }
        return result

    def _safe_scan_ethercat_slaves(self) -> Dict[str, Any]:
        try:
            return self._scan_ethercat_slaves()
        except Exception as exc:
            self.get_logger().error(f'EtherCAT scan failed: {exc}')
            return {
                'available': False,
                'scanned_at': time.time(),
                'error': str(exc),
                'slaves': [],
            }

    def _safe_scan_dynamixel_motors(self) -> Dict[str, Any]:
        try:
            return self._scan_dynamixel_motors()
        except Exception as exc:
            self.get_logger().error(f'Dynamixel scan failed: {exc}')
            return {
                'available': False,
                'scanned_at': time.time(),
                'mode': 'direct_ping',
                'protocol': DYNAMIXEL_SCAN_PROTOCOL,
                'scan_rule': 'auto serial port, baudrate 1000000, broadcast ping plus ID 0-50',
                'error': str(exc),
                'targets': [],
                'devices_count': 0,
                'devices': [],
                'runtime_devices': self._dynamixel_runtime_devices(),
            }

    @staticmethod
    def _skipped_ethercat_scan(scanned_at: float) -> Dict[str, Any]:
        return {
            'available': False,
            'skipped': True,
            'scanned_at': scanned_at,
            'error': '',
            'slaves_count': 0,
            'slaves': [],
        }

    def _skipped_dynamixel_scan(self, scanned_at: float) -> Dict[str, Any]:
        return {
            'available': False,
            'skipped': True,
            'scanned_at': scanned_at,
            'mode': 'direct_ping',
            'protocol': DYNAMIXEL_SCAN_PROTOCOL,
            'scan_rule': 'auto serial port, baudrate 1000000, broadcast ping plus ID 0-50',
            'error': '',
            'targets': [],
            'devices_count': 0,
            'devices': [],
            'runtime_devices': self._dynamixel_runtime_devices(),
        }

    def _motor_status_callback(self, msg: MotorStatus) -> None:
        if not self.monitoring_enabled:
            return

        now = time.time()
        self._last_motor_status_at = now

        controller_indices = list(getattr(msg, 'controller_index', []))
        count = min(len(controller_indices), self.max_motors)
        if len(controller_indices) > self.max_motors:
            self.get_logger().warn(
                f'{self.input_topic} contains {len(controller_indices)} motors; '
                f'only first {self.max_motors} are monitored.'
            )

        for i in range(count):
            controller_index = int(controller_indices[i])
            self._motors[controller_index] = self._motor_from_status(
                msg,
                i,
                controller_index,
                now,
            )

    def _motor_from_status(
        self,
        msg: MotorStatus,
        index: int,
        controller_index: int,
        now: float,
    ) -> Dict[str, Any]:
        statusword = int(self._array_value(msg, 'statusword', index, 0))
        metadata = self._metadata_for(controller_index)
        position = float(self._array_value(msg, 'position', index, 0.0))
        velocity = float(self._array_value(msg, 'velocity', index, 0.0))
        effort = float(self._array_value(msg, 'effort', index, 0.0))
        raw_errorcode = int(self._array_value(msg, 'errorcode', index, 0))
        errorcode = self._normalized_errorcode(raw_errorcode, metadata)
        motor_type = str(metadata.get('motor_type', '')).lower()
        is_dynamixel = motor_type == 'dynamixel'
        status_text = (
            self._dynamixel_statusword_text(statusword)
            if is_dynamixel
            else self._statusword_text(statusword)
        )
        servo_on = bool(statusword & 0x01) if is_dynamixel else (statusword & 0x006F) == 0x0027
        fault = bool(errorcode) if is_dynamixel else bool(statusword & 0x0008)
        position_raw = (
            self._calculated_dynamixel_position_raw(position, metadata)
            if is_dynamixel
            else None
        )
        return {
            'controller_index': controller_index,
            'display_name': f'Axis {controller_index}',
            **metadata,
            'driver_name': str(metadata.get('driver_model') or ''),
            'configuration_state': (
                'configured' if controller_index in self._motor_metadata else 'unconfigured'
            ),
            'state': 'detected',
            'last_seen_at': now,
            'age_sec': 0.0,
            'controlword': int(self._array_value(msg, 'controlword', index, 0)),
            'statusword': statusword,
            'status_text': status_text,
            'errorcode': errorcode,
            'errorcode_raw': raw_errorcode,
            'errorcode_hex': self._hex16(raw_errorcode),
            'error_text': self._error_text(errorcode, ''),
            'station_alias_register': None,
            'position': position,
            'position_deg': position,
            'velocity': velocity,
            'velocity_deg_s': velocity,
            'torque': None if is_dynamixel else effort,
            'current': effort if is_dynamixel else None,
            'position_raw': position_raw,
            'velocity_raw': None,
            'torque_raw': None,
            'current_raw': None,
            'servo_on': servo_on,
            'target_reached': None if is_dynamixel else bool(statusword & 0x0400),
            'fault': fault,
        }

    def _calculated_dynamixel_position_raw(
        self,
        position_deg: float,
        metadata: Dict[str, Any],
    ) -> Optional[int]:
        raw_per_degree = self._optional_float(metadata.get('position_raw_per_degree'))
        if raw_per_degree is None or math.isclose(raw_per_degree, 0.0):
            pulse_per_revolution = self._optional_float(metadata.get('pulse_per_revolution'))
            if pulse_per_revolution is None or pulse_per_revolution <= 0:
                return None
            raw_per_degree = pulse_per_revolution / 360.0

        zero_raw = self._optional_float(metadata.get('dynamixel_zero_position_raw'))
        if zero_raw is None:
            pulse_per_revolution = self._optional_float(metadata.get('pulse_per_revolution'))
            zero_raw = pulse_per_revolution / 2.0 if pulse_per_revolution else 0.0

        raw = int(round(zero_raw + (position_deg * raw_per_degree)))
        min_raw = self._optional_float(metadata.get('dynamixel_min_position_raw'))
        max_raw = self._optional_float(metadata.get('dynamixel_max_position_raw'))
        if min_raw is not None and max_raw is not None:
            lower = int(round(min(min_raw, max_raw)))
            upper = int(round(max(min_raw, max_raw)))
            raw = max(lower, min(upper, raw))
        return raw

    def _publish_motion_state(self) -> None:
        now = time.time()
        if not self.monitoring_enabled:
            if now - self._last_disabled_publish_at < 1.0:
                return
            self._last_disabled_publish_at = now

        motors = self._current_motor_list(now)
        state = {
            'schema_version': 1,
            'generated_at': now,
            'monitoring_enabled': self.monitoring_enabled,
            'source_topic': self.input_topic,
            'ethercat_status_topic': self.ethercat_status_topic,
            'last_motor_status_at': self._last_motor_status_at,
            'last_ethercat_status_at': self._last_ethercat_status_at,
            'ethercat': self._current_ethercat_status(now),
            'motor_type_catalog': MOTOR_TYPE_CATALOG,
            'stale_timeout_sec': self.stale_timeout_sec,
            'disconnected_timeout_sec': self.disconnected_timeout_sec,
            'max_motors': self.max_motors,
            'detected_count': len([m for m in motors if m['state'] == 'detected']),
            'motor_count': len(motors),
            'known_motors_count': len(motors),
            'online_motors_count': len([m for m in motors if m['state'] == 'detected']),
            'motor_type_counts': self._count_values(motors, 'motor_type_label'),
            'transport_counts': self._count_values(motors, 'transport_label'),
            'motors': motors,
        }

        msg = String()
        msg.data = json.dumps(state, ensure_ascii=False, separators=(',', ':'))
        self._publisher.publish(msg)

    def _current_motor_list(self, now: float) -> List[Dict[str, Any]]:
        motors: List[Dict[str, Any]] = []
        configured_indices = set(self._motor_metadata)
        if not configured_indices:
            return motors

        ethercat = self._current_ethercat_status(now)
        ethercat_available = bool(self._ethercat_status)
        ethercat_down = ethercat_available and (
            not ethercat.get('master_active', False) or not ethercat.get('link_up', False)
        )
        slaves_responding = int(ethercat.get('slaves_responding') or 0)

        for controller_index in sorted(configured_indices):
            if controller_index not in self._motors:
                state = 'monitoring_off' if not self.monitoring_enabled else 'disconnected'
                motors.append(self._configured_motor_placeholder(controller_index, state))
                continue

            motor = deepcopy(self._motors[controller_index])
            age = now - float(motor.get('last_seen_at', now))
            motor['age_sec'] = round(age, 3)
            if not self.monitoring_enabled:
                state = 'monitoring_off'
            elif ethercat_down:
                state = 'ethercat_down'
            elif ethercat_available and controller_index >= slaves_responding:
                state = 'disconnected'
            elif age >= self.disconnected_timeout_sec:
                state = 'disconnected'
            elif age >= self.stale_timeout_sec:
                state = 'stale'
            else:
                state = 'detected'
            motor['state'] = state
            motor['state_detail'] = self._state_detail(state)
            motor['configuration_state'] = 'configured'
            motors.append(motor)
        return motors

    def _configured_motor_placeholder(
        self,
        controller_index: int,
        state: str,
    ) -> Dict[str, Any]:
        metadata = self._metadata_for(controller_index)
        return {
            'controller_index': controller_index,
            'display_name': f'Axis {controller_index}',
            **metadata,
            'driver_name': str(metadata.get('driver_model') or ''),
            'configuration_state': 'configured',
            'state': state,
            'last_seen_at': None,
            'age_sec': None,
            'controlword': None,
            'statusword': None,
            'status_text': 'No runtime state',
            'errorcode': 0,
            'errorcode_raw': 0,
            'errorcode_hex': self._hex16(0),
            'error_text': 'No error',
            'station_alias_register': None,
            'position': None,
            'position_deg': None,
            'velocity': None,
            'velocity_deg_s': None,
            'torque': None,
            'current': None,
            'position_raw': None,
            'velocity_raw': None,
            'torque_raw': None,
            'current_raw': None,
            'servo_on': False,
            'target_reached': False,
            'fault': False,
            'state_detail': self._state_detail(state),
        }

    def _current_ethercat_status(self, now: float) -> Dict[str, Any]:
        if not self._ethercat_status:
            return {
                'available': False,
                'age_sec': None,
                'master_active': None,
                'link_up': None,
                'slaves_responding': None,
                'phase': '',
                'state_text': '',
            }

        status = deepcopy(self._ethercat_status)
        status['available'] = True
        status['age_sec'] = round(now - float(status.get('last_seen_at', now)), 3)
        return status

    def _configured_axis_list(self, motors: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        axes_by_index: Dict[int, Dict[str, Any]] = {}
        for controller_index in self._motor_metadata:
            metadata = self._metadata_for(controller_index)
            axes_by_index[controller_index] = {
                'controller_index': controller_index,
                'display_name': metadata.get('display_name', f'Axis {controller_index}'),
                'motor_type': metadata.get('motor_type', 'unknown'),
                'motor_type_label': metadata.get('motor_type_label', 'Unknown'),
                'transport': metadata.get('transport', 'unknown'),
                'transport_label': metadata.get('transport_label', 'Unknown'),
                'driver_model': metadata.get('driver_model', ''),
                'rated_power_w': metadata.get('rated_power_w'),
                'ethercat_alias': metadata.get('alias'),
                'state': 'configured',
                'state_detail': '설정 파일에 등록된 축입니다.',
                'fault': False,
                'age_sec': None,
                'station_alias_register': None,
            }

        for motor in motors:
            controller_index = int(motor['controller_index'])
            axes_by_index[controller_index] = {
                'controller_index': controller_index,
                'display_name': motor.get('display_name', f'Axis {controller_index}'),
                'motor_type': motor.get('motor_type', 'unknown'),
                'motor_type_label': motor.get('motor_type_label', 'Unknown'),
                'transport': motor.get('transport', 'unknown'),
                'transport_label': motor.get('transport_label', 'Unknown'),
                'driver_model': motor.get('driver_model', ''),
                'driver_name': motor.get('driver_name', ''),
                'rated_power_w': motor.get('rated_power_w'),
                'ethercat_alias': motor.get('alias'),
                'state': motor.get('state', 'unknown'),
                'state_detail': motor.get('state_detail', ''),
                'fault': bool(motor.get('fault', False)),
                'age_sec': motor.get('age_sec'),
                'station_alias_register': motor.get('station_alias_register'),
            }

        return [axes_by_index[index] for index in sorted(axes_by_index)]

    def _scan_ethercat_slaves(self) -> Dict[str, Any]:
        started_at = time.time()
        try:
            completed = subprocess.run(
                ['ethercat', 'slaves', '-v'],
                check=False,
                capture_output=True,
                text=True,
                timeout=2.0,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {
                'available': False,
                'scanned_at': started_at,
                'error': str(exc),
                'slaves': [],
            }

        if completed.returncode != 0:
            return {
                'available': False,
                'scanned_at': started_at,
                'error': completed.stderr.strip() or completed.stdout.strip(),
                'slaves': [],
            }

        slaves = self._parse_ethercat_slaves(completed.stdout)
        for slave in slaves:
            rotary = self._read_station_alias_register(slave['slave_position'])
            slave.update(rotary)

        return {
            'available': True,
            'scanned_at': started_at,
            'error': '',
            'slaves_count': len(slaves),
            'slaves': slaves,
        }

    def _parse_ethercat_slaves(self, output: str) -> List[Dict[str, Any]]:
        slaves: List[Dict[str, Any]] = []
        current: Optional[Dict[str, Any]] = None
        header_pattern = re.compile(r'^===\s+Master\s+(\d+),\s+Slave\s+(\d+)\s+===$')

        for raw_line in output.splitlines():
            line = raw_line.strip()
            header = header_pattern.match(line)
            if header:
                if current is not None:
                    slaves.append(current)
                current = {
                    'master_index': int(header.group(1)),
                    'slave_position': int(header.group(2)),
                    'ethercat_alias': None,
                    'device_state': '',
                    'vendor_id': None,
                    'product_code': None,
                    'serial_number': None,
                    'order_number': '',
                    'device_name': '',
                }
                continue

            if current is None or ':' not in line:
                continue

            key, value = [part.strip() for part in line.split(':', 1)]
            first_value = value.split()[0] if value else ''
            if key == 'Alias':
                current['ethercat_alias'] = self._parse_int(first_value)
            elif key == 'State':
                current['device_state'] = first_value
            elif key == 'Vendor Id':
                current['vendor_id'] = self._parse_int(first_value)
            elif key == 'Product code':
                current['product_code'] = self._parse_int(first_value)
            elif key == 'Serial number':
                current['serial_number'] = self._parse_int(first_value)
            elif key == 'Order number':
                current['order_number'] = value
            elif key == 'Device name':
                current['device_name'] = value

        if current is not None:
            slaves.append(current)
        return slaves

    def _read_station_alias_register(self, slave_position: int) -> Dict[str, Any]:
        try:
            completed = subprocess.run(
                [
                    'ethercat',
                    'reg_read',
                    '-p',
                    str(slave_position),
                    '-t',
                    'uint16',
                    '0x0012',
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=1.0,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {
                'rotary_alias': None,
                'rotary_alias_hex': '',
                'rotary_alias_error': str(exc),
            }

        if completed.returncode != 0:
            return {
                'rotary_alias': None,
                'rotary_alias_hex': '',
                'rotary_alias_error': completed.stderr.strip() or completed.stdout.strip(),
            }

        parts = completed.stdout.strip().split()
        raw_hex = parts[0] if parts else ''
        value = self._parse_int(parts[-1]) if parts else None
        if raw_hex.lower().startswith('0x'):
            raw_hex = '0x' + raw_hex[2:].upper()
        return {
            'rotary_alias': value,
            'rotary_alias_hex': raw_hex,
            'rotary_alias_error': '',
        }

    def _scan_dynamixel_motors(self) -> Dict[str, Any]:
        started_at = time.time()
        targets = self._dynamixel_scan_targets()
        runtime_devices = self._dynamixel_runtime_devices()
        runtime_active = (
            self._last_motor_status_at is not None and
            (time.time() - self._last_motor_status_at) <= self.disconnected_timeout_sec
        )

        if not targets:
            return {
                'available': False,
                'scanned_at': started_at,
                'mode': 'direct_ping',
                'protocol': DYNAMIXEL_SCAN_PROTOCOL,
                'scan_rule': 'auto serial port, baudrate 1000000, broadcast ping plus ID 0-50',
                'error': (
                    'No Dynamixel serial port found. Checked /dev/serial/by-id, '
                    '/dev/ttyUSB*, and /dev/ttyACM*.'
                ),
                'targets': [],
                'devices_count': len(runtime_devices),
                'devices': [],
                'runtime_devices': runtime_devices,
            }

        devices: List[Dict[str, Any]] = []
        errors: List[str] = []
        warnings: List[str] = []
        if runtime_active:
            warnings.append(
                'motor_manager_node runtime topic is active; direct Dynamixel ping scan was still executed.'
            )
        for target in targets:
            port = str(target.get('port') or '')
            baudrate = int(target.get('baudrate') or 0)
            ids = list(target.get('ids') or [])
            if not port or not baudrate:
                continue
            try:
                fd = self._open_dynamixel_port(port, baudrate)
            except OSError as exc:
                errors.append(f'{port}@{baudrate}: {exc}')
                continue
            try:
                devices_by_id: Dict[int, Dict[str, Any]] = {}
                attempts = max(1, int(self.dynamixel_scan_attempts))
                for attempt in range(attempts):
                    if self.dynamixel_scan_settle_sec > 0:
                        time.sleep(float(self.dynamixel_scan_settle_sec))
                    for device in self._broadcast_ping_dynamixel(
                        fd,
                        float(self.dynamixel_broadcast_timeout_sec),
                    ):
                        if ids and int(device['id']) not in ids:
                            continue
                        devices_by_id[int(device['id'])] = device

                    if self.dynamixel_scan_id_fallback:
                        for dxl_id in ids:
                            current = devices_by_id.get(int(dxl_id))
                            if current is not None and self._dynamixel_device_has_valid_model(current):
                                continue
                            device = self._ping_dynamixel_id(
                                fd,
                                int(dxl_id),
                                float(self.dynamixel_scan_timeout_sec),
                            )
                            if device is None:
                                continue
                            devices_by_id[int(device['id'])] = device
                    if attempt >= attempts - 1:
                        break

                self._merge_runtime_dynamixel_devices(devices_by_id, runtime_devices)
                for device in devices_by_id.values():
                    device.update({
                        'port': port,
                        'baudrate': baudrate,
                        'model_name': (
                            device.get('model_name')
                            or self._dynamixel_model_name(device.get('model_number'))
                        ),
                    })
                    devices.append(device)
            finally:
                os.close(fd)

        return {
            'available': True,
            'scanned_at': started_at,
            'mode': 'direct_ping',
            'protocol': DYNAMIXEL_SCAN_PROTOCOL,
            'scan_rule': 'auto serial port, baudrate 1000000, broadcast ping plus ID 0-50',
            'error': '; '.join(errors),
            'warning': '; '.join(warnings),
            'targets': targets,
            'attempts': max(1, int(self.dynamixel_scan_attempts)),
            'id_fallback': bool(self.dynamixel_scan_id_fallback),
            'devices_count': len(devices),
            'devices': devices,
            'runtime_devices': runtime_devices,
        }

    def _dynamixel_runtime_devices(self) -> List[Dict[str, Any]]:
        devices: List[Dict[str, Any]] = []
        for motor in self._current_motor_list(time.time()):
            controller_index = int(motor.get('controller_index'))
            metadata = self._metadata_for(controller_index)
            if str(metadata.get('motor_type', '')).lower() != 'dynamixel':
                continue
            bus_id = metadata.get('bus_id', metadata.get('node_id'))
            if bus_id is None:
                continue
            devices.append({
                'id': bus_id,
                'controller_index': controller_index,
                'port': metadata.get('serial_port'),
                'baudrate': metadata.get('serial_baudrate'),
                'model_name': metadata.get('driver_model') or '',
                'model_number': None,
                'firmware_version': None,
                'state': motor.get('state', 'unknown'),
                'last_seen_at': motor.get('last_seen_at'),
                'age_sec': motor.get('age_sec'),
                'source': 'runtime',
            })
        return devices

    @staticmethod
    def _dynamixel_device_has_valid_model(device: Dict[str, Any]) -> bool:
        model_number = device.get('model_number')
        try:
            return int(model_number) > 0
        except (TypeError, ValueError):
            return False

    def _merge_runtime_dynamixel_devices(
        self,
        devices_by_id: Dict[int, Dict[str, Any]],
        runtime_devices: List[Dict[str, Any]],
    ) -> None:
        for runtime in runtime_devices:
            try:
                dxl_id = int(runtime.get('id'))
            except (TypeError, ValueError):
                continue
            if runtime.get('state') != 'detected':
                continue
            current = devices_by_id.get(dxl_id)
            if current is None:
                devices_by_id[dxl_id] = {
                    'id': dxl_id,
                    'packet_error': 0,
                    'model_number': runtime.get('model_number'),
                    'firmware_version': runtime.get('firmware_version'),
                    'model_name': runtime.get('model_name') or '',
                    'source': 'runtime_fallback',
                }
                continue
            if not current.get('model_name') and runtime.get('model_name'):
                current['model_name'] = runtime.get('model_name')
            if not self._dynamixel_device_has_valid_model(current) and runtime.get('model_number'):
                current['model_number'] = runtime.get('model_number')

    def _dynamixel_scan_targets(self) -> List[Dict[str, Any]]:
        path = Path(str(self.motor_config_file)).expanduser() if self.motor_config_file else None
        config: Dict[str, Any] = {}
        if path is not None and path.is_file():
            try:
                with path.open('r', encoding='utf-8') as file:
                    loaded = yaml.safe_load(file) or {}
                if isinstance(loaded, dict):
                    config = loaded
            except (OSError, yaml.YAMLError) as exc:
                self.get_logger().warn(f'Failed to read Dynamixel scan YAML: {exc}')

        ports = self._dynamixel_serial_ports(config)
        scan_max_id = DYNAMIXEL_SCAN_MAX_ID
        ids = list(range(0, scan_max_id + 1))
        for master in config.get('masters', []):
            if not isinstance(master, dict):
                continue
            if str(master.get('type', '')).lower() not in {'serial', 'dynamixel'}:
                continue
            port = str(master.get('serial_port') or '')
            if port and os.path.exists(port):
                self._append_dynamixel_port(ports, port, 'yaml')

        targets: List[Dict[str, Any]] = []
        for port_info in ports:
            for baudrate in DYNAMIXEL_SCAN_BAUDRATES:
                targets.append({
                    'port': port_info['port'],
                    'port_source': port_info['source'],
                    'baudrate': baudrate,
                    'protocol': DYNAMIXEL_SCAN_PROTOCOL,
                    'ids': ids,
                    'id_range': f'broadcast ping plus ID 0-{scan_max_id}',
                })
        return targets

    def _dynamixel_serial_ports(self, config: Dict[str, Any]) -> List[Dict[str, str]]:
        ports: List[Dict[str, str]] = []
        by_id_dir = Path('/dev/serial/by-id')
        if by_id_dir.is_dir():
            for path in sorted(by_id_dir.iterdir(), key=lambda item: item.name):
                if path.exists():
                    self._append_dynamixel_port(ports, str(path), 'auto:/dev/serial/by-id')

        for pattern, source in (
            ('ttyUSB*', 'auto:/dev/ttyUSB'),
            ('ttyACM*', 'auto:/dev/ttyACM'),
        ):
            for path in sorted(Path('/dev').glob(pattern), key=lambda item: item.name):
                if path.exists():
                    self._append_dynamixel_port(ports, str(path), source)

        for master in config.get('masters', []):
            if not isinstance(master, dict):
                continue
            if str(master.get('type', '')).lower() not in {'serial', 'dynamixel'}:
                continue
            port = str(master.get('serial_port') or '')
            if port and os.path.exists(port):
                self._append_dynamixel_port(ports, port, 'yaml')
        return ports

    @staticmethod
    def _append_dynamixel_port(
        ports: List[Dict[str, str]],
        port: str,
        source: str,
    ) -> None:
        try:
            resolved = str(Path(port).resolve(strict=False))
        except OSError:
            resolved = port
        if any(item.get('resolved') == resolved for item in ports):
            return
        ports.append({
            'port': port,
            'resolved': resolved,
            'source': source,
        })

    @staticmethod
    def _open_dynamixel_port(port: str, baudrate: int) -> int:
        fd = os.open(port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        try:
            speed = getattr(termios, f'B{baudrate}')
            attrs = termios.tcgetattr(fd)
            attrs[0] = termios.IGNPAR
            attrs[1] = 0
            attrs[2] = speed | termios.CS8 | termios.CLOCAL | termios.CREAD
            attrs[3] = 0
            attrs[4] = speed
            attrs[5] = speed
            attrs[6][termios.VTIME] = 0
            attrs[6][termios.VMIN] = 0
            termios.tcflush(fd, termios.TCIOFLUSH)
            termios.tcsetattr(fd, termios.TCSANOW, attrs)
        except Exception:
            os.close(fd)
            raise
        return fd

    def _broadcast_ping_dynamixel(self, fd: int, timeout_sec: float) -> List[Dict[str, Any]]:
        termios.tcflush(fd, termios.TCIOFLUSH)
        packet = bytearray([0xFF, 0xFF, 0xFD, 0x00, 0xFE, 0x03, 0x00, 0x01])
        crc = self._dynamixel_crc(packet)
        packet.extend([crc & 0xFF, (crc >> 8) & 0xFF])
        if not self._write_dynamixel_packet(fd, bytes(packet), timeout_sec=0.05):
            return []

        deadline = time.time() + max(timeout_sec, 0.001)
        data = bytearray()
        devices_by_id: Dict[int, Dict[str, Any]] = {}
        while time.time() < deadline:
            remaining = max(0.0, deadline - time.time())
            readable, _, _ = select.select([fd], [], [], remaining)
            if not readable:
                break
            try:
                chunk = os.read(fd, 1024)
            except BlockingIOError:
                continue
            if not chunk:
                continue
            data.extend(chunk)
            for packet_data in self._extract_dynamixel_status_packets(data):
                params = packet_data.get('params', b'')
                dxl_id = int(packet_data.get('id', -1))
                if dxl_id < 0:
                    continue
                devices_by_id[dxl_id] = {
                    'id': dxl_id,
                    'packet_error': packet_data.get('error', 0),
                    'model_number': params[0] | (params[1] << 8) if len(params) >= 2 else None,
                    'firmware_version': params[2] if len(params) >= 3 else None,
                    'source': 'broadcast_ping',
                }
        return [devices_by_id[key] for key in sorted(devices_by_id)]

    def _ping_dynamixel_id(
        self,
        fd: int,
        dxl_id: int,
        timeout_sec: float,
    ) -> Optional[Dict[str, Any]]:
        termios.tcflush(fd, termios.TCIOFLUSH)
        packet = bytearray([0xFF, 0xFF, 0xFD, 0x00, dxl_id & 0xFF, 0x03, 0x00, 0x01])
        crc = self._dynamixel_crc(packet)
        packet.extend([crc & 0xFF, (crc >> 8) & 0xFF])
        if not self._write_dynamixel_packet(fd, bytes(packet), timeout_sec=0.02):
            return None

        deadline = time.time() + max(timeout_sec, 0.001)
        data = bytearray()
        while time.time() < deadline:
            remaining = max(0.0, deadline - time.time())
            readable, _, _ = select.select([fd], [], [], remaining)
            if not readable:
                break
            try:
                chunk = os.read(fd, 256)
            except BlockingIOError:
                continue
            if not chunk:
                continue
            data.extend(chunk)
            packet_data = self._extract_dynamixel_status_packet(data, dxl_id)
            if packet_data is None:
                continue
            params = packet_data.get('params', b'')
            return {
                'id': dxl_id,
                'packet_error': packet_data.get('error', 0),
                'model_number': params[0] | (params[1] << 8) if len(params) >= 2 else None,
                'firmware_version': params[2] if len(params) >= 3 else None,
                'source': 'id_ping',
            }
        return None

    @staticmethod
    def _write_dynamixel_packet(fd: int, packet: bytes, timeout_sec: float) -> bool:
        deadline = time.time() + max(timeout_sec, 0.001)
        written_total = 0
        while written_total < len(packet):
            remaining = max(0.0, deadline - time.time())
            if remaining <= 0.0:
                return False
            _, writable, _ = select.select([], [fd], [], remaining)
            if not writable:
                return False
            try:
                written = os.write(fd, packet[written_total:])
            except BlockingIOError:
                continue
            if written <= 0:
                return False
            written_total += written
        return True

    def _extract_dynamixel_status_packet(
        self,
        data: bytearray,
        expected_id: int,
    ) -> Optional[Dict[str, Any]]:
        header = b'\xFF\xFF\xFD\x00'
        while True:
            index = bytes(data).find(header)
            if index < 0:
                if len(data) > 3:
                    del data[:-3]
                return None
            if index > 0:
                del data[:index]
            if len(data) < 7:
                return None
            length = data[5] | (data[6] << 8)
            total = 7 + length
            if len(data) < total:
                return None
            packet = bytes(data[:total])
            del data[:total]
            if packet[4] != (expected_id & 0xFF):
                continue
            received_crc = packet[-2] | (packet[-1] << 8)
            calculated_crc = self._dynamixel_crc(packet[:-2])
            if received_crc != calculated_crc:
                continue
            if packet[7] != 0x55:
                continue
            return {
                'id': packet[4],
                'error': packet[8] if len(packet) > 8 else 0,
                'params': packet[9:-2],
            }

    def _extract_dynamixel_status_packets(self, data: bytearray) -> List[Dict[str, Any]]:
        packets: List[Dict[str, Any]] = []
        header = b'\xFF\xFF\xFD\x00'
        while True:
            index = bytes(data).find(header)
            if index < 0:
                if len(data) > 3:
                    del data[:-3]
                break
            if index > 0:
                del data[:index]
            if len(data) < 7:
                break
            length = data[5] | (data[6] << 8)
            total = 7 + length
            if len(data) < total:
                break
            packet = bytes(data[:total])
            del data[:total]
            received_crc = packet[-2] | (packet[-1] << 8)
            calculated_crc = self._dynamixel_crc(packet[:-2])
            if received_crc != calculated_crc:
                continue
            if packet[7] != 0x55:
                continue
            packets.append({
                'id': packet[4],
                'error': packet[8] if len(packet) > 8 else 0,
                'params': packet[9:-2],
            })
        return packets

    @staticmethod
    def _dynamixel_crc(data: bytes) -> int:
        crc = 0
        for byte in data:
            crc ^= int(byte) << 8
            for _ in range(8):
                if crc & 0x8000:
                    crc = ((crc << 1) ^ 0x8005) & 0xFFFF
                else:
                    crc = (crc << 1) & 0xFFFF
        return crc & 0xFFFF

    @staticmethod
    def _dynamixel_model_name(model_number: Any) -> str:
        if model_number is None:
            return ''
        known = {
            1120: 'XM540-W270',
            1130: 'XM540-W150',
            1100: 'XH540-W270',
            1110: 'XH540-W150',
        }
        return known.get(int(model_number), '')

    def _build_matching_rows(
        self,
        slaves: List[Dict[str, Any]],
        configured_axes: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        axes_by_alias: Dict[int, List[Dict[str, Any]]] = {}
        for axis in configured_axes:
            alias = self._parse_int(axis.get('ethercat_alias'))
            if alias is None:
                continue
            axes_by_alias.setdefault(alias, []).append(axis)

        seen_aliases = set()
        rows: List[Dict[str, Any]] = []
        for slave in slaves:
            ethercat_alias = self._parse_int(slave.get('ethercat_alias'))
            if ethercat_alias is not None:
                seen_aliases.add(ethercat_alias)
            axes = axes_by_alias.get(ethercat_alias, [])
            axis = axes[0] if axes else None
            if len(axes) > 1:
                match_state = 'duplicate_axis'
            elif axis is None:
                match_state = 'unregistered'
            elif axis.get('state') == 'detected':
                match_state = 'matched'
            else:
                match_state = 'configured'

            rows.append(self._matching_row(slave, axis, match_state))

        for axis in configured_axes:
            ethercat_alias = self._parse_int(axis.get('ethercat_alias'))
            if ethercat_alias is None or ethercat_alias in seen_aliases:
                continue
            rows.append(self._matching_row(None, axis, 'missing'))

        return rows

    def _matching_row(
        self,
        slave: Optional[Dict[str, Any]],
        axis: Optional[Dict[str, Any]],
        match_state: str,
    ) -> Dict[str, Any]:
        return {
            'slave_position': None if slave is None else slave.get('slave_position'),
            'master_index': None if slave is None else slave.get('master_index'),
            'ethercat_alias': (
                axis.get('ethercat_alias')
                if slave is None and axis is not None
                else (None if slave is None else slave.get('ethercat_alias'))
            ),
            'rotary_alias': None if slave is None else slave.get('rotary_alias'),
            'rotary_alias_hex': '' if slave is None else slave.get('rotary_alias_hex', ''),
            'rotary_alias_error': '' if slave is None else slave.get('rotary_alias_error', ''),
            'driver_model': self._row_driver_model(slave, axis),
            'device_state': '' if slave is None else slave.get('device_state', ''),
            'vendor_id': None if slave is None else slave.get('vendor_id'),
            'product_code': None if slave is None else slave.get('product_code'),
            'serial_number': None if slave is None else slave.get('serial_number'),
            'controller_index': None if axis is None else axis.get('controller_index'),
            'display_name': '' if axis is None else axis.get('display_name', ''),
            'axis_state': '' if axis is None else axis.get('state', ''),
            'axis_rotary_alias': None if axis is None else axis.get('station_alias_register'),
            'match_state': match_state,
            'match_state_label': self._match_state_label(match_state),
        }

    @staticmethod
    def _row_driver_model(
        slave: Optional[Dict[str, Any]],
        axis: Optional[Dict[str, Any]],
    ) -> str:
        if slave is not None:
            model = str(slave.get('order_number') or slave.get('device_name') or '')
            if model:
                return model
        if axis is not None:
            return str(axis.get('driver_name') or axis.get('driver_model') or '')
        return ''

    @staticmethod
    def _match_state_label(match_state: str) -> str:
        labels = {
            'matched': 'OK',
            'configured': 'Axis 대기',
            'unregistered': '미등록',
            'missing': '설정만 있음',
            'duplicate_axis': 'Alias 중복',
        }
        return labels.get(match_state, match_state)

    @staticmethod
    def _matching_summary(rows: List[Dict[str, Any]]) -> Dict[str, int]:
        summary = {
            'matched': 0,
            'configured': 0,
            'unregistered': 0,
            'missing': 0,
            'duplicate_axis': 0,
            'total': len(rows),
        }
        for row in rows:
            match_state = str(row.get('match_state', ''))
            summary[match_state] = summary.get(match_state, 0) + 1
        return summary

    @staticmethod
    def _state_detail(state: str) -> str:
        details = {
            'detected': '모터 피드백이 정상 수신 중입니다.',
            'stale': '모터 피드백 갱신이 지연되고 있습니다.',
            'disconnected': '축이 현재 응답하지 않습니다.',
            'monitoring_off': '상위 모니터링이 꺼져 있습니다.',
            'ethercat_down': '서보드라이버 전원 OFF 또는 EtherCAT 통신 끊김 상태입니다.',
        }
        return details.get(state, '상태를 확인할 수 없습니다.')

    def _metadata_for(self, controller_index: int) -> Dict[str, Any]:
        metadata = deepcopy(self._motor_metadata.get(controller_index, {}))
        motor_type = str(metadata.get('motor_type', 'unknown'))
        transport = str(metadata.get('transport', 'unknown'))
        metadata['motor_type'] = motor_type
        metadata['motor_type_label'] = self._motor_type_label(motor_type)
        metadata['transport'] = transport
        metadata['transport_label'] = self._transport_label(transport)
        return metadata

    @staticmethod
    def _pulse_per_revolution(metadata: Dict[str, Any]) -> Optional[float]:
        try:
            value = float(metadata.get('pulse_per_revolution') or 0.0)
        except (TypeError, ValueError):
            return None
        return value if value > 0.0 else None

    @staticmethod
    def _optional_float(value: Any) -> Optional[float]:
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _counts_to_degrees(value: Optional[int], pulse_per_revolution: Optional[float]) -> Optional[float]:
        if value is None or not pulse_per_revolution:
            return None
        return float(value) / pulse_per_revolution * 360.0

    @staticmethod
    def _statusword_text(statusword: int) -> str:
        if statusword & 0x0008:
            return 'Fault'
        masked_6f = statusword & 0x006F
        masked_4f = statusword & 0x004F
        if masked_6f == 0x0027:
            return 'Operation enabled'
        if masked_6f == 0x0023:
            return 'Switched on'
        if masked_6f == 0x0021:
            return 'Ready to switch on'
        if masked_4f == 0x0040:
            return 'Switch on disabled'
        if masked_6f == 0x0007:
            return 'Quick stop active'
        if masked_4f == 0x000F:
            return 'Fault reaction active'
        if masked_4f == 0x0000:
            return 'Not ready to switch on'
        return 'Unknown status'

    @staticmethod
    def _dynamixel_statusword_text(statusword: int) -> str:
        return 'Torque enabled' if statusword & 0x01 else 'Torque disabled'

    @staticmethod
    def _error_text(errorcode: int, alarm_text: str) -> str:
        del alarm_text
        if errorcode == 0:
            return 'No error'
        return f'Error {float(errorcode):.1f}'

    @staticmethod
    def _normalized_errorcode(raw_errorcode: int, metadata: Dict[str, Any]) -> int:
        if (
            str(metadata.get('motor_type', '')).lower() == 'minas'
            and (raw_errorcode & 0xFF00) == 0xFF00
            and (raw_errorcode & 0x00FF) != 0
        ):
            return raw_errorcode & 0x00FF
        return raw_errorcode

    @staticmethod
    def _hex16(value: int) -> str:
        return f'0x{int(value) & 0xFFFF:04X}'

    @staticmethod
    def _motor_type_label(motor_type: str) -> str:
        return MOTOR_TYPE_LABELS.get(str(motor_type), str(motor_type) or 'Unknown')

    @staticmethod
    def _transport_label(transport: str) -> str:
        return TRANSPORT_LABELS.get(str(transport), str(transport) or 'Unknown')

    @staticmethod
    def _count_values(items: List[Dict[str, Any]], key: str) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for item in items:
            value = str(item.get(key) or 'Unknown')
            counts[value] = counts.get(value, 0) + 1
        return counts

    @staticmethod
    def _parse_int(value: Any) -> Optional[int]:
        if value is None or value == '':
            return None
        try:
            return int(str(value), 0)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _array_value(msg: MotorStatus, field: str, index: int, default: Any) -> Any:
        values = getattr(msg, field, None)
        if values is None or index >= len(values):
            return default
        return values[index]


def main(args=None) -> None:
    rclpy.init(args=args)
    node = MotionStateMonitor()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()
