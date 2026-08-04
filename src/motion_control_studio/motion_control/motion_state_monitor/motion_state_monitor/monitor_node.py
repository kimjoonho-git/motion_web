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
DYNAMIXEL_SCAN_MAX_ID = 252
DYNAMIXEL_SCAN_PROTOCOL = '2.0'
COMMUNICATION_UNAVAILABLE_ERROR = 0xFFFF
MOTOR_SCAN_CONTRACT_VERSION = 3


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
        self.project_id = self._project_id_from_motor_config(self.motor_config_file)
        self.project_generation = int(
            self.declare_parameter('project_generation', 0).value
        )
        self.output_topic = self.declare_parameter(
            'output_topic',
            '/motion_control/motion_state',
        ).value
        self.scan_progress_topic = self.declare_parameter(
            'scan_progress_topic',
            '/motion_control/motor_scan_progress',
        ).value
        self.publish_hz = float(self.declare_parameter('publish_hz', 10.0).value)
        self.feedback_process_hz = float(
            self.declare_parameter('feedback_process_hz', 100.0).value
        )
        self.max_motors = int(self.declare_parameter('max_motors', 50).value)
        self.stale_timeout_sec = float(self.declare_parameter('stale_timeout_sec', 0.5).value)
        self.disconnected_timeout_sec = float(
            self.declare_parameter('disconnected_timeout_sec', 2.0).value
        )
        self.connection_loss_confirm_sec = float(
            self.declare_parameter('connection_loss_confirm_sec', 1.0).value
        )
        self.connection_recovery_confirm_sec = float(
            self.declare_parameter('connection_recovery_confirm_sec', 0.5).value
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
        self._last_healthy_motors: Dict[int, Dict[str, Any]] = {}
        self._communication_health: Dict[int, Dict[str, Any]] = {}
        self._motor_metadata: Dict[int, Dict[str, Any]] = {}
        self._ethercat_status: Dict[str, Any] = {}
        self._last_motor_status_at: Optional[float] = None
        self._last_motor_status_processed_at: Optional[float] = None
        self._last_ethercat_status_at: Optional[float] = None
        self._last_ethercat_physical_scan: Dict[str, Any] = {}
        self._last_disabled_publish_at = 0.0
        self._started_at = time.time()
        self._subscription = None
        self._scan_sequence = 0
        self._active_scan_id = ''

        self._publisher = self.create_publisher(String, self.output_topic, 10)
        self._scan_progress_publisher = self.create_publisher(
            String, self.scan_progress_topic, 20
        )
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
        self._ethercat_poll_timer = self.create_timer(
            0.5,
            self._poll_ethercat_bus_status,
        )

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
            ethercat_master_index = master.get('ethercat_master_index', 0)
            serial_port = master.get('serial_port') or master.get('port')
            serial_baudrate = master.get('serial_baudrate', master.get('baudrate'))
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
                    'ethercat_master_index': ethercat_master_index,
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
                    'slave_position': slave.get('position'),
                    'node_id': slave.get(
                        'node_id', slave.get('bus_id', slave.get('id'))
                    ),
                    'bus_id': slave.get('bus_id', slave.get('id')),
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
        response.success = bool(result.get('scan_complete'))
        response.message = json.dumps(result, ensure_ascii=False, separators=(',', ':'))
        if not self.monitoring_enabled:
            self.get_logger().warn('motor scan requested while monitoring is disabled.')
        return response

    def _scan_ac_servo_motors(self, request: Trigger.Request, response: Trigger.Response):
        del request
        result = self._build_scan_result(scan_ethercat=True, scan_dynamixel=False)
        response.success = self._physical_section_success(
            result.get('ethercat_scan'), 'slaves_count'
        )
        response.message = json.dumps(result, ensure_ascii=False, separators=(',', ':'))
        if not self.monitoring_enabled:
            self.get_logger().warn('AC Servo scan requested while monitoring is disabled.')
        return response

    def _scan_dynamixel_motors_service(self, request: Trigger.Request, response: Trigger.Response):
        del request
        result = self._build_scan_result(scan_ethercat=False, scan_dynamixel=True)
        response.success = self._physical_section_success(
            result.get('dynamixel_scan'), 'devices_count'
        )
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
        self._scan_sequence = int(getattr(self, '_scan_sequence', 0)) + 1
        self._active_scan_id = f'{int(time.time() * 1000)}-{self._scan_sequence}'
        requested_labels = []
        if scan_ethercat:
            requested_labels.append('EtherCAT')
        if scan_dynamixel:
            requested_labels.append('Dynamixel')
        self._publish_scan_progress(
            'started',
            f'{" + ".join(requested_labels)} 직접 스캔을 시작합니다',
            transport='all',
        )
        now = time.time()
        ethercat = self._current_ethercat_status(now)
        motors = self._current_motor_list(now)
        configured_axes = self._configured_axis_list(motors)
        ethercat_scan = (
            self._safe_scan_ethercat_slaves()
            if scan_ethercat
            else self._skipped_ethercat_scan(now)
        )
        if scan_ethercat:
            self._last_ethercat_physical_scan = deepcopy(ethercat_scan)
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
        connection_rows = self._build_scan_connection_rows(
            motors,
            ethercat_scan,
            dynamixel_scan,
            scan_ethercat=scan_ethercat,
            scan_dynamixel=scan_dynamixel,
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
                'connection_state': motor.get('connection_state', 'unknown'),
                'connection_connected': bool(motor.get('connection_connected', False)),
                'connection_confirmed': bool(motor.get('connection_confirmed', False)),
                'connection_reason': motor.get('connection_reason', ''),
                'connection_source': motor.get('connection_source', ''),
                'connection_message': motor.get('connection_message', ''),
                'fault': bool(motor.get('fault', False)),
                'servo_on': bool(motor.get('servo_on', False)),
                'last_seen_at': motor.get('last_seen_at'),
                'age_sec': motor.get('age_sec'),
            }
            for motor in motors
            if motor.get('connection_connected', False)
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
            'connection_rows': connection_rows,
            'connection_summary': self._connection_summary(connection_rows),
            'connected_axes': connected_axes,
            'known_axes': configured_axes,
        }
        requested_sections = []
        if scan_ethercat:
            requested_sections.append(('ethercat', ethercat_scan, 'slaves_count'))
        if scan_dynamixel:
            requested_sections.append(('dynamixel', dynamixel_scan, 'devices_count'))
        successful_sections = [
            name for name, section, count_key in requested_sections
            if self._physical_section_success(section, count_key)
        ]
        scan_errors = [
            {
                'transport': name,
                'message': str(section.get('error') or '직접 스캔에서 장치를 확인하지 못했습니다'),
            }
            for name, section, count_key in requested_sections
            if not self._physical_section_success(section, count_key)
            and not bool(section.get('skipped'))
        ]
        scan_duration_ms = round((time.time() - now) * 1000.0, 3)
        scan_outcome = (
            'complete'
            if len(successful_sections) == len(requested_sections)
            else ('partial' if successful_sections else 'failed')
        )
        result.update({
            'scan_id': self._active_scan_id,
            'scan_duration_ms': scan_duration_ms,
            'scan_contract': {
                'version': MOTOR_SCAN_CONTRACT_VERSION,
                'physical_only': True,
                'ethercat_requires_rescan': True,
                'dynamixel_protocol': DYNAMIXEL_SCAN_PROTOCOL,
                'dynamixel_baudrate': DYNAMIXEL_SCAN_BAUDRATES[0],
                'dynamixel_id_min': 0,
                'dynamixel_id_max': DYNAMIXEL_SCAN_MAX_ID,
                'full_success_requires_all_requested_transports': True,
            },
            'scan_success': bool(successful_sections),
            'scan_complete': len(successful_sections) == len(requested_sections),
            'scan_outcome': scan_outcome,
            'scan_errors': scan_errors,
            'physical_scan': {
                'ethercat': ethercat_scan,
                'dynamixel': dynamixel_scan,
            },
            'project_comparison': {
                'matching_rows': matching_rows,
                'matching_summary': self._matching_summary(matching_rows),
            },
            'runtime_status': {
                'connection_rows': connection_rows,
                'connection_summary': self._connection_summary(connection_rows),
                'connected_axes': connected_axes,
                'known_axes': configured_axes,
            },
        })
        failed_summary = ' / '.join(
            f'{item["transport"]}: {item["message"]}' for item in scan_errors
        )
        self._publish_scan_progress(
            scan_outcome,
            (
                f'직접 스캔 완료: EtherCAT {ethercat_scan.get("slaves_count", 0)}축, '
                f'Dynamixel {dynamixel_scan.get("devices_count", 0)}개, '
                f'총 {scan_duration_ms:g}ms, ID {self._active_scan_id}'
                if scan_outcome == 'complete'
                else (
                    f'직접 스캔 부분 완료: {failed_summary}'
                    if scan_outcome == 'partial'
                    else f'직접 스캔 실패: {failed_summary}'
                )
            ),
            transport='all',
            details={
                'success': result['scan_success'],
                'complete': result['scan_complete'],
                'outcome': scan_outcome,
                'ethercat_count': ethercat_scan.get('slaves_count', 0),
                'dynamixel_count': dynamixel_scan.get('devices_count', 0),
                'scan_duration_ms': scan_duration_ms,
                'scan_id': self._active_scan_id,
            },
        )
        return result

    def _publish_scan_progress(
        self,
        phase: str,
        message: str,
        *,
        transport: str,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        publisher = getattr(self, '_scan_progress_publisher', None)
        scan_id = str(getattr(self, '_active_scan_id', '') or '')
        if publisher is None or not scan_id:
            return
        event = {
            'scan_id': scan_id,
            'phase': str(phase),
            'transport': str(transport),
            'message': str(message),
            'details': details if isinstance(details, dict) else {},
            'timestamp': time.time(),
        }
        msg = String()
        msg.data = json.dumps(event, ensure_ascii=False, separators=(',', ':'))
        publisher.publish(msg)

    @staticmethod
    def _physical_section_success(section: Any, count_key: str) -> bool:
        if not isinstance(section, dict):
            return False
        return bool(
            section.get('available')
            and section.get('complete')
            and int(section.get(count_key) or 0) > 0
        )

    def _safe_scan_ethercat_slaves(self) -> Dict[str, Any]:
        try:
            return self._scan_ethercat_slaves()
        except Exception as exc:
            self.get_logger().error(f'EtherCAT scan failed: {exc}')
            return {
                'available': False,
                'complete': False,
                'direct': True,
                'scanned_at': time.time(),
                'error': str(exc),
                'slaves_count': 0,
                'slaves': [],
            }

    def _safe_scan_dynamixel_motors(self) -> Dict[str, Any]:
        try:
            return self._scan_dynamixel_motors()
        except Exception as exc:
            self.get_logger().error(f'Dynamixel scan failed: {exc}')
            return {
                'available': False,
                'complete': False,
                'direct': True,
                'scanned_at': time.time(),
                'mode': 'direct_ping',
                'protocol': DYNAMIXEL_SCAN_PROTOCOL,
                'scan_rule': 'auto serial port, baudrate 1000000, broadcast ping plus ID 0-252',
                'error': str(exc),
                'targets': [],
                'devices_count': 0,
                'devices': [],
            }

    @staticmethod
    def _skipped_ethercat_scan(scanned_at: float) -> Dict[str, Any]:
        return {
            'available': False,
            'complete': False,
            'direct': True,
            'skipped': True,
            'scanned_at': scanned_at,
            'error': '',
            'slaves_count': 0,
            'slaves': [],
        }

    def _skipped_dynamixel_scan(self, scanned_at: float) -> Dict[str, Any]:
        return {
            'available': False,
            'complete': False,
            'direct': True,
            'skipped': True,
            'scanned_at': scanned_at,
            'mode': 'direct_ping',
            'protocol': DYNAMIXEL_SCAN_PROTOCOL,
            'scan_rule': 'auto serial port, baudrate 1000000, broadcast ping plus ID 0-252',
            'error': '',
            'targets': [],
            'devices_count': 0,
            'devices': [],
        }

    def _motor_status_callback(self, msg: MotorStatus) -> None:
        if not self.monitoring_enabled:
            return

        now = time.time()
        self._last_motor_status_at = now
        process_hz = max(float(getattr(self, 'feedback_process_hz', 0.0)), 0.0)
        last_processed_at = getattr(self, '_last_motor_status_processed_at', None)
        if (
            process_hz > 0.0
            and last_processed_at is not None
            and now - float(last_processed_at) < 1.0 / process_hz
        ):
            return
        self._last_motor_status_processed_at = now

        controller_indices = list(getattr(msg, 'controller_index', []))
        count = min(len(controller_indices), self.max_motors)
        if len(controller_indices) > self.max_motors:
            self.get_logger().warn(
                f'{self.input_topic} contains {len(controller_indices)} motors; '
                f'only first {self.max_motors} are monitored.'
            )

        for i in range(count):
            controller_index = int(controller_indices[i])
            motor = self._motor_from_status(
                msg,
                i,
                controller_index,
                now,
            )
            communication_unavailable = (
                int(motor.get('errorcode_raw') or 0) == COMMUNICATION_UNAVAILABLE_ERROR
            )
            self._update_communication_health(
                controller_index,
                communication_unavailable,
                now,
            )
            if not communication_unavailable:
                self._last_healthy_motors[controller_index] = deepcopy(motor)
            self._motors[controller_index] = motor

    def _update_communication_health(
        self,
        controller_index: int,
        communication_unavailable: bool,
        now: float,
    ) -> Dict[str, Any]:
        health = self._communication_health.setdefault(controller_index, {
            'unavailable_since': None,
            'available_since': None,
            'confirmed_offline': False,
        })
        if communication_unavailable:
            health['available_since'] = None
            if health['unavailable_since'] is None:
                health['unavailable_since'] = now
            if now - float(health['unavailable_since']) >= self.connection_loss_confirm_sec:
                health['confirmed_offline'] = True
        else:
            health['unavailable_since'] = None
            if health['available_since'] is None:
                health['available_since'] = now
            if (
                health['confirmed_offline']
                and now - float(health['available_since'])
                >= self.connection_recovery_confirm_sec
            ):
                health['confirmed_offline'] = False
        return health

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
        communication_unavailable = raw_errorcode == COMMUNICATION_UNAVAILABLE_ERROR
        motor_type = str(metadata.get('motor_type', '')).lower()
        is_dynamixel = motor_type == 'dynamixel'
        internal_limit_active = bool(statusword & 0x0800) and not is_dynamixel
        status_text = (
            'Communication unavailable'
            if communication_unavailable
            else (
                self._dynamixel_statusword_text(statusword)
                if is_dynamixel
                else self._statusword_text(statusword)
            )
        )
        if internal_limit_active and not communication_unavailable:
            status_text = f'{status_text} · Internal limit active'
        servo_on = bool(statusword & 0x01) if is_dynamixel else (statusword & 0x006F) == 0x0027
        fault = (
            False
            if communication_unavailable
            else (bool(errorcode) if is_dynamixel else bool(statusword & 0x0008))
        )
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
            'state': 'disconnected' if communication_unavailable else 'detected',
            'last_seen_at': now,
            'age_sec': 0.0,
            'controlword': int(self._array_value(msg, 'controlword', index, 0)),
            'statusword': statusword,
            'status_text': status_text,
            'errorcode': errorcode,
            'errorcode_raw': raw_errorcode,
            'errorcode_hex': self._hex16(raw_errorcode),
            'error_text': (
                'Communication unavailable'
                if communication_unavailable
                else self._error_text(errorcode, '')
            ),
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
            'internal_limit_active': internal_limit_active,
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
            'project_id': getattr(self, 'project_id', ''),
            'project_generation': int(getattr(self, 'project_generation', 0) or 0),
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
            'online_motors_count': len(
                [m for m in motors if m.get('connection_connected', False)]
            ),
            'connection_summary': self._connection_summary(motors),
            'motor_type_counts': self._count_values(motors, 'motor_type_label'),
            'transport_counts': self._count_values(motors, 'transport_label'),
            'motors': motors,
        }

        msg = String()
        msg.data = json.dumps(state, ensure_ascii=False, separators=(',', ':'))
        self._publisher.publish(msg)

    @staticmethod
    def _project_id_from_motor_config(config_file: Any) -> str:
        """Derive ownership only from a project runtime configuration path."""
        raw = str(config_file or '').strip()
        if not raw:
            return ''
        requested_path = Path(raw).expanduser()
        if (
            not requested_path.is_file()
            or requested_path.is_symlink()
        ):
            return ''

        path = requested_path.resolve()
        if path.parent.name == 'runtime':
            runtime_dir = path.parent
        elif path.parent.name == 'sessions' and path.parent.parent.name == 'runtime':
            runtime_dir = path.parent.parent
            if path.parent.is_symlink():
                return ''
        else:
            return ''

        if runtime_dir.is_symlink():
            return ''
        project_dir = runtime_dir.parent
        manifest_path = project_dir / 'project.json'
        if project_dir.is_symlink() or manifest_path.is_symlink() or not manifest_path.is_file():
            return ''
        try:
            manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        except (OSError, ValueError, json.JSONDecodeError):
            return ''
        if not isinstance(manifest, dict):
            return ''
        project_id = str(manifest.get('project_id') or '').strip()
        return project_id if project_id and project_id == project_dir.name else ''

    def _current_motor_list(self, now: float) -> List[Dict[str, Any]]:
        motors: List[Dict[str, Any]] = []
        configured_indices = set(self._motor_metadata)
        if not configured_indices:
            return motors

        ethercat_available = bool(self._ethercat_status)
        for controller_index in sorted(configured_indices):
            if controller_index not in self._motors:
                if not self.monitoring_enabled:
                    state = 'monitoring_off'
                    reason = 'monitoring_disabled'
                    source = 'monitor'
                elif now - self._started_at < self.disconnected_timeout_sec:
                    state = 'initializing'
                    reason = 'awaiting_first_feedback'
                    source = 'runtime_topic'
                else:
                    state = 'disconnected'
                    reason = 'no_runtime_feedback'
                    source = 'runtime_topic'
                motor = self._configured_motor_placeholder(controller_index, state)
                self._set_connection_fields(motor, state, reason, source, now)
                self._set_physical_connection_fields(motor)
                motors.append(motor)
                continue

            motor = deepcopy(self._motors[controller_index])
            health = self._communication_health.get(controller_index, {})
            raw_communication_unavailable = (
                int(motor.get('errorcode_raw') or 0) == COMMUNICATION_UNAVAILABLE_ERROR
            )
            communication_unavailable = bool(health.get('confirmed_offline', False))
            if (
                raw_communication_unavailable
                and not communication_unavailable
                and controller_index in self._last_healthy_motors
            ):
                motor = deepcopy(self._last_healthy_motors[controller_index])
            age = now - float(motor.get('last_seen_at', now))
            motor['age_sec'] = round(age, 3)
            transport = str(motor.get('transport', '')).lower()
            ethercat_axis_state = (
                self._ethercat_axis_state(motor)
                if transport == 'ethercat' and ethercat_available
                else ''
            )
            ethercat_master_status = (
                self._ethercat_master_status(motor)
                if transport == 'ethercat' and ethercat_available
                else {}
            )
            ethercat_down = transport == 'ethercat' and ethercat_available and (
                not ethercat_master_status.get('available', False)
                or not ethercat_master_status.get('master_active', False)
                or not ethercat_master_status.get('link_up', False)
            )
            if not self.monitoring_enabled:
                state = 'monitoring_off'
                reason = 'monitoring_disabled'
                source = 'monitor'
            elif communication_unavailable:
                state = 'disconnected'
                reason = 'communication_unavailable'
                source = 'runtime_error'
            elif raw_communication_unavailable and controller_index not in self._last_healthy_motors:
                state = 'initializing'
                reason = 'communication_confirmation_pending'
                source = 'runtime_error'
            elif transport == 'ethercat' and ethercat_down:
                state = 'ethercat_down'
                reason = 'ethercat_bus_down'
                source = 'bus_status'
            elif transport == 'ethercat' and ethercat_available and not ethercat_axis_state:
                state = 'ethercat_down'
                reason = 'ethercat_axis_missing'
                source = 'bus_status'
            elif (
                transport == 'ethercat'
                and ethercat_available
                and ethercat_axis_state not in {'OP', 'SAFEOP'}
            ):
                state = 'ethercat_down'
                reason = 'ethercat_axis_not_operational'
                source = 'bus_status'
            elif age >= self.disconnected_timeout_sec:
                state = 'disconnected'
                reason = 'feedback_timeout'
                source = 'runtime_topic'
            elif age >= self.stale_timeout_sec:
                state = 'stale'
                reason = 'feedback_stale'
                source = 'runtime_topic'
            else:
                state = 'detected'
                reason = 'runtime_feedback_fresh'
                source = 'runtime_topic'
            motor['state'] = state
            self._set_connection_fields(motor, state, reason, source, now)
            self._set_physical_connection_fields(motor)
            motor['configuration_state'] = 'configured'
            motors.append(motor)
        return motors

    def _set_physical_connection_fields(self, motor: Dict[str, Any]) -> None:
        if str(motor.get('transport') or '').lower() != 'ethercat':
            return
        scan = getattr(self, '_last_ethercat_physical_scan', {})
        scanned_at = scan.get('scanned_at') if isinstance(scan, dict) else None
        if not scan or not scan.get('complete'):
            motor.update({
                'physical_connection_state': 'not_scanned' if not scan else 'unknown',
                'physical_connection_confirmed': False,
                'physical_connection_checked_at': scanned_at,
                'physical_connection_message': (
                    '물리 검색을 아직 실행하지 않았습니다.'
                    if not scan
                    else str(scan.get('error') or '최근 물리 검색을 완료하지 못했습니다.')
                ),
            })
            return

        expected_alias = self._parse_int(motor.get('alias'))
        expected_position = self._parse_int(motor.get('slave_position'))
        expected_master = self._parse_int(motor.get('ethercat_master_index')) or 0
        matched = None
        for slave in scan.get('slaves') or []:
            if not isinstance(slave, dict):
                continue
            physical_alias = self._parse_int(
                slave.get('ethercat_alias', slave.get('rotary_alias'))
            )
            physical_master = self._parse_int(slave.get('master_index')) or 0
            if (
                expected_alias not in (None, 0)
                and physical_alias == expected_alias
                and physical_master == expected_master
            ):
                matched = slave
                break
            if (
                expected_alias in (None, 0)
                and self._parse_int(slave.get('slave_position')) == expected_position
                and physical_master == expected_master
            ):
                matched = slave
                break

        detected = matched is not None
        motor.update({
            'physical_connection_state': 'detected' if detected else 'missing',
            'physical_connection_confirmed': True,
            'physical_connection_checked_at': scanned_at,
            'physical_connection_message': (
                '최근 EtherCAT 물리 검색에서 확인됐습니다.'
                if detected
                else '최근 EtherCAT 물리 검색에서 확인되지 않았습니다.'
            ),
            'physical_slave_position': (
                matched.get('slave_position') if matched is not None else None
            ),
        })

    def _set_connection_fields(
        self,
        motor: Dict[str, Any],
        state: str,
        reason: str,
        source: str,
        checked_at: float,
    ) -> None:
        connection_state = {
            'detected': 'online',
            'disconnected': 'offline',
            'ethercat_down': 'bus_down',
            'stale': 'stale',
            'monitoring_off': 'monitoring_off',
            'initializing': 'initializing',
        }.get(state, 'unknown')
        message = self._connection_message(reason)
        connected = connection_state == 'online'
        confirmed = connection_state in {'online', 'offline', 'bus_down'}
        evidence = {
            'source': source,
            'reason_code': reason,
            'checked_at': checked_at,
            'last_feedback_at': motor.get('last_seen_at'),
            'feedback_age_sec': motor.get('age_sec'),
        }
        motor.update({
            'connection_state': connection_state,
            'connection_connected': connected,
            'connection_confirmed': confirmed,
            'connection_reason': reason,
            'connection_source': source,
            'connection_message': message,
            'connection_evidence': evidence,
            'state_detail': message,
        })

    @staticmethod
    def _connection_message(reason: str) -> str:
        messages = {
            'runtime_feedback_fresh': '모터 런타임 피드백이 정상 수신 중입니다.',
            'communication_unavailable': '제어 노드가 이 축의 통신 불가를 보고했습니다.',
            'ethercat_bus_down': 'EtherCAT Master 또는 물리 링크가 내려가 있습니다.',
            'ethercat_axis_missing': 'EtherCAT 버스에서 설정된 Slave를 찾지 못했습니다.',
            'ethercat_axis_not_operational': 'EtherCAT Slave가 운전 가능 상태가 아닙니다.',
            'feedback_timeout': '마지막 모터 피드백 이후 연결 제한 시간을 초과했습니다.',
            'feedback_stale': '모터 피드백 갱신이 지연되고 있습니다.',
            'monitoring_disabled': '모터 상태 모니터링이 꺼져 있습니다.',
            'awaiting_first_feedback': '제어 노드의 첫 모터 피드백을 기다리고 있습니다.',
            'communication_confirmation_pending': '일시적인 통신 실패인지 확인하고 있습니다.',
            'no_runtime_feedback': '설정된 축이지만 제어 노드에서 피드백을 받지 못했습니다.',
            'scan_detected': '통신 버스 검색에서 모터가 확인되었습니다.',
            'scan_missing': '통신 버스 검색에서 설정된 모터를 찾지 못했습니다.',
            'scan_failed': '통신 버스 검색에 실패하여 연결 여부를 확정할 수 없습니다.',
        }
        return messages.get(reason, '모터 연결 상태를 확인할 수 없습니다.')

    def _poll_ethercat_bus_status(self) -> None:
        now = time.time()
        master_indices = sorted({
            self._parse_int(metadata.get('ethercat_master_index')) or 0
            for metadata in getattr(self, '_motor_metadata', {}).values()
            if str(metadata.get('transport') or '').lower() == 'ethercat'
        }) or [0]
        masters: Dict[str, Dict[str, Any]] = {}
        errors: List[str] = []

        for master_index in master_indices:
            try:
                master = subprocess.run(
                    ['ethercat', 'master', '-m', str(master_index)],
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=2.0,
                )
                slaves = subprocess.run(
                    ['ethercat', 'slaves', '-m', str(master_index)],
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=2.0,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                error = str(exc)
                masters[str(master_index)] = {
                    'available': False,
                    'master_index': master_index,
                    'error': error,
                }
                errors.append(f'Master {master_index}: {error}')
                continue

            if master.returncode != 0 or slaves.returncode != 0:
                error = (
                    master.stderr.strip()
                    or slaves.stderr.strip()
                    or master.stdout.strip()
                    or slaves.stdout.strip()
                )
                masters[str(master_index)] = {
                    'available': False,
                    'master_index': master_index,
                    'error': error,
                }
                errors.append(f'Master {master_index}: {error}')
                continue

            phase_match = re.search(
                r'^\s*Phase:\s*(.+?)\s*$',
                master.stdout,
                re.MULTILINE | re.IGNORECASE,
            )
            states_by_position: Dict[str, str] = {}
            states_by_alias: Dict[str, str] = {}
            for match in re.finditer(
                r'^\s*(\d+)\s+(\d+):\d+\s+([A-Z]+)\b',
                slaves.stdout,
                re.MULTILINE | re.IGNORECASE,
            ):
                position, alias, state = match.groups()
                states_by_position[position] = state.upper()
                if int(alias) > 0:
                    states_by_alias[alias] = state.upper()

            masters[str(master_index)] = {
                'available': True,
                'master_index': master_index,
                'master_active': bool(re.search(
                    r'^\s*Active:\s*yes\s*$',
                    master.stdout,
                    re.MULTILINE | re.IGNORECASE,
                )),
                'link_up': bool(re.search(
                    r'^\s*Link:\s*UP\s*$',
                    master.stdout,
                    re.MULTILINE | re.IGNORECASE,
                )),
                'slaves_responding': len(states_by_position),
                'phase': phase_match.group(1).strip() if phase_match else '',
                'state_text': ', '.join(
                    f'{position}:{state}'
                    for position, state in sorted(
                        states_by_position.items(),
                        key=lambda item: int(item[0]),
                    )
                ),
                'states_by_position': states_by_position,
                'states_by_alias': states_by_alias,
                'error': '',
            }

        available_masters = [
            status for status in masters.values() if status.get('available')
        ]
        first_master = masters.get(str(master_indices[0]), {})
        self._ethercat_status = {
            'available': bool(available_masters),
            'complete': len(available_masters) == len(master_indices),
            'last_seen_at': now,
            'master_active': bool(available_masters) and all(
                status.get('master_active', False) for status in available_masters
            ),
            'link_up': bool(available_masters) and all(
                status.get('link_up', False) for status in available_masters
            ),
            'slaves_responding': sum(
                int(status.get('slaves_responding') or 0)
                for status in available_masters
            ),
            'phase': str(first_master.get('phase') or ''),
            'state_text': ' · '.join(
                f'Master {master_index} [{masters[str(master_index)].get("state_text", "")}]'
                for master_index in master_indices
                if masters[str(master_index)].get('available')
            ),
            'masters': masters,
            'error': ' / '.join(errors),
        }
        self._last_ethercat_status_at = now

    def _ethercat_master_status(self, motor: Dict[str, Any]) -> Dict[str, Any]:
        status = self._ethercat_status if isinstance(self._ethercat_status, dict) else {}
        master_index = self._parse_int(motor.get('ethercat_master_index')) or 0
        masters = status.get('masters') if isinstance(status.get('masters'), dict) else {}
        master_status = masters.get(str(master_index))
        if isinstance(master_status, dict):
            return master_status
        if master_index == 0:
            return status
        return {}

    def _ethercat_axis_state(self, motor: Dict[str, Any]) -> str:
        status = self._ethercat_master_status(motor)
        alias = self._parse_int(motor.get('alias'))
        if alias is not None and alias > 0:
            return str((status.get('states_by_alias') or {}).get(str(alias)) or '')
        position = self._parse_int(motor.get('slave_position'))
        if position is None:
            return ''
        return str(
            (status.get('states_by_position') or {}).get(str(position)) or ''
        )

    def _build_scan_connection_rows(
        self,
        motors: List[Dict[str, Any]],
        ethercat_scan: Dict[str, Any],
        dynamixel_scan: Dict[str, Any],
        *,
        scan_ethercat: bool,
        scan_dynamixel: bool,
    ) -> List[Dict[str, Any]]:
        ethercat_aliases = {
            self._parse_int(slave.get('ethercat_alias'))
            for slave in ethercat_scan.get('slaves', [])
            if self._parse_int(slave.get('ethercat_alias')) is not None
        }
        dynamixel_ids = {
            self._parse_int(device.get('id'))
            for device in dynamixel_scan.get('devices', [])
            if self._parse_int(device.get('id')) is not None
        }
        rows: List[Dict[str, Any]] = []
        for motor in motors:
            transport = str(motor.get('transport', '')).lower()
            motor_type = str(motor.get('motor_type', '')).lower()
            scanned = False
            scan_available = False
            found = False
            scan_source = ''

            if transport == 'ethercat' and scan_ethercat:
                scanned = not bool(ethercat_scan.get('skipped', False))
                scan_available = bool(ethercat_scan.get('available', False))
                found = self._parse_int(motor.get('alias')) in ethercat_aliases
                scan_source = 'ethercat_slave_scan'
            elif (transport == 'serial' or motor_type == 'dynamixel') and scan_dynamixel:
                scanned = not bool(dynamixel_scan.get('skipped', False))
                scan_available = bool(dynamixel_scan.get('available', False))
                raw_identity = (
                    motor.get('bus_id')
                    if motor.get('bus_id') is not None
                    else motor.get('node_id')
                )
                identity = self._parse_int(raw_identity)
                found = identity in dynamixel_ids
                scan_source = (
                    'runtime_topic'
                    if dynamixel_scan.get('mode') == 'runtime_topic'
                    else 'direct_ping'
                )

            row = {
                'controller_index': motor.get('controller_index'),
                'display_name': motor.get('display_name'),
                'motor_type': motor.get('motor_type'),
                'motor_type_label': motor.get('motor_type_label'),
                'transport': motor.get('transport'),
                'transport_label': motor.get('transport_label'),
                'runtime_state': motor.get('connection_state', 'unknown'),
                'runtime_reason': motor.get('connection_reason', ''),
                'connection_state': motor.get('connection_state', 'unknown'),
                'connection_connected': bool(motor.get('connection_connected', False)),
                'connection_confirmed': bool(motor.get('connection_confirmed', False)),
                'connection_reason': motor.get('connection_reason', ''),
                'connection_source': motor.get('connection_source', 'runtime_topic'),
                'connection_message': motor.get('connection_message', ''),
            }
            if scanned and scan_available:
                row.update({
                    'discovery_state': 'detected' if found else 'missing',
                    'discovery_detected': found,
                    'discovery_confirmed': True,
                    'discovery_source': scan_source,
                    'discovery_message': self._connection_message(
                        'scan_detected' if found else 'scan_missing'
                    ),
                })
            elif scanned and not scan_available:
                row.update({
                    'discovery_state': 'unknown',
                    'discovery_detected': False,
                    'discovery_confirmed': False,
                    'discovery_source': scan_source or 'bus_scan',
                    'discovery_message': self._connection_message('scan_failed'),
                })
            else:
                row.update({
                    'discovery_state': 'not_scanned',
                    'discovery_detected': False,
                    'discovery_confirmed': False,
                    'discovery_source': '',
                    'discovery_message': '이 통신 버스는 이번 검색 대상이 아닙니다.',
                })
            rows.append(row)
        return rows

    @staticmethod
    def _connection_summary(items: List[Dict[str, Any]]) -> Dict[str, Any]:
        counts: Dict[str, int] = {}
        for item in items:
            state = str(item.get('connection_state') or 'unknown')
            counts[state] = counts.get(state, 0) + 1
        online = counts.get('online', 0)
        confirmed = sum(1 for item in items if item.get('connection_confirmed', False))
        return {
            'total': len(items),
            'online': online,
            'offline': counts.get('offline', 0),
            'bus_down': counts.get('bus_down', 0),
            'stale': counts.get('stale', 0),
            'initializing': counts.get('initializing', 0),
            'monitoring_off': counts.get('monitoring_off', 0),
            'unknown': counts.get('unknown', 0),
            'confirmed': confirmed,
            'all_online': bool(items) and online == len(items),
            'counts': counts,
        }

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
                'ethercat_master_index': metadata.get('ethercat_master_index', 0),
                'slave_position': metadata.get('slave_position'),
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
                'ethercat_master_index': motor.get(
                    'ethercat_master_index',
                    axes_by_index.get(controller_index, {}).get(
                        'ethercat_master_index', 0
                    ),
                ),
                'slave_position': motor.get(
                    'slave_position',
                    axes_by_index.get(controller_index, {}).get('slave_position'),
                ),
                'state': motor.get('state', 'unknown'),
                'state_detail': motor.get('state_detail', ''),
                'connection_state': motor.get('connection_state', 'unknown'),
                'connection_connected': bool(motor.get('connection_connected', False)),
                'connection_confirmed': bool(motor.get('connection_confirmed', False)),
                'connection_reason': motor.get('connection_reason', ''),
                'connection_source': motor.get('connection_source', ''),
                'connection_message': motor.get('connection_message', ''),
                'fault': bool(motor.get('fault', False)),
                'age_sec': motor.get('age_sec'),
                'station_alias_register': motor.get('station_alias_register'),
            }

        return [axes_by_index[index] for index in sorted(axes_by_index)]

    def _scan_ethercat_slaves(self) -> Dict[str, Any]:
        started_at = time.time()
        self._publish_scan_progress(
            'ethercat_preflight',
            'EtherCAT Slave 운전 상태를 확인합니다',
            transport='ethercat',
        )
        try:
            master_status = subprocess.run(
                ['ethercat', 'master'],
                check=False,
                capture_output=True,
                text=True,
                timeout=2.0,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {
                'available': False,
                'complete': False,
                'direct': True,
                'rescan_performed': False,
                'rescan_blocked': True,
                'scanned_at': started_at,
                'error': f'EtherCAT Master 운전 상태 확인 실패: {exc}',
                'slaves_count': 0,
                'slaves': [],
            }

        if master_status.returncode != 0:
            return {
                'available': False,
                'complete': False,
                'direct': True,
                'rescan_performed': False,
                'rescan_blocked': True,
                'scanned_at': started_at,
                'error': (
                    'EtherCAT Master 운전 상태 확인 실패: '
                    + (master_status.stderr.strip() or master_status.stdout.strip())
                ),
                'slaves_count': 0,
                'slaves': [],
            }

        master_output = master_status.stdout
        master_claimed = bool(
            re.search(r'^\s*Phase:\s*Operation\s*$', master_output, re.MULTILINE | re.IGNORECASE)
            or re.search(r'^\s*Active:\s*yes\s*$', master_output, re.MULTILINE | re.IGNORECASE)
        )
        if master_claimed:
            return {
                'available': False,
                'complete': False,
                'direct': True,
                'rescan_performed': False,
                'rescan_blocked': True,
                'scanned_at': started_at,
                'error': (
                    'EtherCAT Master를 모터 제어 프로그램이 사용 중입니다. '
                    '초기화 또는 운전 중 버스 재열거는 안전하지 않아 직접 스캔을 중단했습니다'
                ),
                'slaves_count': 0,
                'slaves': [],
            }
        try:
            preflight = subprocess.run(
                ['ethercat', 'slaves', '-v'],
                check=False,
                capture_output=True,
                text=True,
                timeout=2.0,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {
                'available': False,
                'complete': False,
                'direct': True,
                'scanned_at': started_at,
                'error': str(exc),
                'slaves_count': 0,
                'slaves': [],
            }

        if preflight.returncode != 0:
            return {
                'available': False,
                'complete': False,
                'direct': True,
                'scanned_at': started_at,
                'error': preflight.stderr.strip() or preflight.stdout.strip(),
                'slaves_count': 0,
                'slaves': [],
            }

        preflight_slaves = self._parse_ethercat_slaves(preflight.stdout)
        active_states = sorted({
            str(slave.get('device_state') or '').upper()
            for slave in preflight_slaves
            if str(slave.get('device_state') or '').upper() in {'SAFEOP', 'OP'}
        })
        if active_states:
            reason = f'EtherCAT Slave가 {"/".join(active_states)} 상태입니다'
            return {
                'available': False,
                'complete': False,
                'direct': True,
                'rescan_performed': False,
                'rescan_blocked': True,
                'scanned_at': started_at,
                'error': (
                    f'{reason}. 운전 중 버스 재열거는 안전하지 않아 직접 스캔을 중단했습니다'
                ),
                'slaves_count': 0,
                'slaves': [],
            }

        self._publish_scan_progress(
            'ethercat_rescan',
            '기존 Slave 정보를 폐기하고 물리 EtherCAT 버스를 재열거합니다',
            transport='ethercat',
        )
        try:
            rescan_started_at = time.time()
            rescanned = subprocess.run(
                ['ethercat', 'rescan'],
                check=False,
                capture_output=True,
                text=True,
                timeout=3.0,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {
                'available': False,
                'complete': False,
                'direct': True,
                'rescan_performed': False,
                'scanned_at': started_at,
                'error': f'EtherCAT 버스 재스캔 실패: {exc}',
                'slaves_count': 0,
                'slaves': [],
            }
        rescan_duration_ms = round((time.time() - rescan_started_at) * 1000.0, 3)
        if rescanned.returncode != 0:
            return {
                'available': False,
                'complete': False,
                'direct': True,
                'rescan_performed': False,
                'rescan_duration_ms': rescan_duration_ms,
                'scanned_at': started_at,
                'error': (
                    'EtherCAT 버스 재스캔 실패: '
                    + (rescanned.stderr.strip() or rescanned.stdout.strip() or 'unknown error')
                ),
                'slaves_count': 0,
                'slaves': [],
            }

        self._publish_scan_progress(
            'ethercat_rescan_done',
            f'ethercat rescan 명령 실제 실행 완료 ({rescan_duration_ms:g}ms)',
            transport='ethercat',
            details={'rescan_duration_ms': rescan_duration_ms},
        )

        slaves: List[Dict[str, Any]] = []
        previous_signature = None
        topology_stable = False
        listing_error = ''
        for attempt in range(60):
            try:
                completed = subprocess.run(
                    ['ethercat', 'slaves', '-v'],
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=2.0,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                listing_error = str(exc)
                break
            if completed.returncode != 0:
                listing_error = completed.stderr.strip() or completed.stdout.strip()
            else:
                slaves = self._parse_ethercat_slaves(completed.stdout)
                signature = tuple(
                    (
                        slave.get('master_index'),
                        slave.get('slave_position'),
                        slave.get('vendor_id'),
                        slave.get('product_code'),
                        slave.get('revision_number'),
                        slave.get('serial_number'),
                    )
                    for slave in slaves
                )
                identity_ready = bool(slaves) and all(
                    all(
                        int(slave.get(key) or 0) > 0
                        for key in ('vendor_id', 'product_code', 'revision_number', 'serial_number')
                    )
                    for slave in slaves
                )
                if identity_ready and signature == previous_signature:
                    topology_stable = True
                    break
                previous_signature = signature if identity_ready else None
            if attempt < 59:
                time.sleep(0.05)

        errors: List[str] = []
        if not slaves:
            return {
                'available': False,
                'complete': False,
                'direct': True,
                'rescan_performed': True,
                'rescan_duration_ms': rescan_duration_ms,
                'scanned_at': started_at,
                'error': listing_error or '재스캔 후 연결된 EtherCAT Slave를 찾지 못했습니다',
                'slaves_count': 0,
                'slaves': [],
            }
        if not topology_stable:
            errors.append('재스캔 후 Slave 목록이 제한 시간 안에 안정화되지 않았습니다')
        else:
            self._publish_scan_progress(
                'ethercat_topology',
                f'새로 열거된 EtherCAT Slave {len(slaves)}개를 확인했습니다',
                transport='ethercat',
                details={'slaves_count': len(slaves)},
            )
        for slave in slaves:
            master_index = int(slave.get('master_index') or 0)
            position = slave['slave_position']
            self._publish_scan_progress(
                'ethercat_slave_read',
                (
                    f'Master {master_index} · Slave {position}: '
                    'SII EEPROM과 Alias 레지스터를 읽습니다'
                ),
                transport='ethercat',
                details={
                    'master_index': master_index,
                    'slave_position': position,
                },
            )
            master_identity = {
                key: slave.get(key)
                for key in ('vendor_id', 'product_code', 'revision_number', 'serial_number')
            }
            sii_identity = self._read_sii_identity(master_index, position)
            slave['master_identity'] = master_identity
            slave.update(sii_identity)
            rotary = self._read_station_alias_register(master_index, position)
            slave.update(rotary)
            slave_errors = []
            if slave.get('sii_error'):
                slave_errors.append(str(slave['sii_error']))
            if slave.get('rotary_alias_error'):
                slave_errors.append(str(slave['rotary_alias_error']))
            identity_mismatches = []
            if not slave.get('sii_error'):
                for key in ('vendor_id', 'product_code', 'revision_number', 'serial_number'):
                    master_value = master_identity.get(key)
                    sii_value = slave.get(key)
                    if (
                        master_value is not None
                        and sii_value is not None
                        and int(master_value) > 0
                        and master_value != sii_value
                    ):
                        identity_mismatches.append(
                            f'{key} master={master_value} SII={sii_value}'
                        )
            slave['identity_consistent'] = not identity_mismatches
            if identity_mismatches:
                slave_errors.append(
                    'Master/SII 장치 식별값 불일치: ' + ', '.join(identity_mismatches)
                )
            slave['direct_read_complete'] = not slave_errors
            slave['scan_error'] = ' / '.join(slave_errors)
            if slave_errors:
                errors.append(
                    f'Master {master_index} · Slave '
                    f'{slave["slave_position"]}: {slave["scan_error"]}'
                )
            self._publish_scan_progress(
                'ethercat_slave_done' if not slave_errors else 'ethercat_slave_failed',
                (
                    f'Master {master_index} · Slave {position}: '
                    f'Alias {slave.get("ethercat_alias")}, '
                    f'Serial {slave.get("serial_number")} 읽기 완료'
                    if not slave_errors
                    else (
                        f'Master {master_index} · Slave {position}: '
                        f'{slave["scan_error"]}'
                    )
                ),
                transport='ethercat',
                details={
                    'master_index': master_index,
                    'slave_position': position,
                    'ethercat_alias': slave.get('ethercat_alias'),
                    'serial_number': slave.get('serial_number'),
                    'success': not slave_errors,
                },
            )

        configured_master_indices = sorted({
            int(value)
            for value in re.findall(
                r'^\s*Master\s*(\d+)\s*$',
                master_output,
                re.MULTILINE | re.IGNORECASE,
            )
        })
        if not configured_master_indices:
            configured_master_indices = sorted({
                int(slave.get('master_index') or 0) for slave in slaves
            })
        master_results = []
        for master_index in configured_master_indices:
            master_slaves = [
                slave
                for slave in slaves
                if int(slave.get('master_index') or 0) == master_index
            ]
            master_errors = [
                str(slave.get('scan_error') or '')
                for slave in master_slaves
                if str(slave.get('scan_error') or '')
            ]
            if not master_slaves:
                master_errors.append('재스캔 후 응답한 Slave가 없습니다')
                errors.append(
                    f'Master {master_index}: 재스캔 후 응답한 Slave가 없습니다'
                )
            master_results.append({
                'master_index': master_index,
                'complete': bool(master_slaves) and not master_errors,
                'slaves_count': len(master_slaves),
                'error': ' / '.join(master_errors),
            })

        return {
            'available': True,
            'complete': not errors,
            'direct': True,
            'source': 'ethercat_rescan_sii_and_register',
            'rescan_performed': True,
            'rescan_duration_ms': rescan_duration_ms,
            'scan_duration_ms': round((time.time() - started_at) * 1000.0, 3),
            'scanned_at': started_at,
            'error': ' / '.join(errors),
            'masters_count': len(master_results),
            'masters': master_results,
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
                    'revision_number': None,
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
            elif key == 'Revision number':
                current['revision_number'] = self._parse_int(first_value)
            elif key == 'Serial number':
                current['serial_number'] = self._parse_int(first_value)
            elif key == 'Order number':
                current['order_number'] = value
            elif key == 'Device name':
                current['device_name'] = value

        if current is not None:
            slaves.append(current)
        return slaves

    @staticmethod
    def _parse_sii_identity(data: bytes) -> Dict[str, Any]:
        if not isinstance(data, (bytes, bytearray)) or len(data) < 32:
            raise ValueError('SII EEPROM 헤더가 32바이트보다 짧습니다')
        return {
            'ethercat_alias': int.from_bytes(data[8:10], 'little'),
            'vendor_id': int.from_bytes(data[16:20], 'little'),
            'product_code': int.from_bytes(data[20:24], 'little'),
            'revision_number': int.from_bytes(data[24:28], 'little'),
            'serial_number': int.from_bytes(data[28:32], 'little'),
        }

    def _read_sii_identity(
        self, master_index: int, slave_position: int
    ) -> Dict[str, Any]:
        try:
            completed = subprocess.run(
                [
                    'ethercat',
                    'sii_read',
                    '-m',
                    str(master_index),
                    '-p',
                    str(slave_position),
                ],
                check=False,
                capture_output=True,
                timeout=2.0,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {
                'ethercat_alias': None,
                'sii_error': f'SII EEPROM 읽기 실패: {exc}',
                'identity_source': 'physical_sii',
            }
        if completed.returncode != 0:
            detail = completed.stderr.decode('utf-8', errors='replace').strip()
            return {
                'ethercat_alias': None,
                'sii_error': f'SII EEPROM 읽기 실패: {detail or "unknown error"}',
                'identity_source': 'physical_sii',
            }
        try:
            identity = self._parse_sii_identity(completed.stdout)
        except ValueError as exc:
            return {
                'ethercat_alias': None,
                'sii_error': str(exc),
                'identity_source': 'physical_sii',
            }
        return {
            **identity,
            'sii_error': '',
            'identity_source': 'physical_sii',
        }

    def _read_station_alias_register(
        self, master_index: int, slave_position: int
    ) -> Dict[str, Any]:
        completed = None
        last_error = ''
        for attempt in range(3):
            try:
                completed = subprocess.run(
                    [
                        'ethercat',
                        'reg_read',
                        '-m',
                        str(master_index),
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
                last_error = str(exc)
                completed = None
            if completed is not None and completed.returncode == 0:
                break
            if completed is not None:
                last_error = completed.stderr.strip() or completed.stdout.strip()
            if attempt < 2:
                self._publish_scan_progress(
                    'ethercat_register_retry',
                    (
                        f'Master {master_index} · Slave {slave_position}: '
                        'Alias 레지스터 응답 지연, '
                        f'{attempt + 2}번째 읽기를 재시도합니다'
                    ),
                    transport='ethercat',
                    details={
                        'master_index': master_index,
                        'slave_position': slave_position,
                        'next_attempt': attempt + 2,
                        'error': last_error,
                    },
                )
                time.sleep(0.05)

        if completed is None or completed.returncode != 0:
            return {
                'rotary_alias': None,
                'rotary_alias_hex': '',
                'rotary_alias_error': last_error or 'Alias 레지스터 읽기 실패',
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
        self._publish_scan_progress(
            'dynamixel_targets',
            'Dynamixel 직렬 포트와 검색 대상을 확인합니다',
            transport='dynamixel',
        )
        targets = self._dynamixel_scan_targets()

        if not targets:
            self._publish_scan_progress(
                'dynamixel_unavailable',
                'Dynamixel 직렬 포트를 찾지 못해 실제 Ping을 실행할 수 없습니다',
                transport='dynamixel',
            )
            return {
                'available': False,
                'complete': False,
                'direct': True,
                'scanned_at': started_at,
                'mode': 'direct_ping',
                'protocol': DYNAMIXEL_SCAN_PROTOCOL,
                'scan_rule': 'auto serial port, baudrate 1000000, broadcast ping plus ID 0-252',
                'error': (
                    'Dynamixel 직렬 포트를 찾지 못했습니다. '
                    '/dev/serial/by-id, /dev/ttyUSB*, /dev/ttyACM* 경로를 확인했습니다.'
                ),
                'targets': [],
                'devices_count': 0,
                'devices': [],
            }

        devices: List[Dict[str, Any]] = []
        errors: List[str] = []
        for target in targets:
            port = str(target.get('port') or '')
            baudrate = int(target.get('baudrate') or 0)
            ids = list(target.get('ids') or [])
            if not port or not baudrate:
                continue
            self._publish_scan_progress(
                'dynamixel_port',
                f'{port} @ {baudrate}bps에서 직접 Ping을 시작합니다',
                transport='dynamixel',
                details={'port': port, 'baudrate': baudrate},
            )
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
                    self._publish_scan_progress(
                        'dynamixel_device',
                        f'Dynamixel ID {device.get("id")}: 실제 Ping 응답 확인',
                        transport='dynamixel',
                        details={
                            'id': device.get('id'),
                            'model_number': device.get('model_number'),
                            'port': port,
                        },
                    )
            finally:
                os.close(fd)

        return {
            'available': bool(devices),
            'complete': bool(devices) and not errors,
            'direct': True,
            'scanned_at': started_at,
            'mode': 'direct_ping',
            'protocol': DYNAMIXEL_SCAN_PROTOCOL,
            'scan_rule': 'auto serial port, baudrate 1000000, broadcast ping plus ID 0-252',
            'error': '; '.join(errors) if errors else (
                '' if devices else '직접 Ping에 응답한 Dynamixel이 없습니다'
            ),
            'warning': '',
            'targets': targets,
            'attempts': max(1, int(self.dynamixel_scan_attempts)),
            'id_fallback': bool(self.dynamixel_scan_id_fallback),
            'devices_count': len(devices),
            'devices': devices,
        }

    @staticmethod
    def _dynamixel_device_has_valid_model(device: Dict[str, Any]) -> bool:
        model_number = device.get('model_number')
        try:
            return int(model_number) > 0
        except (TypeError, ValueError):
            return False

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
        scan_max_id = max(0, min(252, int(self.dynamixel_scan_max_id)))
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
        axes_by_identity: Dict[tuple, List[Dict[str, Any]]] = {}
        for axis in configured_axes:
            identity = self._configured_ethercat_identity(axis)
            if identity is not None:
                axes_by_identity.setdefault(identity, []).append(axis)

        seen_identities = set()
        rows: List[Dict[str, Any]] = []
        for slave in slaves:
            identity = self._scanned_ethercat_identity(slave)
            if identity is not None:
                seen_identities.add(identity)
            axes = axes_by_identity.get(identity, [])
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
            identity = self._configured_ethercat_identity(axis)
            if identity is None or identity in seen_identities:
                continue
            rows.append(self._matching_row(None, axis, 'missing'))

        return rows

    def _configured_ethercat_identity(
        self, axis: Dict[str, Any]
    ) -> Optional[tuple]:
        master_index = self._parse_int(axis.get('ethercat_master_index')) or 0
        alias = self._parse_int(axis.get('ethercat_alias'))
        if alias is not None and alias > 0:
            return ('alias', master_index, alias)
        position = self._parse_int(axis.get('slave_position'))
        if position is None:
            return None
        return ('position', master_index, position)

    def _scanned_ethercat_identity(
        self, slave: Dict[str, Any]
    ) -> Optional[tuple]:
        master_index = self._parse_int(slave.get('master_index')) or 0
        alias = self._parse_int(slave.get('ethercat_alias'))
        if alias is None or alias <= 0:
            alias = self._parse_int(slave.get('rotary_alias'))
        if alias is not None and alias > 0:
            return ('alias', master_index, alias)
        position = self._parse_int(slave.get('slave_position'))
        if position is None:
            return None
        return ('position', master_index, position)

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
