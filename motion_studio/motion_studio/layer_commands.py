"""Layer mutation service used by the Motion Studio ROS boundary."""

from __future__ import annotations

import copy
import re
import time
import uuid
from typing import Any, Dict, List

from .layer_editor import merge_layers
from .layer_validation import point_curve_frame_mismatches, validate_ranges
from .motion_model import layer_motion_ids, normalize_layer


def next_numbered_layer_name(layers: List[Dict[str, Any]], label: str) -> str:
    pattern = re.compile(rf'^{re.escape(label)}\s+(\d+)$')
    numbers = []
    for layer in layers:
        if not isinstance(layer, dict):
            continue
        matched = pattern.fullmatch(str(layer.get('name') or '').strip())
        if matched:
            numbers.append(int(matched.group(1)))
    return f'{label} {max(numbers, default=0) + 1}'


class StudioLayerCommands:
    def __init__(self, studio: Any) -> None:
        self.studio = studio

    @staticmethod
    def _sync_result(result: Dict[str, Any], upserts=(), deletes=()) -> Dict[str, Any]:
        result['layer_sync'] = {
            'upsert_layer_ids': list(upserts),
            'delete_layer_ids': list(deletes),
        }
        return result

    def update(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        studio = self.studio
        layer_id = str(payload.get('layer_id') or '')
        with studio._lock:
            studio._require_idle_locked()
            project = studio._require_project_locked()
            layer = next((item for item in project.get('layers') or []
                          if item.get('layer_id') == layer_id), None)
            if layer is None:
                raise ValueError('레이어를 찾을 수 없습니다')
            if layer.get('locked') and payload.get('locked') is not False and any(
                key in payload for key in ('enabled', 'name')
            ):
                raise ValueError('잠긴 레이어는 재생 선택 상태나 이름을 변경할 수 없습니다')
            if 'enabled' in payload:
                layer['enabled'] = bool(payload['enabled'])
            if 'locked' in payload:
                layer['locked'] = bool(payload['locked'])
            if 'name' in payload:
                layer['name'] = str(payload.get('name') or '').strip()[:40] or layer['name']
            affected_motion_ids = (
                layer_motion_ids(layer)
                if any(key in payload for key in ('enabled', 'name')) else set()
            )
            studio._current_project = studio._store.save_project(
                project, upsert_layer_ids=[layer_id]
            )
            project = studio._current_project
        return self._sync_result(studio._project_result(
            project,
            '레이어 설정 저장 완료',
            affected_motion_ids=affected_motion_ids,
            affected_layer_ids={layer_id},
        ), [layer_id])

    def create(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        studio = self.studio
        with studio._lock:
            studio._require_idle_locked()
            project = studio._require_project_locked()
            layers = project.setdefault('layers', [])
            requested_name = str(payload.get('name') or '').strip()[:40]
            layer = normalize_layer({
                'layer_id': f'layer_{uuid.uuid4().hex[:8]}',
                'name': requested_name or next_numbered_layer_name(layers, '새 레이어'),
                'enabled': False,
                'locked': False,
                'created_at': time.time(),
                'edit_revision': 0,
                'point_curves': [],
                'frames': [],
            }, len(layers))
            layers.append(layer)
            studio._current_project = studio._store.save_project(
                project, upsert_layer_ids=[layer['layer_id']]
            )
            project = studio._current_project
        result = studio._project_result(
            project, '빈 레이어를 생성했습니다 · 편집 후 재생 선택하세요',
            affected_layer_ids={layer['layer_id']},
        )
        result['layer_id'] = layer['layer_id']
        return self._sync_result(result, [layer['layer_id']])

    def replace_data(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        studio = self.studio
        layer_id = str(payload.get('layer_id') or '')
        replacement = payload.get('layer')
        if not isinstance(replacement, dict):
            raise ValueError('저장할 레이어 데이터가 필요합니다')
        with studio._lock:
            studio._require_idle_locked()
            project = studio._require_project_locked()
            index = next((
                order for order, item in enumerate(project.get('layers') or [])
                if str(item.get('layer_id') or '') == layer_id
            ), None)
            if index is None:
                raise ValueError('레이어를 찾을 수 없습니다')
            original = project['layers'][index]
            if original.get('locked'):
                raise ValueError('잠긴 레이어는 편집할 수 없습니다')
            try:
                original_revision = int(payload.get('original_revision') or 0)
            except (TypeError, ValueError) as exc:
                raise ValueError('원본 레이어 편집 버전이 올바르지 않습니다') from exc
            if original_revision != int(original.get('edit_revision') or 0):
                raise ValueError('편집 중 원본 레이어가 변경되었습니다. 편집 창을 다시 열어 작업하세요')
            original_motion_ids = layer_motion_ids(original)
            updated = dict(replacement)
            updated.update({
                'layer_id': layer_id,
                'enabled': original.get('enabled') is not False,
                'locked': False,
                'created_at': original.get('created_at'),
            })
            mapping = studio._store.mapping_check(project)
            available_ids = set(mapping.get('motion_ids') or [])
            edited_ids = {
                str(motion_id)
                for frame in updated.get('frames') or []
                if isinstance(frame, dict)
                for motion_id in (frame.get('values') or {})
            }
            missing = sorted(edited_ids - available_ids)
            if missing:
                raise ValueError('모션축 설정에 없는 Motion ID: ' + ', '.join(missing))
            range_issues = validate_ranges(updated, studio._motion_ranges(mapping))
            curve_mismatches = point_curve_frame_mismatches(updated)
            if curve_mismatches:
                raise ValueError(
                    f"{curve_mismatches[0]['motion_id']} 포인트 곡선과 20ms 프레임이 다릅니다"
                )
            project['layers'][index] = updated
            studio._current_project = studio._store.save_project(
                project, upsert_layer_ids=[layer_id]
            )
            project = studio._current_project
        result = studio._project_result(
            project,
            '편집한 레이어를 저장했습니다',
            affected_motion_ids=original_motion_ids | layer_motion_ids(updated),
            affected_layer_ids={layer_id},
        )
        result['range_warnings'] = range_issues
        return self._sync_result(result, [layer_id])

    def delete(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        studio = self.studio
        layer_id = str(payload.get('layer_id') or '')
        with studio._lock:
            studio._require_idle_locked()
            project = studio._require_project_locked()
            layer = next((item for item in project.get('layers') or []
                          if str(item.get('layer_id') or '') == layer_id), None)
            if layer is None:
                raise ValueError('레이어를 찾을 수 없습니다')
            if layer.get('locked'):
                raise ValueError('잠긴 레이어는 삭제할 수 없습니다')
            affected_motion_ids = layer_motion_ids(layer)
            project['layers'] = [
                item for item in project.get('layers') or []
                if str(item.get('layer_id') or '') != layer_id
            ]
            studio._current_project = studio._store.save_project(
                project, upsert_layer_ids=[], delete_layer_ids=[layer_id]
            )
            project = studio._current_project
        return self._sync_result(studio._project_result(
            project,
            '레이어를 삭제했습니다',
            affected_motion_ids=affected_motion_ids,
            affected_layer_ids={layer_id},
        ), deletes=[layer_id])

    def duplicate(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        studio = self.studio
        layer_id = str(payload.get('layer_id') or '')
        with studio._lock:
            studio._require_idle_locked()
            project = studio._require_project_locked()
            source = next((item for item in project.get('layers') or []
                           if str(item.get('layer_id') or '') == layer_id), None)
            if source is None:
                raise ValueError('복사할 레이어를 찾을 수 없습니다')
            duplicate = copy.deepcopy(source)
            duplicate.update({
                'layer_id': f'layer_{uuid.uuid4().hex[:8]}',
                'name': str(payload.get('name') or f"{source.get('name') or '레이어'} 복사본").strip()[:40] or '레이어 복사본',
                'enabled': False,
                'locked': False,
                'created_at': time.time(),
                'copied_from_layer_id': layer_id,
                'edit_revision': 0,
            })
            for curve in duplicate.get('point_curves') or []:
                curve['curve_id'] = f'curve_{uuid.uuid4().hex[:8]}'
                for point in curve.get('points') or []:
                    point['point_id'] = f'point_{uuid.uuid4().hex[:8]}'
            project.setdefault('layers', []).append(normalize_layer(duplicate))
            studio._current_project = studio._store.save_project(
                project, upsert_layer_ids=[duplicate['layer_id']]
            )
            project = studio._current_project
        result = studio._project_result(
            project,
            '레이어를 독립 복사했습니다 · 재생 미선택 상태입니다',
            affected_motion_ids=layer_motion_ids(duplicate),
            affected_layer_ids={duplicate['layer_id']},
        )
        result['layer_id'] = duplicate['layer_id']
        return self._sync_result(result, [duplicate['layer_id']])

    def commit_merged(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        studio = self.studio
        source_ids = {str(value) for value in payload.get('source_layer_ids') or [] if str(value)}
        if len(source_ids) < 2:
            raise ValueError('합칠 원본 레이어 정보가 필요합니다')
        with studio._lock:
            studio._require_idle_locked()
            project = studio._require_project_locked()
            sources = [item for item in project.get('layers') or []
                       if str(item.get('layer_id') or '') in source_ids]
            if len(sources) != len(source_ids):
                raise ValueError('합칠 원본 레이어를 찾을 수 없습니다')
            if any(item.get('locked') for item in sources):
                raise ValueError('잠긴 레이어는 합치기에 사용할 수 없습니다')
            inconsistent = [str(item.get('name') or item.get('layer_id') or '')
                            for item in sources if point_curve_frame_mismatches(item)]
            if inconsistent:
                raise ValueError(
                    '포인트와 20ms 프레임이 다른 레이어를 먼저 다시 계산하세요: '
                    + ', '.join(inconsistent)
                )
            if not isinstance(payload.get('layer'), dict):
                raise ValueError('계산 노드가 만든 합성 미리보기 데이터가 필요합니다')
            expected_revisions = payload.get('source_revisions') or {}
            for item in sources:
                item_id = str(item.get('layer_id') or '')
                try:
                    expected = int(expected_revisions.get(item_id, -1))
                except (TypeError, ValueError) as exc:
                    raise ValueError('합칠 원본 레이어 버전이 올바르지 않습니다') from exc
                if expected != int(item.get('edit_revision') or 0):
                    raise ValueError('합성 미리보기 이후 원본 레이어가 변경되었습니다')
            mapping = studio._store.mapping_check(project)
            merged = merge_layers(
                project,
                source_ids,
                name=payload.get('name') or payload['layer'].get('name') or '합친 레이어',
                append_layer_id=payload.get('append_layer_id'),
                motion_ranges_deg=studio._motion_ranges(mapping),
                initial_motion_values_deg=studio._manual_initial_values(mapping),
            )
            merge_report = dict(merged.get('merge_report') or {})
            if set(merged.get('source_layer_ids') or []) != source_ids:
                raise ValueError('합성 결과의 원본 레이어 정보가 일치하지 않습니다')
            range_issues = validate_ranges(merged, studio._motion_ranges(mapping))
            merged = dict(merged)
            merged.update({
                'layer_id': f'merged_{uuid.uuid4().hex[:8]}',
                'name': str(payload.get('name') or merged.get('name') or '합친 레이어')[:40],
                'source_layer_ids': sorted(source_ids),
                'enabled': False,
                'locked': False,
            })
            project.setdefault('layers', []).append(merged)
            studio._current_project = studio._store.save_project(
                project, upsert_layer_ids=[merged['layer_id']]
            )
            project = studio._current_project
        result = studio._project_result(
            project,
            '선택 레이어를 새 레이어로 합쳤습니다 · 결과는 재생 미선택 상태입니다',
            affected_motion_ids=layer_motion_ids(merged),
            affected_layer_ids={merged['layer_id']},
        )
        result.update({
            'layer_id': merged['layer_id'],
            'range_warnings': range_issues,
            'merge_report': merge_report,
        })
        return self._sync_result(result, [merged['layer_id']])
