import hashlib
import threading
from pathlib import Path

from motion_studio.export_service import StudioExportService
from motion_studio.studio_node import MotionStudioNode
from motion_studio.workspace_session import StudioWorkspaceSession


def test_workspace_switch_clears_previous_project_and_composition_cache(tmp_path):
    for project_id in ('project-a', 'project-b'):
        project_dir = tmp_path / project_id
        project_dir.mkdir()
        (project_dir / 'project.json').write_text('{}', encoding='utf-8')

    selected = []
    studio = MotionStudioNode.__new__(MotionStudioNode)
    studio.motion_projects_dir = tmp_path.resolve()
    studio._lock = threading.RLock()
    studio._workspace_project_id = ''
    studio._current_project = None
    studio._composition_cache_project_id = ''
    studio._composition_cache = {}
    studio._workspace_catalog_cache = None
    studio._require_idle_locked = lambda: None
    studio._store = type('Store', (), {
        'use_workspace': lambda _self, path: selected.append(Path(path).name),
    })()
    session = StudioWorkspaceSession(studio)

    session.select({'project_id': 'project-a'})
    studio._current_project = {'project_id': 'project-a'}
    studio._composition_cache_project_id = 'project-a'
    studio._composition_cache = {'conflicts': [{'motion_id': '1-1'}]}
    studio._workspace_catalog_cache = {'projects': ['project-a']}
    session.select({'project_id': 'project-b'})

    assert selected == ['project-a', 'project-b']
    assert studio._workspace_project_id == 'project-b'
    assert studio._current_project is None
    assert studio._composition_cache_project_id == ''
    assert studio._composition_cache == {}
    assert studio._workspace_catalog_cache is None


def test_workspace_context_uses_only_the_selected_project_mapping(tmp_path):
    project_id = 'project-a'
    mapping_dir = tmp_path / project_id / 'motion_axis_matching'
    mapping_dir.mkdir(parents=True)
    (tmp_path / project_id / 'project.json').write_text('{}', encoding='utf-8')
    mapping_path = mapping_dir / 'mapping.yaml'
    mapping_path.write_text('mappings: []\n', encoding='utf-8')
    mapping_sha = hashlib.sha256(mapping_path.read_bytes()).hexdigest()

    studio = MotionStudioNode.__new__(MotionStudioNode)
    studio.motion_projects_dir = tmp_path.resolve()
    studio._lock = threading.RLock()
    studio._workspace_project_id = project_id
    studio._project_generation = 7
    studio._execution_context = {}
    studio._execution_context_ready = False
    studio.snapshot = lambda: {'state': 'idle'}
    session = StudioWorkspaceSession(studio)

    result = session.apply_execution_context({
        'project_id': project_id,
        'project_generation': 7,
        'context_id': 'context-a',
        'mapping_file_id': 'mapping.yaml',
        'mapping_sha256': mapping_sha,
    })

    assert result['project_id'] == project_id
    assert result['mapping_sha256'] == mapping_sha
    assert studio._execution_context_ready is False
    session.confirm_execution_context({'context_id': 'context-a'})
    assert studio._execution_context_ready is True


def test_export_service_preserves_motion_file_contract():
    project = {
        'project_id': 'project-a',
        'name': '내보내기 테스트',
        'period_sec': 0.02,
        'layers': [{
            'layer_id': 'layer-a',
            'name': '레이어 A',
            'enabled': True,
            'frames': [
                {'frame': 1, 'time_sec': 0.02, 'values': {'1-1': 3.0}},
            ],
        }],
    }
    written = {}

    class Store:
        @staticmethod
        def mapping_check(_project):
            return {'rows': [], 'motion_ids': ['1-1']}

        @staticmethod
        def write_motion_file(file_id, content):
            written.update(file_id=file_id, content=content)
            return 'exported.json'

    studio = MotionStudioNode.__new__(MotionStudioNode)
    studio._lock = threading.RLock()
    studio._store = Store()
    studio._workspace_catalog_cache = {'motion_files': ['old.json']}
    studio._require_idle_locked = lambda: None
    studio._require_project_locked = lambda: project
    studio._validate_mapping_locked = lambda _project: None
    studio._require_point_curve_consistency = lambda _project, _action: None
    studio._motion_ranges = lambda _mapping: {}
    studio._manual_initial_values = lambda _mapping: {}

    result = StudioExportService(studio).export({'file_id': 'final-motion'})

    assert result == {
        'success': True,
        'message': '모션 파일 내보내기 완료',
        'file_id': 'exported.json',
        'frame_count': 1,
    }
    assert written['file_id'] == 'final-motion'
    assert '"1-1",3.0' in written['content']
    assert studio._workspace_catalog_cache is None


def test_studio_node_keeps_only_service_delegation_for_stage_8_responsibilities():
    root = Path(__file__).parents[1] / 'motion_studio'
    node_source = (root / 'studio_node.py').read_text(encoding='utf-8')
    recording_source = (root / 'recording_session.py').read_text(encoding='utf-8')
    workspace_source = (root / 'workspace_session.py').read_text(encoding='utf-8')
    export_source = (root / 'export_service.py').read_text(encoding='utf-8')

    assert len(node_source.splitlines()) <= 650
    assert 'return self._recording().start(payload)' in node_source
    assert 'return self._workspace().apply_execution_context(payload)' in node_source
    assert 'return self._exporter().export(payload)' in node_source
    assert 'def wait_for_midi_faders_zero(' in recording_source
    assert 'def composition(' in workspace_source
    assert 'class StudioExportService:' in export_source
