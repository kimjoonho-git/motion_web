"""Own motion-axis mapping files and validation outside the web layer."""

import json
import math
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import rclpy
import yaml
from rclpy.node import Node
from std_msgs.msg import String

from motion_runtime.midi_bank_store import (
    atomic_write_with_backup,
    load_midi_banks,
    save_midi_banks,
)


DEFAULT_MOTION_PROJECTS_DIR = (
    Path(os.environ.get('MOTION_WORKSPACE', Path.cwd())).expanduser()
    / 'motion_projects'
)
INITIAL_MODES = ('first_frame', 'manual')


class MotionMappingManager(Node):
    def __init__(self) -> None:
        super().__init__('motion_mapping_manager')
        self.motion_projects_dir = Path(
            str(self.declare_parameter(
                'motion_projects_dir', str(DEFAULT_MOTION_PROJECTS_DIR)
            ).value)
        ).expanduser().resolve()
        # These are assigned to a selected project for each request.  The
        # legacy motion_data directory is never used as a project workspace.
        self.mappings_dir = self.motion_projects_dir
        self.motion_files_dir = self.motion_projects_dir
        self.request_topic = str(
            self.declare_parameter(
                'request_topic',
                '/motion_control/motion_mapping_request',
            ).value
        )
        self.response_topic = str(
            self.declare_parameter(
                'response_topic',
                '/motion_control/motion_mapping_response',
            ).value
        )

        self._response_publisher = self.create_publisher(String, self.response_topic, 10)
        self._request_subscription = self.create_subscription(
            String,
            self.request_topic,
            self._request_callback,
            10,
        )

        self.get_logger().info(
            f'motion_mapping_manager started: request_topic={self.request_topic}, '
            f'response_topic={self.response_topic}, mappings_dir={self.mappings_dir}'
        )

    def _request_callback(self, msg: String) -> None:
        try:
            request = json.loads(msg.data)
        except json.JSONDecodeError as exc:
            self.get_logger().warn(f'invalid mapping request JSON: {exc}')
            return

        if not isinstance(request, dict):
            self._publish_response('', False, 'mapping request must be an object')
            return

        request_id = str(request.get('request_id') or '')
        command = str(request.get('command') or '').strip()
        payload = request.get('payload')
        if not isinstance(payload, dict):
            payload = {}

        try:
            self._select_project(payload)
            if command == 'list':
                response = self._list_mappings()
            elif command == 'load':
                response = self._load_mapping(payload.get('file_id'))
            elif command == 'save':
                response = self._save_mapping(payload)
            elif command == 'validate':
                response = self._validate_mapping_request(payload)
            elif command == 'delete':
                response = self._delete_mapping(payload.get('file_id'))
            elif command == 'load_midi_banks':
                response = self._load_midi_banks(payload.get('file_id'))
            elif command == 'save_midi_banks':
                response = self._save_midi_banks(payload)
            else:
                response = {
                    'success': False,
                    'message': f'unknown mapping command: {command}',
                }
        except Exception as exc:  # Defensive boundary for the web bridge.
            # RcutilsLogger does not implement logging.Logger.exception().
            # Keep the manager alive so one invalid/missing file request does
            # not disable every later mapping and MIDI-bank operation.
            self.get_logger().error(
                f'motion mapping command failed: {command}: {exc}'
            )
            response = {
                'success': False,
                'message': f'motion mapping command failed: {exc}',
            }

        response['request_id'] = request_id
        self._publish(response)

    def _select_project(self, payload: Dict[str, Any]) -> str:
        project_id = str(payload.get('project_id') or '').strip()
        if (
            not project_id
            or project_id != Path(project_id).name
            or project_id.startswith('.')
            or '/' in project_id
            or '\\' in project_id
        ):
            raise ValueError('유효한 통합 프로젝트 ID가 필요합니다')
        project_dir = (self.motion_projects_dir / project_id).resolve()
        if project_dir.parent != self.motion_projects_dir or not (project_dir / 'project.json').is_file():
            raise ValueError(f'통합 프로젝트를 찾을 수 없습니다: {project_id}')
        self.mappings_dir = project_dir / 'motion_axis_matching'
        self.motion_files_dir = project_dir / 'motions'
        self.mappings_dir.mkdir(parents=True, exist_ok=True)
        self.motion_files_dir.mkdir(parents=True, exist_ok=True)
        return project_id

    def _publish_response(self, request_id: str, success: bool, message: str) -> None:
        self._publish({
            'request_id': request_id,
            'success': success,
            'message': message,
        })

    def _publish(self, payload: Dict[str, Any]) -> None:
        msg = String()
        msg.data = json.dumps(payload, ensure_ascii=False)
        self._response_publisher.publish(msg)

    def _list_mappings(self) -> Dict[str, Any]:
        self.mappings_dir.mkdir(parents=True, exist_ok=True)
        files = []
        for path in sorted(
            (
                item for item in self.mappings_dir.iterdir()
                if item.is_file() and item.suffix.lower() in ('.yaml', '.yml')
            ),
            key=lambda item: item.stat().st_mtime if item.exists() else 0.0,
            reverse=True,
        ):
            files.append(self._mapping_file_summary(path))

        return {
            'success': True,
            'message': 'motion mapping files loaded',
            'project_dir': str(self.mappings_dir.parent),
            'mappings_dir': str(self.mappings_dir),
            'files': files,
        }

    def _load_mapping(self, file_id: Any) -> Dict[str, Any]:
        path = self._mapping_file_path(file_id)
        content = path.read_text(encoding='utf-8')
        mapping = self._normalize_mapping(yaml.safe_load(content) or {}, fallback_name=path.stem)
        validation = self._validate_mapping(mapping)
        return {
            **self._list_mappings(),
            'success': True,
            'message': 'motion mapping loaded',
            'file': self._mapping_file_summary(path, mapping=mapping),
            'mapping': mapping,
            'content': content,
            'validation': validation,
        }

    def _save_mapping(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        mapping_payload = payload.get('mapping')
        if not isinstance(mapping_payload, dict):
            mapping_payload = payload

        file_id = payload.get('file_id') or mapping_payload.get('file_id')
        fallback_name = Path(str(file_id or '')).stem if file_id else ''
        mapping = self._normalize_mapping(mapping_payload, fallback_name=fallback_name)
        validation = self._validate_mapping(mapping)
        if not validation['valid']:
            return {
                **self._list_mappings(),
                'success': False,
                'message': 'motion mapping validation failed',
                'mapping': mapping,
                'content': '',
                'validation': validation,
            }

        now = time.time()
        mapping['updated_at'] = now
        if not mapping.get('created_at'):
            mapping['created_at'] = now

        source_path = None
        if file_id:
            try:
                source_path = self._mapping_file_path(file_id)
            except ValueError:
                source_path = None
        path = self._new_or_existing_mapping_path(file_id, mapping.get('name'))
        midi_banks = self._midi_banks_from_file(source_path or path)
        if midi_banks is not None:
            # MIDI owns this section. A normal motion-axis mapping save must
            # preserve it even though it is not part of mapping validation.
            mapping['midi_banks'] = midi_banks
        mapping['file_id'] = path.name
        content = yaml.safe_dump(mapping, sort_keys=False, allow_unicode=True)
        backup = atomic_write_with_backup(
            path,
            content,
            self.mappings_dir.parent / 'runtime' / 'history' / 'motion_axis_matching',
        )

        return {
            **self._list_mappings(),
            'success': True,
            'message': 'motion mapping YAML saved',
            'file': self._mapping_file_summary(path, mapping=mapping),
            'mapping': mapping,
            'content': content,
            'validation': validation,
            'backup_file': str(backup) if backup is not None else '',
        }

    def _load_midi_banks(self, file_id: Any) -> Dict[str, Any]:
        path = self._mapping_file_path(file_id)
        state = load_midi_banks(path)
        if state is None:
            return {
                'success': False,
                'missing': True,
                'message': (
                    '아직 저장된 MIDI 뱅크가 없습니다. '
                    'MIDI 탭에서 뱅크 설정 적용/저장을 누르세요'
                ),
                'file': self._mapping_file_summary(path),
                'midi_banks': None,
            }
        return {
            'success': True,
            'message': '모션축 매칭 파일에서 MIDI 뱅크를 불러왔습니다',
            'file': self._mapping_file_summary(path),
            'midi_banks': state,
        }

    def _save_midi_banks(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        path = self._mapping_file_path(payload.get('file_id'))
        state = payload.get('midi_banks')
        if not isinstance(state, dict):
            raise ValueError('midi_banks must be an object')
        backup = save_midi_banks(
            path,
            state,
            self.mappings_dir.parent / 'runtime' / 'history' / 'motion_axis_matching',
        )
        verified = load_midi_banks(path)
        if verified != state:
            raise ValueError('저장 후 MIDI 뱅크 파일 검증에 실패했습니다')
        return {
            'success': True,
            'message': 'MIDI 뱅크를 모션축 매칭 파일에 저장하고 검증했습니다',
            'file': self._mapping_file_summary(path),
            'midi_banks': verified,
            'backup_file': str(backup),
        }

    @staticmethod
    def _midi_banks_from_file(path: Optional[Path]) -> Optional[Dict[str, Any]]:
        if path is None or not path.is_file():
            return None
        try:
            root = yaml.safe_load(path.read_text(encoding='utf-8')) or {}
        except (OSError, yaml.YAMLError):
            return None
        if not isinstance(root, dict) or not isinstance(root.get('midi_banks'), dict):
            return None
        return root['midi_banks']

    def _validate_mapping_request(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        mapping_payload = payload.get('mapping')
        if not isinstance(mapping_payload, dict):
            mapping_payload = payload
        file_id = payload.get('file_id') or mapping_payload.get('file_id')
        fallback_name = Path(str(file_id or '')).stem if file_id else ''
        mapping = self._normalize_mapping(mapping_payload, fallback_name=fallback_name)
        validation = self._validate_mapping(mapping)
        return {
            'success': validation['valid'],
            'message': validation['message'],
            'mapping': mapping,
            'validation': validation,
        }

    def _delete_mapping(self, file_id: Any) -> Dict[str, Any]:
        path = self._mapping_file_path(file_id)
        path.unlink()
        return {
            **self._list_mappings(),
            'success': True,
            'message': 'motion mapping deleted',
        }

    def _mapping_file_summary(
        self,
        path: Path,
        *,
        mapping: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        stat = path.stat()
        valid = True
        message = 'ok'
        if mapping is None:
            try:
                mapping = self._normalize_mapping(
                    yaml.safe_load(path.read_text(encoding='utf-8')) or {},
                    fallback_name=path.stem,
                )
            except (OSError, yaml.YAMLError, ValueError) as exc:
                mapping = {}
                valid = False
                message = str(exc)
        if valid and isinstance(mapping, dict):
            validation = self._validate_mapping(mapping, include_motion_file=False)
            valid = bool(validation.get('valid'))
            message = validation.get('message') or ('ok' if valid else 'validation failed')

        mappings = mapping.get('mappings') if isinstance(mapping, dict) else []
        if not isinstance(mappings, list):
            mappings = []
        enabled_count = sum(1 for item in mappings if isinstance(item, dict) and item.get('enabled'))
        mapped_count = sum(
            1
            for item in mappings
            if isinstance(item, dict)
            and (item.get('motor_ref') or item.get('motor_axis') is not None)
        )

        return {
            'id': path.name,
            'filename': path.name,
            'path': str(path),
            'size_bytes': stat.st_size,
            'updated_at': stat.st_mtime,
            'valid': valid,
            'message': message,
            'name': mapping.get('name') if isinstance(mapping, dict) else path.stem,
            'motion_file_id': mapping.get('motion_file_id') if isinstance(mapping, dict) else '',
            'mapping_count': len(mappings),
            'enabled_count': enabled_count,
            'mapped_count': mapped_count,
        }

    def _normalize_mapping(self, mapping: Dict[str, Any], *, fallback_name: str = '') -> Dict[str, Any]:
        if not isinstance(mapping, dict):
            raise ValueError('mapping root must be an object')

        name = str(mapping.get('name') or fallback_name or 'motion_mapping').strip()
        motion_file_id = str(mapping.get('motion_file_id') or '').strip()
        rows = mapping.get('mappings')
        if rows is None:
            rows = mapping.get('axes')
        if not isinstance(rows, list):
            rows = []

        normalized_rows = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            motion_id = str(row.get('motion_id') or '').strip()
            if not motion_id:
                continue
            initial_mode = str(row.get('initial_mode') or 'first_frame').strip() or 'first_frame'
            reference_enabled = bool(row.get('reference_enabled', True))
            reference_position = self._optional_float(row.get('reference_position_deg'), 0.0)
            initial_position = self._optional_float(row.get('initial_motion_position_deg'), 0.0)
            initial_move_time = self._optional_float(row.get('initial_move_time_sec'), 5.0)
            if not reference_enabled:
                reference_position = 0.0
            normalized_rows.append({
                'motion_id': motion_id,
                'enabled': bool(row.get('enabled', True)),
                'motor_ref': str(row.get('motor_ref') or '').strip().lower(),
                'motor_axis': self._optional_int(row.get('motor_axis'), None),
                'reference_enabled': reference_enabled,
                'reference_position_deg': reference_position,
                'motion_lower_deg': self._optional_float(row.get('motion_lower_deg'), -180.0),
                'motion_upper_deg': self._optional_float(row.get('motion_upper_deg'), 180.0),
                'initial_mode': initial_mode,
                'initial_motion_position_deg': initial_position,
                'initial_move_time_sec': initial_move_time,
                'invert': bool(row.get('invert', False)),
                'offset_deg': self._optional_float(row.get('offset_deg'), 0.0),
                'scale': self._optional_float(row.get('scale'), 1.0),
                'gear_ratio': self._optional_float(row.get('gear_ratio'), 1.0),
            })

        return {
            'file_id': str(mapping.get('file_id') or '').strip(),
            'name': name,
            'motion_file_id': motion_file_id,
            'created_at': self._optional_float(mapping.get('created_at'), None),
            'updated_at': self._optional_float(mapping.get('updated_at'), None),
            'mappings': normalized_rows,
        }

    def _validate_mapping(
        self,
        mapping: Dict[str, Any],
        *,
        include_motion_file: bool = True,
    ) -> Dict[str, Any]:
        errors: List[str] = []
        warnings: List[str] = []
        rows_result: Dict[str, Dict[str, Any]] = {}

        name = str(mapping.get('name') or '').strip()
        motion_file_id = str(mapping.get('motion_file_id') or '').strip()
        rows = mapping.get('mappings') if isinstance(mapping.get('mappings'), list) else []
        if not name:
            errors.append('mapping name is required')
        if not rows:
            warnings.append('motion axis mapping is empty')

        first_values: Dict[str, float] = {}
        first_value_message = ''
        if include_motion_file and motion_file_id:
            first_values, first_value_message = self._motion_file_first_values(motion_file_id)
            if first_value_message:
                warnings.append(first_value_message)

        motion_id_counts: Dict[str, int] = {}
        target_counts: Dict[str, int] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            motion_id = str(row.get('motion_id') or '').strip()
            if motion_id:
                motion_id_counts[motion_id] = motion_id_counts.get(motion_id, 0) + 1
            if row.get('enabled'):
                motor_ref = str(row.get('motor_ref') or '').strip()
                motor_axis = row.get('motor_axis')
                target_key = f'ref:{motor_ref}' if motor_ref else (
                    f'axis:{motor_axis}' if motor_axis is not None else ''
                )
                if target_key:
                    target_counts[target_key] = target_counts.get(target_key, 0) + 1

        for row in rows:
            if not isinstance(row, dict):
                continue
            motion_id = str(row.get('motion_id') or '').strip()
            if not motion_id:
                errors.append('motion_id is required for every mapping row')
                continue

            row_errors: List[str] = []
            row_warnings: List[str] = []
            enabled = bool(row.get('enabled'))
            motor_ref = str(row.get('motor_ref') or '').strip()
            motor_axis = row.get('motor_axis')
            lower = self._finite_float(row.get('motion_lower_deg'))
            upper = self._finite_float(row.get('motion_upper_deg'))
            scale = self._finite_float(row.get('scale'))
            gear_ratio = self._finite_float(row.get('gear_ratio'))
            offset = self._finite_float(row.get('offset_deg'))
            reference = self._finite_float(row.get('reference_position_deg'))
            reference_enabled = bool(row.get('reference_enabled', True))
            initial_position = self._finite_float(row.get('initial_motion_position_deg'))
            initial_time = self._finite_float(row.get('initial_move_time_sec'))
            initial_mode = str(row.get('initial_mode') or '').strip()

            if motion_id_counts.get(motion_id, 0) > 1:
                row_errors.append('duplicated motion_id')
            if enabled and not motor_ref and motor_axis is None:
                row_errors.append('enabled row requires motor_ref')
            if motor_ref and not self._valid_motor_ref(motor_ref):
                row_errors.append(f'invalid motor_ref: {motor_ref}')
            target_key = f'ref:{motor_ref}' if motor_ref else (
                f'axis:{motor_axis}' if motor_axis is not None else ''
            )
            if enabled and target_key and target_counts.get(target_key, 0) > 1:
                row_errors.append(f'duplicated motor target: {motor_ref or motor_axis}')
            if scale is None or math.isclose(scale, 0.0, abs_tol=1e-12):
                row_errors.append('scale must be a non-zero number')
            if gear_ratio is None or gear_ratio <= 0:
                row_errors.append('gear_ratio must be > 0')
            if offset is None:
                row_errors.append('offset_deg must be numeric')
            if reference is None:
                row_errors.append('reference_position_deg must be numeric')
            if lower is None or upper is None:
                row_errors.append('motion range must be numeric')
            elif lower > upper:
                row_errors.append('motion_lower_deg must be <= motion_upper_deg')
            if initial_mode not in INITIAL_MODES:
                row_errors.append(f'initial_mode must be one of: {", ".join(INITIAL_MODES)}')
            if initial_position is None:
                row_errors.append('initial_motion_position_deg must be numeric')
            if initial_time is None or initial_time <= 0:
                row_errors.append('initial_move_time_sec must be > 0')
            if (
                initial_mode == 'manual'
                and lower is not None
                and upper is not None
                and initial_position is not None
                and not (lower <= initial_position <= upper)
            ):
                row_warnings.append('manual initial position is outside motion range')
            if initial_mode == 'first_frame' and include_motion_file and motion_id not in first_values:
                row_warnings.append('first frame value not found in selected motion file')
            if initial_mode == 'first_frame' and motion_id in first_values:
                initial_position = first_values[motion_id]
                row['initial_motion_position_deg'] = initial_position
            effective_reference = reference if reference_enabled else 0.0

            preview: Dict[str, Any] = {
                'reference_enabled': reference_enabled,
                'reference_position_deg': effective_reference,
                'stored_reference_position_deg': reference,
                'motion_lower_deg': lower,
                'motion_upper_deg': upper,
                'initial_motion_position_deg': initial_position,
                'stored_initial_motion_position_deg': initial_position,
                'motion_offset_deg': offset,
                'scale': scale,
                'gear_ratio': gear_ratio,
            }
            if not row_errors:
                lower_output = self._motion_to_output_value(row, lower)
                upper_output = self._motion_to_output_value(row, upper)
                lower_target = self._motion_to_motor_target(row, lower)
                upper_target = self._motion_to_motor_target(row, upper)
                manual_output = (
                    self._motion_to_output_value(row, initial_position)
                    if initial_position is not None
                    else None
                )
                manual_target = (
                    self._motion_to_motor_target(row, initial_position)
                    if initial_position is not None
                    else None
                )
                preview.update({
                    'motion_lower_output_deg': lower_output,
                    'motion_upper_output_deg': upper_output,
                    'motion_output_min_deg': min(lower_output, upper_output),
                    'motion_output_max_deg': max(lower_output, upper_output),
                    'motion_lower_motor_target_deg': lower_target,
                    'motion_upper_motor_target_deg': upper_target,
                    'motion_motor_target_min_deg': min(lower_target, upper_target),
                    'motion_motor_target_max_deg': max(lower_target, upper_target),
                    'manual_initial_output_deg': manual_output,
                    'manual_initial_motor_target_deg': manual_target,
                })
                if motion_id in first_values:
                    first_motion_value = first_values[motion_id]
                    first_output_value = self._motion_to_output_value(row, first_motion_value)
                    preview.update({
                        'first_frame_motion_position_deg': first_motion_value,
                        'first_frame_output_deg': first_output_value,
                        'first_frame_motor_target_deg': self._motion_to_motor_target(row, first_motion_value),
                    })

            for message in row_errors:
                errors.append(f'{motion_id}: {message}')
            for message in row_warnings:
                warnings.append(f'{motion_id}: {message}')

            rows_result[motion_id] = {
                'status': 'error' if row_errors else ('warning' if row_warnings else 'ok'),
                'messages': [*row_errors, *row_warnings],
                **preview,
            }

        valid = not errors
        return {
            'valid': valid,
            'message': 'motion mapping validation passed' if valid else 'motion mapping validation failed',
            'errors': errors,
            'warnings': warnings,
            'rows': rows_result,
        }

    @staticmethod
    def _valid_motor_ref(value: Any) -> bool:
        text = str(value or '').strip().lower()
        parts = text.split(':')
        if len(parts) != 3:
            return False
        family, key, raw_value = parts
        if (family, key) not in {
            ('ac_servo', 'alias'),
            ('dynamixel', 'id'),
        }:
            return False
        try:
            return int(raw_value, 0) >= 0
        except (TypeError, ValueError):
            return False

    def _motion_to_output_value(self, row: Dict[str, Any], motion_value_deg: Optional[float]) -> float:
        motion_value = self._finite_float(motion_value_deg)
        if motion_value is None:
            motion_value = 0.0
        scale = self._finite_float(row.get('scale')) or 1.0
        offset = self._finite_float(row.get('offset_deg')) or 0.0
        sign = -1.0 if bool(row.get('invert')) else 1.0
        return (motion_value + offset) * scale * sign

    def _motion_to_motor_target(self, row: Dict[str, Any], motion_value_deg: Optional[float]) -> float:
        reference = self._finite_float(row.get('reference_position_deg')) or 0.0
        if not bool(row.get('reference_enabled', True)):
            reference = 0.0
        gear_ratio = self._finite_float(row.get('gear_ratio')) or 1.0
        output_value = self._motion_to_output_value(row, motion_value_deg)
        return reference + (output_value * gear_ratio)

    def _motion_file_first_values(self, file_id: str) -> tuple[Dict[str, float], str]:
        try:
            path = self._motion_file_path(file_id)
            rows = self._motion_rows_from_content(path.read_text(encoding='utf-8'))
        except (OSError, ValueError) as exc:
            return {}, f'motion file could not be read: {exc}'

        records = []
        for row_index, row in enumerate(rows):
            for motion_id, value, time_sec in self._motion_records_from_row(row):
                if motion_id is None:
                    continue
                value_number = self._finite_float(value)
                if value_number is None:
                    continue
                time_number = self._finite_float(time_sec)
                records.append({
                    'motion_id': str(motion_id),
                    'value': value_number,
                    'time_sec': time_number if time_number is not None else float(row_index),
                    'row_index': row_index,
                })

        if not records:
            return {}, 'motion file has no readable motion values'

        first_values: Dict[str, float] = {}
        for record in sorted(records, key=lambda item: (item['time_sec'], item['row_index'])):
            first_values.setdefault(record['motion_id'], record['value'])
        return first_values, ''

    def _motion_file_path(self, file_id: Any) -> Path:
        name = str(file_id or '').strip()
        if not name:
            raise ValueError('motion file_id is required')
        if name != Path(name).name or '/' in name or '\\' in name:
            raise ValueError('invalid motion file id')
        path = self.motion_files_dir / name
        if not path.is_file():
            raise ValueError(f'motion file not found: {name}')
        return path

    def _motion_rows_from_content(self, content: str) -> List[Any]:
        text = content.strip()
        if not text:
            return []
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            rows = []
            for line in content.splitlines():
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    item = json.loads(stripped)
                except json.JSONDecodeError:
                    continue
                if isinstance(item, dict) and item.get('type') == 'motion_header':
                    continue
                rows.append(item)
            return rows

        if isinstance(payload, dict):
            for key in ('data', 'rows', 'records', 'motion_data', 'motions', 'frames', 'values'):
                value = payload.get(key)
                if isinstance(value, list):
                    return value
            return [payload]
        if isinstance(payload, list):
            if payload and isinstance(payload[0], list):
                first = [str(item).strip().lower() for item in payload[0]]
                if 'frame' in first and 'value' in first:
                    return payload[1:]
            return payload
        return []

    def _motion_records_from_row(self, row: Any) -> List[tuple[Any, Any, Any]]:
        if isinstance(row, dict):
            if row.get('type') == 'motion_header':
                return []
            motion_id = (
                row.get('motion_id')
                or row.get('motionId')
                or row.get('motion ID')
                or row.get('motion Id')
                or row.get('id')
            )
            value = row.get('value', row.get('angle', row.get('position')))
            time_sec = row.get('time_sec', row.get('time', row.get('time(sec)')))
            return [(motion_id, value, time_sec)] if motion_id is not None else []
        if isinstance(row, list):
            if len(row) == 4:
                return [(row[2], row[3], row[1])]
            if len(row) > 4:
                records = []
                for index in range(2, len(row) - 1, 2):
                    records.append((row[index], row[index + 1], row[1] if len(row) > 1 else None))
                return records
        return []

    def _mapping_file_path(self, file_id: Any) -> Path:
        name = str(file_id or '').strip()
        if not name:
            raise ValueError('mapping file_id is required')
        if name != Path(name).name or '/' in name or '\\' in name:
            raise ValueError('invalid mapping file id')
        if not name.lower().endswith(('.yaml', '.yml')):
            name = f'{name}.yaml'
        path = self.mappings_dir / name
        if not path.is_file():
            raise ValueError(f'motion mapping not found: {name}')
        return path

    def _new_or_existing_mapping_path(self, file_id: Any, name: Any) -> Path:
        safe = self._safe_mapping_filename(str(name or 'motion_mapping'))
        if file_id:
            requested = str(file_id).strip()
            if requested != Path(requested).name or '/' in requested or '\\' in requested:
                raise ValueError('invalid mapping file id')
            if not requested.lower().endswith(('.yaml', '.yml')):
                requested = f'{requested}.yaml'
            if safe != requested:
                return self._available_mapping_path(safe)
            return self.mappings_dir / requested

        return self._available_mapping_path(safe)

    def _available_mapping_path(self, filename: str) -> Path:
        path = self.mappings_dir / filename
        if not path.exists():
            return path

        stamp = time.strftime('%Y%m%d_%H%M%S')
        stem = Path(filename).stem
        for index in range(1000):
            suffix = f'{stamp}' if index == 0 else f'{stamp}_{index}'
            candidate = self.mappings_dir / f'{stem}_{suffix}.yaml'
            if not candidate.exists():
                return candidate
        raise RuntimeError('unable to allocate unique motion mapping filename')

    @staticmethod
    def _safe_mapping_filename(name: str) -> str:
        cleaned = ''.join(
            char if char.isalnum() or char in ('-', '_', '.') else '_'
            for char in Path(name).name.strip()
        ).strip('._')
        if not cleaned:
            cleaned = 'motion_mapping'
        if not cleaned.lower().endswith(('.yaml', '.yml')):
            cleaned = f'{cleaned}.yaml'
        return cleaned

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

    @staticmethod
    def _finite_float(value: Any) -> Optional[float]:
        if value is None or value == '':
            return None
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if math.isfinite(number) else None


def main(args=None) -> None:
    rclpy.init(args=args)
    node = MotionMappingManager()
    try:
        rclpy.spin(node)
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()
