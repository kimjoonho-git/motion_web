"""Workspace, execution-context, and composition-cache ownership."""

from __future__ import annotations

from motion_common.execution_context import MAPPING_FILE_MISSING, confirm_context_id, verify_mapping_fingerprint
from motion_common.paths import project_dir_for

import hashlib
from pathlib import Path
from typing import Any, Dict

from .layer_validation import (
    project_point_curve_frame_mismatches,
    validate_ranges,
)
from .timeline import layer_conflicts


class ExecutionContextNotReady(ValueError):
    """모터를 움직여도 되는지 아직 확인되지 않았다 · §6-258

    막은 쪽은 이 노드가 아니라 브릿지의 실행 컨텍스트다 · 이 노드는 **왜**
    아닌지 모른다(모터가 꺼져 있는지, 설정이 어긋났는지) · 그래서 표시만
    달아 보내고, 이유는 아는 쪽이 붙인다.
    """


class StudioWorkspaceSession:
    def __init__(self, studio: Any) -> None:
        self.studio = studio

    def clear_composition_cache(self) -> None:
        self.studio._composition_cache_project_id = ''
        self.studio._composition_cache = {}

    def select(self, payload: Dict[str, Any]) -> None:
        studio = self.studio
        project_id = str(
            payload.get('project_id') or payload.get('workspace_project_id') or ''
        ).strip()
        project_dir = project_dir_for(studio.motion_projects_dir, project_id)
        if project_id == studio._workspace_project_id:
            return
        with studio._lock:
            studio._require_idle_locked()
            studio._store.use_workspace(project_dir)
            studio._workspace_project_id = project_id
            studio._current_project = None
            self.clear_composition_cache()
            studio._workspace_catalog_cache = None

    def apply_execution_context(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        studio = self.studio
        self.select(payload)
        context_id = str(payload.get('context_id') or '').strip()
        mapping_file_id = str(payload.get('mapping_file_id') or '').strip()
        mapping_sha256 = str(payload.get('mapping_sha256') or '').strip()
        if not context_id or not mapping_file_id or not mapping_sha256:
            raise ValueError('실행 컨텍스트 ID와 모션축 설정 버전이 필요합니다')
        mapping_dir = (
            studio.motion_projects_dir
            / studio._workspace_project_id
            / 'motion_axis_matching'
        )
        path = mapping_dir / mapping_file_id
        if path.parent != mapping_dir:
            raise ValueError(MAPPING_FILE_MISSING)
        actual_sha = verify_mapping_fingerprint(path, mapping_sha256)
        with studio._lock:
            next_context = {
                'context_id': context_id,
                'project_id': studio._workspace_project_id,
                'project_generation': int(payload.get('project_generation') or 0),
                'mapping_file_id': mapping_file_id,
                'mapping_sha256': actual_sha,
            }
            same_context = studio._execution_context == next_context
            studio._execution_context = next_context
            if not same_context:
                studio._execution_context_ready = False
        return {
            'success': True,
            'message': '모션 스튜디오 실행 컨텍스트 적용 확인 완료',
            **studio._execution_context,
            'status': studio.snapshot(),
        }

    def confirm_execution_context(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        studio = self.studio
        with studio._lock:
            confirm_context_id(studio._execution_context, payload)
            studio._execution_context_ready = True
            confirmed_context = dict(studio._execution_context)
        return {
            'success': True,
            'message': '모션 스튜디오 사용 허용',
            **confirmed_context,
            'status': studio.snapshot(),
        }

    def require_execution_context(self) -> None:
        studio = self.studio
        with studio._lock:
            ready = studio._execution_context_ready
            project_id = studio._execution_context.get('project_id')
        if (
            not ready
            or project_id != studio._workspace_project_id
            or self.context_generation() != int(studio._project_generation or 0)
        ):
            raise ExecutionContextNotReady(
                '현재 프로젝트 실행 컨텍스트 적용 대기 중입니다'
            )
        path = (
            studio.motion_projects_dir
            / project_id
            / 'motion_axis_matching'
            / str(studio._execution_context.get('mapping_file_id') or '')
        )
        if (
            not path.is_file()
            or hashlib.sha256(path.read_bytes()).hexdigest()
            != studio._execution_context.get('mapping_sha256')
        ):
            with studio._lock:
                studio._execution_context_ready = False
            raise ValueError(
                '모션축 설정 파일이 변경되어 실행 컨텍스트 재적용이 필요합니다'
            )

    def invalidate(self) -> Dict[str, Any]:
        """**실행을 막는다 · 열어 둔 파일까지 덮지는 않는다** · §6-257

        여기는 사실을 두 개 들고 있었다.

            어느 프로젝트의 파일인가   `_current_project`   레이어 목록·편집·저장
            실행해도 되는가            `_execution_context`  재생·녹화·초기이동

        `invalidate_context` 의 뜻은 **두 번째**다 · 「지금 이 프로젝트로
        모터를 움직이면 안 된다」 · 그런데 첫 번째까지 같이 지웠다.

        브릿지는 모터가 준비되지 않으면 **1초마다** 이것을 보낸다 · 모터가
        꺼져 있으면 열어 둔 레이어가 1초마다 사라져서, 저장이 그 창과
        경주했다 · 사람에게는 「먼저 왼쪽에서 통합 프로젝트를 선택하세요」로
        보였다 · 왼쪽은 멀쩡한데.

        파일을 여는 쪽은 `select()` 가 따로 본다 · 통로가 모든 요청에
        `project_id` 를 넣어 주므로, 프로젝트가 바뀌면 거기서 지워진다 ·
        여기서 또 지울 이유가 없다.

        **합성 결과 캐시도 같은 이유로 지우지 않는다** · §6-260 · 그것은 열어
        둔 레이어에서 나온 값이고, 브릿지는 그 캐시가 차 있는지로 「이 노드가
        붙어 있나」를 판정한다 · 매초 지우면 판정이 늘 「아니오」가 되어
        **조회할 때마다 프로젝트를 통째로 다시 붙였다** · 실측으로 읽기만 하는
        조회 한 번이 4.9초였고 레이어 파일 8.4MB 가 매번 다시 쓰였다 ·
        레이어가 바뀌면 `composition()` 이 그때 다시 계산한다.
        """
        studio = self.studio
        with studio._lock:
            studio._operation_machine().cancel()
            studio._workspace_catalog_cache = None
            studio._execution_context = {}
            studio._execution_context_ready = False
            studio._midi_state = {}
            studio._motion_run_status = {}
            studio._run_results.clear()
            studio._midi_results.clear()
            studio._record_started = 0.0
            studio._record_frames = []
            studio._record_eligible_motion_ids = set()
            studio._recorded_motion_ids = set()
            studio._status = studio._empty_status()
        return {
            'success': True,
            'message': '모션 스튜디오 실행 대기 · 열어 둔 레이어는 유지',
            'project_id': studio._workspace_project_id,
            'context_id': '',
            'status': studio.snapshot(),
        }

    def composition(
        self,
        project: Dict[str, Any],
        mapping: Dict[str, Any],
        *,
        affected_motion_ids: set[str] | None = None,
        affected_layer_ids: set[str] | None = None,
    ) -> Dict[str, Any]:
        studio = self.studio
        project_id = str(project.get('project_id') or '')
        motion_ranges = studio._motion_ranges(mapping)
        selected_motion_ids = {
            str(value) for value in affected_motion_ids or set() if str(value)
        }
        selected_layer_ids = {
            str(value) for value in affected_layer_ids or set() if str(value)
        }
        cached = getattr(studio, '_composition_cache', {})
        cache_matches = (
            getattr(studio, '_composition_cache_project_id', '') == project_id
            and isinstance(cached, dict)
            and bool(cached)
        )
        incremental = cache_matches and (
            bool(selected_motion_ids) or bool(selected_layer_ids)
        )
        if incremental:
            conflicts = [
                item for item in cached.get('conflicts') or []
                if str(item.get('motion_id') or '') not in selected_motion_ids
            ]
            conflicts.extend(layer_conflicts(
                project, motion_ids=selected_motion_ids
            ))
            range_warnings = [
                item for item in cached.get('range_warnings') or []
                if str(item.get('layer_id') or '') not in selected_layer_ids
            ]
            curve_mismatches = [
                item for item in cached.get('point_curve_mismatches') or []
                if str(item.get('layer_id') or '') not in selected_layer_ids
            ]
            target_layers = [
                layer for layer in project.get('layers') or []
                if (
                    isinstance(layer, dict)
                    and str(layer.get('layer_id') or '') in selected_layer_ids
                )
            ]
        else:
            conflicts = layer_conflicts(project)
            range_warnings = []
            curve_mismatches = []
            target_layers = [
                layer for layer in project.get('layers') or []
                if isinstance(layer, dict)
            ]
        range_warnings.extend(
            {
                **issue,
                'layer_id': str(layer.get('layer_id') or ''),
                'layer_name': str(layer.get('name') or ''),
            }
            for layer in target_layers
            for issue in validate_ranges(layer, motion_ranges)
        )
        curve_mismatches.extend(project_point_curve_frame_mismatches(
            {'layers': target_layers}
        ))
        composition = {
            'conflicts': conflicts,
            'range_warnings': range_warnings,
            'point_curve_mismatches': curve_mismatches,
            'conflict_free': (
                not conflicts
                and not curve_mismatches
            ),
        }
        studio._composition_cache_project_id = project_id
        studio._composition_cache = composition
        return composition

    def project_result(
        self,
        project: Dict[str, Any],
        message: str = '완료',
        *,
        affected_motion_ids: set[str] | None = None,
        affected_layer_ids: set[str] | None = None,
    ) -> Dict[str, Any]:
        studio = self.studio
        mapping = studio._store.mapping_check(project)
        composition = self.composition(
            project,
            mapping,
            affected_motion_ids=affected_motion_ids,
            affected_layer_ids=affected_layer_ids,
        )
        return {
            'success': True,
            'message': message,
            'project': project,
            'mapping': mapping,
            'status': studio.snapshot(),
            'composition': composition,
        }

    @staticmethod
    def require_point_curve_consistency(
        project: Dict[str, Any], action: str
    ) -> None:
        mismatches = project_point_curve_frame_mismatches(project)
        if not mismatches:
            return
        first = mismatches[0]
        raise ValueError(
            f'{action} 차단: {first["layer_name"]}의 {first["motion_id"]} '
            '포인트 곡선과 20ms 프레임이 다릅니다. '
            '포인트 기준으로 다시 계산하세요'
        )

    def context_generation(self) -> int:
        try:
            return int(self.studio._execution_context.get('project_generation') or 0)
        except (AttributeError, TypeError, ValueError):
            return 0

    def response_generation_matches(self, payload: Any) -> bool:
        if not isinstance(payload, dict):
            return False
        try:
            return int(payload.get('project_generation')) == self.context_generation()
        except (TypeError, ValueError):
            return False
