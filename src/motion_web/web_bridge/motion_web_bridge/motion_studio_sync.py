"""Project synchronization and workspace isolation for Motion Studio."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, Optional


def _project_tree_category_signature(tree: Any, category: str) -> str:
    rows = []
    for folder in tree or []:
        if not isinstance(folder, dict) or folder.get('category') != category:
            continue
        rows.extend(
            (
                str(file_info.get('name') or ''),
                str(file_info.get('sha256') or ''),
            )
            for file_info in folder.get('children') or []
            if isinstance(file_info, dict)
        )
    return hashlib.sha256(
        json.dumps(
            sorted(rows), ensure_ascii=False, separators=(',', ':')
        ).encode('utf-8')
    ).hexdigest()


class MotionStudioSync:
    def __init__(self, bridge: Any) -> None:
        self.bridge = bridge

    def clear_project_memory(self) -> None:
        bridge = self.bridge
        with bridge._motion_studio_lock:
            bridge._motion_studio_results.clear()
            bridge._motion_studio_status = {}
            bridge._motion_studio_workspace_signatures = {}
        with bridge._motion_studio_editor_lock:
            bridge._motion_studio_editor_results.clear()

    def sync_result(self, result: Dict[str, Any]) -> Dict[str, Any]:
        bridge = self.bridge
        repository = getattr(bridge, 'project_repository', None)
        if repository is None or result.get('success') is False:
            return result
        selected_project_id = repository.selected_project_id()
        result_project = (
            result.get('project')
            if isinstance(result.get('project'), dict) else {}
        )
        result_workspace_id = str(
            result_project.get('workspace_project_id') or ''
        )
        if result_workspace_id and result_workspace_id != selected_project_id:
            message = (
                '저장 완료 전에 선택 프로젝트가 변경되어 레이어 파일 동기화를 폐기했습니다'
            )
            result.update({
                'success': False,
                'message': message,
                'project_sync_warning': message,
            })
            result.pop('project', None)
            return result
        result_generation = result.get('project_generation')
        if result_generation is not None:
            try:
                generation_matches = (
                    int(result_generation) == bridge._current_project_generation()
                )
            except (TypeError, ValueError):
                generation_matches = False
            if not generation_matches:
                message = (
                    '저장 완료 전에 프로젝트 세대가 변경되어 레이어 파일 동기화를 폐기했습니다'
                )
                result.update({
                    'success': False,
                    'message': message,
                    'project_sync_warning': message,
                })
                result.pop('project', None)
                return result
        layer_sync = result.get('layer_sync')
        try:
            if isinstance(layer_sync, dict):
                sync = repository.sync_studio_layers(
                    result.get('project'),
                    upsert_layer_ids=layer_sync.get('upsert_layer_ids') or [],
                    delete_layer_ids=layer_sync.get('delete_layer_ids') or [],
                    replace_all=False,
                )
            else:
                sync = repository.sync_studio_layers(result.get('project'))
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            result['project_sync_warning'] = str(exc)
            return result
        signatures = getattr(
            bridge, '_motion_studio_workspace_signatures', None
        )
        if not isinstance(signatures, dict):
            signatures = {}
            bridge._motion_studio_workspace_signatures = signatures
        current = dict(signatures.get(selected_project_id) or {})
        current['layers'] = str(sync.get('layer_signature') or '')
        signatures[selected_project_id] = current
        result['project_sync'] = sync
        if isinstance(layer_sync, dict) and result_project:
            upsert_ids = {
                str(value)
                for value in layer_sync.get('upsert_layer_ids') or []
                if str(value)
            }
            layers = [
                layer for layer in result_project.get('layers') or []
                if isinstance(layer, dict)
            ]
            metadata = {
                key: value
                for key, value in result_project.items()
                if key != 'layers'
            }
            result['project_patch'] = {
                'metadata': metadata,
                'layer_order': [
                    str(layer.get('layer_id') or '') for layer in layers
                ],
                'upsert_layers': [
                    layer for layer in layers
                    if str(layer.get('layer_id') or '') in upsert_ids
                ],
                'delete_layer_ids': [
                    str(value)
                    for value in layer_sync.get('delete_layer_ids') or []
                    if str(value)
                ],
            }
            result.pop('project', None)
        return result

    def export(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        bridge = self.bridge
        result = bridge.request_motion_studio('export', payload)
        file_id = str(result.get('file_id') or '').strip()
        if result.get('success') is not False and file_id:
            project_id = bridge.project_repository.selected_project_id()
            return bridge._sync_project_file(
                result,
                'motions',
                bridge.project_repository.export_path(
                    project_id, 'motions', file_id
                ),
            )
        return result

    def prepare(self) -> Dict[str, Any]:
        bridge = self.bridge
        repository = bridge.project_repository
        project_id = repository.selected_project_id()
        if not project_id:
            return {
                'success': False,
                'message': '왼쪽에서 통합 프로젝트를 먼저 선택하세요',
                'unified_project': True,
                'workspace_project': None,
                'projects': [],
                'project': None,
                'mappings': [],
                'motion_files': [],
                'status': {'state': 'idle', 'message': '통합 프로젝트 미선택'},
            }
        detail = repository.get_project(project_id)
        workspace = detail['project']
        layer_signature = _project_tree_category_signature(
            detail.get('tree'), 'layers'
        )
        motion_signature = _project_tree_category_signature(
            detail.get('tree'), 'motions'
        )
        workspace_signatures = getattr(
            bridge, '_motion_studio_workspace_signatures', {}
        )
        if not isinstance(workspace_signatures, dict):
            workspace_signatures = {}
        cached_signatures = workspace_signatures.get(project_id) or {}
        active = workspace.get('active_files') or {}
        mapping_name = str(active.get('motion_axis_matching') or '')
        if not mapping_name:
            return {
                'success': False,
                'message': '현재 프로젝트의 모션축 설정 파일을 선택하세요',
                'unified_project': True,
                'workspace_project': workspace,
                'projects': [],
                'project': None,
                'mappings': [],
                'motion_files': [],
                'status': {'state': 'idle', 'message': '모션축 설정 미선택'},
            }
        published_motion_names = []
        mapping_sha256 = ''
        for folder in detail.get('tree') or []:
            category = str(folder.get('category') or '')
            for file_info in folder.get('children') or []:
                file_name = str(file_info.get('name') or '')
                if category == 'motions':
                    published_motion_names.append(file_name)
                elif (
                    category == 'motion_axis_matching'
                    and file_name == mapping_name
                ):
                    mapping_sha256 = str(file_info.get('sha256') or '')
        with bridge._motion_studio_lock:
            studio_state = str(
                bridge._motion_studio_status.get('state') or 'idle'
            )
        studio_busy = studio_state not in {'idle', 'error'}
        result = bridge.request_motion_studio('list', {}, timeout_sec=8.0)
        current_project = (
            result.get('project')
            if isinstance(result.get('project'), dict) else {}
        )
        workspace_matches = (
            str(current_project.get('workspace_project_id') or '') == project_id
            and str(current_project.get('mapping_file_id') or '') == mapping_name
            and bool(mapping_sha256)
            and str(current_project.get('mapping_sha256') or '') == mapping_sha256
            and str(cached_signatures.get('layers') or '') == layer_signature
            and str(cached_signatures.get('motions') or '') == motion_signature
            and isinstance(result.get('composition'), dict)
            and 'conflicts' in result.get('composition')
        )
        if not studio_busy and not workspace_matches:
            layers_by_id: Dict[str, Dict[str, Any]] = {}
            for folder in detail.get('tree') or []:
                if folder.get('category') != 'layers':
                    continue
                for file_info in folder.get('children') or []:
                    loaded = repository.read_file(
                        project_id, 'layers', file_info.get('name')
                    )
                    layer = json.loads(loaded['content'])
                    if not isinstance(layer, dict):
                        continue
                    layer_id = str(
                        layer.get('layer_id') or file_info.get('name')
                    )
                    layers_by_id[layer_id] = layer
            result = bridge.request_motion_studio(
                'open_workspace',
                {
                    'workspace_project_id': project_id,
                    'name': workspace.get('name'),
                    'mapping_file_id': mapping_name,
                    'layers': list(layers_by_id.values()),
                },
                timeout_sec=8.0,
            )
            if result.get('success') is not False:
                workspace_signatures[project_id] = {
                    'layers': layer_signature,
                    'motions': motion_signature,
                }
                bridge._motion_studio_workspace_signatures = workspace_signatures
        result['unified_project'] = True
        result['workspace_project'] = workspace
        result['mappings'] = [
            item for item in result.get('mappings') or []
            if item.get('file_id') == mapping_name
        ]
        result['motion_files'] = [
            item for item in result.get('motion_files') or []
            if item.get('file_id') in published_motion_names
        ]
        return result

    def request_prepared(
        self, command: str, payload: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        bridge = self.bridge
        start_generation = None
        if command in {'record', 'play', 'initialize'}:
            with bridge._motion_studio_start_order_lock():
                bridge._motion_studio_start_generation += 1
                start_generation = bridge._motion_studio_start_generation
        prepared = bridge.prepare_unified_motion_studio()
        if prepared.get('success') is False:
            return prepared
        if command in {'record', 'play', 'initialize'}:
            blocker = bridge._motor_runtime_control_blocker()
            if blocker:
                return {
                    'success': False,
                    'message': f'모션 스튜디오 동작 불가: {blocker}',
                }
        return bridge.request_motion_studio(
            command,
            payload or {},
            start_generation=start_generation,
        )

    def import_layer(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        bridge = self.bridge
        prepared = bridge.prepare_unified_motion_studio()
        if prepared.get('success') is False:
            return prepared
        result = bridge.request_motion_studio(
            'import_motion_layer',
            {'motion_file_id': payload.get('motion_file_id')},
            timeout_sec=8.0,
        )
        result['unified_project'] = True
        result['workspace_project'] = prepared.get('workspace_project')
        return bridge.sync_motion_studio_result(result)
