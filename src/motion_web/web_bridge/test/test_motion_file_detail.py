from pathlib import Path
from motion_web_bridge import motion_file_analysis
import pytest

from motion_web_bridge.project_service import ProjectService
from motion_web_bridge.bridge_node import MotionWebBridge, create_app
from motion_web_bridge.project_repository import ProjectRepository


MOTION_CONTENT = '\n'.join([
    '{"type":"motion_header","rotation_unit":"deg"}',
    '[1,0.02,"1-1",0.0]',
])


def _project_of(bridge):
    """노드 스텁에 프로젝트 서비스를 붙인다 · §6-23으로 노드에서 떨어져 나왔다."""
    service = getattr(bridge, '_project', None)
    if service is None:
        service = ProjectService(
            bridge,
            repository=getattr(bridge, 'project_repository', None),
            motion_projects_dir=getattr(bridge, 'motion_projects_dir', Path('.')),
        )
        bridge._project = service
    repository = getattr(bridge, 'project_repository', None)
    if repository is not None:
        service.repository = repository
    projects_dir = getattr(bridge, 'motion_projects_dir', None)
    if projects_dir is not None:
        service.motion_projects_dir = projects_dir
    return service


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
    bridge.ensure_project_mutation_allowed = lambda _project_id: None
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

    detail = motion_file_analysis.motion_file_entry(path, include_detail=True)
    summary = motion_file_analysis.motion_file_entry(path, include_detail=False)

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


def test_motion_file_import_needs_a_motion_axis_setting(tmp_path):
    """모션축 설정이 없는 프로젝트는 모션 파일을 받지 않는다.

    모션 파일 혼자서는 재생 등록도 실행도 스튜디오로 보내기도 못 한다 ·
    넣어 봐야 파일만 놓이고 `import_text`가 비어 있던 `active_files`에
    그 파일을 말없이 꽂는다.
    """
    projects_dir = tmp_path / 'projects'
    repository = ProjectRepository(projects_dir)
    target_id = repository.create_project('target')['project']['project_id']
    bridge = MotionWebBridge.__new__(MotionWebBridge)
    bridge.project_repository = repository
    bridge.ensure_project_mutation_allowed = lambda _project_id: None

    def _import(name):
        return _project_of(bridge).import_file(target_id, {
            'category': 'motions',
            'file_name': name,
            'content': MOTION_CONTENT,
        })

    with pytest.raises(ValueError, match='모션축 설정이 없는 프로젝트'):
        _import('external.json')

    (projects_dir / target_id / 'motion_axis_matching' / 'axes.yaml').write_text(
        'mappings: []\n', encoding='utf-8'
    )
    _import('external.json')
    assert (projects_dir / target_id / 'motions' / 'external.json').is_file()


def test_only_motion_files_can_be_brought_into_a_project(tmp_path):
    """밖에서 들어올 수 있는 것은 모션 파일 하나뿐이다.

    모터축·모션축 설정은 그 PC 의 하드웨어 배선에 매인 값이고 레이어는
    스튜디오가 제 프로젝트 안에서만 다룬다 · 화면에서 종류 칸을 없앴어도
    길이 열려 있으면 언젠가 다시 새어 든다.
    """
    projects_dir = tmp_path / 'projects'
    repository = ProjectRepository(projects_dir)
    project_id = repository.create_project('target')['project']['project_id']
    bridge = MotionWebBridge.__new__(MotionWebBridge)
    bridge.project_repository = repository
    bridge.ensure_project_mutation_allowed = lambda _project_id: None

    for category in ('motor_axes', 'motion_axis_matching', 'layers', ''):
        with pytest.raises(ValueError, match='모션 파일뿐입니다'):
            _project_of(bridge).import_file(project_id, {
                'category': category,
                'file_name': 'x.yaml',
                'content': 'mappings: []\n',
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
    bridge.ensure_project_mutation_allowed = lambda _project_id: None

    result = bridge.delete_motion_file('show.json')

    assert result['success'] is True
    assert repository.read_file(
        registered_project, 'motions', 'show.json'
    )['file_name'] == 'show.json'
