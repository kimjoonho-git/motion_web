import json
import math
import threading
import time
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import rclpy
import yaml
from motion_control_msgs.msg import MotorStatus
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy
from std_msgs.msg import String
from std_srvs.srv import SetBool, Trigger
from rclpy.action import ActionServer, CancelResponse
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.executors import MultiThreadedExecutor

from motion_coordination_interfaces.action import MotorScan

from .ethercat_scanner import EthercatScanner
from .connection_state import (
    CommunicationHealth,
    build_scan_connection_rows,
    connection_summary,
    set_connection_fields,
    set_physical_connection_fields,
)
from .motor_values import (
    MOTOR_TYPE_LABELS,
    unchecked_float,
    statusword_text,
    dynamixel_statusword_text,
    error_text,
    normalized_errorcode,
    hex16,
    motor_type_label,
    transport_label,
    count_values,
    array_value,
    dynamixel_position_raw,
)
from .dynamixel_scanner import (
    DynamixelScanner,
    DYNAMIXEL_SCAN_BAUDRATES,
    DYNAMIXEL_SCAN_MAX_ID,
    DYNAMIXEL_SCAN_PROTOCOL,
)
from motion_common import topics


MOTOR_TYPE_CATALOG = [
    {'type': 'minas', 'label': MOTOR_TYPE_LABELS['minas']},
    {'type': 'zeroerr', 'label': MOTOR_TYPE_LABELS['zeroerr']},
    {'type': 'dynamixel', 'label': MOTOR_TYPE_LABELS['dynamixel']},
    {'type': 'cubemars', 'label': MOTOR_TYPE_LABELS['cubemars']},
    {'type': 'unknown', 'label': MOTOR_TYPE_LABELS['unknown']},
]

COMMUNICATION_UNAVAILABLE_ERROR = 0xFFFF
MOTOR_SCAN_CONTRACT_VERSION = 3


class MotionStateMonitor(Node):
    """Read-only monitor that converts motion_system status into /motion_control/motion_state JSON."""

    def __init__(self) -> None:
        super().__init__('motion_state_monitor')

        self.input_topic = self.declare_parameter(
            'input_topic',
            topics.MOTOR_STATUS,
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
            topics.MOTION_STATE,
        ).value
        self.scan_progress_topic = self.declare_parameter(
            'scan_progress_topic',
            topics.MOTOR_SCAN_PROGRESS,
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
            self.declare_parameter('dynamixel_scan_timeout_sec', 0.02).value
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
        self._health = CommunicationHealth(self)
        self._motor_metadata: Dict[int, Dict[str, Any]] = {}
        self._last_motor_status_at: Optional[float] = None
        self._last_motor_status_processed_at: Optional[float] = None
        self._last_ethercat_physical_scan: Dict[str, Any] = {}
        self._last_disabled_publish_at = 0.0
        self._started_at = time.time()
        self._subscription = None
        # EtherCAT 물리 스캔은 별도 객체가 맡는다 (§6-32)
        self._ethercat = EthercatScanner(self)
        self._dynamixel = DynamixelScanner(self)
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
        # 장기 작업 Action · 진행 상황을 같은 통로로 보내고 취소를 받는다 (§6-26)
        # 기존 Trigger 서비스는 그대로 둔다 · 구코드 호출자가 남아 있을 수 있다
        self._scan_action_group = ReentrantCallbackGroup()
        self._scan_action_server = ActionServer(
            self,
            MotorScan,
            'motor_scan',
            execute_callback=self._execute_scan_goal,
            cancel_callback=self._accept_scan_cancel,
            callback_group=self._scan_action_group,
        )
        #: 실행 중인 Action 목표 · 진행 이벤트를 여기로도 보낸다
        self._active_scan_goal = None
        self._scan_goal_lock = threading.Lock()
        self._load_motor_metadata()

        if self.monitoring_enabled:
            self._create_input_subscription()

        period_sec = 1.0 / max(self.publish_hz, 0.1)
        self._timer = self.create_timer(period_sec, self._publish_motion_state)
        self._ethercat_poll_timer = self.create_timer(
            0.5,
            self._ethercat._poll_ethercat_bus_status,
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
                    'motor_type_label': motor_type_label(motor_type),
                    'transport': transport,
                    'transport_label': transport_label(transport),
                    'master_id': master_id,
                    'ethercat_master_index': ethercat_master_index,
                    'driver_id': slave.get('driver_id'),
                    'driver_model': driver_model,
                    'pulse_per_revolution': pulse_per_revolution,
                    **raw_model,
                    'rated_power_w': unchecked_float(driver.get('rated_power_w')),
                    'rated_torque_nm': unchecked_float(
                        driver.get('rated_effort', driver.get('rated_torque'))
                    ),
                    'rated_current_a': unchecked_float(driver.get('rated_current')),
                    'rated_speed_rpm': unchecked_float(driver.get('rated_speed_rpm')),
                    'speed': unchecked_float(driver.get('speed')),
                    'acceleration': unchecked_float(driver.get('acceleration')),
                    'deceleration': unchecked_float(driver.get('deceleration')),
                    'profile_velocity': unchecked_float(driver.get('profile_velocity')),
                    'profile_acceleration': unchecked_float(driver.get('profile_acceleration')),
                    'profile_deceleration': unchecked_float(driver.get('profile_deceleration')),
                    'lower': unchecked_float(driver.get('lower')),
                    'upper': unchecked_float(driver.get('upper')),
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
        pulse_per_revolution = unchecked_float(driver.get('pulse_per_revolution'))
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
        if not model_path.is_absolute():
            model_path = param_path.parent / model_path
        info['dynamixel_model_file'] = str(model_path.resolve())
        model_info = self._read_dynamixel_model_file(model_path)
        info.update(model_info)

        min_raw = unchecked_float(info.get('dynamixel_min_position_raw'))
        max_raw = unchecked_float(info.get('dynamixel_max_position_raw'))
        min_rad = unchecked_float(info.get('dynamixel_min_radian'))
        max_rad = unchecked_float(info.get('dynamixel_max_radian'))
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
            value = unchecked_float(parts[1])
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

    # ------------------------------------------------------------------ #
    # 모터 검색 Action · §6-26
    # ------------------------------------------------------------------ #

    #: 취소를 받아들이는 지점 · 물리 검색 한 종류가 끝난 뒤
    SCAN_TRANSPORTS = {
        'all': (True, True),
        'ac_servo': (True, False),
        'dynamixel': (False, True),
    }

    def _accept_scan_cancel(self, goal_handle) -> CancelResponse:
        """취소 요청을 받아들인다.

        **진행 중인 물리 검색을 중간에 끊지는 않는다.** `ethercat rescan`과
        Dynamixel Ping은 시작하면 끝까지 간다 · 모터 스캔 불변조건이 요구하는
        바다. 취소는 **다음 장치 종류로 넘어가기 전**에 확인한다.
        """
        del goal_handle
        self.get_logger().info('motor scan cancel requested')
        return CancelResponse.ACCEPT

    def _execute_scan_goal(self, goal_handle):
        transport = str(goal_handle.request.transport or 'all').strip().lower()
        if transport not in self.SCAN_TRANSPORTS:
            goal_handle.abort()
            result = MotorScan.Result()
            result.success = False
            result.message = json.dumps(
                {'error': f'지원하지 않는 검색 종류입니다: {transport}'},
                ensure_ascii=False,
            )
            return result

        scan_ethercat, scan_dynamixel = self.SCAN_TRANSPORTS[transport]
        with self._scan_goal_lock:
            self._active_scan_goal = goal_handle
        try:
            payload = self._build_scan_result(
                scan_ethercat=scan_ethercat,
                scan_dynamixel=scan_dynamixel,
                cancel_requested=goal_handle.is_cancel_requested,
            )
        finally:
            with self._scan_goal_lock:
                self._active_scan_goal = None

        result = MotorScan.Result()
        result.message = json.dumps(payload, ensure_ascii=False, separators=(',', ':'))
        result.cancelled = bool(payload.get('cancelled'))
        if result.cancelled:
            goal_handle.canceled()
            result.success = False
            return result
        if transport == 'all':
            result.success = bool(payload.get('scan_complete'))
        elif transport == 'ac_servo':
            result.success = self._physical_section_success(
                payload.get('ethercat_scan'), 'slaves_count'
            )
        else:
            result.success = self._physical_section_success(
                payload.get('dynamixel_scan'), 'devices_count'
            )
        goal_handle.succeed()
        return result

    def _build_scan_result(
        self,
        *,
        scan_ethercat: bool,
        scan_dynamixel: bool,
        cancel_requested: Optional[Callable[[], bool]] = None,
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
        ethercat = self._ethercat._current_ethercat_status(now)
        motors = self._current_motor_list(now)
        configured_axes = self._configured_axis_list(motors)
        ethercat_scan = (
            self._ethercat._safe_scan_ethercat_slaves()
            if scan_ethercat
            else self._ethercat._skipped_ethercat_scan(now)
        )
        if scan_ethercat:
            self._last_ethercat_physical_scan = deepcopy(ethercat_scan)
        # 장치 종류 사이에서만 취소를 본다 · 진행 중인 물리 검색은 끊지 않는다
        cancelled = bool(cancel_requested()) if callable(cancel_requested) else False
        if cancelled and scan_dynamixel:
            self._publish_scan_progress(
                'cancelled',
                'EtherCAT 검색 후 취소 요청을 확인해 Dynamixel 검색을 중단합니다',
                transport='dynamixel',
            )
            scan_dynamixel = False
        dynamixel_scan = (
            self._dynamixel._safe_scan_dynamixel_motors()
            if scan_dynamixel
            else self._dynamixel._skipped_dynamixel_scan(now)
        )
        matching_rows = (
            self._build_matching_rows(ethercat_scan.get('slaves', []), configured_axes)
            if scan_ethercat
            else []
        )
        connection_rows = build_scan_connection_rows(
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
            'motor_type_counts': count_values(motors, 'motor_type_label'),
            'transport_counts': count_values(motors, 'transport_label'),
            'ethercat_scan': ethercat_scan,
            'dynamixel_scan': dynamixel_scan,
            'matching_rows': matching_rows,
            'matching_summary': self._matching_summary(matching_rows),
            'connection_rows': connection_rows,
            'connection_summary': connection_summary(connection_rows),
            'connected_axes': connected_axes,
            'known_axes': configured_axes,
            # 취소로 남은 장치 종류를 건너뛰었는가 (§6-26)
            'cancelled': cancelled,
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
                'connection_summary': connection_summary(connection_rows),
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
        self._send_scan_feedback(event)

    def _send_scan_feedback(self, event: Dict[str, Any]) -> None:
        """같은 진행 이벤트를 Action 목표에도 보낸다 (§6-26).

        토픽은 그대로 둔다 · 화면과 구코드 호출자가 아직 그것을 본다.
        """
        lock = getattr(self, '_scan_goal_lock', None)
        if lock is None:
            # Action 서버 없이 세운 시험 스텁 · 토픽 발행만 한다
            return
        with lock:
            goal = self._active_scan_goal
        if goal is None:
            return
        feedback = MotorScan.Feedback()
        feedback.scan_id = str(event.get('scan_id') or '')
        feedback.phase = str(event.get('phase') or '')
        feedback.transport = str(event.get('transport') or '')
        feedback.message = str(event.get('message') or '')
        feedback.details = json.dumps(
            event.get('details') or {}, ensure_ascii=False, separators=(',', ':')
        )
        feedback.timestamp = float(event.get('timestamp') or 0.0)
        try:
            goal.publish_feedback(feedback)
        except Exception as exc:  # noqa: BLE001 - 진행 알림 실패가 스캔을 막으면 안 된다
            self.get_logger().warn(f'scan feedback publish failed: {exc}')

    @staticmethod
    def _physical_section_success(section: Any, count_key: str) -> bool:
        if not isinstance(section, dict):
            return False
        return bool(
            section.get('available')
            and section.get('complete')
            and int(section.get(count_key) or 0) > 0
        )

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
            self._health.update(
                controller_index,
                communication_unavailable,
                now,
            )
            if not communication_unavailable:
                self._last_healthy_motors[controller_index] = deepcopy(motor)
            self._motors[controller_index] = motor

    def _motor_from_status(
        self,
        msg: MotorStatus,
        index: int,
        controller_index: int,
        now: float,
    ) -> Dict[str, Any]:
        statusword = int(array_value(msg, 'statusword', index, 0))
        metadata = self._metadata_for(controller_index)
        position = float(array_value(msg, 'position', index, 0.0))
        velocity = float(array_value(msg, 'velocity', index, 0.0))
        effort = float(array_value(msg, 'effort', index, 0.0))
        raw_errorcode = int(array_value(msg, 'errorcode', index, 0))
        errorcode = normalized_errorcode(raw_errorcode, metadata)
        communication_unavailable = raw_errorcode == COMMUNICATION_UNAVAILABLE_ERROR
        motor_type = str(metadata.get('motor_type', '')).lower()
        is_dynamixel = motor_type == 'dynamixel'
        internal_limit_active = bool(statusword & 0x0800) and not is_dynamixel
        status_text = (
            'Communication unavailable'
            if communication_unavailable
            else (
                dynamixel_statusword_text(statusword)
                if is_dynamixel
                else statusword_text(statusword)
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
            dynamixel_position_raw(position, metadata)
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
            'controlword': int(array_value(msg, 'controlword', index, 0)),
            'statusword': statusword,
            'status_text': status_text,
            'errorcode': errorcode,
            'errorcode_raw': raw_errorcode,
            'errorcode_hex': hex16(raw_errorcode),
            'error_text': (
                'Communication unavailable'
                if communication_unavailable
                else error_text(errorcode, '')
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
            'last_ethercat_status_at': self._ethercat.last_status_at,
            'ethercat': self._ethercat._current_ethercat_status(now),
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
            'connection_summary': connection_summary(motors),
            'motor_type_counts': count_values(motors, 'motor_type_label'),
            'transport_counts': count_values(motors, 'transport_label'),
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

        ethercat_available = bool(self._ethercat.status)
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
                set_connection_fields(motor, state, reason, source, now)
                set_physical_connection_fields(
                    motor,
                    getattr(self, '_last_ethercat_physical_scan', {}),
                )
                motors.append(motor)
                continue

            motor = deepcopy(self._motors[controller_index])
            health = self._health.entries.get(controller_index, {})
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
                self._ethercat._ethercat_axis_state(motor)
                if transport == 'ethercat' and ethercat_available
                else ''
            )
            ethercat_master_status = (
                self._ethercat._ethercat_master_status(motor)
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
            set_connection_fields(motor, state, reason, source, now)
            set_physical_connection_fields(
                    motor,
                    getattr(self, '_last_ethercat_physical_scan', {}),
                )
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
            'errorcode_hex': hex16(0),
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

    def _build_matching_rows(
        self,
        slaves: List[Dict[str, Any]],
        configured_axes: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        axes_by_identity: Dict[tuple, List[Dict[str, Any]]] = {}
        for axis in configured_axes:
            identity = self._ethercat._configured_ethercat_identity(axis)
            if identity is not None:
                axes_by_identity.setdefault(identity, []).append(axis)

        seen_identities = set()
        rows: List[Dict[str, Any]] = []
        for slave in slaves:
            identity = self._ethercat._scanned_ethercat_identity(slave)
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
            identity = self._ethercat._configured_ethercat_identity(axis)
            if identity is None or identity in seen_identities:
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
        metadata['motor_type_label'] = motor_type_label(motor_type)
        metadata['transport'] = transport
        metadata['transport_label'] = transport_label(transport)
        return metadata


def main(args=None) -> None:
    rclpy.init(args=args)
    node = MotionStateMonitor()
    # 스캔이 도는 동안에도 상태 발행과 취소 요청을 받아야 한다 (§6-26)
    executor = MultiThreadedExecutor(num_threads=2)
    executor.add_node(node)
    try:
        executor.spin()
    except KeyboardInterrupt:
        pass
    finally:
        executor.shutdown()
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()
