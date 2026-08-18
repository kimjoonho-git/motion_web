"""DDS-only multi-PC group coordination node.

The node exposes only typed high-level group messages to the LAN. Local motion
validation and control remain on the loopback Web Bridge API so motor commands
continue to use motion_run_manager -> motion_supervisor -> motion_system.
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

import rclpy
from motion_coordination_interfaces.msg import (
    GroupAlarm,
    GroupCommand,
    GroupEvent,
    GroupHeartbeat,
    GroupTimeSync,
    GroupSystemInfo,
)
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
from .alarm_registry import AlarmRegistry
from .command_dispatcher import CommandDispatcher
from .group_configuration import (
    GroupConfig,
    load_group_config,
    migrate_legacy_group_config,
)
from .group_execution import GroupExecution, Member, MemberRegistry, ScheduledAction
from .group_peer_display import enrich_peer_row
from .local_api import LocalCoordinationApi
from .local_runtime_monitor import LocalRuntimeMonitor
from .safety_stop import SafetyStopController, SafetyStopOutcome
from .trigger_sync import (
    TriggerSyncEstimator,
    coordinator_to_local_ns,
    local_to_coordinator_ns,
)


MAX_LOCAL_BODY_BYTES = 64 * 1024
LOCAL_RUNTIME_ACTIVE_TIMEOUT_SEC = 0.5


def _stamp_to_float(stamp: Any) -> float:
    return float(stamp.sec) + (float(stamp.nanosec) / 1_000_000_000.0)


def _set_stamp(stamp: Any, value: float) -> None:
    seconds = max(float(value), 0.0)
    stamp.sec = int(seconds)
    stamp.nanosec = int(round((seconds - int(seconds)) * 1_000_000_000.0))
    if stamp.nanosec >= 1_000_000_000:
        stamp.sec += 1
        stamp.nanosec = 0


def _message_uint32(message: Any, field_name: str, default: int = 0) -> int:
    return int(getattr(message, field_name, default) or 0)


def _set_optional_message_field(message: Any, field_name: str, value: Any) -> None:
    if hasattr(message, field_name):
        setattr(message, field_name, value)


class MotionCoordinationNode(Node):
    def __init__(self, config: Optional[GroupConfig] = None) -> None:
        super().__init__('motion_group_coordinator')
        workspace = Path(os.environ.get('MOTION_WORKSPACE') or Path.cwd()).resolve()
        config_path = Path(
            os.environ.get('MOTION_COORDINATION_CONFIG')
            or workspace / 'config/motion_coordination.yaml'
        ).expanduser()
        self._config = config or load_group_config(config_path)
        self._boot_id = f'boot-{uuid.uuid4().hex}'
        self._joined = bool(self._config.configured)
        self._sequence = 0
        self._git_branch = ''
        self._git_hash = ''
        self._git_message = ''
        try:
            cwd = os.environ.get('MOTION_WORKSPACE', os.getcwd())
            self._git_branch = subprocess.check_output(['git', 'rev-parse', '--abbrev-ref', 'HEAD'], cwd=cwd).decode('utf-8').strip()
            self._git_hash = subprocess.check_output(['git', 'rev-parse', '--short', 'HEAD'], cwd=cwd).decode('utf-8').strip()
            self._git_message = subprocess.check_output(['git', 'log', '-1', '--format=%s'], cwd=cwd).decode('utf-8').strip()
        except Exception:
            pass
        self._lock = threading.RLock()
        self._trigger_sync_status: Dict[str, Any] = {
            'trigger_sync_state': 'idle',
            'trigger_sync_uncertainty_ms': 0.0,
            'trigger_sync_source': 'dds_relative_monotonic',
        }
        self._auto_play_triggered = False
        self._local_sync_offset_ns = 0
        self._sync_estimators: Dict[str, TriggerSyncEstimator] = {}
        self._sync_sent_samples: Dict[str, int] = {}
        self._sync_probes: Dict[tuple[str, int], int] = {}
        self._sync_ready: set[str] = set()
        self._sync_next_action = ''
        self._sync_deadline = 0.0
        self._sync_last_probe_at = 0.0
        self._local_status: Dict[str, Any] = {}
        self._last_local_event_key: tuple[Any, ...] = ()
        self._last_alarm_key: tuple[Any, ...] = ()
        self._alarm_registry = AlarmRegistry()
        self._seen_commands: Dict[str, float] = {}
        self._system_info_cache: Dict[str, GroupSystemInfo] = {}
        self._cancelled_execution_ids: set[str] = set()
        self._coordination_error: Dict[str, Any] = {}
        self._duplicate_pc_boot_id = ''
        self._registry = MemberRegistry(
            warning_timeout_sec=self._config.warning_timeout_sec,
            timeout_sec=self._config.peer_timeout_sec,
        )
        self._execution = GroupExecution(start_lead_sec=self._config.start_lead_sec)
        # One object owns both execution state and its transient command lease.
        self._safety_stop = SafetyStopController()
        local_web_port = int(os.environ.get('MOTION_WEB_BRIDGE_PORT') or 8000)
        self._local_web_base_url = f'http://127.0.0.1:{local_web_port}'
        self._local_runtime_monitor = LocalRuntimeMonitor(
            self._fetch_local_runtime_status,
            active_interval_sec=0.05,
            idle_interval_sec=min(self._config.heartbeat_sec, 0.5),
        )
        self._local_runtime_monitor.start()
        self._command_dispatcher = CommandDispatcher(self._process_group_command)
        self._command_dispatcher.start()

        reliable = QoSProfile(depth=32, reliability=ReliabilityPolicy.RELIABLE)
        heartbeat_qos = QoSProfile(
            depth=8,
            reliability=ReliabilityPolicy.RELIABLE,
            durability=DurabilityPolicy.TRANSIENT_LOCAL,
        )
        alarm_qos = QoSProfile(
            depth=16,
            reliability=ReliabilityPolicy.RELIABLE,
            durability=DurabilityPolicy.TRANSIENT_LOCAL,
        )
        system_info_qos = QoSProfile(
            depth=16,
            reliability=ReliabilityPolicy.RELIABLE,
            durability=DurabilityPolicy.TRANSIENT_LOCAL,
        )
        self._heartbeat_pub = self.create_publisher(
            GroupHeartbeat, '/motion_group/heartbeat', heartbeat_qos
        )
        self._system_info_pub = self.create_publisher(
            GroupSystemInfo, '/motion_group/system_info', system_info_qos
        )
        self._command_pub = self.create_publisher(
            GroupCommand, '/motion_group/command', reliable
        )
        self._event_pub = self.create_publisher(
            GroupEvent, '/motion_group/event', reliable
        )
        self._alarm_pub = self.create_publisher(
            GroupAlarm, '/motion_group/alarm', alarm_qos
        )
        self._time_sync_pub = self.create_publisher(
            GroupTimeSync, '/motion_group/time_sync', reliable
        )
        self._heartbeat_sub = self.create_subscription(
            GroupHeartbeat, '/motion_group/heartbeat', self._heartbeat_callback, heartbeat_qos
        )
        self._system_info_sub = self.create_subscription(
            GroupSystemInfo, '/motion_group/system_info', self._system_info_callback, system_info_qos
        )
        self._command_sub = self.create_subscription(
            GroupCommand, '/motion_group/command', self._command_callback, reliable
        )
        self._event_sub = self.create_subscription(
            GroupEvent, '/motion_group/event', self._event_callback, reliable
        )
        self._alarm_sub = self.create_subscription(
            GroupAlarm, '/motion_group/alarm', self._alarm_callback, alarm_qos
        )
        self._time_sync_sub = self.create_subscription(
            GroupTimeSync, '/motion_group/time_sync',
            self._time_sync_callback, reliable,
        )

        local_port = int(os.environ.get('MOTION_COORDINATION_LOCAL_PORT') or 8011)
        self._local_api = LocalCoordinationApi(
            self.snapshot, self._handle_local_request, port=local_port,
        )
        self._local_api.start()
        self._heartbeat_timer = self.create_timer(
            self._config.heartbeat_sec, self._heartbeat_tick
        )
        self._state_timer = self.create_timer(0.1, self._state_tick)
        self.get_logger().info(
            'DDS group coordination initialized · '
            f'pc={self._config.pc_id} · group={self._config.group_id or "none"} · '
            f'domain={self._config.dds_domain_id} · joined={str(self._joined).lower()}'
        )
        self._publish_system_info()

    def _publish_system_info(self) -> None:
        message = GroupSystemInfo()
        message.pc_id = self._config.pc_id
        message.git_branch = self._git_branch
        message.git_hash = self._git_hash
        message.git_message = self._git_message
        self._system_info_pub.publish(message)

    def _system_info_callback(self, message: GroupSystemInfo) -> None:
        if message.pc_id == self._config.pc_id:
            return
        self._system_info_cache[message.pc_id] = message
        member = self._registry.member(message.pc_id)
        if member:
            member.git_branch = message.git_branch
            member.git_hash = message.git_hash
            member.git_message = message.git_message

    def _heartbeat_tick(self) -> None:
        if self._config.configured and self._joined:
            self._publish_heartbeat(joined=True)

    def _publish_heartbeat(self, *, joined: bool) -> None:
        message = GroupHeartbeat()
        message.group_id = self._config.group_id
        message.pc_id = self._config.pc_id
        message.boot_id = self._boot_id
        message.display_name = self._config.display_name
        message.sequence = self._next_sequence()
        _set_stamp(message.sent_at, time.time())
        message.joined = bool(joined)
        message.is_master = bool(self._config.is_master)
        with self._lock:
            message.execution_active = bool(self._execution.execution_id)
            message.execution_id = self._execution.execution_id
            message.cycle_number = int(self._execution.cycle_number)
            message.state = self._local_group_state()
            message.trigger_sync_state = str(
                self._trigger_sync_status.get('trigger_sync_state') or 'idle'
            )
            message.trigger_sync_uncertainty_ms = float(
                self._trigger_sync_status.get(
                    'trigger_sync_uncertainty_ms'
                ) or 0.0
            )
            message.servo_alarm_grade = self._local_alarm_grade()
            local_status = self._local_status.get('motion_run_status')
            local_status = local_status if isinstance(local_status, Mapping) else {}
            progress = local_status.get('progress')
            progress = progress if isinstance(progress, Mapping) else {}
            message.motion_phase = str(local_status.get('phase') or '')
            message.display_step = str(local_status.get('display_step') or '')
            message.motion_elapsed_sec = float(progress.get('elapsed_sec') or 0.0)
            message.motion_duration_sec = float(progress.get('duration_sec') or 0.0)
            message.motion_progress_ratio = float(progress.get('ratio') or 0.0)
            message.current_cycle = int(
                self._resolve_motion_cycle(local_status)
            )
        self._heartbeat_pub.publish(message)

    def _state_tick(self) -> None:
        with self._lock:
            execution_active = bool(self._execution.execution_id)
        self._local_runtime_monitor.set_active(execution_active)
        self._consume_local_runtime_status()
        self._emit_local_runtime_event()
        self._enforce_execution_membership()
        self._enforce_schedule_ack_deadline()
        self._enforce_motion_start_report_deadline()
        self._drive_trigger_sync()
        self._prune_seen_commands()
        self._check_multiple_masters()
        self._drive_auto_play()

    def _heartbeat_callback(self, message: GroupHeartbeat) -> None:
        if message.group_id != self._config.group_id:
            return
        if message.pc_id == self._config.pc_id:
            if message.boot_id and message.boot_id != self._boot_id:
                self._handle_duplicate_pc_id(message.boot_id)
            return
        previous = self._registry.member(message.pc_id)
        restarted = bool(previous and previous.boot_id != message.boot_id)
        info = self._system_info_cache.get(message.pc_id)
        self._registry.update(Member(
            pc_id=message.pc_id,
            boot_id=message.boot_id,
            joined=bool(message.joined),
            is_master=bool(message.is_master),
            git_branch=str(info.git_branch) if info else '',
            git_hash=str(info.git_hash) if info else '',
            git_message=str(info.git_message) if info else '',
            state=message.state,
            trigger_sync_state=message.trigger_sync_state,
            trigger_sync_uncertainty_ms=float(
                message.trigger_sync_uncertainty_ms
            ),
            alarm_grade=int(message.servo_alarm_grade),
            received_monotonic=time.monotonic(),
            sequence=int(message.sequence),
            display_name=str(message.display_name),
            motion_phase=str(message.motion_phase),
            motion_elapsed_sec=float(message.motion_elapsed_sec),
            motion_duration_sec=float(message.motion_duration_sec),
            motion_progress_ratio=float(message.motion_progress_ratio),
            current_cycle=int(message.current_cycle),
            display_step=str(message.display_step),
        ))
        pending_alarm = self._alarm_registry.member_boot_changed(
            message.pc_id, message.boot_id,
            previous.boot_id if restarted and previous is not None else '',
        )
        if pending_alarm is not None:
            self._alarm_callback(pending_alarm)
        if restarted and message.pc_id in self._execution.participants:
            threading.Thread(
                target=self._stop_for_peer_failure,
                args=(f'{message.pc_id} 프로그램 재시작',),
                daemon=True,
            ).start()

    def _handle_duplicate_pc_id(self, conflicting_boot_id: str) -> None:
        with self._lock:
            self._duplicate_pc_boot_id = conflicting_boot_id
            current = self._coordination_error
            if (
                current.get('active')
                and current.get('code') == 'DUPLICATE_PC_ID'
                and current.get('conflicting_boot_id') == conflicting_boot_id
            ):
                return
            execution_id = self._execution.execution_id
        if execution_id:
            self._stop_for_peer_failure(
                f'중복 PC ID 감지: {self._config.pc_id}'
            )
        self._publish_coordination_error(
            code='DUPLICATE_PC_ID',
            message=(
                f'같은 PC ID를 사용하는 다른 연동 프로세스가 있습니다: '
                f'{self._config.pc_id}'
            ),
            execution_id=execution_id,
        )

    def _check_multiple_masters(self) -> None:
        if not self._config.is_master:
            return
        masters = [
            pc_id for pc_id in self._registry.live_joined()
            if self._registry.member(pc_id) and self._registry.member(pc_id).is_master
        ]
        if not masters:
            return
        with self._lock:
            current = self._coordination_error
            if (
                current.get('active')
                and current.get('code') == 'MULTIPLE_MASTERS'
            ):
                return
            execution_id = self._execution.execution_id
        if execution_id:
            self._stop_for_peer_failure('다중 마스터 충돌 감지')
        self._publish_coordination_error(
            code='MULTIPLE_MASTERS',
            message=(
                f'그룹 내에 마스터 역할을 가진 PC가 여러 대 있습니다: '
                f'{self._config.pc_id}, {", ".join(masters)}'
            ),
            execution_id=execution_id,
        )

    def _drive_auto_play(self) -> None:
        if not self._config.is_master or not self._config.auto_play:
            return
        if not self._joined:
            return
        required_peers = tuple(self._config.required_peers) or tuple(sorted(set((self._config.pc_id, *self._registry.joined()))))
        if len(required_peers) < 2:
            return
        with self._lock:
            if self._execution.execution_id or self._coordination_error.get('active'):
                self._auto_play_triggered = False
                return
            if self._auto_play_triggered:
                return
        now = time.monotonic()
        missing_or_unhealthy = []
        for pc_id in required_peers:
            if pc_id == self._config.pc_id:
                status = 'online'
                alarm = self._local_alarm_grade()
            else:
                status = self._registry.status(pc_id, now=now)
                member = self._registry.member(pc_id)
                joined = member.joined if member else False
                alarm = member.alarm_grade if member else 0
            if status != 'online' or not joined or alarm > 0:
                missing_or_unhealthy.append(pc_id)
        if missing_or_unhealthy:
            return
        
        # All required peers are online and healthy. We can start the group execution.
        try:
            with self._lock:
                self._auto_play_triggered = True
            self._start_group_execution(
                participants_override=required_peers,
                request={
                    'run_mode': 'continuous',
                    'repeat_mode': 'reinitialize',
                    'dwell_sec': 0.0,
                },
                initialization_only=False,
            )
            self.get_logger().info('마스터 권한으로 그룹 자동 재생을 시작했습니다')
        except Exception as exc:
            with self._lock:
                self._auto_play_triggered = False
            self.get_logger().error(f'자동 재생 시작 실패: {exc}')

    def _time_sync_callback(self, message: GroupTimeSync) -> None:
        """Handle one execution-local DDS monotonic clock exchange."""
        if (
            message.group_id != self._config.group_id
            or not self._joined
            or message.execution_id != self._execution.execution_id
            or message.coordinator_id != self._execution.coordinator_id
        ):
            return
        kind = str(message.kind)
        if kind == 'probe':
            if message.target_pc_id != self._config.pc_id:
                return
            response = GroupTimeSync()
            response.group_id = message.group_id
            response.execution_id = message.execution_id
            response.coordinator_id = message.coordinator_id
            response.target_pc_id = self._config.pc_id
            response.responder_pc_id = self._config.pc_id
            response.kind = 'response'
            response.sample_number = int(message.sample_number)
            response.t1_monotonic_ns = int(message.t1_monotonic_ns)
            response.t2_monotonic_ns = time.monotonic_ns()
            response.t3_monotonic_ns = time.monotonic_ns()
            self._time_sync_pub.publish(response)
            return
        if kind == 'response':
            if message.coordinator_id != self._config.pc_id:
                return
            pc_id = str(message.responder_pc_id)
            with self._lock:
                estimator = self._sync_estimators.get(pc_id)
                if estimator is None or pc_id in self._sync_ready:
                    return
                probe_key = (pc_id, int(message.sample_number))
                expected_t1 = self._sync_probes.pop(probe_key, None)
                if expected_t1 != int(message.t1_monotonic_ns):
                    return
                accepted = estimator.add_exchange(
                    t1_ns=int(message.t1_monotonic_ns),
                    t2_ns=int(message.t2_monotonic_ns),
                    t3_ns=int(message.t3_monotonic_ns),
                    t4_ns=time.monotonic_ns(),
                )
                if not accepted or estimator.sample_count < self._config.trigger_sync_samples:
                    return
                estimate = estimator.estimate()
                if (
                    estimate.uncertainty_ms
                    > self._config.max_trigger_sync_uncertainty_ms
                ):
                    self._fail_trigger_sync(
                        f'{pc_id} DDS 트리거 동기화 불확실성 '
                        f'{estimate.uncertainty_ms:.3f}ms'
                    )
                    return
                result = GroupTimeSync()
                result.group_id = self._config.group_id
                result.execution_id = self._execution.execution_id
                result.coordinator_id = self._config.pc_id
                result.target_pc_id = pc_id
                result.responder_pc_id = pc_id
                result.kind = 'result'
                result.sample_number = int(estimator.sample_count)
                result.offset_ns = int(estimate.offset_ns)
                result.uncertainty_ns = int(estimate.uncertainty_ns)
                self._time_sync_pub.publish(result)
            return
        if kind == 'result':
            if message.target_pc_id != self._config.pc_id:
                return
            uncertainty_ms = int(message.uncertainty_ns) / 1_000_000.0
            if uncertainty_ms > self._config.max_trigger_sync_uncertainty_ms:
                return
            with self._lock:
                self._local_sync_offset_ns = int(message.offset_ns)
                self._trigger_sync_status = {
                    'trigger_sync_state': 'ready',
                    'trigger_sync_uncertainty_ms': round(uncertainty_ms, 6),
                    'trigger_sync_source': 'dds_relative_monotonic',
                    'coordinator_id': message.coordinator_id,
                }
            acknowledgement = GroupTimeSync()
            acknowledgement.group_id = message.group_id
            acknowledgement.execution_id = message.execution_id
            acknowledgement.coordinator_id = message.coordinator_id
            acknowledgement.target_pc_id = self._config.pc_id
            acknowledgement.responder_pc_id = self._config.pc_id
            acknowledgement.kind = 'result_ack'
            acknowledgement.sample_number = int(message.sample_number)
            acknowledgement.offset_ns = int(message.offset_ns)
            acknowledgement.uncertainty_ns = int(message.uncertainty_ns)
            self._time_sync_pub.publish(acknowledgement)
            return
        if kind == 'result_ack' and message.coordinator_id == self._config.pc_id:
            with self._lock:
                pc_id = str(message.responder_pc_id)
                if pc_id not in self._sync_estimators:
                    return
                self._sync_ready.add(pc_id)
                self._complete_trigger_sync_if_ready()

    def _begin_trigger_sync(self, next_action: str) -> None:
        if next_action not in {'initialize', 'cycle_initialize', 'start'}:
            raise ValueError('DDS 트리거 동기화 후속 동작이 올바르지 않습니다')
        with self._lock:
            if self._execution.coordinator_id != self._config.pc_id:
                raise ValueError('임시 진행 PC만 트리거 동기화를 시작할 수 있습니다')
            remote = [
                pc_id for pc_id in self._execution.participants
                if pc_id != self._config.pc_id
            ]
            self._sync_estimators = {
                pc_id: TriggerSyncEstimator() for pc_id in remote
            }
            self._sync_sent_samples = {pc_id: 0 for pc_id in remote}
            self._sync_probes = {}
            self._sync_ready = {self._config.pc_id}
            self._sync_next_action = next_action
            self._sync_deadline = time.monotonic() + self._config.prepare_timeout_sec
            self._sync_last_probe_at = 0.0
            self._local_sync_offset_ns = 0
            self._trigger_sync_status = {
                'trigger_sync_state': 'syncing' if remote else 'ready',
                'trigger_sync_uncertainty_ms': 0.0,
                'trigger_sync_source': 'dds_relative_monotonic',
                'coordinator_id': self._config.pc_id,
            }
            self._complete_trigger_sync_if_ready()

    def _drive_trigger_sync(self) -> None:
        with self._lock:
            if (
                not self._sync_next_action
                or self._execution.coordinator_id != self._config.pc_id
            ):
                return
            now = time.monotonic()
            if now >= self._sync_deadline:
                missing = sorted(set(self._execution.participants) - self._sync_ready)
                self._fail_trigger_sync(
                    f'DDS 트리거 동기화 제한시간 초과: {", ".join(missing)}'
                )
                return
            if now - self._sync_last_probe_at < 0.05:
                return
            self._sync_last_probe_at = now
            max_attempts = self._config.trigger_sync_samples * 3
            for pc_id, estimator in self._sync_estimators.items():
                if pc_id in self._sync_ready:
                    continue
                sent = self._sync_sent_samples.get(pc_id, 0)
                if estimator.sample_count >= self._config.trigger_sync_samples:
                    continue
                if sent >= max_attempts:
                    continue
                probe = GroupTimeSync()
                probe.group_id = self._config.group_id
                probe.execution_id = self._execution.execution_id
                probe.coordinator_id = self._config.pc_id
                probe.target_pc_id = pc_id
                probe.kind = 'probe'
                probe.sample_number = sent + 1
                probe.t1_monotonic_ns = time.monotonic_ns()
                self._sync_sent_samples[pc_id] = sent + 1
                self._sync_probes[(pc_id, sent + 1)] = int(
                    probe.t1_monotonic_ns
                )
                self._time_sync_pub.publish(probe)

    def _complete_trigger_sync_if_ready(self) -> None:
        if not self._sync_next_action:
            return
        if self._sync_ready < set(self._execution.participants):
            return
        next_action = self._sync_next_action
        self._sync_next_action = ''
        self._sync_deadline = 0.0
        self._trigger_sync_status.update({'trigger_sync_state': 'ready'})
        if next_action == 'initialize':
            action = self._execution.initialize_action(now=time.monotonic())
        elif next_action == 'cycle_initialize':
            action = self._execution.cycle_initialize_action(now=time.monotonic())
        else:
            action = self._execution.start_action(now=time.monotonic())
        self._publish_action(action)

    def _fail_trigger_sync(self, reason: str) -> None:
        failure_status = {
            'trigger_sync_state': 'failed',
            'trigger_sync_uncertainty_ms': 0.0,
            'trigger_sync_source': 'dds_relative_monotonic',
            'message': reason,
        }
        self._cancel_before_start(reason, code='TRIGGER_SYNC_FAILED')
        self._trigger_sync_status = failure_status
        self.get_logger().error(reason)

    def _command_callback(self, message: GroupCommand) -> None:
        if message.group_id != self._config.group_id or not self._joined:
            return
        if self._config.pc_id not in set(message.participant_ids):
            return
        if not message.command_id or self._command_seen(message.command_id):
            return
        with self._lock:
            participants = tuple(sorted(set(message.participant_ids)))
            urgent_stop = bool(
                message.command in {'stop_now', 'cancel_before_start'}
                and self._config.pc_id in set(message.participant_ids)
                and message.coordinator_id in set(message.participant_ids)
                and self._stop_command_matches(message.execution_id, participants)
            )
            if urgent_stop:
                self._cancelled_execution_ids.add(message.execution_id)
        if not self._command_dispatcher.submit(
            message, urgent_stop=urgent_stop,
        ):
            self.get_logger().warn('종료 중인 그룹 명령을 폐기했습니다')

    def _process_group_command(self, message: GroupCommand) -> None:
        try:
            command = str(message.command)
            with self._lock:
                execution_cancelled = (
                    message.execution_id in self._cancelled_execution_ids
                )
            if execution_cancelled and command not in {
                'stop_after_cycle', 'stop_now', 'cancel_before_start'
            }:
                raise ValueError('정지된 그룹 실행의 지연 명령을 폐기했습니다')
            participants = tuple(sorted(set(message.participant_ids)))
            if not 1 <= len(participants) <= 8:
                raise ValueError('그룹 실행 참가 PC는 1~8대여야 합니다')
            if command == 'prepare':
                self._accept_execution_claim(message, participants)
                with self._lock:
                    self._execution.repeat_mode = (
                        str(message.repeat_mode or 'direct').strip().lower()
                    )
                    self._execution.dwell_sec = max(float(message.dwell_sec), 0.0)
                    self._execution.initialization_only = bool(
                        message.initialization_only
                    )
                    self._execution.run_mode = str(
                        message.run_mode or 'continuous'
                    ).strip().lower()
                result = self._local_readiness()
                event = 'ready' if result.get('success') else 'rejected'
                self._publish_event(
                    message, event, bool(result.get('success')),
                    str(result.get('message') or event),
                )
                return
            if command in {'stop_after_cycle', 'stop_now', 'cancel_before_start'}:
                self._require_stop_command(message, participants)
            else:
                self._require_active_command(message, participants)
            if command in {'initialize_at', 'cycle_initialize_at'}:
                local_target_ns = self._local_schedule_ns(message)
                control_command = (
                    'group_prepare'
                    if command == 'initialize_at' else 'group_initialize_at'
                )
                result = self._call_local_control({
                    'command': control_command,
                    'execution_id': message.execution_id,
                    'cycle_number': int(message.cycle_number),
                    'initialize_monotonic': local_target_ns / 1_000_000_000.0,
                    'network_operation_id': message.command_id,
                    'repeat_mode': self._execution.repeat_mode,
                    'dwell_sec': self._execution.dwell_sec,
                    'initialization_only': self._execution.initialization_only,
                    'run_mode': self._execution.run_mode,
                })
                event = (
                    'initialize_scheduled'
                    if command == 'initialize_at' else 'cycle_initialize_scheduled'
                )
            elif command == 'start_at':
                local_target_ns = self._local_schedule_ns(message)
                result = self._call_local_control({
                    'command': 'group_start_at',
                    'execution_id': message.execution_id,
                    'cycle_number': int(message.cycle_number),
                    'start_monotonic': local_target_ns / 1_000_000_000.0,
                    'network_operation_id': message.command_id,
                })
                event = 'start_scheduled'
            elif command == 'stop_after_cycle':
                result = self._call_local_control({
                    'command': 'stop_after_cycle',
                    'execution_id': message.execution_id,
                    'network_operation_id': message.command_id,
                })
                with self._lock:
                    if self._execution.coordinator_id == self._config.pc_id:
                        self._execution.stop_after_cycle = True
                event = 'stop_after_cycle_accepted'
            elif command in {'stop_now', 'cancel_before_start'}:
                release_timeout = (
                    0.25 if command == 'stop_now'
                    else self._LOCAL_WORKER_RELEASE_TIMEOUT_SEC
                )
                result = self._call_local_control({
                    'command': 'stop_now' if command == 'stop_now' else 'group_cancel',
                    'execution_id': message.execution_id,
                    'network_operation_id': message.command_id,
                }, timeout_sec=release_timeout)
                event = 'stopped'
                with self._lock:
                    if (
                        self._execution.coordinator_id != self._config.pc_id
                        and result.get('success')
                    ):
                        self._clear_active_execution()
            else:
                raise ValueError('지원하지 않는 그룹 명령입니다')
            if command in {'initialize_at', 'cycle_initialize_at', 'start_at'}:
                with self._lock:
                    execution_cancelled = (
                        message.execution_id in self._cancelled_execution_ids
                    )
                if execution_cancelled:
                    self._call_local_control({
                        'command': 'stop_now',
                        'execution_id': message.execution_id,
                        'network_operation_id': (
                            f'late-command-stop-{message.command_id}'
                        ),
                    }, timeout_sec=0.25)
                    result = {
                        'success': False,
                        'message': '정지 후 완료된 지연 그룹 명령을 폐기했습니다',
                    }
                    event = 'rejected'
            self._publish_event(
                message, event if result.get('success') else 'rejected',
                bool(result.get('success')),
                str(result.get('message') or event),
            )
        except Exception as exc:
            self._publish_event(message, 'rejected', False, str(exc))

    def _event_callback(self, message: GroupEvent) -> None:
        if message.group_id != self._config.group_id:
            return
        cancel_reason = ''
        spread_failure: Optional[Dict[str, Any]] = None
        runtime_error = ''
        with self._lock:
            if (
                not self._execution.execution_id
                or message.execution_id != self._execution.execution_id
            ):
                return
            if self._execution.coordinator_id != self._config.pc_id:
                return
            try:
                if message.event == 'ready' and message.success:
                    self._record_schedule_ack(message)
                    self._execution.mark_ready(message.pc_id)
                    if self._execution.ready == set(self._execution.participants):
                        self._begin_trigger_sync('initialize')
                elif message.event == 'rejected':
                    if self._execution.state in {
                        'preparing', 'initializing', 'armed', 'start_scheduled',
                        'motion_completed', 'cycle_initializing',
                    }:
                        cancel_reason = (
                            f'{message.pc_id} 그룹 시작 거부: '
                            f'{message.message or "로컬 준비 실패"}'
                        )
                    else:
                        runtime_error = (
                            f'{message.pc_id} 그룹 명령 실패: '
                            f'{message.message or "응답 거부"}'
                        )
                elif message.event == 'armed' and message.success:
                    self._execution.mark_armed(
                        message.pc_id,
                        int(message.triggered_monotonic_ns) / 1_000_000_000.0,
                    )
                    if self._execution.state == 'armed':
                        self.get_logger().info(
                            '그룹 초기화 트리거 편차 · '
                            f'execution={self._execution.execution_id} · '
                            f'spread_ms={self._execution.last_initialize_spread_ms}'
                        )
                        if self._execution.initialize_within_tolerance() is False:
                            spread_failure = {
                                'stage': 'initialize',
                                'execution_id': self._execution.execution_id,
                                'participants': tuple(self._execution.participants),
                                'cycle_number': 0,
                                'spread_ms': self._execution.last_initialize_spread_ms,
                                'triggered': dict(
                                    self._execution.initialize_triggered
                                ),
                            }
                        elif self._execution.initialization_only:
                            self._finish_group_initialization()
                        else:
                            self._publish_next_start()
                elif message.event == 'initialize_scheduled' and message.success:
                    self._record_schedule_ack(message)
                elif message.event == 'cycle_initialize_scheduled' and message.success:
                    self._record_schedule_ack(message)
                elif message.event == 'start_scheduled' and message.success:
                    self._execution.mark_scheduled(message.pc_id, int(message.cycle_number))
                    self._record_schedule_ack(message)
                elif message.event == 'motion_started' and message.success:
                    self._execution.mark_triggered(
                        message.pc_id,
                        int(message.cycle_number),
                        int(message.triggered_monotonic_ns) / 1_000_000_000.0,
                    )
                    if self._execution.state == 'running':
                        self._execution.motion_start_report_deadline = 0.0
                        self.get_logger().info(
                            '그룹 모션 시작 트리거 편차 · '
                            f'execution={self._execution.execution_id} · '
                            f'cycle={self._execution.cycle_number} · '
                            f'spread_ms={self._execution.last_start_spread_ms}'
                        )
                    if self._execution.trigger_within_tolerance() is False:
                        spread_failure = {
                            'stage': 'motion_start',
                            'execution_id': self._execution.execution_id,
                            'participants': tuple(self._execution.participants),
                            'cycle_number': self._execution.cycle_number,
                            'spread_ms': self._execution.last_start_spread_ms,
                            'triggered': dict(self._execution.triggered),
                        }
                elif message.event == 'motion_completed' and message.success:
                    self._execution.mark_motion_completed(
                        message.pc_id, int(message.cycle_number),
                    )
                    if self._execution.state == 'motion_completed':
                        if (
                            self._execution.stop_after_cycle
                            or self._execution.run_mode == 'once'
                        ):
                            self._begin_group_release()
                        else:
                            self._publish_next_cycle()
                elif message.event == 'cycle_initialized' and message.success:
                    self._execution.mark_cycle_initialized(
                        message.pc_id, int(message.cycle_number),
                    )
                    if self._execution.state == 'cycle_ready':
                        self._publish_next_start()
                elif message.event == 'stopped':
                    if (
                        self._execution.pending_command == 'cancel_before_start'
                        and message.command_id
                        == self._execution.pending_command_id
                    ):
                        self._record_schedule_ack(message)
                        if self._execution.pending_acks >= set(
                            self._execution.participants
                        ):
                            self._execution.stop_now(
                                error=bool(self._execution.release_error),
                            )
                            self._clear_active_execution()
                    elif self._execution.pending_command == 'cancel_before_start':
                        # Runtime-only stopped events use synthetic IDs and must
                        # not interfere with the release ACK barrier.
                        pass
                    elif (
                        self._execution.stop_after_cycle
                        or self._execution.run_mode == 'once'
                    ) and self._execution.pending_command != 'cancel_before_start':
                        # Runtime stop-after-cycle completes without emitting
                        # cycle_ready. Start the same release handshake here.
                        self._begin_group_release()
                    elif self._execution.state != 'releasing':
                        self._execution.stop_now()
                elif message.event == 'error':
                    runtime_error = (
                        f'{message.pc_id} 로컬 그룹 실행 오류: '
                        f'{message.message or "확인 필요"}'
                    )
            except ValueError as exc:
                self.get_logger().warn(f'Group event rejected: {exc}')
        if cancel_reason:
            self._cancel_before_start(cancel_reason, code='GROUP_START_REJECTED')
        if spread_failure is not None:
            self._handle_trigger_spread_exceeded(spread_failure)
        if runtime_error:
            self._stop_for_peer_failure(runtime_error)

    _LOCAL_WORKER_RELEASE_TIMEOUT_SEC = 5.0

    def _release_local_worker(
        self,
        execution_id: str,
        *,
        network_operation_id: str = '',
        timeout_sec: Optional[float] = None,
        participants: Optional[tuple[str, ...]] = None,
    ) -> Dict[str, Any]:
        """Stop the local group worker and wait until its thread exits."""
        release_timeout = (
            float(timeout_sec)
            if timeout_sec is not None
            else self._LOCAL_WORKER_RELEASE_TIMEOUT_SEC
        )
        cancel_result = self._call_local_control({
            'command': 'group_cancel',
            'execution_id': execution_id,
            'network_operation_id': network_operation_id,
        }, timeout_sec=release_timeout)
        if cancel_result.get('success'):
            return cancel_result
        stop_participants = participants
        if not stop_participants:
            with self._lock:
                stop_participants = self._execution.participants
        if not stop_participants:
            stop_participants = (self._config.pc_id,)
        stop_outcome = self._issue_stop_now(
            execution_id=execution_id,
            participants=stop_participants,
            cycle_number=0,
            command_id=network_operation_id or f'local-release-{uuid.uuid4().hex}',
        )
        return stop_outcome.local_result

    def _enter_group_release(
        self,
        cancel_message: GroupCommand,
        *,
        local_release: Mapping[str, Any],
        error: bool = False,
    ) -> None:
        """Begin or advance the multi-PC release barrier."""
        with self._lock:
            execution_id = self._execution.execution_id
            if not execution_id or execution_id != cancel_message.execution_id:
                return
            participants = set(self._execution.participants)
            self._execution.pending_command = 'cancel_before_start'
            self._execution.state = 'releasing'
            self._execution.pending_command_id = cancel_message.command_id
            self._execution.pending_acks.clear()
            if local_release.get('success'):
                self._execution.pending_acks.add(self._config.pc_id)
            self._execution.pending_ack_deadline = (
                time.monotonic() + self._config.prepare_timeout_sec
            )
            self._execution.release_error = error
            release_complete = self._execution.pending_acks >= participants
            if release_complete:
                self._execution.stop_now(error=error)
                self._clear_active_execution()
                return
        self._command_pub.publish(cancel_message)

    def _cancel_before_start(self, reason: str, *, code: str) -> None:
        """Cancel a pre-start session locally, notify peers, then release its lease."""
        with self._lock:
            execution_id = self._execution.execution_id
            if not execution_id:
                return
            participants = self._execution.participants
            is_coordinator = self._execution.coordinator_id == self._config.pc_id
            cancel_message = self._new_command(
                command='cancel_before_start',
                execution_id=execution_id,
                cycle_number=self._execution.cycle_number,
                participants=participants,
            )
        local_release = self._release_local_worker(
            execution_id,
            network_operation_id=cancel_message.command_id,
            participants=participants,
        )
        message = str(reason)
        if not local_release.get('success'):
            message += (
                ' · 로컬 worker 정리 실패: '
                f'{local_release.get("message") or "응답 없음"}'
            )
            with self._lock:
                if self._execution.execution_id == execution_id:
                    self._execution.stop_now(error=True)
            self._publish_coordination_error(
                code='GROUP_WORKER_STILL_RUNNING',
                message=message,
                execution_id=execution_id,
            )
            return
        if is_coordinator and len(participants) > 1:
            self._enter_group_release(
                cancel_message, local_release=local_release, error=True,
            )
        else:
            with self._lock:
                if self._execution.execution_id == execution_id:
                    self._execution.stop_now(error=True)
                    self._clear_active_execution()
        self._publish_coordination_error(
            code=code, message=message, execution_id=execution_id,
        )

    def _handle_trigger_spread_exceeded(self, context: Mapping[str, Any]) -> None:
        execution_id = str(context.get('execution_id') or '')
        participants = tuple(context.get('participants') or ())
        spread_ms = float(context.get('spread_ms') or 0.0)
        stage = str(context.get('stage') or 'motion_start')
        initialize_stage = stage == 'initialize'
        label = '초기화' if initialize_stage else '모션 시작'
        error_code = (
            'GROUP_INITIALIZE_TRIGGER_SPREAD_EXCEEDED'
            if initialize_stage else 'GROUP_TRIGGER_SPREAD_EXCEEDED'
        )
        stop_outcome = self._issue_stop_now(
            execution_id=execution_id, participants=participants,
            cycle_number=int(context.get('cycle_number') or 0),
        )
        with self._lock:
            self._execution.stop_now(error=True)
            if stop_outcome.local_success:
                self._clear_active_execution()
        self._publish_coordination_error(
            code=error_code,
            message=(
                f'{label} 트리거 편차 20ms 초과: {spread_ms:.3f}ms'
                + (
                    '' if stop_outcome.local_success else
                    ' · 로컬 즉시 정지 요청 실패'
                )
            ),
            execution_id=execution_id,
        )

    def _publish_coordination_error(
        self, *, code: str, message: str, execution_id: str,
    ) -> None:
        error = {
            'active': True,
            'code': str(code),
            'error_source': 'group_coordination',
            'grade': 2,
            'execution_id': str(execution_id),
            'message': str(message),
            'occurred_at': time.time(),
        }
        with self._lock:
            self._coordination_error = error
        alarm = GroupAlarm()
        alarm.group_id = self._config.group_id
        alarm.execution_id = str(execution_id)
        alarm.pc_id = self._config.pc_id
        alarm.boot_id = self._boot_id
        alarm.sequence = self._next_sequence()
        _set_stamp(alarm.occurred_at, error['occurred_at'])
        alarm.grade = 2
        alarm.motor_axis = -1
        alarm.error_code = str(code)
        alarm.error_source = 'group_coordination'
        alarm.action = '그룹 실행 중지·재실행 차단'
        alarm.message = str(message)[:512]
        alarm.active = True
        if self._joined and self._config.group_id:
            self._alarm_pub.publish(alarm)

    def _acknowledge_coordination_error(self) -> Dict[str, Any]:
        with self._lock:
            if not self._coordination_error.get('active'):
                return {'success': True, 'message': '확인할 그룹 동기화 오류가 없습니다'}
            if self._coordination_error.get('code') == 'DUPLICATE_PC_ID':
                raise ValueError('중복 PC ID를 수정하고 연동 서비스를 재시작하세요')
            previous = dict(self._coordination_error)
            self._coordination_error = {}
            self._alarm_registry.alarms = {
                pc_id: alarm for pc_id, alarm in self._alarm_registry.alarms.items()
                if alarm.get('error_source') != 'group_coordination'
            }
        alarm = GroupAlarm()
        alarm.group_id = self._config.group_id
        alarm.execution_id = str(previous.get('execution_id') or '')
        alarm.pc_id = self._config.pc_id
        alarm.boot_id = self._boot_id
        alarm.sequence = self._next_sequence()
        _set_stamp(alarm.occurred_at, time.time())
        alarm.grade = 0
        alarm.motor_axis = -1
        alarm.error_code = str(previous.get('code') or '')
        alarm.error_source = 'group_coordination'
        alarm.action = '사용자 오류 확인'
        alarm.message = '그룹 동기화 오류 확인 완료'
        alarm.active = False
        if self._joined and self._config.group_id:
            self._alarm_pub.publish(alarm)
        return {'success': True, 'message': '그룹 동기화 오류 확인 완료'}

    def _temporarily_disable_coordination(self) -> Dict[str, Any]:
        """Stop any owned group run, then leave this PC without peer approval."""
        with self._lock:
            execution_id = str(self._execution.execution_id or '')
            execution_state = str(self._execution.state or 'idle')

        stop_message = ''
        if execution_id:
            stopped = self._request_group_stop(after_cycle=False)
            stop_message = str(stopped.get('message') or '')
        elif execution_state not in {'idle', 'stopped', 'error'}:
            # A failed prepare can leave a display-only state with no execution
            # lease. Release it locally so the user can continue standalone work.
            with self._lock:
                self._execution.reset()
                self._clear_active_execution()
        else:
            with self._lock:
                self._clear_active_execution()

        with self._lock:
            self._coordination_error = {}
            self._alarm_registry.clear_coordination()
            joined = self._joined
            self._joined = False
        if joined and self._config.configured:
            self._publish_heartbeat(joined=False)
        return {
            'success': True,
            'message': (
                '이 PC의 DDS 연동을 일시 해제했습니다. '
                '단독 모션·모션 스튜디오를 사용할 수 있습니다.'
                + (f' {stop_message}' if stop_message else '')
            ),
        }

    def _alarm_callback(self, message: GroupAlarm) -> None:
        if message.group_id != self._config.group_id:
            return
        if message.pc_id == self._config.pc_id:
            return
        member = self._registry.member(message.pc_id)
        if not self._alarm_registry.accept(
            message, member.boot_id if member is not None else '',
        ):
            return
        coordination_alarm = message.error_source == 'group_coordination'
        with self._lock:
            if message.active:
                self._alarm_registry.set(message.pc_id, {
                    'pc_id': message.pc_id,
                    'execution_id': message.execution_id,
                    'grade': int(message.grade),
                    'motor_axis': int(message.motor_axis),
                    'error_code': message.error_code,
                    'error_source': message.error_source,
                    'action': message.action,
                    'message': message.message,
                    'occurred_at': _stamp_to_float(message.occurred_at),
                })
                if coordination_alarm:
                    self._coordination_error = {
                        'active': True,
                        'code': message.error_code,
                        'error_source': message.error_source,
                        'grade': int(message.grade),
                        'execution_id': message.execution_id,
                        'pc_id': message.pc_id,
                        'message': message.message,
                        'occurred_at': _stamp_to_float(message.occurred_at),
                    }
            else:
                self._alarm_registry.remove(message.pc_id)
                if coordination_alarm:
                    self._alarm_registry.clear_coordination()
                    if not self._duplicate_pc_boot_id:
                        self._coordination_error = {}
                return
        # GroupAlarm only shares status. Motion control is delivered once on
        # the ordered GroupCommand path (stop_after_cycle or stop_now).

    def _handle_local_request(
        self, request: Mapping[str, Any]
    ) -> Dict[str, Any]:
        try:
            command = str(request.get('command') or '')
            if command in {'status', 'check_readiness'}:
                result = {'success': True, **self.snapshot()}
            elif command == 'join':
                if not self._config.configured:
                    raise ValueError('그룹 ID와 DDS Domain ID 설정이 필요합니다')
                if self._duplicate_pc_boot_id:
                    raise ValueError('중복 PC ID를 먼저 수정하세요')
                self._joined = True
                result = {'success': True, 'message': 'DDS 그룹 참가'}
            elif command == 'leave':
                if self._execution.execution_id:
                    raise ValueError('그룹 실행 중에는 그룹에서 나갈 수 없습니다')
                if self._joined and self._config.configured:
                    self._publish_heartbeat(joined=False)
                self._joined = False
                result = {'success': True, 'message': 'DDS 그룹 나가기'}
            elif command == 'temporarily_disable':
                result = self._temporarily_disable_coordination()
            elif command in {'start_group', 'synchronized_run'}:
                result = self._start_group_execution(request=request)
            elif command == 'initialize_group':
                result = self._start_group_execution(
                    request=request, initialization_only=True,
                )
            elif command == 'stop_after_cycle':
                result = self._request_group_stop(after_cycle=True)
            elif command in {'stop_now', 'stop_motion'}:
                result = self._request_group_stop(after_cycle=False)
            elif command == 'acknowledge_group_error':
                result = self._acknowledge_coordination_error()
            else:
                raise ValueError('지원하지 않는 그룹 연동 요청입니다')
        except Exception as exc:
            result = {'success': False, 'message': str(exc)}
        return result

    def _start_group_execution(
        self,
        *,
        participants_override: Optional[tuple[str, ...]] = None,
        request: Optional[Mapping[str, Any]] = None,
        initialization_only: bool = False,
    ) -> Dict[str, Any]:
        if not self._joined:
            raise ValueError('먼저 DDS 그룹에 참가하세요')
        request = request or {}
        run_mode = str(request.get('run_mode') or 'continuous').strip().lower()
        repeat_mode = str(request.get('repeat_mode') or 'reinitialize').strip().lower()
        try:
            dwell_sec = float(request.get('dwell_sec') or 0.0)
        except (TypeError, ValueError) as exc:
            raise ValueError('그룹 대기 시간은 숫자여야 합니다') from exc
        try:
            target_cycle_count = int(request.get('target_cycle_count') or 0)
        except (TypeError, ValueError) as exc:
            raise ValueError('목표 정지 회차가 올바르지 않습니다') from exc
        if target_cycle_count < 0:
            raise ValueError('목표 정지 회차는 0 이상이어야 합니다')
        if run_mode not in {'once', 'continuous'}:
            raise ValueError('그룹 실행 방식은 1회 또는 연속이어야 합니다')
        if repeat_mode not in {'direct', 'dwell', 'reinitialize', 'dwell_reinitialize'}:
            raise ValueError('지원하지 않는 그룹 회차 사이 동작입니다')
        if dwell_sec < 0.0:
            raise ValueError('그룹 대기 시간은 0초 이상이어야 합니다')
        if repeat_mode not in {'dwell', 'dwell_reinitialize'}:
            dwell_sec = 0.0
        with self._lock:
            if self._duplicate_pc_boot_id:
                raise ValueError('중복 PC ID를 수정하고 연동 서비스를 재시작하세요')
            if self._coordination_error.get('active'):
                raise ValueError(
                    '그룹 동기화 오류를 확인한 후 다시 실행하세요: '
                    f'{self._coordination_error.get("message") or "확인 필요"}'
                )
            if self._local_alarm_grade() > 0:
                raise ValueError('이 PC의 Servo 알람을 확인하세요')
            joined = self._registry.joined()
            participants = tuple(sorted(set(
                participants_override
                or (self._config.pc_id, *joined)
            )))
            if len(participants) < 2:
                raise ValueError('그룹 연동에는 정상 연결된 PC가 2대 이상 필요합니다')
            if len(participants) > 8:
                raise ValueError('그룹 실행 참가 PC는 최대 8대입니다')
            unhealthy = []
            for pc_id in participants:
                if pc_id == self._config.pc_id:
                    continue
                member = self._registry.member(pc_id)
                state = self._registry.status(pc_id)
                if member is None or not member.joined or state != 'online':
                    unhealthy.append(f'{pc_id}({state})')
                elif member.alarm_grade > 0:
                    unhealthy.append(f'{pc_id}(Servo 알람 {member.alarm_grade}등급)')
            if unhealthy:
                raise ValueError(
                    '그룹 참가 PC 상태 때문에 실행을 시작할 수 없습니다: '
                    + ', '.join(unhealthy)
                )
            execution_id = self._execution.begin(
                self._config.pc_id, participants,
                run_mode=run_mode, repeat_mode=repeat_mode, dwell_sec=dwell_sec,
                target_cycle_count=target_cycle_count,
                initialization_only=initialization_only,
            )
            self._sync_estimators.clear()
            self._sync_sent_samples.clear()
            self._sync_probes.clear()
            self._sync_ready.clear()
            self._sync_next_action = ''
            self._local_sync_offset_ns = 0
            self._trigger_sync_status = {
                'trigger_sync_state': 'idle',
                'trigger_sync_uncertainty_ms': 0.0,
                'trigger_sync_source': 'dds_relative_monotonic',
            }
            command = self._new_command(
                command='prepare', execution_id=execution_id,
                cycle_number=0, participants=participants,
                repeat_mode=repeat_mode, dwell_sec=dwell_sec,
                target_cycle_count=target_cycle_count,
                initialization_only=initialization_only,
                run_mode=run_mode,
            )
            self._execution.pending_command = 'prepare'
            self._execution.pending_command_id = command.command_id
            self._execution.pending_acks.clear()
            self._execution.pending_ack_deadline = (
                time.monotonic() + self._config.prepare_timeout_sec
            )
            self._execution.pending_scheduled_at = 0.0
            self._command_pub.publish(command)
        return {
            'success': True,
            'message': (
                '그룹 초기 위치 이동 준비 확인 시작'
                if initialization_only else '그룹 실행 준비 확인 시작'
            ),
            'execution_id': execution_id,
            'participants': list(participants),
            'run_mode': run_mode,
            'repeat_mode': repeat_mode,
            'dwell_sec': dwell_sec,
            'initialization_only': initialization_only,
        }

    def _request_group_stop(self, *, after_cycle: bool) -> Dict[str, Any]:
        with self._lock:
            if not self._execution.execution_id:
                raise ValueError('활성 그룹 실행이 없습니다')
            command = 'stop_after_cycle' if after_cycle else 'stop_now'
            execution_id = self._execution.execution_id
            participants = self._execution.participants
            cycle_number = self._execution.cycle_number
        if after_cycle:
            stop_message = self._new_command(
                command=command, execution_id=execution_id,
                cycle_number=cycle_number, participants=participants,
            )
            local = self._call_local_control({
                'command': command,
                'execution_id': execution_id,
                'network_operation_id': stop_message.command_id,
            })
            self._command_pub.publish(stop_message)
            dds_stop_published = True
        else:
            outcome = self._issue_stop_now(
                execution_id=execution_id, participants=participants,
                cycle_number=cycle_number,
            )
            local = outcome.local_result
            dds_stop_published = outcome.dds_stop_published
        with self._lock:
            if self._execution.execution_id == execution_id:
                if after_cycle:
                    self._execution.request_stop_after_cycle()
                else:
                    self._execution.stop_now()
                    self._clear_active_execution()
        local_success = bool(local.get('success'))
        if not local_success and not after_cycle:
            self._publish_coordination_error(
                code='GROUP_LOCAL_STOP_FAILED',
                message=(
                    '이 PC의 즉시 정지를 확인하지 못했습니다: '
                    f'{local.get("message") or "응답 없음"}'
                ),
                execution_id=execution_id,
            )
        return {
            'success': local_success,
            'message': (
                '현재 회차 후 정지 로컬 적용·DDS 전달'
                if after_cycle and local_success else
                '전체 즉시 정지 로컬 적용·DDS 전달'
                if local_success else
                f'로컬 정지 확인 실패·DDS 정지는 전달됨: '
                f'{local.get("message") or "응답 없음"}'
            ),
            'dds_stop_published': dds_stop_published,
        }

    def _publish_action(self, action: ScheduledAction) -> None:
        command = self._new_command(
            command=action.command,
            execution_id=action.execution_id,
            cycle_number=action.cycle_number,
            participants=self._execution.participants,
            command_id=action.command_id,
            scheduled_monotonic=action.scheduled_at,
        )
        self._execution.pending_command = action.command
        self._execution.pending_command_id = action.command_id
        self._execution.pending_acks.clear()
        self._execution.pending_ack_deadline = (
            action.scheduled_at - self._config.schedule_ack_margin_sec
        )
        self._execution.pending_scheduled_at = float(action.scheduled_at)
        if action.command == 'start_at':
            self._execution.motion_start_report_deadline = (
                float(action.scheduled_at)
                + self._config.trigger_report_timeout_sec
            )
        self._command_pub.publish(command)

    def _publish_next_start(self) -> None:
        unhealthy = self._execution_unhealthy_members()
        if unhealthy:
            reason = (
                f'다음 그룹 모션 시작 차단: {", ".join(unhealthy)} 상태 확인 필요'
            )
            self._stop_for_peer_failure(reason)
            raise ValueError(reason)
        self._begin_trigger_sync('start')

    def _finish_group_initialization(self) -> None:
        """Release prepared group sessions after a successful init-only move."""
        with self._lock:
            execution_id = self._execution.execution_id
            if not execution_id:
                return
            message = self._new_command(
                command='cancel_before_start',
                execution_id=execution_id,
                cycle_number=self._execution.cycle_number,
                participants=self._execution.participants,
            )
        local = self._call_local_control({
            'command': 'group_cancel',
            'execution_id': execution_id,
            'network_operation_id': message.command_id,
        })
        # A group session may have already released itself after the
        # initialization callback. That is a successful cleanup, not a
        # group-initialization failure.
        if not local.get('success') and '세션이 일치하지 않습니다' not in str(
            local.get('message') or ''
        ):
            self._publish_coordination_error(
                code='GROUP_INITIALIZE_RELEASE_FAILED',
                message=str(local.get('message') or '그룹 초기 위치 이동 정리 실패'),
                execution_id=execution_id,
            )
        self._command_pub.publish(message)
        with self._lock:
            if self._execution.execution_id == execution_id:
                self._execution.stop_now()
                self._clear_active_execution()

    def _publish_next_cycle(self) -> None:
        """Apply the common group repeat policy after every completed cycle."""
        with self._lock:
            execution_id = self._execution.execution_id
            repeat_mode = self._execution.repeat_mode
            dwell_sec = self._execution.dwell_sec

        def schedule() -> None:
            with self._lock:
                if self._execution.execution_id != execution_id:
                    return
            self._publish_next_cycle_initialization()

        if repeat_mode in {'dwell', 'dwell_reinitialize'} and dwell_sec > 0.0:
            threading.Timer(dwell_sec, schedule).start()
        else:
            schedule()

    def _publish_next_cycle_initialization(self) -> None:
        unhealthy = self._execution_unhealthy_members()
        if unhealthy:
            reason = (
                f'그룹 회차 초기화 차단: {", ".join(unhealthy)} 상태 확인 필요'
            )
            self._stop_for_peer_failure(reason)
            raise ValueError(reason)
        self._begin_trigger_sync('cycle_initialize')

    def _record_schedule_ack(self, message: GroupEvent) -> None:
        if message.command_id != self._execution.pending_command_id:
            return
        self._execution.pending_acks.add(message.pc_id)
        if self._execution.pending_acks >= set(self._execution.participants):
            self._execution.pending_command = ''
            self._execution.pending_command_id = ''
            self._execution.pending_ack_deadline = 0.0
            self._execution.pending_scheduled_at = 0.0

    def _enforce_schedule_ack_deadline(self) -> None:
        reason = ''
        command = ''
        with self._lock:
            if not self._execution.pending_command_id or time.monotonic() < self._execution.pending_ack_deadline:
                return
            missing = sorted(set(self._execution.participants) - self._execution.pending_acks)
            if not missing:
                return
            scheduled_at = self._execution.pending_scheduled_at
            command = 'cancel_before_start' if self._execution.pending_command in {
                'prepare', 'initialize_at', 'cycle_initialize_at', 'start_at',
                'cancel_before_start',
            } else 'stop_now'
            if (
                self._execution.pending_command == 'start_at'
                and scheduled_at > 0.0
                and time.monotonic() >= scheduled_at
            ):
                command = 'stop_now'
            reason = f'그룹 예약 확인 제한시간 초과 · {", ".join(missing)}'
        if command == 'cancel_before_start':
            self._cancel_before_start(reason, code='GROUP_SCHEDULE_ACK_TIMEOUT')
        elif command:
            self._stop_for_peer_failure(reason)
        if reason:
            self.get_logger().error(reason)

    def _enforce_motion_start_report_deadline(self) -> None:
        with self._lock:
            if (
                not self._execution.motion_start_report_deadline
                or time.monotonic() < self._execution.motion_start_report_deadline
                or not self._execution.execution_id
                or self._execution.coordinator_id != self._config.pc_id
            ):
                return
            missing = sorted(
                set(self._execution.participants)
                - set(self._execution.triggered)
            )
            if not missing:
                self._execution.motion_start_report_deadline = 0.0
                return
            execution_id = self._execution.execution_id
            self._execution.motion_start_report_deadline = 0.0
        reason = '모션 시작 트리거 보고 제한시간 초과: ' + ', '.join(missing)
        self._stop_for_peer_failure(reason)
        self._publish_coordination_error(
            code='GROUP_MOTION_START_REPORT_TIMEOUT',
            message=reason,
            execution_id=execution_id,
        )

    def _broadcast_stop(self, command: str) -> None:
        if not self._execution.execution_id:
            return
        message = self._new_command(
            command=command,
            execution_id=self._execution.execution_id,
            cycle_number=self._execution.cycle_number,
            participants=self._execution.participants,
        )
        self._command_pub.publish(message)

    def _begin_group_release(self) -> None:
        """Keep the lease until every participant confirms worker release."""
        with self._lock:
            if not self._execution.execution_id:
                return
            if self._execution.pending_command == 'cancel_before_start':
                return
            execution_id = self._execution.execution_id
            message = self._new_command(
                command='cancel_before_start',
                execution_id=execution_id,
                cycle_number=self._execution.cycle_number,
                participants=self._execution.participants,
            )
            release_participants = self._execution.participants
        local_release = self._release_local_worker(
            execution_id,
            network_operation_id=message.command_id,
            participants=release_participants,
        )
        if not local_release.get('success'):
            self.get_logger().error(
                '그룹 해제 전 로컬 worker 정리 실패: '
                f'{local_release.get("message") or "응답 없음"}'
            )
        self._enter_group_release(cancel_message=message, local_release=local_release)

    def _new_command(
        self, *, command: str, execution_id: str, cycle_number: int,
        participants: tuple[str, ...], command_id: str = '',
        scheduled_monotonic: float = 0.0,
        repeat_mode: str = '', dwell_sec: float = 0.0,
        target_cycle_count: int = 0,
        initialization_only: bool = False,
        run_mode: str = '',
    ) -> GroupCommand:
        message = GroupCommand()
        message.group_id = self._config.group_id
        message.execution_id = execution_id
        message.command_id = command_id or f'cmd-{uuid.uuid4().hex}'
        message.coordinator_id = self._config.pc_id
        message.command = command
        message.sequence = self._next_sequence()
        message.cycle_number = int(cycle_number)
        _set_stamp(message.sent_at, time.time())
        message.scheduled_monotonic_ns = int(
            max(float(scheduled_monotonic), 0.0) * 1_000_000_000
        )
        message.participant_ids = list(participants)
        message.repeat_mode = str(repeat_mode)
        message.dwell_sec = float(dwell_sec)
        _set_optional_message_field(
            message, 'target_cycle_count', int(target_cycle_count)
        )
        message.initialization_only = bool(initialization_only)
        message.run_mode = str(run_mode)
        return message

    def _publish_event(
        self, command: GroupCommand, event: str, success: bool, message_text: str,
        *, triggered_at: float = 0.0,
    ) -> None:
        message = GroupEvent()
        message.group_id = self._config.group_id
        message.execution_id = command.execution_id
        message.command_id = command.command_id
        message.pc_id = self._config.pc_id
        message.boot_id = self._boot_id
        message.event = event
        message.state = self._local_group_state()
        message.sequence = self._next_sequence()
        message.cycle_number = int(command.cycle_number)
        _set_stamp(message.occurred_at, time.time())
        message.triggered_monotonic_ns = (
            local_to_coordinator_ns(
                int(float(triggered_at) * 1_000_000_000),
                self._local_sync_offset_ns,
            )
            if triggered_at > 0.0 else 0
        )
        message.success = bool(success)
        message.message = str(message_text)[:512]
        self._event_pub.publish(message)

    def _publish_runtime_event(
        self, event: str, execution_id: str, cycle_number: int,
        *, success: bool = True, triggered_at: float = 0.0, message_text: str = '',
    ) -> None:
        command = GroupCommand()
        command.execution_id = execution_id
        command.command_id = f'runtime-{event}-{cycle_number}'
        command.cycle_number = int(cycle_number)
        self._publish_event(
            command, event, success, message_text or event,
            triggered_at=triggered_at,
        )

    def _emit_local_runtime_event(self) -> None:
        with self._lock:
            status = self._local_status.get('motion_run_status')
            status = status if isinstance(status, Mapping) else {}
            if not status.get('group_execution'):
                return
            execution_id = str(status.get('execution_id') or '')
            if not execution_id or execution_id != self._execution.execution_id:
                return
            phase = str(status.get('phase') or '')
            cycle = int(status.get('group_cycle_number') or status.get('current_cycle') or 0)
            event = ''
            triggered_at = 0.0
            success = True
            if phase == 'group_armed':
                event = 'armed'
                triggered_at = float(
                    status.get('initialize_triggered_monotonic') or 0.0
                )
            elif phase == 'running':
                event = 'motion_started'
                triggered_at = float(
                    (status.get('lifecycle') or {}).get(
                        'motion_started_monotonic'
                    ) or 0.0
                )
            elif phase == 'group_motion_completed':
                event = 'motion_completed'
                cycle = int(status.get('current_cycle') or cycle)
            elif phase == 'group_cycle_initialized':
                event = 'cycle_initialized'
                cycle = int(status.get('current_cycle') or cycle)
            elif phase == 'error':
                event = 'error'
                success = False
            elif phase == 'stopped':
                if self._execution.pending_command == 'cancel_before_start':
                    return
                event = 'stopped'
            if not event:
                return
            key = (execution_id, event, cycle, triggered_at, success)
            if key == self._last_local_event_key:
                return
            self._last_local_event_key = key
        self._publish_runtime_event(
            event, execution_id, cycle, success=success,
            triggered_at=triggered_at,
            message_text=str(status.get('message') or event),
        )

    def _enforce_execution_membership(self) -> None:
        with self._lock:
            if not self._execution.execution_id:
                return
            if self._execution.state in {'stopped', 'error', 'releasing'}:
                return
            missing = [
                pc_id for pc_id in self._execution.participants
                if pc_id != self._config.pc_id
                and self._registry.status(pc_id) == 'offline'
            ]
            if not missing:
                return
            execution_id = self._execution.execution_id
            participants = self._execution.participants
            cycle_number = self._execution.cycle_number
        stop_outcome = self._issue_stop_now(
            execution_id=execution_id, participants=participants,
            cycle_number=cycle_number,
        )
        with self._lock:
            if self._execution.execution_id == execution_id:
                self._execution.stop_now(error=True)
                if stop_outcome.local_success:
                    self._clear_active_execution()
        self.get_logger().error(
            f'그룹 참가 PC 통신 단절 · 전체 정지: {", ".join(missing)}'
        )
        self._publish_coordination_error(
            code='GROUP_PARTICIPANT_DISCONNECTED',
            message='그룹 참가 PC 통신 단절: ' + ', '.join(missing),
            execution_id=execution_id,
        )

    def _accept_execution_claim(
        self, message: GroupCommand, participants: tuple[str, ...]
    ) -> None:
        with self._lock:
            if message.coordinator_id not in participants:
                raise ValueError('임시 진행 PC가 그룹 실행 참가 목록에 없습니다')
            expected = tuple(sorted(set(
                (self._config.pc_id, *self._registry.joined())
            )))
            if participants != expected:
                raise ValueError(
                    'PC별 그룹 참가 목록이 일치하지 않습니다: '
                    f'수신={list(participants)}, 로컬={list(expected)}'
                )
            if self._execution.execution_id and self._execution.execution_id != message.execution_id:
                # Deterministic arbitration prevents two simultaneous initiators
                # from leaving the group split between different executions.
                winner = min(self._execution.coordinator_id, message.coordinator_id)
                if winner != message.coordinator_id:
                    raise ValueError('다른 임시 진행 PC의 그룹 실행이 이미 활성 상태입니다')
                previous_execution_id = self._execution.execution_id
                self._call_local_control({
                    'command': 'group_cancel',
                    'execution_id': previous_execution_id,
                    'network_operation_id': f'claim-replaced-{uuid.uuid4().hex}',
                })
                self._execution.reset()
                self._clear_active_execution()
            if self._execution.execution_id != message.execution_id:
                self._execution.activate_claim(
                    message.execution_id, message.coordinator_id, participants,
                )
                self._execution.target_cycle_count = _message_uint32(
                    message, 'target_cycle_count'
                )
            else:
                self._execution.coordinator_id = message.coordinator_id
                self._execution.participants = participants
            if message.coordinator_id != self._config.pc_id:
                self._trigger_sync_status = {
                    'trigger_sync_state': 'sync_waiting',
                    'trigger_sync_uncertainty_ms': 0.0,
                    'trigger_sync_source': 'dds_relative_monotonic',
                    'coordinator_id': message.coordinator_id,
                }

    def _require_active_command(
        self, message: GroupCommand, participants: tuple[str, ...]
    ) -> None:
        with self._lock:
            if (
                message.execution_id != self._execution.execution_id
                or message.coordinator_id != self._execution.coordinator_id
                or participants != self._execution.participants
            ):
                raise ValueError('그룹 실행 ID·임시 진행 PC·참가 목록이 일치하지 않습니다')

    def _local_group_execution_id(self) -> str:
        status = self._local_status.get('motion_run_status')
        if isinstance(status, Mapping) and status.get('group_execution'):
            return str(status.get('execution_id') or '')
        return ''

    @staticmethod
    def _resolve_motion_cycle(local_status: Mapping[str, Any]) -> int:
        """Return motion_run display_cycle when available."""
        if not isinstance(local_status, Mapping):
            return 0
        return int(
            local_status.get('display_cycle')
            or local_status.get('group_cycle_number')
            or local_status.get('current_cycle')
            or 0
        )

    def _stop_command_matches(
        self, execution_id: str, participants: tuple[str, ...],
    ) -> bool:
        if not execution_id:
            return False
        with self._lock:
            local_execution_id = self._execution.execution_id
            local_participants = self._execution.participants
        if (
            execution_id == local_execution_id
            and (
                not local_participants
                or set(local_participants) == set(participants)
            )
        ):
            return True
        return execution_id == self._local_group_execution_id()

    def _require_stop_command(
        self, message: GroupCommand, participants: tuple[str, ...]
    ) -> None:
        if self._config.pc_id not in participants:
            raise ValueError('그룹 정지 요청에 이 PC가 포함되어 있지 않습니다')
        if message.coordinator_id not in participants:
            raise ValueError('그룹 정지 요청 발신 PC가 참가 목록에 없습니다')
        if self._stop_command_matches(message.execution_id, participants):
            return
        raise ValueError('그룹 정지 요청의 실행 ID·참가 목록이 일치하지 않습니다')

    def _local_schedule_ns(self, message: GroupCommand) -> int:
        with self._lock:
            if self._trigger_sync_status.get('trigger_sync_state') != 'ready':
                raise ValueError('DDS 트리거 동기화 상태를 확인하세요')
            local_target_ns = coordinator_to_local_ns(
                int(message.scheduled_monotonic_ns),
                self._local_sync_offset_ns,
            )
        remaining_ns = local_target_ns - time.monotonic_ns()
        if remaining_ns < int(self._config.schedule_ack_margin_sec * 1_000_000_000):
            raise ValueError('그룹 예약 트리거의 준비 여유가 부족합니다')
        return local_target_ns

    def _local_readiness(self) -> Dict[str, Any]:
        return self._local_http('/api/coordination/local-readiness', {})

    def _call_local_control(
        self, payload: Mapping[str, Any], *, timeout_sec: float = 4.0,
    ) -> Dict[str, Any]:
        return self._local_http(
            '/api/coordination/local-control', payload,
            timeout_sec=timeout_sec,
        )

    def _issue_stop_now(
        self, *, execution_id: str, participants: tuple[str, ...],
        cycle_number: int, command_id: str = '',
    ) -> SafetyStopOutcome:
        with self._lock:
            self._cancelled_execution_ids.add(str(execution_id))
        stop_message = self._new_command(
            command='stop_now', execution_id=execution_id,
            cycle_number=cycle_number, participants=participants,
            command_id=command_id,
        )
        return self._safety_stop.stop_now(
            lambda: self._call_local_control({
                'command': 'stop_now',
                'execution_id': execution_id,
                'network_operation_id': stop_message.command_id,
            }, timeout_sec=0.25),
            lambda: self._command_pub.publish(stop_message),
        )

    def _local_http(
        self, path: str, payload: Optional[Mapping[str, Any]] = None,
        *, timeout_sec: float = 4.0,
    ) -> Dict[str, Any]:
        base_url = getattr(self, '_local_web_base_url', 'http://127.0.0.1:8000')
        url = f'{base_url}{path}'
        data = None
        method = 'GET'
        headers: Dict[str, str] = {}
        if payload is not None:
            data = json.dumps(dict(payload), separators=(',', ':')).encode('utf-8')
            method = 'POST'
            headers['Content-Type'] = 'application/json'
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=float(timeout_sec)) as response:
                value = json.loads(response.read(MAX_LOCAL_BODY_BYTES).decode('utf-8'))
            return value if isinstance(value, dict) else {
                'success': False, 'message': '로컬 응답 형식 오류'
            }
        except (OSError, ValueError, urllib.error.URLError) as exc:
            return {'success': False, 'message': f'로컬 Web Bridge 응답 없음: {exc}'}

    def _fetch_local_runtime_status(self) -> Dict[str, Any]:
        result = self._local_http(
            '/api/coordination/local-status', timeout_sec=0.25,
        )
        if result.get('bridge_state') != 'ok':
            raise OSError(result.get('message') or '로컬 Web Bridge 상태 응답 없음')
        return result

    def _consume_local_runtime_status(self) -> None:
        sample = self._local_runtime_monitor.snapshot()
        result = sample.get('status')
        result = result if isinstance(result, Mapping) else {}
        received_monotonic = float(sample.get('received_monotonic') or 0.0)
        active_since = float(
            sample.get('active_since_monotonic') or 0.0
        )
        with self._lock:
            execution_active = bool(self._execution.execution_id)
        now = time.monotonic()
        waiting_for_active_sample = bool(
            execution_active
            and active_since
            and received_monotonic < active_since
            and now - active_since <= LOCAL_RUNTIME_ACTIVE_TIMEOUT_SEC
        )
        if waiting_for_active_sample:
            return
        if (
            execution_active
            and (
                not received_monotonic
                or now - received_monotonic
                > LOCAL_RUNTIME_ACTIVE_TIMEOUT_SEC
            )
        ):
            detail = str(sample.get('error') or '상태 갱신 제한시간 초과')
            self._stop_for_peer_failure(
                f'로컬 Web Bridge 상태 수신 중단: {detail}'
            )
            return
        if not result:
            return
        with self._lock:
            self._local_status = dict(result)
        self._publish_local_alarm_if_changed()

    def _publish_local_alarm_if_changed(self) -> None:
        safety = self._local_status.get('safety_status')
        safety = safety if isinstance(safety, Mapping) else {}
        grade = int(safety.get('servo_alarm_grade') or 0)
        active = safety.get('servo_alarm_active')
        active = active if isinstance(active, list) else []
        first = active[0] if active and isinstance(active[0], Mapping) else {}
        key = (grade, tuple(
            (int(item.get('axis') or -1), int(item.get('code') or 0), int(item.get('grade') or 0))
            for item in active if isinstance(item, Mapping)
        ))
        if key == self._last_alarm_key:
            return
        self._last_alarm_key = key
        message = GroupAlarm()
        message.group_id = self._config.group_id
        message.execution_id = self._execution.execution_id
        message.pc_id = self._config.pc_id
        message.boot_id = self._boot_id
        message.sequence = self._next_sequence()
        _set_stamp(message.occurred_at, time.time())
        message.grade = grade
        message.motor_axis = int(first.get('axis') or -1)
        message.error_code = str(first.get('code') or '')
        message.error_source = 'servo_alarm'
        message.action = {
            1: '해당 에러축 정지·다음 회차 차단',
            2: '전체 모션 즉시 정지',
            3: '전체 모터 제어 차단',
        }.get(grade, '')
        message.message = str(safety.get('message') or '')[:512]
        message.active = grade > 0
        if self._joined and self._config.group_id:
            self._alarm_pub.publish(message)
        if grade >= 2 and self._execution.execution_id:
            execution_id = self._execution.execution_id
            participants = self._execution.participants
            self._issue_stop_now(
                execution_id=execution_id, participants=participants,
                cycle_number=self._execution.cycle_number,
                command_id=f'local-alarm-{message.sequence}',
            )
            self._execution.stop_now(error=True)
            self._clear_active_execution()
        elif grade == 1 and self._execution.execution_id:
            self._execution.stop_after_cycle = True
            self._call_local_control({
                'command': 'stop_after_cycle',
                'execution_id': self._execution.execution_id,
                'network_operation_id': f'local-grade1-{message.sequence}',
            })
            if self._execution.coordinator_id == self._config.pc_id:
                self._broadcast_stop('stop_after_cycle')

    def snapshot(self) -> Dict[str, Any]:
        now = time.monotonic()
        with self._lock:
            execution_id = self._execution.execution_id
        execution_active = bool(execution_id)
        peers = []
        for pc_id in self._registry.joined():
            member = self._registry.member(pc_id)
            if member is None:
                continue
            peers.append(enrich_peer_row({
                'pc_id': pc_id,
                'display_name': member.display_name,
                'is_master': member.is_master,
                'git_branch': member.git_branch,
                'git_hash': member.git_hash,
                'git_message': member.git_message,
                'state': self._registry.status(pc_id, now=now),
                'motion_state': member.state,
                'trigger_sync_state': member.trigger_sync_state,
                'trigger_sync_uncertainty_ms': (
                    member.trigger_sync_uncertainty_ms
                ),
                'servo_alarm_grade': member.alarm_grade,
                'motion_phase': member.motion_phase,
                'motion_elapsed_sec': member.motion_elapsed_sec,
                'motion_duration_sec': member.motion_duration_sec,
                'motion_progress_ratio': member.motion_progress_ratio,
                'current_cycle': member.current_cycle,
                'display_cycle': member.current_cycle,
                'display_step': member.display_step,
            }, execution_active=execution_active))
        with self._lock:
            local_status = self._local_status.get('motion_run_status')
            local_status = local_status if isinstance(local_status, Mapping) else {}
            execution_state = self._execution.state
            execution_id = self._execution.execution_id
            participants = self._execution.participants
            cycle_number = self._execution.cycle_number
            motion_cycle = self._resolve_motion_cycle(local_status)
            if self._execution.execution_id and motion_cycle > 0:
                cycle_number = motion_cycle
            if (
                self._execution.execution_id
                and self._execution.coordinator_id != self._config.pc_id
            ):
                execution_state = self._local_group_state()
                execution_id = self._execution.execution_id
                participants = self._execution.participants
            local_peer = enrich_peer_row({
                'pc_id': self._config.pc_id,
                'display_name': self._config.display_name,
                'git_branch': self._git_branch,
                'git_hash': self._git_hash,
                'git_message': self._git_message,
                'motion_state': self._local_group_state(),
                'trigger_sync_state': str(
                    self._trigger_sync_status.get(
                        'trigger_sync_state'
                    ) or 'idle'
                ),
                'trigger_sync_uncertainty_ms': float(
                    self._trigger_sync_status.get(
                        'trigger_sync_uncertainty_ms'
                    ) or 0.0
                ),
                'servo_alarm_grade': self._local_alarm_grade(),
                'motion_phase': str(local_status.get('phase') or ''),
                'motion_elapsed_sec': float(
                    (local_status.get('progress') or {}).get('elapsed_sec') or 0.0
                ),
                'motion_duration_sec': float(
                    (local_status.get('progress') or {}).get('duration_sec') or 0.0
                ),
                'motion_progress_ratio': float(
                    (local_status.get('progress') or {}).get('ratio') or 0.0
                ),
                'current_cycle': motion_cycle,
                'display_cycle': motion_cycle,
                'display_step': str(local_status.get('display_step') or ''),
            }, execution_active=execution_active)
            if local_peer.get('motion_cycle'):
                cycle_number = int(local_peer['motion_cycle'])
            return {
                'node_connected': True,
                'transport': 'ros2_dds',
                'config': {
                    'pc_id': self._config.pc_id,
                    'display_name': self._config.display_name,
                    'enabled': self._config.enabled,
                    'group_id': self._config.group_id,
                    'dds_domain_id': self._config.dds_domain_id,
                },
                'joined': self._joined,
                'local': local_peer,
                'peers': peers,
                'alarms': [
                    dict(self._alarm_registry.alarms[pc_id])
                    for pc_id in sorted(self._alarm_registry.alarms)
                ],
                'execution': {
                    'state': execution_state,
                    'execution_id': execution_id,
                    'coordinator_id': self._execution.coordinator_id,
                    'participants': list(participants),
                    'cycle_number': cycle_number,
                    'target_cycle_count': self._execution.target_cycle_count,
                    'stop_after_cycle': self._execution.stop_after_cycle,
                    'initialize_spread_ms': self._execution.last_initialize_spread_ms,
                    'initialize_within_20ms': self._execution.initialize_within_tolerance(),
                    'start_spread_ms': self._execution.last_start_spread_ms,
                    'start_within_20ms': self._execution.trigger_within_tolerance(),
                },
                'trigger_sync': dict(self._trigger_sync_status),
                'coordination_error': dict(self._coordination_error),
                'timeouts': {
                    'heartbeat_sec': self._config.heartbeat_sec,
                    'warning_sec': self._config.warning_timeout_sec,
                    'offline_sec': self._config.peer_timeout_sec,
                    'start_lead_sec': self._config.start_lead_sec,
                    'trigger_report_timeout_sec': (
                        self._config.trigger_report_timeout_sec
                    ),
                },
            }

    def _local_group_state(self) -> str:
        status = self._local_status.get('motion_run_status')
        if isinstance(status, Mapping) and status.get('group_execution'):
            return str(status.get('state') or 'unknown')
        return 'ready'

    def _local_alarm_grade(self) -> int:
        safety = self._local_status.get('safety_status')
        return int(safety.get('servo_alarm_grade') or 0) if isinstance(safety, Mapping) else 0

    def _command_seen(self, command_id: str) -> bool:
        with self._lock:
            if command_id in self._seen_commands:
                return True
            self._seen_commands[command_id] = time.monotonic()
            return False

    def _prune_seen_commands(self) -> None:
        cutoff = time.monotonic() - 86400.0
        with self._lock:
            self._seen_commands = {
                key: stamp for key, stamp in self._seen_commands.items() if stamp >= cutoff
            }
            if len(self._seen_commands) > 4096:
                rows = sorted(self._seen_commands.items(), key=lambda item: item[1])[-4096:]
                self._seen_commands = dict(rows)
            if len(self._cancelled_execution_ids) > 4096:
                active = self._execution.execution_id
                self._cancelled_execution_ids = {active} if active else set()

    def _clear_active_execution(self) -> None:
        self._execution.clear_active()
        self._sync_estimators.clear()
        self._sync_sent_samples.clear()
        self._sync_probes.clear()
        self._sync_ready.clear()
        self._sync_next_action = ''
        self._sync_deadline = 0.0
        self._sync_last_probe_at = 0.0
        self._local_sync_offset_ns = 0
        self._trigger_sync_status = {
            'trigger_sync_state': 'idle',
            'trigger_sync_uncertainty_ms': 0.0,
            'trigger_sync_source': 'dds_relative_monotonic',
        }

    def _execution_unhealthy_members(self) -> list[str]:
        unhealthy = []
        with self._lock:
            for pc_id in self._execution.participants:
                if pc_id == self._config.pc_id:
                    continue
                member = self._registry.member(pc_id)
                if (
                    member is None
                    or self._registry.status(pc_id) != 'online'
                    or member.alarm_grade > 0
                ):
                    unhealthy.append(pc_id)
        return sorted(set(unhealthy))

    def _stop_for_peer_failure(self, reason: str) -> None:
        with self._lock:
            if not self._execution.execution_id:
                return
            execution_id = self._execution.execution_id
            participants = self._execution.participants
            cycle_number = self._execution.cycle_number
        stop_outcome = self._issue_stop_now(
            execution_id=execution_id, participants=participants,
            cycle_number=cycle_number,
            command_id=f'peer-failure-{uuid.uuid4().hex}',
        )
        with self._lock:
            self._execution.stop_now(error=True)
            if stop_outcome.local_success:
                self._clear_active_execution()
        self.get_logger().error(f'그룹 참가 PC 오류 · 전체 정지: {reason}')
        self._publish_coordination_error(
            code='GROUP_PARTICIPANT_FAILURE',
            message=f'그룹 참가 PC 오류: {reason}',
            execution_id=execution_id,
        )

    def _next_sequence(self) -> int:
        with self._lock:
            self._sequence += 1
            return self._sequence

    def _stop_active_execution_for_shutdown(self) -> None:
        with self._lock:
            execution_id = self._execution.execution_id
            if not execution_id:
                return
            participants = self._execution.participants
            cycle_number = self._execution.cycle_number
        outcome = self._issue_stop_now(
            execution_id=execution_id,
            participants=participants,
            cycle_number=cycle_number,
            command_id=f'shutdown-{uuid.uuid4().hex}',
        )
        with self._lock:
            self._execution.stop_now(error=True)
            self._clear_active_execution()
        if not outcome.local_success:
            self.get_logger().error(
                '연동 서비스 종료 전 로컬 즉시 정지 확인 실패: '
                f'{outcome.local_result.get("message") or "응답 없음"}'
            )

    def destroy_node(self) -> bool:
        self._stop_active_execution_for_shutdown()
        if getattr(self, '_joined', False) and self._config.configured:
            try:
                self._publish_heartbeat(joined=False)
            except Exception:
                pass
            self._joined = False
        local_api = getattr(self, '_local_api', None)
        if local_api is not None:
            local_api.close()
        local_runtime_monitor = getattr(self, '_local_runtime_monitor', None)
        if local_runtime_monitor is not None:
            local_runtime_monitor.close()
        command_dispatcher = getattr(self, '_command_dispatcher', None)
        if command_dispatcher is not None:
            command_dispatcher.close()
        return super().destroy_node()


def main(args=None) -> None:
    workspace = Path(os.environ.get('MOTION_WORKSPACE') or Path.cwd()).resolve()
    config_path = Path(
        os.environ.get('MOTION_COORDINATION_CONFIG')
        or workspace / 'config/motion_coordination.yaml'
    ).expanduser()
    config, _migrated = migrate_legacy_group_config(config_path)
    rclpy.init(args=args, domain_id=config.dds_domain_id)
    node = MotionCoordinationNode(config)
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()
