"""모션 재생 · 초기 위치 이동 · 주기 반복 · 모터 명령 발행.

`MotionRunManager`에서 떼어냈다 · §5 분해 목표안의 `MotionPlayer` · §6-31

**모터를 실제로 움직이는 코드다.** 최종 출력은 여전히 `motion_supervisor`가
단독으로 발행하고(§2), 이 객체는 그쪽으로 목표값을 보낼 뿐이다. 옮기면서
계산도 순서도 바꾸지 않았다 · 같은 값을 같은 차례로 보낸다.

노드에 남긴 것 · 실행 락과 정지 신호 · 상태 저장과 발행 · 자동 반복 상태 ·
현재 모터 목록 · 계획 수립기. 이 객체는 그것들을 `self.manager`로 본다.
"""

from __future__ import annotations

import json
import math
import time
import traceback
from typing import Any, Dict, List, Mapping, Optional

from motion_common.values import finite_float
from std_msgs.msg import Int8MultiArray, String

from . import motion_run_rules
from .motion_run_constants import (
    CW_ENABLE_OPERATION_MINAS,
    CW_NEW_SET_POINT_MINAS,
    DYNAMIXEL_TORQUE_ENABLE,
    ID_CONTROLWORD,
    ID_TARGET_POSITION,
    STATE_TIMEOUT_SEC,
)


class MotionPlayer:
    def __init__(self, manager: Any) -> None:
        self.manager = manager

    def _prepare_and_run(
        self,
        mode: str,
        payload: Dict[str, Any],
        motors_snapshot: List[Dict[str, Any]],
    ) -> None:
        try:
            if bool(payload.get('automation_run')):
                self.manager._wait_for_automation_ready(payload)
                motors_snapshot = self.manager._current_motors()
                with self.manager._run_lock:
                    self.manager._automation_runtime['resume_pending'] = False

            if mode == 'initialize':
                plan = self.manager._plan_builder.build(
                    payload,
                    initialization_only=True,
                    motors_snapshot=motors_snapshot,
                )
                if self.manager._stop_event.is_set():
                    return
                self._run_initialization(plan)
                return

            plan = self.manager._plan_builder.build(
                payload,
                motors_snapshot=motors_snapshot,
            )
            initialization_plan = self.manager._plan_builder.build(
                payload,
                initialization_only=True,
                motors_snapshot=motors_snapshot,
            )
            if self.manager._stop_event.is_set():
                return
            # 시작 전에도 **내가 쓸 축만** 본다 · §6-106
            ownership_error = self.manager._playback_ownership_error(
                axes=[
                    int(axis_plan['motor_axis'])
                    for axis_plan in self._playback_axes(plan)
                ]
            )
            if ownership_error:
                raise ValueError(ownership_error)
            guard_error = motion_run_rules._motion_auto_start_guard_error(plan)
            if guard_error:
                raise ValueError(guard_error)
            self._run_initialization_then_motion(initialization_plan, plan)
        except InterruptedError:
            return
        except Exception as exc:
            if self.manager._stop_event.is_set():
                return
            self.manager.get_logger().error(
                f'motion run preparation failed: {mode}\n{traceback.format_exc()}'
            )
            status = motion_run_rules._empty_status()
            status.update({
                'state': 'error',
                'phase': 'error',
                'message': f'모션 실행 준비 실패: {exc}',
                'project_id': str(payload.get('project_id') or ''),
                'motion_file_id': str(payload.get('motion_file_id') or ''),
                'mapping_file_id': str(payload.get('mapping_file_id') or ''),
                'run_mode': str(payload.get('run_mode') or 'once'),
                'automation_run': bool(payload.get('automation_run')),
                'operation_generation': int(
                    payload.get('operation_generation') or 0
                ),
                'request_source': str(
                    payload.get('request_source') or 'motion_run'
                ),
                'phase_finished_at': time.time(),
            })
            self.manager._set_status(status)
            if bool(payload.get('automation_run')):
                self.manager._automation_failure(str(exc))

    def _run_initialization_then_motion(
        self,
        initialization_plan: Dict[str, Any],
        motion_plan: Dict[str, Any],
    ) -> None:
        self._run_initialization(initialization_plan)
        if self.manager._stop_event.is_set():
            return
        if self.manager.status().get('state') != 'initialized':
            return
        if not self._run_countdown(motion_plan):
            return
        if (
            motion_plan.get('automation_run')
            and self.manager._graceful_stop_event.is_set()
        ):
            self._finish_cycle_stop(
                motion_plan,
                time.time(),
                0,
                '초기위치 이동 완료 후 자동 반복 정지',
            )
            return
        if motion_plan.get('repeat_mode') in {'reinitialize', 'dwell_reinitialize'}:
            self._run_motion(motion_plan, initialization_plan)
        else:
            self._run_motion(motion_plan)

    def _run_initialization(self, plan: Dict[str, Any]) -> None:
        try:
            if self.manager._stop_event.is_set():
                raise InterruptedError()
            group_cycle_context = self._group_cycle_context(plan)
            init_axes = list(plan['axes'])
            if not init_axes:
                now = time.time()
                status = motion_run_rules._status_from_plan('initialized', '초기 위치 이동 대상이 없습니다', plan)
                status['phase'] = 'initialized'
                status.update(group_cycle_context)
                status['phase_started_at'] = now
                status['phase_finished_at'] = now
                status['lifecycle'] = {
                    **self.manager._current_lifecycle(),
                    'initial_started_at': now,
                    'initial_finished_at': now,
                }
                self.manager._set_status(status)
                return

            motors = self._wait_for_current_motors()
            starts: Dict[int, float] = {}
            targets: Dict[int, float] = {}
            durations: Dict[int, float] = {}
            for axis in init_axes:
                motor_axis = int(axis['motor_axis'])
                motor = self.manager._motor_for_axis(motor_axis, motors)
                motor_error = motion_run_rules._motor_ready_error(
                    motor or {'controller_index': motor_axis}
                )
                if motor_error:
                    raise RuntimeError(motor_error)
                current = motion_run_rules._motor_position_deg(motor)
                if current is None:
                    raise RuntimeError(f'Axis {motor_axis} current position is unavailable')
                starts[motor_axis] = current
                targets[motor_axis] = float(axis['initial_motor_target_deg'])
                durations[motor_axis] = max(float(axis.get('initial_move_time_sec') or 0.0), self.manager.period_sec)

            max_duration = max(durations.values()) if durations else self.manager.period_sec
            initial_started_at = time.time()
            status = motion_run_rules._status_from_plan('initializing', '초기 위치 이동 중', plan)
            status['phase'] = 'initializing'
            status.update(group_cycle_context)
            status['phase_started_at'] = initial_started_at
            status['phase_finished_at'] = None
            status['lifecycle'] = {
                **self.manager._current_lifecycle(),
                'initial_started_at': initial_started_at,
                'initial_finished_at': None,
            }
            if plan.get('automation_run'):
                with self.manager._run_lock:
                    self.manager._automation_runtime.update({
                        'state': 'initializing',
                        'message': '자동 반복 초기위치 이동 중',
                    })
            self.manager._set_status(status)
            self._run_initial_position_stream(
                motors,
                init_axes,
                starts,
                targets,
                durations,
                max_duration,
            )
            reached, message = self._wait_for_targets(
                init_axes,
                targets,
                self._target_settle_timeout_sec(),
            )
            if not reached:
                raise RuntimeError(f'초기 위치 도달 확인 실패: {message}')
            self._publish_motion_values({
                str(axis['motion_id']): float(axis['initial_motion_position_deg'])
                for axis in init_axes
            })
            initial_finished_at = time.time()
            status = motion_run_rules._status_from_plan('initialized', '초기 위치 이동 완료', plan)
            status['phase'] = 'initialized'
            status.update(group_cycle_context)
            status['phase_started_at'] = initial_started_at
            status['phase_finished_at'] = initial_finished_at
            status['lifecycle'] = {
                **self.manager._current_lifecycle(),
                'initial_started_at': initial_started_at,
                'initial_finished_at': initial_finished_at,
            }
            if plan.get('automation_run'):
                with self.manager._run_lock:
                    self.manager._automation_runtime.update({
                        'state': 'initialized',
                        'message': '자동 반복 초기위치 이동 완료',
                    })
            self.manager._set_status(status)
        except InterruptedError:
            status = motion_run_rules._status_from_plan('stopped', '초기 위치 이동 정지', plan)
            status['phase'] = 'stopped'
            status['phase_finished_at'] = time.time()
            status['lifecycle'] = self.manager._current_lifecycle()
            self.manager._set_status(status)
        except Exception as exc:
            self.manager.get_logger().error(f'initial position move failed\n{traceback.format_exc()}')
            status = motion_run_rules._status_from_plan('error', f'초기 위치 이동 실패: {exc}', plan)
            status['phase'] = 'error'
            status['phase_finished_at'] = time.time()
            status['lifecycle'] = self.manager._current_lifecycle()
            self.manager._set_status(status)
            if plan.get('automation_run'):
                self.manager._automation_failure(str(exc))

    def _run_motion(
        self,
        plan: Dict[str, Any],
        initialization_plan: Optional[Dict[str, Any]] = None,
    ) -> None:
        try:
            run_mode = str(plan.get('run_mode') or 'once')
            continuous = run_mode == 'continuous'
            automation_run = bool(plan.get('automation_run'))
            repeat_mode = str(plan.get('repeat_mode') or 'direct')
            dwell_sec = max(float(plan.get('dwell_sec') or 0.0), 0.0)
            self._require_playback_command_allowed(self._playback_axes(plan))
            motors = self.manager._current_motors()
            self._prepare_motion_stream(motors, plan['axes'])
            motion_started_at = time.time()
            motion_started_monotonic = time.monotonic()
            running_message = (
                '자동 반복 모션 실행 중'
                if automation_run
                else ('연속 모션 실행 중' if continuous else '모션 1회 실행 중')
            )
            status = motion_run_rules._status_from_plan('running', running_message, plan)
            status['phase'] = 'running'
            status['phase_started_at'] = motion_started_at
            status['phase_finished_at'] = None
            status['lifecycle'] = {
                **self.manager._current_lifecycle(),
                'motion_started_at': motion_started_at,
                'motion_started_monotonic': motion_started_monotonic,
                'motion_finished_at': None,
            }
            requested_start_at = float(plan.get('scheduled_start_at') or 0.0)
            if requested_start_at:
                status['requested_start_at'] = requested_start_at
                status['actual_start_at'] = motion_started_at
                status['start_error_ms'] = round(
                    (motion_started_at - requested_start_at) * 1000.0, 3
                )
            if automation_run:
                with self.manager._run_lock:
                    self.manager._automation_runtime.update({
                        'state': 'running',
                        'message': running_message,
                    })
            playback_cycle = motion_run_rules._playback_cycle_number(plan, 0)
            if playback_cycle > 0:
                status['current_cycle'] = playback_cycle
            self.manager._set_status(status)
            samples = plan['samples']
            cycle_count = 0
            grade1_seen = False
            while True:
                cycle_started = time.monotonic()
                for index, sample in enumerate(samples):
                    if self.manager._stop_event.is_set():
                        status = motion_run_rules._status_from_plan('stopped', '연속 모션 정지' if continuous else '모션 실행 정지', plan)
                        status['phase'] = 'stopped'
                        status['phase_started_at'] = motion_started_at
                        status['phase_finished_at'] = time.time()
                        status['lifecycle'] = self.manager._current_lifecycle()
                        status['cycle_count'] = cycle_count
                        self.manager._set_status(status)
                        return
                    self._require_playback_command_allowed(self._playback_axes(plan))
                    if automation_run and self._current_servo_alarm_grade() == 1:
                        grade1_seen = True
                    positions = self._owned_positions(
                        plan, sample['positions'], float(sample['time_sec']),
                    )
                    self._publish_motion_setpoints(
                        motors,
                        plan['axes'],
                        positions,
                        sample.get('motion_values'),
                    )
                    self.manager._update_progress(
                        'running',
                        float(sample['time_sec']),
                        float(plan['summary']['duration_sec']),
                        index,
                        len(positions),
                        run_mode=run_mode,
                        cycle_count=cycle_count,
                        current_cycle=motion_run_rules._playback_cycle_number(
                            plan, cycle_count,
                        ),
                    )
                    motion_run_rules._sleep_until(cycle_started + ((index + 1) * self.manager.period_sec))
                cycle_count += 1
                synchronized_count = int(plan.get('synchronized_repeat_count') or 0)
                if synchronized_count:
                    if self.manager._graceful_stop_event.is_set():
                        self._finish_cycle_stop(
                            plan, motion_started_at, cycle_count,
                            '현재 동기 반복 회차 완료 후 정지',
                        )
                        return
                    if cycle_count >= synchronized_count:
                        break
                    if not self._wait_synchronized_boundary(
                        plan, motors, samples, cycle_count
                    ):
                        return
                    continue
                if not continuous:
                    break
                if automation_run and grade1_seen:
                    self.manager._automation_failure(
                        '1등급 서보 에러 · 나머지 축의 현재 회차 완료 후 자동 반복 중단'
                    )
                    self._finish_cycle_stop(
                        plan,
                        motion_started_at,
                        cycle_count,
                        '1등급 서보 에러로 자동 반복 중단',
                        state='error',
                    )
                    return
                target_cycle_count = int(plan['summary'].get('target_cycle_count') or 0)
                reached_target = target_cycle_count > 0 and cycle_count >= target_cycle_count
                if self.manager._graceful_stop_event.is_set() or reached_target:
                    stop_message = '설정된 목표 회차 도달로 자동 정지' if reached_target and not self.manager._graceful_stop_event.is_set() else '현재 모션 회차 완료 후 정지'
                    if automation_run:
                        self._finish_cycle_stop(
                            plan,
                            motion_started_at,
                            cycle_count,
                            stop_message,
                        )
                        return
                    else:
                        break
                if repeat_mode in {'dwell', 'dwell_reinitialize'} and dwell_sec > 0.0:
                    if not self._wait_between_cycles(
                        plan,
                        motion_started_at,
                        cycle_count,
                        dwell_sec,
                    ):
                        return
                if repeat_mode in {'reinitialize', 'dwell_reinitialize'}:
                    if initialization_plan is None:
                        raise RuntimeError('반복 초기위치 이동 계획이 없습니다')
                    self._run_initialization(initialization_plan)
                    if self.manager._stop_event.is_set():
                        return
                    if self.manager.status().get('state') != 'initialized':
                        raise RuntimeError(
                            self.manager.status().get('message')
                            or '반복 초기위치 이동 실패'
                        )
                    if self.manager._graceful_stop_event.is_set():
                        self._finish_cycle_stop(
                            plan,
                            motion_started_at,
                            cycle_count,
                            '반복 초기위치 이동 완료 후 정지',
                        )
                        return
                    self._require_playback_command_allowed(self._playback_axes(plan))
                    motors = self.manager._current_motors()
                    self._prepare_motion_stream(motors, plan['axes'])
                    self._restore_running_status(
                        plan,
                        motion_started_at,
                        cycle_count,
                    )

            final_positions = samples[-1]['positions'] if samples else {}
            if final_positions:
                self._publish_motion_setpoints(
                    motors,
                    plan['axes'],
                    final_positions,
                    samples[-1].get('motion_values'),
                )
                status = motion_run_rules._status_from_plan('verifying', '모션 최종 위치 확인 중', plan)
                status['phase'] = 'verifying'
                status['phase_started_at'] = motion_started_at
                status['phase_finished_at'] = None
                status['lifecycle'] = self.manager._current_lifecycle()
                status['progress'] = {
                    'elapsed_sec': float(plan['summary']['duration_sec']),
                    'duration_sec': float(plan['summary']['duration_sec']),
                    'ratio': 1.0,
                    'sample_index': len(samples),
                    'active_axis_count': len(final_positions),
                }
                self.manager._set_status(status)
                reached, message = self._wait_for_targets(
                    plan['axes'],
                    final_positions,
                    self._target_settle_timeout_sec(),
                )
                if not reached:
                    raise RuntimeError(f'모션 최종 위치 도달 확인 실패: {message}')
            motion_finished_at = time.time()
            status = motion_run_rules._status_from_plan('completed', '모션 실행 완료', plan)
            status['phase'] = 'completed'
            status['phase_started_at'] = motion_started_at
            status['phase_finished_at'] = motion_finished_at
            status['lifecycle'] = {
                **self.manager._current_lifecycle(),
                'motion_started_at': motion_started_at,
                'motion_finished_at': motion_finished_at,
            }
            status['progress'] = {
                'elapsed_sec': float(plan['summary']['duration_sec']),
                'duration_sec': float(plan['summary']['duration_sec']),
                'ratio': 1.0,
                'sample_index': len(samples),
                'active_axis_count': len(plan.get('axes', [])),
            }
            status['cycle_count'] = cycle_count
            self.manager._set_status(status)
        except Exception as exc:
            self.manager.get_logger().error(f'motion run failed\n{traceback.format_exc()}')
            status = motion_run_rules._status_from_plan('error', f'모션 실행 실패: {exc}', plan)
            status['phase'] = 'error'
            status['phase_finished_at'] = time.time()
            status['lifecycle'] = self.manager._current_lifecycle()
            self.manager._set_status(status)
            if bool(plan.get('automation_run')):
                self.manager._automation_failure(str(exc))

    def _run_initial_position_stream(
        self,
        motors: List[Dict[str, Any]],
        axes: List[Dict[str, Any]],
        starts: Dict[int, float],
        targets: Dict[int, float],
        durations: Dict[int, float],
        max_duration: float,
    ) -> None:
        """Move all initial axes with one combined command per control tick.

        Keeping all axes in one MotorStatus message prevents per-axis action
        threads from overwriting each other when many motors move together.
        """
        duration = max(float(max_duration), self.manager.period_sec)
        has_ac_axes = motion_run_rules._has_ac_axes(axes)
        clear_sec = self._setpoint_clear_sec() if has_ac_axes else 0.0
        tick_sec = self.manager.period_sec + clear_sec if has_ac_axes else self.manager.period_sec
        steps = max(1, int(math.ceil(duration / tick_sec)))
        start_time = time.monotonic()

        for step in range(steps + 1):
            if self.manager._stop_event.is_set():
                raise InterruptedError()
            self._require_playback_command_allowed(axes)

            elapsed = min(step * tick_sec, duration)
            positions: Dict[int, float] = {}
            for axis_plan in axes:
                motor_axis = int(axis_plan['motor_axis'])
                start = float(starts[motor_axis])
                target = float(targets[motor_axis])
                axis_duration = max(float(durations.get(motor_axis, duration)), self.manager.period_sec)
                ratio = min(max(elapsed / axis_duration, 0.0), 1.0)
                positions[motor_axis] = start + ((target - start) * motion_run_rules._smoothstep(ratio))

            self._publish_initial_positions(motors, axes, positions, has_ac_axes, clear_sec)
            self.manager._update_progress(
                'initializing',
                elapsed,
                duration,
                step,
                len(positions),
            )

            if step >= steps:
                break
            motion_run_rules._sleep_until(start_time + ((step + 1) * tick_sec))

        self._publish_initial_positions(motors, axes, targets, has_ac_axes, clear_sec)

    def _run_countdown(self, plan: Dict[str, Any]) -> bool:
        scheduled_at = float(plan.get('scheduled_start_at') or 0.0)
        duration = max(float(plan.get('countdown_sec') or 0.0), 0.0)
        if scheduled_at > 0.0:
            duration = max(scheduled_at - time.time(), 0.0)
            if duration <= 0.0:
                status = motion_run_rules._status_from_plan('error', '예약 시작 시각이 이미 지났습니다', plan)
                status['phase'] = 'error'
                self.manager._set_status(status)
                return False
        if duration <= 0.0:
            return True
        started_at = time.time()
        deadline = time.monotonic() + duration
        status = motion_run_rules._status_from_plan('countdown', '모션 시작 대기', plan)
        status['phase'] = 'countdown'
        status['phase_started_at'] = started_at
        status['phase_finished_at'] = None
        status['lifecycle'] = self.manager._current_lifecycle()
        self.manager._set_status(status)
        while True:
            if self.manager._stop_event.is_set():
                status = motion_run_rules._status_from_plan(
                    'stopped',
                    '모션 시작 대기 중 정지',
                    plan,
                )
                status['phase'] = 'stopped'
                status['phase_started_at'] = started_at
                status['phase_finished_at'] = time.time()
                status['lifecycle'] = self.manager._current_lifecycle()
                self.manager._set_status(status)
                return False
            remaining = max(deadline - time.monotonic(), 0.0)
            elapsed = min(duration - remaining, duration)
            self.manager._update_status({
                'state': 'countdown',
                'phase': 'countdown',
                'message': f'모션 시작 {max(math.ceil(remaining), 1)}초 전',
                'progress': {
                    'elapsed_sec': elapsed,
                    'duration_sec': duration,
                    'ratio': min(elapsed / duration, 1.0),
                    'sample_index': 0,
                    'active_axis_count': len(plan.get('axes') or []),
                },
            })
            if remaining <= 0.0:
                return True
            time.sleep(min(0.05, remaining))

    def _wait_between_cycles(
        self,
        plan: Dict[str, Any],
        motion_started_at: float,
        cycle_count: int,
        dwell_sec: float,
    ) -> bool:
        started_at = time.time()
        status = motion_run_rules._status_from_plan(
            'waiting',
            f'자동 반복 대기 중 · {dwell_sec:g}초',
            plan,
        )
        status['phase'] = 'repeat_waiting'
        status['phase_started_at'] = started_at
        status['phase_finished_at'] = None
        status['lifecycle'] = self.manager._current_lifecycle()
        status['cycle_count'] = cycle_count
        status['current_cycle'] = cycle_count
        duration_sec = float(plan['summary']['duration_sec'])
        status['progress'] = {
            'elapsed_sec': duration_sec,
            'duration_sec': duration_sec,
            'ratio': 1.0,
            'sample_index': len(plan.get('samples') or []),
            'active_axis_count': len(plan.get('axes') or []),
        }
        status['repeat_wait'] = {
            'duration_sec': dwell_sec,
            'remaining_sec': dwell_sec,
        }
        self.manager._set_status(status)
        deadline = time.monotonic() + dwell_sec
        while time.monotonic() < deadline:
            if self.manager._stop_event.is_set():
                self._finish_cycle_stop(
                    plan,
                    motion_started_at,
                    cycle_count,
                    '자동 반복 대기 중 즉시 정지',
                )
                return False
            if self.manager._graceful_stop_event.is_set():
                self._finish_cycle_stop(
                    plan,
                    motion_started_at,
                    cycle_count,
                    '자동 반복 대기 취소 후 정지',
                )
                return False
            remaining = max(deadline - time.monotonic(), 0.0)
            self.manager._update_status({
                'state': 'waiting',
                'phase': 'repeat_waiting',
                'message': f'다음 모션까지 {remaining:.1f}초',
                'repeat_wait': {
                    'duration_sec': dwell_sec,
                    'remaining_sec': remaining,
                },
            })
            time.sleep(min(0.1, remaining))
        self._restore_running_status(plan, motion_started_at, cycle_count)
        return True

    def _wait_for_targets(
        self,
        axes: List[Dict[str, Any]],
        targets: Dict[int, float],
        timeout_sec: float,
    ) -> tuple[bool, str]:
        deadline = time.monotonic() + max(float(timeout_sec), 0.0)
        last_message = ''
        while True:
            motors = self.manager._current_motors()
            ok = True
            messages = []
            for axis_plan in axes:
                motor_axis = int(axis_plan['motor_axis'])
                if motor_axis not in targets:
                    continue
                motor = self.manager._motor_for_axis(motor_axis, motors)
                ready_error = motion_run_rules._motor_ready_error(
                    motor or {'controller_index': motor_axis}
                )
                if ready_error:
                    return False, ready_error
                current = motion_run_rules._motor_position_deg(motor)
                target = float(targets[motor_axis])
                tolerance = self._target_tolerance_deg(axis_plan)
                if current is None:
                    ok = False
                    messages.append(f'Axis {motor_axis} current position is unavailable')
                    continue
                error = abs(current - target)
                if error > tolerance:
                    ok = False
                    messages.append(
                        f'Axis {motor_axis} current {current:.3f} deg, '
                        f'target {target:.3f} deg, error {error:.3f} deg'
                    )
            if ok:
                return True, 'targets reached'
            last_message = '; '.join(messages[:4])
            if time.monotonic() >= deadline:
                return False, last_message or 'target position was not reached'
            time.sleep(min(max(self.manager.period_sec, 0.01), 0.05))

    def _wait_synchronized_boundary(
        self, plan: Dict[str, Any], motors: List[Dict[str, Any]],
        samples: List[Dict[str, Any]], cycle_count: int,
    ) -> bool:
        """Hold the final target until an absolute cycle boundary."""
        first_start = float(plan.get('scheduled_start_at') or 0.0)
        cycle_sec = float(plan.get('synchronized_cycle_sec') or 0.0)
        deadline_wall = first_start + (cycle_count * cycle_sec)
        remaining = deadline_wall - time.time()
        if remaining < -self.manager.period_sec:
            raise RuntimeError('동기 반복 시작 시각을 놓쳤습니다')
        deadline = time.monotonic() + max(remaining, 0.0)
        final_sample = samples[-1] if samples else {}
        self.manager._update_status({
            'state': 'waiting', 'phase': 'waiting',
            'message': '다음 동기 반복 시작 대기',
            'next_start_at': deadline_wall,
        })
        while time.monotonic() < deadline:
            if self.manager._stop_event.is_set():
                return False
            if self.manager._graceful_stop_event.is_set():
                status = motion_run_rules._status_from_plan(
                    'stopped', '다음 동기 반복 시작 전 정지', plan
                )
                status['phase'] = 'stopped'
                status['cycle_count'] = cycle_count
                status['phase_finished_at'] = time.time()
                self.manager._set_status(status)
                return False
            if final_sample and plan.get('hold_final_until_cycle'):
                self._publish_motion_setpoints(
                    motors, plan['axes'], final_sample['positions'],
                    final_sample.get('motion_values'),
                )
            motion_run_rules._sleep_until(min(time.monotonic() + self.manager.period_sec, deadline))
        self._restore_running_status(plan, time.time(), cycle_count)
        return True

    def _finish_cycle_stop(
        self,
        plan: Dict[str, Any],
        motion_started_at: float,
        cycle_count: int,
        message: str,
        *,
        state: str = 'stopped',
    ) -> None:
        status = motion_run_rules._status_from_plan(state, message, plan)
        status['phase'] = state
        status['phase_started_at'] = motion_started_at
        status['phase_finished_at'] = time.time()
        status['lifecycle'] = self.manager._current_lifecycle()
        status['cycle_count'] = cycle_count
        status['current_cycle'] = cycle_count
        with self.manager._run_lock:
            self.manager._automation_runtime.update({
                'state': 'waiting',
                'message': status['message'],
            })
        self.manager._set_status(status)
        self.manager._graceful_stop_event.clear()
        if state != 'error':
            with self.manager._run_lock:
                # 부팅 자동 재생을 없애면서 `enabled` 도 없앴다 · §6-134 ·
                # 반복 방식은 늘 준비돼 있고, 시작만 사람·스케줄이 시킨다
                self.manager._automation_runtime.update({
                    'state': 'ready',
                    'message': message,
                    'stop_after_cycle': False,
                })

    def _restore_running_status(
        self,
        plan: Dict[str, Any],
        motion_started_at: float,
        cycle_count: int,
    ) -> None:
        message = (
            '자동 반복 모션 실행 중'
            if plan.get('automation_run')
            else '연속 모션 실행 중'
        )
        status = motion_run_rules._status_from_plan('running', message, plan)
        cycle_started_at = time.time()
        status['phase'] = 'running'
        status['phase_started_at'] = cycle_started_at
        status['phase_finished_at'] = None
        status['lifecycle'] = self.manager._current_lifecycle()
        status['cycle_count'] = cycle_count
        status['current_cycle'] = cycle_count + 1
        if plan.get('automation_run'):
            with self.manager._run_lock:
                self.manager._automation_runtime.update({
                    'state': 'running',
                    'message': message,
                })
        self.manager._set_status(status)

    def _group_cycle_context(self, plan: Mapping[str, Any]) -> Dict[str, int]:
        current = self.manager.status()
        cycle = int(
            plan.get('group_cycle_number')
            or current.get('group_cycle_number')
            or current.get('current_cycle')
            or 0
        )
        if cycle <= 0:
            return {}
        return {
            'group_execution': True,
            'execution_id': str(
                plan.get('execution_id') or current.get('execution_id') or ''
            ),
            'group_cycle_number': cycle,
            'current_cycle': cycle,
        }

    def _wait_for_current_motors(
        self,
        timeout_sec: float = STATE_TIMEOUT_SEC,
    ) -> List[Dict[str, Any]]:
        deadline = time.monotonic() + max(float(timeout_sec), 0.0)
        while True:
            motors = self.manager._current_motors()
            if motors:
                return motors
            if self.manager._stop_event.is_set():
                raise InterruptedError()
            remaining = deadline - time.monotonic()
            if remaining <= 0.0:
                return []
            time.sleep(min(max(self.manager.period_sec, 0.01), 0.05, remaining))

    def _current_servo_alarm_grade(self) -> int:
        lock = getattr(self.manager, '_safety_status_lock', None)
        if lock is None:
            return 0
        with lock:
            status = getattr(self.manager, '_latest_safety_status', None)
            payload = dict(status) if isinstance(status, dict) else {}
        try:
            grade = int(payload.get('servo_alarm_grade') or 0)
        except (TypeError, ValueError):
            return 0
        return grade if grade in (1, 2, 3) else 0

    def _prepare_motion_stream(
        self,
        motors: List[Dict[str, Any]],
        axes: List[Dict[str, Any]],
    ) -> None:
        """Prime AC servo axes once before frame-by-frame motion streaming."""
        if motion_run_rules._has_ac_axes(axes):
            self._publish_ac_enable_for_axes(motors, axes)
            time.sleep(self._setpoint_clear_sec())

    @staticmethod
    def _owned_positions(
        plan: Dict[str, Any],
        positions: Dict[int, float],
        time_sec: float,
    ) -> Dict[int, float]:
        """재생이 지금 쥔 축만 발행한다 · §6-73 §6-77

        오버더빙은 녹화된 축만 재생이 몰고 나머지 시간은 MIDI 로 녹화한다.
        소유 구간 밖인데도 계속 명령하면 `CommandArbiter` 가 그 축을 계속 쥐고
        있어 MIDI 가 영영 못 들어온다.

        구간 **밖**은 끝난 뒤만이 아니다 · 합성은 모든 축을 매 순간 채우므로
        10 초부터 데이터가 있는 축도 0 초부터 값이 나온다 · 시작 전도 거른다.

        `axis_playback_spans` 가 없으면 **아무것도 거르지 않는다** · 로컬·그룹
        실행은 이 값을 주지 않으므로 지금과 똑같이 돈다.
        """
        spans = plan.get('axis_playback_spans')
        if not spans:
            return positions
        return {
            motor_axis: value
            for motor_axis, value in positions.items()
            if motor_axis not in spans
            or any(
                start - 1e-9 <= time_sec <= end + 1e-9
                for start, end in spans[motor_axis]
            )
        }

    def _publish_motion_setpoints(
        self,
        motors: List[Dict[str, Any]],
        axes: List[Dict[str, Any]],
        positions: Dict[int, float],
        motion_values: Optional[Dict[str, float]] = None,
    ) -> None:
        if not positions:
            return
        self._publish_positions(motors, axes, positions)
        if motion_values:
            self._publish_motion_values(motion_values)

    def _publish_positions(
        self,
        motors: List[Dict[str, Any]],
        axes: List[Dict[str, Any]],
        positions: Dict[int, float],
    ) -> None:
        target_axes = motion_run_rules._sorted_controller_axes(positions.keys())
        command = motion_run_rules._empty_motor_command(target_axes)
        axes_by_index = {int(axis['motor_axis']): axis for axis in axes}
        for slot, motor_axis in enumerate(target_axes):
            target = positions.get(motor_axis)
            if target is None:
                continue
            axis_plan = axes_by_index.get(int(motor_axis), {})
            command.number_of_target_interfaces[slot] = 2
            command.target_interface_id[slot] = Int8MultiArray(
                data=[ID_CONTROLWORD, ID_TARGET_POSITION]
            )
            command.controlword[slot] = (
                DYNAMIXEL_TORQUE_ENABLE
                if axis_plan.get('motor_type') == 'dynamixel'
                else CW_NEW_SET_POINT_MINAS
            )
            command.position[slot] = float(target)
        self.manager._command_pub.publish(command)

    def _publish_motion_values(self, values: Dict[str, float]) -> None:
        publisher = getattr(self.manager, '_motion_value_pub', None)
        if publisher is None:
            return
        cleaned = {}
        for motion_id, value in values.items():
            number = finite_float(value)
            key = str(motion_id or '').strip()
            if key and number is not None:
                cleaned[key] = float(number)
        if not cleaned:
            return
        payload = {
            'source': 'motion_run',
            'project_id': str(self.manager._execution_context.get('project_id') or ''),
            'project_generation': int(
                self.manager._execution_context.get('project_generation') or 0
            ),
            'stamp': time.time(),
            'values': cleaned,
        }
        publisher.publish(String(data=json.dumps(payload, ensure_ascii=False)))

    def _publish_ac_enable_for_axes(
        self,
        motors: List[Dict[str, Any]],
        axes: List[Dict[str, Any]],
        positions: Optional[Dict[int, float]] = None,
    ) -> None:
        target_axes = set(int(axis) for axis in positions.keys()) if positions is not None else None
        ac_axes = [
            int(axis['motor_axis'])
            for axis in axes
            if axis.get('motor_type') == 'ac_servo'
            and (target_axes is None or int(axis['motor_axis']) in target_axes)
        ]
        if not ac_axes:
            return
        ac_axes = motion_run_rules._sorted_controller_axes(ac_axes)
        command = motion_run_rules._empty_motor_command(ac_axes)
        for slot, _axis in enumerate(ac_axes):
            command.number_of_target_interfaces[slot] = 1
            command.target_interface_id[slot] = Int8MultiArray(data=[ID_CONTROLWORD])
            command.controlword[slot] = CW_ENABLE_OPERATION_MINAS
        self.manager._command_pub.publish(command)

    def _publish_initial_positions(
        self,
        motors: List[Dict[str, Any]],
        axes: List[Dict[str, Any]],
        positions: Dict[int, float],
        has_ac_axes: bool,
        clear_sec: float,
    ) -> None:
        if has_ac_axes:
            self._publish_ac_enable_for_axes(motors, axes, positions)
            motion_run_rules._sleep_until(time.monotonic() + max(float(clear_sec), 0.0))
        self._publish_motion_setpoints(motors, axes, positions)

    def _setpoint_clear_sec(self) -> float:
        return max(self.manager.period_sec + 0.002, 0.002)

    def _target_settle_timeout_sec(self) -> float:
        return self.manager._runtime_float_parameter(
            'target_settle_timeout_sec',
            self.manager.target_settle_timeout_sec,
        )

    def _target_tolerance_deg(self, axis_plan: Dict[str, Any]) -> float:
        if axis_plan.get('motor_type') == 'dynamixel':
            return self.manager._runtime_float_parameter(
                'dynamixel_target_tolerance_deg',
                self.manager.dynamixel_target_tolerance_deg,
            )
        return self.manager._runtime_float_parameter(
            'ac_target_tolerance_deg',
            self.manager.ac_target_tolerance_deg,
        )

    @staticmethod
    def _playback_axes(plan: Dict[str, Any]) -> List[Dict[str, Any]]:
        """재생이 **실제로 모는** 축만 · §6-107

        계획의 `axes` 는 모션에 적힌 축 전부다 · 추가 녹화에서는 그중 일부만
        재생이 몰고 나머지는 **지금 MIDI 로 녹화하는 축**이다 · 그 전부를 두고
        "MIDI 가 쓰는 중이냐" 를 물으면, 녹화 중인 축 때문에 재생이 스스로
        멈춘다 · 레이어가 끊기고 그 위에 얹어 녹화할 수 없었다.

        누가 무엇을 모는지는 `axis_playback_spans` 가 이미 정해 두었다 ·
        발행할 때 거르는 `_owned_positions` 와 **같은 표**를 본다 · 판정과
        발행이 서로 다른 것을 보면 반드시 어긋난다.

        표가 없으면 전부가 재생의 축이다 · 로컬·그룹 실행은 지금 그대로다.
        """
        axes = list(plan.get('axes') or [])
        spans = plan.get('axis_playback_spans')
        if not spans:
            return axes
        return [
            axis_plan for axis_plan in axes
            if spans.get(int(axis_plan['motor_axis']), True)
        ]

    def _require_playback_command_allowed(self, axes=None) -> None:
        """재생을 계속해도 되는지 · 안 되면 멈춘다 · §6-106

        `axes` 를 주면 **그 축들만** 본다 · 다른 축을 MIDI 가 잡고 있어도
        내 축이 비어 있으면 계속한다. 추가 녹화가 그 위에 선다.
        """
        error = self.manager._playback_ownership_error(
            axes=None if axes is None else [
                int(axis_plan['motor_axis']) for axis_plan in axes
            ]
        )
        if error:
            raise RuntimeError(error)
