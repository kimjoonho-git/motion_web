"""Project-owned motion asset storage.

This module deliberately has no ROS or motor-control dependencies.  Opening,
editing, importing, or deleting a project asset must never apply hardware
configuration or issue a motor command.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

import yaml


PROJECT_VERSION = 1
MAX_TEXT_BYTES = 10 * 1024 * 1024
DEFAULT_MOTOR_FILE = 'motor_axes.yaml'
DEFAULT_MOTION_AXIS_FILE = 'motion_axes.yaml'
PROJECT_CATEGORIES = {
    'motor_axes': {'.yaml', '.yml'},
    'motion_axis_matching': {'.yaml', '.yml'},
    'motions': {'.json'},
    'layers': {'.json'},
}
DISPLAY_NAMES = {
    'motor_axes': '모터축 설정',
    'motion_axis_matching': '모션축 설정',
    'motions': '모션 파일',
    'layers': '레이어',
}


def _safe_stem(value: Any, fallback: str = 'project') -> str:
    text = re.sub(r'[^0-9A-Za-z가-힣._-]+', '_', str(value or '').strip())
    text = text.strip('._-')
    return text[:80] or fallback


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


class ProjectRepository:
    """Manage project metadata and independent, reusable project files."""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.selection_file = self.root / '.selected_project.json'
        self._migrate_generated_empty_mappings()
        self._migrate_internal_backups()
        self._remove_empty_no_project_workspace()

    def _migrate_internal_backups(self) -> None:
        for project_dir in self.root.iterdir():
            if not project_dir.is_dir() or project_dir.name.startswith('.'):
                continue
            for category in ('motor_axes', 'motion_axis_matching'):
                source_dir = project_dir / category
                if not source_dir.is_dir():
                    continue
                history_dir = project_dir / 'runtime' / 'history' / category
                for source in source_dir.glob('*.bak-*'):
                    if not source.is_file() or source.is_symlink():
                        continue
                    history_dir.mkdir(parents=True, exist_ok=True)
                    target = history_dir / source.name
                    counter = 2
                    while target.exists():
                        target = history_dir / f'{counter}-{source.name}'
                        counter += 1
                    source.rename(target)

    def _remove_empty_no_project_workspace(self) -> None:
        workspace = self.root / '.no_project'
        if not workspace.is_dir():
            return
        try:
            files = [path for path in workspace.rglob('*') if path.is_file()]
        except OSError:
            return
        if files:
            return
        for path in sorted(
            (item for item in workspace.rglob('*') if item.is_dir()),
            key=lambda item: len(item.parts),
            reverse=True,
        ):
            try:
                path.rmdir()
            except OSError:
                return
        try:
            workspace.rmdir()
        except OSError:
            pass

    def _migrate_generated_empty_mappings(self) -> None:
        """Remove only the old, unmistakably auto-generated empty mapping.

        The file is preserved in that project's trash. User-created empty
        mappings are not touched because their generated name signature does
        not match.
        """
        for project_dir in self.root.iterdir():
            if not project_dir.is_dir() or project_dir.name.startswith('.'):
                continue
            manifest_path = project_dir / 'project.json'
            generated = project_dir / 'motion_axis_matching' / DEFAULT_MOTION_AXIS_FILE
            if not manifest_path.is_file() or not generated.is_file():
                continue
            try:
                manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
                payload = yaml.safe_load(generated.read_text(encoding='utf-8')) or {}
            except (OSError, ValueError, json.JSONDecodeError, yaml.YAMLError):
                continue
            if not isinstance(manifest, dict) or not isinstance(payload, dict):
                continue
            if (
                str(payload.get('file_id') or '') != DEFAULT_MOTION_AXIS_FILE
                or str(payload.get('name') or '') != f'{project_dir.name}_motion_axes'
                or str(payload.get('motion_file_id') or '')
                or payload.get('mappings') != []
            ):
                continue
            trash_dir = project_dir / 'trash' / 'motion_axis_matching'
            trash_dir.mkdir(parents=True, exist_ok=True)
            target = trash_dir / f'legacy-generated-empty-{DEFAULT_MOTION_AXIS_FILE}'
            counter = 2
            while target.exists():
                target = trash_dir / f'legacy-generated-empty-{counter}-{DEFAULT_MOTION_AXIS_FILE}'
                counter += 1
            generated.rename(target)
            active = manifest.get('active_files')
            if not isinstance(active, dict):
                active = {}
                manifest['active_files'] = active
            if active.get('motion_axis_matching') == DEFAULT_MOTION_AXIS_FILE:
                remaining = sorted(
                    path.name
                    for path in (project_dir / 'motion_axis_matching').iterdir()
                    if path.is_file() and path.suffix.lower() in ('.yaml', '.yml')
                )
                active['motion_axis_matching'] = remaining[0] if remaining else ''
            manifest['updated_at'] = time.time()
            self._write_manifest(project_dir, manifest)

    def list_projects(self) -> Dict[str, Any]:
        projects = []
        for path in sorted(self.root.iterdir(), key=lambda item: item.name.lower()):
            if not path.is_dir() or path.name.startswith('.'):
                continue
            manifest_path = path / 'project.json'
            if not manifest_path.is_file():
                continue
            try:
                manifest = self._read_manifest(path)
            except (OSError, ValueError, json.JSONDecodeError):
                continue
            projects.append(self._project_summary(path, manifest))
        selected = self.selected_project_id()
        if selected and not any(item['project_id'] == selected for item in projects):
            selected = ''
        return {
            'success': True,
            'projects': projects,
            'selected_project_id': selected,
            'project_root': str(self.root),
        }

    def create_project(self, name: Any) -> Dict[str, Any]:
        now = time.time()
        project_id = f'{_safe_stem(name, "motion_project")}-{uuid.uuid4().hex[:8]}'
        project_dir = self.root / project_id
        project_dir.mkdir()
        for category in PROJECT_CATEGORIES:
            (project_dir / category).mkdir()
        (project_dir / 'runtime').mkdir()
        (project_dir / 'trash').mkdir()
        manifest = {
            'version': PROJECT_VERSION,
            'project_id': project_id,
            'name': str(name or '새 모션 프로젝트').strip() or '새 모션 프로젝트',
            'memo': '',
            'created_at': now,
            'updated_at': now,
            'active_files': {
                'motor_axes': DEFAULT_MOTOR_FILE,
                # A new project has no motion-axis mapping until the user
                # creates or imports one.  Do not manufacture a file that can
                # be mistaken for a user-owned setup.
                'motion_axis_matching': '',
                'motions': '',
                'layers': '',
            },
            'runtime_state': {
                'applied_motor_sha256': '',
                'applied_at': None,
                'jog_verified': False,
            },
        }
        self._atomic_write(
            project_dir / 'motor_axes' / DEFAULT_MOTOR_FILE,
            yaml.safe_dump(self._empty_motor_config(), sort_keys=False, allow_unicode=True),
        )
        self._write_manifest(project_dir, manifest)
        self.select_project(project_id)
        return self.get_project(project_id)

    def get_project(self, project_id: Any) -> Dict[str, Any]:
        project_dir = self._project_dir(project_id)
        manifest = self._read_manifest(project_dir)
        return {
            'success': True,
            'project': self._project_summary(project_dir, manifest),
            'tree': self._tree(project_dir, manifest),
        }

    def update_project_memo(self, project_id: Any, memo: Any) -> Dict[str, Any]:
        project_dir = self._project_dir(project_id)
        text = str(memo or '')
        if len(text) > 4000:
            raise ValueError('프로젝트 메모는 4000자까지 입력할 수 있습니다')
        manifest = self._read_manifest(project_dir)
        manifest['memo'] = text
        self._write_manifest(project_dir, manifest)
        return self.get_project(project_dir.name)

    def select_project(self, project_id: Any) -> Dict[str, Any]:
        project_dir = self._project_dir(project_id)
        selection = self._read_selection()
        selection['project_id'] = project_dir.name
        self._atomic_write(
            self.selection_file,
            json.dumps(selection, ensure_ascii=False) + '\n',
        )
        return self.get_project(project_dir.name)

    def _read_selection(self) -> Dict[str, Any]:
        try:
            payload = json.loads(self.selection_file.read_text(encoding='utf-8'))
        except (OSError, ValueError, json.JSONDecodeError):
            return {}
        return payload if isinstance(payload, dict) else {}

    def selected_project_id(self) -> str:
        payload = self._read_selection()
        project_id = str(payload.get('project_id') or '').strip()
        return project_id if project_id == Path(project_id).name else ''

    def mark_runtime_motor_config_applied(self, project_id: Any) -> Path:
        """Remember only which project runtime file the managed service must run."""
        project_dir = self._project_dir(project_id)
        runtime = project_dir / 'runtime' / 'applied_motor_config.yaml'
        if not runtime.is_file():
            raise ValueError('적용할 런타임 모터축 설정 파일이 없습니다')
        selection = self._read_selection()
        selection['applied_project_id'] = project_dir.name
        self._atomic_write(
            self.selection_file,
            json.dumps(selection, ensure_ascii=False) + '\n',
        )
        return runtime

    def applied_runtime_motor_config(self) -> Optional[Path]:
        """Resolve the managed service config strictly inside its project folder."""
        selection = self._read_selection()
        project_id = str(selection.get('applied_project_id') or '').strip()
        if not project_id or project_id != Path(project_id).name:
            return None
        try:
            project_dir = self._project_dir(project_id)
        except (FileNotFoundError, ValueError):
            return None
        runtime = project_dir / 'runtime' / 'applied_motor_config.yaml'
        return runtime if runtime.is_file() else None

    def delete_project(self, project_id: Any) -> Dict[str, Any]:
        project_dir = self._project_dir(project_id)
        manifest = self._read_manifest(project_dir)
        trash_root = self.root / '.trash' / 'projects'
        trash_root.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime('%Y%m%d-%H%M%S')
        target = trash_root / f'{stamp}-{project_dir.name}'
        counter = 1
        while target.exists():
            target = trash_root / f'{stamp}-{counter}-{project_dir.name}'
            counter += 1
        project_dir.rename(target)
        selection = self._read_selection()
        if selection.get('project_id') == project_dir.name:
            selection.pop('project_id', None)
        if selection.get('applied_project_id') == project_dir.name:
            selection.pop('applied_project_id', None)
        if selection:
            self._atomic_write(
                self.selection_file,
                json.dumps(selection, ensure_ascii=False) + '\n',
            )
        else:
            try:
                self.selection_file.unlink()
            except FileNotFoundError:
                pass
        return {
            **self.list_projects(),
            'message': f"프로젝트 '{manifest.get('name') or project_dir.name}'를 휴지통으로 이동했습니다",
            'deleted_project_id': project_dir.name,
            'trash_path': str(target),
        }

    def copy_file_from_project(
        self,
        target_project_id: Any,
        source_project_id: Any,
        category: Any,
        file_name: Any,
        new_name: Any = None,
    ) -> Dict[str, Any]:
        target_dir = self._project_dir(target_project_id)
        source_dir = self._project_dir(source_project_id)
        if target_dir == source_dir:
            raise ValueError('같은 프로젝트가 아닌 다른 프로젝트를 선택하세요')
        safe_category = self._category(category)
        source = self._asset_path(source_dir.name, safe_category, file_name)
        requested_name = new_name if str(new_name or '').strip() else source.name
        target_name = self._file_name(safe_category, requested_name)
        target = target_dir / safe_category / target_name
        if target.exists():
            stem = Path(target_name).stem
            suffix = Path(target_name).suffix
            counter = 2
            target = target_dir / safe_category / f'{stem}-copy{suffix}'
            while target.exists():
                target = target_dir / safe_category / f'{stem}-copy-{counter}{suffix}'
                counter += 1
            target_name = target.name
        content = source.read_text(encoding='utf-8')
        if len(content.encode('utf-8')) > MAX_TEXT_BYTES:
            raise ValueError('파일이 10MB 제한을 초과합니다')
        self._validate_content(safe_category, target_name, content)
        shutil.copy2(source, target)
        manifest = self._read_manifest(target_dir)
        if not manifest['active_files'].get(safe_category):
            manifest['active_files'][safe_category] = target_name
        self._write_manifest(target_dir, manifest)
        result = self.get_project(target_dir.name)
        result.update({
            'message': f'{source_dir.name}/{source.name}을 {target_dir.name}/{safe_category}/{target_name}으로 복사했습니다',
            'copied_file': {
                'source_project_id': source_dir.name,
                'target_project_id': target_dir.name,
                'category': safe_category,
                'source_file_name': source.name,
                'file_name': target_name,
                'path': str(target),
            },
        })
        return result

    def import_text(
        self, project_id: Any, category: Any, file_name: Any, content: Any
    ) -> Dict[str, Any]:
        project_dir = self._project_dir(project_id)
        safe_category = self._category(category)
        name = self._file_name(safe_category, file_name)
        text = str(content if content is not None else '')
        encoded = text.encode('utf-8')
        if len(encoded) > MAX_TEXT_BYTES:
            raise ValueError('파일이 10MB 제한을 초과합니다')
        self._validate_content(safe_category, name, text)
        target = project_dir / safe_category / name
        if target.exists():
            raise ValueError(f'같은 이름의 파일이 이미 있습니다: {name}')
        self._atomic_write(target, text if text.endswith('\n') else text + '\n')
        manifest = self._read_manifest(project_dir)
        if not manifest['active_files'].get(safe_category):
            manifest['active_files'][safe_category] = name
        self._write_manifest(project_dir, manifest)
        return self.get_project(project_dir.name)

    def read_file(self, project_id: Any, category: Any, file_name: Any) -> Dict[str, Any]:
        path = self._asset_path(project_id, category, file_name)
        content = path.read_text(encoding='utf-8')
        return {
            'success': True,
            'project_id': self._project_dir(project_id).name,
            'category': self._category(category),
            'file_name': path.name,
            'content': content,
            'size': path.stat().st_size,
            'sha256': _sha256(content.encode('utf-8')),
        }

    def save_file(
        self, project_id: Any, category: Any, file_name: Any, content: Any
    ) -> Dict[str, Any]:
        path = self._asset_path(project_id, category, file_name)
        text = str(content if content is not None else '')
        if len(text.encode('utf-8')) > MAX_TEXT_BYTES:
            raise ValueError('파일이 10MB 제한을 초과합니다')
        self._validate_content(self._category(category), path.name, text)
        self._atomic_write(path, text if text.endswith('\n') else text + '\n')
        self._touch_manifest(self._project_dir(project_id))
        return self.read_file(project_id, category, file_name)

    def rename_file(
        self, project_id: Any, category: Any, file_name: Any, new_name: Any
    ) -> Dict[str, Any]:
        project_dir = self._project_dir(project_id)
        safe_category = self._category(category)
        source = self._asset_path(project_id, safe_category, file_name)
        target_name = self._file_name(safe_category, new_name)
        target = project_dir / safe_category / target_name
        if target.exists():
            raise ValueError(f'같은 이름의 파일이 이미 있습니다: {target_name}')
        source.rename(target)
        manifest = self._read_manifest(project_dir)
        if manifest['active_files'].get(safe_category) == source.name:
            manifest['active_files'][safe_category] = target.name
        self._write_manifest(project_dir, manifest)
        return self.get_project(project_dir.name)

    def delete_file(self, project_id: Any, category: Any, file_name: Any) -> Dict[str, Any]:
        project_dir = self._project_dir(project_id)
        safe_category = self._category(category)
        source = self._asset_path(project_id, safe_category, file_name)
        manifest = self._read_manifest(project_dir)
        was_active = manifest['active_files'].get(safe_category) == source.name
        trash_dir = project_dir / 'trash' / safe_category
        trash_dir.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime('%Y%m%d-%H%M%S')
        target = trash_dir / f'{stamp}-{source.name}'
        counter = 1
        while target.exists():
            target = trash_dir / f'{stamp}-{counter}-{source.name}'
            counter += 1
        source.rename(target)
        replacement = ''
        if was_active:
            remaining = sorted(
                path.name for path in (project_dir / safe_category).iterdir()
                if path.is_file() and not path.is_symlink()
            )
            if remaining:
                replacement = remaining[0]
            elif safe_category == 'motor_axes':
                replacement = DEFAULT_MOTOR_FILE
                self._atomic_write(
                    project_dir / safe_category / replacement,
                    yaml.safe_dump(self._empty_motor_config(), sort_keys=False, allow_unicode=True),
                )
            manifest['active_files'][safe_category] = replacement
        manifest['updated_at'] = time.time()
        self._write_manifest(project_dir, manifest)
        result = self.get_project(project_dir.name)
        result.update({
            'message': '파일을 프로젝트 휴지통으로 이동했습니다',
            'deleted_file': source.name,
            'replacement_active_file': replacement,
            'trash_path': str(target),
        })
        return result

    def set_active(
        self, project_id: Any, category: Any, file_name: Any
    ) -> Dict[str, Any]:
        project_dir = self._project_dir(project_id)
        safe_category = self._category(category)
        path = self._asset_path(project_id, safe_category, file_name)
        manifest = self._read_manifest(project_dir)
        manifest['active_files'][safe_category] = path.name
        self._write_manifest(project_dir, manifest)
        return self.get_project(project_dir.name)

    def export_path(self, project_id: Any, category: Any, file_name: Any) -> Path:
        return self._asset_path(project_id, category, file_name)

    def prepare_runtime_motor_config(self, project_id: Any) -> Dict[str, Any]:
        """Create the disposable motor config consumed by control nodes."""
        project_dir = self._project_dir(project_id)
        manifest = self._read_manifest(project_dir)
        file_name = manifest['active_files'].get('motor_axes') or ''
        if not file_name:
            raise ValueError('현재 모터축 설정 파일이 없습니다')
        source = self._asset_path(project_id, 'motor_axes', file_name)
        content = source.read_text(encoding='utf-8')
        payload = yaml.safe_load(content) or {}
        if not isinstance(payload, dict):
            raise ValueError('모터축 설정 YAML 최상위 값은 객체여야 합니다')
        motor_count = sum(
            len(master.get('slaves') or [])
            for master in payload.get('masters') or []
            if isinstance(master, dict)
        )
        if motor_count < 1:
            raise ValueError('등록된 모터축이 없어 설정을 적용할 수 없습니다')
        self._validate_runtime_motor_profiles(payload)
        runtime_payload = self._relocate_workspace_paths(payload)
        runtime_content = yaml.safe_dump(
            runtime_payload, sort_keys=False, allow_unicode=True
        )
        runtime = project_dir / 'runtime' / 'applied_motor_config.yaml'
        self._atomic_write(runtime, runtime_content)
        checksum = _sha256(content.encode('utf-8'))
        state = manifest.get('runtime_state') if isinstance(manifest.get('runtime_state'), dict) else {}
        state.update({'applied_motor_sha256': checksum, 'applied_at': time.time()})
        manifest['runtime_state'] = state
        self._write_manifest(project_dir, manifest)
        return {
            'success': True,
            'project_id': project_dir.name,
            'source_file': source.name,
            'runtime_file': str(runtime),
            'sha256': checksum,
        }

    @staticmethod
    def _validate_runtime_motor_profiles(payload: Dict[str, Any]) -> None:
        """Reject incomplete or accidentally count-scaled motion profiles.

        A bus scan identifies devices, but it does not measure a safe motion
        profile.  Applying a slave that references no driver, or a profile so
        slow that ordinary jog appears broken, must fail before control nodes
        are restarted.
        """
        drivers = {
            driver.get('id'): driver
            for driver in payload.get('drivers') or []
            if isinstance(driver, dict) and driver.get('id') is not None
        }
        required_positive = (
            'profile_velocity',
            'profile_acceleration',
            'profile_deceleration',
        )
        for master in payload.get('masters') or []:
            if not isinstance(master, dict):
                continue
            for slave in master.get('slaves') or []:
                if not isinstance(slave, dict):
                    continue
                axis = slave.get('controller_index', '?')
                driver_id = slave.get('driver_id')
                driver = drivers.get(driver_id)
                if not isinstance(driver, dict):
                    raise ValueError(
                        f'Axis {axis}의 driver_id {driver_id} 설정이 없습니다'
                    )
                for field in required_positive:
                    try:
                        value = float(driver.get(field))
                    except (TypeError, ValueError):
                        value = 0.0
                    if value <= 0.0:
                        raise ValueError(
                            f'Axis {axis}의 {field} 값을 0보다 크게 설정하세요'
                        )
                if str(driver.get('type') or '') == 'minas':
                    velocity = float(driver['profile_velocity'])
                    if velocity < 0.1:
                        raise ValueError(
                            f'Axis {axis}의 AC profile_velocity가 {velocity:g} deg/s로 '
                            '지나치게 낮습니다. 모터 모델의 운전 프로파일을 확인하세요'
                        )

    def discover_usb_projects(self) -> Dict[str, Any]:
        """Rescan project folders copied into the project root by USB/file manager."""
        result = self.list_projects()
        result['message'] = f"프로젝트 폴더 {len(result['projects'])}개를 확인했습니다"
        result['project_root'] = str(self.root)
        return result

    def mark_jog_verified(self) -> None:
        project_id = self.selected_project_id()
        if not project_id:
            return
        project_dir = self._project_dir(project_id)
        manifest = self._read_manifest(project_dir)
        state = manifest.get('runtime_state') if isinstance(manifest.get('runtime_state'), dict) else {}
        state['jog_verified'] = True
        manifest['runtime_state'] = state
        self._write_manifest(project_dir, manifest)

    def sync_project_file(self, category: Any, source: Path | str) -> Dict[str, Any]:
        """Validate a file already saved inside the selected project.

        A file outside the selected project is rejected. Import and
        cross-project copy must use their explicit repository operations.
        """
        project_id = self.selected_project_id()
        if not project_id:
            return {'success': True, 'synced': False, 'message': '선택된 프로젝트 없음'}
        project_dir = self._project_dir(project_id)
        safe_category = self._category(category)
        source_path = Path(source).expanduser().resolve()
        name = self._file_name(safe_category, source_path.name)
        category_dir = (project_dir / safe_category).resolve()
        if source_path.parent != category_dir:
            raise ValueError('현재 프로젝트 외부 파일은 자동 동기화할 수 없습니다')
        if not source_path.is_file() or source_path.is_symlink():
            raise ValueError(f'동기화할 파일을 찾을 수 없습니다: {name}')
        if source_path.stat().st_size > MAX_TEXT_BYTES:
            raise ValueError('파일이 10MB 제한을 초과합니다')
        text = source_path.read_text(encoding='utf-8')
        self._validate_content(safe_category, name, text)
        manifest = self._read_manifest(project_dir)
        manifest['active_files'][safe_category] = name
        self._write_manifest(project_dir, manifest)
        return {
            'success': True,
            'synced': True,
            'project_id': project_id,
            'category': safe_category,
            'file_name': name,
        }

    def sync_studio_layers(self, studio_project: Any) -> Dict[str, Any]:
        project_id = self.selected_project_id()
        if not project_id or not isinstance(studio_project, dict):
            return {'success': True, 'synced': False, 'message': '동기화할 프로젝트 없음'}
        project_dir = self._project_dir(project_id)
        studio_id = _safe_stem(studio_project.get('project_id'), 'studio')
        manifest = self._read_manifest(project_dir)
        for previous in manifest.get('studio_managed_layers') or []:
            if not isinstance(previous, str) or previous != Path(previous).name:
                continue
            path = project_dir / 'layers' / previous
            if path.is_file() and not path.is_symlink():
                path.unlink()
        synced = []
        for index, layer in enumerate(studio_project.get('layers') or []):
            if not isinstance(layer, dict):
                continue
            layer_id = _safe_stem(layer.get('layer_id'), f'layer_{index + 1}')
            name = f'{studio_id}__{layer_id}.json'
            content = json.dumps(layer, ensure_ascii=False, indent=2) + '\n'
            self._validate_content('layers', name, content)
            self._atomic_write(project_dir / 'layers' / name, content)
            synced.append(name)
        manifest['studio_managed_layers'] = synced
        active_layer = manifest['active_files'].get('layers')
        if synced and not active_layer:
            manifest['active_files']['layers'] = synced[0]
        elif active_layer and not (project_dir / 'layers' / active_layer).is_file():
            manifest['active_files']['layers'] = synced[0] if synced else ''
        self._write_manifest(project_dir, manifest)
        return {
            'success': True,
            'synced': bool(synced),
            'project_id': project_id,
            'files': synced,
        }

    def _tree(self, project_dir: Path, manifest: Dict[str, Any]) -> list[Dict[str, Any]]:
        tree = []
        active = manifest.get('active_files') or {}
        for category in PROJECT_CATEGORIES:
            children = []
            for path in sorted(
                (project_dir / category).iterdir(), key=lambda item: item.name.lower()
            ):
                if not path.is_file() or path.is_symlink():
                    continue
                if path.suffix.lower() not in PROJECT_CATEGORIES[category]:
                    continue
                content = path.read_bytes()
                children.append({
                    'name': path.name,
                    'category': category,
                    'size': len(content),
                    'sha256': _sha256(content),
                    'active': active.get(category) == path.name,
                    **(
                        {'midi_banks': self._midi_bank_tree_info(path)}
                        if category == 'motion_axis_matching' else {}
                    ),
                })
            tree.append({
                'category': category,
                'name': DISPLAY_NAMES[category],
                'children': children,
            })
        return tree

    @staticmethod
    def _midi_bank_tree_info(path: Path) -> Dict[str, Any]:
        """Describe the MIDI banks embedded in one project-local mapping file."""
        try:
            root = yaml.safe_load(path.read_text(encoding='utf-8')) or {}
        except (OSError, yaml.YAMLError):
            root = {}
        state = root.get('midi_banks') if isinstance(root, dict) else None
        if not isinstance(state, dict):
            return {
                'stored': False,
                'count': 0,
                'active_bank_id': '',
                'banks': [],
            }
        banks = []
        for item in state.get('banks') or []:
            if not isinstance(item, dict):
                continue
            mappings = item.get('mappings')
            banks.append({
                'bank_id': str(item.get('bank_id') or ''),
                'name': str(item.get('name') or item.get('bank_id') or '이름 없음'),
                'mapping_count': len(mappings) if isinstance(mappings, list) else 0,
            })
        return {
            'stored': True,
            'count': len(banks),
            'active_bank_id': str(state.get('active_bank_id') or ''),
            'banks': banks,
        }

    def _project_summary(self, project_dir: Path, manifest: Dict[str, Any]) -> Dict[str, Any]:
        counts = {
            category: sum(
                1
                for item in (project_dir / category).iterdir()
                if item.is_file()
                and not item.is_symlink()
                and item.suffix.lower() in PROJECT_CATEGORIES[category]
            )
            for category in PROJECT_CATEGORIES
        }
        return {
            **manifest,
            'path': str(project_dir),
            'counts': counts,
            'setup_status': self._setup_status(project_dir, manifest),
            'selected': project_dir.name == self.selected_project_id(),
        }

    def _setup_status(self, project_dir: Path, manifest: Dict[str, Any]) -> Dict[str, Any]:
        active = manifest.get('active_files') or {}
        motor_path = project_dir / 'motor_axes' / str(active.get('motor_axes') or '')
        axis_path = project_dir / 'motion_axis_matching' / str(
            active.get('motion_axis_matching') or ''
        )
        motor_count = 0
        motion_axis_count = 0
        motor_sha = ''
        try:
            motor_text = motor_path.read_text(encoding='utf-8')
            motor_config = yaml.safe_load(motor_text) or {}
            motor_sha = _sha256(motor_text.encode('utf-8'))
            for master in motor_config.get('masters') or []:
                if isinstance(master, dict):
                    motor_count += len(master.get('slaves') or [])
        except (OSError, yaml.YAMLError, AttributeError):
            pass
        try:
            motion_axis = yaml.safe_load(axis_path.read_text(encoding='utf-8')) or {}
            motion_axis_count = len(motion_axis.get('mappings') or motion_axis.get('axes') or [])
        except (OSError, yaml.YAMLError, AttributeError):
            pass
        runtime_state = manifest.get('runtime_state') or {}
        applied = bool(motor_sha and runtime_state.get('applied_motor_sha256') == motor_sha)
        return {
            'project_created': True,
            'motor_count': motor_count,
            'motor_configured': motor_count > 0,
            'motor_applied': applied,
            'jog_verified': bool(runtime_state.get('jog_verified')),
            'motion_axis_count': motion_axis_count,
            'motion_axes_configured': motion_axis_count > 0,
            'motion_count': sum(
                1 for path in (project_dir / 'motions').iterdir() if path.is_file()
            ),
        }

    def _project_dir(self, project_id: Any) -> Path:
        name = str(project_id or '').strip()
        if not name or name != Path(name).name or name.startswith('.'):
            raise ValueError('올바르지 않은 프로젝트 ID입니다')
        path = (self.root / name).resolve()
        if path.parent != self.root or not (path / 'project.json').is_file():
            raise ValueError(f'프로젝트를 찾을 수 없습니다: {name}')
        return path

    def _read_manifest(self, project_dir: Path) -> Dict[str, Any]:
        payload = json.loads((project_dir / 'project.json').read_text(encoding='utf-8'))
        if not isinstance(payload, dict) or payload.get('version') != PROJECT_VERSION:
            raise ValueError('지원하지 않는 프로젝트 파일입니다')
        if payload.get('project_id') != project_dir.name:
            raise ValueError('프로젝트 ID와 디렉터리 이름이 다릅니다')
        active = payload.get('active_files')
        if not isinstance(active, dict):
            active = {}
        payload['active_files'] = {
            category: str(active.get(category) or '') for category in PROJECT_CATEGORIES
        }
        payload['memo'] = str(payload.get('memo') or '')
        for category in PROJECT_CATEGORIES:
            (project_dir / category).mkdir(exist_ok=True)
        (project_dir / 'runtime').mkdir(exist_ok=True)
        (project_dir / 'trash').mkdir(exist_ok=True)
        return payload

    def _write_manifest(self, project_dir: Path, manifest: Dict[str, Any]) -> None:
        manifest = dict(manifest)
        manifest['updated_at'] = time.time()
        self._atomic_write(
            project_dir / 'project.json',
            json.dumps(manifest, ensure_ascii=False, indent=2) + '\n',
        )

    def _touch_manifest(self, project_dir: Path) -> None:
        self._write_manifest(project_dir, self._read_manifest(project_dir))

    def _asset_path(self, project_id: Any, category: Any, file_name: Any) -> Path:
        project_dir = self._project_dir(project_id)
        safe_category = self._category(category)
        name = self._file_name(safe_category, file_name)
        path = (project_dir / safe_category / name).resolve()
        if path.parent != (project_dir / safe_category).resolve() or not path.is_file():
            raise ValueError(f'프로젝트 파일을 찾을 수 없습니다: {name}')
        return path

    @staticmethod
    def _category(category: Any) -> str:
        value = str(category or '').strip()
        if value not in PROJECT_CATEGORIES:
            raise ValueError('지원하지 않는 프로젝트 파일 종류입니다')
        return value

    @staticmethod
    def _file_name(category: str, file_name: Any) -> str:
        name = str(file_name or '').strip()
        suffixes = PROJECT_CATEGORIES[category]
        if (
            not name
            or name != Path(name).name
            or name.startswith('.')
            or Path(name).suffix.lower() not in suffixes
        ):
            raise ValueError('파일명 또는 확장자가 올바르지 않습니다')
        return name

    @staticmethod
    def _validate_content(category: str, file_name: str, content: str) -> None:
        if not content.strip():
            raise ValueError('빈 파일은 저장할 수 없습니다')
        try:
            if category in {'motor_axes', 'motion_axis_matching'}:
                payload = yaml.safe_load(content)
                if not isinstance(payload, dict):
                    raise ValueError('YAML 최상위 값은 객체여야 합니다')
            elif category == 'motions':
                lines = [line.strip() for line in content.splitlines() if line.strip()]
                if len(lines) < 2:
                    raise ValueError('모션 헤더와 프레임 데이터가 필요합니다')
                header = json.loads(lines[0])
                if not isinstance(header, dict) or header.get('type') != 'motion_header':
                    raise ValueError('지원하지 않는 모션 파일 헤더입니다')
                for line in lines[1:]:
                    if not isinstance(json.loads(line), list):
                        raise ValueError('모션 프레임은 배열이어야 합니다')
            else:
                if not isinstance(json.loads(content), dict):
                    raise ValueError('레이어 파일 최상위 값은 객체여야 합니다')
        except (json.JSONDecodeError, yaml.YAMLError) as exc:
            raise ValueError(f'{file_name} 파일 형식이 올바르지 않습니다: {exc}') from exc

    @staticmethod
    def _atomic_write(path: Path, content: str) -> None:
        temporary = path.with_name(f'.{path.name}.{uuid.uuid4().hex}.tmp')
        temporary.write_text(content, encoding='utf-8')
        temporary.replace(path)

    @staticmethod
    def _empty_motor_config() -> Dict[str, Any]:
        return {
            'period': 1000000,
            'masters': [],
            'drivers': [],
        }

    @staticmethod
    def _empty_motion_axis_config(project_id: str) -> Dict[str, Any]:
        return {
            'file_id': DEFAULT_MOTION_AXIS_FILE,
            'name': f'{project_id}_motion_axes',
            'motion_file_id': '',
            'mappings': [],
            'midi_banks': {
                'version': 1,
                'active_bank_id': 'bank_1',
                'banks': [{'bank_id': 'bank_1', 'name': 'Bank 1', 'mappings': []}],
            },
        }

    def _relocate_workspace_paths(self, value: Any) -> Any:
        """Resolve portable project paths for the current Git workspace."""
        if isinstance(value, dict):
            return {key: self._relocate_workspace_paths(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self._relocate_workspace_paths(item) for item in value]
        if not isinstance(value, str) or not value.startswith('/'):
            return value
        for marker in ('/src/', '/config/'):
            index = value.find(marker)
            if index >= 0:
                candidate = (self.root.parent / value[index + 1:]).resolve()
                return str(candidate)
        return value
