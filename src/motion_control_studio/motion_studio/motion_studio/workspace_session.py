"""Workspace, execution-context, and composition-cache ownership."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Dict

from .layer_validation import (
    project_point_curve_frame_mismatches,
    validate_ranges,
)
from .project_store import ProjectStore
from .timeline import layer_conflicts, layer_transition_warnings


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
        if (
            not project_id
            or project_id != Path(project_id).name
            or project_id.startswith('.')
            or '/' in project_id
            or '\\' in project_id
        ):
            raise ValueError('유효한 통합 프로젝트 ID가 필요합니다')
        project_dir = (studio.motion_projects_dir / project_id).resolve()
        if (
            project_dir.parent != studio.motion_projects_dir
            or not (project_dir / 'project.json').is_file()
        ):
            raise ValueError(f'통합 프로젝트를 찾을 수 없습니다: {project_id}')
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
        if path.parent != mapping_dir or not path.is_file():
            raise ValueError('현재 프로젝트의 모션축 설정 파일을 찾을 수 없습니다')
        actual_sha = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual_sha != mapping_sha256:
            raise ValueError('모션축 설정 파일 버전이 실행 컨텍스트와 다릅니다')
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
        context_id = str(payload.get('context_id') or '').strip()
        with studio._lock:
            if (
                not context_id
                or context_id != studio._execution_context.get('context_id')
            ):
                raise ValueError('확인하려는 실행 컨텍스트가 적용된 설정과 다릅니다')
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
            raise ValueError('현재 프로젝트 실행 컨텍스트 적용 대기 중입니다')
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
        studio = self.studio
        with studio._lock:
            studio._operation_machine().cancel()
            studio._store = ProjectStore()
            studio._workspace_project_id = ''
            studio._current_project = None
            self.clear_composition_cache()
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
            'message': '모션 스튜디오 프로젝트 메모리 폐기',
            'project_id': '',
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
        manual_values = studio._manual_initial_values(mapping)
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
            transition_warnings = [
                item for item in cached.get('transition_warnings') or []
                if str(item.get('motion_id') or '') not in selected_motion_ids
            ]
            transition_warnings.extend(layer_transition_warnings(
                project,
                motion_ranges,
                manual_values,
                motion_ids=selected_motion_ids,
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
            transition_warnings = layer_transition_warnings(
                project, motion_ranges, manual_values
            )
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
            'transition_warnings': transition_warnings,
            'range_warnings': range_warnings,
            'point_curve_mismatches': curve_mismatches,
            'conflict_free': (
                not conflicts
                and not transition_warnings
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
