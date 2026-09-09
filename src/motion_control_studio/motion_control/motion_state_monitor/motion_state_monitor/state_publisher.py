"""모터 상태 수신과 발행 · 축 목록 구성.

`MotionStateMonitor`에서 떼어냈다 · §5 분해 목표안의 `StatePublisher` · §6-36

수신한 축 상태와 그 시각을 **이 객체가 갖는다** · 노드는 ROS 개체(발행자·QoS)만
만들고 여기에 빌려준다. 노드가 사서함 노릇을 하지 않도록 판정과 목록 구성은
전부 이쪽에 있다.

연결 판정은 `connection_state`, 값 변환은 `motor_values`에 있다 · 여기서는
그 둘을 엮어 발행 형태로 만든다.
"""

from __future__ import annotations

import json
import time
from copy import deepcopy
from typing import Any, Dict, List, Optional

from motion_control_msgs.msg import MotorStatus
from std_msgs.msg import String

from .connection_state import (
    connection_summary,
    set_connection_fields,
    set_physical_connection_fields,
)
from .motor_values import (
    MOTOR_TYPE_LABELS,
    array_value,
    count_values,
    dynamixel_position_raw,
    dynamixel_statusword_text,
    error_text,
    hex16,
    motor_type_label,
    normalized_errorcode,
    statusword_text,
    transport_label,
)

MOTOR_TYPE_CATALOG = [
    {'type': 'minas', 'label': MOTOR_TYPE_LABELS['minas']},
    {'type': 'zeroerr', 'label': MOTOR_TYPE_LABELS['zeroerr']},
    {'type': 'dynamixel', 'label': MOTOR_TYPE_LABELS['dynamixel']},
    {'type': 'cubemars', 'label': MOTOR_TYPE_LABELS['cubemars']},
    {'type': 'unknown', 'label': MOTOR_TYPE_LABELS['unknown']},
]

#: 제어 노드가 통신 불가를 알릴 때 쓰는 오류 코드
COMMUNICATION_UNAVAILABLE_ERROR = 0xFFFF


class StatePublisher:
    def __init__(self, monitor: Any) -> None:
        self.monitor = monitor
        #: controller_index -> 마지막 수신 상태
        self.motors: Dict[int, Dict[str, Any]] = {}
        #: 통신 불가 직전의 정상 상태 · 확정 전까지 이것을 쓴다
        self.last_healthy_motors: Dict[int, Dict[str, Any]] = {}
        self.last_status_at: Optional[float] = None
        self.last_processed_at: Optional[float] = None
        self.last_disabled_publish_at = 0.0
        self.started_at = time.time()
        self.subscription = None

    def _create_input_subscription(self) -> None:
        if self.subscription is None:
            self.subscription = self.monitor.create_subscription(
                MotorStatus,
                self.monitor.input_topic,
                self._motor_status_callback,
                self.monitor._input_qos,
            )

    def _destroy_motor_status_subscription(self) -> None:
        if self.subscription is not None:
            self.monitor.destroy_subscription(self.subscription)
            self.subscription = None

    def _motor_status_callback(self, msg: MotorStatus) -> None:
        if not self.monitor.monitoring_enabled:
            return

        now = time.time()
        self.last_status_at = now
        process_hz = max(float(getattr(self.monitor, 'feedback_process_hz', 0.0)), 0.0)
        last_processed_at = getattr(self, 'last_processed_at', None)
        if (
            process_hz > 0.0
            and last_processed_at is not None
            and now - float(last_processed_at) < 1.0 / process_hz
        ):
            return
        self.last_processed_at = now

        controller_indices = list(getattr(msg, 'controller_index', []))
        count = min(len(controller_indices), self.monitor.max_motors)
        if len(controller_indices) > self.monitor.max_motors:
            self.monitor.get_logger().warn(
                f'{self.monitor.input_topic} contains {len(controller_indices)} motors; '
                f'only first {self.monitor.max_motors} are monitored.'
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
            self.monitor._health.update(
                controller_index,
                communication_unavailable,
                now,
            )
            if not communication_unavailable:
                self.last_healthy_motors[controller_index] = deepcopy(motor)
            self.motors[controller_index] = motor

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
                'configured' if controller_index in self.monitor._motor_metadata else 'unconfigured'
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
        if not self.monitor.monitoring_enabled:
            if now - self.last_disabled_publish_at < 1.0:
                return
            self.last_disabled_publish_at = now

        motors = self._current_motor_list(now)
        state = {
            'schema_version': 1,
            'project_id': getattr(self.monitor, 'project_id', ''),
            'project_generation': int(getattr(self.monitor, 'project_generation', 0) or 0),
            'generated_at': now,
            'monitoring_enabled': self.monitor.monitoring_enabled,
            'source_topic': self.monitor.input_topic,
            'ethercat_status_topic': self.monitor.ethercat_status_topic,
            'last_motor_status_at': self.last_status_at,
            'last_ethercat_status_at': self.monitor._ethercat.last_status_at,
            'ethercat': self.monitor._ethercat._current_ethercat_status(now),
            'motor_type_catalog': MOTOR_TYPE_CATALOG,
            'stale_timeout_sec': self.monitor.stale_timeout_sec,
            'disconnected_timeout_sec': self.monitor.disconnected_timeout_sec,
            'max_motors': self.monitor.max_motors,
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
        self.monitor._publisher.publish(msg)

    def _current_motor_list(self, now: float) -> List[Dict[str, Any]]:
        motors: List[Dict[str, Any]] = []
        configured_indices = set(self.monitor._motor_metadata)
        if not configured_indices:
            return motors

        ethercat_available = bool(self.monitor._ethercat.status)
        for controller_index in sorted(configured_indices):
            if controller_index not in self.motors:
                if not self.monitor.monitoring_enabled:
                    state = 'monitoring_off'
                    reason = 'monitoring_disabled'
                    source = 'monitor'
                elif now - self.started_at < self.monitor.disconnected_timeout_sec:
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
                    getattr(self.monitor, '_last_ethercat_physical_scan', {}),
                )
                motors.append(motor)
                continue

            motor = deepcopy(self.motors[controller_index])
            health = self.monitor._health.entries.get(controller_index, {})
            raw_communication_unavailable = (
                int(motor.get('errorcode_raw') or 0) == COMMUNICATION_UNAVAILABLE_ERROR
            )
            communication_unavailable = bool(health.get('confirmed_offline', False))
            if (
                raw_communication_unavailable
                and not communication_unavailable
                and controller_index in self.last_healthy_motors
            ):
                motor = deepcopy(self.last_healthy_motors[controller_index])
            age = now - float(motor.get('last_seen_at', now))
            motor['age_sec'] = round(age, 3)
            transport = str(motor.get('transport', '')).lower()
            ethercat_axis_state = (
                self.monitor._ethercat._ethercat_axis_state(motor)
                if transport == 'ethercat' and ethercat_available
                else ''
            )
            ethercat_master_status = (
                self.monitor._ethercat._ethercat_master_status(motor)
                if transport == 'ethercat' and ethercat_available
                else {}
            )
            ethercat_down = transport == 'ethercat' and ethercat_available and (
                not ethercat_master_status.get('available', False)
                or not ethercat_master_status.get('master_active', False)
                or not ethercat_master_status.get('link_up', False)
            )
            if not self.monitor.monitoring_enabled:
                state = 'monitoring_off'
                reason = 'monitoring_disabled'
                source = 'monitor'
            elif communication_unavailable:
                state = 'disconnected'
                reason = 'communication_unavailable'
                source = 'runtime_error'
            elif raw_communication_unavailable and controller_index not in self.last_healthy_motors:
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
            elif age >= self.monitor.disconnected_timeout_sec:
                state = 'disconnected'
                reason = 'feedback_timeout'
                source = 'runtime_topic'
            elif age >= self.monitor.stale_timeout_sec:
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
                    getattr(self.monitor, '_last_ethercat_physical_scan', {}),
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
        for controller_index in self.monitor._motor_metadata:
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

    def _metadata_for(self, controller_index: int) -> Dict[str, Any]:
        metadata = deepcopy(self.monitor._motor_metadata.get(controller_index, {}))
        motor_type = str(metadata.get('motor_type', 'unknown'))
        transport = str(metadata.get('transport', 'unknown'))
        metadata['motor_type'] = motor_type
        metadata['motor_type_label'] = motor_type_label(motor_type)
        metadata['transport'] = transport
        metadata['transport_label'] = transport_label(transport)
        return metadata

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
