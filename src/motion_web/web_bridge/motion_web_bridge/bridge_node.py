import asyncio
import ast
import json
import math
import socket
import subprocess
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import rclpy
import uvicorn
import yaml
from ament_index_python.packages import get_package_share_directory
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from rclpy.node import Node
from std_msgs.msg import String
from std_srvs.srv import SetBool, Trigger


DYNAMIXEL_BAUDRATE = 1000000
MOTION_DATA_PERIOD_SEC = 0.02
MOTION_FILE_SIZE_LIMIT_BYTES = 10 * 1024 * 1024


class MotionWebBridge(Node):
    def __init__(self) -> None:
        super().__init__('motion_web_bridge')
        self.motion_state_topic = self.declare_parameter(
            'motion_state_topic',
            '/motion_control/motion_state',
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
        self.jog_request_topic = self.declare_parameter(
            'jog_request_topic',
            '/motion_control/manual_jog_request',
        ).value
        self.jog_result_topic = self.declare_parameter(
            'jog_result_topic',
            '/motion_control/manual_jog_result',
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
        self.max_jog_delta_deg = float(
            self.declare_parameter('max_jog_delta_deg', 360.0).value
        )
        self.host = self.declare_parameter('host', '0.0.0.0').value
        self.port = int(self.declare_parameter('port', 8000).value)
        self.access_host = str(self.declare_parameter('access_host', '').value)
        default_config = Path('/home/joonho_test/ros2_ws/config/active_motor_config.yaml')
        self.motor_config_file = Path(
            str(self.declare_parameter('motor_config_file', str(default_config)).value)
        ).expanduser()
        self.motor_config_selection_file = (
            self.motor_config_file.parent / 'selected_motor_config_path.txt'
        )
        default_restart_script = Path('/home/joonho_test/ros2_ws/scripts/restart_motion_monitor.sh')
        self.restart_script = Path(
            str(self.declare_parameter('restart_script', str(default_restart_script)).value)
        ).expanduser()
        default_motion_data_dir = Path('/home/joonho_test/ros2_ws/motion_data')
        self.motion_data_dir = Path(
            str(self.declare_parameter('motion_data_dir', str(default_motion_data_dir)).value)
        ).expanduser()
        self.motion_files_dir = self.motion_data_dir / 'files'
        self.motion_files_dir.mkdir(parents=True, exist_ok=True)
        default_event_log_dir = Path('/home/joonho_test/ros2_ws/log/motor_events')
        self.event_log_dir = Path(
            str(self.declare_parameter('event_log_dir', str(default_event_log_dir)).value)
        ).expanduser()
        self.event_log_dir.mkdir(parents=True, exist_ok=True)
        self.event_log_retention_days = max(
            1,
            int(self.declare_parameter('event_log_retention_days', 30).value),
        )
        self.event_log_max_bytes = max(
            1024 * 1024,
            int(self.declare_parameter('event_log_max_bytes', 100 * 1024 * 1024).value),
        )
        self.web_publish_hz = float(self.declare_parameter('web_publish_hz', 10.0).value)
        self._web_access = self._build_web_access_info()

        self._lock = threading.Lock()
        self._motion_state: Optional[Dict[str, Any]] = None
        self._motion_state_received_at: Optional[float] = None
        self._jog_result_lock = threading.Lock()
        self._jog_results: Dict[str, Dict[str, Any]] = {}
        self._action_result_lock = threading.Lock()
        self._action_results: Dict[str, Dict[str, Any]] = {}
        self._motion_mapping_lock = threading.Lock()
        self._motion_mapping_results: Dict[str, Dict[str, Any]] = {}
        self._motion_run_lock = threading.Lock()
        self._motion_run_results: Dict[str, Dict[str, Any]] = {}
        self._motion_run_status: Dict[str, Any] = {}
        self._midi_monitor_lock = threading.Lock()
        self._midi_monitor_status: Dict[str, Any] = {}
        self._midi_monitor_results: Dict[str, Dict[str, Any]] = {}
        self._event_log_lock = threading.RLock()
        self._active_motor_errors: Dict[str, str] = {}
        self._last_motion_run_state: Optional[str] = None

        self._subscription = self.create_subscription(
            String,
            self.motion_state_topic,
            self._motion_state_callback,
            10,
        )
        self._monitoring_client = self.create_client(SetBool, self.monitoring_service)
        self._scan_client = self.create_client(Trigger, self.scan_service)
        self._scan_ac_servo_client = self.create_client(Trigger, self.scan_ac_servo_service)
        self._scan_dynamixel_client = self.create_client(Trigger, self.scan_dynamixel_service)
        self._jog_request_publisher = self.create_publisher(String, self.jog_request_topic, 10)
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
            f'motion_data_dir={self.motion_data_dir}, '
            f'restart_script={self.restart_script}, '
            f'url={self._web_access["url"]}'
        )

    def _motion_state_callback(self, msg: String) -> None:
        try:
            payload = json.loads(msg.data)
        except json.JSONDecodeError:
            self.get_logger().warn(f'Invalid {self.motion_state_topic} JSON received.')
            return

        with self._lock:
            self._motion_state = payload
            self._motion_state_received_at = time.time()
        self._record_motor_error_transitions(payload)

    def _jog_result_callback(self, msg: String) -> None:
        try:
            payload = json.loads(msg.data)
        except json.JSONDecodeError:
            self.get_logger().warn(f'Invalid {self.jog_result_topic} JSON received.')
            return
        if not isinstance(payload, dict):
            return

        request_id = str(payload.get('request_id') or '')
        if not request_id:
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
        if not request_id:
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
        if not request_id:
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
        if not request_id:
            return

        payload['_received_at'] = time.time()
        with self._motion_run_lock:
            self._motion_run_results[request_id] = payload
            status = payload.get('status')
            if isinstance(status, dict):
                self._motion_run_status = status
            cutoff = time.time() - 20.0
            stale_keys = [
                key for key, value in self._motion_run_results.items()
                if float(value.get('_received_at') or 0.0) < cutoff
            ]
            for key in stale_keys:
                self._motion_run_results.pop(key, None)
        if isinstance(status, dict):
            self._record_motion_run_transition(status)

    def _motion_run_status_callback(self, msg: String) -> None:
        try:
            payload = json.loads(msg.data)
        except json.JSONDecodeError:
            self.get_logger().warn(f'Invalid {self.motion_run_status_topic} JSON received.')
            return
        if not isinstance(payload, dict):
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
        if not request_id:
            return
        payload['_bridge_received_at'] = time.time()
        with self._midi_monitor_lock:
            self._midi_monitor_results[request_id] = payload
            if payload.get('success') and isinstance(payload.get('channels'), list):
                self._midi_monitor_status = dict(payload)
            cutoff = time.time() - 20.0
            stale_keys = [
                key for key, value in self._midi_monitor_results.items()
                if float(value.get('_bridge_received_at') or 0.0) < cutoff
            ]
            for key in stale_keys:
                self._midi_monitor_results.pop(key, None)

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

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            motion_state = self._motion_state
            received_at = self._motion_state_received_at
        with self._motion_run_lock:
            motion_run_status = dict(self._motion_run_status) if self._motion_run_status else {}
        with self._midi_monitor_lock:
            midi_monitor = dict(self._midi_monitor_status) if self._midi_monitor_status else {}
        midi_received_at = midi_monitor.pop('_bridge_received_at', None)
        if midi_received_at is not None and time.time() - float(midi_received_at) > 1.0:
            midi_monitor['connected'] = False
            midi_monitor['message'] = 'MIDI 모니터 노드 상태 수신 중단'

        return {
            'bridge_state': 'ok',
            'motion_state_topic': self.motion_state_topic,
            'motion_state_received_at': received_at,
            'motion_state_age_sec': None if received_at is None else round(time.time() - received_at, 3),
            'motion_test_limits': {
                'max_jog_delta_deg': self.max_jog_delta_deg,
            },
            'web_access': self._web_access,
            'motion_run_status': motion_run_status,
            'midi_monitor': midi_monitor,
            'motion_state': motion_state,
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
        path = self.event_log_dir / f'{now:%Y-%m-%d}.jsonl'
        try:
            with self._event_log_lock:
                self.event_log_dir.mkdir(parents=True, exist_ok=True)
                with path.open('a', encoding='utf-8') as stream:
                    stream.write(json.dumps(record, ensure_ascii=False, separators=(',', ':')))
                    stream.write('\n')
                self._prune_motor_event_logs()
        except OSError as error:
            self.get_logger().error(f'Failed to write motor event log {path}: {error}')
        return record

    def _prune_motor_event_logs(self) -> None:
        cutoff = datetime.now().astimezone().date() - timedelta(
            days=self.event_log_retention_days - 1
        )
        with self._event_log_lock:
            paths = sorted(self.event_log_dir.glob('*.jsonl'))
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

    def clear_motor_events(self) -> Dict[str, Any]:
        deleted_files = 0
        deleted_bytes = 0
        with self._event_log_lock:
            for path in self.event_log_dir.glob('*.jsonl'):
                try:
                    deleted_bytes += path.stat().st_size
                    path.unlink()
                    deleted_files += 1
                except OSError:
                    continue
        return {
            'success': True,
            'message': '모터 동작 로그를 삭제했습니다.',
            'deleted_files': deleted_files,
            'deleted_bytes': deleted_bytes,
        }

    def motor_events(self, limit: int = 200, category: str = 'all') -> Dict[str, Any]:
        safe_limit = max(1, min(int(limit), 1000))
        category_filter = str(category or 'all')
        events: List[Dict[str, Any]] = []
        with self._event_log_lock:
            paths = sorted(self.event_log_dir.glob('*.jsonl'), reverse=True)
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
            'count': len(events),
            'events': events,
            'retention_days': self.event_log_retention_days,
            'max_bytes': self.event_log_max_bytes,
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
        return self._call_scan_service(self._scan_client, self.scan_service, timeout_sec)

    def scan_ac_servo_motors(self, timeout_sec: float = 10.0) -> Dict[str, Any]:
        return self._call_scan_service(
            self._scan_ac_servo_client,
            self.scan_ac_servo_service,
            timeout_sec,
        )

    def scan_dynamixel_motors(self, timeout_sec: float = 20.0) -> Dict[str, Any]:
        return self._call_scan_service(
            self._scan_dynamixel_client,
            self.scan_dynamixel_service,
            timeout_sec,
        )

    def _call_scan_service(
        self,
        client,
        service_name: str,
        timeout_sec: float,
    ) -> Dict[str, Any]:
        if not client.wait_for_service(timeout_sec=0.2):
            return {
                'success': False,
                'message': f'scan service unavailable: {service_name}',
                'scan': None,
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
                **self.snapshot(),
            }

        response = future.result()
        scan = None
        try:
            scan = json.loads(response.message)
        except json.JSONDecodeError:
            self.get_logger().warn('Invalid scan JSON received.')

        return {
            'success': bool(response.success),
            'message': response.message,
            'scan': scan,
            **self.snapshot(),
        }

    def load_motor_config(self) -> Dict[str, Any]:
        if not self.motor_config_file.is_file():
            return {
                'success': False,
                'message': 'motor config YAML not found',
                'config_file': str(self.motor_config_file),
                'content': '',
                'registry': self._empty_motor_registry(),
            }

        try:
            content = self.motor_config_file.read_text(encoding='utf-8')
            config = yaml.safe_load(content) or {}
        except (OSError, yaml.YAMLError) as exc:
            return {
                'success': False,
                'message': f'failed to load motor config YAML: {exc}',
                'config_file': str(self.motor_config_file),
                'content': '',
                'registry': self._empty_motor_registry(),
            }

        return {
            'success': True,
            'message': 'motor config YAML loaded',
            'config_file': str(self.motor_config_file),
            'content': content,
            'registry': self._registry_from_motor_config(config),
        }

    def save_motor_config(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        try:
            target_file = self._motor_config_file_from_payload(payload)
            if 'content' in payload:
                content = str(payload.get('content') or '')
                config = yaml.safe_load(content) or {}
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

            self._write_motor_config(content, target_file)
        except (OSError, ValueError, yaml.YAMLError) as exc:
            return {
                'success': False,
                'message': f'failed to save motor config YAML: {exc}',
                'config_file': str(self.motor_config_file),
                'content': payload.get('content', ''),
                'registry': self._empty_motor_registry(),
            }

        result = self.load_motor_config()
        result['message'] = 'motor config YAML saved; restart motor_manager_node to apply'
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
            subprocess.Popen(
                ['/bin/bash', str(self.restart_script)],
                cwd=str(self.restart_script.parent.parent),
                start_new_session=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError as exc:
            return {
                'success': False,
                'message': f'failed to start restart script: {exc}',
                'restart_script': str(self.restart_script),
                **self.snapshot(),
            }

        return {
            'success': True,
            'message': 'YAML apply restart started; web will reconnect shortly',
            'restart_script': str(self.restart_script),
            **self.snapshot(),
        }

    def list_motion_mappings(self) -> Dict[str, Any]:
        return self._request_motion_mapping('list', {})

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
        result = self._request_motion_mapping('save', payload)
        if result.get('success') is False:
            return result

        saved_file_id = self._motion_mapping_file_id(result)
        if saved_file_id:
            # The mapping manager preserved the file-owned MIDI block. Reload
            # that verified file state instead of asking MIDI to write YAML.
            midi_result = self._load_and_apply_midi_banks(saved_file_id)
            result['midi_banks'] = midi_result
            if midi_result.get('success') is False:
                result['success'] = False
                result['message'] = (
                    '모션축 매칭은 저장됐지만 MIDI 뱅크 적용에 실패했습니다: '
                    f"{midi_result.get('message') or 'unknown error'}"
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
        applied['message'] = '모션축 매칭 파일의 MIDI 뱅크를 노드에 적용했습니다'
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
        return self._request_motion_mapping('delete', {'file_id': file_id})

    def _request_motion_mapping(
        self,
        command: str,
        payload: Dict[str, Any],
        timeout_sec: float = 2.0,
    ) -> Dict[str, Any]:
        request_id = f'mapping-{time.time_ns()}'
        msg = String()
        msg.data = json.dumps({
            'request_id': request_id,
            'command': command,
            'payload': payload if isinstance(payload, dict) else {},
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

    def motion_run_initialize(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self._request_motion_run('initialize', payload, timeout_sec=2.0)

    def motion_run_start(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self._request_motion_run('start', payload, timeout_sec=2.0)

    def motion_run_stop(self) -> Dict[str, Any]:
        return self._request_motion_run('stop', {}, timeout_sec=2.0)

    def midi_monitor_status(self) -> Dict[str, Any]:
        result = self._request_midi_monitor('status', {}, timeout_sec=1.0)
        if result.get('success') is False:
            with self._midi_monitor_lock:
                cached = dict(self._midi_monitor_status) if self._midi_monitor_status else {}
            cached.pop('_bridge_received_at', None)
            if cached:
                return cached
        return result

    def save_midi_monitor_mapping(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        updated = self._request_midi_monitor('update_bank', payload, timeout_sec=2.0)
        return self._persist_midi_bank_result(updated)

    def create_midi_bank(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self._request_midi_monitor('create_bank', payload, timeout_sec=2.0)

    def select_midi_bank(self, bank_id: str) -> Dict[str, Any]:
        return self._request_midi_monitor(
            'select_bank',
            {'bank_id': bank_id},
            timeout_sec=2.0,
        )

    def update_midi_bank(self, bank_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        updated = self._request_midi_monitor(
            'update_bank',
            {**payload, 'bank_id': bank_id},
            timeout_sec=2.0,
        )
        return self._persist_midi_bank_result(updated)

    def delete_midi_bank(self, bank_id: str) -> Dict[str, Any]:
        return self._request_midi_monitor(
            'delete_bank',
            {'bank_id': bank_id},
            timeout_sec=2.0,
        )

    def save_midi_banks_to_file(self) -> Dict[str, Any]:
        return self._persist_midi_bank_result(self.midi_monitor_status())

    def load_midi_banks_from_file(self) -> Dict[str, Any]:
        status = self.midi_monitor_status()
        if status.get('success') is False:
            return status
        file_id = self._midi_mapping_file_id(status)
        if not file_id:
            return {'success': False, 'message': '선택된 모션축 매칭 파일이 없습니다'}
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
            return {'success': False, 'message': '선택된 모션축 매칭 파일이 없습니다'}
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
        applied['message'] = 'MIDI 뱅크 설정을 모션축 매칭 파일에 저장하고 노드에 적용했습니다'
        return applied

    def _request_midi_monitor(
        self,
        command: str,
        payload: Dict[str, Any],
        timeout_sec: float = 2.0,
    ) -> Dict[str, Any]:
        request_id = f'midi-{time.time_ns()}'
        msg = String()
        msg.data = json.dumps({
            'request_id': request_id,
            'command': command,
            'payload': payload if isinstance(payload, dict) else {},
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
        request_id = f'run-{time.time_ns()}'
        msg = String()
        msg.data = json.dumps({
            'request_id': request_id,
            'command': command,
            'payload': payload if isinstance(payload, dict) else {},
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

    def list_motion_files(self) -> Dict[str, Any]:
        self._ensure_motion_file_dir()
        files = []
        for path in sorted(
            (
                item for item in self.motion_files_dir.iterdir()
                if item.is_file() and item.suffix.lower() == '.json'
            ),
            key=lambda item: item.stat().st_mtime if item.exists() else 0.0,
            reverse=True,
        ):
            files.append(self._motion_file_entry(path, include_detail=False))
        return {
            'success': True,
            'message': 'motion files loaded',
            'motion_data_dir': str(self.motion_data_dir),
            'files_dir': str(self.motion_files_dir),
            'files': files,
        }

    def upload_motion_file(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        filename = str(payload.get('filename') or '').strip()
        content = payload.get('content')
        if not filename:
            return {
                **self.list_motion_files(),
                'success': False,
                'message': 'filename is required',
            }
        if not isinstance(content, str) or not content.strip():
            return {
                **self.list_motion_files(),
                'success': False,
                'message': 'content is required',
            }
        if len(content.encode('utf-8')) > MOTION_FILE_SIZE_LIMIT_BYTES:
            return {
                **self.list_motion_files(),
                'success': False,
                'message': 'motion JSON file is too large',
            }

        analysis = self._analyze_motion_json(content, include_records=True)
        if not analysis.get('format_valid'):
            return {
                **self.list_motion_files(),
                'success': False,
                'message': analysis.get('message') or 'invalid motion data file',
                'analysis': analysis,
            }

        self._ensure_motion_file_dir()
        target = self._new_motion_file_path(filename)
        try:
            target.write_text(content, encoding='utf-8')
        except OSError as exc:
            return {
                **self.list_motion_files(),
                'success': False,
                'message': f'failed to save motion JSON: {exc}',
            }

        analysis_valid = bool(analysis.get('valid'))
        return {
            **self.list_motion_files(),
            'success': True,
            'message': (
                'motion JSON uploaded and saved'
                if analysis_valid
                else 'motion JSON uploaded and saved; validation errors found'
            ),
            'validation_success': analysis_valid,
            'file': self._motion_file_entry(target, include_detail=True),
        }

    def load_motion_file(self, file_id: Any) -> Dict[str, Any]:
        try:
            path = self._motion_file_path(file_id)
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

    def delete_motion_file(self, file_id: Any) -> Dict[str, Any]:
        try:
            path = self._motion_file_path(file_id)
            path.unlink()
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
        }

    def _ensure_motion_file_dir(self) -> None:
        self.motion_files_dir.mkdir(parents=True, exist_ok=True)

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
            entry['content_preview'] = content[:12000]
        return entry

    def _motion_file_path(self, file_id: Any) -> Path:
        name = str(file_id or '').strip()
        if not name:
            raise ValueError('file_id is required')
        if name != Path(name).name or '/' in name or '\\' in name:
            raise ValueError('invalid motion file id')
        path = self.motion_files_dir / name
        if not path.is_file():
            raise ValueError(f'motion file not found: {name}')
        return path

    def _new_motion_file_path(self, filename: str) -> Path:
        safe = self._safe_motion_filename(filename)
        date_text = time.strftime('%Y%m%d')
        source = Path(safe)
        stem = source.stem or 'motion'
        suffix = source.suffix or '.json'
        target = self.motion_files_dir / f'{stem}_{date_text}{suffix}'
        counter = 1
        while target.exists():
            counter += 1
            target = self.motion_files_dir / f'{stem}_{date_text}_{counter}{suffix}'
        return target

    @staticmethod
    def _safe_motion_filename(filename: str) -> str:
        name = Path(filename).name.strip()
        if not name:
            name = 'motion.json'
        cleaned = ''.join(
            char if char.isalnum() or char in ('-', '_', '.') else '_'
            for char in name
        ).strip('._')
        if not cleaned:
            cleaned = 'motion'
        if not cleaned.lower().endswith('.json'):
            cleaned = f'{cleaned}.json'
        return cleaned

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

        request_id = f'jog-{time.time_ns()}'
        payload = {
            'request_id': request_id,
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

        return {
            'success': bool(result.get('success')),
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

        request_id = f'dynamixel-jog-{time.time_ns()}'
        payload = {
            'request_id': request_id,
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

        return {
            'success': bool(result.get('success')),
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

        request_id = f'ac-servo-action-{time.time_ns()}'
        payload = {
            'request_id': request_id,
            'command': 'ac_servo_absolute_move',
            'axis': axis_value,
            'target_deg': target_value,
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

        request_id = f'dynamixel-action-{time.time_ns()}'
        payload = {
            'request_id': request_id,
            'command': 'dynamixel_absolute_move',
            'axis': axis_value,
            'target_deg': target_value,
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

        request_id = f'ac-servo-control-{time.time_ns()}'
        payload = {
            'request_id': request_id,
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

    def _read_current_motor_config(self) -> Dict[str, Any]:
        if not self.motor_config_file.is_file():
            return self._default_motor_config()
        content = self.motor_config_file.read_text(encoding='utf-8')
        config = yaml.safe_load(content) or {}
        return config if isinstance(config, dict) else self._default_motor_config()

    def _motor_config_file_from_payload(self, payload: Dict[str, Any]) -> Path:
        requested = str(payload.get('file_name') or '').strip()
        if not requested:
            return self.motor_config_file

        name = Path(requested).name.strip()
        if not name or name in ('.', '..'):
            raise ValueError('motor config file name is empty')
        if not name.lower().endswith(('.yaml', '.yml')):
            name = f'{name}.yaml'

        config_dir = self.motor_config_file.parent.resolve()
        target = (config_dir / name).resolve()
        try:
            target.relative_to(config_dir)
        except ValueError as exc:
            raise ValueError('motor config file must stay under config directory') from exc
        return target

    def _write_motor_config_selection(self, path: Path) -> None:
        self.motor_config_selection_file.parent.mkdir(parents=True, exist_ok=True)
        self.motor_config_selection_file.write_text(str(path) + '\n', encoding='utf-8')

    def _write_motor_config(self, content: str, target_file: Optional[Path] = None) -> None:
        target = target_file or self.motor_config_file
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.is_file():
            timestamp = time.strftime('%Y%m%d-%H%M%S')
            backup = target.with_suffix(f'{target.suffix}.bak-{timestamp}')
            backup.write_text(target.read_text(encoding='utf-8'), encoding='utf-8')
        target.write_text(content.rstrip() + '\n', encoding='utf-8')
        self.motor_config_file = target
        self._write_motor_config_selection(target)

    @staticmethod
    def _default_motor_config() -> Dict[str, Any]:
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
                    'driver_model': 'MADLN05BE',
                    'pulse_per_revolution': 8388608,
                    'rated_effort': 0.16,
                    'unit_effort': 0.1,
                    'rated_current': 1.1,
                    'rated_power_w': 50,
                    'rated_speed_rpm': 3000,
                    'lower': -27000.0,
                    'upper': 27000.0,
                    'speed': 2000000.0,
                    'acceleration': 0.37450702829239285,
                    'deceleration': 0.37450702829239285,
                    'profile_velocity': 0.037450702829239284,
                    'profile_acceleration': 0.37450702829239285,
                    'profile_deceleration': 0.37450702829239285,
                    'profile_position_value': 1,
                    'profile_velocity_value': 3,
                    'profile_effort_value': 4,
                    'type': 'minas',
                    'param_file': '/home/joonho_test/ros2_ws/src/motion_system/ros2/motion_system_ros2/motion_control_bridge/param',
                },
            ],
        }

    def _registry_from_motor_config(self, config: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(config, dict):
            config = {}
        drivers_by_id = {
            int(driver.get('id')): driver
            for driver in config.get('drivers', [])
            if isinstance(driver, dict) and driver.get('id') is not None
        }

        motors: List[Dict[str, Any]] = []
        for master in config.get('masters', []):
            if not isinstance(master, dict):
                continue
            transport = str(master.get('type') or 'unknown')
            serial_port = master.get('serial_port')
            serial_baudrate = self._optional_int(master.get('serial_baudrate'), None)
            for index, slave in enumerate(master.get('slaves', [])):
                if not isinstance(slave, dict):
                    continue
                driver_id = self._optional_int(slave.get('driver_id'), 0)
                driver = drivers_by_id.get(driver_id, {})
                driver_family = str(driver.get('type') or 'unknown')
                motor_type = 'ac_servo' if driver_family == 'minas' else driver_family
                axis = self._optional_int(slave.get('controller_index'), index)
                alias = self._optional_int(slave.get('alias'), None)
                bus_id = self._optional_int(slave.get('bus_id'), None)
                name = str(slave.get('name') or f'Axis {axis}')
                motor_id = (
                    f'{motor_type}_{transport}_alias_{alias}'
                    if alias is not None
                    else f'{motor_type}_{transport}_id_{bus_id}'
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
                                'rotary_alias': None,
                                'ethercat_alias': alias,
                                'node_id': bus_id,
                                'bus_id': bus_id,
                                'serial_port': serial_port,
                                'serial_baudrate': serial_baudrate,
                                'slave_position': slave.get('position'),
                                'driver_model': driver.get('driver_model', ''),
                            },
                            'config': {
                                'controller_index': axis,
                                'driver_id': driver_id,
                                'bus_id': bus_id,
                                'serial_port': serial_port,
                                'serial_baudrate': serial_baudrate,
                                'alias': alias,
                                'position': self._optional_int(slave.get('position'), 0),
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

        ethercat_master = next((master for master in masters if master.get('type') == 'ethercat'), None)
        if ethercat_master is None:
            ethercat_master = {
                'id': 0,
                'type': 'ethercat',
                'ethercat_master_index': 0,
            }
            masters.append(ethercat_master)

        slaves = []
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
            slaves.append(
                {
                    'controller_index': axis,
                    'name': name,
                    'driver_id': self._optional_int(motor_config.get('driver_id'), 0),
                    'alias': self._optional_int(motor_config.get('alias'), 0),
                    'position': self._optional_int(motor_config.get('position'), 0),
                    'vendor_id': self._optional_int(motor_config.get('vendor_id'), 0x0000066F),
                    'product_id': self._optional_int(motor_config.get('product_id'), 0x60380004),
                    'profile_mode': self._optional_int(motor_config.get('profile_mode'), 0),
                }
            )

        slaves.sort(key=lambda item: int(item.get('controller_index') or 0))
        ethercat_master['slaves'] = slaves
        ethercat_master['number_of_slaves'] = len(slaves)

        serial_masters = self._serial_masters_from_registry(registry, masters, drivers)
        non_serial_masters = [master for master in masters if master.get('type') != 'serial']
        masters = non_serial_masters + serial_masters

        config['masters'] = masters
        config['drivers'] = self._prune_unused_drivers(
            self._normalize_driver_configs(drivers),
            masters,
        )
        return config

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

    @staticmethod
    def _dynamixel_param_file_for_model(driver_model: str) -> str:
        model = driver_model.lower().replace('_', '-')
        if 'xm540-w150' in model:
            return '/home/joonho_test/ros2_ws/config/dynamixel_xm540_w150.yaml'
        return '/home/joonho_test/ros2_ws/config/dynamixel_xm540_w270.yaml'

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
        identity = motor.get('identity') if isinstance(motor.get('identity'), dict) else {}
        driver_type = str(motor.get('driver_family') or motor.get('motor_type') or 'unknown')
        driver_model = str(identity.get('driver_model') or '').strip()
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
        template = next(
            (
                dict(driver)
                for driver in drivers
                if isinstance(driver, dict) and str(driver.get('type') or '') == driver_type
            ),
            None,
        )
        if template is None and driver_type == 'dynamixel':
            template = self._default_dynamixel_driver()
        elif template is None and driver_type == 'minas':
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
        if driver_model:
            template['driver_model'] = driver_model
        elif not template.get('driver_model'):
            template['driver_model'] = driver_type
        if driver_type == 'dynamixel':
            template['param_file'] = self._dynamixel_param_file_for_model(
                str(template.get('driver_model') or '')
            )
        drivers.append(template)
        return next_driver_id

    @staticmethod
    def _default_dynamixel_driver() -> Dict[str, Any]:
        return {
            'driver_model': 'Dynamixel',
            'pulse_per_revolution': 4096,
            'rated_effort': 1.0,
            'unit_effort': 0.00269,
            'rated_current': 1.0,
            'rated_speed_rpm': 30,
            'lower': -180.0,
            'upper': 180.0,
            'speed': 100.0,
            'acceleration': 100.0,
            'deceleration': 100.0,
            'profile_velocity': 100.0,
            'profile_acceleration': 100.0,
            'profile_deceleration': 100.0,
            'profile_position_value': 3,
            'profile_velocity_value': 1,
            'profile_effort_value': 0,
            'type': 'dynamixel',
            'param_file': '/home/joonho_test/ros2_ws/config/dynamixel_xm540_w270.yaml',
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
        identity = motor.get('identity') if isinstance(motor.get('identity'), dict) else {}
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


def create_app(bridge: MotionWebBridge) -> FastAPI:
    app = FastAPI(title='Motion Web Bridge')
    ui_share = Path(get_package_share_directory('motion_web_ui')) / 'static'
    @app.get('/')
    async def index():
        return FileResponse(str(ui_share / 'index.html'))

    @app.get('/static/{asset_path:path}')
    async def static_asset(asset_path: str):
        relative_path = Path(asset_path)
        if relative_path.is_absolute() or '..' in relative_path.parts:
            raise HTTPException(status_code=404, detail='Not Found')
        asset = ui_share / relative_path
        if not asset.is_file():
            raise HTTPException(status_code=404, detail='Not Found')
        return FileResponse(str(asset))

    @app.get('/api/status')
    async def status():
        return bridge.snapshot()

    @app.get('/api/motor-events')
    async def motor_events(limit: int = 200, category: str = 'all'):
        return bridge.motor_events(limit=limit, category=category)

    @app.delete('/api/motor-events')
    async def clear_motor_events():
        return bridge.clear_motor_events()

    @app.post('/api/monitoring/enabled')
    async def set_monitoring(request: Request):
        body = await request.json()
        enabled = bool(body.get('enabled', True))
        return bridge.set_monitoring(enabled)

    @app.post('/api/motors/scan')
    async def scan_motors():
        return bridge.scan_motors()

    @app.post('/api/motors/scan/ac-servo')
    async def scan_ac_servo_motors():
        return bridge.scan_ac_servo_motors()

    @app.post('/api/motors/scan/dynamixel')
    async def scan_dynamixel_motors():
        return bridge.scan_dynamixel_motors()

    @app.get('/api/motor-config')
    async def motor_config():
        return bridge.load_motor_config()

    @app.put('/api/motor-config')
    async def save_motor_config(request: Request):
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail='request body must be an object')
        return bridge.save_motor_config(body)

    @app.post('/api/motor-config/apply')
    async def apply_motor_config():
        return bridge.apply_motor_config()

    @app.get('/api/motion-files')
    async def motion_files():
        return bridge.list_motion_files()

    @app.post('/api/motion-files/upload')
    async def upload_motion_file(request: Request):
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail='request body must be an object')
        return bridge.upload_motion_file(body)

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
        return bridge.motion_run_check(body)

    @app.post('/api/motion-run/initialize')
    async def motion_run_initialize(request: Request):
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail='request body must be an object')
        return bridge.motion_run_initialize(body)

    @app.post('/api/motion-run/start')
    async def motion_run_start(request: Request):
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail='request body must be an object')
        return bridge.motion_run_start(body)

    @app.post('/api/motion-run/stop')
    async def motion_run_stop():
        return bridge.motion_run_stop()

    @app.get('/api/midi-monitor')
    async def midi_monitor_status():
        return bridge.midi_monitor_status()

    @app.put('/api/midi-monitor/mapping')
    async def save_midi_monitor_mapping(request: Request):
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail='request body must be an object')
        return bridge.save_midi_monitor_mapping(body)

    @app.post('/api/midi-monitor/banks')
    async def create_midi_bank(request: Request):
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail='request body must be an object')
        return bridge.create_midi_bank(body)

    @app.post('/api/midi-monitor/banks/{bank_id}/select')
    async def select_midi_bank(bank_id: str):
        return bridge.select_midi_bank(bank_id)

    @app.put('/api/midi-monitor/banks/{bank_id}')
    async def update_midi_bank(bank_id: str, request: Request):
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail='request body must be an object')
        return bridge.update_midi_bank(bank_id, body)

    @app.delete('/api/midi-monitor/banks/{bank_id}')
    async def delete_midi_bank(bank_id: str):
        return bridge.delete_midi_bank(bank_id)

    @app.post('/api/midi-monitor/banks/file/save')
    async def save_midi_banks_to_file():
        return bridge.save_midi_banks_to_file()

    @app.post('/api/midi-monitor/banks/file/load')
    async def load_midi_banks_from_file():
        return bridge.load_midi_banks_from_file()

    @app.post('/api/midi-monitor/runtime/reset')
    async def reset_midi_runtime_values():
        return bridge.reset_midi_runtime_values()

    @app.post('/api/midi-monitor/device/connect')
    async def connect_midi_device():
        return bridge.connect_midi_device()

    @app.post('/api/midi-monitor/device/disconnect')
    async def disconnect_midi_device():
        return bridge.disconnect_midi_device()

    @app.post('/api/motion-test/ac-servo/jog')
    async def ac_servo_jog(request: Request):
        body = await request.json()
        return bridge.request_ac_servo_jog(
            body.get('axis'),
            body.get('relative_deg'),
        )

    @app.post('/api/motion-test/dynamixel/jog')
    async def dynamixel_jog(request: Request):
        body = await request.json()
        return bridge.request_dynamixel_jog(
            body.get('axis'),
            body.get('relative_deg'),
        )

    @app.post('/api/motion-test/ac-servo/action')
    async def ac_servo_action(request: Request):
        body = await request.json()
        return bridge.request_ac_servo_action(
            body.get('axis'),
            body.get('target_deg'),
            body.get('duration_sec'),
        )

    @app.post('/api/motion-test/dynamixel/action')
    async def dynamixel_action(request: Request):
        body = await request.json()
        return bridge.request_dynamixel_action(
            body.get('axis'),
            body.get('target_deg'),
            body.get('duration_sec'),
        )

    @app.post('/api/motion-test/ac-servo/control')
    async def ac_servo_control(request: Request):
        body = await request.json()
        return bridge.request_ac_servo_control(
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
                await asyncio.sleep(period_sec)
        except WebSocketDisconnect:
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
