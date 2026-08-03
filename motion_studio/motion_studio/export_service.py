"""Motion-file export service for completed Motion Studio projects."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from .timeline import (
    final_export_layer,
    motion_file_text,
    render_project,
)


class StudioExportService:
    def __init__(self, studio: Any) -> None:
        self.studio = studio

    def export(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        studio = self.studio
        with studio._lock:
            studio._require_idle_locked()
            project = studio._require_project_locked()
            studio._validate_mapping_locked(project)
            studio._require_point_curve_consistency(
                project, '모션 파일 내보내기'
            )
            export_layer = final_export_layer(project)
            mapping = studio._store.mapping_check(project)
            frames = render_project(
                project,
                motion_ranges_deg=studio._motion_ranges(mapping),
                initial_motion_values_deg=studio._manual_initial_values(mapping),
            )
            requested_file_id = str(
                payload.get('file_id') or project['name']
            ).strip()
            file_title = Path(requested_file_id).stem.strip() or project['name']
            file_id = studio._store.write_motion_file(
                requested_file_id,
                motion_file_text(
                    project,
                    frames,
                    editor_layer=export_layer,
                    file_title=file_title,
                ),
            )
            studio._workspace_catalog_cache = None
        return {
            'success': True,
            'message': '모션 파일 내보내기 완료',
            'file_id': file_id,
            'frame_count': len(frames),
        }
