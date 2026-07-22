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
        self._migrate_generated_empty_motor_configs()
        self._migrate_internal_backups()
        self._remove_empty_no_project_workspace()
        self._normalize_selection_boundary()

    def _normalize_selection_boundary(self) -> None:
        """Never retain an applied runtime belonging to another project."""
        selection = self._read_selection()
        selected = str(selection.get('project_id') or '').strip()
        applied = str(selection.get('applied_project_id') or '').strip()
        if not applied or applied == selected:
            return
        selection.pop('applied_project_id', None)
        self._atomic_write(
            self.selection_file,
            json.dumps(selection, ensure_ascii=False) + '\n',
        )

    def _migrate_generated_empty_motor_configs(self) -> None:
        """Remove untouched placeholder motor files created by older releases."""
        empty = self._empty_motor_config()
        for project_dir in self.root.iterdir():
            if (
                not project_dir.is_dir()
                or project_dir.is_symlink()
                or project_dir.name.startswith('.')
            ):
                continue
            try:
                manifest = self._read_manifest(project_dir)
            except (OSError, ValueError, json.JSONDecodeError):
                continue
            active = manifest.get('active_files') or {}
            if active.get('motor_axes') != DEFAULT_MOTOR_FILE:
                continue
            path = project_dir / 'motor_axes' / DEFAULT_MOTOR_FILE
            runtime = manifest.get('runtime_state') or {}
            if (
                not path.is_file()
                or path.is_symlink()
                or runtime.get('applied_at') is not None
            ):
                continue
            if (project_dir / 'runtime' / 'applied_motor_config.yaml').is_file():
                continue
            try:
                content = yaml.safe_load(path.read_text(encoding='utf-8')) or {}
                created_at = float(manifest.get('created_at') or 0.0)
                untouched = abs(path.stat().st_mtime - created_at) <= 1.0
            except (OSError, TypeError, ValueError, yaml.YAMLError):
                continue
            if content != empty or not untouched:
                continue
            path.unlink()
            active['motor_axes'] = ''
            manifest['active_files'] = active
            self._write_manifest(project_dir, manifest)

    def _migrate_internal_backups(self) -> None:
        for project_dir in self.root.iterdir():
            if (
                not project_dir.is_dir()
                or project_dir.is_symlink()
                or project_dir.name.startswith('.')
            ):
                continue
            for category in ('motor_axes', 'motion_axis_matching'):
                source_dir = project_dir / category
                if not source_dir.is_dir() or source_dir.is_symlink():
                    continue
                history_dir = None
                for source in source_dir.glob('*.bak-*'):
                    if not source.is_file() or source.is_symlink():
                        continue
                    if history_dir is None:
                        history_dir = self._local_directory(
                            project_dir, 'runtime', 'history', category
                        )
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
            if (
                not project_dir.is_dir()
                or project_dir.is_symlink()
                or project_dir.name.startswith('.')
            ):
                continue
            manifest_path = project_dir / 'project.json'
            generated = project_dir / 'motion_axis_matching' / DEFAULT_MOTION_AXIS_FILE
            if (
                not manifest_path.is_file()
                or manifest_path.is_symlink()
                or not generated.is_file()
                or generated.is_symlink()
            ):
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
            trash_dir = self._local_directory(
                project_dir, 'trash', 'motion_axis_matching'
            )
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
        (project_dir / 'logs').mkdir()
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
                # The first motor file is created only by an explicit user save.
                'motor_axes': '',
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
        if str(selection.get('project_id') or '') != project_dir.name:
            selection.pop('applied_project_id', None)
        selection['project_id'] = project_dir.name
        self._atomic_write(
            self.selection_file,
            json.dumps(selection, ensure_ascii=False) + '\n',
        )
        return self.get_project(project_dir.name)

    def project_logs_dir(self, project_id: Any) -> Path:
        project_dir = self._project_dir(project_id)
        return self._local_directory(project_dir, 'logs')

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

    def project_generation(self) -> int:
        """Return the durable generation shared by the bridge and browser."""
        try:
            generation = int(self._read_selection().get('project_generation') or 1)
        except (TypeError, ValueError):
            generation = 1
        return max(1, generation)

    def set_project_generation(self, generation: Any) -> int:
        """Persist a monotonic project boundary across bridge restarts."""
        value = max(1, int(generation))
        selection = self._read_selection()
        previous = self.project_generation()
        if value < previous:
            raise ValueError('project_generation은 감소시킬 수 없습니다')
        selection['project_generation'] = value
        self._atomic_write(
            self.selection_file,
            json.dumps(selection, ensure_ascii=False) + '\n',
        )
        return value

    def execution_context(self, project_id: Any) -> Dict[str, Any]:
        """Return one immutable identity for the project's active runtime files."""
        project_dir = self._project_dir(project_id)
        manifest = self._read_manifest(project_dir)
        active = manifest.get('active_files') or {}
        files: Dict[str, Dict[str, Any]] = {}
        missing = []
        for category in PROJECT_CATEGORIES:
            name = str(active.get(category) or '').strip()
            item = {'name': name, 'sha256': '', 'exists': False}
            if name:
                try:
                    path = self._asset_path(project_dir.name, category, name)
                    content = path.read_bytes()
                    item.update({
                        'sha256': _sha256(content),
                        'exists': True,
                        'size': len(content),
                    })
                except (OSError, ValueError):
                    pass
            files[category] = item
            if category in {'motor_axes', 'motion_axis_matching'} and not item['exists']:
                missing.append(category)

        identity = {
            'version': 1,
            'project_id': project_dir.name,
            'files': {
                category: {
                    'name': item['name'],
                    'sha256': item['sha256'],
                }
                for category, item in files.items()
            },
        }
        encoded = json.dumps(
            identity, ensure_ascii=False, sort_keys=True, separators=(',', ':')
        ).encode('utf-8')
        selection = self._read_selection()
        motor_name = files['motor_axes']['name']
        motor_source = (
            project_dir / 'motor_axes' / motor_name if motor_name else None
        )
        motor_applied = bool(
            motor_source is not None
            and selection.get('applied_project_id') == project_dir.name
            and self._motor_runtime_matches(project_dir, motor_source)
        )
        return {
            **identity,
            'context_id': _sha256(encoded),
            'files': files,
            'missing': missing,
            'configuration_complete': not missing,
            'motor_applied': motor_applied,
            'created_at': time.time(),
        }

    def mark_runtime_motor_config_applied(self, project_id: Any) -> Path:
        """Remember only which project runtime file the managed service must run."""
        project_dir = self._project_dir(project_id)
        if self.selected_project_id() != project_dir.name:
            raise ValueError('현재 선택 프로젝트의 모터 설정만 적용할 수 있습니다')
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
        if project_id != self.selected_project_id():
            return None
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
        project_name = str(manifest.get('name') or project_dir.name)

        # Older releases archived deleted projects. Remove only archive
        # directories whose own manifest confirms the exact same project ID.
        trash_root = self.root / '.trash' / 'projects'
        if trash_root.is_dir() and not trash_root.is_symlink():
            for archived in list(trash_root.iterdir()):
                if not archived.is_dir() or archived.is_symlink():
                    continue
                try:
                    archived_manifest = json.loads(
                        (archived / 'project.json').read_text(encoding='utf-8')
                    )
                except (OSError, ValueError, json.JSONDecodeError):
                    continue
                if (
                    isinstance(archived_manifest, dict)
                    and archived_manifest.get('project_id') == project_dir.name
                ):
                    shutil.rmtree(archived)

        original_selection = self._read_selection()
        selection = dict(original_selection)
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

        # Rename inside the repository first so a partially removed project
        # can never be listed or opened. Report success only after rmtree has
        # removed the complete directory tree.
        deleting = self.root / f'.deleting-{project_dir.name}-{uuid.uuid4().hex}'
        try:
            project_dir.rename(deleting)
            shutil.rmtree(deleting)
        except OSError:
            if deleting.exists() and not project_dir.exists():
                deleting.rename(project_dir)
            if original_selection:
                self._atomic_write(
                    self.selection_file,
                    json.dumps(original_selection, ensure_ascii=False) + '\n',
                )
            else:
                try:
                    self.selection_file.unlink()
                except FileNotFoundError:
                    pass
            raise
        return {
            **self.list_projects(),
            'message': f"프로젝트 '{project_name}'와 관련 파일을 영구 삭제했습니다",
            'deleted_project_id': project_dir.name,
            'permanently_deleted': True,
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

    def read_read_only_file(self, project_id: Any, relative_path: Any) -> Dict[str, Any]:
        """Read protected project metadata/runtime files without exposing writes."""
        project_dir = self._project_dir(project_id)
        raw_path = str(relative_path or '').strip().replace('\\', '/')
        requested = Path(raw_path)
        if (
            not raw_path
            or requested.is_absolute()
            or '..' in requested.parts
            or any(part.startswith('.') for part in requested.parts)
        ):
            raise ValueError('올바르지 않은 읽기 전용 파일 경로입니다')
        if raw_path != 'project.json' and requested.parts[0] not in {'runtime', 'trash'}:
            raise ValueError('읽기 전용 프로젝트 파일만 열 수 있습니다')
        candidate = project_dir / requested
        path = candidate.resolve()
        try:
            path.relative_to(project_dir)
        except ValueError as exc:
            raise ValueError('프로젝트 외부 파일은 열 수 없습니다') from exc
        if candidate.is_symlink() or not path.is_file():
            raise ValueError(f'프로젝트 파일을 찾을 수 없습니다: {raw_path}')
        content_bytes = path.read_bytes()
        if len(content_bytes) > MAX_TEXT_BYTES:
            raise ValueError('파일이 10MB 제한을 초과하여 원문을 표시할 수 없습니다')
        try:
            content = content_bytes.decode('utf-8')
        except UnicodeDecodeError as exc:
            raise ValueError('텍스트 형식이 아닌 파일은 원문을 표시할 수 없습니다') from exc
        return {
            'success': True,
            'project_id': project_dir.name,
            'category': 'read_only',
            'file_name': path.name,
            'relative_path': requested.as_posix(),
            'content': content,
            'size': len(content_bytes),
            'sha256': _sha256(content_bytes),
            'read_only': True,
            'internal': True,
        }

    def save_file(
        self, project_id: Any, category: Any, file_name: Any, content: Any
    ) -> Dict[str, Any]:
        project_dir = self._project_dir(project_id)
        safe_category = self._category(category)
        name = self._file_name(safe_category, file_name)
        try:
            path = self._asset_path(project_id, safe_category, name)
        except ValueError:
            manifest = self._read_manifest(project_dir)
            if safe_category != 'motor_axes' or manifest['active_files'].get(safe_category):
                raise
            path = project_dir / safe_category / name
        text = str(content if content is not None else '')
        if len(text.encode('utf-8')) > MAX_TEXT_BYTES:
            raise ValueError('파일이 10MB 제한을 초과합니다')
        self._validate_content(safe_category, path.name, text)
        self._atomic_write(path, text if text.endswith('\n') else text + '\n')
        manifest = self._read_manifest(project_dir)
        if not manifest['active_files'].get(safe_category):
            manifest['active_files'][safe_category] = path.name
        self._write_manifest(project_dir, manifest)
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
        trash_dir = self._local_directory(project_dir, 'trash', safe_category)
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
        # Validate with web identity metadata still present.  Only the final
        # disposable runtime file removes those web-only fields.
        payload = dict(payload)
        payload['masters'] = [
            master
            for master in payload.get('masters') or []
            if not (
                isinstance(master, dict)
                and master.get('type') == 'ethercat'
                and not (master.get('slaves') or [])
            )
        ]
        motor_count = sum(
            len(master.get('slaves') or [])
            for master in payload.get('masters') or []
            if isinstance(master, dict)
        )
        if motor_count < 1:
            raise ValueError('등록된 모터축이 없어 설정을 적용할 수 없습니다')
        self._validate_runtime_motor_profiles(payload)
        runtime_payload = self._runtime_motor_payload(payload)
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

    def _runtime_motor_payload(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Return only the configuration that changes motor-node execution."""
        runtime_payload = dict(payload)
        # A serial-only project may retain an inert empty EtherCAT master.
        runtime_payload['masters'] = [
            master
            for master in runtime_payload.get('masters') or []
            if not (
                isinstance(master, dict)
                and master.get('type') == 'ethercat'
                and not (master.get('slaves') or [])
            )
        ]
        runtime_payload = self._relocate_workspace_paths(runtime_payload)
        # Physical scan identities are validated by the web workflow but are
        # deliberately not consumed by the established motor runtime schema.
        runtime_payload.pop('web_axis_identities', None)
        return runtime_payload

    def _motor_runtime_matches(self, project_dir: Path, source: Path) -> bool:
        """Compare effective motor settings, excluding web-only metadata."""
        runtime = project_dir / 'runtime' / 'applied_motor_config.yaml'
        try:
            payload = yaml.safe_load(source.read_text(encoding='utf-8')) or {}
            if not isinstance(payload, dict):
                return False
            expected = yaml.safe_dump(
                self._runtime_motor_payload(payload),
                sort_keys=False,
                allow_unicode=True,
            ).encode('utf-8')
            return runtime.is_file() and _sha256(runtime.read_bytes()) == _sha256(expected)
        except (OSError, yaml.YAMLError, AttributeError):
            return False

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
        used_controller_indices = set()
        used_nonzero_aliases = set()
        used_zero_alias_positions = set()
        identity_by_axis = {
            item.get('controller_index'): item
            for item in payload.get('web_axis_identities') or []
            if isinstance(item, dict) and item.get('controller_index') is not None
        }
        for master in payload.get('masters') or []:
            if not isinstance(master, dict):
                continue
            for slave in master.get('slaves') or []:
                if not isinstance(slave, dict):
                    continue
                axis = slave.get('controller_index', '?')
                if axis in used_controller_indices:
                    raise ValueError(f'Control Index {axis} 값이 중복되어 있습니다')
                used_controller_indices.add(axis)
                if str(master.get('type') or '') == 'ethercat':
                    try:
                        alias = int(slave.get('alias') or 0)
                        position = int(slave.get('position') or 0)
                    except (TypeError, ValueError) as exc:
                        raise ValueError(
                            f'Axis {axis}의 EEPROM Alias 또는 Position 값이 올바르지 않습니다'
                        ) from exc
                    identity = identity_by_axis.get(axis)
                    if isinstance(identity, dict):
                        try:
                            identity_alias = int(identity.get('eeprom_alias'))
                            identity_position = int(identity.get('slave_position'))
                        except (TypeError, ValueError) as exc:
                            raise ValueError(
                                f'Axis {axis}의 물리 식별 정보가 완전하지 않습니다'
                            ) from exc
                        if alias != identity_alias:
                            raise ValueError(
                                f'Axis {axis}의 EEPROM Alias가 실행 설정({alias})과 '
                                f'물리 식별 정보({identity_alias})에서 다릅니다. '
                                '모터축 설정에서 확인 후 변경 내용 저장을 누르세요'
                            )
                        if position != identity_position:
                            raise ValueError(
                                f'Axis {axis}의 Slave Position이 실행 설정({position})과 '
                                f'물리 식별 정보({identity_position})에서 다릅니다. '
                                '모터축 설정에서 확인 후 변경 내용 저장을 누르세요'
                            )
                    if alias != 0:
                        if alias in used_nonzero_aliases:
                            raise ValueError(
                                f'EEPROM Alias {alias} 값이 중복되어 있습니다'
                            )
                        used_nonzero_aliases.add(alias)
                    else:
                        if position in used_zero_alias_positions:
                            raise ValueError(
                                f'EEPROM Alias 0의 Slave Position {position} 값이 중복되어 있습니다'
                            )
                        used_zero_alias_positions.add(position)
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
        prepared = []
        for index, layer in enumerate(studio_project.get('layers') or []):
            if not isinstance(layer, dict):
                continue
            layer_id = _safe_stem(layer.get('layer_id'), f'layer_{index + 1}')
            name = f'{studio_id}__{layer_id}.json'
            content = json.dumps(layer, ensure_ascii=False, indent=2) + '\n'
            self._validate_content('layers', name, content)
            prepared.append((name, content))
        synced = [name for name, _content in prepared]
        for name, content in prepared:
            self._atomic_write(project_dir / 'layers' / name, content)
        for previous in manifest.get('studio_managed_layers') or []:
            if (
                not isinstance(previous, str)
                or previous != Path(previous).name
                or previous in synced
            ):
                continue
            path = project_dir / 'layers' / previous
            if path.is_file() and not path.is_symlink():
                path.unlink()
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
        manifest_path = project_dir / 'project.json'
        manifest_content = manifest_path.read_bytes()
        tree.append({
            'category': 'project_root',
            'name': '프로젝트 정보',
            'read_only': True,
            'children': [{
                'node_type': 'file',
                'name': manifest_path.name,
                'relative_path': manifest_path.name,
                'category': 'project_root',
                'size': len(manifest_content),
                'sha256': _sha256(manifest_content),
                'active': False,
                'read_only': True,
                'internal': True,
            }],
        })
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
        logs_dir = self._local_directory(project_dir, 'logs')
        log_children = []
        for path in sorted(logs_dir.glob('*.jsonl'), reverse=True):
            if not path.is_file() or path.is_symlink():
                continue
            try:
                size = path.stat().st_size
                record_count = sum(
                    1 for line in path.read_text(encoding='utf-8').splitlines() if line.strip()
                )
            except OSError:
                continue
            log_children.append({
                'name': path.name,
                'category': 'logs',
                'size': size,
                'record_count': record_count,
                'active': False,
            })
        tree.append({
            'category': 'logs',
            'name': '로그',
            'children': log_children,
        })
        for category, label in (
            ('runtime', 'runtime · 실행용'),
            ('trash', 'trash · 휴지통'),
        ):
            directory = self._local_directory(project_dir, category)
            tree.append({
                'category': category,
                'name': label,
                'read_only': True,
                'children': self._read_only_directory_tree(directory, project_dir, category),
            })
        return tree

    def _read_only_directory_tree(
        self,
        directory: Path,
        project_dir: Path,
        category: str,
    ) -> list[Dict[str, Any]]:
        """Return the real on-disk subtree without exposing mutation APIs."""
        nodes: list[Dict[str, Any]] = []
        try:
            entries = sorted(
                directory.iterdir(),
                key=lambda item: (not item.is_dir(), item.name.lower()),
            )
        except OSError:
            return nodes
        for path in entries:
            if path.is_symlink() or path.name.startswith('.'):
                continue
            try:
                relative_path = path.relative_to(project_dir).as_posix()
                if path.is_dir():
                    nodes.append({
                        'node_type': 'folder',
                        'name': path.name,
                        'relative_path': relative_path,
                        'category': category,
                        'read_only': True,
                        'internal': True,
                        'children': self._read_only_directory_tree(
                            path, project_dir, category
                        ),
                    })
                    continue
                if not path.is_file():
                    continue
                content = path.read_bytes()
            except OSError:
                continue
            nodes.append({
                'node_type': 'file',
                'name': path.name,
                'relative_path': relative_path,
                'category': category,
                'size': len(content),
                'sha256': _sha256(content),
                'active': False,
                'read_only': True,
                'internal': True,
            })
        return nodes

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
        selection = self._read_selection()
        applied = bool(
            motor_sha
            and selection.get('project_id') == project_dir.name
            and selection.get('applied_project_id') == project_dir.name
            and self._motor_runtime_matches(project_dir, motor_path)
        )
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
        candidate = self.root / name
        if candidate.is_symlink():
            raise ValueError('프로젝트 폴더는 링크일 수 없습니다')
        path = candidate.resolve()
        manifest_path = path / 'project.json'
        if (
            path.parent != self.root
            or manifest_path.is_symlink()
            or not manifest_path.is_file()
        ):
            raise ValueError(f'프로젝트를 찾을 수 없습니다: {name}')
        return path

    def _read_manifest(self, project_dir: Path) -> Dict[str, Any]:
        manifest_path = project_dir / 'project.json'
        if project_dir.is_symlink() or manifest_path.is_symlink():
            raise ValueError('프로젝트 구조에 링크를 사용할 수 없습니다')
        payload = json.loads(manifest_path.read_text(encoding='utf-8'))
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
            self._local_directory(project_dir, category)
        self._local_directory(project_dir, 'logs')
        self._local_directory(project_dir, 'runtime')
        self._local_directory(project_dir, 'trash')
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
        candidate = project_dir / safe_category / name
        path = candidate.resolve()
        if (
            path.parent != (project_dir / safe_category).resolve()
            or candidate.is_symlink()
            or not path.is_file()
        ):
            raise ValueError(f'프로젝트 파일을 찾을 수 없습니다: {name}')
        return path

    @staticmethod
    def _local_directory(project_dir: Path, *parts: str) -> Path:
        """Create a directory without following a link outside its project."""
        root = project_dir.resolve()
        current = project_dir
        for part in parts:
            current = current / part
            if current.is_symlink():
                raise ValueError(
                    f'프로젝트 내부 폴더는 링크일 수 없습니다: {current.name}'
                )
            if current.exists() and not current.is_dir():
                raise ValueError(
                    f'프로젝트 폴더 경로가 올바르지 않습니다: {current.name}'
                )
            current.mkdir(exist_ok=True)
            try:
                current.resolve().relative_to(root)
            except ValueError as exc:
                raise ValueError('프로젝트 외부 폴더는 사용할 수 없습니다') from exc
        return current

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
