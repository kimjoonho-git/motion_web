"""Read and write editable motion-studio projects.

Motion-axis mapping YAML files are deliberately read-only here.  A project
stores only the selected mapping file id and checksum so a changed mapping is
detected before a motor-affecting operation.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import yaml


PROJECT_VERSION = 1
DEFAULT_PERIOD_SEC = 0.02
MOTION_FILE_SIZE_LIMIT_BYTES = 10 * 1024 * 1024
MOTION_ID_PATTERN = re.compile(r'^[1-9]\d*-[1-9]\d*$')


def _safe_name(value: Any, fallback: str = 'motion_project') -> str:
    text = str(value or '').strip()
    cleaned = ''.join(
        character if character.isalnum() or character in ('-', '_') else '_'
        for character in text
    ).strip('_')
    return cleaned or fallback


def _finite_float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _transition_safety_level(value: Any) -> int:
    try:
        level = int(value)
    except (TypeError, ValueError):
        level = 4
    return max(1, min(10, level))


def _nonnegative_int(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _mapping_rows(root: Any) -> List[Dict[str, Any]]:
    rows = root.get('mappings') if isinstance(root, dict) else None
    if not isinstance(rows, list):
        return []
    return [dict(row) for row in rows if isinstance(row, dict)]


class ProjectStore:
    def __init__(self, project_dir: Optional[Path | str] = None) -> None:
        self.project_dir: Optional[Path] = None
        self.projects_dir: Optional[Path] = None
        self.files_dir: Optional[Path] = None
        self.mappings_dir: Optional[Path] = None
        self.runtime_dir: Optional[Path] = None
        if project_dir is not None:
            self.use_workspace(project_dir)

    def use_workspace(self, project_dir: Path | str) -> None:
        """Point every studio file category at one integrated project."""
        root = Path(project_dir).expanduser().resolve()
        self.project_dir = root
        self.projects_dir = root / 'runtime' / 'studio_projects'
        self.files_dir = root / 'motions'
        self.mappings_dir = root / 'motion_axis_matching'
        self.runtime_dir = root / 'runtime' / 'studio_runtime'
        self.projects_dir.mkdir(parents=True, exist_ok=True)
        self.files_dir.mkdir(parents=True, exist_ok=True)
        self.mappings_dir.mkdir(parents=True, exist_ok=True)
        self.runtime_dir.mkdir(parents=True, exist_ok=True)

    def list_projects(self) -> List[Dict[str, Any]]:
        projects = []
        for path in sorted(
            self.projects_dir.glob('*.json'),
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        ):
            try:
                project = self._read_project(path)
            except (OSError, ValueError, json.JSONDecodeError):
                continue
            projects.append(self.summary(project, path))
        return projects

    def list_mappings(self) -> List[Dict[str, Any]]:
        mappings = []
        if not self.mappings_dir.is_dir():
            return mappings
        for path in sorted(self.mappings_dir.iterdir()):
            if not path.is_file() or path.suffix.lower() not in ('.yaml', '.yml'):
                continue
            try:
                mapping = self.inspect_mapping(path.name)
            except (OSError, UnicodeDecodeError, ValueError, yaml.YAMLError):
                continue
            mappings.append({
                'file_id': mapping['file_id'],
                'sha256': mapping['sha256'],
                'motion_ids': mapping['motion_ids'],
                'rows': mapping['rows'],
            })
        return mappings

    def list_motion_files(self) -> List[Dict[str, Any]]:
        files = []
        if not self.files_dir.is_dir():
            return files
        for path in sorted(
            self.files_dir.glob('*.json'),
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        ):
            if path.name.startswith('__studio_'):
                continue
            try:
                motion = self.read_motion_file(path.name)
                files.append({
                    'file_id': path.name,
                    'title': motion['title'],
                    'frame_count': len(motion['frames']),
                    'duration_sec': motion['duration_sec'],
                    'motion_ids': motion['motion_ids'],
                    'valid': True,
                    'message': '가져오기 가능',
                })
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                files.append({
                    'file_id': path.name,
                    'title': path.stem,
                    'frame_count': 0,
                    'duration_sec': 0.0,
                    'motion_ids': [],
                    'valid': False,
                    'message': str(exc),
                })
        return files

    def read_motion_file(self, file_id: Any) -> Dict[str, Any]:
        path = self._motion_file_path(file_id)
        if path.stat().st_size > MOTION_FILE_SIZE_LIMIT_BYTES:
            raise ValueError('모션 파일이 10MB 제한을 초과합니다')
        lines = [
            line.strip()
            for line in path.read_text(encoding='utf-8').splitlines()
            if line.strip()
        ]
        if len(lines) < 2:
            raise ValueError('모션 헤더와 프레임 데이터가 필요합니다')
        header = json.loads(lines[0])
        if not isinstance(header, dict) or header.get('type') != 'motion_header':
            raise ValueError('지원하지 않는 모션 파일 헤더입니다')
        if str(header.get('rotation_unit') or 'deg').lower() != 'deg':
            raise ValueError('현재는 deg 단위 모션 파일만 가져올 수 있습니다')
        frames = []
        motion_ids = []
        seen_motion_ids = set()
        for line_number, line in enumerate(lines[1:], start=2):
            row = json.loads(line)
            if not isinstance(row, list) or len(row) < 4 or (len(row) - 2) % 2:
                raise ValueError(f'{line_number}행 모션 프레임 형식이 올바르지 않습니다')
            try:
                frame_number = int(row[0])
                time_sec = float(row[1])
            except (TypeError, ValueError) as exc:
                raise ValueError(f'{line_number}행 frame/time 값이 올바르지 않습니다') from exc
            if frame_number < 1 or not math.isfinite(time_sec) or time_sec < 0.0:
                raise ValueError(f'{line_number}행 frame/time 범위가 올바르지 않습니다')
            values = {}
            for index in range(2, len(row), 2):
                motion_id = str(row[index] or '').strip()
                if not MOTION_ID_PATTERN.match(motion_id):
                    raise ValueError(f'{line_number}행 Motion ID가 올바르지 않습니다: {motion_id}')
                value = _finite_float(row[index + 1], math.nan)
                if not math.isfinite(value):
                    raise ValueError(f'{line_number}행 모션값이 올바르지 않습니다: {motion_id}')
                values[motion_id] = value
                if motion_id not in seen_motion_ids:
                    seen_motion_ids.add(motion_id)
                    motion_ids.append(motion_id)
            frames.append({
                'frame': frame_number,
                'time_sec': round(time_sec, 9),
                'values': values,
            })
        frames.sort(key=lambda item: (item['time_sec'], item['frame']))
        return {
            'file_id': path.name,
            'title': str(header.get('title') or path.stem),
            'motion_ids': motion_ids,
            'duration_sec': max((frame['time_sec'] for frame in frames), default=0.0),
            'frames': frames,
        }

    def import_motion_file(
        self,
        file_id: Any,
        mapping_file_id: Any,
        name: Any = None,
    ) -> Dict[str, Any]:
        motion = self.read_motion_file(file_id)
        mapping = self.inspect_mapping(mapping_file_id)
        available = set(mapping['motion_ids'])
        missing = [motion_id for motion_id in motion['motion_ids'] if motion_id not in available]
        if missing:
            raise ValueError(
                '선택한 모션축 설정에 없는 Motion ID: ' + ', '.join(missing)
            )
        project = self.create_project(name or motion['title'], mapping['file_id'])
        project['layers'] = [{
            'layer_id': f'import_{uuid.uuid4().hex[:8]}',
            'name': f'가져오기 · {motion["file_id"]}',
            'enabled': True,
            'locked': False,
            'created_at': time.time(),
            'source_motion_file_id': motion['file_id'],
            'frames': motion['frames'],
        }]
        return self.save_project(project)

    def append_motion_file(
        self, project: Dict[str, Any], file_id: Any
    ) -> Dict[str, Any]:
        motion = self.read_motion_file(file_id)
        mapping = self.inspect_mapping(project.get('mapping_file_id'))
        available = set(mapping['motion_ids'])
        missing = [motion_id for motion_id in motion['motion_ids'] if motion_id not in available]
        if missing:
            raise ValueError(
                '선택한 모션축 설정에 없는 Motion ID: ' + ', '.join(missing)
            )
        project.setdefault('layers', []).append({
            'layer_id': f'import_{uuid.uuid4().hex[:8]}',
            'name': f'가져오기 · {motion["file_id"]}',
            'enabled': True,
            'locked': False,
            'created_at': time.time(),
            'source_motion_file_id': motion['file_id'],
            'frames': motion['frames'],
        })
        return self.save_project(project)

    def create_project(self, name: Any, mapping_file_id: Any) -> Dict[str, Any]:
        mapping = self.inspect_mapping(mapping_file_id)
        now = time.time()
        project_id = f'{_safe_name(name)}-{uuid.uuid4().hex[:8]}'
        project = {
            'version': PROJECT_VERSION,
            'project_id': project_id,
            'name': str(name or '새 모션 프로젝트').strip() or '새 모션 프로젝트',
            'mapping_file_id': mapping['file_id'],
            'mapping_sha256': mapping['sha256'],
            'period_sec': DEFAULT_PERIOD_SEC,
            'transition_safety_level': 4,
            'created_at': now,
            'updated_at': now,
            'layers': [],
        }
        self.save_project(project)
        return project

    def load_project(self, project_id: Any) -> Dict[str, Any]:
        return self._read_project(self._project_path(project_id))

    def save_project(self, project: Dict[str, Any]) -> Dict[str, Any]:
        normalized = self.normalize_project(project)
        normalized['updated_at'] = time.time()
        path = self._project_path(normalized['project_id'], require_existing=False)
        self._atomic_write(path, json.dumps(normalized, ensure_ascii=False, indent=2) + '\n')
        return normalized

    def delete_project(self, project_id: Any) -> None:
        self._project_path(project_id).unlink()

    def write_motion_file(
        self,
        file_id: Any,
        content: str,
        *,
        hidden: bool = False,
    ) -> str:
        name = _safe_name(Path(str(file_id or '')).stem, 'motion') + '.json'
        if hidden and not name.startswith('__studio_'):
            name = f'__studio_{name}'
        target_dir = self.runtime_dir if hidden else self.files_dir
        self._atomic_write(target_dir / name, content)
        return name

    def inspect_mapping(self, file_id: Any) -> Dict[str, Any]:
        path = self._mapping_path(file_id)
        content = path.read_bytes()
        root = yaml.safe_load(content.decode('utf-8')) or {}
        rows = []
        seen_motion_ids = set()
        seen_motor_targets = set()
        for row in _mapping_rows(root):
            if row.get('enabled') is False:
                continue
            motion_id = str(row.get('motion_id') or '').strip()
            if not motion_id:
                continue
            if motion_id in seen_motion_ids:
                raise ValueError(f'duplicated Motion ID in mapping: {motion_id}')
            seen_motion_ids.add(motion_id)
            try:
                motor_axis = int(row.get('motor_axis'))
            except (TypeError, ValueError):
                motor_axis = None
            motor_ref = str(row.get('motor_ref') or '').strip().lower()
            target = f'ref:{motor_ref}' if motor_ref else (
                f'axis:{motor_axis}' if motor_axis is not None else ''
            )
            if target:
                if target in seen_motor_targets:
                    raise ValueError(f'duplicated motor target in mapping: {motor_ref or motor_axis}')
                seen_motor_targets.add(target)
            rows.append({
                'motion_id': motion_id,
                'motor_ref': motor_ref,
                'motor_axis': motor_axis,
                'motion_lower_deg': _finite_float(row.get('motion_lower_deg'), -180.0),
                'motion_upper_deg': _finite_float(row.get('motion_upper_deg'), 180.0),
                'initial_mode': str(row.get('initial_mode') or 'first_frame'),
                'initial_motion_position_deg': _finite_float(
                    row.get('initial_motion_position_deg'), 0.0
                ),
            })
        return {
            'file_id': path.name,
            'sha256': hashlib.sha256(content).hexdigest(),
            'motion_ids': [row['motion_id'] for row in rows],
            'rows': rows,
        }

    def mapping_check(self, project: Dict[str, Any]) -> Dict[str, Any]:
        mapping = self.inspect_mapping(project.get('mapping_file_id'))
        expected = str(project.get('mapping_sha256') or '')
        return {
            **mapping,
            'matches_project': bool(expected and mapping['sha256'] == expected),
            'expected_sha256': expected,
        }

    def summary(self, project: Dict[str, Any], path: Path | None = None) -> Dict[str, Any]:
        layers = project.get('layers') if isinstance(project.get('layers'), list) else []
        duration = project_duration(project)
        target = path or self._project_path(project.get('project_id'), require_existing=False)
        return {
            'project_id': project.get('project_id'),
            'workspace_project_id': project.get('workspace_project_id'),
            'name': project.get('name'),
            'mapping_file_id': project.get('mapping_file_id'),
            'layer_count': len(layers),
            'duration_sec': duration,
            'updated_at': project.get('updated_at'),
            'path': str(target),
        }

    def normalize_project(self, project: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(project, dict):
            raise ValueError('project must be an object')
        project_id = _safe_name(project.get('project_id'))
        mapping_file_id = str(project.get('mapping_file_id') or '').strip()
        if not mapping_file_id:
            raise ValueError('mapping_file_id is required')
        period_sec = _finite_float(project.get('period_sec'), DEFAULT_PERIOD_SEC)
        if not math.isclose(period_sec, DEFAULT_PERIOD_SEC, abs_tol=1e-9):
            raise ValueError('motion studio period_sec must be 0.02')
        layers = []
        for index, layer in enumerate(project.get('layers') or []):
            layers.append(normalize_layer(layer, index))
        self.inspect_mapping(mapping_file_id)
        return {
            'version': PROJECT_VERSION,
            'project_id': project_id,
            'name': str(project.get('name') or project_id).strip() or project_id,
            'workspace_project_id': str(project.get('workspace_project_id') or '').strip(),
            'mapping_file_id': mapping_file_id,
            'mapping_sha256': str(project.get('mapping_sha256') or ''),
            'period_sec': DEFAULT_PERIOD_SEC,
            'transition_safety_level': _transition_safety_level(
                project.get('transition_safety_level', 4)
            ),
            'created_at': _finite_float(project.get('created_at'), time.time()),
            'updated_at': _finite_float(project.get('updated_at'), time.time()),
            'layers': layers,
        }

    def _read_project(self, path: Path) -> Dict[str, Any]:
        payload = json.loads(path.read_text(encoding='utf-8'))
        return self.normalize_project(payload)

    def _project_path(self, project_id: Any, *, require_existing: bool = True) -> Path:
        safe = _safe_name(project_id)
        path = self.projects_dir / f'{safe}.json'
        if require_existing and not path.is_file():
            raise ValueError(f'motion studio project not found: {safe}')
        return path

    def _mapping_path(self, file_id: Any) -> Path:
        name = str(file_id or '').strip()
        if not name or name != Path(name).name or Path(name).suffix.lower() not in ('.yaml', '.yml'):
            raise ValueError('invalid mapping file id')
        path = self.mappings_dir / name
        if not path.is_file():
            raise ValueError(f'motion axis mapping not found: {name}')
        return path

    def _motion_file_path(self, file_id: Any) -> Path:
        name = str(file_id or '').strip()
        if (
            not name
            or name != Path(name).name
            or Path(name).suffix.lower() != '.json'
            or name.startswith('__studio_')
        ):
            raise ValueError('올바르지 않은 모션 파일 ID입니다')
        path = self.files_dir / name
        if not path.is_file():
            raise ValueError(f'모션 파일을 찾을 수 없습니다: {name}')
        return path

    @staticmethod
    def _atomic_write(path: Path, content: str) -> None:
        temporary = path.with_suffix(path.suffix + '.tmp')
        temporary.write_text(content, encoding='utf-8')
        temporary.replace(path)


def unique_motion_ids(values: Iterable[Any]) -> List[str]:
    result = []
    seen = set()
    for value in values:
        motion_id = str(value or '').strip()
        if not motion_id or motion_id in seen:
            continue
        if not MOTION_ID_PATTERN.match(motion_id):
            raise ValueError(f'invalid Motion ID: {motion_id}')
        seen.add(motion_id)
        result.append(motion_id)
    return result


def normalize_layer(layer: Any, index: int = 0) -> Dict[str, Any]:
    if not isinstance(layer, dict):
        raise ValueError('layer must be an object')
    frames = []
    for frame_index, frame in enumerate(layer.get('frames') or [], start=1):
        if not isinstance(frame, dict):
            continue
        values = {}
        source_values = frame.get('values') if isinstance(frame.get('values'), dict) else {}
        for motion_id, value in source_values.items():
            text = str(motion_id or '').strip()
            if not MOTION_ID_PATTERN.match(text):
                raise ValueError(f'invalid Motion ID in layer: {text}')
            number = _finite_float(value, math.nan)
            if not math.isfinite(number):
                raise ValueError(f'non-finite motion value: {text}')
            values[text] = number
        frames.append({
            'frame': int(frame.get('frame') or frame_index),
            'time_sec': round(_finite_float(frame.get('time_sec'), frame_index * DEFAULT_PERIOD_SEC), 9),
            'values': values,
        })
    frames.sort(key=lambda item: (item['time_sec'], item['frame']))
    point_curves = []
    seen_curve_ids = set()
    for curve_index, curve in enumerate(layer.get('point_curves') or []):
        if not isinstance(curve, dict):
            continue
        curve_id = _safe_name(curve.get('curve_id'), f'curve_{curve_index + 1}')
        if curve_id in seen_curve_ids:
            raise ValueError(f'duplicated point curve id: {curve_id}')
        seen_curve_ids.add(curve_id)
        motion_id = str(curve.get('motion_id') or '').strip()
        if not MOTION_ID_PATTERN.match(motion_id):
            raise ValueError(f'invalid Motion ID in point curve: {motion_id}')
        points = []
        seen_point_ids = set()
        for point_index, point in enumerate(curve.get('points') or []):
            if not isinstance(point, dict):
                continue
            point_id = _safe_name(point.get('point_id'), f'point_{point_index + 1}')
            if point_id in seen_point_ids:
                raise ValueError(f'duplicated point id in curve: {point_id}')
            seen_point_ids.add(point_id)
            tangent_mode = str(point.get('tangent_mode') or 'auto')
            if tangent_mode not in {'auto', 'smooth', 'broken', 'linear'}:
                raise ValueError(f'invalid tangent mode: {tangent_mode}')
            in_handle = point.get('in_handle') if isinstance(point.get('in_handle'), dict) else {}
            out_handle = point.get('out_handle') if isinstance(point.get('out_handle'), dict) else {}
            points.append({
                'point_id': point_id,
                'time_sec': round(_finite_float(point.get('time_sec'), 0.0), 9),
                'value_deg': _finite_float(point.get('value_deg'), 0.0),
                'tangent_mode': tangent_mode,
                'in_handle': {
                    'dt_sec': _finite_float(in_handle.get('dt_sec'), 0.0),
                    'dv_deg': _finite_float(in_handle.get('dv_deg'), 0.0),
                },
                'out_handle': {
                    'dt_sec': _finite_float(out_handle.get('dt_sec'), 0.0),
                    'dv_deg': _finite_float(out_handle.get('dv_deg'), 0.0),
                },
            })
        points.sort(key=lambda item: (item['time_sec'], item['point_id']))
        if len(points) < 2:
            raise ValueError('point curve requires at least two points')
        point_curves.append({
            'curve_id': curve_id,
            'motion_id': motion_id,
            'points': points,
        })
    return {
        'layer_id': _safe_name(layer.get('layer_id'), f'layer_{index + 1}'),
        'name': str(layer.get('name') or f'레이어 {index + 1}').strip()[:40]
        or f'레이어 {index + 1}',
        'enabled': layer.get('enabled') is not False,
        'locked': bool(layer.get('locked', False)),
        'created_at': _finite_float(layer.get('created_at'), time.time()),
        'source_motion_file_id': str(layer.get('source_motion_file_id') or ''),
        'source_layer_ids': [
            str(value) for value in layer.get('source_layer_ids') or [] if str(value)
        ],
        'copied_from_layer_id': str(layer.get('copied_from_layer_id') or ''),
        'edit_revision': _nonnegative_int(layer.get('edit_revision')),
        'point_curves': point_curves,
        'frames': frames,
    }


def project_duration(project: Dict[str, Any]) -> float:
    maximum = 0.0
    for layer in project.get('layers') or []:
        for frame in layer.get('frames') or []:
            maximum = max(maximum, _finite_float(frame.get('time_sec')))
    return round(maximum, 9)
