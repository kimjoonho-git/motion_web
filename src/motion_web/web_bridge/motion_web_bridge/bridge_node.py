import asyncio
import ast
import copy
import hashlib
import json
import math
import os
import re
import socket
import subprocess
import tempfile
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import rclpy
import uvicorn
from urllib.parse import quote
import yaml
from ament_index_python.packages import get_package_share_directory
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
from std_msgs.msg import String
from std_srvs.srv import SetBool, Trigger

from .ethercat_alias_manager import EthercatAliasError, EthercatAliasManager
from .coordination_bridge import (
    CoordinationWebBridge, local_motion_control, local_motion_readiness,
)
from .motor_restart_coordinator import MotorRestartCoordinator
from .motor_restart_diagnostics import diagnose_motor_restart_failure
from .motion_studio_bridge import MotionStudioRosBridge
from .motion_studio_routes import register_motion_studio_routes
from .motion_studio_sync import (
    MotionStudioSync,
    _project_tree_category_signature,
)
from .project_repository import ProjectRepository
from .servo_alarm_policy import (
    CATALOG_VERSION as SERVO_ALARM_CATALOG_VERSION,
    catalog_payload,
    configured_counts,
    effective_grade_map,
    GRADE_DEFINITIONS,
    normalize_overrides,
    policy_revision,
)


DYNAMIXEL_BAUDRATE = 1000000
MOTION_DATA_PERIOD_SEC = 0.02


def motor_activity_snapshot(
    motion_run: Dict[str, Any],
    motion_studio: Dict[str, Any],
    safety_status: Dict[str, Any],
) -> Dict[str, Any]:
    """Return one conservative, display-only motor activity classification."""
    run = motion_run if isinstance(motion_run, dict) else {}
    studio = motion_studio if isinstance(motion_studio, dict) else {}
    safety = safety_status if isinstance(safety_status, dict) else {}
    run_state = str(run.get('state') or 'idle')
    run_phase = str(run.get('phase') or '')
    studio_state = str(studio.get('state') or 'idle')
    owner = str(safety.get('command_owner') or 'none')
    manual_values = safety.get('manual_activity_modes')
    if not isinstance(manual_values, list):
        manual_values = []
    manual_modes = {str(item) for item in manual_values if str(item)}

    def active(kind: str, label: str, source: str) -> Dict[str, Any]:
        return {
            'active': True,
            'kind': kind,
            'label': label,
            'source': source,
            'warning': False,
        }

    if run_state == 'initializing':
        return active('initializing', '초기 위치 이동 중', 'motion_run')
    if run_state in {'running', 'verifying'}:
        if bool(run.get('automation_run')):
            return active('automation', '자동 반복 모션 동작 중', 'motion_run')
        return active('motion_run', '모션 동작 중', 'motion_run')
    if run_state == 'countdown':
        return {
            'active': False,
            'kind': 'countdown',
            'label': '',
            'source': 'motion_run',
            'warning': False,
        }
    if run_state == 'initialized' and studio_state == 'initializing':
        return {
            'active': False,
            'kind': 'initialized',
            'label': '',
            'source': 'motion_run',
            'warning': False,
        }
    if studio_state == 'initializing':
        return active('initializing', '초기 위치 이동 중', 'motion_studio')
    if studio_state == 'playing':
        return active('studio_playback', '모션 스튜디오 동작 중', 'motion_studio')
    if studio_state == 'recording':
        return active('studio_recording', '모션 스튜디오 녹화 중', 'motion_studio')
    if 'action' in manual_modes:
        return active('action', '동작 모드 동작 중', 'motion_supervisor')
    if 'jog' in manual_modes:
        return active('jog', '조그 모드 동작 중', 'motion_supervisor')
    if owner == 'midi':
        return active('midi', 'MIDI 모터 제어 중', 'motion_supervisor')

    repeat_waiting = run_state == 'waiting' and run_phase == 'repeat_waiting'
    if repeat_waiting and owner == 'playback':
        return {
            'active': False,
            'kind': 'repeat_waiting',
            'label': '',
            'source': 'motion_run',
            'warning': False,
        }
    if owner not in {'', 'none'}:
        return {
            'active': True,
            'kind': 'unknown',
            'label': '모터 동작 상태 확인 필요',
            'source': 'motion_supervisor',
            'warning': True,
        }
    return {
        'active': False,
        'kind': 'idle',
        'label': '',
        'source': '',
        'warning': False,
    }


def _monitoring_finite_float(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _monitoring_motor_ref(motor: Dict[str, Any]) -> str:
    text = ' '.join(str(motor.get(key) or '').lower() for key in (
        'motor_type', 'motor_type_label', 'driver_model', 'transport'
    ))
    if 'dynamixel' in text:
        value = motor.get('bus_id', motor.get('node_id'))
        serial_port = str(motor.get('serial_port') or '').strip()
        try:
            return (
                f'dynamixel:port:{quote(serial_port, safe="")}:id:{int(value)}'
                if serial_port else ''
            )
        except (TypeError, ValueError):
            return ''
    if 'minas' in text or 'ac servo' in text or 'ac_servo' in text:
        value = motor.get('alias', motor.get('ethercat_alias'))
        master_index = motor.get('ethercat_master_index', 0)
        try:
            master = int(master_index)
        except (TypeError, ValueError):
            return ''
        try:
            alias = int(value)
        except (TypeError, ValueError):
            alias = 0
        if alias > 0 and master >= 0:
            return f'ac_servo:master:{master}:alias:{alias}'
        try:
            position = int(motor.get('slave_position'))
            return (
                f'ac_servo:master:{master}:slave:{position}'
                if master >= 0 and position >= 0 else ''
            )
        except (TypeError, ValueError):
            return ''
    return ''

def _monitoring_motor_refs(motor: Dict[str, Any]) -> List[str]:
    canonical = _monitoring_motor_ref(motor)
    text = ' '.join(str(motor.get(key) or '').lower() for key in (
        'motor_type', 'motor_type_label', 'driver_model', 'transport'
    ))
    try:
        if 'minas' in text or 'ac servo' in text or 'ac_servo' in text:
            alias = int(motor.get('alias', motor.get('ethercat_alias')))
            legacy = f'ac_servo:alias:{alias}' if alias > 0 else ''
        elif 'dynamixel' in text:
            bus_id = int(motor.get('bus_id', motor.get('node_id')))
            legacy = f'dynamixel:id:{bus_id}' if bus_id >= 0 else ''
        else:
            legacy = ''
    except (TypeError, ValueError):
        legacy = ''
    return [item for item in (canonical, legacy) if item]


def add_monitoring_motion_values(
    motion_state: Dict[str, Any],
    mapping_rows: List[Dict[str, Any]],
    motion_value_state: Optional[Dict[str, Any]] = None,
) -> None:
    """Attach control-layer motion values without deriving them from feedback."""
    motors = motion_state.get('motors')
    if not isinstance(motors, list):
        return
    value_state = motion_value_state if isinstance(motion_value_state, dict) else {}
    received_values = value_state.get('values')
    if not isinstance(received_values, dict):
        received_values = {}
    value_sources = value_state.get('sources')
    if not isinstance(value_sources, dict):
        value_sources = {}
    valid_motors = [motor for motor in motors if isinstance(motor, dict)]
    rows_by_axis: Dict[int, List[Dict[str, Any]]] = {}
    for row in mapping_rows:
        if not isinstance(row, dict) or row.get('enabled') is False:
            continue
        motor_ref = str(row.get('motor_ref') or '').strip()
        axis: Optional[int] = None
        if motor_ref:
            matches = [
                motor for motor in valid_motors
                if motor_ref.lower() in {
                    ref.lower() for ref in _monitoring_motor_refs(motor)
                }
            ]
            if len(matches) == 1:
                try:
                    axis = int(matches[0].get('controller_index'))
                except (TypeError, ValueError):
                    axis = None
        else:
            try:
                axis = int(row.get('motor_axis'))
            except (TypeError, ValueError):
                axis = None
        if axis is not None and axis >= 0:
            rows_by_axis.setdefault(axis, []).append(row)

    for motor in valid_motors:
        motor.update({
            'motion_axis_configured': False,
            'motion_id': None,
            'motion_value_deg': None,
            'motion_value_status': 'unmapped',
            'motion_value_message': '모션축 미설정',
            'motion_value_source': None,
        })
        try:
            axis = int(motor.get('controller_index'))
        except (TypeError, ValueError):
            continue
        rows = rows_by_axis.get(axis, [])
        if not rows:
            continue
        motor['motion_axis_configured'] = True
        if len(rows) != 1:
            motor.update({
                'motion_value_status': 'missing',
                'motion_value_message': '활성 모션축 중복 설정으로 모션값을 연결할 수 없음',
            })
            continue

        row = rows[0]
        motion_id = str(row.get('motion_id') or '').strip()
        motor['motion_id'] = motion_id or None
        motion_value = _monitoring_finite_float(received_values.get(motion_id))
        if not motion_id or motion_value is None:
            motor.update({
                'motion_value_status': 'missing',
                'motion_value_message': '모션값 토픽 미수신',
            })
            continue
        source = str(value_sources.get(motion_id) or '')
        source_label = {'midi': 'MIDI', 'motion_run': '모션 실행'}.get(source, source)
        motor.update({
            'motion_value_deg': round(motion_value, 6),
            'motion_value_status': 'received',
            'motion_value_message': (
                f'{source_label} 제어 모션값 수신' if source_label else '제어 모션값 수신'
            ),
            'motion_value_source': source or None,
        })


def _workspace_root() -> Path:
    configured = str(os.environ.get('MOTION_WORKSPACE') or '').strip()
    if configured:
        return Path(configured).expanduser().resolve()

    candidates = [Path.cwd(), Path(__file__).resolve()]
    try:
        candidates.insert(0, Path(get_package_share_directory('motion_web_bridge')).resolve())
    except Exception:
        pass
    for candidate in candidates:
        for parent in (candidate, *candidate.parents):
            if parent.name == 'install':
                return parent.parent
            if (parent / 'src').is_dir() and (parent / 'scripts').is_dir():
                return parent
    return Path.cwd().resolve()


def _safe_project_publish_stem(value: Any) -> str:
    text = ''.join(
        character if character.isalnum() or character in '._-' else '_'
        for character in str(value or '')
    ).strip('._-')
    return text[:80] or 'project_file'


class MotionWebBridge(Node):
    def __init__(self) -> None:
        super().__init__('motion_web_bridge')
        self.ethercat_alias_manager = EthercatAliasManager()
        self.motion_state_topic = self.declare_parameter(
            'motion_state_topic',
            '/motion_control/motion_state',
        ).value
        self.motion_value_topic = self.declare_parameter(
            'motion_value_topic',
            '/motion_control/motion_value_state',
        ).value
        self.monitoring_service = self.declare_parameter(
            'monitoring_service',
            '/set_monitoring',
        ).value
        self.scan_service = self.declare_parameter('scan_service', '/scan_motors').value
        self.scan_ac_servo_service = self.declare_parameter(
            'scan_ac_servo_service',
            '/scan_ac_servo_motors',
        ).value
        self.scan_dynamixel_service = self.declare_parameter(
            'scan_dynamixel_service',
            '/scan_dynamixel_motors',
        ).value
        self.scan_progress_topic = self.declare_parameter(
            'scan_progress_topic',
            '/motion_control/motor_scan_progress',
        ).value
        self.jog_request_topic = self.declare_parameter(
            'jog_request_topic',
            '/motion_control/manual_jog_request',
        ).value
        self.jog_result_topic = self.declare_parameter(
            'jog_result_topic',
            '/motion_control/manual_jog_result',
        ).value
        self.safety_request_topic = self.declare_parameter(
            'safety_request_topic',
            '/motion_control/safety_request',
        ).value
        self.action_request_topic = self.declare_parameter(
            'action_request_topic',
            '/motion_control/manual_action_request',
        ).value
        self.action_result_topic = self.declare_parameter(
            'action_result_topic',
            '/motion_control/manual_action_result',
        ).value
        self.motion_mapping_request_topic = self.declare_parameter(
            'motion_mapping_request_topic',
            '/motion_control/motion_mapping_request',
        ).value
        self.motion_mapping_response_topic = self.declare_parameter(
            'motion_mapping_response_topic',
            '/motion_control/motion_mapping_response',
        ).value
        self.motion_run_request_topic = self.declare_parameter(
            'motion_run_request_topic',
            '/motion_control/motion_run_request',
        ).value
        self.motion_run_response_topic = self.declare_parameter(
            'motion_run_response_topic',
            '/motion_control/motion_run_response',
        ).value
        self.motion_run_status_topic = self.declare_parameter(
            'motion_run_status_topic',
            '/motion_control/motion_run_status',
        ).value
        self.midi_monitor_state_topic = self.declare_parameter(
            'midi_monitor_state_topic',
            '/motion_web/midi_monitor/state',
        ).value
        self.midi_monitor_request_topic = self.declare_parameter(
            'midi_monitor_request_topic',
            '/motion_web/midi_monitor/request',
        ).value
        self.midi_monitor_response_topic = self.declare_parameter(
            'midi_monitor_response_topic',
            '/motion_web/midi_monitor/response',
        ).value
        self.motion_studio_request_topic = self.declare_parameter(
            'motion_studio_request_topic', '/motion_studio/request'
        ).value
        self.motion_studio_response_topic = self.declare_parameter(
            'motion_studio_response_topic', '/motion_studio/response'
        ).value
        self.motion_studio_status_topic = self.declare_parameter(
            'motion_studio_status_topic', '/motion_studio/status'
        ).value
        self.motion_studio_editor_request_topic = self.declare_parameter(
            'motion_studio_editor_request_topic', '/motion_studio/editor/request'
        ).value
        self.motion_studio_editor_response_topic = self.declare_parameter(
            'motion_studio_editor_response_topic', '/motion_studio/editor/response'
        ).value
        self.safety_status_topic = self.declare_parameter(
            'safety_status_topic', '/motion_control/safety_status'
        ).value
        self.max_jog_delta_deg = float(
            self.declare_parameter('max_jog_delta_deg', 360.0).value
        )
        self.host = self.declare_parameter('host', '0.0.0.0').value
        self.port = int(self.declare_parameter('port', 8000).value)
        self.access_host = str(self.declare_parameter('access_host', '').value)
        self.workspace_root = _workspace_root()
        default_config = self.workspace_root / 'config' / 'bootstrap_motor_config.yaml'
        self.motor_config_file = Path(
            str(self.declare_parameter('motor_config_file', str(default_config)).value)
        ).expanduser()
        # Keep the launch-time file separate from the editable file of the
        # currently selected project. It represents the configuration used by
        # the running motor stack until an explicit apply/restart occurs.
        self.applied_motor_config_file = self.motor_config_file.resolve()
        default_restart_script = self.workspace_root / 'scripts' / 'restart_motion_monitor.sh'
        self.restart_script = Path(
            str(self.declare_parameter('restart_script', str(default_restart_script)).value)
        ).expanduser()
        default_motion_projects_dir = self.workspace_root / 'motion_projects'
        self.motion_projects_dir = Path(
            str(self.declare_parameter(
                'motion_projects_dir', str(default_motion_projects_dir)
            ).value)
        ).expanduser()
        self.project_repository = ProjectRepository(self.motion_projects_dir)
        self.motor_restart_coordinator = MotorRestartCoordinator(
            self.project_repository,
            self._motor_operation_runtime_readiness,
        )
        self._bind_selected_project_sources()
        default_event_log_dir = self.workspace_root / 'log' / 'motor_events'
        self.event_log_dir = Path(
            str(self.declare_parameter('event_log_dir', str(default_event_log_dir)).value)
        ).expanduser()
        self.event_log_dir.mkdir(parents=True, exist_ok=True)
        self.event_log_retention_days = max(
            1,
            int(self.declare_parameter('event_log_retention_days', 14).value),
        )
        self.event_log_max_bytes = max(
            1024 * 1024,
            int(self.declare_parameter('event_log_max_bytes', 10 * 1024 * 1024).value),
        )
        self.event_log_max_records = max(
            100,
            int(self.declare_parameter('event_log_max_records', 5000).value),
        )
        self.event_log_max_files = max(
            1,
            int(self.declare_parameter('event_log_max_files', 14).value),
        )
        self.web_publish_hz = float(self.declare_parameter('web_publish_hz', 10.0).value)
        self._web_access = self._build_web_access_info()

        self._lock = threading.Lock()
        self._motion_state: Optional[Dict[str, Any]] = None
        self._motion_state_received_at: Optional[float] = None
        self._motion_value_lock = threading.Lock()
        self._motion_value_state: Dict[str, Any] = {
            'project_id': '',
            'project_generation': 0,
            'values': {},
            'sources': {},
            'stamps': {},
        }
        self._jog_result_lock = threading.Lock()
        self._jog_results: Dict[str, Dict[str, Any]] = {}
        self._action_result_lock = threading.Lock()
        self._action_results: Dict[str, Dict[str, Any]] = {}
        self._motion_mapping_lock = threading.Lock()
        self._motion_mapping_results: Dict[str, Dict[str, Any]] = {}
        self._motion_run_lock = threading.Lock()
        self._motion_run_results: Dict[str, Dict[str, Any]] = {}
        self._motion_run_status: Dict[str, Any] = {}
        self._coordination_poll_lock = threading.Lock()
        self._coordination_poll_received_monotonic = 0.0
        self._coordination_watchdog_stop_execution_id = ''
        self._midi_monitor_lock = threading.Lock()
        self._midi_monitor_status: Dict[str, Any] = {}
        self._midi_monitor_results: Dict[str, Dict[str, Any]] = {}
        self._motion_studio_lock = threading.Lock()
        self._motion_studio_status: Dict[str, Any] = {}
        self._motion_studio_results: Dict[str, Dict[str, Any]] = {}
        self._motion_studio_workspace_signatures: Dict[str, Dict[str, str]] = {}
        self._motion_studio_command_order_lock = threading.Lock()
        self._motion_studio_start_generation = 0
        self._motion_studio_editor_lock = threading.Lock()
        self._motion_studio_editor_results: Dict[str, Dict[str, Any]] = {}
        self._motion_studio_ros_bridge = MotionStudioRosBridge(self)
        self._motion_studio_sync_service = MotionStudioSync(self)
        self._safety_status_lock = threading.Lock()
        self._safety_status: Dict[str, Any] = {}
        self._execution_context_lock = threading.RLock()
        self._execution_context_apply_lock = threading.Lock()
        self._monitoring_motion_mapping_lock = threading.Lock()
        self._monitoring_motion_mapping_context_id = ''
        self._monitoring_motion_mapping_rows: List[Dict[str, Any]] = []
        self._project_generation_lock = threading.Lock()
        self._project_generation = self.project_repository.project_generation()
        self._supervisor_project_generation = 0
        self._bridge_instance_id = f'{os.getpid()}-{time.time_ns()}'
        self._bridge_started_at = time.time()
        self._execution_context_status: Dict[str, Any] = {
            'state': 'starting',
            'ready': False,
            'message': '현재 프로젝트 실행 컨텍스트 확인 중',
            'context_id': '',
            'project_id': '',
            'nodes': {},
            'updated_at': time.time(),
        }
        self._event_log_lock = threading.RLock()
        self._scan_progress_lock = threading.RLock()
        self._motor_scan_request_lock = threading.Lock()
        self._motor_lifecycle_lock = threading.Lock()
        self._motor_operation_recovery_lock = threading.Lock()
        self._motor_operation_reconcile_lock = threading.Lock()
        self._scan_progress: Dict[str, Any] = {
            'scan_id': '',
            'events': [],
            'running': False,
            'updated_at': None,
        }
        self._active_motor_errors: Dict[str, str] = {}
        self._last_motion_run_state: Optional[str] = None

        self._subscription = self.create_subscription(
            String,
            self.motion_state_topic,
            self._motion_state_callback,
            10,
        )
        motion_value_qos = QoSProfile(
            depth=10,
            reliability=ReliabilityPolicy.RELIABLE,
            durability=DurabilityPolicy.TRANSIENT_LOCAL,
        )
        self._motion_value_subscription = self.create_subscription(
            String,
            self.motion_value_topic,
            self._motion_value_callback,
            motion_value_qos,
        )
        self._scan_progress_subscription = self.create_subscription(
            String,
            self.scan_progress_topic,
            self._scan_progress_callback,
            20,
        )
        self._monitoring_client = self.create_client(SetBool, self.monitoring_service)
        self._scan_client = self.create_client(Trigger, self.scan_service)
        self._scan_ac_servo_client = self.create_client(Trigger, self.scan_ac_servo_service)
        self._scan_dynamixel_client = self.create_client(Trigger, self.scan_dynamixel_service)
        self._jog_request_publisher = self.create_publisher(String, self.jog_request_topic, 10)
        self._safety_request_publisher = self.create_publisher(
            String, self.safety_request_topic, 10
        )
        self._action_request_publisher = self.create_publisher(String, self.action_request_topic, 10)
        self._motion_mapping_request_publisher = self.create_publisher(
            String,
            self.motion_mapping_request_topic,
            10,
        )
        self._motion_run_request_publisher = self.create_publisher(
            String,
            self.motion_run_request_topic,
            10,
        )
        self._midi_monitor_request_publisher = self.create_publisher(
            String,
            self.midi_monitor_request_topic,
            10,
        )
        self._motion_studio_request_publisher = self.create_publisher(
            String, self.motion_studio_request_topic, 10
        )
        self._motion_studio_editor_request_publisher = self.create_publisher(
            String, self.motion_studio_editor_request_topic, 10
        )
        self._jog_result_subscription = self.create_subscription(
            String,
            self.jog_result_topic,
            self._jog_result_callback,
            10,
        )
        self._action_result_subscription = self.create_subscription(
            String,
            self.action_result_topic,
            self._action_result_callback,
            10,
        )
        self._motion_mapping_response_subscription = self.create_subscription(
            String,
            self.motion_mapping_response_topic,
            self._motion_mapping_response_callback,
            10,
        )
        self._motion_run_response_subscription = self.create_subscription(
            String,
            self.motion_run_response_topic,
            self._motion_run_response_callback,
            10,
        )
        self._motion_run_status_subscription = self.create_subscription(
            String,
            self.motion_run_status_topic,
            self._motion_run_status_callback,
            10,
        )
        self._midi_monitor_state_subscription = self.create_subscription(
            String,
            self.midi_monitor_state_topic,
            self._midi_monitor_state_callback,
            10,
        )
        self._midi_monitor_response_subscription = self.create_subscription(
            String,
            self.midi_monitor_response_topic,
            self._midi_monitor_response_callback,
            10,
        )
        self._motion_studio_response_subscription = self.create_subscription(
            String,
            self.motion_studio_response_topic,
            self._motion_studio_response_callback,
            10,
        )
        self._motion_studio_status_subscription = self.create_subscription(
            String,
            self.motion_studio_status_topic,
            self._motion_studio_status_callback,
            10,
        )
        self._motion_studio_editor_response_subscription = self.create_subscription(
            String,
            self.motion_studio_editor_response_topic,
            self._motion_studio_editor_response_callback,
            10,
        )
        self._safety_status_subscription = self.create_subscription(
            String,
            self.safety_status_topic,
            self._safety_status_callback,
            10,
        )
        self._coordination_web_bridge = CoordinationWebBridge(
            self,
            self.workspace_root,
            self._current_project_generation,
        )
        self._startup_project_context_timer = self.create_timer(
            1.0, self._schedule_execution_context_reconcile
        )
        self._motor_operation_reconcile_timer = self.create_timer(
            0.2, self._motor_operation_reconcile_callback
        )
        self._coordination_watchdog_timer = self.create_timer(
            0.1, self._coordination_watchdog_callback
        )

        self.get_logger().info(
            f'motion_web_bridge started: topic={self.motion_state_topic}, '
            f'scan_service={self.scan_service}, '
            f'scan_ac_servo_service={self.scan_ac_servo_service}, '
            f'scan_dynamixel_service={self.scan_dynamixel_service}, '
            f'jog_request_topic={self.jog_request_topic}, '
            f'jog_result_topic={self.jog_result_topic}, '
            f'action_request_topic={self.action_request_topic}, '
            f'action_result_topic={self.action_result_topic}, '
            f'motion_mapping_request_topic={self.motion_mapping_request_topic}, '
            f'motion_mapping_response_topic={self.motion_mapping_response_topic}, '
            f'motion_run_request_topic={self.motion_run_request_topic}, '
            f'motion_run_response_topic={self.motion_run_response_topic}, '
            f'max_jog_delta_deg={self.max_jog_delta_deg:g}, '
            f'motor_config_file={self.motor_config_file}, '
            f'motion_projects_dir={self.motion_projects_dir}, '
            f'restart_script={self.restart_script}, '
            f'url={self._web_access["url"]}'
        )

    def _motion_state_callback(self, msg: String) -> None:
        try:
            payload = json.loads(msg.data)
        except json.JSONDecodeError:
            self.get_logger().warn(f'Invalid {self.motion_state_topic} JSON received.')
            return

        if (
            not self._selected_project_owns_runtime()
            or not self._payload_matches_selected_project(
                payload, require_generation=False
            )
        ):
            return
        with self._lock:
            self._motion_state = payload
            self._motion_state_received_at = time.time()
        self._record_motor_error_transitions(payload)

    def _motion_value_callback(self, msg: String) -> None:
        try:
            payload = json.loads(msg.data)
        except json.JSONDecodeError:
            self.get_logger().warn(f'Invalid {self.motion_value_topic} JSON received.')
            return
        if not isinstance(payload, dict):
            return
        project_id = str(payload.get('project_id') or '')
        try:
            generation = int(payload.get('project_generation'))
        except (TypeError, ValueError):
            return
        if (
            project_id != self.project_repository.selected_project_id()
            or generation != self._current_project_generation()
        ):
            return
        raw_values = payload.get('values')
        if not isinstance(raw_values, dict):
            return
        source = str(payload.get('source') or '')
        stamp = _monitoring_finite_float(payload.get('stamp')) or time.time()
        updates = {}
        for motion_id, value in raw_values.items():
            key = str(motion_id or '').strip()
            number = _monitoring_finite_float(value)
            if key and number is not None:
                updates[key] = number
        if not updates:
            return
        with self._motion_value_lock:
            if (
                self._motion_value_state.get('project_id') != project_id
                or self._motion_value_state.get('project_generation') != generation
            ):
                self._motion_value_state = {
                    'project_id': project_id,
                    'project_generation': generation,
                    'values': {},
                    'sources': {},
                    'stamps': {},
                }
            values = self._motion_value_state['values']
            sources = self._motion_value_state['sources']
            stamps = self._motion_value_state['stamps']
            for motion_id, value in updates.items():
                if stamp < float(stamps.get(motion_id) or 0.0):
                    continue
                values[motion_id] = value
                sources[motion_id] = source
                stamps[motion_id] = stamp

    def _scan_progress_callback(self, msg: String) -> None:
        try:
            event = json.loads(msg.data)
        except json.JSONDecodeError:
            self.get_logger().warn(f'Invalid {self.scan_progress_topic} JSON received.')
            return
        if not isinstance(event, dict) or not str(event.get('scan_id') or ''):
            return
        now = time.time()
        with self._scan_progress_lock:
            scan_id = str(event['scan_id'])
            if scan_id != self._scan_progress.get('scan_id'):
                self._scan_progress = {
                    'scan_id': scan_id,
                    'events': [],
                    'running': True,
                    'started_at': event.get('timestamp') or now,
                    'updated_at': now,
                    'project_id': self.project_repository.selected_project_id(),
                    'project_generation': self._current_project_generation(),
                }
            events = self._scan_progress.setdefault('events', [])
            recorded = dict(event)
            recorded['index'] = len(events)
            events.append(recorded)
            if len(events) > 300:
                del events[:-300]
                for index, item in enumerate(events):
                    item['index'] = index
            self._scan_progress['updated_at'] = now
            if event.get('phase') in {'complete', 'completed', 'partial', 'failed'}:
                self._scan_progress['running'] = False
                self._scan_progress['completed_at'] = now

    def motor_scan_progress(self) -> Dict[str, Any]:
        with self._scan_progress_lock:
            progress = copy.deepcopy(self._scan_progress)
        return {
            'success': True,
            'progress': progress,
            'project_id': self.project_repository.selected_project_id(),
            'project_generation': self._current_project_generation(),
        }

    def _jog_result_callback(self, msg: String) -> None:
        try:
            payload = json.loads(msg.data)
        except json.JSONDecodeError:
            self.get_logger().warn(f'Invalid {self.jog_result_topic} JSON received.')
            return
        if not isinstance(payload, dict):
            return

        request_id = str(payload.get('request_id') or '')
        if not request_id or not self._request_matches_current_generation(request_id):
            return

        with self._jog_result_lock:
            self._jog_results[request_id] = payload
            cutoff = time.time() - 10.0
            stale_keys = [
                key for key, value in self._jog_results.items()
                if float(value.get('stamp') or 0.0) < cutoff
            ]
            for key in stale_keys:
                self._jog_results.pop(key, None)

    def _action_result_callback(self, msg: String) -> None:
        try:
            payload = json.loads(msg.data)
        except json.JSONDecodeError:
            self.get_logger().warn(f'Invalid {self.action_result_topic} JSON received.')
            return
        if not isinstance(payload, dict):
            return

        request_id = str(payload.get('request_id') or '')
        if not request_id or not self._request_matches_current_generation(request_id):
            return

        with self._action_result_lock:
            self._action_results[request_id] = payload
            cutoff = time.time() - 10.0
            stale_keys = [
                key for key, value in self._action_results.items()
                if float(value.get('stamp') or 0.0) < cutoff
            ]
            for key in stale_keys:
                self._action_results.pop(key, None)

    def _motion_mapping_response_callback(self, msg: String) -> None:
        try:
            payload = json.loads(msg.data)
        except json.JSONDecodeError:
            self.get_logger().warn(f'Invalid {self.motion_mapping_response_topic} JSON received.')
            return
        if not isinstance(payload, dict):
            return

        request_id = str(payload.get('request_id') or '')
        if not request_id or not self._response_matches_current_generation(payload):
            return

        payload['_received_at'] = time.time()
        with self._motion_mapping_lock:
            self._motion_mapping_results[request_id] = payload
            cutoff = time.time() - 20.0
            stale_keys = [
                key for key, value in self._motion_mapping_results.items()
                if float(value.get('_received_at') or 0.0) < cutoff
            ]
            for key in stale_keys:
                self._motion_mapping_results.pop(key, None)

    def _motion_run_response_callback(self, msg: String) -> None:
        try:
            payload = json.loads(msg.data)
        except json.JSONDecodeError:
            self.get_logger().warn(f'Invalid {self.motion_run_response_topic} JSON received.')
            return
        if not isinstance(payload, dict):
            return

        request_id = str(payload.get('request_id') or '')
        if not request_id or not self._response_matches_current_generation(payload):
            return

        payload['_received_at'] = time.time()
        with self._motion_run_lock:
            self._motion_run_results[request_id] = payload
            status = payload.get('status')
            if isinstance(status, dict) and self._payload_matches_selected_project(status):
                self._motion_run_status = status
            cutoff = time.time() - 20.0
            stale_keys = [
                key for key, value in self._motion_run_results.items()
                if float(value.get('_received_at') or 0.0) < cutoff
            ]
            for key in stale_keys:
                self._motion_run_results.pop(key, None)
        if isinstance(status, dict) and self._payload_matches_selected_project(status):
            self._record_motion_run_transition(status)

    def _motion_run_status_callback(self, msg: String) -> None:
        try:
            payload = json.loads(msg.data)
        except json.JSONDecodeError:
            self.get_logger().warn(f'Invalid {self.motion_run_status_topic} JSON received.')
            return
        if not isinstance(payload, dict):
            return
        if not self._payload_matches_selected_project(payload):
            return
        with self._motion_run_lock:
            self._motion_run_status = payload
        self._record_motion_run_transition(payload)

    def _midi_monitor_state_callback(self, msg: String) -> None:
        try:
            payload = json.loads(msg.data)
        except json.JSONDecodeError:
            self.get_logger().warn(f'Invalid {self.midi_monitor_state_topic} JSON received.')
            return
        if not isinstance(payload, dict):
            return
        if not self._payload_matches_selected_project(payload):
            return
        payload['_bridge_received_at'] = time.time()
        with self._midi_monitor_lock:
            self._midi_monitor_status = payload

    def _midi_monitor_response_callback(self, msg: String) -> None:
        try:
            payload = json.loads(msg.data)
        except json.JSONDecodeError:
            self.get_logger().warn(f'Invalid {self.midi_monitor_response_topic} JSON received.')
            return
        if not isinstance(payload, dict):
            return
        request_id = str(payload.get('request_id') or '')
        if not request_id or not self._response_matches_current_generation(payload):
            return
        payload['_bridge_received_at'] = time.time()
        with self._midi_monitor_lock:
            self._midi_monitor_results[request_id] = payload
            if (
                payload.get('success')
                and isinstance(payload.get('channels'), list)
                and self._payload_matches_selected_project(payload)
            ):
                self._midi_monitor_status = dict(payload)
            cutoff = time.time() - 20.0
            stale_keys = [
                key for key, value in self._midi_monitor_results.items()
                if float(value.get('_bridge_received_at') or 0.0) < cutoff
            ]
            for key in stale_keys:
                self._midi_monitor_results.pop(key, None)

    def _motion_studio_status_callback(self, msg: String) -> None:
        self._motion_studio_transport().status_callback(msg)

    def _safety_status_callback(self, msg: String) -> None:
        try:
            payload = json.loads(msg.data)
        except json.JSONDecodeError:
            self.get_logger().warn(f'Invalid {self.safety_status_topic} JSON received.')
            return
        if isinstance(payload, dict):
            with self._safety_status_lock:
                self._safety_status = payload

    def _motion_studio_response_callback(self, msg: String) -> None:
        self._motion_studio_transport().response_callback(msg)

    def _motion_studio_editor_response_callback(self, msg: String) -> None:
        self._motion_studio_transport().editor_response_callback(msg)

    def _wait_for_jog_result(
        self,
        request_id: str,
        timeout_sec: float = 1.0,
    ) -> Optional[Dict[str, Any]]:
        deadline = time.time() + timeout_sec
        while time.time() < deadline:
            with self._jog_result_lock:
                result = self._jog_results.pop(request_id, None)
            if result is not None:
                return result
            time.sleep(0.01)

        with self._jog_result_lock:
            return self._jog_results.pop(request_id, None)

    def _wait_for_action_result(
        self,
        request_id: str,
        timeout_sec: float = 1.0,
    ) -> Optional[Dict[str, Any]]:
        deadline = time.time() + timeout_sec
        while time.time() < deadline:
            with self._action_result_lock:
                result = self._action_results.pop(request_id, None)
            if result is not None:
                return result
            time.sleep(0.01)

        with self._action_result_lock:
            return self._action_results.pop(request_id, None)

    def _wait_for_motion_mapping_result(
        self,
        request_id: str,
        timeout_sec: float = 2.0,
    ) -> Optional[Dict[str, Any]]:
        deadline = time.time() + timeout_sec
        while time.time() < deadline:
            with self._motion_mapping_lock:
                result = self._motion_mapping_results.pop(request_id, None)
            if result is not None:
                return result
            time.sleep(0.01)

        with self._motion_mapping_lock:
            return self._motion_mapping_results.pop(request_id, None)

    def _wait_for_motion_run_result(
        self,
        request_id: str,
        timeout_sec: float = 2.0,
    ) -> Optional[Dict[str, Any]]:
        deadline = time.time() + timeout_sec
        while time.time() < deadline:
            with self._motion_run_lock:
                result = self._motion_run_results.pop(request_id, None)
            if result is not None:
                return result
            time.sleep(0.01)

        with self._motion_run_lock:
            return self._motion_run_results.pop(request_id, None)

    def _wait_for_midi_monitor_result(
        self,
        request_id: str,
        timeout_sec: float = 2.0,
    ) -> Optional[Dict[str, Any]]:
        deadline = time.time() + timeout_sec
        while time.time() < deadline:
            with self._midi_monitor_lock:
                result = self._midi_monitor_results.pop(request_id, None)
            if result is not None:
                return result
            time.sleep(0.01)
        with self._midi_monitor_lock:
            return self._midi_monitor_results.pop(request_id, None)

    def _wait_for_motion_studio_result(
        self,
        request_id: str,
        timeout_sec: float = 3.0,
    ) -> Optional[Dict[str, Any]]:
        return self._motion_studio_transport().wait_for_result(
            request_id, timeout_sec
        )

    def _wait_for_motion_studio_editor_result(
        self, request_id: str, timeout_sec: float = 4.0
    ) -> Optional[Dict[str, Any]]:
        return self._motion_studio_transport().wait_for_editor_result(
            request_id, timeout_sec
        )

    def _motor_operation_reconcile_callback(self) -> None:
        lock = getattr(self, '_motor_operation_reconcile_lock', None)
        if lock is None:
            lock = threading.Lock()
            self._motor_operation_reconcile_lock = lock
        if not lock.acquire(blocking=False):
            return
        try:
            with self._lock:
                motion_state = copy.deepcopy(self._motion_state)
            runtime_status = self._runtime_service_status(motion_state)
            execution_context = self.execution_context_status(validate_files=False)
            self._reconcile_motor_operation_status(
                runtime_status,
                motion_state,
                execution_context,
            )
        except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
            self.get_logger().error(
                f'Motor operation reconcile failed: {exc}'
            )
        finally:
            lock.release()

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            motion_state = copy.deepcopy(self._motion_state)
            received_at = self._motion_state_received_at
        with self._motion_value_lock:
            motion_value_state = copy.deepcopy(self._motion_value_state)
        with self._motion_run_lock:
            motion_run_status = dict(self._motion_run_status) if self._motion_run_status else {}
        with self._midi_monitor_lock:
            midi_monitor = dict(self._midi_monitor_status) if self._midi_monitor_status else {}
        with self._motion_studio_lock:
            motion_studio = dict(self._motion_studio_status) if self._motion_studio_status else {}
        with self._safety_status_lock:
            safety_status = dict(self._safety_status) if self._safety_status else {}
        midi_received_at = midi_monitor.pop('_bridge_received_at', None)
        if midi_received_at is not None and time.time() - float(midi_received_at) > 1.0:
            midi_monitor['connected'] = False
            midi_monitor['message'] = 'MIDI 모니터 노드 상태 수신 중단'
        midi_monitor = self._safety_adjusted_midi_status(
            midi_monitor, safety_status=safety_status
        )

        runtime_status = self._runtime_service_status(motion_state)
        # Websocket status is published frequently. The stored execution
        # project service).  The stored execution context hashes every active
        # project file, so validating it for every websocket frame makes page
        # and API responses contend with continuous disk reads and hashing.
        # The coordinator and explicit context endpoints still perform the
        # full validation; a status frame only reports that validated result.
        execution_context = self.execution_context_status(validate_files=False)
        motor_operation = self.project_repository.motor_operation_status()
        selected_project_id = self.project_repository.selected_project_id()
        runtime_project_id = self._runtime_project_id_from_path(selected_project_id)
        stored_context = execution_context.get('context')
        motor_config_applied = bool(
            isinstance(stored_context, dict)
            and stored_context.get('project_id') == selected_project_id
            and stored_context.get('motor_applied')
        )
        project_scope = {
            'selected_project_id': selected_project_id,
            'runtime_project_id': runtime_project_id,
            'runtime_matches_selected': bool(
                selected_project_id
                and runtime_project_id
                and selected_project_id == runtime_project_id
            ),
            'motor_config_applied': motor_config_applied,
        }
        if isinstance(motion_state, dict):
            mapping_rows = self._monitoring_mapping_rows_for_context(
                execution_context,
                selected_project_id,
            )
            current_generation = self._current_project_generation()
            if (
                motion_value_state.get('project_id') != selected_project_id
                or motion_value_state.get('project_generation') != current_generation
            ):
                motion_value_state = {}
            add_monitoring_motion_values(
                motion_state,
                mapping_rows,
                motion_value_state,
            )
            motion_state['project_scope'] = project_scope
            motion_state['project_generation'] = current_generation

        return {
            'bridge_state': 'ok',
            'bridge_instance_id': str(getattr(self, '_bridge_instance_id', '')),
            'bridge_started_at': getattr(self, '_bridge_started_at', None),
            'project_generation': self._current_project_generation(),
            'system_info': {
                'hostname': socket.gethostname(),
                'workspace_root': str(Path(getattr(self, 'workspace_root', Path.cwd())).resolve()),
                'motion_projects_dir': str(Path(getattr(self, 'motion_projects_dir', Path.cwd())).resolve()),
            },
            'service_management': {
                'managed': bool(os.environ.get('MOTION_CONTROL_SERVICE_UNIT')),
                'mode': 'automatic' if os.environ.get('MOTION_CONTROL_SERVICE_UNIT') else 'manual',
                'unit': str(os.environ.get('MOTION_CONTROL_SERVICE_UNIT') or ''),
                'motor_managed': (
                    os.environ.get('MOTION_MOTOR_SERVICE_UNIT') == 'motion-motor.service'
                ),
                'motor_unit': str(os.environ.get('MOTION_MOTOR_SERVICE_UNIT') or ''),
                'runtime': runtime_status,
            },
            'motion_state_topic': self.motion_state_topic,
            'motion_state_received_at': received_at,
            'motion_state_age_sec': None if received_at is None else round(time.time() - received_at, 3),
            'motion_test_limits': {
                'max_jog_delta_deg': self.max_jog_delta_deg,
            },
            'web_access': self._web_access,
            'motion_run_status': motion_run_status,
            'motor_activity': motor_activity_snapshot(
                motion_run_status,
                motion_studio,
                safety_status,
            ),
            'midi_monitor': midi_monitor,
            'motion_studio': motion_studio,
            'safety_status': safety_status,
            'execution_context': execution_context,
            'motor_operation': motor_operation,
            'project_scope': project_scope,
            'coordination': (
                self._coordination_web_bridge.snapshot()
                if hasattr(self, '_coordination_web_bridge') else {}
            ),
            'motion_state': motion_state,
        }

    def coordination_status(self) -> Dict[str, Any]:
        """Return global PC coordination state without project data."""
        return self._coordination_web_bridge.snapshot()

    def update_coordination_settings(
        self, payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Update this PC's global DDS group settings."""
        return self._coordination_web_bridge.update_settings(payload)

    def coordination_local_readiness(self) -> Dict[str, Any]:
        """Check the currently active local execution files and safety state."""
        return local_motion_readiness(self)

    def coordination_local_status(self) -> Dict[str, Any]:
        """Return only the runtime fields needed by the loopback DDS adapter."""
        with self._coordination_poll_lock:
            self._coordination_poll_received_monotonic = time.monotonic()
        with self._motion_run_lock:
            motion_run_status = (
                dict(self._motion_run_status) if self._motion_run_status else {}
            )
        with self._safety_status_lock:
            safety_status = (
                dict(self._safety_status) if self._safety_status else {}
            )
        return {
            'bridge_state': 'ok',
            'sampled_monotonic': time.monotonic(),
            'motion_run_status': motion_run_status,
            'safety_status': safety_status,
        }

    def _coordination_watchdog_callback(self) -> None:
        """Stop a local group run if its coordination process disappears."""
        with self._motion_run_lock:
            status = dict(self._motion_run_status or {})
        execution_id = str(status.get('execution_id') or '')
        phase = str(status.get('phase') or '')
        active = bool(
            status.get('group_execution')
            and execution_id
            and phase not in {'stopped', 'group_motion_completed', 'error'}
        )
        if not active:
            self._coordination_watchdog_stop_execution_id = ''
            return
        with self._coordination_poll_lock:
            received = self._coordination_poll_received_monotonic
        if received and time.monotonic() - received <= 1.0:
            return
        if self._coordination_watchdog_stop_execution_id == execution_id:
            return
        self._coordination_watchdog_stop_execution_id = execution_id
        threading.Thread(
            target=self.coordination_stop_now,
            name='coordination-watchdog-stop',
            daemon=True,
        ).start()

    def coordination_control(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Send one manual group operation through the local ROS adapter."""
        return self._coordination_web_bridge.request_control(payload)

    def coordination_local_control(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Execute a validated loopback request through motion_run_manager."""
        return local_motion_control(self, payload)

    def coordination_stop_now(self) -> Dict[str, Any]:
        """Publish the final-output safety command before stopping motion run."""
        errors = []
        cancel_pending = getattr(self, 'cancel_pending_motion_studio_start', None)
        if callable(cancel_pending):
            try:
                cancel_pending()
            except Exception as exc:
                errors.append(f'시작 예약 취소 실패: {exc}')
        try:
            request_id = self.publish_safety_stop(False)
            safety_stop = {
                'success': True,
                'request_id': request_id,
                'acknowledgement_pending': True,
                'message': '최종 모터 출력 정지 명령 우선 전송 완료',
            }
        except Exception as exc:
            safety_stop = {
                'success': False,
                'request_id': '',
                'acknowledgement_pending': False,
                'message': f'최종 모터 출력 정지 명령 전송 실패: {exc}',
            }
            errors.append(str(safety_stop['message']))
        try:
            result = self.motion_run_stop()
        except Exception as exc:
            result = {
                'success': False,
                'message': f'motion_run_manager 정지 요청 실패: {exc}',
            }
        result = dict(result) if isinstance(result, dict) else {
            'success': False,
            'message': 'motion_run_manager 정지 응답 형식 오류',
        }
        result['safety_stop'] = safety_stop
        if errors:
            source_message = str(result.get('message') or '')
            result['success'] = False
            result['message'] = ' · '.join(filter(None, (
                *errors, source_message,
            )))
        return result

    def _reconcile_motor_operation_status(
        self,
        runtime_status: Dict[str, Any],
        motion_state: Any,
        execution_context: Dict[str, Any],
    ) -> Dict[str, Any]:
        repository = getattr(self, 'project_repository', None)
        if repository is None or not hasattr(repository, 'motor_operation_status'):
            return {}
        operation = repository.motor_operation_status()
        if not operation:
            return {}
        operation_id = str(operation.get('operation_id') or '')
        status = str(operation.get('status') or '')
        operation_type = str(operation.get('type') or '')
        started_at = float(operation.get('started_at') or 0.0)
        state_payload = motion_state if isinstance(motion_state, dict) else {}
        bridge_restarted = float(
            getattr(self, '_bridge_started_at', 0.0) or 0.0
        ) > started_at
        if (
            operation_type in {'ac_servo_scan', 'full_scan'}
            and status in {'running', 'timeout'}
            and bridge_restarted
        ):
            return self._schedule_interrupted_scan_recovery(operation)
        if status == 'timeout':
            # ``motor_operation_status`` synthesizes phase=timeout only while
            # the stored operation is still running.  A terminal timeout with
            # another phase has already been handled and must never schedule
            # rollback/restart again after the next snapshot or bridge start.
            if str(operation.get('phase') or '') != 'timeout':
                return operation
            if operation_type == 'motor_apply':
                return self._rollback_failed_motor_apply(
                    operation,
                    status='timeout',
                    error=str(
                        operation.get('error')
                        or '모터 설정 적용 제한시간을 초과했습니다'
                    ),
                )
            if operation_type == 'motor_restart':
                diagnosis = diagnose_motor_restart_failure(
                    operation,
                    state_payload,
                    runtime_status,
                )
                try:
                    return repository.finish_motor_operation(
                        operation_id,
                        'timeout',
                        phase='timed_out',
                        error=str(diagnosis['message']),
                        details={
                            'failure_code': diagnosis['failure_code'],
                            'pending_axes': diagnosis['pending_axes'],
                            'pending_connections': diagnosis['pending_connections'],
                        },
                    )
                except ValueError:
                    return operation
            try:
                return repository.finish_motor_operation(
                    operation_id,
                    'timeout',
                    phase='timed_out',
                    error=str(operation.get('error') or '모터 작업 제한시간을 초과했습니다'),
                )
            except ValueError:
                return operation
        if status != 'running':
            return operation
        # Scan operations own their preparation/scanning/restoring phases.
        # Applying restart/apply readiness rules here can terminate a scan
        # while it temporarily stops Motor Manager for EtherCAT ownership.
        if operation_type in {
            'ac_servo_scan',
            'dynamixel_scan',
            'full_scan',
            'motor_scan',
        }:
            return operation

        if operation_type == 'motor_restart':
            return self._motor_restart_lifecycle().reconcile(
                operation,
                runtime_status,
                state_payload,
            )
        last_motor_status_at = self._optional_float(
            state_payload.get('last_motor_status_at'), None
        )
        fresh_feedback = bool(
            last_motor_status_at is not None
            and last_motor_status_at > started_at
        )
        runtime_ready = runtime_status.get('phase') == 'ready'
        readiness = (
            self._motor_operation_runtime_readiness(
                operation,
                state_payload,
                runtime_status,
            )
            if runtime_ready and fresh_feedback
            else {'ready': False, 'failed': False, 'error': ''}
        )
        if readiness.get('failed') is True:
            error = str(readiness.get('error') or 'Motor Manager 실행 검증 실패')
            if operation_type == 'motor_apply':
                return self._rollback_failed_motor_apply(
                    operation,
                    status='failure',
                    error=error,
                )
            return repository.finish_motor_operation(
                operation_id,
                'failure',
                phase='failed',
                error=error,
            )
        if operation_type == 'motor_apply':
            if (
                bridge_restarted
                and runtime_ready
                and fresh_feedback
                and readiness.get('ready') is True
            ):
                return repository.finish_motor_operation(
                    operation_id,
                    'success',
                    phase='completed',
                    message='모터 설정 적용·재시작 완료',
                )
            if (
                bridge_restarted
                and runtime_status.get('phase') in {
                    'motor_manager_start_blocked',
                    'motor_manager_disabled',
                    'runtime_config_mismatch',
                }
            ):
                return self._rollback_failed_motor_apply(
                    operation,
                    status='failure',
                    error=str(
                        runtime_status.get('message')
                        or 'Motor Manager 시작 실패'
                    ),
                )
        return operation

    def _motor_restart_lifecycle(self) -> MotorRestartCoordinator:
        coordinator = getattr(self, 'motor_restart_coordinator', None)
        if coordinator is None:
            coordinator = MotorRestartCoordinator(
                self.project_repository,
                self._motor_operation_runtime_readiness,
            )
            self.motor_restart_coordinator = coordinator
        return coordinator

    def _motor_operation_runtime_readiness(
        self,
        operation: Dict[str, Any],
        motion_state: Dict[str, Any],
        runtime_status: Dict[str, Any],
    ) -> Dict[str, Any]:
        details = operation.get('details')
        details = dict(details) if isinstance(details, dict) else {}
        expected_file = str(details.get('runtime_file') or '').strip()
        if not expected_file:
            return {
                'ready': False,
                'failed': True,
                'error': '검증할 Motor Manager 실행 설정 경로가 없습니다',
            }
        verified_file = str(
            details.get('verified_motor_config_file') or ''
        ).strip()
        actual_file = verified_file
        if not actual_file:
            actual_file = str(
                runtime_status.get('runtime_config_file') or ''
            ).strip()
            if not actual_file:
                return {'ready': False, 'failed': False, 'error': ''}
            try:
                matches = (
                    runtime_status.get('runtime_target_matches_process') is True
                    and
                    Path(actual_file).expanduser().resolve()
                    == Path(expected_file).expanduser().resolve()
                )
            except (OSError, ValueError):
                matches = False
            if not matches:
                return {
                    'ready': False,
                    'failed': True,
                    'error': (
                        'Motor Manager 실행 설정 불일치 · '
                        f'기대 {expected_file} · 실제 {actual_file}'
                    ),
                }
            try:
                self.project_repository.update_motor_operation(
                    str(operation.get('operation_id') or ''),
                    str(operation.get('phase') or 'verifying'),
                    details={'verified_motor_config_file': actual_file},
                )
            except ValueError:
                pass

        expected_axes = details.get('expected_axes')
        if not isinstance(expected_axes, list):
            expected_axes = []
        try:
            expected = sorted(set(int(axis) for axis in expected_axes))
        except (TypeError, ValueError):
            expected = []
        if not expected:
            return {
                'ready': False,
                'failed': True,
                'error': '검증할 설정 대상 모터축이 없습니다',
            }
        motors = motion_state.get('motors')
        motors = motors if isinstance(motors, list) else []
        by_axis = {}
        for motor in motors:
            if not isinstance(motor, dict):
                continue
            try:
                by_axis[int(motor.get('controller_index'))] = motor
            except (TypeError, ValueError):
                continue
        pending = []
        for axis in expected:
            motor = by_axis.get(axis)
            if (
                motor is None
                or motor.get('connection_connected') is not True
                or str(motor.get('connection_state') or '') != 'online'
                or motor.get('fault') is True
            ):
                pending.append(axis)
        return {
            'ready': not pending,
            'failed': False,
            'error': '',
            'expected_axes': expected,
            'pending_axes': pending,
            'actual_config_file': actual_file,
        }

    def _rollback_failed_motor_apply(
        self,
        operation: Dict[str, Any],
        *,
        status: str,
        error: str,
    ) -> Dict[str, Any]:
        operation_id = str(operation.get('operation_id') or '')
        details = operation.get('details')
        details = dict(details) if isinstance(details, dict) else {}
        previous_runtime = details.get('previous_runtime')
        previous_runtime = (
            dict(previous_runtime)
            if isinstance(previous_runtime, dict) else {}
        )
        completed = self.project_repository.finish_motor_operation(
            operation_id,
            status,
            phase='rollback_requested',
            error=error,
        )
        self.project_repository.restore_motor_runtime_target(previous_runtime)
        if (
            os.environ.get('MOTION_CONTROL_SERVICE_UNIT')
            == 'motion-control.service'
            and os.environ.get('MOTION_MOTOR_SERVICE_UNIT')
            == 'motion-motor.service'
        ):
            try:
                self._schedule_managed_service_restart(
                    'motion-motor.service',
                    'motion-control.service',
                )
            except (OSError, ValueError) as exc:
                completed = self.project_repository.finish_motor_operation(
                    operation_id,
                    status,
                    phase='rollback_schedule_failed',
                    error=f'{error} · 이전 실행 설정 재시작 요청 실패: {exc}',
                )
        return completed

    def _schedule_interrupted_scan_recovery(
        self,
        operation: Dict[str, Any],
    ) -> Dict[str, Any]:
        lock = getattr(self, '_motor_operation_recovery_lock', None)
        if lock is None:
            lock = threading.Lock()
            self._motor_operation_recovery_lock = lock
        if not lock.acquire(blocking=False):
            return self.project_repository.motor_operation_status()
        operation_id = str(operation.get('operation_id') or '')
        try:
            updated = self.project_repository.update_motor_operation(
                operation_id,
                'restoring_after_bridge_restart',
                message='중단된 AC Servo 검색의 Motor Manager 복구 중',
                timeout_sec=20.0,
            )
        except ValueError:
            lock.release()
            return self.project_repository.motor_operation_status()
        threading.Thread(
            target=self._recover_interrupted_scan,
            args=(updated,),
            name='interrupted-ac-servo-scan-recovery',
            daemon=True,
        ).start()
        return updated

    def _recover_interrupted_scan(self, operation: Dict[str, Any]) -> None:
        lock = getattr(self, '_motor_operation_recovery_lock', None)
        operation_id = str(operation.get('operation_id') or '')
        details = operation.get('details')
        details = dict(details) if isinstance(details, dict) else {}
        was_active = details.get('motor_service_was_active') is True
        expected_axes = details.get('expected_axes')
        expected_axes = list(expected_axes) if isinstance(expected_axes, list) else []
        try:
            if not was_active:
                self.project_repository.finish_motor_operation(
                    operation_id,
                    'failure',
                    phase='interrupted',
                    error='브리지 종료로 AC Servo 검색 결과를 확인할 수 없습니다',
                )
                return
            self._run_managed_user_service('start', 'motion-motor.service')
            recovery = self._wait_for_motor_runtime_recovery(
                expected_axes,
                timeout_sec=12.0,
                motor_service='motion-motor.service',
            )
            if recovery.get('recovered') is True:
                self.project_repository.finish_motor_operation(
                    operation_id,
                    'failure',
                    phase='interrupted_recovered',
                    error=(
                        '브리지 종료로 AC Servo 검색 결과를 확인할 수 없습니다. '
                        'Motor Manager는 검색 전 실행 상태로 복구했습니다'
                    ),
                    details={'recovery': recovery},
                )
            else:
                self.project_repository.finish_motor_operation(
                    operation_id,
                    'failure',
                    phase='restore_failed',
                    error='중단된 AC Servo 검색 후 Motor Manager 복구에 실패했습니다',
                    details={'recovery': recovery},
                )
        except (OSError, RuntimeError, subprocess.TimeoutExpired, ValueError) as exc:
            try:
                self.project_repository.finish_motor_operation(
                    operation_id,
                    'failure',
                    phase='restore_failed',
                    error=f'중단된 AC Servo 검색 복구 실패: {exc}',
                )
            except ValueError:
                pass
        finally:
            if lock is not None and lock.locked():
                lock.release()

    def _monitoring_mapping_rows_for_context(
        self,
        execution_context: Dict[str, Any],
        project_id: str,
    ) -> List[Dict[str, Any]]:
        """Load the applied mapping once per immutable execution context."""
        context_id = str(execution_context.get('context_id') or '')
        context = execution_context.get('context')
        if (
            not execution_context.get('ready')
            or not context_id
            or not project_id
            or not isinstance(context, dict)
            or str(context.get('project_id') or '') != project_id
        ):
            return []
        files = context.get('files')
        mapping_info = (
            files.get('motion_axis_matching')
            if isinstance(files, dict) else None
        )
        if not isinstance(mapping_info, dict):
            return []
        mapping_name = str(mapping_info.get('name') or '').strip()
        expected_sha = str(mapping_info.get('sha256') or '').strip()
        if not mapping_name or not expected_sha:
            return []

        if not hasattr(self, '_monitoring_motion_mapping_lock'):
            self._monitoring_motion_mapping_lock = threading.Lock()
            self._monitoring_motion_mapping_context_id = ''
            self._monitoring_motion_mapping_rows = []
        with self._monitoring_motion_mapping_lock:
            if self._monitoring_motion_mapping_context_id == context_id:
                return copy.deepcopy(self._monitoring_motion_mapping_rows)
            rows: List[Dict[str, Any]] = []
            try:
                result = self.project_repository.read_file(
                    project_id,
                    'motion_axis_matching',
                    mapping_name,
                )
                if str(result.get('sha256') or '') == expected_sha:
                    payload = yaml.safe_load(str(result.get('content') or '')) or {}
                    raw_rows = payload.get('mappings') if isinstance(payload, dict) else None
                    if isinstance(raw_rows, list):
                        rows = [dict(row) for row in raw_rows if isinstance(row, dict)]
            except (AttributeError, OSError, ValueError, yaml.YAMLError):
                rows = []
            self._monitoring_motion_mapping_context_id = context_id
            self._monitoring_motion_mapping_rows = rows
            return copy.deepcopy(rows)

    def execution_context_status(self, *, validate_files: bool = True) -> Dict[str, Any]:
        with self._execution_context_lock:
            status = copy.deepcopy(self._execution_context_status)
        project_id = self.project_repository.selected_project_id()
        if validate_files and project_id and status.get('ready'):
            try:
                current = self.project_repository.execution_context(project_id)
            except (OSError, ValueError, json.JSONDecodeError):
                current = {}
            if current.get('context_id') != status.get('context_id'):
                status.update({
                    'state': 'stale',
                    'ready': False,
                    'message': '저장 설정이 변경되어 실행 컨텍스트 재적용 대기 중',
                    'stored_context_id': current.get('context_id', ''),
                })
        runtime_blocker = (
            self._motor_runtime_control_blocker()
            if status.get('ready')
            else ''
        )
        status['control_allowed'] = bool(status.get('ready') and not runtime_blocker)
        status['control_block_reason'] = runtime_blocker
        status['stored_equals_runtime'] = bool(status.get('ready'))
        return status

    def _motor_runtime_control_blocker(self) -> str:
        lock = getattr(self, '_lock', None)
        if lock is None:
            motion_state = copy.deepcopy(getattr(self, '_motion_state', None))
            received_at = getattr(self, '_motion_state_received_at', None)
        else:
            with lock:
                motion_state = copy.deepcopy(getattr(self, '_motion_state', None))
                received_at = getattr(self, '_motion_state_received_at', None)
        if not isinstance(motion_state, dict) or received_at is None:
            return '모터 상태를 아직 수신하지 못했습니다'
        if time.time() - float(received_at) > 1.0:
            return '모터 상태 수신이 중단되었습니다'

        motors = [
            motor for motor in motion_state.get('motors') or []
            if isinstance(motor, dict)
        ]
        if not motors:
            return '실행할 모터축이 없습니다'

        unavailable = []
        faulted = []
        for motor in motors:
            try:
                axis = int(motor.get('controller_index'))
            except (TypeError, ValueError):
                axis = '?'
            if motor.get('fault') is True:
                faulted.append(str(axis))
            if (
                motor.get('connection_connected') is not True
                or str(motor.get('connection_state') or '') != 'online'
            ):
                unavailable.append(str(axis))
        if unavailable:
            return f'온라인이 아닌 축이 있습니다: {", ".join(unavailable)}'
        if faulted:
            return f'오류 축이 있습니다: {", ".join(faulted)}'
        return ''

    def _set_execution_context_status(self, **values: Any) -> None:
        with self._execution_context_lock:
            self._execution_context_status.update(values)
            self._execution_context_status['updated_at'] = time.time()

    def _execution_context_id(self) -> str:
        status = self.execution_context_status()
        return str(status.get('context_id') or '') if status.get('ready') else ''

    def _establish_project_generation_boundary(self, *, force: bool = False) -> None:
        """Synchronize the persistent project generation with the command owner.

        The supervisor is recreated by a full program restart and therefore
        starts at generation zero, while the bridge restores the persisted
        generation.  Establish the boundary before any project consumer can
        become ready so valid MIDI commands are not rejected after restart.
        """
        generation = self._current_project_generation()
        if (
            not force
            and int(getattr(self, '_supervisor_project_generation', 0) or 0)
            == generation
        ):
            return
        boundary_id = self._new_project_request_id('project-boundary')
        boundary = String()
        boundary.data = json.dumps({
            'request_id': boundary_id,
            'project_generation': generation,
            'command': 'project_generation_boundary',
        }, ensure_ascii=False)
        publisher = getattr(self, '_action_request_publisher', None)
        if publisher is not None:
            publisher.publish(boundary)
            acknowledged = self._wait_for_action_result(boundary_id, timeout_sec=1.0)
            if not isinstance(acknowledged, dict) or acknowledged.get('success') is not True:
                raise ValueError(
                    '최종 모터 명령 노드가 프로젝트 세대 전환을 확인하지 않았습니다'
                )
        policy_result = self.publish_servo_alarm_policy()
        if policy_result.get('success') is not True:
            self._set_execution_context_status(
                state='waiting_motor_runtime',
                ready=False,
                project_id=str(project_id),
                context_id='',
                message='선택 프로젝트의 서보 에러 정책 적용 대기',
                nodes={},
                failures={
                    'servo_alarm_policy': str(
                        policy_result.get('message') or '응답 없음'
                    ),
                },
            )
            raise ValueError(
                '최종 모터 명령 노드가 서보 에러 정책을 확인하지 않았습니다: '
                f'{policy_result.get("message") or "응답 없음"}'
            )
        self._supervisor_project_generation = generation

    def _invalidate_execution_nodes(self, context_id: str = '') -> None:
        payload = {'context_id': context_id}
        # A forced boundary also stops any command that belonged to the
        # invalidated context, even when the numeric generation is unchanged.
        self._establish_project_generation_boundary(force=True)
        self._request_motion_mapping('invalidate_context', payload, timeout_sec=0.5)
        self._request_midi_monitor('invalidate_context', payload, timeout_sec=0.5)
        self._request_motion_run('invalidate_context', payload, timeout_sec=0.5)
        self.request_motion_studio('invalidate_context', payload, timeout_sec=0.5)
        self._clear_project_scoped_memory()

    def _execution_context_ack_matches(
        self, result: Dict[str, Any], context_id: str, project_id: str
    ) -> bool:
        """Accept the common acknowledgement fields, including UI snapshots.

        MIDI status snapshots historically expose the context as a nested
        object, while the other managed nodes return it at the top level.
        The coordinator must validate the values, not mistake that harmless
        response-shape difference for a failed project application.
        """
        nested = result.get('execution_context')
        if not isinstance(nested, dict):
            nested = {}
        status = result.get('status')
        status_context = (
            status.get('execution_context')
            if isinstance(status, dict) else {}
        )
        if not isinstance(status_context, dict):
            status_context = {}
        acknowledged_context = str(
            result.get('context_id')
            or nested.get('context_id')
            or status_context.get('context_id')
            or ''
        )
        acknowledged_project = str(
            result.get('project_id')
            or nested.get('project_id')
            or status_context.get('project_id')
            or ''
        )
        acknowledged_generation = result.get('project_generation')
        if acknowledged_generation is None:
            acknowledged_generation = nested.get('project_generation')
        if acknowledged_generation is None:
            acknowledged_generation = status_context.get('project_generation')
        try:
            generation_matches = (
                int(acknowledged_generation) == self._current_project_generation()
            )
        except (TypeError, ValueError):
            generation_matches = False
        return (
            result.get('success') is True
            and acknowledged_context == context_id
            and acknowledged_project == project_id
            and generation_matches
        )

    def _schedule_execution_context_reconcile(self) -> None:
        """Run orchestration outside the single ROS callback thread.

        Response subscriptions must remain free while the coordinator waits
        for acknowledgements from the managed nodes.
        """
        if self._execution_context_apply_lock.locked():
            return
        threading.Thread(
            target=self._reconcile_execution_context,
            name='project-context-coordinator',
            daemon=True,
        ).start()

    def _reconcile_execution_context(self) -> Dict[str, Any]:
        if not self._execution_context_apply_lock.acquire(blocking=False):
            return self.execution_context_status()
        try:
            project_id = self.project_repository.selected_project_id()
            if not project_id:
                self._set_execution_context_status(
                    state='no_project', ready=False, project_id='', context_id='',
                    message='현재 프로젝트를 선택하세요', nodes={},
                )
                return self.execution_context_status()
            try:
                context = self.project_repository.execution_context(project_id)
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                self._set_execution_context_status(
                    state='error', ready=False, project_id=project_id, context_id='',
                    message=f'프로젝트 실행 컨텍스트 생성 실패: {exc}', nodes={},
                )
                return self.execution_context_status()

            context_id = str(context.get('context_id') or '')
            with self._execution_context_lock:
                previous = dict(self._execution_context_status)
            try:
                self._establish_project_generation_boundary()
            except ValueError as exc:
                self._set_execution_context_status(
                    state='waiting_motor_runtime', ready=False,
                    project_id=project_id, context_id=context_id,
                    message=str(exc), nodes={},
                    failures={'motor_runtime': str(exc)}, context=context,
                )
                return self.execution_context_status()
            if context.get('missing'):
                if previous.get('state') != 'configuration_required' or previous.get('context_id') != context_id:
                    self._invalidate_execution_nodes(context_id)
                self._set_execution_context_status(
                    state='configuration_required', ready=False,
                    project_id=project_id, context_id=context_id,
                    message='모터축 설정과 모션축 설정 파일을 확정하세요',
                    missing=list(context.get('missing') or []), nodes={}, context=context,
                )
                return self.execution_context_status()
            mapping = context['files']['motion_axis_matching']
            if (
                previous.get('state') == 'motor_apply_required'
                and previous.get('context_id') == context_id
                and time.time() - float(previous.get('updated_at') or 0.0) < 5.0
            ):
                return self.execution_context_status()
            payload = {
                'context_id': context_id,
                'project_generation': self._current_project_generation(),
                'mapping_file_id': mapping['name'],
                'mapping_sha256': mapping['sha256'],
            }
            if previous.get('ready') and previous.get('context_id') == context_id:
                # A ready context is immutable: its id already includes the
                # selected project's configuration file hashes. Re-sending
                # apply_context as a periodic health check is unsafe because
                # motion_run intentionally rejects configuration changes while
                # initialization/playback is active. Treating that rejection as
                # a node failure used to invalidate MIDI, motion_run and studio
                # in the middle of recording. A changed file produces a new
                # context_id and naturally takes the normal apply path below.
                return self.execution_context_status()
            if not previous.get('ready') or previous.get('context_id') != context_id:
                self._set_execution_context_status(
                    state='applying', ready=False, project_id=project_id,
                    context_id=context_id, message='프로젝트 설정을 각 노드에 적용 중',
                    context=context,
                )
            nodes = {
                'motion_mapping': self._request_motion_mapping(
                    'apply_context', payload, timeout_sec=2.0
                ),
                'midi_control': self._request_midi_monitor(
                    'select_project', payload, timeout_sec=2.0
                ),
                'motion_run': self._request_motion_run(
                    'apply_context', payload, timeout_sec=2.0
                ),
                'motion_studio': self.request_motion_studio(
                    'apply_context', payload, timeout_sec=2.0
                ),
            }
            failed = {
                name: str(result.get('message') or '응답 없음')
                for name, result in nodes.items()
                if not self._execution_context_ack_matches(
                    result, context_id, project_id
                )
            }
            if failed:
                self._invalidate_execution_nodes(context_id)
                self._set_execution_context_status(
                    state='waiting_nodes', ready=False, nodes=nodes,
                    message='필수 노드의 프로젝트 설정 적용 응답 대기 중',
                    failures=failed,
                )
                return self.execution_context_status()

            if not context.get('motor_applied'):
                # The project files were accepted by every consumer above.
                # Keep that project-scoped mapping and MIDI bank loaded while
                # motor control remains blocked.  Invalidating here used to
                # erase the MIDI node's project_id and restore its default
                # Bank 1 once per reconciliation cycle, even though the saved
                # project data itself was valid.
                self._set_execution_context_status(
                    state='motor_apply_required', ready=False,
                    project_id=project_id, context_id=context_id,
                    message='프로젝트 파일은 각 노드에 전달됐지만 모터축 설정 적용 및 재시작이 필요합니다',
                    nodes=nodes, failures={}, context=context,
                )
                return self.execution_context_status()

            with self._lock:
                motion_state = copy.deepcopy(self._motion_state)
            motor_runtime = self._runtime_service_status(motion_state)
            motor_runtime.update({
                'success': (
                    self._runtime_project_id() == project_id
                    and motor_runtime.get('phase') == 'ready'
                ),
                'project_id': self._runtime_project_id(),
                'context_id': context_id,
            })
            nodes['motor_runtime'] = motor_runtime
            if not motor_runtime['success']:
                self._invalidate_execution_nodes(context_id)
                self._set_execution_context_status(
                    state='waiting_motor_runtime', ready=False, nodes=nodes,
                    message='현재 프로젝트의 모터 관리 노드 상태 확인 대기 중',
                    failures={'motor_runtime': str(motor_runtime.get('message') or '')},
                )
                return self.execution_context_status()

            confirmations = {
                'midi_control': self._request_midi_monitor(
                    'confirm_context', payload, timeout_sec=2.0
                ),
                'motion_run': self._request_motion_run(
                    'confirm_context', payload, timeout_sec=2.0
                ),
                'motion_studio': self.request_motion_studio(
                    'confirm_context', payload, timeout_sec=2.0
                ),
            }
            confirm_failed = {
                name: str(result.get('message') or '응답 없음')
                for name, result in confirmations.items()
                if not self._execution_context_ack_matches(
                    result, context_id, project_id
                )
            }
            nodes.update({f'{name}_confirm': value for name, value in confirmations.items()})
            if confirm_failed:
                self._invalidate_execution_nodes(context_id)
                self._set_execution_context_status(
                    state='waiting_nodes', ready=False, nodes=nodes,
                    message='필수 노드의 제어 허용 확인 대기 중',
                    failures=confirm_failed,
                )
                return self.execution_context_status()
            self._set_execution_context_status(
                state='ready', ready=True, nodes=nodes, failures={},
                message='저장 설정과 실행 설정이 일치합니다 · 사용자 제어 가능',
                verified_at=time.time(),
            )
            return self.execution_context_status()
        finally:
            self._execution_context_apply_lock.release()

    def _reconcile_execution_context_blocking(
        self, *, timeout_sec: float = 10.0, poll_interval: float = 0.1,
    ) -> Dict[str, Any]:
        """Wait until project execution context is ready or a terminal state is reached."""
        terminal_states = {
            'ready', 'error', 'configuration_required', 'no_project',
        }
        deadline = time.monotonic() + max(float(timeout_sec), 0.0)
        last_status = self.execution_context_status()
        while time.monotonic() < deadline:
            if self._execution_context_apply_lock.locked():
                time.sleep(min(poll_interval, 0.05))
                continue
            last_status = self._reconcile_execution_context()
            state = str(last_status.get('state') or '')
            if state in terminal_states:
                return last_status
            time.sleep(poll_interval)
        return last_status

    def _runtime_service_status(self, motion_state: Any) -> Dict[str, Any]:
        runtime_path = Path(getattr(self, 'applied_motor_config_file', Path()))
        runtime_config = str(runtime_path) if runtime_path.is_file() else ''
        repository = getattr(self, 'project_repository', None)
        runtime_target = (
            repository.motor_runtime_state()
            if repository is not None and hasattr(repository, 'motor_runtime_state')
            else {}
        )
        target_config = str(runtime_target.get('config_file') or '')
        runtime_target_matches_process = bool(
            runtime_target.get('valid') is True
            and runtime_config
            and Path(target_config).resolve() == runtime_path.resolve()
        )
        start_block_reason = str(
            os.environ.get('MOTOR_START_BLOCK_REASON') or ''
        ).strip()
        motor_manager_expected = (
            bool(runtime_config)
            and runtime_target_matches_process
            and not start_block_reason
        )
        runtime_config_path = runtime_config or str(
            self.workspace_root / 'config' / 'bootstrap_motor_config.yaml'
        )
        state_payload = motion_state if isinstance(motion_state, dict) else {}
        generated_at = self._optional_float(state_payload.get('generated_at'), None)
        last_motor_status_at = self._optional_float(
            state_payload.get('last_motor_status_at'), None
        )
        motor_feedback_age_sec = None
        if generated_at is not None and last_motor_status_at is not None:
            motor_feedback_age_sec = max(generated_at - last_motor_status_at, 0.0)
        motors = state_payload.get('motors')
        motor_count = len(motors) if isinstance(motors, list) else 0
        if start_block_reason:
            runtime_phase = 'motor_manager_start_blocked'
            runtime_message = start_block_reason
        elif runtime_target.get('valid') is True and not runtime_target_matches_process:
            runtime_phase = 'runtime_config_mismatch'
            runtime_message = 'Motor Manager 실행 설정과 적용 대상 설정이 다릅니다'
        elif not motor_manager_expected:
            runtime_phase = 'motor_manager_disabled'
            runtime_message = '모터 실행 설정이 없어 motor_manager_node를 시작하지 않았습니다'
        elif last_motor_status_at is None:
            runtime_phase = 'waiting_motor_feedback'
            runtime_message = 'motor_manager_node 시작 후 첫 모터 상태를 기다리는 중입니다'
        elif motor_feedback_age_sec is not None and motor_feedback_age_sec > 1.5:
            runtime_phase = 'motor_feedback_stale'
            runtime_message = 'motor_manager_node의 모터 상태 갱신이 중단되었습니다'
        else:
            runtime_phase = 'ready'
            runtime_message = f'모터 상태 {motor_count}축 수신 중'

        return {
            'phase': runtime_phase,
            'message': runtime_message,
            'motor_manager_expected': motor_manager_expected,
            'motor_manager_start_block_reason': start_block_reason,
            'ros_localhost_only': str(
                os.environ.get('ROS_LOCALHOST_ONLY') or ''
            ) == '1',
            'runtime_config_file': runtime_config_path,
            'runtime_target_file': target_config,
            'runtime_target_matches_process': runtime_target_matches_process,
            'motor_count': motor_count,
            'last_motor_status_at': last_motor_status_at,
            'motor_feedback_age_sec': (
                None if motor_feedback_age_sec is None
                else round(motor_feedback_age_sec, 3)
            ),
        }

    def _record_motor_error_transitions(self, payload: Dict[str, Any]) -> None:
        motors = payload.get('motors')
        if not isinstance(motors, list):
            return

        current_errors: Dict[str, str] = {}
        new_events: List[Dict[str, Any]] = []
        with self._event_log_lock:
            previous_errors = dict(self._active_motor_errors)
            for motor in motors:
                if not isinstance(motor, dict):
                    continue
                axis = self._optional_int(motor.get('controller_index'), None)
                if axis is None:
                    continue
                errorcode = self._optional_int(motor.get('errorcode'), 0) or 0
                statusword = self._optional_int(motor.get('statusword'), 0) or 0
                fault = bool(motor.get('fault')) or errorcode != 0 or bool(statusword & 0x0008)
                if not fault:
                    continue

                error_hex = str(motor.get('errorcode_hex') or f'0x{errorcode & 0xFFFF:04X}')
                error_text = str(
                    motor.get('error_text')
                    or motor.get('status_text')
                    or '모터 오류 상태'
                )
                signature = f'{error_hex}|{statusword & 0x0008}|{error_text}'
                axis_key = str(axis)
                current_errors[axis_key] = signature
                if previous_errors.get(axis_key) == signature:
                    continue

                name = str(motor.get('display_name') or f'Axis {axis}')
                new_events.append({
                    'category': 'error',
                    'event_type': 'motor_error',
                    'target': f'Axis {axis} · {name}',
                    'content': f'{error_hex} {error_text}',
                    'details': {
                        'axis': axis,
                        'name': name,
                        'motor_type': str(motor.get('motor_type_label') or motor.get('motor_type') or ''),
                        'errorcode': errorcode,
                        'errorcode_hex': error_hex,
                        'error_text': error_text,
                        'statusword': statusword,
                    },
                })
            self._active_motor_errors = current_errors

        for event in new_events:
            self._append_motor_event(**event)

    def _record_motion_run_transition(self, status: Dict[str, Any]) -> None:
        state = str(status.get('state') or 'idle')
        with self._event_log_lock:
            previous_state = self._last_motion_run_state
            self._last_motion_run_state = state
        if previous_state is None or previous_state == state:
            return

        motion_file = str(status.get('motion_file_id') or '-')
        mapping_file = str(status.get('mapping_file_id') or '-')
        axes = status.get('axes') if isinstance(status.get('axes'), list) else []
        target = f'{motion_file} · {len(axes)}축'
        details = {
            'previous_state': previous_state,
            'state': state,
            'motion_file_id': motion_file,
            'mapping_file_id': mapping_file,
            'axis_count': len(axes),
            'run_mode': str(status.get('run_mode') or 'once'),
        }

        if state == 'initializing' and previous_state != 'initializing':
            self._append_motor_event(
                category='initial_position',
                event_type='initial_position_started',
                target=target,
                content=f'초기 위치 이동 시작 · 매핑 {mapping_file}',
                details=details,
            )

        if previous_state == 'initializing' and state != 'initializing':
            completed = state in {'initialized', 'ready', 'running'}
            self._append_motor_event(
                category='initial_position',
                event_type='initial_position_completed' if completed else 'initial_position_stopped',
                target=target,
                content='초기 위치 이동 완료' if completed else f'초기 위치 이동 종료 · 상태 {state}',
                details=details,
            )

        if state == 'running' and previous_state != 'running':
            continuous = details['run_mode'] == 'continuous'
            motion_label = '연속 모션' if continuous else '1회 모션'
            self._append_motor_event(
                category='motion',
                event_type='continuous_motion_started' if continuous else 'single_motion_started',
                target=target,
                content=f'{motion_label} 시작 · 매핑 {mapping_file}',
                details=details,
            )

    def _append_motor_event(
        self,
        category: str,
        event_type: str,
        target: str,
        content: str,
        details: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        now = datetime.now().astimezone()
        record = {
            'id': f'{time.time_ns()}',
            'timestamp': now.timestamp(),
            'timestamp_text': now.isoformat(timespec='milliseconds'),
            'category': str(category),
            'event_type': str(event_type),
            'target': str(target),
            'content': str(content),
            'details': details if isinstance(details, dict) else {},
        }
        log_dir, project_id, project_name = self._motor_event_log_context(for_write=True)
        if project_id:
            record['project_id'] = project_id
            record['project_name'] = project_name
        path = log_dir / f'{now:%Y-%m-%d}.jsonl'
        try:
            with self._event_log_lock:
                log_dir.mkdir(parents=True, exist_ok=True)
                with path.open('a', encoding='utf-8') as stream:
                    stream.write(json.dumps(record, ensure_ascii=False, separators=(',', ':')))
                    stream.write('\n')
                self._prune_motor_event_logs(log_dir)
        except OSError as error:
            self.get_logger().error(f'Failed to write motor event log {path}: {error}')
        return record

    def _motor_event_log_context(self, for_write: bool = False) -> tuple[Path, str, str]:
        configured_fallback = getattr(self, 'event_log_dir', None)
        workspace_root = Path(getattr(self, 'workspace_root', Path.cwd()))
        fallback = Path(configured_fallback or workspace_root / 'log' / 'motor_events')
        repository = getattr(self, 'project_repository', None)
        if repository is None:
            return fallback, '', ''

        project_id = ''
        if for_write:
            try:
                project_id = str(self._runtime_project_id() or '')
            except (AttributeError, OSError, ValueError):
                project_id = ''
        if not project_id:
            try:
                project_id = str(repository.selected_project_id() or '')
            except (AttributeError, OSError, ValueError):
                project_id = ''
        if not project_id:
            return fallback, '', ''
        try:
            project = repository.get_project(project_id).get('project') or {}
            return repository.project_logs_dir(project_id), project_id, str(project.get('name') or project_id)
        except (AttributeError, OSError, ValueError):
            return fallback, '', ''

    @staticmethod
    def _event_log_paths(log_dir: Path) -> List[Path]:
        return sorted(
            path for path in log_dir.glob('*.jsonl')
            if path.is_file() and not path.is_symlink()
        )

    @staticmethod
    def _event_log_lines(path: Path) -> List[str]:
        try:
            return [line for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]
        except OSError:
            return []

    def _prune_motor_event_logs(self, log_dir: Optional[Path] = None) -> None:
        target_dir = Path(log_dir or self.event_log_dir)
        cutoff = datetime.now().astimezone().date() - timedelta(
            days=self.event_log_retention_days - 1
        )
        with self._event_log_lock:
            paths = self._event_log_paths(target_dir)
            for path in list(paths):
                try:
                    file_date = datetime.strptime(path.stem, '%Y-%m-%d').date()
                except ValueError:
                    continue
                if file_date < cutoff:
                    try:
                        path.unlink()
                    except OSError:
                        continue
                    paths.remove(path)

            while len(paths) > self.event_log_max_files:
                oldest = paths.pop(0)
                try:
                    oldest.unlink()
                except OSError:
                    pass

            sizes: Dict[Path, int] = {}
            for path in paths:
                try:
                    sizes[path] = path.stat().st_size
                except OSError:
                    sizes[path] = 0
            total_bytes = sum(sizes.values())
            while total_bytes > self.event_log_max_bytes and len(paths) > 1:
                oldest = paths.pop(0)
                try:
                    oldest.unlink()
                except OSError:
                    continue
                total_bytes -= sizes.get(oldest, 0)

            line_counts = {path: len(self._event_log_lines(path)) for path in paths}
            total_records = sum(line_counts.values())
            while total_records > self.event_log_max_records and len(paths) > 1:
                oldest = paths.pop(0)
                try:
                    oldest.unlink()
                except OSError:
                    continue
                total_records -= line_counts.get(oldest, 0)

            if paths:
                newest = paths[-1]
                lines = self._event_log_lines(newest)
                if len(lines) > self.event_log_max_records:
                    lines = lines[-self.event_log_max_records:]
                encoded_lines = [(line + '\n').encode('utf-8') for line in lines]
                encoded_size = sum(len(line) for line in encoded_lines)
                while encoded_lines and encoded_size > self.event_log_max_bytes:
                    encoded_size -= len(encoded_lines.pop(0))
                try:
                    newest.write_bytes(b''.join(encoded_lines))
                except OSError:
                    pass

    def clear_motor_events(self) -> Dict[str, Any]:
        log_dir, project_id, project_name = self._motor_event_log_context()
        deleted_files = 0
        deleted_bytes = 0
        with self._event_log_lock:
            for path in self._event_log_paths(log_dir):
                try:
                    deleted_bytes += path.stat().st_size
                    path.unlink()
                    deleted_files += 1
                except OSError:
                    continue
        return {
            'success': True,
            'message': '현재 프로젝트의 모터 동작 로그를 삭제했습니다.',
            'deleted_files': deleted_files,
            'deleted_bytes': deleted_bytes,
            'project_id': project_id,
            'project_name': project_name,
        }

    def delete_motor_event_file(self, file_name: Any) -> Dict[str, Any]:
        name = str(file_name or '').strip()
        if name != Path(name).name or not re.fullmatch(r'\d{4}-\d{2}-\d{2}\.jsonl', name):
            raise ValueError('올바르지 않은 로그 파일명입니다')
        log_dir, project_id, project_name = self._motor_event_log_context()
        path = log_dir / name
        if not path.is_file() or path.is_symlink():
            raise ValueError(f'로그 파일을 찾을 수 없습니다: {name}')
        with self._event_log_lock:
            size = path.stat().st_size
            path.unlink()
        return {
            'success': True,
            'message': f'{name} 로그 파일을 삭제했습니다.',
            'deleted_file': name,
            'deleted_bytes': size,
            'project_id': project_id,
            'project_name': project_name,
        }

    def motor_events(
        self, limit: int = 200, category: str = 'all', file_name: str = 'all'
    ) -> Dict[str, Any]:
        safe_limit = max(1, min(int(limit), 1000))
        category_filter = str(category or 'all')
        file_filter = str(file_name or 'all')
        log_dir, project_id, project_name = self._motor_event_log_context()
        events: List[Dict[str, Any]] = []
        file_rows: List[Dict[str, Any]] = []
        with self._event_log_lock:
            paths = list(reversed(self._event_log_paths(log_dir)))
            for path in paths:
                lines = self._event_log_lines(path)
                try:
                    size = path.stat().st_size
                except OSError:
                    size = 0
                file_rows.append({
                    'name': path.name,
                    'size': size,
                    'record_count': len(lines),
                })
            if file_filter != 'all':
                paths = [path for path in paths if path.name == file_filter]
            for path in paths:
                try:
                    lines = path.read_text(encoding='utf-8').splitlines()
                except OSError:
                    continue
                for line in reversed(lines):
                    try:
                        event = json.loads(line)
                    except (json.JSONDecodeError, TypeError):
                        continue
                    if not isinstance(event, dict):
                        continue
                    if category_filter != 'all' and event.get('category') != category_filter:
                        continue
                    events.append(event)
                    if len(events) >= safe_limit:
                        break
                if len(events) >= safe_limit:
                    break
        return {
            'success': True,
            'category': category_filter,
            'file_name': file_filter,
            'count': len(events),
            'events': events,
            'files': file_rows,
            'retention_days': self.event_log_retention_days,
            'max_bytes': self.event_log_max_bytes,
            'max_records': self.event_log_max_records,
            'max_files': self.event_log_max_files,
            'project_id': project_id,
            'project_name': project_name,
        }

    def _build_web_access_info(self) -> Dict[str, Any]:
        lan_ip = self.access_host or self._detect_lan_ip()
        display_host = lan_ip or self.host
        if display_host in ('', '0.0.0.0', '::'):
            display_host = '<this-pc-ip>'
        return {
            'bind_host': self.host,
            'port': self.port,
            'lan_ip': lan_ip,
            'url': f'http://{display_host}:{self.port}/',
        }

    @staticmethod
    def _detect_lan_ip() -> str:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.connect(('8.8.8.8', 80))
                ip = sock.getsockname()[0]
                if ip and not ip.startswith('127.'):
                    return ip
        except OSError:
            pass

        try:
            for ip in socket.gethostbyname_ex(socket.gethostname())[2]:
                if ip and not ip.startswith('127.'):
                    return ip
        except OSError:
            pass

        return ''

    def set_monitoring(self, enabled: bool, timeout_sec: float = 2.0) -> Dict[str, Any]:
        if not self._monitoring_client.wait_for_service(timeout_sec=0.2):
            return {
                'success': False,
                'message': f'monitoring service unavailable: {self.monitoring_service}',
                **self.snapshot(),
            }

        request = SetBool.Request()
        request.data = enabled
        future = self._monitoring_client.call_async(request)
        deadline = time.time() + timeout_sec
        while not future.done() and time.time() < deadline:
            time.sleep(0.02)

        if not future.done():
            return {
                'success': False,
                'message': 'monitoring service timeout',
                **self.snapshot(),
            }

        response = future.result()
        return {
            'success': bool(response.success),
            'message': response.message,
            **self.snapshot(),
        }

    def scan_motors(self, timeout_sec: float = 20.0) -> Dict[str, Any]:
        return self._call_scan_service(
            self._scan_client,
            self.scan_service,
            timeout_sec,
            release_ethercat=True,
            operation_type='full_scan',
        )

    def scan_ac_servo_motors(self, timeout_sec: float = 10.0) -> Dict[str, Any]:
        return self._call_scan_service(
            self._scan_ac_servo_client,
            self.scan_ac_servo_service,
            timeout_sec,
            release_ethercat=True,
            operation_type='ac_servo_scan',
        )

    def scan_dynamixel_motors(self, timeout_sec: float = 20.0) -> Dict[str, Any]:
        return self._call_scan_service(
            self._scan_dynamixel_client,
            self.scan_dynamixel_service,
            timeout_sec,
            operation_type='dynamixel_scan',
        )

    def read_ethercat_aliases(self) -> Dict[str, Any]:
        try:
            slaves = self.ethercat_alias_manager.read_slaves()
        except EthercatAliasError as exc:
            return {'success': False, 'message': str(exc), 'slaves': []}
        return {
            'success': True,
            'message': f'EtherCAT EEPROM Alias {len(slaves)}축 읽기 완료',
            'slaves': slaves,
        }

    def write_ethercat_alias(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        if payload.get('confirmed') is not True:
            return {
                'success': False,
                'message': '사용자 확인값이 없어 EEPROM Alias 쓰기를 중단했습니다.',
            }
        try:
            master_index = int(payload.get('master_index', 0))
            slave_position = int(payload.get('slave_position'))
            new_alias = int(payload.get('new_alias'))
        except (TypeError, ValueError):
            return {
                'success': False,
                'message': (
                    'EtherCAT Master 번호, Slave Position과 EEPROM Alias는 '
                    '정수여야 합니다.'
                ),
            }
        if master_index < 0:
            return {
                'success': False,
                'message': 'EtherCAT Master 번호는 0 이상의 정수여야 합니다.',
            }
        expected = payload.get('expected')
        if not isinstance(expected, dict):
            return {'success': False, 'message': '선택 장비 확인값이 없습니다.'}
        try:
            result = self.ethercat_alias_manager.write_alias(
                slave_position,
                new_alias,
                expected,
                master_index=master_index,
            )
        except EthercatAliasError as exc:
            return {'success': False, 'message': str(exc)}
        self._append_motor_event(
            category='system',
            event_type='ethercat_alias_written',
            target=(
                f'Master {result["master_index"]} · '
                f'Slave {result["slave_position"]}'
            ),
            content=(
                f'EEPROM Alias {result["previous_alias"]} → {result["new_alias"]}'
            ),
            details=result,
        )
        return {'success': True, **result}

    def _call_scan_service(
        self,
        client,
        service_name: str,
        timeout_sec: float,
        *,
        release_ethercat: bool = False,
        operation_type: str = 'motor_scan',
    ) -> Dict[str, Any]:
        lifecycle_lock = getattr(self, '_motor_lifecycle_lock', None)
        if lifecycle_lock is None:
            lifecycle_lock = threading.Lock()
            self._motor_lifecycle_lock = lifecycle_lock
        if not lifecycle_lock.acquire(blocking=False):
            return {
                'success': False,
                'message': '다른 모터 설정·검색·재시작 작업이 진행 중입니다',
                'scan': None,
                'project_id': self.project_repository.selected_project_id(),
                'project_generation': self._current_project_generation(),
                **self.snapshot(),
            }
        scan_lock = getattr(self, '_motor_scan_request_lock', None)
        if scan_lock is None:
            scan_lock = threading.Lock()
            self._motor_scan_request_lock = scan_lock
        if not scan_lock.acquire(blocking=False):
            lifecycle_lock.release()
            return {
                'success': False,
                'message': '다른 모터 검색이 진행 중입니다. 완료 후 다시 시도하세요',
                'scan': None,
                'project_id': self.project_repository.selected_project_id(),
                'project_generation': self._current_project_generation(),
                **self.snapshot(),
            }
        operation: Dict[str, Any] = {}
        result: Dict[str, Any]
        try:
            operation = self.project_repository.begin_motor_operation(
                operation_type,
                'preparing',
                timeout_sec=timeout_sec + (20.0 if release_ethercat else 5.0),
                details={
                    'service_name': service_name,
                    'project_id': self.project_repository.selected_project_id(),
                },
            )
            operation_id = str(operation.get('operation_id') or '')
            if release_ethercat:
                result = self._call_ethercat_scan_service_locked(
                    client,
                    service_name,
                    timeout_sec,
                    operation_id=operation_id,
                )
            else:
                self.project_repository.update_motor_operation(
                    operation_id,
                    'scanning',
                    message='모터 물리 검색 진행 중',
                )
                result = self._call_scan_service_locked(client, service_name, timeout_sec)
            current = self.project_repository.motor_operation_status()
            outcome = self._scan_operation_outcome(
                result.get('scan'),
                operation_type=operation_type,
                fallback_success=result.get('success') is True,
            )
            result['partial'] = outcome == 'partial'
            result['success'] = outcome == 'success'
            if (
                current.get('operation_id') == operation_id
                and current.get('status') == 'running'
            ):
                current = self.project_repository.finish_motor_operation(
                    operation_id,
                    outcome,
                    phase={
                        'success': 'completed',
                        'partial': 'partial',
                        'failure': 'failed',
                    }[outcome],
                    message=str(result.get('message') or ''),
                    error=(
                        ''
                        if outcome in {'success', 'partial'}
                        else str(result.get('message') or '')
                    ),
                )
            result.update(self.snapshot())
            result['motor_operation'] = current
            return result
        except ValueError as exc:
            return {
                'success': False,
                'message': str(exc),
                'scan': None,
                'project_id': self.project_repository.selected_project_id(),
                'project_generation': self._current_project_generation(),
                **self.snapshot(),
            }
        except Exception as exc:
            operation_id = str(operation.get('operation_id') or '')
            if operation_id:
                try:
                    self.project_repository.finish_motor_operation(
                        operation_id,
                        'failure',
                        phase='failed',
                        error=str(exc),
                    )
                except ValueError:
                    pass
            raise
        finally:
            scan_lock.release()
            lifecycle_lock.release()

    def _call_ethercat_scan_service_locked(
        self,
        client,
        service_name: str,
        timeout_sec: float,
        *,
        operation_id: str = '',
    ) -> Dict[str, Any]:
        """Release the persistent EtherCAT owner for one physical scan.

        The scan contract requires ``ethercat rescan``.  The persistent Motor
        Manager must therefore be stopped first and restored afterwards.  This
        orchestration belongs to the upper web layer; motion_system remains
        unchanged.
        """
        motor_service = str(
            os.environ.get('MOTION_MOTOR_SERVICE_UNIT') or ''
        ).strip()
        if motor_service != 'motion-motor.service':
            return self._call_scan_service_locked(client, service_name, timeout_sec)

        blocker = self._ethercat_scan_safety_blocker(
            require_fresh_motor_state=False,
        )
        if blocker:
            return {
                'success': False,
                'message': f'AC Servo 검색 미실행: {blocker}',
                'scan': None,
                'scan_blocked': True,
                'project_id': self.project_repository.selected_project_id(),
                'project_generation': self._current_project_generation(),
                **self.snapshot(),
            }

        was_active = self._managed_user_service_active(motor_service)
        runtime_handoff = self._ethercat_scan_runtime_handoff()
        restore_runtime = bool(was_active and not runtime_handoff['required'])
        if restore_runtime:
            blocker = self._ethercat_scan_safety_blocker(
                require_fresh_motor_state=True,
            )
            if blocker:
                return {
                    'success': False,
                    'message': f'AC Servo 검색 미실행: {blocker}',
                    'scan': None,
                    'scan_blocked': True,
                    'project_id': self.project_repository.selected_project_id(),
                    'project_generation': self._current_project_generation(),
                    **self.snapshot(),
                }

        expected_ethercat_axes = (
            self._expected_runtime_ethercat_axes() if restore_runtime else []
        )
        expected_recovery_axes = (
            self._expected_runtime_axes() if restore_runtime else []
        )
        if operation_id:
            self.project_repository.update_motor_operation(
                operation_id,
                'preparing',
                details={
                    'motor_service_was_active': was_active,
                    'expected_axes': expected_recovery_axes,
                    'expected_ethercat_axes': expected_ethercat_axes,
                    'runtime_handoff': runtime_handoff,
                },
            )
        if restore_runtime and not expected_ethercat_axes:
            return {
                'success': False,
                'message': (
                    'AC Servo 검색 미실행: 실행 설정에서 복구 대상 EtherCAT 축을 '
                    '확인할 수 없습니다'
                ),
                'scan': None,
                'scan_blocked': True,
                'project_id': self.project_repository.selected_project_id(),
                'project_generation': self._current_project_generation(),
                **self.snapshot(),
            }
        if restore_runtime and not expected_recovery_axes:
            return {
                'success': False,
                'message': (
                    'AC Servo 검색 미실행: 실행 설정에서 복구 대상 전체 모터축을 '
                    '확인할 수 없습니다'
                ),
                'scan': None,
                'scan_blocked': True,
                'project_id': self.project_repository.selected_project_id(),
                'project_generation': self._current_project_generation(),
                **self.snapshot(),
            }
        result: Dict[str, Any]
        restore_error = ''
        recovery: Dict[str, Any] = {
            'required': restore_runtime,
            'expected_axes': expected_recovery_axes if restore_runtime else [],
            'online_axes': [],
            'recovered': not restore_runtime,
        }
        try:
            if was_active:
                if operation_id:
                    stop_message = (
                        '이전 프로젝트 Motor Manager 정지 및 EtherCAT 소유권 해제 중'
                        if runtime_handoff['required']
                        else 'Motor Manager 정지 및 EtherCAT 소유권 해제 중'
                    )
                    self.project_repository.update_motor_operation(
                        operation_id,
                        'stopping_runtime',
                        message=stop_message,
                    )
                self._run_managed_user_service('stop', motor_service)
                self._wait_for_ethercat_release(timeout_sec=5.0)
            if operation_id:
                self.project_repository.update_motor_operation(
                    operation_id,
                    'scanning',
                    message='AC Servo 물리 검색 진행 중',
                )
            result = self._call_scan_service_locked(client, service_name, timeout_sec)
        except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
            result = {
                'success': False,
                'message': f'AC Servo 검색 미실행: {exc}',
                'scan': None,
                'scan_blocked': True,
                'project_id': self.project_repository.selected_project_id(),
                'project_generation': self._current_project_generation(),
                **self.snapshot(),
            }
        finally:
            if restore_runtime:
                try:
                    if operation_id:
                        try:
                            self.project_repository.update_motor_operation(
                                operation_id,
                                'restoring',
                                message='검색 전 Motor Manager 실행 상태 복구 중',
                            )
                        except ValueError:
                            # Runtime restoration is a safety action and must
                            # not depend on operation bookkeeping still being
                            # writable/running.
                            pass
                    self._run_managed_user_service('start', motor_service)
                    recovery = self._wait_for_motor_runtime_recovery(
                        expected_recovery_axes,
                        timeout_sec=12.0,
                        motor_service=motor_service,
                    )
                    if not recovery.get('recovered'):
                        restore_error = (
                            'Motor Manager 재시작 후 서비스·모터 상태 복구 실패: '
                            f'{len(recovery.get("online_axes") or [])}/'
                            f'{len(expected_recovery_axes)}축'
                        )
                except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
                    restore_error = str(exc)

        result['motor_service_was_active'] = was_active
        result['motor_service_restore_required'] = restore_runtime
        result['motor_service_restored'] = bool(
            restore_runtime and not restore_error
        )
        result['motor_runtime_recovery'] = recovery
        result['runtime_handoff'] = runtime_handoff
        if runtime_handoff['required'] and result.get('success') is True:
            result['message'] = (
                f'{result.get("message") or "AC Servo 검색 완료"} / '
                '이전 프로젝트 모터 실행은 정지되었습니다. '
                '현재 프로젝트 설정을 저장한 뒤 설정 적용 및 재시작하세요'
            )
        if restore_error:
            result['success'] = False
            result['restore_error'] = restore_error
            result['message'] = (
                f'{result.get("message") or "AC Servo 검색 종료"} / '
                f'Motor Manager 복구 실패: {restore_error}'
            )
        return result

    def _ethercat_scan_runtime_handoff(self) -> Dict[str, Any]:
        """Describe whether an active Motor Manager belongs to another project.

        A project switch intentionally does not change the active runtime.
        Therefore a physical scan for the newly selected project must be able
        to retire the previous project's runtime without depending on feedback
        from that runtime.  The scan safety blocker still rejects every active
        upper-level motion operation and any observed moving EtherCAT axis.
        """
        selected_project_id = str(
            self.project_repository.selected_project_id() or ''
        ).strip()
        runtime_project_id = ''
        try:
            runtime_state = self.project_repository.motor_runtime_state()
        except (AttributeError, OSError, ValueError, json.JSONDecodeError):
            runtime_state = {}
        if isinstance(runtime_state, dict):
            runtime_project_id = str(
                runtime_state.get('target_project_id') or ''
            ).strip()
        if not runtime_project_id:
            runtime_project_id = str(
                self._runtime_project_id_from_path(selected_project_id) or ''
            ).strip()
        return {
            'required': bool(
                selected_project_id
                and runtime_project_id
                and runtime_project_id != selected_project_id
            ),
            'selected_project_id': selected_project_id,
            'runtime_project_id': runtime_project_id,
        }

    def _ethercat_scan_safety_blocker(
        self,
        *,
        require_fresh_motor_state: bool = True,
        allow_run_stopping: bool = False,
        allow_studio_stopping: bool = False,
    ) -> str:
        blocker = self._project_change_blocker(
            ignore_motor_lifecycle=True,
            allow_run_stopping=allow_run_stopping,
            allow_studio_stopping=allow_studio_stopping,
        )
        if blocker:
            return blocker

        with self._lock:
            motion_state = copy.deepcopy(self._motion_state)
            received_at = self._motion_state_received_at
        if not isinstance(motion_state, dict) or received_at is None:
            return (
                '최신 모터 상태를 확인할 수 없습니다'
                if require_fresh_motor_state else ''
            )
        if time.time() - float(received_at) > 1.0:
            return (
                '최신 모터 상태가 중단되어 정지 여부를 확인할 수 없습니다'
                if require_fresh_motor_state else ''
            )

        moving_axes = []
        observed_axes = []
        for motor in motion_state.get('motors') or []:
            if not isinstance(motor, dict):
                continue
            if str(motor.get('transport') or '').lower() != 'ethercat':
                continue
            observed_axes.append(str(motor.get('controller_index', '?')))
            if (
                motor.get('connection_connected') is not True
                or str(motor.get('connection_state') or '') != 'online'
                or motor.get('fault') is True
            ):
                continue
            velocity = _monitoring_finite_float(
                motor.get('velocity_deg_s', motor.get('velocity'))
            )
            target_reached = motor.get('target_reached') is True
            # A stopped servo can report roughly 1~2 deg/s of quantization
            # noise.  Ignore that noise only when the drive also reports that
            # its target has been reached.  Missing/false target state keeps
            # the stricter threshold, while clear motion is always blocked.
            moving = (
                velocity is not None
                and (
                    abs(velocity) > 5.0
                    or (not target_reached and abs(velocity) > 1.0)
                )
            )
            if moving:
                moving_axes.append(str(motor.get('controller_index', '?')))
        if require_fresh_motor_state and not observed_axes:
            return '최신 모터 상태에서 AC Servo 축을 확인할 수 없습니다'
        if moving_axes:
            return (
                f'AC Servo 축 {", ".join(moving_axes)}이 움직이는 중입니다. '
                '완전히 정지한 뒤 다시 검색하세요'
            )
        return ''

    @staticmethod
    def _managed_user_service_active(service: str) -> bool:
        completed = subprocess.run(
            ['/usr/bin/systemctl', '--user', 'is-active', '--quiet', service],
            check=False,
            capture_output=True,
            text=True,
            timeout=3.0,
        )
        return completed.returncode == 0

    @staticmethod
    def _run_managed_user_service(action: str, service: str) -> None:
        if action not in {'start', 'stop'} or service != 'motion-motor.service':
            raise ValueError('허용되지 않은 Motor Manager 서비스 작업입니다')
        completed = subprocess.run(
            ['/usr/bin/systemctl', '--user', action, service],
            check=False,
            capture_output=True,
            text=True,
            timeout=10.0,
        )
        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip()
            raise RuntimeError(detail or f'{service} {action} 실패')

    @staticmethod
    def _wait_for_ethercat_release(timeout_sec: float) -> None:
        deadline = time.time() + timeout_sec
        last_output = ''
        while time.time() < deadline:
            master = subprocess.run(
                ['ethercat', 'master'],
                check=False,
                capture_output=True,
                text=True,
                timeout=2.0,
            )
            last_output = master.stderr.strip() or master.stdout.strip()
            if master.returncode == 0:
                claimed = bool(
                    re.search(
                        r'^\s*Phase:\s*Operation\s*$',
                        master.stdout,
                        re.MULTILINE | re.IGNORECASE,
                    )
                    or re.search(
                        r'^\s*Active:\s*yes\s*$',
                        master.stdout,
                        re.MULTILINE | re.IGNORECASE,
                    )
                )
                if not claimed:
                    slaves = subprocess.run(
                        ['ethercat', 'slaves'],
                        check=False,
                        capture_output=True,
                        text=True,
                        timeout=2.0,
                    )
                    last_output = slaves.stderr.strip() or slaves.stdout.strip()
                    active_slave = bool(
                        slaves.returncode == 0
                        and re.search(
                            r'^\s*\d+\s+\S+\s+(?:SAFEOP|OP)\b',
                            slaves.stdout,
                            re.MULTILINE | re.IGNORECASE,
                        )
                    )
                    if slaves.returncode == 0 and not active_slave:
                        return
            time.sleep(0.05)
        raise RuntimeError(
            'Motor Manager 정지 후에도 EtherCAT Master 또는 Slave 운전 상태가 해제되지 않았습니다'
            + (f': {last_output}' if last_output else '')
        )

    def _expected_runtime_ethercat_axes(self) -> List[int]:
        repository = getattr(self, 'project_repository', None)
        runtime = (
            repository.applied_runtime_motor_config()
            if repository is not None
            and hasattr(repository, 'applied_runtime_motor_config')
            else None
        )
        if runtime is not None:
            return self._configured_axes_from_runtime_file(
                runtime,
                transport='ethercat',
            )

        # Compatibility fallback for an unmanaged/legacy launch with no
        # durable runtime target.
        with self._lock:
            motion_state = copy.deepcopy(self._motion_state)
            received_at = self._motion_state_received_at
        if (
            not isinstance(motion_state, dict)
            or received_at is None
            or time.time() - float(received_at) > 1.0
        ):
            return []
        axes = []
        for motor in motion_state.get('motors') or []:
            if not isinstance(motor, dict):
                continue
            if str(motor.get('transport') or '').lower() != 'ethercat':
                continue
            if motor.get('connection_connected') is not True:
                continue
            try:
                axes.append(int(motor.get('controller_index')))
            except (TypeError, ValueError):
                continue
        return sorted(set(axes))

    def _expected_runtime_axes(self) -> List[int]:
        repository = getattr(self, 'project_repository', None)
        runtime = (
            repository.applied_runtime_motor_config()
            if repository is not None
            and hasattr(repository, 'applied_runtime_motor_config')
            else None
        )
        if runtime is not None:
            return self._configured_axes_from_runtime_file(runtime)

        with self._lock:
            motion_state = copy.deepcopy(self._motion_state)
            received_at = self._motion_state_received_at
        if (
            not isinstance(motion_state, dict)
            or received_at is None
            or time.time() - float(received_at) > 1.0
        ):
            return []
        axes = []
        for motor in motion_state.get('motors') or []:
            if not isinstance(motor, dict):
                continue
            if motor.get('connection_connected') is not True:
                continue
            try:
                axes.append(int(motor.get('controller_index')))
            except (TypeError, ValueError):
                continue
        return sorted(set(axes))

    @staticmethod
    def _configured_axes_from_runtime_file(
        runtime: Path | str,
        *,
        transport: str = '',
    ) -> List[int]:
        try:
            payload = yaml.safe_load(
                Path(runtime).read_text(encoding='utf-8')
            ) or {}
            axes = [
                int(slave['controller_index'])
                for master in payload.get('masters') or []
                if isinstance(master, dict)
                and (
                    not transport
                    or str(master.get('type') or '').lower() == transport.lower()
                )
                for slave in master.get('slaves') or []
                if isinstance(slave, dict) and 'controller_index' in slave
            ]
            return sorted(set(axes))
        except (OSError, TypeError, ValueError, yaml.YAMLError):
            return []

    def _wait_for_motor_runtime_recovery(
        self,
        expected_axes: List[int],
        timeout_sec: float,
        motor_service: str = '',
    ) -> Dict[str, Any]:
        expected = sorted(set(int(axis) for axis in expected_axes))
        started_at = time.time()
        online_axes: List[int] = []
        if not expected:
            return {
                'required': True,
                'expected_axes': [],
                'online_axes': [],
                'recovered': False,
                'service_active': (
                    not motor_service
                    or self._managed_user_service_active(motor_service)
                ),
                'duration_sec': 0.0,
                'error': '복구 대상 EtherCAT 축을 확인할 수 없습니다',
            }
        while time.time() - started_at < timeout_sec:
            service_active = (
                not motor_service
                or self._managed_user_service_active(motor_service)
            )
            with self._lock:
                motion_state = copy.deepcopy(self._motion_state)
                received_at = self._motion_state_received_at
            online_axes = []
            if (
                isinstance(motion_state, dict)
                and received_at is not None
                and float(received_at) >= started_at
            ):
                for motor in motion_state.get('motors') or []:
                    if not isinstance(motor, dict):
                        continue
                    if motor.get('connection_connected') is not True:
                        continue
                    if motor.get('fault') is True:
                        continue
                    try:
                        online_axes.append(int(motor.get('controller_index')))
                    except (TypeError, ValueError):
                        continue
                online_axes = sorted(set(online_axes))
                if service_active and all(axis in online_axes for axis in expected):
                    return {
                        'required': True,
                        'expected_axes': expected,
                        'online_axes': online_axes,
                        'recovered': True,
                        'service_active': True,
                        'duration_sec': round(time.time() - started_at, 3),
                    }
            time.sleep(0.05)
        return {
            'required': True,
            'expected_axes': expected,
            'online_axes': online_axes,
            'recovered': False,
            'service_active': (
                not motor_service
                or self._managed_user_service_active(motor_service)
            ),
            'duration_sec': round(time.time() - started_at, 3),
        }

    def _call_scan_service_locked(
        self,
        client,
        service_name: str,
        timeout_sec: float,
    ) -> Dict[str, Any]:
        scan_project_id = self.project_repository.selected_project_id()
        scan_generation = self._current_project_generation()
        if not client.wait_for_service(timeout_sec=0.2):
            return {
                'success': False,
                'message': f'scan service unavailable: {service_name}',
                'scan': None,
                'project_generation': scan_generation,
                **self.snapshot(),
            }

        future = client.call_async(Trigger.Request())
        deadline = time.time() + timeout_sec
        while not future.done() and time.time() < deadline:
            time.sleep(0.02)

        if not future.done():
            return {
                'success': False,
                'message': 'scan service timeout',
                'scan': None,
                'project_generation': scan_generation,
                **self.snapshot(),
            }

        response = future.result()
        if (
            self.project_repository.selected_project_id() != scan_project_id
            or self._current_project_generation() != scan_generation
        ):
            return {
                'success': False,
                'message': '프로젝트가 변경되어 이전 프로젝트의 검색 결과를 폐기했습니다',
                'scan': None,
                'project_id': self.project_repository.selected_project_id(),
                'project_generation': self._current_project_generation(),
                **self.snapshot(),
            }
        scan = None
        try:
            scan = json.loads(response.message)
        except json.JSONDecodeError:
            self.get_logger().warn('Invalid scan JSON received.')
        if isinstance(scan, dict):
            self._annotate_ethercat_project_compatibility(scan)
        message = self._scan_result_message(
            bool(response.success),
            scan,
            str(response.message or ''),
        )

        return {
            'success': bool(response.success),
            'message': message,
            'scan': scan,
            'project_id': scan_project_id,
            'project_generation': scan_generation,
            **self.snapshot(),
        }

    def _annotate_ethercat_project_compatibility(
        self,
        scan: Dict[str, Any],
    ) -> None:
        """Compare physical EtherCAT evidence with the selected project.

        Physical scan completeness remains unchanged: every registered Master
        is still rescanned and reported.  This additional result only answers
        whether the Masters used by the selected project match exactly, so an
        unused disconnected Master is not confused with a project mismatch.
        """
        ethercat = scan.get('ethercat_scan')
        if not isinstance(ethercat, dict) or ethercat.get('skipped') is True:
            return

        comparison = scan.setdefault('project_comparison', {})
        if not isinstance(comparison, dict):
            comparison = {}
            scan['project_comparison'] = comparison

        try:
            registry_result = self.load_motor_config()
        except (OSError, ValueError, yaml.YAMLError) as exc:
            comparison['ethercat_project'] = {
                'available': False,
                'compatible': False,
                'message': f'현재 프로젝트 모터축 설정 확인 실패: {exc}',
            }
            return
        if registry_result.get('success') is not True:
            comparison['ethercat_project'] = {
                'available': False,
                'compatible': False,
                'message': str(
                    registry_result.get('message')
                    or '현재 프로젝트 모터축 설정을 확인할 수 없습니다'
                ),
            }
            return

        expected_by_master: Dict[int, List[Dict[str, Any]]] = {}
        registry = registry_result.get('registry')
        for motor in (
            registry.get('motors', [])
            if isinstance(registry, dict)
            else []
        ):
            if not isinstance(motor, dict):
                continue
            if str(motor.get('transport') or '').lower() != 'ethercat':
                continue
            if motor.get('enabled') is False or motor.get('deleted') is True:
                continue
            config = motor.get('config') if isinstance(motor.get('config'), dict) else {}
            identity = (
                motor.get('identity')
                if isinstance(motor.get('identity'), dict)
                else {}
            )
            master_index = self._optional_int(
                config.get('ethercat_master_index'),
                self._optional_int(identity.get('ethercat_master_index'), 0),
            )
            position = self._optional_int(config.get('position'), None)
            if master_index is None or master_index < 0 or position is None:
                continue
            expected_by_master.setdefault(master_index, []).append({
                'controller_index': self._optional_int(
                    config.get('controller_index'),
                    self._optional_int(motor.get('axis'), None),
                ),
                'position': position,
                'alias': self._optional_int(
                    identity.get('ethercat_alias'),
                    self._optional_int(config.get('alias'), 0),
                ),
                'vendor_id': self._optional_int(
                    identity.get('vendor_id'),
                    self._optional_int(config.get('vendor_id'), None),
                ),
                'product_code': self._optional_int(
                    identity.get('product_code'),
                    self._optional_int(config.get('product_id'), None),
                ),
                'serial_number': self._optional_int(
                    identity.get('serial_number'), None
                ),
            })

        if not expected_by_master:
            comparison['ethercat_project'] = {
                'available': False,
                'compatible': False,
                'message': '현재 프로젝트에 EtherCAT 모터축 설정이 없습니다',
                'required_master_indices': [],
            }
            return

        observed_by_master: Dict[int, List[Dict[str, Any]]] = {}
        for slave in ethercat.get('slaves') or []:
            if not isinstance(slave, dict):
                continue
            master_index = self._optional_int(slave.get('master_index'), 0)
            if master_index is None:
                continue
            observed_by_master.setdefault(master_index, []).append(slave)

        master_rows = []
        compatible = True
        for master_index in sorted(expected_by_master):
            expected = expected_by_master[master_index]
            observed = observed_by_master.get(master_index, [])
            errors = []
            if len(observed) != len(expected):
                errors.append(f'축 수 {len(observed)}/{len(expected)}')
            observed_by_position = {
                self._optional_int(item.get('slave_position'), None): item
                for item in observed
                if self._optional_int(item.get('slave_position'), None) is not None
            }
            for target in expected:
                position = target['position']
                actual = observed_by_position.get(position)
                if actual is None:
                    errors.append(f'Slave {position} 응답 없음')
                    continue
                if actual.get('direct_read_complete') is not True:
                    errors.append(
                        f'Slave {position} 물리 식별정보 읽기 미완료'
                    )
                for field in ('vendor_id', 'product_code', 'serial_number'):
                    expected_value = target.get(field)
                    actual_value = self._optional_int(actual.get(field), None)
                    if (
                        expected_value is not None
                        and expected_value > 0
                        and actual_value != expected_value
                    ):
                        errors.append(
                            f'Slave {position} {field} 불일치'
                        )
                expected_alias = target.get('alias')
                actual_alias = self._optional_int(
                    actual.get('ethercat_alias'), 0
                )
                if (
                    expected_alias is not None
                    and expected_alias > 0
                    and actual_alias != expected_alias
                ):
                    errors.append(f'Slave {position} EEPROM Alias 불일치')
            row_complete = not errors
            compatible = compatible and row_complete
            master_rows.append({
                'master_index': master_index,
                'expected_slaves_count': len(expected),
                'observed_slaves_count': len(observed),
                'compatible': row_complete,
                'errors': errors,
            })

        registered_indices = {
            self._optional_int(row.get('master_index'), 0)
            for row in ethercat.get('masters') or []
            if isinstance(row, dict)
        }
        required_indices = sorted(expected_by_master)
        unused_indices = sorted(
            index
            for index in registered_indices
            if index is not None and index not in expected_by_master
        )
        comparison['ethercat_project'] = {
            'available': True,
            'compatible': compatible,
            'required_master_indices': required_indices,
            'unused_registered_master_indices': unused_indices,
            'masters': master_rows,
            'message': (
                f'프로젝트 EtherCAT 구성 확인 완료 · '
                f'Master {", ".join(str(index) for index in required_indices)}'
                if compatible
                else '프로젝트 EtherCAT 구성 불일치'
            ),
        }

    @staticmethod
    def _scan_item_has_detected_devices(scan_item: Any) -> bool:
        if not isinstance(scan_item, dict) or scan_item.get('skipped') is True:
            return False
        for key in ('slaves_count', 'devices_count'):
            try:
                if int(scan_item.get(key) or 0) > 0:
                    return True
            except (TypeError, ValueError):
                pass
        for key in ('slaves', 'devices'):
            value = scan_item.get(key)
            if isinstance(value, list) and len(value) > 0:
                return True
        return False

    @staticmethod
    def _scan_result_message(
        success: bool,
        scan: Any,
        fallback: str,
    ) -> str:
        """Keep scan evidence in ``scan`` and expose only a concise UI message."""
        if not isinstance(scan, dict):
            return fallback

        ethercat = scan.get('ethercat_scan')
        if not isinstance(ethercat, dict):
            physical = scan.get('physical_scan')
            if isinstance(physical, dict):
                ethercat = physical.get('ethercat')

        dynamixel = scan.get('dynamixel_scan')
        if not isinstance(dynamixel, dict):
            physical = scan.get('physical_scan')
            if isinstance(physical, dict):
                dynamixel = physical.get('dynamixel')
        requested = [
            item
            for item in (ethercat, dynamixel)
            if isinstance(item, dict) and item.get('skipped') is not True
        ]
        project_comparison = scan.get('project_comparison')
        ethercat_project = (
            project_comparison.get('ethercat_project')
            if isinstance(project_comparison, dict)
            else None
        )
        project_compatible = bool(
            isinstance(ethercat_project, dict)
            and ethercat_project.get('compatible') is True
        )
        project_incompatible = bool(
            isinstance(ethercat_project, dict)
            and ethercat_project.get('available') is True
            and ethercat_project.get('compatible') is not True
        )
        partial = bool(
            not success
            and not project_incompatible
            and (
                project_compatible
                or (
                    any(item.get('complete') is True for item in requested)
                    and any(item.get('complete') is not True for item in requested)
                )
                or any(
                    MotionWebBridge._scan_item_has_detected_devices(item)
                    for item in requested
                )
            )
        )
        parts = [
            (
                '모터 검색 완료'
                if success
                else '모터 검색 부분 완료'
                if partial
                else '모터 검색 실패'
            )
        ]
        if isinstance(ethercat, dict) and ethercat.get('skipped') is not True:
            try:
                parts.append(f'AC Servo {int(ethercat.get("slaves_count") or 0)}축')
            except (TypeError, ValueError):
                pass
            master_rows = ethercat.get('masters')
            if isinstance(master_rows, list) and master_rows:
                parts.append(
                    ' / '.join(
                        (
                            f'Master {int(row.get("master_index") or 0)} '
                            f'{int(row.get("slaves_count") or 0)}축'
                        )
                        for row in master_rows
                        if isinstance(row, dict)
                    )
                )
        if isinstance(dynamixel, dict) and dynamixel.get('skipped') is not True:
            try:
                parts.append(f'Dynamixel {int(dynamixel.get("devices_count") or 0)}축')
            except (TypeError, ValueError):
                pass
        if project_compatible:
            parts.append('프로젝트 EtherCAT 구성 확인 완료')
            unused = ethercat_project.get('unused_registered_master_indices') or []
            if unused:
                parts.append(
                    '미사용 Master '
                    + ', '.join(str(index) for index in unused)
                    + ' 미연결 허용'
                )

        scan_id = str(scan.get('scan_id') or '').strip()
        if scan_id:
            parts.append(f'scan_id {scan_id}')
        errors = scan.get('scan_errors')
        if not success and isinstance(errors, list):
            concise_errors = [
                str(
                    error.get('message')
                    if isinstance(error, dict)
                    else error
                ).strip()
                for error in errors[:2]
                if str(
                    error.get('message')
                    if isinstance(error, dict)
                    else error
                ).strip()
            ]
            if concise_errors:
                parts.append(', '.join(concise_errors))
        return ' · '.join(parts)

    @staticmethod
    def _scan_operation_outcome(
        scan: Any,
        *,
        operation_type: str,
        fallback_success: bool,
    ) -> str:
        if not isinstance(scan, dict):
            return 'success' if fallback_success else 'failure'

        ethercat = scan.get('ethercat_scan')
        dynamixel = scan.get('dynamixel_scan')
        physical = scan.get('physical_scan')
        if isinstance(physical, dict):
            if not isinstance(ethercat, dict):
                ethercat = physical.get('ethercat')
            if not isinstance(dynamixel, dict):
                dynamixel = physical.get('dynamixel')

        if operation_type == 'full_scan':
            requested = [ethercat, dynamixel]
        elif operation_type == 'ac_servo_scan':
            requested = [ethercat]
        elif operation_type == 'dynamixel_scan':
            requested = [dynamixel]
        else:
            requested = [
                item
                for item in (ethercat, dynamixel)
                if isinstance(item, dict) and item.get('skipped') is not True
            ]
        requested = [item for item in requested if isinstance(item, dict)]
        if not requested:
            return 'success' if fallback_success else 'failure'
        project_comparison = scan.get('project_comparison')
        ethercat_project = (
            project_comparison.get('ethercat_project')
            if isinstance(project_comparison, dict)
            else None
        )
        if (
            operation_type in {'full_scan', 'ac_servo_scan'}
            and isinstance(ethercat_project, dict)
            and ethercat_project.get('available') is True
            and ethercat_project.get('compatible') is not True
        ):
            return 'failure'
        completed = [item.get('complete') is True for item in requested]
        if all(completed):
            return 'success'
        if any(completed):
            return 'partial'
        if any(
            MotionWebBridge._scan_item_has_detected_devices(item)
            for item in requested
        ):
            return 'partial'
        if (
            operation_type in {'full_scan', 'ac_servo_scan'}
            and isinstance(ethercat_project, dict)
            and ethercat_project.get('compatible') is True
        ):
            return 'partial'
        return 'failure'

    def list_motion_projects(self) -> Dict[str, Any]:
        result = self.project_repository.list_projects()
        result['project_generation'] = self._current_project_generation()
        runtime_project_id = self._runtime_project_id()
        result['runtime_project_id'] = runtime_project_id
        for project in result.get('projects') or []:
            project['runtime_active'] = project.get('project_id') == runtime_project_id
        return result

    def _runtime_project_id(self) -> str:
        project_id = self._runtime_project_id_from_path()
        if not project_id:
            return ''
        try:
            self.project_repository.get_project(project_id)
        except (OSError, ValueError, json.JSONDecodeError):
            return ''
        return project_id

    def _runtime_project_id_from_path(self, selected_project_id: str = '') -> str:
        """Resolve launch-time runtime ownership without parsing project YAML.

        This helper is used only on high-frequency, read-only status paths.
        Project mutation and execution-context paths continue to call
        ``_runtime_project_id`` and perform the full repository validation.
        """
        try:
            relative = self.applied_motor_config_file.relative_to(
                self.motion_projects_dir.resolve()
            )
        except (AttributeError, ValueError):
            return ''
        parts = relative.parts
        if len(parts) < 2:
            return ''
        project_id = str(parts[0])
        return project_id

    def _selected_project_owns_runtime(self) -> bool:
        selected = self.project_repository.selected_project_id()
        return bool(
            selected
            and selected == self._runtime_project_id_from_path()
        )

    def _current_project_generation(self) -> int:
        lock = getattr(self, '_project_generation_lock', None)
        if lock is None:
            return int(getattr(self, '_project_generation', 1))
        with lock:
            return int(self._project_generation)

    def _advance_project_generation(self) -> int:
        with self._project_generation_lock:
            next_generation = self._project_generation + 1
            self.project_repository.set_project_generation(next_generation)
            self._project_generation = next_generation
            return int(self._project_generation)

    def _new_project_request_id(self, prefix: str) -> str:
        return f'{prefix}-g{self._current_project_generation()}-{time.time_ns()}'

    def _request_matches_current_generation(self, request_id: Any) -> bool:
        marker = f'-g{self._current_project_generation()}-'
        return marker in str(request_id or '')

    def _response_matches_current_generation(self, payload: Any) -> bool:
        if not isinstance(payload, dict):
            return False
        try:
            generation = int(payload.get('project_generation'))
        except (TypeError, ValueError):
            return False
        return (
            generation == self._current_project_generation()
            and self._request_matches_current_generation(payload.get('request_id'))
        )

    def _payload_matches_selected_project(
        self, payload: Any, *, require_generation: bool = True
    ) -> bool:
        """Reject status belonging to any project other than the selected one."""
        if not isinstance(payload, dict):
            return False
        nested = payload.get('execution_context')
        if not isinstance(nested, dict):
            nested = {}
        project_id = str(
            payload.get('project_id')
            or payload.get('workspace_project_id')
            or nested.get('project_id')
            or ''
        ).strip()
        selected = self.project_repository.selected_project_id()
        generation = payload.get('project_generation')
        if generation is None:
            generation = nested.get('project_generation')
        if not require_generation:
            generation_matches = True
        else:
            try:
                generation_matches = int(generation) == self._current_project_generation()
            except (TypeError, ValueError):
                generation_matches = False
        return bool(
            selected and project_id and project_id == selected and generation_matches
        )

    def _clear_project_scoped_memory(self) -> None:
        """Permanently discard every cached value owned by the old project."""
        with self._lock:
            self._motion_state = None
            self._motion_state_received_at = None
        with self._event_log_lock:
            self._active_motor_errors = {}
            self._last_motion_run_state = None
        with self._jog_result_lock:
            self._jog_results.clear()
        with self._action_result_lock:
            self._action_results.clear()
        with self._motion_mapping_lock:
            self._motion_mapping_results.clear()
        with self._motion_run_lock:
            self._motion_run_results.clear()
            self._motion_run_status = {}
        with self._midi_monitor_lock:
            self._midi_monitor_results.clear()
            self._midi_monitor_status = {}
        self._motion_studio_sync().clear_project_memory()
        empty_scan_progress = {
            'scan_id': '',
            'events': [],
            'running': False,
            'updated_at': None,
        }
        scan_progress_lock = getattr(self, '_scan_progress_lock', None)
        if scan_progress_lock is None:
            self._scan_progress = empty_scan_progress
        else:
            with scan_progress_lock:
                self._scan_progress = empty_scan_progress

    def _project_change_blocker(
        self,
        *,
        ignore_motor_lifecycle: bool = False,
        allow_run_stopping: bool = False,
        allow_studio_stopping: bool = False,
    ) -> str:
        lifecycle_lock = getattr(self, '_motor_lifecycle_lock', None)
        if (
            not ignore_motor_lifecycle
            and lifecycle_lock is not None
            and lifecycle_lock.locked()
        ):
            return '모터 설정·검색·재시작 작업이 진행 중이므로 프로젝트를 변경할 수 없습니다'
        repository = getattr(self, 'project_repository', None)
        if (
            not ignore_motor_lifecycle
            and repository is not None
            and hasattr(repository, 'motor_operation_status')
        ):
            operation = repository.motor_operation_status()
            if operation.get('status') == 'running':
                return '모터 설정·검색·재시작 작업이 진행 중이므로 프로젝트를 변경할 수 없습니다'
        run_lock = getattr(self, '_motion_run_lock', None)
        if run_lock is None:
            run_status = getattr(self, '_motion_run_status', {})
        else:
            with run_lock:
                run_status = dict(getattr(self, '_motion_run_status', {}) or {})
        studio_lock = getattr(self, '_motion_studio_lock', None)
        if studio_lock is None:
            studio_status = getattr(self, '_motion_studio_status', {})
        else:
            with studio_lock:
                studio_status = dict(getattr(self, '_motion_studio_status', {}) or {})
        run_state = str((run_status or {}).get('state') or 'idle')
        studio_state = str((studio_status or {}).get('state') or 'idle')
        blocked_run_states = {
            'initializing',
            'initialized',
            'running',
            'waiting',
            'verifying',
            'stopping',
        }
        if allow_run_stopping:
            blocked_run_states.discard('stopping')
        if run_state in blocked_run_states:
            return f'모션 동작 상태가 {run_state}이므로 프로젝트를 변경할 수 없습니다'
        blocked_studio_states = {
            'initializing', 'countdown', 'recording', 'playing', 'stopping',
        }
        if allow_studio_stopping:
            blocked_studio_states.discard('stopping')
        if studio_state in blocked_studio_states:
            return f'모션 스튜디오 상태가 {studio_state}이므로 프로젝트를 변경할 수 없습니다'
        return ''

    def _ensure_project_change_allowed(self) -> None:
        blocker = self._project_change_blocker()
        if blocker:
            raise ValueError(blocker)

    def _ensure_project_mutation_allowed(self, project_id: Any) -> None:
        self._ensure_selected_project(project_id)
        self._ensure_project_change_allowed()

    def _ensure_selected_project(self, project_id: Any) -> None:
        if str(project_id or '') != self.project_repository.selected_project_id():
            raise ValueError('현재 선택한 프로젝트 파일만 사용할 수 있습니다')

    def _clear_stopping_project_release_state(self) -> None:
        run_lock = getattr(self, '_motion_run_lock', None)
        if run_lock is None:
            run_status = getattr(self, '_motion_run_status', {}) or {}
            if str(run_status.get('state') or '') == 'stopping':
                self._motion_run_status = {
                    **dict(run_status),
                    'state': 'stopped',
                    'message': '실행 적용 해제로 정지 상태를 정리했습니다',
                }
        else:
            with run_lock:
                run_status = getattr(self, '_motion_run_status', {}) or {}
                if str(run_status.get('state') or '') == 'stopping':
                    self._motion_run_status = {
                        **dict(run_status),
                        'state': 'stopped',
                        'message': '실행 적용 해제로 정지 상태를 정리했습니다',
                    }
        studio_lock = getattr(self, '_motion_studio_lock', None)
        if studio_lock is None:
            studio_status = getattr(self, '_motion_studio_status', {}) or {}
            if str(studio_status.get('state') or '') == 'stopping':
                self._motion_studio_status = {
                    **dict(studio_status),
                    'state': 'idle',
                    'message': '실행 적용 해제로 정지 상태를 정리했습니다',
                }
        else:
            with studio_lock:
                studio_status = getattr(self, '_motion_studio_status', {}) or {}
                if str(studio_status.get('state') or '') == 'stopping':
                    self._motion_studio_status = {
                        **dict(studio_status),
                        'state': 'idle',
                        'message': '실행 적용 해제로 정지 상태를 정리했습니다',
                    }

    def create_motion_project(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        self._ensure_project_change_allowed()
        previous_generation = self._current_project_generation()
        self._execution_context_apply_lock.acquire()
        try:
            self._advance_project_generation()
            self._invalidate_execution_nodes()
            created = self.project_repository.create_project(payload.get('name'))
        finally:
            self._execution_context_apply_lock.release()
        result = self.select_motion_project(created['project']['project_id'])
        result['previous_project_generation'] = previous_generation
        result['project_generation'] = self._current_project_generation()
        return result

    def delete_motion_project(self, project_id: Any) -> Dict[str, Any]:
        self._ensure_project_mutation_allowed(project_id)
        if str(project_id or '') == self._runtime_project_id():
            raise ValueError(
                '현재 모터에 적용된 프로젝트는 삭제할 수 없습니다. '
                '「전체 동작 정지」 후 「실행 적용 해제」를 실행하거나, '
                '다른 프로젝트를 적용한 뒤 삭제하세요'
            )
        previous_generation = self._current_project_generation()
        self._execution_context_apply_lock.acquire()
        try:
            self._advance_project_generation()
            self._invalidate_execution_nodes()
            result = self.project_repository.delete_project(project_id)
        finally:
            self._execution_context_apply_lock.release()
        result['previous_project_generation'] = previous_generation
        result['project_generation'] = self._current_project_generation()
        if not self.project_repository.selected_project_id():
            self.motor_config_file = Path()
        return result

    def update_motion_project(self, project_id: Any, payload: Dict[str, Any]) -> Dict[str, Any]:
        self._ensure_selected_project(project_id)
        return self.project_repository.update_project_memo(project_id, payload.get('memo'))

    def copy_motion_project_file(
        self, project_id: Any, payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        self._ensure_project_mutation_allowed(project_id)
        if str(payload.get('category') or '').strip() == 'motions':
            raise ValueError(
                '모션 파일은 프로젝트 복사로 전달할 수 없습니다. '
                '모션 파일 화면의 스튜디오 내보내기를 사용하세요'
            )
        return self.project_repository.copy_file_from_project(
            project_id,
            payload.get('source_project_id'),
            payload.get('category'),
            payload.get('file_name'),
            payload.get('new_name'),
        )

    def _bind_selected_project_sources(self) -> None:
        project_id = self.project_repository.selected_project_id()
        if not project_id:
            self.motor_config_file = Path()
            return
        try:
            detail = self.project_repository.get_project(project_id)
            active = detail.get('project', {}).get('active_files') or {}
            motor_name = str(active.get('motor_axes') or '')
            if motor_name:
                self.motor_config_file = self.project_repository.export_path(
                    project_id, 'motor_axes', motor_name
                )
            else:
                self.motor_config_file = Path()
        except (OSError, ValueError, json.JSONDecodeError):
            self.motor_config_file = Path()
            return

    def _initialize_selected_project_context(self) -> None:
        self._reconcile_execution_context()

    def load_motion_project(self, project_id: Any) -> Dict[str, Any]:
        result = self.project_repository.get_project(project_id)
        result['project_generation'] = self._current_project_generation()
        return result

    def select_motion_project(self, project_id: Any) -> Dict[str, Any]:
        previous_generation = self._current_project_generation()
        changing_project = (
            str(project_id or '') != self.project_repository.selected_project_id()
        )
        if changing_project:
            self._ensure_project_change_allowed()
            self._execution_context_apply_lock.acquire()
        try:
            if changing_project:
                self._advance_project_generation()
                self._invalidate_execution_nodes()
            result = self.project_repository.select_project(project_id)
            result['previous_project_generation'] = previous_generation
            result['project_generation'] = self._current_project_generation()
            active = result.get('project', {}).get('active_files') or {}
            motor_name = str(active.get('motor_axes') or '')
            if motor_name:
                self.motor_config_file = self.project_repository.export_path(
                    project_id, 'motor_axes', motor_name
                )
            else:
                self.motor_config_file = Path()
            self._set_execution_context_status(
                state='selected', ready=False, project_id=str(project_id), context_id='',
                message='프로젝트 선택 완료 · 실행 컨텍스트 적용 대기 중', nodes={},
            )
        finally:
            if changing_project:
                self._execution_context_apply_lock.release()
        policy_result = self.publish_servo_alarm_policy()
        if policy_result.get('success') is not True:
            raise ValueError(
                '선택 프로젝트의 서보 에러 정책을 적용하지 못했습니다: '
                f'{policy_result.get("message") or "응답 없음"}'
            )
        result['execution_context'] = self._reconcile_execution_context()
        return result

    def servo_alarm_policy(self) -> Dict[str, Any]:
        project_id = self.project_repository.selected_project_id()
        stored = self.project_repository.load_servo_alarm_policy(project_id)
        overrides = normalize_overrides(stored.get('overrides'))
        return self._servo_alarm_policy_payload(project_id, overrides)

    def _servo_alarm_policy_payload(
        self,
        project_id: str,
        overrides: Dict[str, int],
    ) -> Dict[str, Any]:
        catalog = catalog_payload(overrides)
        effective_grades = effective_grade_map(overrides)
        return {
            'success': True,
            'project_id': project_id,
            'project_generation': self._current_project_generation(),
            'catalog_version': SERVO_ALARM_CATALOG_VERSION,
            'grade_definitions': GRADE_DEFINITIONS,
            'overrides': overrides,
            'effective_grades': effective_grades,
            'policy_revision': policy_revision(
                effective_grades,
                SERVO_ALARM_CATALOG_VERSION,
            ),
            'counts': configured_counts(catalog),
            'catalog': catalog,
        }

    def save_servo_alarm_policy(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        project_id = self.project_repository.selected_project_id()
        if not project_id:
            raise ValueError('서보 에러 등급을 저장할 프로젝트를 먼저 선택하세요')
        self._ensure_project_change_allowed()
        overrides = normalize_overrides(payload.get('overrides'))
        previous = self.servo_alarm_policy()
        candidate = self._servo_alarm_policy_payload(project_id, overrides)
        published = self.publish_servo_alarm_policy(candidate)
        if published.get('success') is not True:
            raise ValueError(
                '서보 에러 등급을 Supervisor에 적용하지 못해 저장하지 않았습니다: '
                f'{published.get("message") or "응답 없음"}'
            )
        try:
            saved = self.project_repository.save_servo_alarm_policy(
                project_id,
                overrides,
            )
        except Exception:
            self.publish_servo_alarm_policy(previous)
            raise
        return {
            **candidate,
            'message': '현재 프로젝트의 서보 에러 등급을 저장하고 적용했습니다',
            'saved': saved,
            'supervisor_applied': True,
            'supervisor_message': published.get('message', ''),
        }

    def publish_servo_alarm_policy(
        self,
        policy: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        policy = policy or self.servo_alarm_policy()
        request_id = self._new_project_request_id('servo-alarm-policy')
        payload = {
            'request_id': request_id,
            'project_generation': self._current_project_generation(),
            'project_id': policy.get('project_id', ''),
            'command': 'servo_alarm_policy_update',
            'catalog_version': policy['catalog_version'],
            'grades': policy['effective_grades'],
            'policy_revision': policy['policy_revision'],
        }
        publisher = getattr(self, '_safety_request_publisher', None)
        if publisher is None:
            return {'success': False, 'message': '서보 에러 정책 전송 경로가 없습니다'}
        publisher.publish(
            String(data=json.dumps(payload, ensure_ascii=False, separators=(',', ':')))
        )
        result = self._wait_for_jog_result(request_id, timeout_sec=1.0)
        if not isinstance(result, dict):
            return {'success': False, 'message': 'Supervisor 정책 적용 응답이 없습니다'}
        return {
            'success': bool(result.get('success')),
            'message': str(result.get('message') or ''),
            'request_id': request_id,
        }

    def import_motion_project_file(
        self, project_id: Any, payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        self._ensure_project_mutation_allowed(project_id)
        if str(payload.get('category') or '').strip() == 'motions':
            raise ValueError(
                '외부 모션 JSON 파일 가져오기는 지원하지 않습니다. '
                '모션 스튜디오에서 실행 파일을 저장하세요'
            )
        return self.project_repository.import_text(
            project_id,
            payload.get('category'),
            payload.get('file_name'),
            payload.get('content'),
        )

    def load_motion_project_file(
        self, project_id: Any, category: Any, file_name: Any
    ) -> Dict[str, Any]:
        self._ensure_selected_project(project_id)
        return self.project_repository.read_file(project_id, category, file_name)

    def load_read_only_project_file(
        self, project_id: Any, relative_path: Any
    ) -> Dict[str, Any]:
        self._ensure_selected_project(project_id)
        return self.project_repository.read_read_only_file(project_id, relative_path)

    def download_motion_project_file(
        self, project_id: Any, category: Any, file_name: Any
    ) -> Path:
        self._ensure_selected_project(project_id)
        return self.project_repository.export_path(project_id, category, file_name)

    def save_motion_project_file(
        self, project_id: Any, category: Any, file_name: Any, payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        self._ensure_project_mutation_allowed(project_id)
        return self.project_repository.save_file(
            project_id, category, file_name, payload.get('content')
        )

    def rename_motion_project_file(
        self, project_id: Any, category: Any, file_name: Any, payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        self._ensure_project_mutation_allowed(project_id)
        return self.project_repository.rename_file(
            project_id, category, file_name, payload.get('new_name')
        )

    def delete_motion_project_file(
        self, project_id: Any, category: Any, file_name: Any
    ) -> Dict[str, Any]:
        self._ensure_project_mutation_allowed(project_id)
        result = self.project_repository.delete_file(project_id, category, file_name)
        if self.project_repository.selected_project_id() == str(project_id):
            replacement = str(result.get('replacement_active_file') or '')
            if str(category) == 'motor_axes':
                if replacement:
                    self.open_motion_project_file_for_editing(
                        project_id, category, replacement
                    )
                    self._write_motor_config_selection(self.motor_config_file)
                else:
                    self.motor_config_file = Path()
                    self._clear_motor_config_selection()
        return result

    def delete_motor_config(self) -> Dict[str, Any]:
        project_id = self.project_repository.selected_project_id()
        if not project_id:
            return {
                'success': False,
                'message': '통합 프로젝트를 먼저 선택하세요',
                'config_file': '',
                'content': '',
                'registry': self._empty_motor_registry(),
            }
        try:
            current_path = self._selected_motor_config_path()
            deleted = self.delete_motion_project_file(
                project_id, 'motor_axes', current_path.name
            )
        except (OSError, ValueError) as exc:
            return {
                'success': False,
                'message': f'모터축 설정 파일 삭제 실패: {exc}',
                'config_file': '',
                'content': '',
                'registry': self._empty_motor_registry(),
            }

        loaded = self.load_motor_config()
        replacement = str(deleted.get('replacement_active_file') or '')
        loaded.update({
            'success': True,
            'deleted_file': str(deleted.get('deleted_file') or current_path.name),
            'replacement_active_file': replacement,
            'trash_path': str(deleted.get('trash_path') or ''),
            'message': (
                f'모터축 설정 파일을 프로젝트 휴지통으로 이동하고 {replacement} 파일을 선택했습니다'
                if replacement
                else '모터축 설정 파일을 프로젝트 휴지통으로 이동했습니다'
            ),
        })
        return loaded

    def activate_motion_project_file(
        self, project_id: Any, category: Any, file_name: Any
    ) -> Dict[str, Any]:
        self._ensure_project_mutation_allowed(project_id)
        # This only selects a file in project metadata.  It does not apply a
        # motor configuration or publish a motion command.
        result = self.project_repository.set_active(project_id, category, file_name)
        if str(category) in {'motor_axes', 'motion_axis_matching', 'motions'}:
            result['editor_link'] = self.open_motion_project_file_for_editing(
                project_id, category, file_name
            )
        return result

    def _selected_project_published_names(self, category: str) -> set[str]:
        project_id = self.project_repository.selected_project_id()
        if not project_id:
            return set()
        detail = self.project_repository.get_project(project_id)
        names = set()
        for folder in detail.get('tree') or []:
            if folder.get('category') != category:
                continue
            for file_info in folder.get('children') or []:
                names.add(str(file_info.get('name') or ''))
        return names

    def open_motion_project_file_for_editing(
        self, project_id: Any, category: Any, file_name: Any
    ) -> Dict[str, Any]:
        self._ensure_project_mutation_allowed(project_id)
        path = self.project_repository.export_path(project_id, category, file_name)
        category_text = str(category)
        if category_text == 'motor_axes':
            self.motor_config_file = path
            return {
                'success': True,
                'workspace': 'config',
                'category': category_text,
                'file_name': path.name,
                'message': '모터축 설정 편집기에 연결했습니다 · 설정 적용은 실행하지 않았습니다',
            }
        if category_text in {'motion_axis_matching', 'motions'}:
            return {
                'success': True,
                'workspace': 'project',
                'category': category_text,
                'motion_tab': 'mapping' if category_text == 'motion_axis_matching' else 'files',
                'file_name': path.name,
                'path': str(path),
                'message': '현재 프로젝트 파일을 기능 탭에서 직접 사용합니다 · 모션 실행은 시작하지 않았습니다',
            }
        return {
            'success': True,
            'workspace': 'studio',
            'category': category_text,
            'file_name': path.name,
            'message': '레이어는 왼쪽 프로젝트 파일에서 관리하고 모션 스튜디오에서 합성합니다',
        }

    def _sync_project_file(
        self, result: Dict[str, Any], category: str, path: Path
    ) -> Dict[str, Any]:
        repository = getattr(self, 'project_repository', None)
        if repository is None:
            return result
        try:
            sync = repository.sync_project_file(category, path)
        except (OSError, UnicodeDecodeError, ValueError, yaml.YAMLError) as exc:
            result['project_sync_warning'] = str(exc)
            return result
        result['project_sync'] = sync
        return result

    def _motor_config_payload_from_path(
        self,
        config_file: Path,
        *,
        message: str = 'motor config YAML loaded',
    ) -> Dict[str, Any]:
        """Build the UI/API payload from one concrete motor_axes YAML path."""
        path = Path(config_file)
        if not path.is_file():
            return {
                'success': False,
                'message': 'motor config YAML not found',
                'config_file': str(path),
                'config_revision': '',
                'content': '',
                'registry': self._empty_motor_registry(),
            }
        try:
            raw = path.read_bytes()
            content = raw.decode('utf-8')
            config = yaml.safe_load(content) or {}
        except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
            return {
                'success': False,
                'message': f'failed to load motor config YAML: {exc}',
                'config_file': str(path),
                'config_revision': '',
                'content': '',
                'registry': self._empty_motor_registry(),
            }
        if not isinstance(config, dict):
            return {
                'success': False,
                'message': 'motor config YAML root must be an object',
                'config_file': str(path),
                'config_revision': '',
                'content': '',
                'registry': self._empty_motor_registry(),
            }
        axis_config = self._expand_shared_driver_profiles(config)
        if axis_config != config:
            content = yaml.safe_dump(axis_config, sort_keys=False, allow_unicode=True)
        self.motor_config_file = path
        return {
            'success': True,
            'message': message,
            'config_file': str(path),
            'config_revision': hashlib.sha256(raw).hexdigest(),
            'content': content,
            'registry': self._registry_from_motor_config(axis_config),
        }

    def load_motor_config(self) -> Dict[str, Any]:
        try:
            self.motor_config_file = self._selected_motor_config_path()
        except ValueError as exc:
            if '모터축 설정 파일이 없습니다' in str(exc):
                return {
                    'success': True,
                    'saved': False,
                    'message': '아직 저장된 모터축 설정 파일이 없습니다',
                    'config_file': '',
                    'config_revision': '',
                    'content': '',
                    'registry': self._empty_motor_registry(),
                }
            return {
                'success': False,
                'message': str(exc),
                'config_file': '',
                'config_revision': '',
                'content': '',
                'registry': self._empty_motor_registry(),
            }
        return self._motor_config_payload_from_path(self.motor_config_file)

    def save_motor_config(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        try:
            self._ensure_project_mutation_allowed(
                self.project_repository.selected_project_id()
            )
            target_file = self._motor_config_file_from_payload(payload)
            expected_revision = str(payload.get('base_revision') or '').strip()
            if target_file.is_file():
                actual_revision = hashlib.sha256(target_file.read_bytes()).hexdigest()
                if not expected_revision:
                    raise ValueError(
                        '설정 파일 버전 정보가 없습니다. 설정 다시 불러오기 후 저장하세요'
                    )
                if expected_revision != actual_revision:
                    raise ValueError(
                        '설정 파일이 화면을 불러온 뒤 변경됐습니다. '
                        '현재 파일 보호를 위해 저장을 거부했습니다. 설정 다시 불러오기를 실행하세요'
                    )
            elif expected_revision:
                raise ValueError(
                    '화면에서 불러온 설정 파일이 현재 존재하지 않습니다. '
                    '설정 다시 불러오기를 실행하세요'
                )
            if 'content' in payload:
                content = str(payload.get('content') or '')
                config = yaml.safe_load(content) or {}
                config = self._expand_shared_driver_profiles(config)
                content = yaml.safe_dump(config, sort_keys=False, allow_unicode=True)
            else:
                registry = payload.get('registry', payload)
                if not isinstance(registry, dict):
                    raise ValueError('registry must be an object')
                normalized = self._normalize_motor_registry(registry)
                normalized['updated_at'] = time.time()
                current = self._read_current_motor_config()
                config = self._motor_config_from_registry(normalized, current)
                content = yaml.safe_dump(config, sort_keys=False, allow_unicode=True)

            if not isinstance(config, dict):
                raise ValueError('motor config YAML root must be an object')
            configured_axes = len(
                self._registry_from_motor_config(
                    self._expand_shared_driver_profiles(config)
                ).get('motors') or []
            )
            if configured_axes == 0:
                raise ValueError(
                    '0축 모터 설정은 저장할 수 없습니다. '
                    '설정 파일 제거는 현재 설정 파일 휴지통으로 이동을 사용하세요'
                )

            self._write_motor_config(content, target_file)
        except (OSError, ValueError, yaml.YAMLError) as exc:
            return {
                'success': False,
                'message': f'failed to save motor config YAML: {exc}',
                'config_file': str(self.motor_config_file),
                'content': payload.get('content', ''),
                'registry': self._empty_motor_registry(),
            }

        # Persist project ownership of the written file, then return that same
        # file as the save response. Do not rebuild the response through the
        # active-file selector alone: a new project has an empty
        # active_files.motor_axes until sync finishes, and an empty registry
        # response would wipe the UI axis list (names/aliases) and leave
        # "설정 적용 및 재시작" disabled until a manual reload.
        synced = self._sync_project_file({}, 'motor_axes', target_file)
        result = self._motor_config_payload_from_path(
            target_file,
            message=(
                'motor config YAML saved; restart motor_manager_node to apply'
            ),
        )
        if not result.get('success'):
            return result
        saved_motors = (result.get('registry') or {}).get('motors') or []
        if len(saved_motors) == 0:
            return {
                'success': False,
                'message': (
                    '모터축 설정 파일은 저장됐지만 저장 응답에 축 목록이 없습니다. '
                    '설정 불러오기로 파일을 다시 확인하세요'
                ),
                'config_file': str(target_file),
                'config_revision': '',
                'content': '',
                'registry': self._empty_motor_registry(),
            }
        if 'project_sync' in synced:
            result['project_sync'] = synced['project_sync']
        if 'project_sync_warning' in synced:
            result['project_sync_warning'] = synced['project_sync_warning']
        return result

    def apply_motor_config(self) -> Dict[str, Any]:
        if not self.restart_script.is_file():
            return {
                'success': False,
                'message': f'restart script not found: {self.restart_script}',
                'restart_script': str(self.restart_script),
                **self.snapshot(),
            }

        try:
            self._ensure_project_change_allowed()
        except ValueError as exc:
            return {
                'success': False,
                'message': str(exc),
                **self.snapshot(),
            }
        lifecycle_lock = getattr(self, '_motor_lifecycle_lock', None)
        if lifecycle_lock is None:
            lifecycle_lock = threading.Lock()
            self._motor_lifecycle_lock = lifecycle_lock
        if not lifecycle_lock.acquire(blocking=False):
            return {
                'success': False,
                'message': '다른 모터 설정·검색·재시작 작업이 진행 중입니다',
                **self.snapshot(),
            }
        project_id = self.project_repository.selected_project_id()
        if not project_id:
            lifecycle_lock.release()
            return {
                'success': False,
                'message': '적용할 프로젝트를 먼저 선택하세요',
                **self.snapshot(),
            }
        operation: Dict[str, Any] = {}
        previous_runtime = self.project_repository.motor_runtime_target_snapshot()
        try:
            operation = self.project_repository.begin_motor_operation(
                'motor_apply',
                'preparing',
                timeout_sec=45.0,
                details={
                    'project_id': project_id,
                    'previous_runtime': previous_runtime,
                },
            )
            prepared = self.project_repository.prepare_runtime_motor_config(project_id)
            runtime_file = self.project_repository.mark_runtime_motor_config_applied(
                project_id
            )
            expected_axes = self._configured_axes_from_runtime_file(
                runtime_file
            )
            if not expected_axes:
                raise ValueError('적용할 모터 실행 설정에서 대상 축을 확인할 수 없습니다')
            self.project_repository.update_motor_operation(
                str(operation['operation_id']),
                'prepared',
                details={
                    'runtime_file': str(runtime_file),
                    'expected_axes': expected_axes,
                },
            )
            managed_service = str(
                os.environ.get('MOTION_CONTROL_SERVICE_UNIT') or ''
            ).strip()
            motor_service = str(
                os.environ.get('MOTION_MOTOR_SERVICE_UNIT') or ''
            ).strip()
            if managed_service and managed_service != 'motion-control.service':
                raise ValueError('허용되지 않은 자동실행 서비스 이름입니다')
            if managed_service:
                if motor_service != 'motion-motor.service':
                    raise ValueError(
                        'Motor Manager 분리 서비스가 설치되지 않았습니다. '
                        '최초 설치를 다시 실행하세요'
                    )
                self._schedule_managed_service_restart(
                    motor_service,
                    managed_service,
                )
                restart_mode = 'split_managed_services'
            else:
                environment = dict(os.environ)
                environment['MOTOR_CONFIG_FILE'] = str(runtime_file)
                environment['MOTION_WORKSPACE'] = str(self.workspace_root)
                environment['START_MOTOR_MANAGER'] = 'true'
                subprocess.Popen(
                    ['/bin/bash', str(self.restart_script)],
                    cwd=str(self.restart_script.parent.parent),
                    env=environment,
                    start_new_session=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                restart_mode = 'legacy_script'
            self.project_repository.update_motor_operation(
                str(operation['operation_id']),
                'restart_requested',
                message='새 모터 설정으로 서비스 재시작 요청 완료',
                details={'runtime_file': str(runtime_file)},
            )
        except (OSError, ValueError, yaml.YAMLError) as exc:
            operation_id = str(operation.get('operation_id') or '')
            if operation_id:
                try:
                    self.project_repository.finish_motor_operation(
                        operation_id,
                        'failure',
                        phase='failed',
                        error=str(exc),
                    )
                except ValueError:
                    pass
            self.project_repository.restore_motor_runtime_target(previous_runtime)
            return {
                'success': False,
                'message': f'프로젝트 설정을 적용할 수 없습니다: {exc}',
                'restart_script': str(self.restart_script),
                **self.snapshot(),
            }
        finally:
            lifecycle_lock.release()

        return {
            'success': True,
            'message': '프로젝트 설정 적용을 시작했습니다. 웹이 잠시 후 다시 연결됩니다',
            'restart_script': str(self.restart_script),
            'restart_mode': restart_mode,
            'runtime_config': {
                **prepared,
                'session_file': str(runtime_file),
                'session_id': self.project_repository.motor_runtime_state().get(
                    'session_id', ''
                ),
            },
            'motor_operation': self.project_repository.motor_operation_status(),
            **self.snapshot(),
        }

    def restart_managed_program(self) -> Dict[str, Any]:
        """Restart only upper-level nodes while Motor Manager keeps running."""
        self._clear_stopping_project_release_state()
        managed_service = str(
            os.environ.get('MOTION_CONTROL_SERVICE_UNIT') or ''
        ).strip()
        if managed_service != 'motion-control.service':
            self._clear_stopping_project_release_state()
            return {
                'success': False,
                'message': '자동실행 서비스가 설치되지 않았습니다. 최초 설치를 먼저 완료하세요',
                **self.snapshot(),
            }
        if os.environ.get('MOTION_MOTOR_SERVICE_UNIT') != 'motion-motor.service':
            self._clear_stopping_project_release_state()
            return {
                'success': False,
                'message': (
                    'Motor Manager 분리 서비스가 설치되지 않았습니다. '
                    '최초 설치를 다시 실행하세요'
                ),
                **self.snapshot(),
            }
        self._ensure_project_change_allowed()
        restart_services = [managed_service]
        coordination_service = str(
            os.environ.get('MOTION_COORDINATION_SERVICE_UNIT') or ''
        ).strip()
        if coordination_service == 'motion-coordination.service':
            restart_services.append(coordination_service)
        try:
            self._schedule_managed_service_restart(*restart_services)
        except OSError as exc:
            self._clear_stopping_project_release_state()
            return {
                'success': False,
                'message': f'프로그램 재시작 요청에 실패했습니다: {exc}',
                **self.snapshot(),
            }
        return {
            'success': True,
            'message': (
                '상위 프로그램 재시작을 시작했습니다. '
                'Motor Manager와 현재 서보 상태는 유지됩니다'
            ),
            'restart_mode': 'upper_service',
            **self.snapshot(),
        }

    def create_desktop_shortcut(self) -> Dict[str, Any]:
        """Install the packaged web launcher on this service user's desktop."""
        home = Path(str(os.environ.get('HOME') or Path.home())).expanduser().resolve()
        try:
            desktop_result = subprocess.run(
                ['xdg-user-dir', 'DESKTOP'],
                check=False,
                capture_output=True,
                text=True,
                timeout=5,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return {
                'success': False,
                'message': f'바탕화면 경로를 확인할 수 없습니다: {exc}',
            }
        desktop_text = desktop_result.stdout.strip()
        if desktop_result.returncode != 0 or not desktop_text:
            return {
                'success': False,
                'message': '바탕화면 경로를 확인할 수 없습니다',
            }
        desktop = Path(desktop_text).expanduser().resolve()
        if desktop == home or not desktop.is_dir():
            return {
                'success': False,
                'message': '현재 사용자에게 사용할 수 있는 바탕화면 폴더가 없습니다',
            }

        source_candidates: List[Path] = []
        try:
            source_candidates.append(
                Path(get_package_share_directory('motion_web_bridge'))
                / 'deploy'
                / 'motion-program.desktop'
            )
        except Exception:
            pass
        source_candidates.append(
            Path(getattr(self, 'workspace_root', Path.cwd()))
            / 'src'
            / 'motion_web'
            / 'web_bridge'
            / 'deploy'
            / 'motion-program.desktop'
        )
        source = next((path for path in source_candidates if path.is_file()), None)
        if source is None:
            return {
                'success': False,
                'message': '설치된 바탕화면 바로가기 원본을 찾을 수 없습니다',
            }

        destination = desktop / '모션 프로그램 열기.desktop'
        try:
            launcher_data = source.read_bytes()
            if (
                b'[Desktop Entry]' not in launcher_data
                or b'Exec=xdg-open http://localhost:8000' not in launcher_data
            ):
                raise ValueError('바탕화면 바로가기 원본 형식이 올바르지 않습니다')
            already_installed = (
                destination.is_file()
                and destination.read_bytes() == launcher_data
                and bool(destination.stat().st_mode & 0o111)
            )
            if not already_installed:
                temporary_path: Optional[Path] = None
                try:
                    with tempfile.NamedTemporaryFile(
                        dir=desktop,
                        prefix='.motion-program-',
                        suffix='.desktop',
                        delete=False,
                    ) as temporary:
                        temporary.write(launcher_data)
                        temporary_path = Path(temporary.name)
                    temporary_path.chmod(0o755)
                    os.replace(temporary_path, destination)
                    temporary_path = None
                finally:
                    if temporary_path is not None:
                        temporary_path.unlink(missing_ok=True)
            else:
                destination.chmod(destination.stat().st_mode | 0o111)
        except (OSError, ValueError) as exc:
            return {
                'success': False,
                'message': f'바탕화면 바로가기를 만들 수 없습니다: {exc}',
            }

        trusted = False
        try:
            trust_result = subprocess.run(
                ['gio', 'set', str(destination), 'metadata::trusted', 'true'],
                check=False,
                capture_output=True,
                text=True,
                timeout=5,
            )
            trusted = trust_result.returncode == 0
        except (OSError, subprocess.SubprocessError):
            trusted = False
        if trusted:
            message = (
                '바탕화면 바로가기가 이미 설치되어 있습니다'
                if already_installed
                else '바탕화면 바로가기를 만들었습니다'
            )
        else:
            message = (
                '바탕화면 바로가기를 만들었습니다. '
                '아이콘을 우클릭해 실행 허용을 선택하세요'
            )
        return {
            'success': True,
            'status': 'already_installed' if already_installed else 'created',
            'message': message,
            'path': str(destination),
            'trusted': trusted,
        }

    def restart_motor_control_system(self) -> Dict[str, Any]:
        """Restart the persistent Motor Manager only after explicit confirmation."""
        self._clear_stopping_project_release_state()
        motor_service = str(
            os.environ.get('MOTION_MOTOR_SERVICE_UNIT') or ''
        ).strip()
        if motor_service != 'motion-motor.service':
            self._clear_stopping_project_release_state()
            return {
                'success': False,
                'message': (
                    'Motor Manager 분리 서비스가 설치되지 않았습니다. '
                    '최초 설치를 다시 실행하세요'
                ),
                **self.snapshot(),
            }
        runtime_config = self.project_repository.selected_runtime_motor_config()
        if runtime_config is None:
            self._clear_stopping_project_release_state()
            return {
                'success': False,
                'message': (
                    '현재 프로젝트의 모터축 설정이 적용되지 않았습니다. '
                    '모터 관리에서 설정 적용·재시작을 먼저 실행하세요'
                ),
                **self.snapshot(),
            }
        expected_axes = self._configured_axes_from_runtime_file(runtime_config)
        if not expected_axes:
            self._clear_stopping_project_release_state()
            return {
                'success': False,
                'message': '현재 모터 실행 설정에서 대상 축을 확인할 수 없습니다',
                **self.snapshot(),
            }
        self._ensure_project_change_allowed()
        lifecycle_lock = getattr(self, '_motor_lifecycle_lock', None)
        if lifecycle_lock is None:
            lifecycle_lock = threading.Lock()
            self._motor_lifecycle_lock = lifecycle_lock
        if not lifecycle_lock.acquire(blocking=False):
            self._clear_stopping_project_release_state()
            return {
                'success': False,
                'message': '다른 모터 설정·검색·재시작 작업이 진행 중입니다',
                **self.snapshot(),
            }
        try:
            operation = self._motor_restart_lifecycle().begin(
                project_id=self.project_repository.selected_project_id(),
                runtime_file=runtime_config,
                expected_axes=expected_axes,
            )
        except (OSError, RuntimeError, ValueError) as exc:
            operation_id = str(
                locals().get('operation', {}).get('operation_id') or ''
            )
            if operation_id:
                try:
                    self.project_repository.finish_motor_operation(
                        operation_id,
                        'failure',
                        phase='failed',
                        error=str(exc),
                    )
                except ValueError:
                    pass
            return {
                'success': False,
                'message': f'모터 제어 시스템 재시작 요청에 실패했습니다: {exc}',
                **self.snapshot(),
            }
        finally:
            lifecycle_lock.release()
        return {
            'success': True,
            'message': (
                '모터 제어 시스템 재시작을 시작했습니다. '
                'AC Servo가 OFF됐다가 자동 ON될 수 있습니다'
            ),
            'restart_mode': 'motor_service',
            'motor_operation': self.project_repository.motor_operation_status(),
            **self.snapshot(),
        }

    def clear_motor_runtime_application(self) -> Dict[str, Any]:
        """Stop Motor Manager and clear runtime ownership for project deletion."""
        motor_service = str(
            os.environ.get('MOTION_MOTOR_SERVICE_UNIT') or ''
        ).strip()
        if motor_service != 'motion-motor.service':
            return {
                'success': False,
                'message': (
                    'Motor Manager 분리 서비스가 설치되지 않았습니다. '
                    '최초 설치를 다시 실행하세요'
                ),
                **self.snapshot(),
            }
        runtime_state = self.project_repository.motor_runtime_state()
        runtime_project_id = str(runtime_state.get('target_project_id') or '').strip()
        if not runtime_project_id:
            return {
                'success': True,
                'cleared': False,
                'message': '해제할 모터 실행 적용이 없습니다',
                'runtime_project_id': '',
                **self.snapshot(),
            }
        project_blocker = self._project_change_blocker(
            allow_run_stopping=True,
            allow_studio_stopping=True,
        )
        if project_blocker:
            return {
                'success': False,
                'message': project_blocker,
                **self.snapshot(),
            }
        execution_blocker = self._coordination_execution_blocker()
        if execution_blocker:
            return {
                'success': False,
                'message': f'실행 적용 해제 미실행: {execution_blocker}',
                **self.snapshot(),
            }
        moving_blocker = self._ethercat_scan_safety_blocker(
            require_fresh_motor_state=self._managed_user_service_active(motor_service),
            allow_run_stopping=True,
            allow_studio_stopping=True,
        )
        if moving_blocker:
            return {
                'success': False,
                'message': (
                    f'실행 적용 해제 미실행: {moving_blocker}. '
                    '먼저 「전체 동작 정지」를 실행하세요'
                ),
                **self.snapshot(),
            }
        lifecycle_lock = getattr(self, '_motor_lifecycle_lock', None)
        if lifecycle_lock is None:
            lifecycle_lock = threading.Lock()
            self._motor_lifecycle_lock = lifecycle_lock
        if not lifecycle_lock.acquire(blocking=False):
            return {
                'success': False,
                'message': '다른 모터 설정·검색·재시작 작업이 진행 중입니다',
                **self.snapshot(),
            }
        operation: Dict[str, Any] = {}
        cleared: Dict[str, Any] = {}
        try:
            operation = self.project_repository.begin_motor_operation(
                'motor_runtime_clear',
                'preparing',
                timeout_sec=30.0,
                details={'previous_project_id': runtime_project_id},
            )
            try:
                self.motion_run_stop()
            except Exception:
                pass
            try:
                self.publish_safety_stop(False)
            except Exception:
                pass
            if self._managed_user_service_active(motor_service):
                self.project_repository.update_motor_operation(
                    str(operation['operation_id']),
                    'stopping_runtime',
                    message='Motor Manager 정지 및 EtherCAT 소유권 해제 중',
                )
                self._run_managed_user_service('stop', motor_service)
                try:
                    self._wait_for_ethercat_release(8.0)
                except Exception:
                    pass
            cleared = self.project_repository.clear_motor_runtime_target()
            self._clear_motor_config_selection()
            self._clear_stopping_project_release_state()
            # Drop launch-time ownership so delete / runtime_project_id update
            # without waiting for a Bridge restart.
            self.applied_motor_config_file = Path()
            self.project_repository.finish_motor_operation(
                str(operation['operation_id']),
                'success',
                phase='completed',
                message=str(cleared.get('message') or '모터 실행 적용 해제 완료'),
                details={
                    'previous_project_id': cleared.get('previous_project_id') or '',
                    'motor_service_stopped': True,
                },
            )
        except (OSError, RuntimeError, ValueError) as exc:
            operation_id = str(operation.get('operation_id') or '')
            if operation_id:
                try:
                    self.project_repository.finish_motor_operation(
                        operation_id,
                        'failure',
                        phase='failed',
                        error=str(exc),
                    )
                except ValueError:
                    pass
            return {
                'success': False,
                'message': f'실행 적용 해제 실패: {exc}',
                **self.snapshot(),
            }
        finally:
            lifecycle_lock.release()
        return {
            'success': True,
            'cleared': bool(cleared.get('cleared')),
            'previous_project_id': cleared.get('previous_project_id') or '',
            'message': (
                f"{cleared.get('message') or '모터 실행 적용을 해제했습니다'}. "
                'Motor Manager는 정지 상태입니다. '
                '다시 사용하려면 프로젝트에서 「설정 적용 및 재시작」을 실행하세요'
            ),
            'runtime_project_id': '',
            'motor_operation': self.project_repository.motor_operation_status(),
            **self.list_motion_projects(),
            **self.snapshot(),
        }

    @staticmethod
    def _schedule_managed_service_restart(*managed_services: str) -> None:
        """Return the HTTP response before stopping the process serving it.

        Starting systemctl immediately races the API response against
        Uvicorn shutdown.  A detached, fixed-command shell gives the response
        a short window to leave the socket, then asks systemd to restart the
        validated service.  Positional arguments keep the service name out of
        shell parsing.
        """
        allowed_services = {
            'motion-control.service',
            'motion-motor.service',
            'motion-coordination.service',
        }
        if (
            not managed_services
            or any(service not in allowed_services for service in managed_services)
        ):
            raise ValueError('허용되지 않은 자동실행 서비스 이름입니다')
        subprocess.Popen(
            [
                '/bin/bash',
                '-c',
                'sleep 0.5; exec "$@"',
                'motion-control-delayed-restart',
                '/usr/bin/systemctl',
                '--user',
                'restart',
                '--no-block',
                *managed_services,
            ],
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def list_motion_mappings(self) -> Dict[str, Any]:
        result = self._request_motion_mapping('list', {})
        if not self.project_repository.selected_project_id():
            result['message'] = '통합 프로젝트를 먼저 선택하세요'
        else:
            result['message'] = '현재 프로젝트 모션축 설정을 불러왔습니다'
        return result

    def load_motion_mapping(self, file_id: Any) -> Dict[str, Any]:
        result = self._request_motion_mapping('load', {'file_id': file_id})
        if result.get('success') is False:
            return result

        loaded_file_id = self._motion_mapping_file_id(result) or str(file_id or '').strip()
        if loaded_file_id:
            midi_result = self._load_and_apply_midi_banks(loaded_file_id)
            result['midi_banks'] = midi_result
            if midi_result.get('success') is False:
                result['midi_banks_warning'] = str(midi_result.get('message') or '')
        return result

    def save_motion_mapping(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        blocker = self._project_change_blocker()
        if blocker:
            return {'success': False, 'message': blocker, 'files': []}
        result = self._request_motion_mapping('save', payload)
        if result.get('success') is False:
            return result

        saved_file_id = self._motion_mapping_file_id(result)
        if saved_file_id and getattr(self, 'project_repository', None) is not None:
            project_id = self.project_repository.selected_project_id()
            result = self._sync_project_file(
                result,
                'motion_axis_matching',
                self.project_repository.export_path(
                    project_id, 'motion_axis_matching', saved_file_id
                ),
            )
            # The active mapping file is one immutable part of the project
            # execution context. Applying only its MIDI-bank subsection leaves
            # the MIDI axis registry and every other consumer on the previous
            # file hash. Reconcile the complete context after the repository
            # has confirmed the saved file and active-file selection.
            execution_context = self._reconcile_execution_context()
            result['execution_context'] = execution_context
            result['runtime_applied'] = bool(execution_context.get('ready'))
            if result['runtime_applied']:
                result['message'] = (
                    '모션축 설정 저장 완료 · 변경된 모션 범위를 MIDI에 적용했습니다'
                )
            else:
                runtime_message = str(
                    execution_context.get('message')
                    or '실행 컨텍스트 적용 대기'
                )
                result['runtime_apply_warning'] = runtime_message
                result['message'] = (
                    '모션축 설정은 저장됐지만 MIDI 적용 대기 중입니다: '
                    f'{runtime_message}'
                )
        return result

    def _load_and_apply_midi_banks(self, file_id: str) -> Dict[str, Any]:
        loaded = self._request_motion_mapping(
            'load_midi_banks',
            {'file_id': file_id},
            timeout_sec=3.0,
        )
        if loaded.get('success') is False:
            return loaded
        state = loaded.get('midi_banks')
        applied = self._request_midi_monitor(
            'apply_banks',
            {'mapping_file_id': file_id, 'midi_banks': state},
            timeout_sec=3.0,
        )
        if applied.get('success') is False:
            return applied
        applied['file'] = loaded.get('file')
        applied['message'] = (
            '모션축 설정 파일의 MIDI 뱅크 적용 완료 · SELECT 전체 해제 · 페이더 0 이동'
            if applied.get('select_reset') else
            '모션축 설정 파일의 MIDI 필터 적용 완료 · SELECT 상태 유지'
        )
        return applied

    @staticmethod
    def _motion_mapping_file_id(result: Dict[str, Any]) -> str:
        file_info = result.get('file')
        if not isinstance(file_info, dict):
            return ''
        return str(file_info.get('id') or file_info.get('filename') or '').strip()

    def validate_motion_mapping(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self._request_motion_mapping('validate', payload)

    def delete_motion_mapping(self, file_id: Any) -> Dict[str, Any]:
        project_id = self.project_repository.selected_project_id()
        if not project_id:
            return {'success': False, 'message': '통합 프로젝트를 먼저 선택하세요', 'files': []}
        blocker = self._project_change_blocker()
        if blocker:
            return {'success': False, 'message': blocker, 'files': []}
        try:
            deleted = self.project_repository.delete_file(
                project_id, 'motion_axis_matching', file_id
            )
        except (OSError, ValueError) as exc:
            return {'success': False, 'message': str(exc), 'files': []}
        result = self.list_motion_mappings()
        result['message'] = '모션축 설정 파일을 프로젝트 휴지통으로 이동했습니다'
        result['project'] = deleted.get('project')
        return result

    def _request_motion_mapping(
        self,
        command: str,
        payload: Dict[str, Any],
        timeout_sec: float = 2.0,
    ) -> Dict[str, Any]:
        request_id = self._new_project_request_id('mapping')
        project_generation = self._current_project_generation()
        msg = String()
        request_payload = dict(payload) if isinstance(payload, dict) else {}
        request_payload['project_id'] = self.project_repository.selected_project_id()
        request_payload['project_generation'] = project_generation
        msg.data = json.dumps({
            'request_id': request_id,
            'project_generation': project_generation,
            'command': command,
            'payload': request_payload,
        }, ensure_ascii=False)
        self._motion_mapping_request_publisher.publish(msg)
        result = self._wait_for_motion_mapping_result(request_id, timeout_sec=timeout_sec)
        if result is None:
            return {
                'success': False,
                'message': 'motion_mapping_manager response timeout',
                'files': [],
                'mapping': None,
                'content': '',
            }
        result.pop('_received_at', None)
        return result

    def motion_run_status(self) -> Dict[str, Any]:
        result = self._request_motion_run('status', {}, timeout_sec=1.0)
        if result.get('success') is False and result.get('message') == 'motion_run_manager response timeout':
            with self._motion_run_lock:
                status = dict(self._motion_run_status) if self._motion_run_status else {}
            if status:
                return {
                    'success': True,
                    'message': 'motion run status from cache',
                    'status': status,
                }
        return result

    def motion_run_check(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self._request_motion_run('check', payload, timeout_sec=3.0)

    def _coordination_execution_blocker(self) -> str:
        service = getattr(self, '_coordination_web_bridge', None)
        return service.local_execution_blocker() if service is not None else ''

    def motion_run_initialize(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        if str(payload.get('request_source') or '') != 'network_control':
            conflict = self._coordination_execution_blocker()
            if conflict:
                return {'success': False, 'message': f'초기 위치 이동 불가: {conflict}'}
        blocker = self._motor_runtime_control_blocker()
        if blocker:
            return {'success': False, 'message': f'초기 위치 이동 불가: {blocker}'}
        return self._request_motion_run('initialize', payload, timeout_sec=2.0)

    def motion_run_start(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        if str(payload.get('request_source') or '') != 'network_control':
            conflict = self._coordination_execution_blocker()
            if conflict:
                return {'success': False, 'message': f'모션 실행 불가: {conflict}'}
        blocker = self._motor_runtime_control_blocker()
        if blocker:
            return {'success': False, 'message': f'모션 실행 불가: {blocker}'}
        return self._request_motion_run('start', payload, timeout_sec=2.0)

    def motion_automation_configure(
        self, payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        return self._request_motion_run(
            'automation_configure', payload, timeout_sec=2.0
        )

    def motion_automation_start(
        self, payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        conflict = self._coordination_execution_blocker()
        if conflict:
            return {'success': False, 'message': f'자동 반복 시작 불가: {conflict}'}
        blocker = self._motor_runtime_control_blocker()
        if blocker:
            return {'success': False, 'message': f'자동 반복 시작 불가: {blocker}'}
        return self._request_motion_run(
            'automation_start', payload, timeout_sec=2.0
        )

    def motion_automation_reserve(
        self, payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        conflict = self._coordination_execution_blocker()
        if conflict:
            return {'success': False, 'message': f'자동 반복 예약 불가: {conflict}'}
        return self._request_motion_run(
            'automation_reserve', payload, timeout_sec=2.0
        )

    def motion_automation_disable(self) -> Dict[str, Any]:
        return self._request_motion_run(
            'automation_disable', {}, timeout_sec=2.0
        )

    def motion_run_stop(self) -> Dict[str, Any]:
        return self._request_motion_run('stop', {}, timeout_sec=2.0)

    def motion_run_stop_after_cycle(self) -> Dict[str, Any]:
        return self._request_motion_run('stop_after_cycle', {}, timeout_sec=2.0)

    def motion_group_prepare(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        blocker = self._motor_runtime_control_blocker()
        if blocker:
            return {'success': False, 'message': f'그룹 실행 준비 불가: {blocker}'}
        return self._request_motion_run('group_prepare', payload, timeout_sec=2.0)

    def motion_group_start_at(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self._request_motion_run('group_start_at', payload, timeout_sec=2.0)

    def motion_group_initialize_at(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self._request_motion_run('group_initialize_at', payload, timeout_sec=2.0)

    def motion_group_cancel(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self._request_motion_run('group_cancel', payload, timeout_sec=2.0)

    def midi_monitor_status(self) -> Dict[str, Any]:
        result = self._request_midi_monitor('status', {}, timeout_sec=1.0)
        if result.get('success') is False:
            with self._midi_monitor_lock:
                cached = dict(self._midi_monitor_status) if self._midi_monitor_status else {}
            cached.pop('_bridge_received_at', None)
            if cached:
                result = {
                    **cached,
                    'success': False,
                    'node_state': 'stale',
                    'connected': False,
                    'motor_output_enabled': False,
                    'message': 'MIDI 모니터 노드 응답 없음 · 이전 상태는 제어에 사용하지 않습니다',
                }
        return self._safety_adjusted_midi_status(result)

    def _safety_adjusted_midi_status(
        self,
        status: Dict[str, Any],
        *,
        safety_status: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Expose the supervisor's final-output latch in every MIDI status API."""
        result = dict(status) if isinstance(status, dict) else {}
        if safety_status is None:
            with self._safety_status_lock:
                safety_status = dict(self._safety_status) if self._safety_status else {}
        blocked = bool(safety_status.get('commands_blocked'))
        result['motor_output_blocked_by_safety'] = blocked
        result['motor_output_block_reason'] = (
            str(safety_status.get('message') or '안전 정지로 모터 출력이 차단되었습니다')
            if blocked else ''
        )
        if blocked:
            result['motor_output_enabled'] = False
        return result

    def save_midi_monitor_mapping(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        updated = self._request_midi_monitor('update_bank', payload, timeout_sec=2.0)
        return self._persist_midi_bank_result(updated)

    def create_midi_bank(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        created = self._request_midi_monitor('create_bank', payload, timeout_sec=2.0)
        return self._persist_midi_bank_result(created)

    def select_midi_bank(self, bank_id: str) -> Dict[str, Any]:
        selected = self._request_midi_monitor(
            'select_bank',
            {'bank_id': bank_id},
            timeout_sec=2.0,
        )
        return self._persist_midi_bank_result(selected)

    def update_midi_bank(self, bank_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        updated = self._request_midi_monitor(
            'update_bank',
            {**payload, 'bank_id': bank_id},
            timeout_sec=2.0,
        )
        return self._persist_midi_bank_result(updated)

    def delete_midi_bank(self, bank_id: str) -> Dict[str, Any]:
        deleted = self._request_midi_monitor(
            'delete_bank',
            {'bank_id': bank_id},
            timeout_sec=2.0,
        )
        return self._persist_midi_bank_result(deleted)

    def save_midi_banks_to_file(self) -> Dict[str, Any]:
        return self._persist_midi_bank_result(self.midi_monitor_status())

    def load_midi_banks_from_file(self) -> Dict[str, Any]:
        status = self.midi_monitor_status()
        if status.get('success') is False:
            return status
        file_id = self._midi_mapping_file_id(status)
        if not file_id:
            return {'success': False, 'message': '선택된 모션축 설정 파일이 없습니다'}
        return self._load_and_apply_midi_banks(file_id)

    def reset_midi_runtime_values(self) -> Dict[str, Any]:
        return self._request_midi_monitor('reset_runtime_values', {}, timeout_sec=2.0)

    def connect_midi_device(self) -> Dict[str, Any]:
        return self._request_midi_monitor('connect_device', {}, timeout_sec=2.0)

    def disconnect_midi_device(self) -> Dict[str, Any]:
        return self._request_midi_monitor('disconnect_device', {}, timeout_sec=2.0)

    @staticmethod
    def _midi_mapping_file_id(result: Dict[str, Any]) -> str:
        file_id = str(result.get('motion_mapping_file_id') or '').strip()
        if file_id:
            return Path(file_id).name
        file_path = str(result.get('bank_config_file') or '').strip()
        return Path(file_path).name if file_path else ''

    def _persist_midi_bank_result(self, updated: Dict[str, Any]) -> Dict[str, Any]:
        if updated.get('success') is False:
            return updated
        file_id = self._midi_mapping_file_id(updated)
        state = updated.get('bank_state')
        if not file_id:
            return {'success': False, 'message': '선택된 모션축 설정 파일이 없습니다'}
        if not isinstance(state, dict):
            return {'success': False, 'message': 'MIDI 노드의 뱅크 설정 응답이 올바르지 않습니다'}

        saved = self._request_motion_mapping(
            'save_midi_banks',
            {'file_id': file_id, 'midi_banks': state},
            timeout_sec=3.0,
        )
        if saved.get('success') is False:
            # Restore the file-owned state so a failed write never leaves an
            # unlabelled memory-only configuration active.
            rollback = self._load_and_apply_midi_banks(file_id)
            return {
                'success': False,
                'message': f"MIDI 뱅크 파일 저장 실패: {saved.get('message') or 'unknown error'}",
                'rollback_success': rollback.get('success') is not False,
            }

        applied = self._request_midi_monitor(
            'apply_banks',
            {'mapping_file_id': file_id, 'midi_banks': saved.get('midi_banks')},
            timeout_sec=3.0,
        )
        if applied.get('success') is False:
            return {
                **applied,
                'message': '파일 저장은 완료됐지만 MIDI 노드 적용에 실패했습니다: '
                f"{applied.get('message') or 'unknown error'}",
                'file_saved': True,
            }
        applied['backup_file'] = saved.get('backup_file')
        applied['file'] = saved.get('file')
        applied['message'] = (
            'MIDI 뱅크 저장·적용 완료 · SELECT 전체 해제 · 페이더 0 이동'
            if applied.get('select_reset') else
            'MIDI 필터 저장·적용 완료 · SELECT 상태 유지'
        )
        return applied

    def _request_midi_monitor(
        self,
        command: str,
        payload: Dict[str, Any],
        timeout_sec: float = 2.0,
    ) -> Dict[str, Any]:
        request_id = self._new_project_request_id('midi')
        project_generation = self._current_project_generation()
        msg = String()
        request_payload = dict(payload) if isinstance(payload, dict) else {}
        request_payload['project_id'] = self.project_repository.selected_project_id()
        request_payload['project_generation'] = project_generation
        msg.data = json.dumps({
            'request_id': request_id,
            'project_generation': project_generation,
            'command': command,
            'payload': request_payload,
        }, ensure_ascii=False)
        self._midi_monitor_request_publisher.publish(msg)
        result = self._wait_for_midi_monitor_result(request_id, timeout_sec=timeout_sec)
        if result is None:
            return {
                'success': False,
                'connected': False,
                'message': 'MIDI 모니터 노드 응답 없음',
                'motor_output_enabled': False,
                'channels': [],
            }
        result.pop('_bridge_received_at', None)
        return result

    def _request_motion_run(
        self,
        command: str,
        payload: Dict[str, Any],
        timeout_sec: float = 2.0,
    ) -> Dict[str, Any]:
        request_id = self._new_project_request_id('run')
        project_generation = self._current_project_generation()
        msg = String()
        request_payload = dict(payload) if isinstance(payload, dict) else {}
        request_payload['project_id'] = self.project_repository.selected_project_id()
        request_payload['project_generation'] = project_generation
        if command in {
            'check',
            'initialize',
            'start',
            'group_prepare',
            'group_start_at',
            'group_initialize_at',
            'automation_configure',
            'automation_start',
            'automation_disable',
        }:
            request_payload['context_id'] = self._execution_context_id()
        msg.data = json.dumps({
            'request_id': request_id,
            'project_generation': project_generation,
            'command': command,
            'payload': request_payload,
        }, ensure_ascii=False)
        self._motion_run_request_publisher.publish(msg)
        result = self._wait_for_motion_run_result(request_id, timeout_sec=timeout_sec)
        if result is None:
            return {
                'success': False,
                'message': 'motion_run_manager response timeout',
                'status': {},
            }
        result.pop('_received_at', None)
        return result

    def _motion_studio_transport(self) -> MotionStudioRosBridge:
        service = getattr(self, '_motion_studio_ros_bridge', None)
        if service is None:
            service = MotionStudioRosBridge(self)
            self._motion_studio_ros_bridge = service
        return service

    def request_motion_studio(
        self,
        command: str,
        payload: Optional[Dict[str, Any]] = None,
        timeout_sec: float = 4.0,
        start_generation: Optional[int] = None,
    ) -> Dict[str, Any]:
        return self._motion_studio_transport().request(
            command, payload, timeout_sec, start_generation
        )

    def request_motion_studio_editor(
        self,
        command: str,
        payload: Optional[Dict[str, Any]] = None,
        timeout_sec: float = 8.0,
    ) -> Dict[str, Any]:
        return self._motion_studio_transport().request_editor(
            command, payload, timeout_sec
        )

    def _motion_studio_sync(self) -> MotionStudioSync:
        service = getattr(self, '_motion_studio_sync_service', None)
        if service is None:
            service = MotionStudioSync(self)
            self._motion_studio_sync_service = service
        return service

    def sync_motion_studio_result(
        self, result: Dict[str, Any]
    ) -> Dict[str, Any]:
        return self._motion_studio_sync().sync_result(result)

    def export_motion_studio(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self._motion_studio_sync().export(payload)

    def prepare_unified_motion_studio(self) -> Dict[str, Any]:
        return self._motion_studio_sync().prepare()

    def request_prepared_motion_studio(
        self, command: str, payload: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        return self._motion_studio_sync().request_prepared(command, payload)

    def cancel_pending_motion_studio_start(self) -> int:
        return self._motion_studio_transport().cancel_pending_start()

    def _motion_studio_start_order_lock(self) -> threading.Lock:
        return self._motion_studio_transport().start_order_lock()

    def import_motion_studio_layer(
        self, payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        return self._motion_studio_sync().import_layer(payload)

    def list_motion_files(self) -> Dict[str, Any]:
        project_id = self.project_repository.selected_project_id()
        if not project_id:
            return {
                'success': True,
                'message': '통합 프로젝트를 먼저 선택하세요',
                'project_dir': '',
                'files_dir': '',
                'files': [],
            }
        files_dir = self.motion_projects_dir / project_id / 'motions'
        files_dir.mkdir(parents=True, exist_ok=True)
        files = []
        for path in sorted(
            (
                item for item in files_dir.iterdir()
                if (
                    item.is_file()
                    and item.suffix.lower() == '.json'
                    and not item.name.startswith('__studio_')
                )
            ),
            key=lambda item: item.stat().st_mtime if item.exists() else 0.0,
            reverse=True,
        ):
            files.append(self._motion_file_entry(path, include_detail=False))
        return {
            'success': True,
            'message': (
                '현재 프로젝트 모션 파일을 불러왔습니다'
                if self.project_repository.selected_project_id()
                else '통합 프로젝트를 먼저 선택하세요'
            ),
            'project_dir': str(self.motion_projects_dir / project_id),
            'files_dir': str(files_dir),
            'files': files,
        }

    def load_motion_file(self, file_id: Any) -> Dict[str, Any]:
        try:
            path = self._motion_file_path(file_id, self._selected_motion_files_dir())
        except ValueError as exc:
            return {
                **self.list_motion_files(),
                'success': False,
                'message': str(exc),
            }
        return {
            **self.list_motion_files(),
            'success': True,
            'message': 'motion file loaded',
            'file': self._motion_file_entry(path, include_detail=True),
        }

    def _motion_file_registration_refs(
        self, project_id: str, motion_file_id: str
    ) -> List[str]:
        """Return project-local mapping files that register one motion file."""
        detail = self.project_repository.get_project(project_id)
        references: List[str] = []
        for folder in detail.get('tree') or []:
            if folder.get('category') != 'motion_axis_matching':
                continue
            for file_info in folder.get('children') or []:
                mapping_name = str(file_info.get('name') or '').strip()
                if not mapping_name:
                    continue
                try:
                    loaded = self.project_repository.read_file(
                        project_id, 'motion_axis_matching', mapping_name
                    )
                    mapping = yaml.safe_load(loaded.get('content') or '') or {}
                except (OSError, ValueError, yaml.YAMLError) as exc:
                    raise ValueError(
                        f'모션축 설정 {mapping_name}의 재생 등록 상태를 확인할 수 없습니다: {exc}'
                    ) from exc
                if not isinstance(mapping, dict):
                    raise ValueError(
                        f'모션축 설정 {mapping_name}의 재생 등록 상태를 확인할 수 없습니다'
                    )
                registered_id = str(mapping.get('motion_file_id') or '').strip()
                if registered_id == motion_file_id:
                    references.append(mapping_name)
        return references

    def delete_motion_file(self, file_id: Any) -> Dict[str, Any]:
        try:
            project_id = self.project_repository.selected_project_id()
            if not project_id:
                raise ValueError('통합 프로젝트를 먼저 선택하세요')
            self._ensure_project_mutation_allowed(project_id)
            target = self._motion_file_path(
                file_id, self.motion_projects_dir / project_id / 'motions'
            )
            registration_refs = self._motion_file_registration_refs(
                project_id, target.name
            )
            if registration_refs:
                return {
                    **self.list_motion_files(),
                    'success': False,
                    'deletion_blocked': 'registered_motion_file',
                    'registered_mapping_files': registration_refs,
                    'message': (
                        '재생 등록된 모션 파일은 삭제할 수 없습니다. '
                        '재생 등록을 해제한 뒤 다시 삭제하세요. '
                        f"모션축 설정: {', '.join(registration_refs)}"
                    ),
                }
            result = self.project_repository.delete_file(
                project_id, 'motions', target.name
            )
        except (OSError, ValueError) as exc:
            return {
                **self.list_motion_files(),
                'success': False,
                'message': f'failed to delete motion file: {exc}',
            }
        return {
            **self.list_motion_files(),
            'success': True,
            'message': 'motion file deleted',
            'project': result.get('project'),
        }

    def _motion_file_entry(self, path: Path, *, include_detail: bool) -> Dict[str, Any]:
        stat = path.stat()
        entry: Dict[str, Any] = {
            'id': path.name,
            'filename': path.name,
            'path': str(path),
            'size_bytes': stat.st_size,
            'updated_at': stat.st_mtime,
        }
        try:
            content = path.read_text(encoding='utf-8')
            analysis = self._analyze_motion_json(content, include_records=include_detail)
        except OSError as exc:
            analysis = {
                'json_valid': False,
                'valid': False,
                'message': f'failed to read file: {exc}',
                'errors': [str(exc)],
                'warnings': [],
            }
            content = ''
        entry['analysis'] = analysis
        if include_detail:
            entry['content'] = content
            entry['content_preview'] = content[:12000]
        return entry

    def _selected_motion_files_dir(self) -> Path:
        project_id = self.project_repository.selected_project_id()
        if not project_id:
            raise ValueError('통합 프로젝트를 먼저 선택하세요')
        return self.motion_projects_dir / project_id / 'motions'

    def _motion_file_path(self, file_id: Any, directory: Path) -> Path:
        name = str(file_id or '').strip()
        if not name:
            raise ValueError('file_id is required')
        if name != Path(name).name or '/' in name or '\\' in name:
            raise ValueError('invalid motion file id')
        path = directory / name
        if not path.is_file():
            raise ValueError(f'motion file not found: {name}')
        return path

    def _analyze_motion_json(self, content: str, *, include_records: bool) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            'json_valid': False,
            'format_valid': False,
            'valid': False,
            'message': 'not analyzed',
            'headers': [],
            'total_records': 0,
            'valid_records': 0,
            'motion_id_count': 0,
            'motion_ids': [],
            'time': {},
            'frame': {},
            'interpolation': {
                'period_sec': MOTION_DATA_PERIOD_SEC,
                'required': False,
            },
            'errors': [],
            'warnings': [],
        }
        try:
            payload = json.loads(content)
        except json.JSONDecodeError as exc:
            rows, headers, source, text_error = self._extract_motion_rows_from_text(content)
            if not rows:
                result['message'] = f'invalid JSON: {exc}'
                result['errors'].append(str(exc))
                if text_error:
                    result['errors'].append(text_error)
                return result
            result['message'] = 'motion data parsed from header/list rows'
            result['warnings'].append('strict JSON 형식은 아니지만 헤더+대괄호 행 형식으로 해석했습니다')
        else:
            result['json_valid'] = True
            rows, headers, source = self._extract_motion_rows(payload)
        result['format_valid'] = True
        result['headers'] = headers
        result['source'] = source
        result['total_records'] = len(rows)
        if not rows:
            result['message'] = 'motion data rows not found'
            result['errors'].append('motion data rows not found')
            return result

        parsed_records = []
        errors = result['errors']
        for index, row in enumerate(rows):
            parsed, error = self._parse_motion_row(row, headers)
            if error:
                if len(errors) < 50:
                    errors.append(f'row {index + 1}: {error}')
                continue
            parsed['row_index'] = index
            parsed_records.append(parsed)

        result['valid_records'] = len(parsed_records)
        if not parsed_records:
            result['message'] = 'valid motion records not found'
            if not errors:
                errors.append('valid motion records not found')
            return result

        times_in_input = [record['time_sec'] for record in parsed_records]
        for previous, current in zip(times_in_input, times_in_input[1:]):
            if current + 1e-9 < previous:
                result['warnings'].append('time values are not monotonic in file order')
                break

        duplicate_pairs = set()
        duplicated_count = 0
        for record in parsed_records:
            key = (round(record['time_sec'], 9), record['motion_id'])
            if key in duplicate_pairs:
                duplicated_count += 1
            duplicate_pairs.add(key)
        if duplicated_count:
            result['warnings'].append(f'duplicate time/motion_id records: {duplicated_count}')

        sorted_records = sorted(
            parsed_records,
            key=lambda item: (item['time_sec'], str(item['motion_id']), item['row_index']),
        )
        time_values = [record['time_sec'] for record in sorted_records]
        frame_values = [record['frame'] for record in sorted_records]
        min_time = min(time_values)
        max_time = max(time_values)
        result['time'] = {
            'start_sec': min_time,
            'end_sec': max_time,
            'duration_sec': max_time - min_time,
            'unique_count': len(set(round(value, 9) for value in time_values)),
        }
        result['frame'] = {
            'min': min(frame_values),
            'max': max(frame_values),
            'unique_count': len(set(frame_values)),
        }

        groups: Dict[str, List[Dict[str, Any]]] = {}
        for record in sorted_records:
            groups.setdefault(str(record['motion_id']), []).append(record)

        motion_ids = []
        interpolation_required = False
        for motion_id in sorted(groups, key=self._motion_id_sort_key):
            records = groups[motion_id]
            values = [record['value'] for record in records]
            group_times = [record['time_sec'] for record in records]
            diffs = [
                group_times[index] - group_times[index - 1]
                for index in range(1, len(group_times))
            ]
            off_period = [
                diff for diff in diffs
                if abs(diff - MOTION_DATA_PERIOD_SEC) > 0.001
            ]
            if off_period:
                interpolation_required = True
            motion_ids.append({
                'motion_id': motion_id,
                'count': len(records),
                'first_value': records[0]['value'],
                'last_value': records[-1]['value'],
                'min_value': min(values),
                'max_value': max(values),
                'first_time_sec': min(group_times),
                'last_time_sec': max(group_times),
                'period_sec_min': min(diffs) if diffs else None,
                'period_sec_max': max(diffs) if diffs else None,
                'requires_interpolation': bool(off_period),
            })

        unique_times = sorted(set(round(value, 9) for value in time_values))
        if unique_times:
            for value in unique_times:
                offset = (value - min_time) / MOTION_DATA_PERIOD_SEC
                if abs(offset - round(offset)) > 0.001:
                    interpolation_required = True
                    break

        sample_count = 1
        duration = max_time - min_time
        if duration > 0.0:
            sample_count = int(math.floor(duration / MOTION_DATA_PERIOD_SEC)) + 1
            last_sample_time = min_time + ((sample_count - 1) * MOTION_DATA_PERIOD_SEC)
            if max_time - last_sample_time > 0.001:
                sample_count += 1

        result['motion_ids'] = motion_ids
        result['motion_id_count'] = len(motion_ids)
        result['interpolation'] = {
            'period_sec': MOTION_DATA_PERIOD_SEC,
            'required': interpolation_required,
            'sample_count': sample_count,
            'estimated_record_count': sample_count * len(motion_ids),
            'method': 'linear',
        }
        if include_records:
            result['preview_records'] = sorted_records[:80]
            result['graph_series'] = self._motion_graph_series(groups)

        result['valid'] = len(errors) == 0
        result['message'] = 'motion data valid' if result['valid'] else 'motion data has errors'
        return result

    def _extract_motion_rows(self, payload: Any) -> tuple[List[Any], List[str], str]:
        if isinstance(payload, list):
            if payload and isinstance(payload[0], list):
                possible_header = [str(item) for item in payload[0]]
                if self._header_has_required_columns(possible_header):
                    return self._expand_motion_pair_rows(payload[1:], possible_header), possible_header, 'array_with_header'
            return payload, [], 'array'

        if isinstance(payload, dict):
            headers = payload.get('header', payload.get('headers', payload.get('columns', [])))
            if not isinstance(headers, list):
                headers = []
            headers = [str(item) for item in headers]
            for key in ('data', 'rows', 'records', 'motion_data', 'motions', 'frames', 'values'):
                value = payload.get(key)
                if isinstance(value, list):
                    if value and isinstance(value[0], list):
                        possible_header = [str(item) for item in value[0]]
                        if self._header_has_required_columns(possible_header):
                            return self._expand_motion_pair_rows(value[1:], possible_header), possible_header, f'{key}_with_header'
                    return self._expand_motion_pair_rows(value, headers), headers, key
            if all(
                self._motion_column_value(payload, name) is not None
                for name in ('frame', 'time', 'motion_id', 'value')
            ):
                return [payload], headers, 'object'
        return [], [], 'unknown'

    def _extract_motion_rows_from_text(
        self,
        content: str,
    ) -> tuple[List[Any], List[str], str, str]:
        lines = [
            line.strip()
            for line in content.splitlines()
            if line.strip() and not line.strip().startswith('#')
        ]
        if len(lines) < 2:
            return [], [], 'text', 'text format requires a header line and at least one data row'

        headers = self._parse_motion_header_line(lines[0])
        if not self._header_has_required_columns(headers):
            return [], [], 'text', 'required header not found: frame, time(sec), motion Id, value'

        rows = []
        skipped = 0
        for line in lines[1:]:
            row = self._parse_motion_text_row(line)
            if row is None:
                skipped += 1
                continue
            rows.append(row)

        if not rows:
            return [], headers, 'text_header_list_rows', 'bracket data rows not found'
        rows = self._expand_motion_pair_rows(rows, headers)
        if skipped:
            return rows, headers, 'text_header_list_rows', f'skipped non-data lines: {skipped}'
        return rows, headers, 'text_header_list_rows', ''

    def _parse_motion_header_line(self, line: str) -> List[str]:
        text = line.strip().strip('\ufeff').rstrip(',').strip()
        if text.startswith('{') and text.endswith('}'):
            header_obj = self._parse_motion_header_object(text)
            fields = header_obj.get('fields') if isinstance(header_obj, dict) else None
            if isinstance(fields, list):
                return [str(item).strip() for item in fields]
        if text.startswith('[') and text.endswith(']'):
            parsed = self._parse_motion_text_row(text)
            if parsed is not None:
                return [str(item).strip() for item in parsed]
        if ',' in text:
            return [part.strip().strip('"\'') for part in text.split(',')]
        if '\t' in text:
            return [part.strip().strip('"\'') for part in text.split('\t')]
        lowered = text.lower()
        if all(token in lowered for token in ('frame', 'time', 'motion', 'value')):
            return ['frame', 'time(sec)', 'motion Id', 'value']
        return [text]

    @staticmethod
    def _parse_motion_header_object(text: str) -> Dict[str, Any]:
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            try:
                parsed = ast.literal_eval(text)
            except (SyntaxError, ValueError):
                parsed = {}
        return parsed if isinstance(parsed, dict) else {}

    def _parse_motion_text_row(self, line: str) -> Optional[List[Any]]:
        text = line.strip().rstrip(',').strip()
        if not text or text in ('[', ']'):
            return None
        if text.startswith('[') and text.endswith(']'):
            try:
                parsed = ast.literal_eval(text)
            except (SyntaxError, ValueError):
                parsed = None
            if isinstance(parsed, list):
                return parsed
            text = text[1:-1]
        if ',' not in text:
            return None
        return [part.strip().strip('"\'') for part in text.split(',')]

    def _expand_motion_pair_rows(self, rows: List[Any], headers: List[str]) -> List[Any]:
        header_map = self._motion_header_map(headers)
        if not header_map:
            return rows
        if (
            header_map.get('frame') != 0
            or header_map.get('time') != 1
            or header_map.get('motion_id') != 2
            or header_map.get('value') != 3
        ):
            return rows

        expanded: List[Any] = []
        changed = False
        for row in rows:
            if not isinstance(row, list) or len(row) <= 4:
                expanded.append(row)
                continue
            if (len(row) - 2) % 2 != 0:
                expanded.append(row)
                continue
            frame = row[0]
            time_sec = row[1]
            for index in range(2, len(row), 2):
                expanded.append([frame, time_sec, row[index], row[index + 1]])
            changed = True
        return expanded if changed else rows

    def _parse_motion_row(
        self,
        row: Any,
        headers: List[str],
    ) -> tuple[Optional[Dict[str, Any]], str]:
        if isinstance(row, dict):
            frame = self._motion_column_value(row, 'frame')
            time_sec = self._motion_column_value(row, 'time')
            motion_id = self._motion_column_value(row, 'motion_id')
            value = self._motion_column_value(row, 'value')
        elif isinstance(row, list):
            values = row
            header_map = self._motion_header_map(headers)
            if not header_map and len(values) >= 4:
                header_map = {'frame': 0, 'time': 1, 'motion_id': 2, 'value': 3}
            try:
                frame = values[header_map['frame']]
                time_sec = values[header_map['time']]
                motion_id = values[header_map['motion_id']]
                value = values[header_map['value']]
            except (KeyError, IndexError):
                return None, 'required columns not found'
        else:
            return None, 'record must be an object or array'

        frame_value = self._optional_float(frame, None)
        time_value = self._optional_float(time_sec, None)
        value_number = self._optional_float(value, None)
        motion_text = self._motion_id_text(motion_id)
        if frame_value is None:
            return None, 'frame must be numeric'
        if time_value is None:
            return None, 'time(sec) must be numeric'
        if time_value < 0:
            return None, 'time(sec) must be greater than or equal to 0'
        if not motion_text:
            return None, 'motion Id is required'
        if value_number is None:
            return None, 'value must be numeric'

        return {
            'frame': int(round(frame_value)),
            'time_sec': time_value,
            'motion_id': motion_text,
            'value': value_number,
        }, ''

    def _motion_column_value(self, row: Dict[str, Any], target: str) -> Any:
        for key, value in row.items():
            if self._motion_column_key(str(key)) == target:
                return value
        return None

    @staticmethod
    def _motion_column_key(label: str) -> str:
        compact = ''.join(char for char in label.lower() if char.isalnum())
        if compact in ('frame', 'frameid', 'frameindex'):
            return 'frame'
        if compact in ('time', 'times', 'timesec', 'seconds', 'sec', 'timestamp'):
            return 'time'
        if compact in ('motionid', 'motion', 'id', 'jointid', 'channelid'):
            return 'motion_id'
        if compact in ('value', 'angle', 'angledeg', 'deg', 'position', 'positiondeg'):
            return 'value'
        return compact

    def _motion_header_map(self, headers: List[str]) -> Dict[str, int]:
        mapping: Dict[str, int] = {}
        for index, header in enumerate(headers):
            key = self._motion_column_key(header)
            if key in ('frame', 'time', 'motion_id', 'value') and key not in mapping:
                mapping[key] = index
        return mapping if all(key in mapping for key in ('frame', 'time', 'motion_id', 'value')) else {}

    def _header_has_required_columns(self, headers: List[str]) -> bool:
        return bool(self._motion_header_map(headers))

    @staticmethod
    def _motion_id_text(value: Any) -> str:
        if value is None:
            return ''
        if isinstance(value, (int, float)) and math.isfinite(float(value)):
            number = float(value)
            return str(int(number)) if math.isclose(number, round(number)) else str(number)
        return str(value).strip()

    @staticmethod
    def _motion_id_sort_key(value: str) -> tuple[int, Any]:
        try:
            return (0, int(value))
        except ValueError:
            return (1, value)

    def _motion_graph_series(
        self,
        groups: Dict[str, List[Dict[str, Any]]],
        max_series: int = 12,
        max_points: int = 300,
    ) -> List[Dict[str, Any]]:
        series = []
        for motion_id in sorted(groups, key=self._motion_id_sort_key)[:max_series]:
            records = groups[motion_id]
            stride = max(1, int(math.ceil(len(records) / max_points)))
            points = [
                {
                    'time_sec': record['time_sec'],
                    'value': record['value'],
                }
                for record in records[::stride]
            ]
            series.append({
                'motion_id': motion_id,
                'points': points,
            })
        return series

    def request_ac_servo_jog(self, axis: Any, relative_deg: Any) -> Dict[str, Any]:
        axis_value = self._optional_int(axis, None)
        relative_value = self._optional_float(relative_deg, None)
        if axis_value is None:
            return {
                'success': False,
                'message': 'axis is required',
                **self.snapshot(),
            }
        if relative_value is None or math.isclose(relative_value, 0.0, abs_tol=1e-9):
            return {
                'success': False,
                'message': 'relative_deg is required',
                **self.snapshot(),
            }

        motor = self._motion_state_motor(axis_value)
        if motor is None:
            return {
                'success': False,
                'message': f'Axis {axis_value} not found in current motion_state',
                **self.snapshot(),
            }
        if not self._is_ac_servo_motor(motor):
            return {
                'success': False,
                'message': f'Axis {axis_value} is not AC Servo',
                **self.snapshot(),
            }
        if str(motor.get('state') or '') != 'detected':
            return {
                'success': False,
                'message': f'Axis {axis_value} is not detected',
                **self.snapshot(),
            }
        if motor.get('servo_on') is not True:
            return {
                'success': False,
                'message': f'Axis {axis_value} servo is OFF',
                **self.snapshot(),
            }
        if bool(motor.get('fault', False)):
            return {
                'success': False,
                'message': f'Axis {axis_value} has error',
                **self.snapshot(),
            }

        request_id = self._new_project_request_id('jog')
        payload = {
            'request_id': request_id,
            'project_generation': self._current_project_generation(),
            'command': 'ac_servo_jog',
            'axis': axis_value,
            'relative_deg': relative_value,
        }
        self._jog_request_publisher.publish(
            String(data=json.dumps(payload, ensure_ascii=False, separators=(',', ':')))
        )

        result = self._wait_for_jog_result(request_id)
        if result is None:
            return {
                'success': False,
                'message': (
                    f'AC Servo jog request published, but motion_supervisor result '
                    f'timed out: Axis {axis_value}, {relative_value:+.3f} deg'
                ),
                'request_id': request_id,
                **self.snapshot(),
            }

        success = bool(result.get('success'))
        if success:
            self.project_repository.mark_jog_verified()
        return {
            'success': success,
            'message': str(result.get('message') or 'motion_supervisor returned empty result'),
            'request_id': request_id,
            'supervisor_result': result,
            **self.snapshot(),
        }

    def request_dynamixel_jog(self, axis: Any, relative_deg: Any) -> Dict[str, Any]:
        axis_value = self._optional_int(axis, None)
        relative_value = self._optional_float(relative_deg, None)
        if axis_value is None:
            return {
                'success': False,
                'message': 'axis is required',
                **self.snapshot(),
            }
        if relative_value is None or math.isclose(relative_value, 0.0, abs_tol=1e-9):
            return {
                'success': False,
                'message': 'relative_deg is required',
                **self.snapshot(),
            }

        motor = self._motion_state_motor(axis_value)
        if motor is None:
            return {
                'success': False,
                'message': f'Axis {axis_value} not found in current motion_state',
                **self.snapshot(),
            }
        if not self._is_dynamixel_motor(motor):
            return {
                'success': False,
                'message': f'Axis {axis_value} is not Dynamixel',
                **self.snapshot(),
            }
        if str(motor.get('state') or '') != 'detected':
            return {
                'success': False,
                'message': f'Axis {axis_value} is not detected',
                **self.snapshot(),
            }
        if bool(motor.get('fault', False)):
            return {
                'success': False,
                'message': f'Axis {axis_value} has error',
                **self.snapshot(),
            }

        request_id = self._new_project_request_id('dynamixel-jog')
        payload = {
            'request_id': request_id,
            'project_generation': self._current_project_generation(),
            'command': 'dynamixel_jog',
            'axis': axis_value,
            'relative_deg': relative_value,
        }
        self._jog_request_publisher.publish(
            String(data=json.dumps(payload, ensure_ascii=False, separators=(',', ':')))
        )

        result = self._wait_for_jog_result(request_id)
        if result is None:
            return {
                'success': False,
                'message': (
                    f'Dynamixel jog request published, but motion_supervisor result '
                    f'timed out: Axis {axis_value}, {relative_value:+.3f} deg'
                ),
                'request_id': request_id,
                **self.snapshot(),
            }

        success = bool(result.get('success'))
        if success:
            self.project_repository.mark_jog_verified()
        return {
            'success': success,
            'message': str(result.get('message') or 'motion_supervisor returned empty result'),
            'request_id': request_id,
            'supervisor_result': result,
            **self.snapshot(),
        }

    def request_ac_servo_action(
        self,
        axis: Any,
        target_deg: Any,
        duration_sec: Any = None,
        range_recovery: Any = False,
    ) -> Dict[str, Any]:
        axis_value = self._optional_int(axis, None)
        target_value = self._optional_float(target_deg, None)
        duration_value = self._optional_float(duration_sec, None)
        if axis_value is None:
            return {
                'success': False,
                'message': 'axis is required',
                **self.snapshot(),
            }
        if target_value is None:
            return {
                'success': False,
                'message': 'target_deg is required',
                **self.snapshot(),
            }
        if duration_sec not in (None, '') and (duration_value is None or duration_value <= 0):
            return {
                'success': False,
                'message': 'duration_sec must be greater than 0',
                **self.snapshot(),
            }

        motor = self._motion_state_motor(axis_value)
        if motor is None:
            return {
                'success': False,
                'message': f'Axis {axis_value} not found in current motion_state',
                **self.snapshot(),
            }
        if not self._is_ac_servo_motor(motor):
            return {
                'success': False,
                'message': f'Axis {axis_value} is not AC Servo',
                **self.snapshot(),
            }
        if str(motor.get('state') or '') != 'detected':
            return {
                'success': False,
                'message': f'Axis {axis_value} is not detected',
                **self.snapshot(),
            }
        if motor.get('servo_on') is not True:
            return {
                'success': False,
                'message': f'Axis {axis_value} servo is OFF',
                **self.snapshot(),
            }
        if bool(motor.get('fault', False)):
            return {
                'success': False,
                'message': f'Axis {axis_value} has error',
                **self.snapshot(),
            }

        request_id = self._new_project_request_id('ac-servo-action')
        payload = {
            'request_id': request_id,
            'project_generation': self._current_project_generation(),
            'command': 'ac_servo_absolute_move',
            'axis': axis_value,
            'target_deg': target_value,
            'range_recovery': range_recovery is True,
        }
        if duration_value is not None:
            payload['duration_sec'] = duration_value
        self._action_request_publisher.publish(
            String(data=json.dumps(payload, ensure_ascii=False, separators=(',', ':')))
        )

        result = self._wait_for_action_result(request_id)
        if result is None:
            return {
                'success': False,
                'message': (
                    f'AC Servo action request published, but motion_supervisor result '
                    f'timed out: Axis {axis_value}, target {target_value:.3f} deg'
                ),
                'request_id': request_id,
                **self.snapshot(),
            }

        return {
            'success': bool(result.get('success')),
            'message': str(result.get('message') or 'motion_supervisor returned empty result'),
            'request_id': request_id,
            'supervisor_result': result,
            **self.snapshot(),
        }

    def request_dynamixel_action(
        self,
        axis: Any,
        target_deg: Any,
        duration_sec: Any = None,
        range_recovery: Any = False,
    ) -> Dict[str, Any]:
        axis_value = self._optional_int(axis, None)
        target_value = self._optional_float(target_deg, None)
        duration_value = self._optional_float(duration_sec, None)
        if axis_value is None:
            return {
                'success': False,
                'message': 'axis is required',
                **self.snapshot(),
            }
        if target_value is None:
            return {
                'success': False,
                'message': 'target_deg is required',
                **self.snapshot(),
            }
        if duration_sec not in (None, '') and (duration_value is None or duration_value <= 0):
            return {
                'success': False,
                'message': 'duration_sec must be greater than 0',
                **self.snapshot(),
            }

        motor = self._motion_state_motor(axis_value)
        if motor is None:
            return {
                'success': False,
                'message': f'Axis {axis_value} not found in current motion_state',
                **self.snapshot(),
            }
        if not self._is_dynamixel_motor(motor):
            return {
                'success': False,
                'message': f'Axis {axis_value} is not Dynamixel',
                **self.snapshot(),
            }
        if str(motor.get('state') or '') != 'detected':
            return {
                'success': False,
                'message': f'Axis {axis_value} is not detected',
                **self.snapshot(),
            }
        if bool(motor.get('fault', False)):
            return {
                'success': False,
                'message': f'Axis {axis_value} has error',
                **self.snapshot(),
            }

        request_id = self._new_project_request_id('dynamixel-action')
        payload = {
            'request_id': request_id,
            'project_generation': self._current_project_generation(),
            'command': 'dynamixel_absolute_move',
            'axis': axis_value,
            'target_deg': target_value,
            'range_recovery': range_recovery is True,
        }
        if duration_value is not None:
            payload['duration_sec'] = duration_value
        self._action_request_publisher.publish(
            String(data=json.dumps(payload, ensure_ascii=False, separators=(',', ':')))
        )

        result = self._wait_for_action_result(request_id)
        if result is None:
            return {
                'success': False,
                'message': (
                    f'Dynamixel action request published, but motion_supervisor result '
                    f'timed out: Axis {axis_value}, target {target_value:.3f} deg'
                ),
                'request_id': request_id,
                **self.snapshot(),
            }

        return {
            'success': bool(result.get('success')),
            'message': str(result.get('message') or 'motion_supervisor returned empty result'),
            'request_id': request_id,
            'supervisor_result': result,
            **self.snapshot(),
        }

    def request_ac_servo_control(
        self,
        action: Any,
        axis: Any = None,
        scope: Any = 'selected',
    ) -> Dict[str, Any]:
        action_value = str(action or '').strip().lower().replace('-', '_')
        scope_value = str(scope or 'selected').strip().lower()
        if action_value not in ('servo_on', 'servo_off', 'fault_reset'):
            return {
                'success': False,
                'message': 'action must be servo_on, servo_off, or fault_reset',
                **self.snapshot(),
            }
        if scope_value not in ('selected', 'all'):
            return {
                'success': False,
                'message': 'scope must be selected or all',
                **self.snapshot(),
            }

        if scope_value == 'all':
            axes = [
                self._optional_int(motor.get('controller_index'), None)
                for motor in self._motion_state_motors()
                if self._is_ac_servo_motor(motor)
                and str(motor.get('state') or '') == 'detected'
            ]
            axes = [item for item in axes if item is not None]
            if not axes:
                return {
                    'success': False,
                    'message': 'detected AC Servo axis not found',
                    **self.snapshot(),
                }
            axis_value = None
        else:
            axis_value = self._optional_int(axis, None)
            if axis_value is None:
                return {
                    'success': False,
                    'message': 'axis is required',
                    **self.snapshot(),
                }
            motor = self._motion_state_motor(axis_value)
            if motor is None:
                return {
                    'success': False,
                    'message': f'Axis {axis_value} not found in current motion_state',
                    **self.snapshot(),
                }
            if not self._is_ac_servo_motor(motor):
                return {
                    'success': False,
                    'message': f'Axis {axis_value} is not AC Servo',
                    **self.snapshot(),
                }
            if str(motor.get('state') or '') != 'detected':
                return {
                    'success': False,
                    'message': f'Axis {axis_value} is not detected',
                    **self.snapshot(),
                }
            axes = [axis_value]

        request_id = self._new_project_request_id('ac-servo-control')
        payload = {
            'request_id': request_id,
            'project_generation': self._current_project_generation(),
            'command': 'ac_servo_control',
            'action': action_value,
            'scope': scope_value,
            'axes': axes,
        }
        if axis_value is not None:
            payload['axis'] = axis_value

        self._jog_request_publisher.publish(
            String(data=json.dumps(payload, ensure_ascii=False, separators=(',', ':')))
        )

        result = self._wait_for_jog_result(request_id, timeout_sec=2.0)
        if result is None:
            return {
                'success': False,
                'message': (
                    f'AC Servo control request published, but motion_supervisor result '
                    f'timed out: {action_value}'
                ),
                'request_id': request_id,
                **self.snapshot(),
            }

        return {
            'success': bool(result.get('success')),
            'message': str(result.get('message') or 'motion_supervisor returned empty result'),
            'request_id': request_id,
            'supervisor_result': result,
            **self.snapshot(),
        }

    def request_safety_stop(self, emergency: bool) -> Dict[str, Any]:
        request_id = self.publish_safety_stop(emergency)
        result = self._wait_for_jog_result(request_id, timeout_sec=2.0)
        if result is None:
            return {
                'success': False,
                'message': 'motion_supervisor safety stop response timed out',
                'request_id': request_id,
                **self.snapshot(),
            }
        return {
            'success': bool(result.get('success')),
            'message': str(result.get('message') or 'safety stop result unavailable'),
            'request_id': request_id,
            'supervisor_result': result,
            **self.snapshot(),
        }

    def publish_safety_stop(self, emergency: bool) -> str:
        """Publish a priority safety command without waiting for acknowledgement."""
        request_id = self._new_project_request_id('safety-stop')
        payload = {
            'request_id': request_id,
            'project_generation': self._current_project_generation(),
            'command': 'safety_emergency_stop' if emergency else 'safety_motion_stop',
        }
        self._safety_request_publisher.publish(
            String(data=json.dumps(payload, ensure_ascii=False, separators=(',', ':')))
        )
        return request_id

    def _read_current_motor_config(self) -> Dict[str, Any]:
        try:
            self.motor_config_file = self._selected_motor_config_path()
        except ValueError:
            return self._default_motor_config()
        if not self.motor_config_file.is_file():
            return self._default_motor_config()
        content = self.motor_config_file.read_text(encoding='utf-8')
        config = yaml.safe_load(content) or {}
        return config if isinstance(config, dict) else self._default_motor_config()

    def _motor_config_file_from_payload(self, payload: Dict[str, Any]) -> Path:
        project_id = self.project_repository.selected_project_id()
        if not project_id:
            raise ValueError('통합 프로젝트를 먼저 선택하세요')
        detail = self.project_repository.get_project(project_id)
        project = detail.get('project') or {}
        active = project.get('active_files') or {}
        active_name = str(active.get('motor_axes') or '').strip()
        config_dir = (Path(str(project.get('path') or '')) / 'motor_axes').resolve()
        active_path = config_dir / active_name if active_name else config_dir / 'motor_axes.yaml'
        self.motor_config_file = active_path
        requested = str(payload.get('file_name') or '').strip()
        if not requested:
            return active_path

        name = Path(requested).name.strip()
        if not name or name in ('.', '..'):
            raise ValueError('motor config file name is empty')
        if not name.lower().endswith(('.yaml', '.yml')):
            name = f'{name}.yaml'

        target = (config_dir / name).resolve()
        try:
            target.relative_to(config_dir)
        except ValueError as exc:
            raise ValueError('motor config file must stay under config directory') from exc
        return target

    def _selected_motor_config_path(self) -> Path:
        project_id = self.project_repository.selected_project_id()
        if not project_id:
            raise ValueError('통합 프로젝트를 먼저 선택하세요')
        detail = self.project_repository.get_project(project_id)
        active = detail.get('project', {}).get('active_files') or {}
        file_name = str(active.get('motor_axes') or '').strip()
        if not file_name:
            raise ValueError('현재 프로젝트에 모터축 설정 파일이 없습니다')
        return self.project_repository.export_path(
            project_id, 'motor_axes', file_name
        )

    def _write_motor_config_selection(self, path: Path) -> None:
        project_id = self.project_repository.selected_project_id()
        if not project_id:
            raise ValueError('통합 프로젝트를 먼저 선택하세요')
        project = self.project_repository.get_project(project_id)['project']
        selection_file = Path(project['path']) / 'runtime' / 'selected_motor_config_path.txt'
        selection_file.parent.mkdir(parents=True, exist_ok=True)
        selection_file.write_text(str(path) + '\n', encoding='utf-8')

    def _clear_motor_config_selection(self) -> None:
        project_id = self.project_repository.selected_project_id()
        if not project_id:
            return
        project = self.project_repository.get_project(project_id)['project']
        selection_file = Path(project['path']) / 'runtime' / 'selected_motor_config_path.txt'
        selection_file.unlink(missing_ok=True)

    def _write_motor_config(self, content: str, target_file: Optional[Path] = None) -> None:
        target = target_file or self.motor_config_file
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.is_file():
            timestamp = time.strftime('%Y%m%d-%H%M%S')
            history_dir = target.parent.parent / 'runtime' / 'history' / 'motor_axes'
            history_dir.mkdir(parents=True, exist_ok=True)
            backup = history_dir / f'{timestamp}-{target.name}'
            counter = 2
            while backup.exists():
                backup = history_dir / f'{timestamp}-{counter}-{target.name}'
                counter += 1
            backup.write_text(target.read_text(encoding='utf-8'), encoding='utf-8')
        target.write_text(content.rstrip() + '\n', encoding='utf-8')
        self.motor_config_file = target
        self._write_motor_config_selection(target)

    def _default_motor_config(self) -> Dict[str, Any]:
        return {
            'period': 1000000,
            'masters': [
                {
                    'id': 0,
                    'type': 'ethercat',
                    'number_of_slaves': 0,
                    'ethercat_master_index': 0,
                    'slaves': [],
                },
            ],
            'drivers': [
                {
                    'id': 0,
                    'driver_model': 'UNVERIFIED_MINAS',
                    'pulse_per_revolution': 8388608,
                    'rated_effort': 0.16,
                    'unit_effort': 0.1,
                    'rated_current': 1.1,
                    'rated_power_w': 50,
                    'rated_speed_rpm': 3000,
                    'lower': -36000.0,
                    'upper': 36000.0,
                    'speed': 2000000.0,
                    'acceleration': 180000.0,
                    'deceleration': 180000.0,
                    'profile_velocity': 18000.0,
                    'profile_acceleration': 180000.0,
                    'profile_deceleration': 180000.0,
                    'profile_position_value': 1,
                    'profile_velocity_value': 3,
                    'profile_effort_value': 4,
                    'type': 'minas',
                    'param_file': str(
                        self.workspace_root
                        / 'src/motion_system/ros2/motion_system_ros2/motion_control_bridge/param'
                    ),
                },
            ],
        }

    def _registry_from_motor_config(self, config: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(config, dict):
            config = {}
        identity_by_axis = {
            self._optional_int(item.get('controller_index'), None): item
            for item in config.get('web_axis_identities', [])
            if isinstance(item, dict)
            and self._optional_int(item.get('controller_index'), None) is not None
        }
        profile_by_axis = {
            self._optional_int(item.get('controller_index'), None): item
            for item in config.get('web_axis_profiles', [])
            if isinstance(item, dict)
            and self._optional_int(item.get('controller_index'), None) is not None
        }
        drivers_by_id = {
            int(driver.get('id')): driver
            for driver in config.get('drivers', [])
            if isinstance(driver, dict) and driver.get('id') is not None
        }

        motors: List[Dict[str, Any]] = []
        for master in config.get('masters', []):
            if not isinstance(master, dict):
                continue
            master_id = self._optional_int(master.get('id'), 0)
            transport = str(master.get('type') or 'unknown')
            ethercat_master_index = self._optional_int(
                master.get('ethercat_master_index'), 0
            )
            serial_port = master.get('serial_port') or master.get('port')
            serial_baudrate = self._optional_int(
                master.get('serial_baudrate'),
                self._optional_int(master.get('baudrate'), None),
            )
            for index, slave in enumerate(master.get('slaves', [])):
                if not isinstance(slave, dict):
                    continue
                driver_id = self._optional_int(slave.get('driver_id'), 0)
                driver = drivers_by_id.get(driver_id, {})
                driver_family = str(driver.get('type') or 'unknown')
                motor_type = 'ac_servo' if driver_family == 'minas' else driver_family
                axis = self._optional_int(slave.get('controller_index'), index)
                alias = self._optional_int(slave.get('alias'), None)
                web_identity = identity_by_axis.get(axis, {})
                web_profile = profile_by_axis.get(axis, {})
                bus_id = self._optional_int(
                    slave.get('bus_id'),
                    self._optional_int(slave.get('id'), None),
                )
                slave_position = self._optional_int(slave.get('position'), index)
                name = str(slave.get('name') or f'Axis {axis}')
                motor_id = (
                    f'{motor_type}_{transport}_master_{ethercat_master_index}_alias_{alias}'
                    if transport == 'ethercat' and alias is not None and alias > 0
                    else (
                        f'{motor_type}_{transport}_master_'
                        f'{ethercat_master_index}_slave_{slave_position}'
                    )
                    if transport == 'ethercat'
                    else (
                        f'{motor_type}_{transport}_port_'
                        f'{quote(str(serial_port or ""), safe="")}_id_{bus_id}'
                    )
                    if bus_id is not None
                    else f'{motor_type}_{transport}_axis_{axis}'
                )
                motors.append(
                    self._normalize_motor_entry(
                        {
                            'id': motor_id,
                            'enabled': True,
                            'hidden': False,
                            'deleted': False,
                            'axis': axis,
                            'name': name,
                            'motor_type': motor_type,
                            'driver_family': driver_family,
                            'transport': transport,
                            'identity': {
                                'ethercat_master_index': (
                                    ethercat_master_index
                                    if transport == 'ethercat'
                                    else None
                                ),
                                'rotary_alias': self._optional_int(
                                    web_identity.get('rotary_alias'), None
                                ),
                                'ethercat_alias': self._optional_int(
                                    web_identity.get('eeprom_alias'), alias
                                ),
                                'node_id': bus_id,
                                'bus_id': bus_id,
                                'serial_port': serial_port,
                                'serial_baudrate': serial_baudrate,
                                'slave_position': self._optional_int(
                                    web_identity.get('slave_position'),
                                    slave_position
                                    if alias in (None, 0) else None,
                                ),
                                'identity_source': str(
                                    web_identity.get('identity_source') or ''
                                ),
                                'vendor_id': self._optional_int(
                                    web_identity.get('vendor_id'),
                                    self._optional_int(slave.get('vendor_id'), None),
                                ),
                                'product_code': self._optional_int(
                                    web_identity.get('product_id'),
                                    self._optional_int(slave.get('product_id'), None),
                                ),
                                'revision_number': self._optional_int(
                                    web_identity.get('revision_number'), None
                                ),
                                'serial_number': self._optional_int(
                                    web_identity.get('serial_number'), None
                                ),
                                'sii_order_number': str(
                                    web_identity.get('sii_order_number') or ''
                                ),
                                'sii_device_name': str(
                                    web_identity.get('sii_device_name') or ''
                                ),
                            },
                            'profile': {
                                'driver_model': str(
                                    web_profile.get('driver_model')
                                    or driver.get('driver_model')
                                    or ''
                                ),
                                'model_confirmed': (
                                    web_profile.get(
                                        'model_confirmed',
                                        web_identity.get('nameplate_confirmed'),
                                    ) is True
                                ),
                                'model_source': str(
                                    web_profile.get('model_source')
                                    or (
                                        'user_nameplate'
                                        if web_identity.get('nameplate_confirmed') is True
                                        else ''
                                    )
                                ),
                            },
                            'config': {
                                'controller_index': axis,
                                'ethercat_master_index': (
                                    ethercat_master_index
                                    if transport == 'ethercat'
                                    else None
                                ),
                                'master_id': master_id,
                                'driver_id': driver_id,
                                'bus_id': bus_id,
                                'serial_port': serial_port,
                                'serial_baudrate': serial_baudrate,
                                'alias': alias,
                                'position': slave_position,
                                'vendor_id': self._optional_int(slave.get('vendor_id'), None),
                                'product_id': self._optional_int(slave.get('product_id'), None),
                                'profile_mode': self._optional_int(slave.get('profile_mode'), 0),
                            },
                        },
                        len(motors),
                    )
                )

        return {
            'version': 1,
            'updated_at': None,
            'motors': motors,
        }

    @staticmethod
    def _expand_shared_driver_profiles(config: Dict[str, Any]) -> Dict[str, Any]:
        """Give every configured axis an independent driver profile.

        The runtime schema stores limits on driver entries. Reusing one driver ID
        therefore makes lower/upper and velocity settings change for every axis
        that references it. Cloning only repeated references preserves the schema
        while making axis editing independent for AC Servo and Dynamixel alike.
        """
        if not isinstance(config, dict):
            return config

        expanded = copy.deepcopy(config)
        drivers = expanded.get('drivers')
        masters = expanded.get('masters')
        if not isinstance(drivers, list) or not isinstance(masters, list):
            return expanded

        drivers_by_id = {
            MotionWebBridge._optional_int(driver.get('id'), None): driver
            for driver in drivers
            if isinstance(driver, dict)
            and MotionWebBridge._optional_int(driver.get('id'), None) is not None
        }
        used_ids = set(drivers_by_id)
        next_id = max(used_ids | {-1}) + 1
        reference_counts: Dict[int, int] = {}

        for master in masters:
            if not isinstance(master, dict):
                continue
            slaves = master.get('slaves')
            if not isinstance(slaves, list):
                continue
            for slave in slaves:
                if not isinstance(slave, dict):
                    continue
                driver_id = MotionWebBridge._optional_int(slave.get('driver_id'), None)
                if driver_id is None or driver_id not in drivers_by_id:
                    continue
                count = reference_counts.get(driver_id, 0)
                reference_counts[driver_id] = count + 1
                if count == 0:
                    continue

                while next_id in used_ids:
                    next_id += 1
                cloned_driver = copy.deepcopy(drivers_by_id[driver_id])
                cloned_driver['id'] = next_id
                drivers.append(cloned_driver)
                slave['driver_id'] = next_id
                used_ids.add(next_id)
                next_id += 1

        return expanded

    def _motor_config_from_registry(
        self,
        registry: Dict[str, Any],
        current: Dict[str, Any],
    ) -> Dict[str, Any]:
        if not isinstance(current, dict):
            current = self._default_motor_config()
        config = dict(current)
        config['period'] = 1000000
        drivers = config.get('drivers')
        if not isinstance(drivers, list) or not drivers:
            drivers = self._default_motor_config()['drivers']
        drivers = [dict(driver) if isinstance(driver, dict) else {} for driver in drivers]

        masters = config.get('masters')
        if not isinstance(masters, list) or not masters:
            masters = self._default_motor_config()['masters']
        masters = [dict(master) if isinstance(master, dict) else {} for master in masters]

        ethercat_slaves_by_master: Dict[int, List[Dict[str, Any]]] = {}
        web_axis_identities = []
        web_axis_profiles = []
        for motor in registry.get('motors', []):
            if not isinstance(motor, dict):
                continue
            if motor.get('deleted') or not motor.get('enabled', False):
                continue
            if motor.get('transport') != 'ethercat':
                continue
            motor_config = motor.get('config') if isinstance(motor.get('config'), dict) else {}
            axis = self._optional_int(motor_config.get('controller_index'), motor.get('axis'))
            if axis is None:
                continue
            name = str(motor.get('name') or f'Axis {axis}').strip() or f'Axis {axis}'
            identity = motor.get('identity') if isinstance(motor.get('identity'), dict) else {}
            profile = motor.get('profile') if isinstance(motor.get('profile'), dict) else {}
            ethercat_master_index = self._optional_int(
                motor_config.get('ethercat_master_index'),
                self._optional_int(identity.get('ethercat_master_index'), 0),
            )
            if ethercat_master_index is None or ethercat_master_index < 0:
                raise ValueError(
                    f'Axis {axis}의 EtherCAT Master 번호가 올바르지 않습니다'
                )
            eeprom_alias = self._optional_int(
                identity.get('ethercat_alias'),
                self._optional_int(motor_config.get('alias'), 0),
            )
            slave_position = self._optional_int(
                identity.get('slave_position'),
                self._optional_int(motor_config.get('position'), 0),
            )
            driver_id = self._driver_id_for_registry_motor(motor, drivers)
            ethercat_slaves_by_master.setdefault(
                ethercat_master_index, []
            ).append(
                {
                    'controller_index': axis,
                    'name': name,
                    'driver_id': driver_id,
                    'alias': eeprom_alias,
                    # The motion-system position field is the physical
                    # EtherCAT Slave Position. Keep it identical to the
                    # user-visible identity instead of retaining stale data.
                    'position': slave_position,
                    'vendor_id': self._optional_int(
                        identity.get('vendor_id'),
                        self._optional_int(motor_config.get('vendor_id'), None),
                    ),
                    'product_id': self._optional_int(
                        identity.get('product_code'),
                        self._optional_int(motor_config.get('product_id'), None),
                    ),
                    'profile_mode': self._optional_int(motor_config.get('profile_mode'), 0),
                }
            )
            web_axis_identities.append({
                'controller_index': axis,
                'ethercat_master_index': ethercat_master_index,
                'eeprom_alias': eeprom_alias,
                'rotary_alias': self._optional_int(identity.get('rotary_alias'), None),
                'slave_position': slave_position,
                'vendor_id': self._optional_int(
                    identity.get('vendor_id'),
                    self._optional_int(motor_config.get('vendor_id'), None),
                ),
                'product_id': self._optional_int(
                    identity.get('product_code'),
                    self._optional_int(motor_config.get('product_id'), None),
                ),
                'revision_number': self._optional_int(
                    identity.get('revision_number'),
                    self._optional_int(motor_config.get('revision_number'), None),
                ),
                'serial_number': self._optional_int(
                    identity.get('serial_number'),
                    self._optional_int(motor_config.get('serial_number'), None),
                ),
                'identity_source': str(identity.get('identity_source') or ''),
                'sii_order_number': str(identity.get('sii_order_number') or ''),
                'sii_device_name': str(identity.get('sii_device_name') or ''),
            })
            web_axis_profiles.append({
                'controller_index': axis,
                'driver_model': str(profile.get('driver_model') or ''),
                'model_confirmed': profile.get('model_confirmed') is True,
                'model_source': str(profile.get('model_source') or ''),
            })

        existing_ethercat_masters = {
            self._optional_int(master.get('ethercat_master_index'), 0): master
            for master in masters
            if isinstance(master, dict) and master.get('type') == 'ethercat'
        }
        used_master_ids = {
            self._optional_int(master.get('id'), None)
            for master in masters
            if isinstance(master, dict)
            and self._optional_int(master.get('id'), None) is not None
        }
        next_master_id = max(used_master_ids | {-1}) + 1
        ethercat_masters = []
        for master_index in sorted(ethercat_slaves_by_master):
            ethercat_master = dict(
                existing_ethercat_masters.get(master_index) or {}
            )
            master_id = self._optional_int(ethercat_master.get('id'), None)
            if master_id is None:
                while next_master_id in used_master_ids:
                    next_master_id += 1
                master_id = next_master_id
                used_master_ids.add(master_id)
                next_master_id += 1
            slaves = ethercat_slaves_by_master[master_index]
            slaves.sort(key=lambda item: int(item.get('controller_index') or 0))
            ethercat_master.update({
                'id': master_id,
                'type': 'ethercat',
                'ethercat_master_index': master_index,
                'slaves': slaves,
                'number_of_slaves': len(slaves),
            })
            ethercat_masters.append(ethercat_master)

        if web_axis_identities:
            config['web_axis_identities'] = web_axis_identities
        else:
            config.pop('web_axis_identities', None)
        if web_axis_profiles:
            config['web_axis_profiles'] = web_axis_profiles
        else:
            config.pop('web_axis_profiles', None)

        non_bus_masters = [
            master
            for master in masters
            if master.get('type') not in {'ethercat', 'serial'}
        ]
        master_context = ethercat_masters + non_bus_masters + [
            master for master in masters if master.get('type') == 'serial'
        ]
        serial_masters = self._serial_masters_from_registry(
            registry, master_context, drivers
        )
        masters = ethercat_masters + non_bus_masters + serial_masters

        config['masters'] = masters
        config['drivers'] = self._prune_unused_drivers(
            self._normalize_driver_configs(drivers),
            masters,
        )
        return self._expand_shared_driver_profiles(config)

    def _normalize_driver_configs(self, drivers: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        normalized = []
        for driver in drivers:
            if not isinstance(driver, dict):
                normalized.append(driver)
                continue
            item = dict(driver)
            if str(item.get('type') or '') == 'dynamixel':
                item['param_file'] = self._dynamixel_param_file_for_model(
                    str(item.get('driver_model') or '')
                )
            normalized.append(item)
        return normalized

    def _dynamixel_param_file_for_model(self, driver_model: str) -> str:
        model = driver_model.lower().replace('_', '-')
        if 'xm540-w150' in model:
            return str(self.workspace_root / 'config/dynamixel_xm540_w150.yaml')
        return str(self.workspace_root / 'config/dynamixel_xm540_w270.yaml')

    def _prune_unused_drivers(
        self,
        drivers: List[Dict[str, Any]],
        masters: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        used_driver_ids = set()
        for master in masters:
            if not isinstance(master, dict):
                continue
            slaves = master.get('slaves', [])
            if not isinstance(slaves, list):
                continue
            for slave in slaves:
                if not isinstance(slave, dict):
                    continue
                driver_id = self._optional_int(slave.get('driver_id'), None)
                if driver_id is not None:
                    used_driver_ids.add(driver_id)

        return [
            driver
            for driver in drivers
            if not isinstance(driver, dict)
            or self._optional_int(driver.get('id'), None) is None
            or self._optional_int(driver.get('id'), None) in used_driver_ids
        ]

    def _serial_masters_from_registry(
        self,
        registry: Dict[str, Any],
        current_masters: List[Dict[str, Any]],
        drivers: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        serial_masters_by_key: Dict[tuple, Dict[str, Any]] = {}
        used_master_ids = {
            self._optional_int(master.get('id'), -1)
            for master in current_masters
            if isinstance(master, dict)
        }
        next_master_id = max([item for item in used_master_ids if item is not None] + [-1]) + 1

        def master_for(port: str, baudrate: int) -> Dict[str, Any]:
            nonlocal next_master_id
            key = (port, baudrate)
            if key in serial_masters_by_key:
                return serial_masters_by_key[key]

            existing = next(
                (
                    dict(master)
                    for master in current_masters
                    if master.get('type') == 'serial'
                    and str(master.get('serial_port') or '') == port
                    and self._optional_int(master.get('serial_baudrate'), None) == baudrate
                ),
                None,
            )
            if existing is None:
                while next_master_id in used_master_ids:
                    next_master_id += 1
                existing = {
                    'id': next_master_id,
                    'type': 'serial',
                    'serial_port': port,
                    'serial_baudrate': baudrate,
                }
                used_master_ids.add(next_master_id)
                next_master_id += 1

            existing['type'] = 'serial'
            existing['serial_port'] = port
            existing['serial_baudrate'] = baudrate
            existing['slaves'] = []
            serial_masters_by_key[key] = existing
            return existing

        for motor in registry.get('motors', []):
            if not isinstance(motor, dict):
                continue
            if motor.get('deleted') or not motor.get('enabled', False):
                continue
            if motor.get('transport') != 'serial':
                continue
            motor_config = motor.get('config') if isinstance(motor.get('config'), dict) else {}
            identity = motor.get('identity') if isinstance(motor.get('identity'), dict) else {}
            axis = self._optional_int(motor_config.get('controller_index'), motor.get('axis'))
            bus_id = self._optional_int(
                motor_config.get('bus_id'),
                self._optional_int(identity.get('bus_id'), identity.get('node_id')),
            )
            port = str(motor_config.get('serial_port') or identity.get('serial_port') or '').strip()
            baudrate = self._optional_int(
                motor_config.get('serial_baudrate'),
                self._optional_int(identity.get('serial_baudrate'), None),
            )
            if str(motor.get('driver_family') or motor.get('motor_type') or '') == 'dynamixel':
                baudrate = DYNAMIXEL_BAUDRATE
            if axis is None or bus_id is None or not port or baudrate is None:
                continue

            driver_id = self._driver_id_for_registry_motor(motor, drivers)
            master = master_for(port, baudrate)
            name = str(motor.get('name') or f'Axis {axis}').strip() or f'Axis {axis}'
            master['slaves'].append(
                {
                    'controller_index': axis,
                    'name': name,
                    'driver_id': driver_id,
                    'bus_id': bus_id,
                    'profile_mode': self._optional_int(motor_config.get('profile_mode'), 0),
                }
            )

        serial_masters = []
        for master in serial_masters_by_key.values():
            master['slaves'].sort(key=lambda item: int(item.get('controller_index') or 0))
            master['number_of_slaves'] = len(master['slaves'])
            if master['number_of_slaves'] > 0:
                serial_masters.append(master)
        serial_masters.sort(key=lambda item: int(item.get('id') or 0))
        return serial_masters

    def _driver_id_for_registry_motor(
        self,
        motor: Dict[str, Any],
        drivers: List[Dict[str, Any]],
    ) -> int:
        motor_config = motor.get('config') if isinstance(motor.get('config'), dict) else {}
        identity = dict(motor.get('identity')) if isinstance(motor.get('identity'), dict) else {}
        profile = dict(motor.get('profile')) if isinstance(motor.get('profile'), dict) else {}
        # Accept older registries, but normalize the model/profile facts out of
        # the physical discovery identity before any further processing.
        if not profile.get('driver_model') and identity.get('driver_model'):
            profile['driver_model'] = identity.get('driver_model')
        if 'model_confirmed' not in profile and 'nameplate_confirmed' in identity:
            profile['model_confirmed'] = identity.get('nameplate_confirmed') is True
        if not profile.get('model_source') and profile.get('model_confirmed') is True:
            profile['model_source'] = 'user_nameplate'
        identity.pop('driver_model', None)
        identity.pop('nameplate_confirmed', None)
        driver_type = str(motor.get('driver_family') or motor.get('motor_type') or 'unknown')
        driver_model = str(profile.get('driver_model') or '').strip()
        requested_id = self._optional_int(motor_config.get('driver_id'), None)

        drivers_by_id = {
            self._optional_int(driver.get('id'), None): driver
            for driver in drivers
            if isinstance(driver, dict)
        }
        if requested_id is not None:
            requested_driver = drivers_by_id.get(requested_id)
            if requested_driver is not None:
                same_type = str(requested_driver.get('type') or '') == driver_type
                same_model = not driver_model or str(requested_driver.get('driver_model') or '') == driver_model
                if same_type and same_model:
                    return requested_id

        for driver in drivers:
            if not isinstance(driver, dict):
                continue
            if str(driver.get('type') or '') != driver_type:
                continue
            if driver_model and str(driver.get('driver_model') or '') != driver_model:
                continue
            driver_id = self._optional_int(driver.get('id'), None)
            if driver_id is not None:
                return driver_id

        return self._append_driver_for_registry_motor(driver_type, driver_model, drivers)

    def _append_driver_for_registry_motor(
        self,
        driver_type: str,
        driver_model: str,
        drivers: List[Dict[str, Any]],
    ) -> int:
        if driver_type == 'dynamixel':
            # Dynamixel values are model-specific.  Do not clone the first
            # registered Dynamixel profile and merely rename it, because scan
            # order would then give W150 values to W270 (or vice versa).
            template = self._default_dynamixel_driver(driver_model)
        else:
            template = next(
                (
                    dict(driver)
                    for driver in drivers
                    if isinstance(driver, dict) and str(driver.get('type') or '') == driver_type
                ),
                None,
            )
        if template is None and driver_type == 'minas':
            template = dict(self._default_motor_config()['drivers'][0])
        elif template is None:
            template = {
                'type': driver_type,
                'driver_model': driver_model or driver_type,
            }

        used_driver_ids = {
            self._optional_int(driver.get('id'), -1)
            for driver in drivers
            if isinstance(driver, dict)
        }
        next_driver_id = max([item for item in used_driver_ids if item is not None] + [-1]) + 1
        while next_driver_id in used_driver_ids:
            next_driver_id += 1

        template['id'] = next_driver_id
        template['type'] = driver_type
        if driver_model and driver_type != 'dynamixel':
            template['driver_model'] = driver_model
        elif not template.get('driver_model'):
            template['driver_model'] = driver_type
        if driver_type == 'dynamixel':
            template['param_file'] = self._dynamixel_param_file_for_model(
                str(template.get('driver_model') or '')
            )
        drivers.append(template)
        return next_driver_id

    def _default_dynamixel_driver(self, driver_model: str = '') -> Dict[str, Any]:
        model = str(driver_model or '').strip().upper().replace('_', '-')
        if 'XM540-W150' in model:
            canonical_model = 'XM540-W150'
            rated_speed_rpm = 66
            velocity = 396.0
        elif 'XM540-W270' in model:
            canonical_model = 'XM540-W270-R'
            rated_speed_rpm = 37
            velocity = 222.0
        else:
            canonical_model = str(driver_model or 'Dynamixel').strip() or 'Dynamixel'
            rated_speed_rpm = 30
            velocity = 100.0

        return {
            'driver_model': canonical_model,
            'pulse_per_revolution': 4096,
            'rated_effort': 1.0,
            'unit_effort': 0.00269,
            'rated_current': 1.0,
            'rated_speed_rpm': rated_speed_rpm,
            'lower': -180.0,
            'upper': 180.0,
            'speed': velocity,
            'acceleration': 703104.5,
            'deceleration': 703104.5,
            'profile_velocity': velocity,
            'profile_acceleration': 703104.5,
            'profile_deceleration': 703104.5,
            'profile_position_value': 3,
            'profile_velocity_value': 1,
            'profile_effort_value': 0,
            'type': 'dynamixel',
            'param_file': self._dynamixel_param_file_for_model(canonical_model),
        }

    @staticmethod
    def _empty_motor_registry() -> Dict[str, Any]:
        return {
            'version': 1,
            'updated_at': None,
            'motors': [],
        }

    def _normalize_motor_registry(self, registry: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(registry, dict):
            registry = {}

        motors = registry.get('motors', [])
        if not isinstance(motors, list):
            motors = []

        normalized_motors: List[Dict[str, Any]] = []
        used_ids = set()
        for index, motor in enumerate(motors):
            if not isinstance(motor, dict):
                continue
            normalized = self._normalize_motor_entry(motor, index)
            motor_id = str(normalized['id'])
            if motor_id in used_ids:
                motor_id = f'{motor_id}_{index}'
                normalized['id'] = motor_id
            used_ids.add(motor_id)
            normalized_motors.append(normalized)

        return {
            'version': int(registry.get('version') or 1),
            'updated_at': registry.get('updated_at'),
            'motors': normalized_motors,
        }

    @staticmethod
    def _normalize_motor_entry(motor: Dict[str, Any], index: int) -> Dict[str, Any]:
        motor_type = str(motor.get('motor_type') or 'unknown')
        transport = str(motor.get('transport') or 'unknown')
        driver_family = str(motor.get('driver_family') or motor_type)
        identity = dict(motor.get('identity')) if isinstance(motor.get('identity'), dict) else {}
        profile = dict(motor.get('profile')) if isinstance(motor.get('profile'), dict) else {}
        if not profile.get('driver_model') and identity.get('driver_model'):
            profile['driver_model'] = identity.pop('driver_model')
        else:
            identity.pop('driver_model', None)
        if 'model_confirmed' not in profile and 'nameplate_confirmed' in identity:
            profile['model_confirmed'] = identity.get('nameplate_confirmed') is True
        identity.pop('nameplate_confirmed', None)
        if not profile.get('model_source') and profile.get('model_confirmed') is True:
            profile['model_source'] = 'user_nameplate'
        config = dict(motor.get('config')) if isinstance(motor.get('config'), dict) else {}

        def optional_int(value: Any, default: Optional[int]) -> Optional[int]:
            if value is None or value == '':
                return default
            try:
                return int(str(value), 0)
            except (TypeError, ValueError):
                return default

        axis = optional_int(
            config.get('controller_index'),
            optional_int(motor.get('axis'), None),
        )
        if axis is not None:
            config['controller_index'] = axis

        motor_id = str(motor.get('id') or '').strip()
        if not motor_id:
            motor_id = f'{transport}_{motor_type}_{index}'

        return {
            'id': motor_id,
            'enabled': bool(motor.get('enabled', False)),
            'hidden': bool(motor.get('hidden', False)),
            'deleted': bool(motor.get('deleted', False)),
            'axis': axis,
            'name': str(motor.get('name') or ''),
            'motor_type': motor_type,
            'driver_family': driver_family,
            'transport': transport,
            'identity': identity,
            'profile': profile,
            'config': config,
        }

    @staticmethod
    def _optional_int(value: Any, default: Optional[int]) -> Optional[int]:
        if value is None or value == '':
            return default
        try:
            return int(str(value), 0)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _optional_float(value: Any, default: Optional[float]) -> Optional[float]:
        if value is None or value == '':
            return default
        try:
            number = float(value)
        except (TypeError, ValueError):
            return default
        return number if math.isfinite(number) else default

    def _motion_state_motor(self, axis: int) -> Optional[Dict[str, Any]]:
        for motor in self._motion_state_motors():
            if self._optional_int(motor.get('controller_index'), None) == axis:
                return motor
        return None

    def _motion_state_motors(self) -> List[Dict[str, Any]]:
        with self._lock:
            state = self._motion_state
        if not isinstance(state, dict):
            return []
        motors = state.get('motors', [])
        if not isinstance(motors, list):
            return []
        return [motor for motor in motors if isinstance(motor, dict)]

    @staticmethod
    def _is_ac_servo_motor(motor: Dict[str, Any]) -> bool:
        values = [
            motor.get('motor_type'),
            motor.get('motor_type_label'),
            motor.get('driver_model'),
            motor.get('driver_name'),
            motor.get('transport'),
        ]
        text = ' '.join(str(value or '').lower() for value in values)
        return 'minas' in text or 'ac servo' in text or 'ac_servo' in text

    @staticmethod
    def _is_dynamixel_motor(motor: Dict[str, Any]) -> bool:
        values = [
            motor.get('motor_type'),
            motor.get('motor_type_label'),
            motor.get('driver_model'),
            motor.get('driver_name'),
            motor.get('transport'),
        ]
        text = ' '.join(str(value or '').lower() for value in values)
        return 'dynamixel' in text


def _safety_first_stop(bridge: MotionWebBridge, method, *args):
    """Hold final motor output before waiting for an upper-level source to stop."""
    cancel_pending = getattr(bridge, 'cancel_pending_motion_studio_start', None)
    if callable(cancel_pending):
        cancel_pending()
    safety_result = bridge.request_safety_stop(False)
    source_result = method(*args)
    result = dict(source_result) if isinstance(source_result, dict) else {
        'success': False,
        'message': '정지 대상 노드의 응답 형식이 올바르지 않습니다',
    }
    result['safety_stop'] = safety_result
    failures = []
    if safety_result.get('success') is False:
        failures.append(
            f'최종 모터 출력 정지 확인 실패: '
            f'{safety_result.get("message") or "응답 없음"}'
        )
    if result.get('success') is False:
        failures.append(str(result.get('message') or '상위 동작 정지 확인 실패'))
    if failures:
        result['success'] = False
        result['message'] = ' · '.join(failures)
    return result


def create_app(bridge: MotionWebBridge) -> FastAPI:
    app = FastAPI(title='Motion Web Bridge')
    ui_share = Path(get_package_share_directory('motion_web_ui')) / 'static'
    workspace_dir = os.environ.get('MOTION_WORKSPACE', '')
    dev_static = Path(workspace_dir) / 'src' / 'motion_web' / 'web_ui' / 'static'
    if workspace_dir and dev_static.is_dir():
        ui_share = dev_static

    @app.middleware('http')
    async def project_generation_boundary(request: Request, call_next):
        request_generation = request.headers.get('X-Project-Generation')
        start_generation = bridge._current_project_generation()
        if request_generation not in (None, ''):
            try:
                if int(request_generation) != start_generation:
                    return JSONResponse(
                        status_code=409,
                        content={
                            'success': False,
                            'stale_project_generation': True,
                            'project_generation': start_generation,
                            'message': '현재 프로젝트 세대와 다른 요청을 폐기했습니다',
                        },
                    )
            except ValueError:
                return JSONResponse(
                    status_code=400,
                    content={'success': False, 'message': '프로젝트 세대 형식이 올바르지 않습니다'},
                )
        response = await call_next(request)
        response.headers['X-Project-Generation'] = str(
            bridge._current_project_generation()
        )
        return response

    def project_call(method, *args):
        try:
            return method(*args)
        except (OSError, UnicodeDecodeError, ValueError, yaml.YAMLError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get('/')
    async def index():
        return FileResponse(
            str(ui_share / 'index.html'),
            headers={'Cache-Control': 'no-store'},
        )

    @app.get('/static/{asset_path:path}')
    async def static_asset(asset_path: str):
        relative_path = Path(asset_path)
        if relative_path.is_absolute() or '..' in relative_path.parts:
            raise HTTPException(status_code=404, detail='Not Found')
        asset = ui_share / relative_path
        if not asset.is_file():
            raise HTTPException(status_code=404, detail='Not Found')
        return FileResponse(
            str(asset),
            headers={'Cache-Control': 'no-store'},
        )

    @app.get('/api/status')
    async def status():
        return bridge.snapshot()

    @app.get('/api/system/version')
    async def system_version():
        def _git_text(args: List[str], cwd: str) -> str:
            return subprocess.check_output(
                ['git', *args], cwd=cwd, stderr=subprocess.DEVNULL
            ).decode('utf-8').strip()

        def _web_url(remote: str) -> str:
            if remote.startswith('git@github.com:'):
                return 'https://github.com/' + remote.split(':', 1)[1].removesuffix('.git')
            if remote.startswith('https://github.com/'):
                return remote.removesuffix('.git')
            return remote

        try:
            cwd = os.environ.get('MOTION_WORKSPACE', os.getcwd())
            branch = _git_text(['rev-parse', '--abbrev-ref', 'HEAD'], cwd)
            hash_str = _git_text(['rev-parse', '--short', 'HEAD'], cwd)
            full_hash = _git_text(['rev-parse', 'HEAD'], cwd)
            msg = _git_text(['log', '-1', '--format=%s'], cwd)
            remote = _git_text(['remote', 'get-url', 'origin'], cwd)
            return {
                'branch': branch,
                'hash': hash_str,
                'full_hash': full_hash,
                'message': msg,
                'remote_url': remote,
                'remote_web_url': _web_url(remote),
                'is_main': branch == 'main',
            }
        except Exception:
            return {
                'branch': 'unknown',
                'hash': 'unknown',
                'full_hash': '',
                'message': '',
                'remote_url': '',
                'remote_web_url': '',
                'is_main': False,
            }

    @app.get('/api/coordination')
    async def coordination_status():
        return bridge.coordination_status()

    @app.put('/api/coordination/settings')
    async def update_coordination_settings(request: Request):
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail='request body must be an object')
        try:
            return await asyncio.to_thread(bridge.update_coordination_settings, body)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post('/api/coordination/local-readiness')
    async def coordination_local_readiness():
        return await asyncio.to_thread(bridge.coordination_local_readiness)

    @app.get('/api/coordination/local-status')
    async def coordination_local_status(request: Request):
        remote_ip = request.client.host if request.client else ''
        if remote_ip not in {'127.0.0.1', '::1'}:
            raise HTTPException(status_code=403, detail='loopback only')
        return bridge.coordination_local_status()

    @app.post('/api/coordination/control')
    async def coordination_control(request: Request):
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail='request body must be an object')
        try:
            return await asyncio.to_thread(bridge.coordination_control, body)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post('/api/coordination/local-control')
    async def coordination_local_control(request: Request):
        remote_ip = request.client.host if request.client else ''
        if remote_ip not in {'127.0.0.1', '::1'}:
            raise HTTPException(status_code=403, detail='loopback only')
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail='request body must be an object')
        return await asyncio.to_thread(bridge.coordination_local_control, body)

    @app.get('/api/projects')
    async def motion_projects():
        return project_call(bridge.list_motion_projects)

    @app.post('/api/execution-context/apply')
    async def apply_execution_context():
        return project_call(bridge._reconcile_execution_context)

    @app.post('/api/projects')
    async def create_motion_project(request: Request):
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail='request body must be an object')
        return project_call(bridge.create_motion_project, body)

    @app.get('/api/projects/{project_id}')
    async def motion_project(project_id: str):
        return project_call(bridge.load_motion_project, project_id)

    @app.patch('/api/projects/{project_id}')
    async def update_motion_project(project_id: str, request: Request):
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail='request body must be an object')
        return project_call(bridge.update_motion_project, project_id, body)

    @app.post('/api/projects/{project_id}/select')
    async def select_motion_project(project_id: str):
        return project_call(bridge.select_motion_project, project_id)

    @app.delete('/api/projects/{project_id}')
    async def delete_motion_project(project_id: str):
        return project_call(bridge.delete_motion_project, project_id)

    @app.post('/api/projects/{project_id}/copy-file')
    async def copy_motion_project_file(project_id: str, request: Request):
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail='request body must be an object')
        return project_call(bridge.copy_motion_project_file, project_id, body)

    @app.post('/api/projects/{project_id}/files')
    async def import_motion_project_file(project_id: str, request: Request):
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail='request body must be an object')
        return project_call(bridge.import_motion_project_file, project_id, body)

    @app.get('/api/projects/{project_id}/tree-file')
    async def read_only_motion_project_file(project_id: str, relative_path: str):
        return project_call(
            bridge.load_read_only_project_file, project_id, relative_path
        )

    @app.get('/api/projects/{project_id}/files/{category}/{file_name}/download')
    async def download_motion_project_file(
        project_id: str, category: str, file_name: str
    ):
        path = project_call(
            bridge.download_motion_project_file, project_id, category, file_name
        )
        return FileResponse(str(path), filename=path.name)

    @app.get('/api/projects/{project_id}/files/{category}/{file_name}')
    async def motion_project_file(project_id: str, category: str, file_name: str):
        return project_call(
            bridge.load_motion_project_file, project_id, category, file_name
        )

    @app.put('/api/projects/{project_id}/files/{category}/{file_name}')
    async def save_motion_project_file(
        project_id: str, category: str, file_name: str, request: Request
    ):
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail='request body must be an object')
        return project_call(
            bridge.save_motion_project_file,
            project_id,
            category,
            file_name,
            body,
        )

    @app.post('/api/projects/{project_id}/files/{category}/{file_name}/rename')
    async def rename_motion_project_file(
        project_id: str, category: str, file_name: str, request: Request
    ):
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail='request body must be an object')
        return project_call(
            bridge.rename_motion_project_file,
            project_id,
            category,
            file_name,
            body,
        )

    @app.post('/api/projects/{project_id}/files/{category}/{file_name}/active')
    async def activate_motion_project_file(
        project_id: str, category: str, file_name: str
    ):
        return project_call(
            bridge.activate_motion_project_file,
            project_id,
            category,
            file_name,
        )

    @app.post('/api/projects/{project_id}/files/{category}/{file_name}/open-editor')
    async def open_motion_project_file_for_editing(
        project_id: str, category: str, file_name: str
    ):
        return project_call(
            bridge.open_motion_project_file_for_editing,
            project_id,
            category,
            file_name,
        )

    @app.delete('/api/projects/{project_id}/files/{category}/{file_name}')
    async def delete_motion_project_file(
        project_id: str, category: str, file_name: str
    ):
        return project_call(
            bridge.delete_motion_project_file, project_id, category, file_name
        )

    @app.get('/api/motor-events')
    async def motor_events(
        limit: int = 200, category: str = 'all', file_name: str = 'all'
    ):
        return bridge.motor_events(limit=limit, category=category, file_name=file_name)

    @app.delete('/api/motor-events')
    async def clear_motor_events():
        return bridge.clear_motor_events()

    @app.delete('/api/motor-events/files/{file_name}')
    async def delete_motor_event_file(file_name: str):
        return project_call(bridge.delete_motor_event_file, file_name)

    @app.get('/api/servo-alarm-policy')
    async def servo_alarm_policy():
        return project_call(bridge.servo_alarm_policy)

    @app.put('/api/servo-alarm-policy')
    async def save_servo_alarm_policy(request: Request):
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail='request body must be an object')
        return project_call(bridge.save_servo_alarm_policy, body)

    @app.post('/api/monitoring/enabled')
    async def set_monitoring(request: Request):
        body = await request.json()
        enabled = bool(body.get('enabled', True))
        return bridge.set_monitoring(enabled)

    @app.post('/api/motors/scan')
    async def scan_motors():
        return await asyncio.to_thread(bridge.scan_motors)

    @app.post('/api/motors/scan/ac-servo')
    async def scan_ac_servo_motors():
        return await asyncio.to_thread(bridge.scan_ac_servo_motors)

    @app.post('/api/motors/scan/dynamixel')
    async def scan_dynamixel_motors():
        return await asyncio.to_thread(bridge.scan_dynamixel_motors)

    @app.get('/api/motors/scan/progress')
    async def motor_scan_progress():
        return bridge.motor_scan_progress()

    @app.get('/api/motors/ethercat-aliases')
    async def read_ethercat_aliases():
        return await asyncio.to_thread(bridge.read_ethercat_aliases)

    @app.post('/api/motors/ethercat-alias')
    async def write_ethercat_alias(request: Request):
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail='request body must be an object')
        return await asyncio.to_thread(bridge.write_ethercat_alias, body)

    @app.get('/api/motor-config')
    async def motor_config():
        return bridge.load_motor_config()

    @app.put('/api/motor-config')
    async def save_motor_config(request: Request):
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail='request body must be an object')
        return bridge.save_motor_config(body)

    @app.delete('/api/motor-config')
    async def delete_motor_config():
        return project_call(bridge.delete_motor_config)

    @app.post('/api/motor-config/apply')
    async def apply_motor_config():
        return await asyncio.to_thread(bridge.apply_motor_config)

    @app.post('/api/system/program/restart')
    async def restart_managed_program():
        return await asyncio.to_thread(project_call, bridge.restart_managed_program)

    @app.post('/api/system/desktop-shortcut')
    async def create_desktop_shortcut():
        return await asyncio.to_thread(bridge.create_desktop_shortcut)

    @app.post('/api/system/motor-control/restart')
    async def restart_motor_control_system():
        return await asyncio.to_thread(
            project_call,
            bridge.restart_motor_control_system,
        )

    @app.post('/api/system/motor-runtime/clear')
    async def clear_motor_runtime_application():
        return await asyncio.to_thread(
            project_call,
            bridge.clear_motor_runtime_application,
        )

    @app.get('/api/motion-files')
    async def motion_files():
        return bridge.list_motion_files()

    @app.get('/api/motion-files/{file_id}')
    async def motion_file(file_id: str):
        return bridge.load_motion_file(file_id)

    @app.delete('/api/motion-files/{file_id}')
    async def delete_motion_file(file_id: str):
        return bridge.delete_motion_file(file_id)

    @app.get('/api/motion-mappings')
    async def motion_mappings():
        return bridge.list_motion_mappings()

    @app.post('/api/motion-mappings')
    async def save_motion_mapping(request: Request):
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail='request body must be an object')
        return bridge.save_motion_mapping(body)

    @app.post('/api/motion-mappings/validate')
    async def validate_motion_mapping(request: Request):
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail='request body must be an object')
        return bridge.validate_motion_mapping(body)

    @app.get('/api/motion-mappings/{file_id}')
    async def motion_mapping(file_id: str):
        return bridge.load_motion_mapping(file_id)

    @app.delete('/api/motion-mappings/{file_id}')
    async def delete_motion_mapping(file_id: str):
        return bridge.delete_motion_mapping(file_id)

    @app.get('/api/motion-run/status')
    async def motion_run_status():
        return bridge.motion_run_status()

    @app.post('/api/motion-run/check')
    async def motion_run_check(request: Request):
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail='request body must be an object')
        return await asyncio.to_thread(bridge.motion_run_check, body)

    @app.post('/api/motion-run/initialize')
    async def motion_run_initialize(request: Request):
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail='request body must be an object')
        return await asyncio.to_thread(bridge.motion_run_initialize, body)

    @app.post('/api/motion-run/start')
    async def motion_run_start(request: Request):
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail='request body must be an object')
        return await asyncio.to_thread(bridge.motion_run_start, body)

    @app.put('/api/motion-run/automation')
    async def motion_automation_configure(request: Request):
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail='request body must be an object')
        return await asyncio.to_thread(bridge.motion_automation_configure, body)

    @app.post('/api/motion-run/automation/start')
    async def motion_automation_start(request: Request):
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail='request body must be an object')
        return await asyncio.to_thread(bridge.motion_automation_start, body)

    @app.post('/api/motion-run/automation/reserve')
    async def motion_automation_reserve(request: Request):
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail='request body must be an object')
        return await asyncio.to_thread(bridge.motion_automation_reserve, body)

    @app.post('/api/motion-run/automation/disable')
    async def motion_automation_disable():
        return await asyncio.to_thread(bridge.motion_automation_disable)

    @app.post('/api/motion-run/stop')
    async def motion_run_stop():
        return await asyncio.to_thread(_safety_first_stop, bridge, bridge.motion_run_stop)

    @app.post('/api/motion-run/stop-after-cycle')
    async def motion_run_stop_after_cycle_api():
        return await asyncio.to_thread(bridge.motion_run_stop_after_cycle)

    @app.post('/api/safety/motion-stop')
    async def safety_motion_stop():
        cancel_pending = getattr(bridge, 'cancel_pending_motion_studio_start', None)
        if callable(cancel_pending):
            cancel_pending()
        request_id = bridge.publish_safety_stop(False)
        return {
            'success': True,
            'message': '전체 동작 정지 명령 우선 전송 완료',
            'request_id': request_id,
            'acknowledgement_pending': True,
        }

    @app.post('/api/safety/emergency-stop')
    async def safety_emergency_stop():
        cancel_pending = getattr(bridge, 'cancel_pending_motion_studio_start', None)
        if callable(cancel_pending):
            cancel_pending()
        request_id = bridge.publish_safety_stop(True)
        return {
            'success': True,
            'message': '긴급정지 명령 우선 전송 완료',
            'request_id': request_id,
            'acknowledgement_pending': True,
        }

    register_motion_studio_routes(app, bridge, project_call, _safety_first_stop)

    @app.get('/api/midi-monitor')
    async def midi_monitor_status():
        return await asyncio.to_thread(bridge.midi_monitor_status)

    @app.put('/api/midi-monitor/mapping')
    async def save_midi_monitor_mapping(request: Request):
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail='request body must be an object')
        return await asyncio.to_thread(bridge.save_midi_monitor_mapping, body)

    @app.post('/api/midi-monitor/banks')
    async def create_midi_bank(request: Request):
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail='request body must be an object')
        return await asyncio.to_thread(bridge.create_midi_bank, body)

    @app.post('/api/midi-monitor/banks/{bank_id}/select')
    async def select_midi_bank(bank_id: str):
        return await asyncio.to_thread(bridge.select_midi_bank, bank_id)

    @app.put('/api/midi-monitor/banks/{bank_id}')
    async def update_midi_bank(bank_id: str, request: Request):
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail='request body must be an object')
        return await asyncio.to_thread(bridge.update_midi_bank, bank_id, body)

    @app.delete('/api/midi-monitor/banks/{bank_id}')
    async def delete_midi_bank(bank_id: str):
        return await asyncio.to_thread(bridge.delete_midi_bank, bank_id)

    @app.post('/api/midi-monitor/banks/file/save')
    async def save_midi_banks_to_file():
        return await asyncio.to_thread(bridge.save_midi_banks_to_file)

    @app.post('/api/midi-monitor/banks/file/load')
    async def load_midi_banks_from_file():
        return await asyncio.to_thread(bridge.load_midi_banks_from_file)

    @app.post('/api/midi-monitor/runtime/reset')
    async def reset_midi_runtime_values():
        return await asyncio.to_thread(bridge.reset_midi_runtime_values)

    @app.post('/api/midi-monitor/device/connect')
    async def connect_midi_device():
        return await asyncio.to_thread(bridge.connect_midi_device)

    @app.post('/api/midi-monitor/device/disconnect')
    async def disconnect_midi_device():
        return await asyncio.to_thread(bridge.disconnect_midi_device)

    @app.post('/api/motion-test/ac-servo/jog')
    async def ac_servo_jog(request: Request):
        body = await request.json()
        return await asyncio.to_thread(
            bridge.request_ac_servo_jog,
            body.get('axis'),
            body.get('relative_deg'),
        )

    @app.post('/api/motion-test/dynamixel/jog')
    async def dynamixel_jog(request: Request):
        body = await request.json()
        return await asyncio.to_thread(
            bridge.request_dynamixel_jog,
            body.get('axis'),
            body.get('relative_deg'),
        )

    @app.post('/api/motion-test/ac-servo/action')
    async def ac_servo_action(request: Request):
        body = await request.json()
        return await asyncio.to_thread(
            bridge.request_ac_servo_action,
            body.get('axis'),
            body.get('target_deg'),
            body.get('duration_sec'),
            body.get('range_recovery', False),
        )

    @app.post('/api/motion-test/dynamixel/action')
    async def dynamixel_action(request: Request):
        body = await request.json()
        return await asyncio.to_thread(
            bridge.request_dynamixel_action,
            body.get('axis'),
            body.get('target_deg'),
            body.get('duration_sec'),
            body.get('range_recovery', False),
        )

    @app.post('/api/motion-test/ac-servo/control')
    async def ac_servo_control(request: Request):
        body = await request.json()
        return await asyncio.to_thread(
            bridge.request_ac_servo_control,
            body.get('action'),
            body.get('axis'),
            body.get('scope', 'selected'),
        )

    @app.websocket('/ws/status')
    async def websocket_status(websocket: WebSocket):
        await websocket.accept()
        period_sec = 1.0 / max(bridge.web_publish_hz, 0.1)
        try:
            while True:
                await websocket.send_json(bridge.snapshot())
                try:
                    event = await asyncio.wait_for(
                        websocket.receive(), timeout=period_sec
                    )
                except asyncio.TimeoutError:
                    continue
                if event.get('type') == 'websocket.disconnect':
                    return
        except WebSocketDisconnect:
            return
        except (ConnectionError, RuntimeError):
            # The ASGI server may report a closed transport as a runtime or
            # connection error while it is shutting down.  Either case means
            # this status task must end so service restart is not blocked.
            return

    return app


def main(args=None) -> None:
    rclpy.init(args=args)
    bridge = MotionWebBridge()
    spin_thread = threading.Thread(target=rclpy.spin, args=(bridge,), daemon=True)
    spin_thread.start()

    app = create_app(bridge)
    try:
        uvicorn.run(app, host=bridge.host, port=bridge.port, log_level='info')
    finally:
        bridge.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
        spin_thread.join(timeout=1.0)


if __name__ == '__main__':
    main()
