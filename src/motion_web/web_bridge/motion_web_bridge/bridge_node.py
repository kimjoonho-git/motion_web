import copy
import json
import os
import socket
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import rclpy
import uvicorn
import yaml
from fastapi import FastAPI, HTTPException, Request
from motion_common import generation, rpc, topics
from fastapi.responses import JSONResponse
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
from std_msgs.msg import String
from std_srvs.srv import SetBool, Trigger

from .ethercat_alias_manager import EthercatAliasError, EthercatAliasManager
from .coordination_bridge import (
    CoordinationWebBridge, local_motion_control, local_motion_readiness,
)
from . import (
    motion_studio_session,
    motion_file_analysis,
    motor_config_rules,
)
from .motion_studio_bridge import MotionStudioRosBridge
from .motion_studio_session import MotionStudioSession
from .execution_context_service import ExecutionContextService
from .manual_motor_commands import ManualMotorCommandService
from .motor_runtime_service import MotorRuntimeService
from .motor_config_service import MotorConfigService
from .motor_event_log import MotorEventLog
from .scan_orchestrator import ScanOrchestrator
from .motion_studio_routes import register_motion_studio_routes
from .bridge_helpers import (
    add_monitoring_motion_values,
    motor_activity_snapshot,
    _monitoring_finite_float,
    _workspace_root,
)
from .routes import (
    register_project_routes,
    register_motor_routes,
    register_motion_run_routes,
    register_midi_routes,
    register_safety_routes,
    register_system_routes,
    register_schedule_routes,
)
from .motion_studio_sync import (
    MotionStudioSync,
    # 재수출 · 외부에서 bridge_node 경유로 참조한다
    _project_tree_category_signature,  # noqa: F401
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


class MotionWebBridge(Node):
    def __init__(self) -> None:
        super().__init__('motion_web_bridge')
        self.ethercat_alias_manager = EthercatAliasManager()
        self.motion_state_topic = self.declare_parameter(
            'motion_state_topic',
            topics.MOTION_STATE,
        ).value
        self.motion_value_topic = self.declare_parameter(
            'motion_value_topic',
            topics.MOTION_VALUE_STATE,
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
            topics.MOTOR_SCAN_PROGRESS,
        ).value
        self.jog_request_topic = self.declare_parameter(
            'jog_request_topic',
            topics.MANUAL_JOG_REQUEST,
        ).value
        self.jog_result_topic = self.declare_parameter(
            'jog_result_topic',
            topics.MANUAL_JOG_RESULT,
        ).value
        self.safety_request_topic = self.declare_parameter(
            'safety_request_topic',
            topics.SAFETY_REQUEST,
        ).value
        self.action_request_topic = self.declare_parameter(
            'action_request_topic',
            topics.MANUAL_ACTION_REQUEST,
        ).value
        self.action_result_topic = self.declare_parameter(
            'action_result_topic',
            topics.MANUAL_ACTION_RESULT,
        ).value
        self.motion_mapping_request_topic = self.declare_parameter(
            'motion_mapping_request_topic',
            topics.MOTION_MAPPING_REQUEST,
        ).value
        self.motion_mapping_response_topic = self.declare_parameter(
            'motion_mapping_response_topic',
            topics.MOTION_MAPPING_RESPONSE,
        ).value
        self.motion_run_request_topic = self.declare_parameter(
            'motion_run_request_topic',
            topics.MOTION_RUN_REQUEST,
        ).value
        self.motion_run_response_topic = self.declare_parameter(
            'motion_run_response_topic',
            topics.MOTION_RUN_RESPONSE,
        ).value
        self.motion_run_status_topic = self.declare_parameter(
            'motion_run_status_topic',
            topics.MOTION_RUN_STATUS,
        ).value
        self.midi_monitor_state_topic = self.declare_parameter(
            'midi_monitor_state_topic',
            topics.MIDI_MONITOR_STATE,
        ).value
        self.midi_monitor_request_topic = self.declare_parameter(
            'midi_monitor_request_topic',
            topics.MIDI_MONITOR_REQUEST,
        ).value
        self.midi_monitor_response_topic = self.declare_parameter(
            'midi_monitor_response_topic',
            topics.MIDI_MONITOR_RESPONSE,
        ).value
        self.motion_studio_request_topic = self.declare_parameter(
            'motion_studio_request_topic', topics.STUDIO_REQUEST
        ).value
        self.motion_studio_response_topic = self.declare_parameter(
            'motion_studio_response_topic', topics.STUDIO_RESPONSE
        ).value
        self.motion_studio_status_topic = self.declare_parameter(
            'motion_studio_status_topic', topics.STUDIO_STATUS
        ).value
        self.motion_studio_editor_request_topic = self.declare_parameter(
            'motion_studio_editor_request_topic', topics.STUDIO_EDITOR_REQUEST
        ).value
        self.motion_studio_editor_response_topic = self.declare_parameter(
            'motion_studio_editor_response_topic', topics.STUDIO_EDITOR_RESPONSE
        ).value
        self.safety_status_topic = self.declare_parameter(
            'safety_status_topic', topics.SAFETY_STATUS
        ).value
        self.max_jog_delta_deg = float(
            self.declare_parameter('max_jog_delta_deg', 360.0).value
        )
        self.host = self.declare_parameter('host', '0.0.0.0').value
        self.port = int(self.declare_parameter('port', 8000).value)
        self.access_host = str(self.declare_parameter('access_host', '').value)
        self.workspace_root = _workspace_root()
        default_config = self.workspace_root / 'config' / 'bootstrap_motor_config.yaml'
        launch_motor_config_file = Path(
            str(self.declare_parameter('motor_config_file', str(default_config)).value)
        ).expanduser()
        default_restart_script = self.workspace_root / 'scripts' / 'restart_motion_monitor.sh'
        restart_script = Path(
            str(self.declare_parameter('restart_script', str(default_restart_script)).value)
        ).expanduser()
        default_motion_projects_dir = self.workspace_root / 'motion_projects'
        self.motion_projects_dir = Path(
            str(self.declare_parameter(
                'motion_projects_dir', str(default_motion_projects_dir)
            ).value)
        ).expanduser()
        self.project_repository = ProjectRepository(self.motion_projects_dir)
        self._motor_lifecycle_lock = threading.Lock()
        # 기동 시점의 파일과 선택 프로젝트의 편집 파일을 분리해 둔다. 적용·재시작
        # 전까지는 실행 중인 모터 스택이 기동 시점 파일을 물고 있다.
        self._motor_runtime = MotorRuntimeService(
            self,
            repository=self.project_repository,
            workspace_root=self.workspace_root,
        )
        self._execution_context = ExecutionContextService(
            self,
            repository=self.project_repository,
            workspace_root=self.workspace_root,
        )
        self._motor_config = MotorConfigService(
            self,
            runtime=self._motor_runtime,
            lifecycle_lock=self._motor_lifecycle_lock,
            repository=self.project_repository,
            workspace_root=self.workspace_root,
            selected=launch_motor_config_file,
            applied=launch_motor_config_file.resolve(),
            restart_script=restart_script,
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
        self._motor_event_log = MotorEventLog(
            log_dir=self.event_log_dir,
            retention_days=self.event_log_retention_days,
            max_bytes=self.event_log_max_bytes,
            max_records=self.event_log_max_records,
            max_files=self.event_log_max_files,
            repository=self.project_repository,
            workspace_root=self.workspace_root,
            runtime_project_id=lambda: self._runtime_project_id(),
            logger=self.get_logger,
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
        # 요청·응답 저장소 · motion_common.rpc.ResultStore 단일 구현
        self._motion_mapping_store = rpc.ResultStore()
        self._motion_run_store = rpc.ResultStore()
        self._motion_run_lock = threading.Lock()
        self._motion_run_status: Dict[str, Any] = {}
        self._coordination_poll_lock = threading.Lock()
        self._coordination_poll_received_monotonic = 0.0
        self._coordination_watchdog_stop_execution_id = ''
        self._midi_monitor_lock = threading.Lock()
        self._midi_monitor_status: Dict[str, Any] = {}
        self._midi_monitor_store = rpc.ResultStore()
        self._motion_studio_session = MotionStudioSession()
        self._motion_studio_ros_bridge = MotionStudioRosBridge(
            self, self._motion_studio_session, self._execution_context.context_id
        )
        self._motion_studio_sync_service = MotionStudioSync(
            self, self._motion_studio_session, self._motion_studio_ros_bridge
        )
        self._safety_status_lock = threading.Lock()
        self._safety_status: Dict[str, Any] = {}
        self._monitoring_motion_mapping_lock = threading.Lock()
        self._monitoring_motion_mapping_context_id = ''
        self._monitoring_motion_mapping_rows: List[Dict[str, Any]] = []
        self._project_generation_lock = threading.Lock()
        self._project_generation = self.project_repository.project_generation()
        self._supervisor_project_generation = 0
        self._bridge_instance_id = f'{os.getpid()}-{time.time_ns()}'
        self._bridge_started_at = time.time()

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
            lambda msg: self._scan.progress_callback(msg),
            20,
        )
        self._monitoring_client = self.create_client(SetBool, self.monitoring_service)
        self._scan_client = self.create_client(Trigger, self.scan_service)
        self._scan_ac_servo_client = self.create_client(Trigger, self.scan_ac_servo_service)
        self._scan_dynamixel_client = self.create_client(Trigger, self.scan_dynamixel_service)
        self._scan = ScanOrchestrator(
            self,
            runtime=self._motor_runtime,
            lifecycle_lock=self._motor_lifecycle_lock,
            repository=self.project_repository,
            scan_client=self._scan_client,
            scan_ac_servo_client=self._scan_ac_servo_client,
            scan_dynamixel_client=self._scan_dynamixel_client,
            scan_service=self.scan_service,
            scan_ac_servo_service=self.scan_ac_servo_service,
            scan_dynamixel_service=self.scan_dynamixel_service,
            load_motor_config=self._motor_config.load,
        )
        self._jog_request_publisher = self.create_publisher(String, self.jog_request_topic, 10)
        self._safety_request_publisher = self.create_publisher(
            String, self.safety_request_topic, 10
        )
        self._action_request_publisher = self.create_publisher(String, self.action_request_topic, 10)
        self._manual = ManualMotorCommandService(
            self,
            repository=self.project_repository,
            jog_publisher=self._jog_request_publisher,
            action_publisher=self._action_request_publisher,
            jog_result_topic=self.jog_result_topic,
            action_result_topic=self.action_result_topic,
        )
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
            lambda msg: self._manual.jog_result_callback(msg),
            10,
        )
        self._action_result_subscription = self.create_subscription(
            String,
            self.action_result_topic,
            lambda msg: self._manual.action_result_callback(msg),
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
            1.0, self._execution_context.schedule_reconcile
        )
        self._motor_operation_reconcile_timer = self.create_timer(
            0.2, self._motor_runtime.reconcile_callback
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
            f'motor_config_file={self._motor_config.selected}, '
            f'motion_projects_dir={self.motion_projects_dir}, '
            f'restart_script={self._motor_config.restart_script}, '
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
        self._motor_event_log.record_motor_error_transitions(payload)

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

        self._motion_mapping_store.store(request_id, payload)

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

        self._motion_run_store.store(request_id, payload)
        status = payload.get('status')
        with self._motion_run_lock:
            if isinstance(status, dict) and self._payload_matches_selected_project(status):
                self._motion_run_status = status
        if isinstance(status, dict) and self._payload_matches_selected_project(status):
            self._motor_event_log.record_motion_run_transition(status)

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
        self._motor_event_log.record_motion_run_transition(payload)

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
        self._midi_monitor_store.store(request_id, payload)
        with self._midi_monitor_lock:
            if (
                payload.get('success')
                and isinstance(payload.get('channels'), list)
                and self._payload_matches_selected_project(payload)
            ):
                self._midi_monitor_status = dict(payload)

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

    def _wait_for_motion_mapping_result(
        self,
        request_id: str,
        timeout_sec: float = 2.0,
    ) -> Optional[Dict[str, Any]]:
        return self._motion_mapping_store.wait(request_id, timeout_sec)

    def _wait_for_motion_run_result(
        self,
        request_id: str,
        timeout_sec: float = 2.0,
    ) -> Optional[Dict[str, Any]]:
        return self._motion_run_store.wait(request_id, timeout_sec)

    def _wait_for_midi_monitor_result(
        self,
        request_id: str,
        timeout_sec: float = 2.0,
    ) -> Optional[Dict[str, Any]]:
        return self._midi_monitor_store.wait(request_id, timeout_sec)

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
        motion_studio = self._motion_studio_session.snapshot_status()
        with self._safety_status_lock:
            safety_status = dict(self._safety_status) if self._safety_status else {}
        midi_received_at = midi_monitor.pop('_bridge_received_at', None)
        if midi_received_at is not None and time.time() - float(midi_received_at) > 1.0:
            midi_monitor['connected'] = False
            midi_monitor['message'] = 'MIDI 모니터 노드 상태 수신 중단'
        midi_monitor = self._safety_adjusted_midi_status(
            midi_monitor, safety_status=safety_status
        )

        runtime_status = motor_config_rules.runtime_service_status(
            motion_state,
            applied_motor_config_file=getattr(getattr(self, '_motor_config', None), 'applied', None),
            repository=getattr(self, 'project_repository', None),
            workspace_root=getattr(self, 'workspace_root', Path()),
        )
        # Websocket status is published frequently. The stored execution
        # project service).  The stored execution context hashes every active
        # project file, so validating it for every websocket frame makes page
        # and API responses contend with continuous disk reads and hashing.
        # The coordinator and explicit context endpoints still perform the
        # full validation; a status frame only reports that validated result.
        execution_context = self._execution_context.status(validate_files=False)
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
            acknowledged = self._manual.wait_for_action_result(boundary_id, timeout_sec=1.0)
            if not isinstance(acknowledged, dict) or acknowledged.get('success') is not True:
                raise ValueError(
                    '최종 모터 명령 노드가 프로젝트 세대 전환을 확인하지 않았습니다'
                )
        policy_result = self.publish_servo_alarm_policy()
        if policy_result.get('success') is not True:
            self._execution_context._set_status(
                state='waiting_motor_runtime',
                ready=False,
                project_id=str(self.project_repository.selected_project_id() or ''),
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

    def _try_trigger_automation_resume(
        self, execution_context: Optional[Dict[str, Any]] = None
    ) -> None:
        """Trigger autonomous motion start once execution context is ready and motor control blockers clear."""
        context = (
            execution_context
            if isinstance(execution_context, dict)
            else self._execution_context.status(validate_files=False)
        )
        if not context.get('ready'):
            return
        context_id = str(context.get('context_id') or '')
        if not context_id:
            return

        triggered_id = getattr(self, '_automation_triggered_context_id', None)
        if triggered_id == context_id:
            return

        motion_status = dict(getattr(self, '_motion_run_status', {}) or {})
        automation = (
            motion_status.get('automation')
            if isinstance(motion_status.get('automation'), dict)
            else {}
        )
        if not (automation.get('enabled') and automation.get('armed')):
            return

        if not automation.get('resume_pending'):
            return

        blocker = self._motor_runtime_control_blocker()
        if blocker:
            return

        self.get_logger().info(
            f"자동 재생 예약 활성화됨. 하드웨어 준비 완료. 트리거 시도: {automation.get('motion_file_id')}"
        )
        res = self.motion_automation_start({
            'run_mode': 'continuous',
            'motion_file_id': automation.get('motion_file_id', ''),
            'mapping_file_id': automation.get('mapping_file_id', ''),
            'request_source': 'network_control',
        })
        if isinstance(res, dict) and res.get('success'):
            self._automation_triggered_context_id = context_id
        else:
            self.get_logger().warn(
                f"자동 재생 트리거 대기 중: {res.get('message') if isinstance(res, dict) else res}"
            )

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
        self._motor_event_log.append(
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
            relative = self._motor_config.applied.relative_to(
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
        return generation.new_request_id(prefix, self._current_project_generation())

    def _response_matches_current_generation(self, payload: Any) -> bool:
        return generation.response_matches(payload, self._current_project_generation())

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
        self._motor_event_log.clear_project_memory()
        self._manual.clear_pending()
        self._motion_mapping_store.clear()
        self._motion_run_store.clear()
        self._midi_monitor_store.clear()
        with self._motion_run_lock:
            self._motion_run_status = {}
            self._automation_triggered_context_id = None
        with self._midi_monitor_lock:
            self._midi_monitor_status = {}
        self._motion_studio_sync().clear_project_memory()
        scan = getattr(self, '_scan', None)
        if scan is not None:
            scan.clear_progress()

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
        studio_session = motion_studio_session.session_of(self)
        studio_status = (
            studio_session.snapshot_status() if studio_session is not None else {}
        )
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

    def create_motion_project(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        self._ensure_project_change_allowed()
        previous_generation = self._current_project_generation()
        self._execution_context._apply_lock.acquire()
        try:
            self._advance_project_generation()
            self._execution_context.invalidate_nodes()
            created = self.project_repository.create_project(payload.get('name'))
        finally:
            self._execution_context._apply_lock.release()
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
        self._execution_context._apply_lock.acquire()
        try:
            self._advance_project_generation()
            self._execution_context.invalidate_nodes()
            result = self.project_repository.delete_project(project_id)
        finally:
            self._execution_context._apply_lock.release()
        result['previous_project_generation'] = previous_generation
        result['project_generation'] = self._current_project_generation()
        if not self.project_repository.selected_project_id():
            self._motor_config.selected = Path()
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
            self._motor_config.selected = Path()
            return
        try:
            detail = self.project_repository.get_project(project_id)
            active = detail.get('project', {}).get('active_files') or {}
            motor_name = str(active.get('motor_axes') or '')
            if motor_name:
                self._motor_config.selected = self.project_repository.export_path(
                    project_id, 'motor_axes', motor_name
                )
            else:
                self._motor_config.selected = Path()
        except (OSError, ValueError, json.JSONDecodeError):
            self._motor_config.selected = Path()
            return

    def _initialize_selected_project_context(self) -> None:
        self._execution_context.reconcile()

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
            self._execution_context._apply_lock.acquire()
        try:
            if changing_project:
                self._advance_project_generation()
                self._execution_context.invalidate_nodes()
            result = self.project_repository.select_project(project_id)
            result['previous_project_generation'] = previous_generation
            result['project_generation'] = self._current_project_generation()
            active = result.get('project', {}).get('active_files') or {}
            motor_name = str(active.get('motor_axes') or '')
            if motor_name:
                self._motor_config.selected = self.project_repository.export_path(
                    project_id, 'motor_axes', motor_name
                )
            else:
                self._motor_config.selected = Path()
            self._execution_context._set_status(
                state='selected', ready=False, project_id=str(project_id), context_id='',
                message='프로젝트 선택 완료 · 실행 컨텍스트 적용 대기 중', nodes={},
            )
        finally:
            if changing_project:
                self._execution_context._apply_lock.release()
        policy_result = self.publish_servo_alarm_policy()
        if policy_result.get('success') is not True:
            raise ValueError(
                '선택 프로젝트의 서보 에러 정책을 적용하지 못했습니다: '
                f'{policy_result.get("message") or "응답 없음"}'
            )
        result['execution_context'] = self._execution_context.reconcile()
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
        result = self._manual.wait_for_jog_result(request_id, timeout_sec=1.0)
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
                    motor_config_rules.write_motor_config_selection(self.project_repository, self._motor_config.selected)
                else:
                    self._motor_config.selected = Path()
                    motor_config_rules.clear_motor_config_selection(self.project_repository)
        return result

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
            self._motor_config.selected = path
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

        loaded_file_id = motion_file_analysis.motion_mapping_file_id(result) or str(file_id or '').strip()
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

        saved_file_id = motion_file_analysis.motion_mapping_file_id(result)
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
            execution_context = self._execution_context.reconcile()
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
        with self._lock:
            project_id = self.project_repository.selected_project_id()
            context = self.project_repository.execution_context(project_id) if project_id else {}
            files = context.get('files') if isinstance(context.get('files'), dict) else {}
            motions = files.get('motions') if isinstance(files.get('motions'), dict) else {}
            mapping = files.get('motion_axis_matching') if isinstance(files.get('motion_axis_matching'), dict) else {}
            active_motion = str(motions.get('name') or '').strip()
            active_mapping = str(mapping.get('name') or '').strip()
        
        request_payload = dict(payload if payload is not None else {})
        if not str(request_payload.get('motion_file_id') or '').strip():
            request_payload['motion_file_id'] = active_motion
        if not str(request_payload.get('mapping_file_id') or '').strip():
            request_payload['mapping_file_id'] = active_mapping

        return self._request_motion_run(
            'automation_configure', request_payload, timeout_sec=2.0
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
        file_id = motion_file_analysis.midi_mapping_file_id(status)
        if not file_id:
            return {'success': False, 'message': '선택된 모션축 설정 파일이 없습니다'}
        return self._load_and_apply_midi_banks(file_id)

    def reset_midi_runtime_values(self) -> Dict[str, Any]:
        return self._request_midi_monitor('reset_runtime_values', {}, timeout_sec=2.0)

    def connect_midi_device(self) -> Dict[str, Any]:
        return self._request_midi_monitor('connect_device', {}, timeout_sec=2.0)

    def disconnect_midi_device(self) -> Dict[str, Any]:
        return self._request_midi_monitor('disconnect_device', {}, timeout_sec=2.0)


    def _persist_midi_bank_result(self, updated: Dict[str, Any]) -> Dict[str, Any]:
        if updated.get('success') is False:
            return updated
        file_id = motion_file_analysis.midi_mapping_file_id(updated)
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
            request_payload['context_id'] = self._execution_context.context_id()
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
            context = getattr(self, '_execution_context', None)
            service = MotionStudioRosBridge(
                self,
                self._motion_studio_session,
                context.context_id if context is not None else None,
            )
            self._motion_studio_ros_bridge = service
        return service

    def _motion_studio_sync(self) -> MotionStudioSync:
        service = getattr(self, '_motion_studio_sync_service', None)
        if service is None:
            service = MotionStudioSync(
                self, self._motion_studio_session, self._motion_studio_transport()
            )
            self._motion_studio_sync_service = service
        return service

    def cancel_pending_motion_studio_start(self) -> int:
        return self._motion_studio_transport().cancel_pending_start()

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
            target = motion_file_analysis.motion_file_path(
                file_id, self.motion_projects_dir / project_id / 'motions'
            )
            registration_refs = self._motion_file_registration_refs(
                project_id, target.name
            )
            if registration_refs:
                return {
                    **motion_file_analysis.list_motion_files(
                        self.project_repository, self.motion_projects_dir
                    ),
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
                **motion_file_analysis.list_motion_files(
                    self.project_repository, self.motion_projects_dir
                ),
                'success': False,
                'message': f'failed to delete motion file: {exc}',
            }
        return {
            **motion_file_analysis.list_motion_files(
                self.project_repository, self.motion_projects_dir
            ),
            'success': True,
            'message': 'motion file deleted',
            'project': result.get('project'),
        }













    def request_safety_stop(self, emergency: bool) -> Dict[str, Any]:
        request_id = self.publish_safety_stop(emergency)
        result = self._manual.wait_for_jog_result(request_id, timeout_sec=2.0)
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

    register_system_routes(app, bridge, project_call)
    register_project_routes(app, bridge, project_call)
    register_motor_routes(app, bridge, project_call)
    register_motion_run_routes(app, bridge, _safety_first_stop)
    register_safety_routes(app, bridge)
    register_midi_routes(app, bridge)
    register_schedule_routes(app, bridge, project_call)
    register_motion_studio_routes(app, bridge, project_call, _safety_first_stop)

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
