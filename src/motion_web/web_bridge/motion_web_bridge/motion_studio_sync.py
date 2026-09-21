"""Project synchronization and workspace isolation for Motion Studio."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, Optional

from .motion_studio_bridge import STUDIO_REQUEST_TIMEOUT_SEC
from motion_common.paths import NO_PROJECT_SELECTED


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
    def __init__(self, bridge: Any, session: Any, transport: Any) -> None:
        self.bridge = bridge
        self.session = session
        #: 전송 계층을 직접 갖는다 · 노드 껍데기를 되부르지 않는다(§6-15)
        self.transport = transport

    def clear_project_memory(self) -> None:
        self.session.clear_project_memory()

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
                    int(result_generation) == bridge.current_project_generation()
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
            self.session, 'workspace_signatures', None
        )
        if not isinstance(signatures, dict):
            signatures = {}
            self.session.workspace_signatures = signatures
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
        result = self.transport.request('export', payload)
        file_id = str(result.get('file_id') or '').strip()
        if result.get('success') is not False and file_id:
            project_id = bridge.project_repository.selected_project_id()
            # 프로젝트 반영은 ProjectService 가 맡는다 (§6-23) ·
            # 분리 때 이 호출부만 옛 이름으로 남아 있었다 · §6-52
            return bridge._project.sync_file(
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
                'message': NO_PROJECT_SELECTED,
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
            self.session, 'workspace_signatures', {}
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
        with self.session.lock:
            studio_state = str(
                self.session.status.get('state') or 'idle'
            )
        studio_busy = studio_state not in {'idle', 'error'}
        result = self.transport.request(
            'list', {}, timeout_sec=STUDIO_REQUEST_TIMEOUT_SEC)
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
        # **아예 안 붙어 있으면 도는 중이어도 붙인다** · §6-253
        #
        # 「한가할 때만 붙인다」는 도는 중에 프로젝트를 **갈아끼우지** 않으려는
        # 규칙이다 · 그런데 아예 안 붙어 있는 것은 갈아끼우는 일이 아니라
        # 처음 붙이는 일이다 · 막을 이유가 없다.
        #
        # 서비스가 다시 뜨면 스튜디오 노드는 빈손으로 시작한다 · 그때 편집
        # 중이면 `studio_busy` 라서 다시 붙이기를 건너뛰었고, 「다시 붙이고 한
        # 번 더 보내는」 길(§6-109)이 두 번 다 거부당했다 · 사람에게는 한참
        # 기다리다 저장 실패로 보였다.
        #
        #     [WARN] studio command without an attached project: replace_layer_data
        #     [WARN] studio command without an attached project: update_layer
        attached_to_project = (
            str(current_project.get('workspace_project_id') or '') != ''
        )
        if (not studio_busy or not attached_to_project) and not workspace_matches:
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
            result = self.transport.request(
                'open_workspace',
                {
                    'workspace_project_id': project_id,
                    'name': workspace.get('name'),
                    'mapping_file_id': mapping_name,
                    'layers': list(layers_by_id.values()),
                },
                timeout_sec=STUDIO_REQUEST_TIMEOUT_SEC,
            )
            if result.get('success') is not False:
                workspace_signatures[project_id] = {
                    'layers': layer_signature,
                    'motions': motion_signature,
                }
                self.session.workspace_signatures = workspace_signatures
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

    def request_attached(
        self,
        command: str,
        payload: Optional[Dict[str, Any]] = None,
        timeout_sec: float = STUDIO_REQUEST_TIMEOUT_SEC,
    ) -> Dict[str, Any]:
        """스튜디오에 쓰기 · 노드가 프로젝트를 놓쳤으면 다시 붙이고 한 번만 더 · §6-109

        서비스가 다시 뜨면(갱신·재시작) 스튜디오 노드는 빈손으로 시작한다 ·
        그런데 열려 있던 화면은 그것을 모르고 편집·삭제를 보낸다 · 화면에는
        프로젝트가 멀쩡히 보이는데 "프로젝트를 선택하세요" 가 뜬다.

        새로 고치면 되던 이유는 **조회 경로만** 노드에 되물어 보고 어긋나면
        다시 붙이기 때문이다(`prepare`) · 쓰기 경로에는 그 확인이 없었다.

        평소에는 아무 값도 더 들지 않는다 · 붙어 있으면 그대로 한 번에 끝나고,
        놓쳤을 때만 다시 붙인다 · 왜 놓쳤는지는 묻지 않는다.
        """
        result = self.transport.request(command, payload or {}, timeout_sec=timeout_sec)
        if result.get('project_attached') is not False:
            return result
        prepared = self.prepare()
        if prepared.get('success') is False:
            return prepared
        return self.transport.request(command, payload or {}, timeout_sec=timeout_sec)

    def request_prepared(
        self, command: str, payload: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        bridge = self.bridge
        start_generation = None
        if command in {'record', 'play', 'initialize'}:
            with self.session.order_lock:
                self.session.start_generation += 1
                start_generation = self.session.start_generation
        prepared = self.prepare()
        if prepared.get('success') is False:
            return prepared
        if command in {'record', 'play', 'initialize'}:
            conflict = bridge.coordination_execution_blocker()
            if conflict:
                return {
                    'success': False,
                    'message': f'모션 스튜디오 동작 불가: {conflict}',
                }
            blocker = bridge.motor_runtime_control_blocker()
            if blocker:
                return {
                    'success': False,
                    'message': f'모션 스튜디오 동작 불가: {blocker}',
                }
        return self.transport.request(
            command,
            payload or {},
            start_generation=start_generation,
        )

    def import_layer(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        prepared = self.prepare()
        if prepared.get('success') is False:
            return prepared
        result = self.transport.request(
            'import_motion_layer',
            {'motion_file_id': payload.get('motion_file_id')},
            timeout_sec=STUDIO_REQUEST_TIMEOUT_SEC,
        )
        result['unified_project'] = True
        result['workspace_project'] = prepared.get('workspace_project')
        return self.sync_result(result)
