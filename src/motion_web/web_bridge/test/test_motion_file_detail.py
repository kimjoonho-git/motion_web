import pytest

from motion_web_bridge.bridge_node import MotionWebBridge, create_app
from motion_web_bridge.project_repository import ProjectRepository


MOTION_CONTENT = '\n'.join([
    '{"type":"motion_header","rotation_unit":"deg"}',
    '[1,0.02,"1-1",0.0]',
])


def motion_file_bridge(tmp_path, registered_motion_file_id=''):
    projects_dir = tmp_path / 'projects'
    repository = ProjectRepository(projects_dir)
    project_id = repository.create_project('motion-files')['project']['project_id']
    repository.import_text(
        project_id,
        'motions',
        'show.json',
        MOTION_CONTENT,
    )
    repository.import_text(
        project_id,
        'motion_axis_matching',
        'mapping.yaml',
        (
            'name: mapping\n'
            f'motion_file_id: {registered_motion_file_id}\n'
            'mappings: []\n'
        ),
    )
    bridge = MotionWebBridge.__new__(MotionWebBridge)
    bridge.project_repository = repository
    bridge.motion_projects_dir = projects_dir
    bridge._ensure_project_mutation_allowed = lambda _project_id: None
    return bridge, repository, project_id


def test_motion_file_detail_contains_complete_original_content(tmp_path):
    content = f'header\n{"x" * 15000}\nlast-frame'
    path = tmp_path / 'motion.json'
    path.write_text(content, encoding='utf-8')
    bridge = MotionWebBridge.__new__(MotionWebBridge)
    bridge._analyze_motion_json = lambda _content, *, include_records: {
        'valid': True,
        'include_records': include_records,
    }

    detail = bridge._motion_file_entry(path, include_detail=True)
    summary = bridge._motion_file_entry(path, include_detail=False)

    assert detail['content'] == content
    assert detail['content'].endswith('last-frame')
    assert len(detail['content']) > 12000
    assert 'content' not in summary


def test_external_motion_file_upload_route_is_not_available():
    app = create_app(MotionWebBridge.__new__(MotionWebBridge))
    upload_routes = [
        route
        for route in app.routes
        if getattr(route, 'path', '') == '/api/motion-files/upload'
        and 'POST' in (getattr(route, 'methods', set()) or set())
    ]

    assert upload_routes == []
    assert not hasattr(MotionWebBridge, 'upload_motion_file')


def test_project_file_tools_cannot_import_or_copy_motion_files(tmp_path):
    projects_dir = tmp_path / 'projects'
    repository = ProjectRepository(projects_dir)
    source_id = repository.create_project('source')['project']['project_id']
    target_id = repository.create_project('target')['project']['project_id']
    bridge = MotionWebBridge.__new__(MotionWebBridge)
    bridge.project_repository = repository
    bridge._ensure_project_mutation_allowed = lambda _project_id: None

    with pytest.raises(ValueError, match='외부 모션 JSON'):
        bridge.import_motion_project_file(target_id, {
            'category': 'motions',
            'file_name': 'external.json',
            'content': MOTION_CONTENT,
        })
    with pytest.raises(ValueError, match='프로젝트 복사'):
        bridge.copy_motion_project_file(target_id, {
            'source_project_id': source_id,
            'category': 'motions',
            'file_name': 'show.json',
        })


def test_registered_motion_file_cannot_be_deleted(tmp_path):
    bridge, repository, project_id = motion_file_bridge(tmp_path, 'show.json')

    result = bridge.delete_motion_file('show.json')

    assert result['success'] is False
    assert result['deletion_blocked'] == 'registered_motion_file'
    assert result['registered_mapping_files'] == ['mapping.yaml']
    assert '재생 등록을 해제한 뒤' in result['message']
    assert repository.read_file(project_id, 'motions', 'show.json')['content']


def test_unregistered_motion_file_can_be_deleted(tmp_path):
    bridge, repository, project_id = motion_file_bridge(tmp_path)

    result = bridge.delete_motion_file('show.json')

    assert result['success'] is True
    assert not any(
        item.get('name') == 'show.json'
        for folder in repository.get_project(project_id)['tree']
        if folder.get('category') == 'motions'
        for item in folder.get('children') or []
    )


def test_motion_file_registration_is_isolated_between_projects(tmp_path):
    projects_dir = tmp_path / 'projects'
    repository = ProjectRepository(projects_dir)
    registered_project = repository.create_project('registered')['project']['project_id']
    deletable_project = repository.create_project('deletable')['project']['project_id']
    for project_id, registered_id in (
        (registered_project, 'show.json'),
        (deletable_project, ''),
    ):
        repository.import_text(project_id, 'motions', 'show.json', MOTION_CONTENT)
        repository.import_text(
            project_id,
            'motion_axis_matching',
            'mapping.yaml',
            (
                'name: mapping\n'
                f'motion_file_id: {registered_id}\n'
                'mappings: []\n'
            ),
        )
    repository.select_project(deletable_project)
    bridge = MotionWebBridge.__new__(MotionWebBridge)
    bridge.project_repository = repository
    bridge.motion_projects_dir = projects_dir
    bridge._ensure_project_mutation_allowed = lambda _project_id: None

    result = bridge.delete_motion_file('show.json')

    assert result['success'] is True
    assert repository.read_file(
        registered_project, 'motions', 'show.json'
    )['file_name'] == 'show.json'
