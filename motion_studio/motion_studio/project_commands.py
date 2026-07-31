"""Project and layer command handlers for Motion Studio.

The ROS node remains the public boundary.  This handler owns only editable
project commands so transport, execution context, and motor operations can
evolve independently.
"""

from __future__ import annotations

from typing import Any, Dict


class StudioProjectCommands:
    COMMANDS = {
        'status',
        'list',
        'open_workspace',
        'create',
        'load',
        'import_motion_file',
        'import_motion_layer',
        'save',
        'update_layer',
        'create_layer',
        'replace_layer_data',
        'delete_layer',
        'duplicate_layer',
        'commit_merged_layer',
        'delete',
    }

    def __init__(self, studio: Any) -> None:
        self.studio = studio

    def handles(self, command: str) -> bool:
        return command in self.COMMANDS

    def _catalog(self, *, refresh: bool = False) -> Dict[str, Any]:
        studio = self.studio
        cached = getattr(studio, '_workspace_catalog_cache', None)
        if refresh or not isinstance(cached, dict):
            cached = {
                'projects': studio._store.list_projects(),
                'mappings': studio._store.list_mappings(),
                'motion_files': studio._store.list_motion_files(),
            }
            studio._workspace_catalog_cache = cached
        return {
            key: list(cached.get(key) or [])
            for key in ('projects', 'mappings', 'motion_files')
        }

    def handle(self, command: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        studio = self.studio
        if command == 'status':
            return studio.snapshot()
        if command == 'list':
            with studio._lock:
                current_project = studio._current_project
            catalog = self._catalog()
            return {
                'success': True,
                **catalog,
                'project': current_project,
                'status': studio.snapshot(),
                'composition': dict(
                    getattr(studio, '_composition_cache', {}) or {}
                ),
            }
        if command == 'open_workspace':
            return self._open_workspace(payload)
        if command == 'create':
            with studio._lock:
                studio._require_idle_locked()
            project = studio._store.create_project(
                payload.get('name'), payload.get('mapping_file_id')
            )
            with studio._lock:
                studio._current_project = project
                studio._workspace_catalog_cache = None
                studio._set_status_locked('idle', '새 모션 프로젝트를 만들었습니다')
            return {
                'success': True,
                'project': project,
                'status': studio.snapshot(),
                'layer_sync': {
                    'upsert_layer_ids': [],
                    'delete_layer_ids': [],
                },
            }
        if command == 'load':
            project = studio._store.load_project(payload.get('project_id'))
            with studio._lock:
                studio._require_idle_locked()
                studio._current_project = project
                studio._set_status_locked('idle', '모션 프로젝트를 불러왔습니다')
            return studio._project_result(project)
        if command == 'import_motion_file':
            with studio._lock:
                studio._require_idle_locked()
            project = studio._store.import_motion_file(
                payload.get('motion_file_id'),
                payload.get('mapping_file_id'),
                payload.get('name'),
            )
            with studio._lock:
                studio._current_project = project
                studio._workspace_catalog_cache = None
                studio._set_status_locked(
                    'idle', '모션 파일을 단일 레이어 프로젝트로 가져왔습니다'
                )
            return studio._project_result(
                project, '모션 파일 가져오기 완료 · 단일 레이어로 변환했습니다'
            )
        if command == 'import_motion_layer':
            with studio._lock:
                studio._require_idle_locked()
                project = studio._require_project_locked()
                previous_layer_ids = {
                    str(layer.get('layer_id') or '')
                    for layer in project.get('layers') or []
                    if isinstance(layer, dict)
                }
                project = studio._store.append_motion_file(
                    project, payload.get('motion_file_id')
                )
                studio._current_project = project
                studio._set_status_locked(
                    'idle', '모션 파일을 현재 프로젝트 레이어로 가져왔습니다'
                )
            added_layer_ids = [
                str(layer.get('layer_id') or '')
                for layer in project.get('layers') or []
                if (
                    isinstance(layer, dict)
                    and str(layer.get('layer_id') or '') not in previous_layer_ids
                )
            ]
            affected_motion_ids = {
                str(motion_id)
                for layer in project.get('layers') or []
                if (
                    isinstance(layer, dict)
                    and str(layer.get('layer_id') or '') in added_layer_ids
                )
                for frame in layer.get('frames') or []
                if isinstance(frame, dict)
                for motion_id in (frame.get('values') or {})
            }
            result = studio._project_result(
                project,
                '모션 파일 레이어 가져오기 완료',
                affected_motion_ids=affected_motion_ids,
                affected_layer_ids=set(added_layer_ids),
            )
            result['layer_sync'] = {
                'upsert_layer_ids': added_layer_ids,
                'delete_layer_ids': [],
            }
            return result
        if command == 'save':
            return self._save(payload)

        layer_handlers = {
            'update_layer': studio._update_layer,
            'create_layer': studio._create_layer,
            'replace_layer_data': studio._replace_layer_data,
            'delete_layer': studio._delete_layer,
            'duplicate_layer': studio._duplicate_layer,
            'commit_merged_layer': studio._commit_merged_layer,
        }
        if command in layer_handlers:
            return layer_handlers[command](payload)
        if command == 'delete':
            with studio._lock:
                studio._require_idle_locked()
                project = studio._require_project_locked()
                studio._store.delete_project(project['project_id'])
                studio._current_project = None
                studio._workspace_catalog_cache = None
                studio._set_status_locked('idle', '프로젝트를 삭제했습니다')
            return {'success': True, 'message': '프로젝트 삭제 완료'}
        raise ValueError(f'지원하지 않는 모션 스튜디오 프로젝트 명령: {command}')

    def _open_workspace(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        studio = self.studio
        with studio._lock:
            studio._require_idle_locked()
        workspace_project_id = str(
            payload.get('workspace_project_id') or ''
        ).strip()
        if not workspace_project_id:
            raise ValueError('통합 프로젝트 ID가 필요합니다')
        mapping = studio._store.inspect_mapping(payload.get('mapping_file_id'))
        projects = studio._store.list_projects()
        summary = next(
            (
                item
                for item in projects
                if item.get('workspace_project_id') == workspace_project_id
            ),
            None,
        )
        if summary:
            project = studio._store.load_project(summary['project_id'])
        else:
            project = studio._store.create_project(
                payload.get('name'), mapping['file_id']
            )
        project['workspace_project_id'] = workspace_project_id
        project['name'] = (
            str(payload.get('name') or project['name']).strip() or project['name']
        )
        project['mapping_file_id'] = mapping['file_id']
        project['mapping_sha256'] = mapping['sha256']
        project['layers'] = [
            dict(layer)
            for layer in payload.get('layers') or []
            if isinstance(layer, dict)
        ]
        project = studio._store.save_project(project)
        with studio._lock:
            studio._current_project = project
            studio._set_status_locked(
                'idle', '통합 프로젝트를 모션 스튜디오에 연결했습니다'
            )
        current_summary = studio._store.summary(project)
        catalog_projects = [
            current_summary
            if item.get('project_id') == project.get('project_id')
            else item
            for item in projects
        ]
        if not any(
            item.get('project_id') == project.get('project_id')
            for item in catalog_projects
        ):
            catalog_projects.insert(0, current_summary)
        studio._workspace_catalog_cache = {
            'projects': catalog_projects,
            'mappings': studio._store.list_mappings(),
            'motion_files': studio._store.list_motion_files(),
        }
        result = studio._project_result(project, '통합 프로젝트 연결 완료')
        result.update(self._catalog())
        return result

    def _save(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        studio = self.studio
        with studio._lock:
            studio._require_idle_locked()
            project = studio._require_project_locked()
            if 'name' in payload:
                project['name'] = (
                    str(payload.get('name') or '').strip() or project['name']
                )
            if 'transition_safety_level' in payload:
                try:
                    level = int(payload.get('transition_safety_level'))
                except (TypeError, ValueError) as exc:
                    raise ValueError('급변 기준 단계는 1~10이어야 합니다') from exc
                if level < 1 or level > 10:
                    raise ValueError('급변 기준 단계는 1~10이어야 합니다')
                project['transition_safety_level'] = level
            project = studio._store.save_project(
                project, upsert_layer_ids=[]
            )
            studio._current_project = project
            studio._workspace_catalog_cache = None
        result = studio._project_result(project, '프로젝트 저장 완료')
        result['layer_sync'] = {
            'upsert_layer_ids': [],
            'delete_layer_ids': [],
        }
        return result
