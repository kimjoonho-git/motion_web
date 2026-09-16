"""Project-owned motion asset storage.

This module deliberately has no ROS or motor-control dependencies.  Opening,
editing, importing, or deleting a project asset must never apply hardware
configuration or issue a motor command.
"""

from __future__ import annotations

import io
import json
import shutil
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

import yaml

from motion_common import store

from .motor_runtime_store import MotorRuntimeStore
from .project_tree import build_tree
from .project_paths import (
    PROJECT_CATEGORIES,
    _is_user_file,
    _safe_stem,
    _sha256,
    _sha256_file,
    local_directory,
)

from .motor_profile_validation import validate_runtime_motor_profiles


PROJECT_VERSION = 1
MAX_TEXT_BYTES = 10 * 1024 * 1024
MAX_MOTION_TEXT_BYTES = 256 * 1024 * 1024
DEFAULT_MOTOR_FILE = 'motor_axes.yaml'
DEFAULT_MOTION_AXIS_FILE = 'motion_axes.yaml'
SERVO_ALARM_POLICY_FILE = 'servo_alarm_policy.json'
MOTOR_RUNTIME_TARGET_FIELDS = {
    'target_project_id',
    'session_id',
    'config_relpath',
    'config_sha256',
    'project_generation',
    'applied_at',
}


def _studio_layer_signature(layer_hashes: Dict[str, str]) -> str:
    rows = sorted(
        (str(name), str(digest))
        for name, digest in layer_hashes.items()
        if str(name) and str(digest)
    )
    return _sha256(
        json.dumps(rows, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    )


def _text_limit(category: str) -> tuple[int, str]:
    if category == 'motions':
        return MAX_MOTION_TEXT_BYTES, '256MB'
    return MAX_TEXT_BYTES, '10MB'


class ProjectRepository:
    """Manage project metadata and independent, reusable project files."""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.selection_file = self.root / '.selected_project.json'
        # 모터 실행 상태 파일과 그 락은 별도 객체가 갖는다 (§6-47)
        self.runtime = MotorRuntimeStore(self, self.root / '.motor_runtime.json')
        self._migrate_generated_empty_mappings()
        self._migrate_generated_empty_motor_configs()
        self._migrate_internal_backups()
        self._remove_empty_no_project_workspace()
        self.runtime._migrate_legacy_motor_runtime_state()

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
                        history_dir = local_directory(
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
            trash_dir = local_directory(
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
                    if _is_user_file(path)
                    and path.suffix.lower() in ('.yaml', '.yml')
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

    def load_servo_alarm_policy(self, project_id: Any = None) -> Dict[str, Any]:
        target_id = str(project_id or self.selected_project_id() or '').strip()
        if not target_id:
            return {'version': 1, 'overrides': {}}
        project_dir = self._project_dir(target_id)
        path = project_dir / SERVO_ALARM_POLICY_FILE
        if not path.is_file():
            return {'version': 1, 'overrides': {}}
        try:
            payload = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f'서보 에러 정책 파일을 읽을 수 없습니다: {exc}') from exc
        if not isinstance(payload, dict):
            raise ValueError('서보 에러 정책 파일 형식이 올바르지 않습니다')
        return payload

    def save_servo_alarm_policy(
        self,
        project_id: Any,
        overrides: Dict[str, int],
    ) -> Dict[str, Any]:
        project_dir = self._project_dir(project_id)
        if self.selected_project_id() != project_dir.name:
            raise ValueError('현재 선택 프로젝트의 서보 에러 등급만 저장할 수 있습니다')
        payload = {
            'version': 1,
            'updated_at': time.time(),
            'overrides': dict(overrides),
        }
        content = json.dumps(payload, ensure_ascii=False, indent=2) + '\n'
        path = project_dir / SERVO_ALARM_POLICY_FILE
        self._atomic_write(path, content)
        return {
            'success': True,
            'project_id': project_dir.name,
            'file': str(path),
            'sha256': _sha256(content.encode('utf-8')),
            **payload,
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
            'tree': build_tree(project_dir, manifest),
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

    def project_logs_dir(self, project_id: Any) -> Path:
        project_dir = self._project_dir(project_id)
        return local_directory(project_dir, 'logs')

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
        motor_name = files['motor_axes']['name']
        motor_source = (
            project_dir / 'motor_axes' / motor_name if motor_name else None
        )
        runtime_state = self.runtime.motor_runtime_state()
        motor_applied = bool(
            motor_source is not None
            and runtime_state.get('valid') is True
            and runtime_state.get('target_project_id') == project_dir.name
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

    def delete_project(self, project_id: Any) -> Dict[str, Any]:
        project_dir = self._project_dir(project_id)
        runtime_target = self.runtime.motor_runtime_state()
        if runtime_target.get('target_project_id') == project_dir.name:
            raise ValueError(
                '현재 모터 실행 설정이 사용하는 프로젝트는 삭제할 수 없습니다. '
                '「전체 동작 정지」 후 「실행 적용 해제」를 실행하거나, '
                '다른 프로젝트의 모터 설정을 적용한 뒤 다시 시도하세요'
            )
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

    def import_text(
        self, project_id: Any, category: Any, file_name: Any, content: Any
    ) -> Dict[str, Any]:
        project_dir = self._project_dir(project_id)
        safe_category = self._category(category)
        name = self._file_name(safe_category, file_name)
        text = str(content if content is not None else '')
        encoded = text.encode('utf-8')
        limit, label = _text_limit(safe_category)
        if len(encoded) > limit:
            raise ValueError(f'파일이 {label} 제한을 초과합니다')
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
        limit, label = _text_limit(safe_category)
        if len(text.encode('utf-8')) > limit:
            raise ValueError(f'파일이 {label} 제한을 초과합니다')
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
        trash_dir = local_directory(project_dir, 'trash', safe_category)
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
                if _is_user_file(path)
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
        validate_runtime_motor_profiles(payload)
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
        runtime_payload.pop('web_axis_profiles', None)
        return runtime_payload

    def _motor_runtime_matches(self, project_dir: Path, source: Path) -> bool:
        """Compare effective motor settings, excluding web-only metadata."""
        try:
            payload = yaml.safe_load(source.read_text(encoding='utf-8')) or {}
            if not isinstance(payload, dict):
                return False
            expected = yaml.safe_dump(
                self._runtime_motor_payload(payload),
                sort_keys=False,
                allow_unicode=True,
            ).encode('utf-8')
            runtime_state = self.runtime.motor_runtime_state()
            if (
                runtime_state.get('valid') is not True
                or runtime_state.get('target_project_id') != project_dir.name
            ):
                return False
            runtime = Path(str(runtime_state.get('config_file') or ''))
            return runtime.is_file() and _sha256(runtime.read_bytes()) == _sha256(expected)
        except (OSError, yaml.YAMLError, AttributeError):
            return False

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
        limit, label = _text_limit(safe_category)
        if source_path.stat().st_size > limit:
            raise ValueError(f'파일이 {label} 제한을 초과합니다')
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

    def sync_studio_layers(
        self,
        studio_project: Any,
        *,
        upsert_layer_ids: Optional[Iterable[Any]] = None,
        delete_layer_ids: Optional[Iterable[Any]] = None,
        replace_all: bool = True,
    ) -> Dict[str, Any]:
        started_at = time.perf_counter()
        project_id = self.selected_project_id()
        if not project_id or not isinstance(studio_project, dict):
            return {'success': True, 'synced': False, 'message': '동기화할 프로젝트 없음'}
        project_dir = self._project_dir(project_id)
        studio_id = _safe_stem(studio_project.get('project_id'), 'studio')
        manifest = self._read_manifest(project_dir)
        selected_upserts = (
            None
            if replace_all
            else {
                str(value) for value in upsert_layer_ids or [] if str(value)
            }
        )
        selected_deletes = {
            str(value) for value in delete_layer_ids or [] if str(value)
        }
        prepared = []
        for index, layer in enumerate(studio_project.get('layers') or []):
            if not isinstance(layer, dict):
                continue
            raw_layer_id = str(
                layer.get('layer_id') or f'layer_{index + 1}'
            )
            layer_id = _safe_stem(raw_layer_id, f'layer_{index + 1}')
            if (
                selected_upserts is not None
                and raw_layer_id not in selected_upserts
                and layer_id not in selected_upserts
            ):
                continue
            name = f'{studio_id}__{layer_id}.json'
            content = json.dumps(layer, ensure_ascii=False, indent=2) + '\n'
            self._validate_content('layers', name, content)
            prepared.append((name, content, _sha256(content.encode('utf-8'))))
        written = [name for name, _content, _digest in prepared]
        for name, content, _digest in prepared:
            self._atomic_write(project_dir / 'layers' / name, content)
        previous_managed = [
            name
            for name in manifest.get('studio_managed_layers') or []
            if isinstance(name, str) and name == Path(name).name
        ]
        if replace_all:
            managed = list(written)
            removed = [
                name for name in previous_managed if name not in managed
            ]
        else:
            deleted_names = {
                f'{studio_id}__{_safe_stem(layer_id, "layer")}.json'
                for layer_id in selected_deletes
            }
            removed = [
                name for name in previous_managed if name in deleted_names
            ]
            managed = [
                name for name in previous_managed if name not in deleted_names
            ]
            for name in written:
                if name not in managed:
                    managed.append(name)
        for name in removed:
            path = project_dir / 'layers' / name
            if path.is_file() and not path.is_symlink():
                path.unlink()
        prepared_hashes = {
            name: digest for name, _content, digest in prepared
        }
        cached_files = manifest.get('studio_layer_file_cache')
        if not isinstance(cached_files, dict):
            cached_files = {}
        all_layer_hashes = {}
        current_file_cache = {}
        hashed_file_count = 0
        reused_hash_count = 0
        for path in (project_dir / 'layers').iterdir():
            if not _is_user_file(path):
                continue
            stat = path.stat()
            cached = cached_files.get(path.name)
            digest = prepared_hashes.get(path.name)
            if not digest and isinstance(cached, dict):
                try:
                    cache_matches = (
                        int(cached.get('size')) == stat.st_size
                        and int(cached.get('mtime_ns')) == stat.st_mtime_ns
                        and int(cached.get('ctime_ns')) == stat.st_ctime_ns
                        and bool(str(cached.get('sha256') or ''))
                    )
                except (TypeError, ValueError):
                    cache_matches = False
                if cache_matches:
                    digest = str(cached['sha256'])
                    reused_hash_count += 1
            if not digest:
                digest = _sha256_file(path)
                hashed_file_count += 1
            all_layer_hashes[path.name] = digest
            current_file_cache[path.name] = {
                'size': stat.st_size,
                'mtime_ns': stat.st_mtime_ns,
                'ctime_ns': stat.st_ctime_ns,
                'sha256': digest,
            }
        manifest['studio_managed_layers'] = managed
        manifest['studio_managed_layer_sha256'] = {
            name: all_layer_hashes[name]
            for name in managed if name in all_layer_hashes
        }
        manifest['studio_layer_file_cache'] = current_file_cache
        active_layer = manifest['active_files'].get('layers')
        if managed and not active_layer:
            manifest['active_files']['layers'] = managed[0]
        elif active_layer and not (project_dir / 'layers' / active_layer).is_file():
            manifest['active_files']['layers'] = managed[0] if managed else ''
        if replace_all or written or removed:
            self._write_manifest(project_dir, manifest)
        return {
            'success': True,
            'synced': bool(written or removed),
            'project_id': project_id,
            'files': written,
            'deleted_files': removed,
            'managed_files': managed,
            'layer_signature': _studio_layer_signature(all_layer_hashes),
            'elapsed_ms': round((time.perf_counter() - started_at) * 1000, 3),
            'hashed_file_count': hashed_file_count,
            'reused_hash_count': reused_hash_count,
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
        runtime_target = self.runtime.motor_runtime_state()
        applied = bool(
            motor_sha
            and runtime_target.get('valid') is True
            and runtime_target.get('target_project_id') == project_dir.name
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
                1 for path in (project_dir / 'motions').iterdir()
                if _is_user_file(path)
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
            
        for category in PROJECT_CATEGORIES:
            (path / category).mkdir(exist_ok=True)
        (path / 'logs').mkdir(exist_ok=True)
        (path / 'runtime').mkdir(exist_ok=True)
        (path / 'trash').mkdir(exist_ok=True)
            
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
            local_directory(project_dir, category)
        local_directory(project_dir, 'logs')
        local_directory(project_dir, 'runtime')
        local_directory(project_dir, 'trash')
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
                lines = (
                    line.strip()
                    for line in io.StringIO(content)
                    if line.strip()
                )
                header_line = next(lines, '')
                frame_line = next(lines, '')
                if not header_line or not frame_line:
                    raise ValueError('모션 헤더와 프레임 데이터가 필요합니다')
                header = json.loads(header_line)
                if not isinstance(header, dict) or header.get('type') != 'motion_header':
                    raise ValueError('지원하지 않는 모션 파일 헤더입니다')
                if not isinstance(json.loads(frame_line), list):
                    raise ValueError('모션 프레임은 배열이어야 합니다')
                for line in lines:
                    if not isinstance(json.loads(line), list):
                        raise ValueError('모션 프레임은 배열이어야 합니다')
            else:
                if not isinstance(json.loads(content), dict):
                    raise ValueError('레이어 파일 최상위 값은 객체여야 합니다')
        except (json.JSONDecodeError, yaml.YAMLError) as exc:
            raise ValueError(f'{file_name} 파일 형식이 올바르지 않습니다: {exc}') from exc

    @staticmethod
    def _atomic_write(path: Path, content: str) -> None:
        """프로젝트 파일 기록 · 프로세스 간 락 안에서 원자적으로 (§6-24).

        같은 파일을 `motion_mapping_manager`(motion_runtime 프로세스)도 쓴다.
        원자적 기록만으로는 찢긴 읽기만 막을 뿐, 각자 읽고 각자 쓰면 나중
        기록이 앞선 수정을 지운다. 두 쪽이 같은 락 파일에서 만나야 한다.
        """
        with store.locked_update(path):
            store.atomic_write_text(path, content)

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
