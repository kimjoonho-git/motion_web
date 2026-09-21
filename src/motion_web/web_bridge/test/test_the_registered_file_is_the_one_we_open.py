"""화면은 **프로젝트가 등록한** 모션축 설정 파일을 연다 · §6-238

전에는 서버가 「어느 것이 등록된 파일인가」를 안 알려줬다 · 그래서 화면은
목록의 **첫 번째**를 골라 열었다 · 파일이 하나뿐인 프로젝트에서는 우연히
맞았고, 그래서 오래 들키지 않았다 · 파일이 둘 이상이면 프로젝트가 물고
있는 것과 다른 것을 편집하게 된다.
"""

import json

from motion_web_bridge.project_repository import ProjectRepository


def _repository(tmp_path):
    repository = ProjectRepository(tmp_path / 'projects')
    project_id = repository.create_project('등록된 파일')['project']['project_id']
    return repository, project_id


def _register(repository, project_id, category, name):
    manifest_path = repository._project_dir(project_id) / 'project.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    manifest.setdefault('active_files', {})[category] = name
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8'
    )


def test_registered_file_name_is_readable(tmp_path):
    repository, project_id = _repository(tmp_path)
    _register(repository, project_id, 'motion_axis_matching', 'motion_axis.yaml')

    assert repository.active_file_name(
        project_id, 'motion_axis_matching'
    ) == 'motion_axis.yaml'


def test_nothing_registered_reads_as_empty_not_as_an_error(tmp_path):
    repository, project_id = _repository(tmp_path)
    _register(repository, project_id, 'motion_axis_matching', '')

    assert repository.active_file_name(project_id, 'motion_axis_matching') == ''


def test_an_unknown_project_reads_as_empty(tmp_path):
    repository, _ = _repository(tmp_path)

    assert repository.active_file_name('없는프로젝트', 'motion_axis_matching') == ''


def test_each_category_is_answered_on_its_own(tmp_path):
    repository, project_id = _repository(tmp_path)
    _register(repository, project_id, 'motion_axis_matching', 'motion_axis.yaml')
    _register(repository, project_id, 'motor_axes', 'motor_axes.yaml')

    assert repository.active_file_name(
        project_id, 'motion_axis_matching'
    ) == 'motion_axis.yaml'
    assert repository.active_file_name(project_id, 'motor_axes') == 'motor_axes.yaml'
    assert repository.active_file_name(project_id, 'motions') == ''
