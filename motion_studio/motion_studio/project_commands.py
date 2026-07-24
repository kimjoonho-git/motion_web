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

    def handle(self, command: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        studio = self.studio
        if command == 'status':
            return studio.snapshot()
        if command == 'list':
            with studio._lock:
                current_project = studio._current_project
            return {
                'success': True,
                'projects': studio._store.list_projects(),
                'mappings': studio._store.list_mappings(),
                'motion_files': studio._store.list_motion_files(),
                'project': current_project,
                'status': studio.snapshot(),
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
                studio._set_status_locked('idle', '새 모션 프로젝트를 만들었습니다')
            return {
                'success': True,
                'project': project,
                'status': studio.snapshot(),
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
                project = studio._store.append_motion_file(
                    project, payload.get('motion_file_id')
                )
                studio._current_project = project
                studio._set_status_locked(
                    'idle', '모션 파일을 현재 프로젝트 레이어로 가져왔습니다'
                )
            return studio._project_result(project, '모션 파일 레이어 가져오기 완료')
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
        summary = next(
            (
                item
                for item in studio._store.list_projects()
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
        result = studio._project_result(project, '통합 프로젝트 연결 완료')
        result.update({
            'projects': studio._store.list_projects(),
            'mappings': studio._store.list_mappings(),
            'motion_files': studio._store.list_motion_files(),
        })
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
            project = studio._store.save_project(project)
            studio._current_project = project
        return studio._project_result(project, '프로젝트 저장 완료')
