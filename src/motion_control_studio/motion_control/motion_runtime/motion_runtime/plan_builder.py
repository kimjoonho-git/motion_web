"""모션 실행 계획 수립 · 요청과 현재 모터 상태로부터 실행 계획을 만든다.

`MotionRunManager`에서 떼어냈다 · §5 분해 목표안의 `PlanBuilder` · §6-30

**함수 자체는 아직 하나다.** 421줄짜리 `build` 하나가 이 모듈의 전부다.
쪼개는 것은 별개 작업이다 · 모터를 실제로 움직이는 계획을 만드는 코드라
경계를 잘못 그으면 축 목표값이 틀어진다. 먼저 **집을 마련하고** 거기서
초점 맞춘 시험과 함께 나누는 편이 안전하다.

노드에서 받는 것 · 모터 목록 · 파일 경로 해석 · 파일 읽기 · 제어 주기.
"""

from __future__ import annotations

import math
import time
from typing import Any, Dict, List, Mapping, Optional

from motion_common.values import finite_float, optional_int

from . import motion_run_rules
from .motion_automation_store import REPEAT_MODES
from .motion_run_constants import CONTINUOUS_LOOP_TOLERANCE_DEG


def _axis_release_sec(
    payload: Dict[str, Any],
    axes: List[Dict[str, Any]],
) -> Dict[int, float]:
    """`{motion_id: 종료초}` 요청을 `{모터축: 종료초}` 로 옮긴다 · §6-73

    부르는 쪽(스튜디오)은 모션 ID 로 말하고 발행부는 모터축으로 움직인다 ·
    옮겨 두면 재생 루프가 매 프레임 매핑을 다시 뒤지지 않는다.
    """
    requested = payload.get('axis_release_sec')
    if not isinstance(requested, Mapping):
        return {}
    by_motion_id = {}
    for motion_id, value in requested.items():
        seconds = finite_float(value)
        if seconds is not None and seconds >= 0.0:
            by_motion_id[str(motion_id)] = float(seconds)
    if not by_motion_id:
        return {}
    return {
        int(axis['motor_axis']): by_motion_id[str(axis['motion_id'])]
        for axis in axes
        if str(axis['motion_id']) in by_motion_id
    }


class PlanBuilder:
    def __init__(self, manager: Any) -> None:
        self.manager = manager

    def build(
        self,
        payload: Dict[str, Any],
        *,
        initialization_only: bool = False,
        motors_snapshot: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        run_mode = str(payload.get('run_mode') or 'once').strip().lower()
        if run_mode not in ('once', 'continuous'):
            raise ValueError('run_mode must be once or continuous')
        automation_run = bool(payload.get('automation_run', False))
        repeat_mode = str(payload.get('repeat_mode') or 'direct').strip().lower()
        if repeat_mode not in REPEAT_MODES:
            raise ValueError(f'지원하지 않는 자동 반복 방식입니다: {repeat_mode}')
        dwell_sec = finite_float(payload.get('dwell_sec'))
        dwell_sec = 0.0 if dwell_sec is None else dwell_sec
        if dwell_sec < 0.0:
            raise ValueError('자동 반복 대기 시간은 0초 이상이어야 합니다')
        countdown_sec = finite_float(payload.get('countdown_sec'))
        countdown_sec = 0.0 if countdown_sec is None else countdown_sec
        if countdown_sec < 0.0 or countdown_sec > 10.0:
            raise ValueError('모션 시작 대기 시간은 0초 이상 10초 이하여야 합니다')
        scheduled_start_at = finite_float(payload.get('scheduled_start_at'))
        scheduled_start_at = 0.0 if scheduled_start_at is None else scheduled_start_at
        synchronized_cycle_sec = finite_float(payload.get('synchronized_cycle_sec'))
        synchronized_cycle_sec = 0.0 if synchronized_cycle_sec is None else synchronized_cycle_sec
        try:
            synchronized_repeat_count = int(payload.get('synchronized_repeat_count') or 0)
        except (TypeError, ValueError) as exc:
            raise ValueError('동기 반복 횟수가 올바르지 않습니다') from exc
        try:
            target_cycle_count = int(payload.get('target_cycle_count') or 0)
        except (TypeError, ValueError) as exc:
            raise ValueError('목표 정지 회차가 올바르지 않습니다') from exc
        if target_cycle_count < 0:
            raise ValueError('목표 정지 회차는 0 이상이어야 합니다')
        if synchronized_repeat_count and (
            scheduled_start_at <= time.time() or synchronized_cycle_sec <= 0.0
            or not 1 <= synchronized_repeat_count <= 10000
        ):
            raise ValueError('동기 예약 시작 시각·주기·반복 횟수를 확인하세요')
        try:
            operation_generation = int(payload.get('operation_generation') or 0)
        except (TypeError, ValueError) as exc:
            raise ValueError('작업 세대 값이 올바르지 않습니다') from exc
        if operation_generation < 0:
            raise ValueError('작업 세대 값은 0 이상이어야 합니다')
        group_execution = bool(payload.get('group_execution'))
        if not automation_run and not group_execution and run_mode != 'continuous':
            repeat_mode = 'direct'
            dwell_sec = 0.0
        motion_file_id = str(payload.get('motion_file_id') or '').strip()
        mapping_file_id = str(payload.get('mapping_file_id') or '').strip()
        request_source = str(payload.get('request_source') or 'motion_run').strip()
        studio_request = request_source == 'motion_studio'
        requested_motion_ids = {
            str(value or '').strip()
            for value in (payload.get('active_motion_ids') or [])
            if str(value or '').strip()
        }
        initial_move_time_override = motion_run_rules._initial_move_time_override_sec(payload)
        if hasattr(self.manager, 'motion_projects_dir'):
            project_id, motion_files_dir, mappings_dir = self.manager._project_asset_dirs(payload)
            motion_directory = motion_files_dir
            if studio_request and motion_file_id.startswith('__studio_'):
                motion_directory = motion_files_dir.parent / 'runtime' / 'studio_runtime'
            motion_file_path = (
                self.manager._motion_file_path(motion_file_id, motion_directory)
                if motion_file_id
                else None
            )
            mapping_path = self.manager._mapping_file_path(mapping_file_id, mappings_dir)
        else:
            # Compatibility for isolated unit tests that replace the path
            # helpers without constructing a ROS node.
            project_id = ''
            motion_file_path = (
                self.manager._motion_file_path(motion_file_id)
                if motion_file_id
                else None
            )
            mapping_path = self.manager._mapping_file_path(mapping_file_id)
        if not motion_file_id and not initialization_only:
            raise ValueError('motion file_id is required')
        motors = (
            list(motors_snapshot)
            if motors_snapshot is not None
            else self.manager._current_motors()
        )
        motion_records = (
            self.manager._load_motion_records(motion_file_path)
            if motion_file_id
            else []
        )
        source_motion_data_available = bool(motion_records)
        mapping = self.manager._load_mapping(mapping_path)

        mapping_motion_file_id = str(mapping.get('motion_file_id') or '').strip()
        if (
            mapping_motion_file_id
            and mapping_motion_file_id != motion_file_id
            and not studio_request
            and not (initialization_only and not motion_file_id)
        ):
            raise ValueError(
                f'mapping file expects motion file {mapping_motion_file_id}, not {motion_file_id}'
            )

        groups = motion_run_rules._motion_groups(motion_records)
        if request_source != 'motion_studio':
            requested_motion_ids = (
                set()
                if initialization_only
                else {str(motion_id) for motion_id in groups}
            )
        initialization_fallback_used = False
        if not motors:
            raise ValueError('current motion_state is unavailable')

        rows = mapping.get('mappings')
        if not isinstance(rows, list):
            rows = []
        axes = []
        errors = []
        warnings = []
        for row in rows:
            if not isinstance(row, dict) or row.get('enabled') is False:
                continue
            motion_id = str(row.get('motion_id') or '').strip()
            if requested_motion_ids and motion_id not in requested_motion_ids:
                continue
            if not motion_id:
                errors.append('enabled mapping row without motion_id')
                continue
            motor_ref = str(row.get('motor_ref') or '').strip()
            motor_axis = optional_int(row.get('motor_axis'))
            motor = None
            if motor_ref:
                matches = motion_run_rules._motors_for_ref(motor_ref, motors)
                if len(matches) == 0:
                    errors.append(f'Motion ID {motion_id}: Motor {motor_ref} not found')
                    continue
                if len(matches) > 1:
                    errors.append(f'Motion ID {motion_id}: Motor {motor_ref} is duplicated')
                    continue
                motor = matches[0]
                motor_axis = optional_int(motor.get('controller_index'))
            elif motor_axis is not None:
                # Backward compatibility for mapping files saved before motor_ref.
                motor = self.manager._motor_for_axis(motor_axis, motors)
            if motor_axis is None:
                errors.append(f'Motion ID {motion_id}: motor_ref is required')
                continue
            missing_motion_data = motion_id not in groups
            if missing_motion_data:
                if not initialization_only:
                    errors.append(f'Motion ID {motion_id}: motion file data not found')
                    continue
                initial_mode = str(row.get('initial_mode') or 'first_frame')
                fallback_value = (
                    finite_float(row.get('initial_motion_position_deg')) or 0.0
                    if initial_mode == 'manual'
                    else 0.0
                )
                fallback_record = {
                    'frame': 0,
                    'time_sec': 0.0,
                    'motion_id': motion_id,
                    'value': float(fallback_value),
                    'row_index': len(motion_records),
                }
                motion_records.append(fallback_record)
                groups[motion_id] = [fallback_record]
                initialization_fallback_used = True
                warnings.append(
                    f'Motion ID {motion_id}: '
                    + (
                        f'모션 데이터가 없어 수동 초기위치 {fallback_value:.3f}°를 사용'
                        if initial_mode == 'manual'
                        else '첫 프레임 데이터가 없어 모션 0°를 초기위치로 사용'
                    )
                )
            if motor is None:
                errors.append(f'Motion ID {motion_id}: Axis {motor_axis} not found')
                continue
            motor_error = motion_run_rules._motor_ready_error(motor)
            if motor_error and not automation_run:
                errors.append(f'Motion ID {motion_id}: {motor_error}')

            motion_values = [record['value'] for record in groups[motion_id]]
            motion_min = min(motion_values)
            motion_max = max(motion_values)
            lower = finite_float(row.get('motion_lower_deg'))
            upper = finite_float(row.get('motion_upper_deg'))
            if lower is not None and upper is not None and lower > upper:
                errors.append(f'Motion ID {motion_id}: motion min limit must be <= max limit')
                continue
            if missing_motion_data and (
                (lower is not None and motion_values[0] < lower)
                or (upper is not None and motion_values[0] > upper)
            ):
                errors.append(
                    f'Motion ID {motion_id}: 초기 모션값 {motion_values[0]:.3f}°가 '
                    '모션 설정 범위 밖입니다'
                )
                continue
            if lower is not None and motion_min < lower:
                warnings.append(
                    f'Motion ID {motion_id}: {motion_min:.3f}° 이하 데이터는 {lower:.3f}°로 제한'
                )
            if upper is not None and motion_max > upper:
                warnings.append(
                    f'Motion ID {motion_id}: {motion_max:.3f}° 이상 데이터는 {upper:.3f}°로 제한'
                )

            command_motion_min = motion_run_rules._clamp_motion_value(motion_min, lower, upper)
            command_motion_max = motion_run_rules._clamp_motion_value(motion_max, lower, upper)

            target_min = motion_run_rules._motor_target(row, command_motion_min)
            target_max = motion_run_rules._motor_target(row, command_motion_max)
            target_low = min(target_min, target_max)
            target_high = max(target_min, target_max)
            limit_error = motion_run_rules._target_range_limit_error(motor, target_low, target_high)
            if limit_error:
                errors.append(f'Motion ID {motion_id}: {limit_error}')

            initial_motion_source_value = motion_run_rules._initial_motion_value(row, groups[motion_id])
            initial_motion_value = motion_run_rules._clamp_motion_value(
                initial_motion_source_value,
                lower,
                upper,
            )
            row_initial_time = max(
                finite_float(row.get('initial_move_time_sec')) or 0.0,
                0.0,
            )
            initial_move_time = (
                initial_move_time_override
                if initial_move_time_override is not None
                else row_initial_time
            )
            axis_plan = {
                'motion_id': motion_id,
                'motor_ref': motor_ref,
                'motor_axis': motor_axis,
                'motor_type': motion_run_rules._motor_type(motor),
                'initial_move_time_sec': initial_move_time,
                'initial_motion_source_position_deg': initial_motion_source_value,
                'initial_motion_position_deg': initial_motion_value,
                'initial_motor_target_deg': motion_run_rules._motor_target(row, initial_motion_value),
                'motion_limit_lower_deg': lower,
                'motion_limit_upper_deg': upper,
                'source_motion_min_deg': motion_min,
                'source_motion_max_deg': motion_max,
                'command_motion_min_deg': command_motion_min,
                'command_motion_max_deg': command_motion_max,
                'motion_clamped': command_motion_min != motion_min or command_motion_max != motion_max,
                'target_min_deg': target_low,
                'target_max_deg': target_high,
                'loop_start_motion_deg': motion_run_rules._clamp_motion_value(motion_values[0], lower, upper),
                'loop_end_motion_deg': motion_run_rules._clamp_motion_value(motion_values[-1], lower, upper),
                'row': row,
            }
            axis_plan['loop_start_target_deg'] = motion_run_rules._motor_target(
                row,
                axis_plan['loop_start_motion_deg'],
            )
            axis_plan['loop_end_target_deg'] = motion_run_rules._motor_target(
                row,
                axis_plan['loop_end_motion_deg'],
            )
            axis_plan['loop_delta_deg'] = abs(
                float(axis_plan['loop_end_motion_deg']) - float(axis_plan['loop_start_motion_deg'])
            )
            axis_plan['loop_motor_delta_deg'] = abs(
                float(axis_plan['loop_end_target_deg']) - float(axis_plan['loop_start_target_deg'])
            )
            axis_plan['loop_tolerance_deg'] = CONTINUOUS_LOOP_TOLERANCE_DEG
            axes.append(axis_plan)

        if not axes:
            errors.append('enabled motion mappings not found')
        if requested_motion_ids:
            planned_motion_ids = {str(axis['motion_id']) for axis in axes}
            missing_requested = sorted(requested_motion_ids - planned_motion_ids)
            if missing_requested:
                errors.append(
                    'requested Motion ID is unavailable: '
                    + ', '.join(missing_requested)
                )
        duplicate_axes = motion_run_rules._duplicate_axis_text(axes)
        if duplicate_axes:
            errors.append(f'duplicate motor axis in enabled mappings: {duplicate_axes}')
        if errors:
            raise ValueError('; '.join(errors[:8]))

        start_time = min(record['time_sec'] for record in motion_records)
        end_time = max(record['time_sec'] for record in motion_records)
        duration = max(end_time - start_time, 0.0)
        samples = []
        if not initialization_only:
            sample_count = max(1, int(math.floor(duration / self.manager.period_sec)) + 1)
            last_time = start_time + ((sample_count - 1) * self.manager.period_sec)
            if end_time - last_time > 0.001:
                sample_count += 1
            group_times = {
                motion_id: [float(record['time_sec']) for record in records]
                for motion_id, records in groups.items()
            }
            for index in range(sample_count):
                sample_time = min(start_time + (index * self.manager.period_sec), end_time)
                positions = {}
                motion_values = {}
                for axis in axes:
                    motion_id = str(axis['motion_id'])
                    motion_value = motion_run_rules._interpolated_value(
                        groups[motion_id],
                        group_times[motion_id],
                        sample_time,
                    )
                    motion_value = motion_run_rules._clamp_motion_value(
                        motion_value,
                        axis.get('motion_limit_lower_deg'),
                        axis.get('motion_limit_upper_deg'),
                    )
                    positions[int(axis['motor_axis'])] = motion_run_rules._motor_target(
                        axis['row'],
                        motion_value,
                    )
                    motion_values[motion_id] = float(motion_value)
                samples.append({
                    'time_sec': sample_time - start_time,
                    'absolute_time_sec': sample_time,
                    'positions': positions,
                    'motion_values': motion_values,
                })

        complete_motion_data_available = (
            source_motion_data_available and not initialization_fallback_used
        )
        continuous_capability = (
            motion_run_rules._continuous_capability(axes)
            if complete_motion_data_available
            else {
                'available': False,
                'reason': '실제 모션 데이터가 없어 초기 위치 이동만 가능합니다',
            }
        )
        capabilities = {
            'initial_position': {
                'available': True,
                'reason': '모터 상태·매핑·초기 목표 검사 통과',
            },
            'single_run': {
                'available': complete_motion_data_available,
                'reason': (
                    '모터 상태·매핑 검사 통과, 모션 범위 초과값은 Min/Max로 제한'
                    if complete_motion_data_available
                    else '실제 모션 데이터가 없어 재생할 수 없습니다'
                ),
            },
            'continuous_run': {
                **continuous_capability,
            },
        }

        return {
            'project_id': project_id,
            'request_source': request_source,
            'group_execution': group_execution,
            'execution_id': str(payload.get('execution_id') or ''),
            'group_cycle_number': int(payload.get('group_cycle_number') or 0),
            'motion_file_id': motion_file_id,
            'mapping_file_id': mapping_file_id,
            'run_mode': run_mode,
            'automation_run': automation_run,
            'repeat_mode': repeat_mode,
            'dwell_sec': dwell_sec,
            'countdown_sec': countdown_sec,
            'scheduled_start_at': scheduled_start_at,
            'synchronized_cycle_sec': synchronized_cycle_sec,
            'synchronized_repeat_count': synchronized_repeat_count,
            'hold_final_until_cycle': bool(payload.get('hold_final_until_cycle')),
            'network_operation_id': str(payload.get('network_operation_id') or ''),
            'network_lease_id': str(payload.get('network_lease_id') or ''),
            'operation_generation': operation_generation,
            'motion_file_path': str(motion_file_path) if motion_file_path else '',
            'mapping_path': str(mapping_path),
            'axes': axes,
            # 축별 재생 종료 시각 · 오버더빙에서 스튜디오가 준다 · §6-73
            #
            # 이 시각을 지나면 그 축은 더 이상 명령하지 않는다 · 소유권이 풀려
            # MIDI 가 이어받을 수 있다. 샘플 계산은 그대로 두고 **발행 직전에만**
            # 거르므로, 로컬·그룹 실행 경로는 이 값이 없어 아무 영향이 없다.
            'axis_release_sec': _axis_release_sec(payload, axes),
            'samples': samples,
            'warnings': warnings,
            'capabilities': capabilities,
            'summary': {
                'request_source': request_source,
                'motion_file_id': motion_file_id,
                'mapping_file_id': mapping_file_id,
                'axis_count': len(axes),
                'duration_sec': duration,
                'period_sec': self.manager.period_sec,
                'sample_count': len(samples),
                'initial_move_time_sec': initial_move_time_override,
                'initialization_duration_sec': max(
                    (
                        float(axis.get('initial_move_time_sec') or self.manager.period_sec)
                        for axis in axes
                    ),
                    default=0.0,
                ),
                'continuous_available': continuous_capability['available'],
                'clamped_axis_count': sum(1 for axis in axes if axis.get('motion_clamped')),
                'automation_run': automation_run,
                'repeat_mode': repeat_mode,
                'dwell_sec': dwell_sec,
                'countdown_sec': countdown_sec,
                'scheduled_start_at': scheduled_start_at,
                'synchronized_cycle_sec': synchronized_cycle_sec,
                'synchronized_repeat_count': synchronized_repeat_count,
                'operation_generation': operation_generation,
                'target_cycle_count': target_cycle_count,
            },
        }
